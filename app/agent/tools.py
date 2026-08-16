import ast
import json
import math
import operator
import re
import random
import sqlite3
import time
import traceback
from pathlib import Path
from typing import Any
import regex as rex
from app.agent.sandbox import safe_read_file, safe_ls, truncate_result
from app.agent.query_builder import build_query_cascade, tokenize_terms
from app.storage.user_db import init_user_db
from app.storage.paths import validate_user_path
from app.logging_utils import get_logger

log = get_logger("agent")

_GREP_MAX_PATTERN_LEN = 200
_GREP_TIMEOUT = 0.25
_PATHOLOGICAL = re.compile(r"\([^)]*[+*][^)]*\)[+*]")
_KEYWORD_CACHE: dict[str, tuple[float, set[str]]] = {}
_KEYWORD_CACHE_TTL = 60.0


def _dice_roll(count: int, sides: int) -> int:
    return sum(random.randint(1, sides) for _ in range(count))


def _eval_dice(expr: str) -> str:
    """Replace NdS dice rolls in expr with their rolled totals, returning the
    substituted expression string (e.g. '2d6+3' -> '7+3') for later evaluation."""
    MAX_DICE = 100
    MAX_SIDES = 1000

    def replace(m: re.Match[str]) -> str:
        n, s = int(m.group(1)), int(m.group(2))
        if n > MAX_DICE or s > MAX_SIDES:
            raise ValueError(f"dice rolls capped at {MAX_DICE}d{MAX_SIDES}")
        return str(_dice_roll(n, s))
    return re.sub(r"(\d+)d(\d+)", replace, expr)


_SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _safe_eval(expr: str) -> int | float:
    tree = ast.parse(expr.strip(), mode="eval")

    def _eval(node: ast.AST) -> int | float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"unsupported constant: {node.value!r}")
        if isinstance(node, ast.BinOp):
            op = _SAFE_OPS.get(type(node.op))
            if op is None:
                raise ValueError(f"unsupported operator: {type(node.op).__name__}")
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
                raise ValueError("division by zero")
            if isinstance(node.op, ast.Pow):
                if not isinstance(right, int) or right < 0 or right > 1000:
                    raise ValueError("exponent must be an integer between 0 and 1000")
                if isinstance(left, int) and left.bit_length() > 32:
                    raise ValueError("base too large for exponentiation")
            return op(left, right)
        if isinstance(node, ast.UnaryOp):
            op = _SAFE_OPS.get(type(node.op))
            if op is None:
                raise ValueError(f"unsupported unary operator: {type(node.op).__name__}")
            return op(_eval(node.operand))
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "abs" and len(node.args) == 1:
                return abs(_eval(node.args[0]))
            raise ValueError(f"unsupported function call: {ast.dump(node)}")
        raise ValueError(f"unsupported expression: {ast.dump(node)}")

    return _eval(tree)


class ToolBox:
    def __init__(self, data_dir: Path, user_id: str, db_dir: Path, collection_id: str, owner_uid: str | None = None):
        self.data_dir = data_dir
        self.user_id = user_id
        # Shared collections: all DB/FTS/file access resolves to the owner's
        # tree. Private collections: owner == user.
        self.owner_uid = owner_uid or user_id
        self.db_dir = db_dir
        self.collection_id = collection_id

    def _collection_doc_ids(self) -> set[str]:
        """doc_ids belonging to the current collection (owner's DB)."""
        conn = init_user_db(self.db_dir, self.owner_uid)
        try:
            rows = conn.execute(
                "SELECT doc_id FROM docs WHERE collection_id = ?", (self.collection_id,)
            ).fetchall()
            return {r["doc_id"] for r in rows}
        finally:
            conn.close()

    def _validate_collection_path(self, path: str) -> Path | None:
        """Resolve a tool path inside the owner's tree AND the current
        collection's doc set. Returns None when out of scope."""
        try:
            full = validate_user_path(self.data_dir, self.owner_uid, path)
        except ValueError:
            return None
        doc_ids = self._collection_doc_ids()
        rel = full.relative_to(self.data_dir / self.owner_uid)
        first = rel.parts[0] if rel.parts else ""
        if first not in doc_ids:
            return None
        return full

    def fts_search(self, query: str) -> list[dict[str, Any]]:
        conn = init_user_db(self.db_dir, self.owner_uid)
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
            for stage, fts_query in enumerate(cascade):
                sql = (
                    f"SELECT path, title, summary, keywords, "
                    f"snippet(documents_fts, 4, '<mark>', '</mark>', '...', 15) as snippet, "
                    f"bm25(documents_fts, 0, 5, 4, 4, 3) as rank "
                    f"FROM documents_fts WHERE documents_fts MATCH ? AND {scope} "
                    f"ORDER BY rank LIMIT 5"
                )
                params = (fts_query,) + tuple(f"{d}/%" for d in doc_ids)
                rows = conn.execute(sql, params).fetchall()
                if rows:
                    if fts_query.startswith("title:"):
                        match_mode = "title"
                    else:
                        match_mode = ("and", "or", "prefix")[stage - (1 if cascade[0].startswith("title:") else 0)]
                    results = []
                    for r in rows:
                        item = dict(r)
                        item["summary"] = (item.get("summary") or "")[:300]
                        item["match_mode"] = match_mode
                        item["page"] = self._page_for(item["path"])
                        results.append(item)
                    log.debug(f"fts_search: query='{query}' -> {len(results)} results ({match_mode}, fts='{fts_query}')")
                    return results
            log.debug(f"fts_search: query='{query}' -> 0 results across all fallbacks")
            return [{"match_mode": "none", "hint": f"No matches for '{query}'. Try a different single keyword or use grep."}]
        except Exception as e:
            log.error(f"fts_search ERROR: {e}\n{traceback.format_exc()}")
            return []
        finally:
            conn.close()

    def _page_for(self, path: str) -> int | None:
        try:
            full = self.data_dir / self.owner_uid / path
            if not full.is_file():
                return None
            with open(full, "r") as f:
                if f.read(3) != "---":
                    return None
                rest = f.readline()
                if rest.rstrip("\n") != "":
                    return None
                for line in f:
                    if line.rstrip("\n") == "---":
                        break
                    if line.startswith("page:"):
                        return int(line[5:].strip())
        except (ValueError, OSError):
            pass
        return None

    def _keyword_synonyms(self, conn: sqlite3.Connection, doc_ids: list[str], terms: list[str]) -> dict[str, list[str]]:
        """Per-collection keyword expansion: term -> keyword tokens containing it."""
        if not terms:
            return {}
        short_terms = [t for t in terms if len(t) >= 4]
        if not short_terms:
            return {}
        cache_key = (self.collection_id, len(doc_ids))
        now = time.monotonic()
        cached = _KEYWORD_CACHE.get(cache_key)
        if cached and now - cached[0] < _KEYWORD_CACHE_TTL:
            all_keywords = cached[1]
        else:
            placeholders = " OR ".join("path LIKE ?" for _ in doc_ids)
            rows = conn.execute(
                f"SELECT keywords FROM documents_fts WHERE keywords != '' AND ({placeholders})",
                tuple(f"{d}/%" for d in doc_ids),
            ).fetchall()
            all_keywords = set()
            for r in rows:
                for kw in (r["keywords"] or "").split(","):
                    kw = kw.strip().lower()
                    if kw:
                        all_keywords.add(kw)
            _KEYWORD_CACHE[cache_key] = (now, all_keywords)
        extra = {}
        for t in short_terms:
            hits = {kw for kw in all_keywords if t in kw.split()}
            if hits:
                extra[t] = sorted(hits)
        return extra

    def read_file(self, path: str, lines: str | None = None) -> str:
        if str(path).endswith("index.md"):
            return "index.md files are navigation only and cannot be read. Use list_index to navigate the book structure."
        full = self._validate_collection_path(path)
        if full is None:
            return f"(invalid path: {path})"
        return safe_read_file(self.data_dir, self.owner_uid, path, lines)

    def list_index(self, path: str) -> list[dict[str, Any]]:
        full = self._validate_collection_path(path)
        if full is None:
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

    def grep(self, pattern: str, path: str | None = None) -> list[dict[str, Any]]:
        if len(pattern) > _GREP_MAX_PATTERN_LEN or _PATHOLOGICAL.search(pattern):
            return []
        try:
            compiled = rex.compile(pattern)
        except rex.error:
            return []
        # Restrict search to docs in this collection only
        conn = init_user_db(self.db_dir, self.owner_uid)
        doc_rows = conn.execute(
            "SELECT doc_id FROM docs WHERE collection_id = ?", (self.collection_id,)
        ).fetchall()
        conn.close()
        doc_ids = [r["doc_id"] for r in doc_rows]
        if not doc_ids:
            return []

        hits = []
        for did in doc_ids:
            root = self.data_dir / self.owner_uid / did
            if not root.exists():
                continue
            if path:
                scoped = self._validate_collection_path(path)
                if scoped is None:
                    continue
                root = scoped
            for f in root.rglob("*.md"):
                try:
                    for i, line in enumerate(f.read_text().splitlines(), start=1):
                        try:
                            if compiled.search(line, timeout=_GREP_TIMEOUT):
                                rel = str(f.relative_to(self.data_dir / self.owner_uid))
                                hits.append({"path": rel, "line": i, "text": line.strip()[:200]})
                                if len(hits) >= 20:
                                    return hits
                        except rex.TimeoutError:
                            if hits:
                                return hits
                            return []
                    if len(hits) >= 20:
                        return hits
                except Exception:
                    continue
        return hits

    def table_extract(self, path: str) -> list[dict[str, Any]]:
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
        if len(expr) > 200:
            return "error: expression too long"
        try:
            dice_expr = _eval_dice(expr)
            result = _safe_eval(dice_expr)
            if not isinstance(result, (int, float)):
                return "error: only numeric expressions are supported"
            return str(result)
        except Exception as e:
            return f"error: {e}"

    def ls(self, dir_path: str) -> list[str]:
        full = self._validate_collection_path(dir_path)
        if full is None:
            return []
        try:
            return safe_ls(self.data_dir, self.owner_uid, dir_path)
        except ValueError:
            return []

    def execute(self, name: str, args: dict[str, Any]) -> str:
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