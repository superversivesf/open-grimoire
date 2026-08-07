import json
import re
import random
import traceback
from pathlib import Path
from app.agent.sandbox import safe_read_file, safe_ls, truncate_result
from app.agent.query_builder import build_query_cascade, tokenize_terms
from app.storage.user_db import init_user_db
from app.storage.paths import validate_user_path
from app.logging_utils import get_logger

log = get_logger("agent")


def _dice_roll(count: int, sides: int) -> int:
    return sum(random.randint(1, sides) for _ in range(count))


def _eval_dice(expr: str) -> str:
    """Replace NdS dice rolls in expr with their rolled totals, returning the
    substituted expression string (e.g. '2d6+3' -> '7+3') for later evaluation."""
    def replace(m):
        n, s = int(m.group(1)), int(m.group(2))
        return str(_dice_roll(n, s))
    return re.sub(r"(\d+)d(\d+)", replace, expr)


class ToolBox:
    def __init__(self, data_dir: Path, user_id: str, db_dir: Path, collection_id: str):
        self.data_dir = data_dir
        self.user_id = user_id
        self.db_dir = db_dir
        self.collection_id = collection_id

    def fts_search(self, query: str) -> list[dict]:
        conn = init_user_db(self.db_dir, self.user_id)
        try:
            doc_rows = conn.execute(
                "SELECT doc_id FROM docs WHERE collection_id = ?", (self.collection_id,)
            ).fetchall()
            doc_ids = [r["doc_id"] for r in doc_rows]
            if not doc_ids:
                log.debug(f"fts_search: no docs in collection {self.collection_id}")
                return []
            terms = tokenize_terms(query)
            if not terms:
                return []
            extra = self._keyword_synonyms(conn, doc_ids, terms)
            cascade = build_query_cascade(terms, extra)
            scope = "(" + " OR ".join(f"path LIKE ?" for _ in doc_ids) + ")"
            for fts_query in cascade:
                sql = (
                    f"SELECT path, title, summary, "
                    f"snippet(documents_fts, 4, '<mark>', '</mark>', '...', 10) as snippet, "
                    f"bm25(documents_fts, 0, 5, 8, 8, 1) as rank "
                    f"FROM documents_fts WHERE documents_fts MATCH ? AND {scope} "
                    f"ORDER BY rank LIMIT 5"
                )
                params = (fts_query,) + tuple(f"{d}/%" for d in doc_ids)
                rows = conn.execute(sql, params).fetchall()
                if rows:
                    results = []
                    for r in rows:
                        item = dict(r)
                        item["page"] = self._page_for(item["path"])
                        results.append(item)
                    log.debug(f"fts_search: query='{query}' -> {len(results)} results (fts='{fts_query}')")
                    return results
            log.debug(f"fts_search: query='{query}' -> 0 results across all fallbacks")
            return []
        except Exception as e:
            log.error(f"fts_search ERROR: {e}\n{traceback.format_exc()}")
            return []
        finally:
            conn.close()

    def _page_for(self, path: str) -> int | None:
        try:
            full = self.data_dir / self.user_id / path
            if not full.is_file():
                return None
            text = full.read_text()
            if not text.startswith("---"):
                return None
            end = text.find("\n---\n", 4)
            if end == -1:
                return None
            for line in text[4:end].splitlines():
                if line.startswith("page:"):
                    return int(line[5:].strip())
        except (ValueError, OSError):
            pass
        return None

    def _keyword_synonyms(self, conn, doc_ids: list[str], terms: list[str]) -> dict[str, list[str]]:
        """Per-collection keyword expansion: term -> keyword tokens containing it."""
        if not terms:
            return {}
        placeholders = " OR ".join("path LIKE ?" for _ in doc_ids)
        rows = conn.execute(
            f"SELECT keywords FROM documents_fts WHERE {placeholders}",
            tuple(f"{d}/%" for d in doc_ids),
        ).fetchall()
        all_keywords = set()
        for r in rows:
            for kw in (r["keywords"] or "").split(","):
                kw = kw.strip().lower()
                if kw:
                    all_keywords.add(kw)
        extra = {}
        for t in terms:
            if len(t) < 4:
                continue
            hits = {kw for kw in all_keywords if t in kw or kw in t}
            if hits:
                extra[t] = sorted(hits)
        return extra

    def read_file(self, path: str, lines: str | None = None) -> str:
        return safe_read_file(self.data_dir, self.user_id, path, lines)

    def list_index(self, path: str) -> list[dict]:
        try:
            full = validate_user_path(self.data_dir, self.user_id, path)
        except ValueError:
            return []
        if not full.exists():
            return []
        # If it's a directory, look for index.md inside it
        if full.is_dir():
            full = full / "index.md"
            if not full.exists():
                return []
        if not full.is_file():
            return []
        text = full.read_text()
        entries = []
        for line in text.splitlines():
            m = re.match(r"^-\s+\[(.+?)\]\((.+?)\)", line)
            if m:
                entries.append({"title": m.group(1), "summary": "", "path": m.group(2)})
        return entries

    def grep(self, pattern: str, path: str | None = None) -> list[dict]:
        try:
            regex = re.compile(pattern)
        except re.error:
            return []
        # Restrict search to docs in this collection only
        conn = init_user_db(self.db_dir, self.user_id)
        doc_rows = conn.execute(
            "SELECT doc_id FROM docs WHERE collection_id = ?", (self.collection_id,)
        ).fetchall()
        conn.close()
        doc_ids = [r["doc_id"] for r in doc_rows]
        if not doc_ids:
            return []

        hits = []
        for did in doc_ids:
            root = self.data_dir / self.user_id / did
            if not root.exists():
                continue
            if path:
                try:
                    root = validate_user_path(self.data_dir, self.user_id, path)
                except ValueError:
                    continue
            for f in root.rglob("*.md"):
                try:
                    for i, line in enumerate(f.read_text().splitlines(), start=1):
                        if regex.search(line):
                            rel = str(f.relative_to(self.data_dir / self.user_id))
                            hits.append({"path": rel, "line": i, "text": line.strip()[:200]})
                            if len(hits) >= 20:
                                return hits
                except Exception:
                    continue
        return hits

    def table_extract(self, path: str) -> list[dict]:
        text = self.read_file(path)
        rows = []
        lines = text.splitlines()
        headers = None
        for line in lines:
            if line.startswith("|") and "|" in line[1:]:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if headers is None:
                    headers = cells
                elif all(re.match(r"^[-:]+$", c) for c in cells):
                    continue
                else:
                    rows.append(dict(zip(headers, cells)))
        return rows

    def calc(self, expr: str) -> str:
        from simpleeval import simple_eval, EvalWithCompoundTypes
        try:
            dice_expr = _eval_dice(expr)
            evaluator = EvalWithCompoundTypes()
            result = evaluator.eval(dice_expr)
            return str(result)
        except Exception as e:
            return f"error: {e}"

    def ls(self, dir_path: str) -> list[str]:
        try:
            return safe_ls(self.data_dir, self.user_id, dir_path)
        except ValueError:
            return []

    def execute(self, name: str, args: dict) -> str:
        dispatch = {
            "fts_search": lambda: json.dumps(self.fts_search(args.get("query", ""))),
            "read_file": lambda: self.read_file(args.get("path", ""), args.get("lines")),
            "list_index": lambda: json.dumps(self.list_index(args.get("path", ""))),
            "grep": lambda: json.dumps(self.grep(args.get("pattern", ""), args.get("path"))),
            "table_extract": lambda: json.dumps(self.table_extract(args.get("path", ""))),
            "calc": lambda: self.calc(args.get("expr", "")),
            "ls": lambda: json.dumps(self.ls(args.get("dir", ""))),
        }
        fn = dispatch.get(name)
        if not fn:
            return json.dumps({"error": f"unknown tool: {name}"})
        return fn()