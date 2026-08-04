import json
import re
import random
import traceback
from pathlib import Path
from app.agent.sandbox import safe_read_file, safe_ls, truncate_result
from app.storage.user_db import init_user_db
from app.storage.paths import validate_user_path
from app.logging_utils import get_logger

log = get_logger("agent")


def _dice_roll(count: int, sides: int) -> int:
    return sum(random.randint(1, sides) for _ in range(count))


def _eval_dice(expr: str) -> int:
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
            # Get doc_ids for this collection only
            doc_rows = conn.execute(
                "SELECT doc_id FROM docs WHERE collection_id = ?", (self.collection_id,)
            ).fetchall()
            doc_ids = [r["doc_id"] for r in doc_rows]
            if not doc_ids:
                log.debug(f"fts_search: no docs in collection {self.collection_id}")
                return []
            # Build a filter: only match paths that start with a doc_id from this collection
            placeholders = ",".join("?" * len(doc_ids))
            sql = (f"SELECT path, title, snippet(documents_fts, 4, '<mark>', '</mark>', '...', 10) as snippet, rank "
                   f"FROM documents_fts WHERE documents_fts MATCH ? AND ("
                   + " OR ".join(f"path LIKE ?" for _ in doc_ids)
                   + ") ORDER BY rank LIMIT 5")
            params = (query,) + tuple(f"{did}/%" for did in doc_ids)
            rows = conn.execute(sql, params).fetchall()
            log.debug(f"fts_search: query='{query}' db={self.db_dir} uid={self.user_id} -> {len(rows)} results")
            return [dict(r) for r in rows]
        except Exception as e:
            log.error(f"fts_search ERROR: {e}\n{traceback.format_exc()}")
            return []
        finally:
            conn.close()

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
                            rel = str(f.relative_to(self.data_dir))
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