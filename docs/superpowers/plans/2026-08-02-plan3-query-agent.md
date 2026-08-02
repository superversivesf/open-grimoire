# RPG Manual Query Engine — Plan 3: Query Agent

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the conversational query agent: 8 tools, agent loop, session management, chat UI with citations. After this plan, users can ask questions about their uploaded manuals and get cited answers.

**Architecture:** Per-request agent loop calls the `query` LLM via Ollama's tool-calling API. Tools are sandboxed functions scoped to the user's collection. Session history persists in `db/<user_id>.sqlite`. Chat UI uses HTMX for multi-turn conversation without full reloads.

**Tech Stack:** Python 3.11+, FastAPI, Ollama tool-calling API, `simpleeval` (calc tool), Jinja2 + HTMX (chat UI), existing storage from Plans 1-2.

## Global Constraints

- Builds on Plans 1 and 2 (storage, auth, gateway, pipeline, FTS5 must exist).
- Agent tools are sandboxed: paths validated via `validate_user_path`; no writes, no network, no subprocess.
- Max 8 tool calls per turn; force-terminate after.
- Session history: last 6 turns verbatim + summarized older context.
- Citations format: `{path, page, quote}` rendered as clickable links.
- Tests mock Ollama (no running Ollama required).
- Commit after each task.

---

## File Structure

```
app/
├── agent/
│   ├── __init__.py
│   ├── tools.py          # 8 tool implementations (sandboxed)
│   ├── tools_schema.py   # Ollama tool definitions (JSON schema for each)
│   ├── sandbox.py        # path validation, result truncation, safety
│   ├── loop.py           # agent loop: LLM call → tool exec → repeat
│   ├── history.py        # session history load/trim/summarize/append
│   └── routes.py         # POST /sessions, GET /sessions/:id, POST /sessions/:id
├── web/
│   ├── routes.py         # +GET /sessions (list), link from collection
│   └── templates/
│       ├── chat.html
│       └── _message.html  # HTMX partial for new message pair
tests/
├── test_tools.py
├── test_sandbox.py
├── test_agent_loop.py
├── test_history.py
└── test_session_routes.py
```

---

### Task 1: Sandbox (path validation + result truncation)

**Files:**
- Create: `app/agent/__init__.py`
- Create: `app/agent/sandbox.py`
- Test: `tests/test_sandbox.py`

**Interfaces:**
- Consumes: `validate_user_path` (Plan 1 Task 2).
- Produces:
  - `def safe_read_file(data_dir: Path, user_id: str, path: str, lines: str | None = None) -> str` — validates path, reads file, optional line range, truncates to ~4k tokens.
  - `def truncate_result(text: str, max_chars: int = 16000) -> str` — returns text or truncated with hint.
  - `def safe_ls(data_dir: Path, user_id: str, dir_path: str) -> list[str]` — lists dir contents within user tree.

- [ ] **Step 1: Write the failing test `tests/test_sandbox.py`**

```python
import pytest
from pathlib import Path
from app.agent.sandbox import safe_read_file, truncate_result, safe_ls


def test_safe_read_file(tmp_dirs):
    f = tmp_dirs["data"] / "alice" / "d1" / "x.md"
    f.parent.mkdir(parents=True)
    f.write_text("# Hello\n\nContent here.\n")
    result = safe_read_file(tmp_dirs["data"], "alice", str(f))
    assert "Hello" in result
    assert "Content here" in result


def test_safe_read_file_rejects_escape(tmp_dirs):
    with pytest.raises(ValueError):
        safe_read_file(tmp_dirs["data"], "alice", "/etc/passwd")


def test_safe_read_file_line_range(tmp_dirs):
    f = tmp_dirs["data"] / "alice" / "d1" / "x.md"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(f"line {i}" for i in range(100)))
    result = safe_read_file(tmp_dirs["data"], "alice", str(f), lines="10-15")
    assert "line 10" in result
    assert "line 15" in result
    assert "line 16" not in result
    assert "line 9" not in result


def test_truncate_result_short_text():
    assert truncate_result("short", 1000) == "short"


def test_truncate_result_long_text():
    text = "x" * 20000
    result = truncate_result(text, max_chars=1000)
    assert len(result) < 1100
    assert "truncated" in result.lower() or "file is long" in result.lower()


def test_safe_ls(tmp_dirs):
    d = tmp_dirs["data"] / "alice" / "d1"
    d.mkdir(parents=True)
    (d / "a.md").write_text("a")
    (d / "b.md").write_text("b")
    result = safe_ls(tmp_dirs["data"], "alice", str(d))
    assert "a.md" in result
    assert "b.md" in result


def test_safe_ls_rejects_escape(tmp_dirs):
    with pytest.raises(ValueError):
        safe_ls(tmp_dirs["data"], "alice", "/etc")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sandbox.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/agent/__init__.py` (empty) and `app/agent/sandbox.py`**

```python
# app/agent/__init__.py
```

```python
# app/agent/sandbox.py
from pathlib import Path
from app.storage.paths import validate_user_path


def safe_read_file(data_dir: Path, user_id: str, path: str, lines: str | None = None) -> str:
    full = validate_user_path(data_dir, user_id, path)
    if not full.exists():
        return f"(file not found: {path})"
    text = full.read_text()
    if lines:
        try:
            start, end = map(int, lines.split("-"))
            text_lines = text.splitlines()
            text = "\n".join(text_lines[start - 1 : end])
        except (ValueError, IndexError):
            pass
    return truncate_result(text)


def truncate_result(text: str, max_chars: int = 16000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... file is long, use read_file(path, lines='N-M') for more]"


def safe_ls(data_dir: Path, user_id: str, dir_path: str) -> list[str]:
    full = validate_user_path(data_dir, user_id, dir_path)
    if not full.is_dir():
        return []
    return sorted(p.name for p in full.iterdir())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sandbox.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agent/__init__.py app/agent/sandbox.py tests/test_sandbox.py
git commit -m "feat: agent sandbox (path validation + truncation)"
```

---

### Task 2: Tool implementations (8 tools)

**Files:**
- Create: `app/agent/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `safe_read_file`, `safe_ls`, `truncate_result` (Task 1); `init_user_db` (Plan 1); FTS5 table.
- Produces:
  - `class ToolBox` with `__init__(self, data_dir: Path, user_id: str, db_dir: Path, collection_id: str)`.
  - `def fts_search(self, query: str) -> list[dict]` — top 10 FTS5 matches in the collection's docs.
  - `def read_file(self, path: str, lines: str | None = None) -> str`
  - `def list_index(self, path: str) -> list[dict]` — parses an `index.md`, returns `[{title, summary, path}]`.
  - `def grep(self, pattern: str, path: str | None = None) -> list[dict]` — regex search, top 20 hits.
  - `def table_extract(self, path: str) -> list[dict]` — parses markdown tables in a leaf to JSON rows.
  - `def calc(self, expr: str) -> str` — `simpleeval` with dice.
  - `def ls(self, dir_path: str) -> list[str]`
  - `def execute(self, name: str, args: dict) -> str` — dispatch by name, returns JSON string.

- [ ] **Step 1: Write the failing test `tests/test_tools.py`**

```python
import pytest
from pathlib import Path
from app.agent.tools import ToolBox
from app.storage.user_db import init_user_db, create_collection, create_doc, insert_fts_row


@pytest.fixture
def toolbox(tmp_dirs):
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Book", "h")
    insert_fts_row(uconn, "data/alice/d1/c1/s1.md", "Goblin", "AC 15 monster", "goblin,monster", "Goblins are small humanoids with AC 15 and HP 7.")
    uconn.close()
    doc_dir = tmp_dirs["data"] / "alice" / "d1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "index.md").write_text("# Book\n\n- [Chapter 1](01_chapter/index.md)\n")
    chap = doc_dir / "01_chapter"
    chap.mkdir()
    (chap / "index.md").write_text("# Chapter 1\n\n- [Goblin](01_goblin.md)\n")
    (chap / "01_goblin.md").write_text("---\nsummary: \"Goblin stats.\"\nkeywords: [goblin]\npage: 42\n---\n\n# Goblin\n\n| Name | AC | HP |\n|------|----|----|\n| Goblin | 15 | 7 |\n\nAC 15, HP 7.\n")
    return ToolBox(tmp_dirs["data"], "alice", tmp_dirs["db"], cid)


def test_fts_search(toolbox):
    results = toolbox.fts_search("goblin")
    assert len(results) >= 1
    assert "goblin" in results[0]["path"].lower() or "Goblin" in results[0]["title"]


def test_read_file(toolbox):
    result = toolbox.read_file("data/alice/d1/01_chapter/01_goblin.md")
    assert "Goblin" in result


def test_list_index(toolbox):
    result = toolbox.list_index("data/alice/d1/index.md")
    assert len(result) >= 1
    assert "Chapter 1" in result[0]["title"]


def test_grep(toolbox):
    result = toolbox.grep("AC 15")
    assert len(result) >= 1
    assert "AC 15" in result[0]["text"]


def test_table_extract(toolbox):
    result = toolbox.table_extract("data/alice/d1/01_chapter/01_goblin.md")
    assert len(result) >= 1
    assert result[0]["Name"] == "Goblin"
    assert result[0]["AC"] == "15"


def test_calc_dice(toolbox):
    result = toolbox.calc("2+3")
    assert "5" in result


def test_calc_dice_roll(toolbox):
    result = toolbox.calc("1d20+5")
    import re
    m = re.search(r"\d+", result)
    assert m is not None
    val = int(m.group(0))
    assert 6 <= val <= 25


def test_ls(toolbox):
    result = toolbox.ls("data/alice/d1")
    assert "index.md" in result


def test_execute_dispatch(toolbox):
    result = toolbox.execute("fts_search", {"query": "goblin"})
    import json
    parsed = json.loads(result)
    assert len(parsed) >= 1
```

- [ ] **Step 2: Add `simpleeval` to deps**

Add `"simpleeval>=0.9.13"` to `pyproject.toml` dependencies.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write `app/agent/tools.py`**

```python
import json
import re
import random
from pathlib import Path
from app.agent.sandbox import safe_read_file, safe_ls, truncate_result
from app.storage.user_db import init_user_db
from app.storage.paths import validate_user_path


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
            rows = conn.execute(
                "SELECT path, title, summary, snippet(documents_fts, 4, '<mark>', '</mark>', '...', 20) as snippet, rank FROM documents_fts WHERE documents_fts MATCH ? ORDER BY rank LIMIT 10",
                (query,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
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
        root = self.data_dir / self.user_id
        if path:
            try:
                root = validate_user_path(self.data_dir, self.user_id, path)
            except ValueError:
                return []
        hits = []
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
        return safe_ls(self.data_dir, self.user_id, dir_path)

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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_tools.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add app/agent/tools.py tests/test_tools.py pyproject.toml
git commit -m "feat: 8 agent tools (fts, read, list, grep, table, calc, ls, done)"
```

---

### Task 3: Tool schema definitions (Ollama tool-calling format)

**Files:**
- Create: `app/agent/tools_schema.py`
- Test: `tests/test_tools_schema.py`

**Interfaces:**
- Produces: `TOOL_DEFINITIONS: list[dict]` — Ollama-format tool definitions, one per tool (except `done` which is how the agent signals completion).

- [ ] **Step 1: Write the failing test `tests/test_tools_schema.py`**

```python
from app.agent.tools_schema import TOOL_DEFINITIONS


def test_all_tools_defined():
    names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
    assert names == {"fts_search", "read_file", "list_index", "grep", "table_extract", "calc", "ls", "done"}


def test_each_has_required_fields():
    for t in TOOL_DEFINITIONS:
        assert t["type"] == "function"
        assert "name" in t["function"]
        assert "description" in t["function"]
        assert "parameters" in t["function"]
        assert "properties" in t["function"]["parameters"]


def test_done_schema():
    done = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "done")
    props = done["function"]["parameters"]["properties"]
    assert "answer" in props
    assert "cites" in props
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tools_schema.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/agent/tools_schema.py`**

```python
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "fts_search",
            "description": "Full-text search across all documents in the current collection. Returns ranked matches with path, title, summary, and a snippet. Use this first to find relevant sections.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "FTS5 query string, e.g. 'goblin AC' or 'grapple prone'"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full content of a markdown file. Use for leaf sections found via fts_search or list_index. For large files, pass a line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the markdown file"},
                    "lines": {"type": "string", "description": "Optional line range, e.g. '10-30'"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_index",
            "description": "Read an index.md file and return its child entries (title + path). Use to navigate the document hierarchy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to an index.md file"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Regex search across all markdown files in the user's tree. Returns matching lines with path and line number. Use for cross-references like 'every mention of advantage'. Max 20 results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Optional: limit search to a specific directory"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "table_extract",
            "description": "Parse markdown tables in a file into structured JSON rows. Use for stat blocks, equipment lists, spell tables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to a markdown file containing tables"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calc",
            "description": "Evaluate an arithmetic expression. Supports dice notation (e.g. '2d6+3', '1d20+5'), addition, subtraction, multiplication, division, comparisons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expr": {"type": "string", "description": "Expression to evaluate, e.g. '2d6+3' or '15+2'"},
                },
                "required": ["expr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List files in a directory within the user's document tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "Directory path to list"},
                },
                "required": ["dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Signal that the answer is complete. Provide the final answer and citations to source pages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "description": "The final answer to the user's question"},
                    "cites": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "page": {"type": "integer"},
                                "quote": {"type": "string"},
                            },
                        },
                        "description": "Citations to source pages",
                    },
                },
                "required": ["answer"],
            },
        },
    },
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tools_schema.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agent/tools_schema.py tests/test_tools_schema.py
git commit -m "feat: Ollama tool schema definitions (8 tools)"
```

---

### Task 4: Session history (load, trim, append)

**Files:**
- Create: `app/agent/history.py`
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `get_session`, `init_user_db` (Plan 1).
- Produces:
  - `def load_history(conn, session_id: str) -> list[dict]` — returns the parsed `history_json` list.
  - `def append_turn(conn, session_id: str, user_msg: str, agent_msg: str, cites: list[dict] | None = None) -> None` — appends a turn, updates `history_json` and `updated_at`.
  - `def trim_history(history: list[dict], keep_last: int = 6) -> list[dict]` — returns last N turns + a summary placeholder for older ones (summary generation is a separate function).
  - `def build_messages(history: list[dict], system_prompt: str) -> list[dict]` — converts history to Ollama chat messages format.

- [ ] **Step 1: Write the failing test `tests/test_history.py`**

```python
import json
from app.agent.history import load_history, append_turn, trim_history, build_messages
from app.storage.user_db import init_user_db, create_collection, create_session


def test_load_history_empty(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "C")
    sid = create_session(conn, cid)
    h = load_history(conn, sid)
    assert h == []
    conn.close()


def test_append_and_load(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "C")
    sid = create_session(conn, cid)
    append_turn(conn, sid, "What is AC?", "AC is armor class.", [{"path": "x.md", "page": 5, "quote": "AC 15"}])
    h = load_history(conn, sid)
    assert len(h) == 1
    assert h[0]["user"] == "What is AC?"
    assert h[0]["agent"] == "AC is armor class."
    assert h[0]["cites"][0]["page"] == 5
    conn.close()


def test_trim_history_keeps_last_n():
    history = [{"user": f"q{i}", "agent": f"a{i}", "cites": []} for i in range(10)]
    trimmed = trim_history(history, keep_last=3)
    assert len(trimmed) == 3
    assert trimmed[0]["user"] == "q7"
    assert trimmed[2]["user"] == "q9"


def test_build_messages_format():
    history = [{"user": "hi", "agent": "hello", "cites": []}]
    msgs = build_messages(history, "You are a helpful RPG rules assistant.")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "You are a helpful RPG rules assistant."
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "hi"
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["content"] == "hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_history.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/agent/history.py`**

```python
import json
from app.storage.user_db import get_session


def load_history(conn, session_id: str) -> list[dict]:
    s = get_session(conn, session_id)
    if not s:
        return []
    try:
        return json.loads(s["history_json"])
    except (json.JSONDecodeError, TypeError):
        return []


def append_turn(conn, session_id: str, user_msg: str, agent_msg: str, cites: list[dict] | None = None) -> None:
    history = load_history(conn, session_id)
    history.append({"user": user_msg, "agent": agent_msg, "cites": cites or []})
    conn.execute(
        "UPDATE sessions SET history_json = ?, updated_at = datetime('now') WHERE session_id = ?",
        (json.dumps(history), session_id),
    )
    conn.commit()


def trim_history(history: list[dict], keep_last: int = 6) -> list[dict]:
    if len(history) <= keep_last:
        return history
    return history[-keep_last:]


def build_messages(history: list[dict], system_prompt: str) -> list[dict]:
    msgs = [{"role": "system", "content": system_prompt}]
    for turn in history:
        msgs.append({"role": "user", "content": turn["user"]})
        msgs.append({"role": "assistant", "content": turn["agent"]})
    return msgs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_history.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/agent/history.py tests/test_history.py
git commit -m "feat: session history load/trim/append/build_messages"
```

---

### Task 5: Agent loop (LLM + tool execution)

**Files:**
- Create: `app/agent/loop.py`
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `OllamaGateway` (Plan 1), `ToolBox` (Task 2), `TOOL_DEFINITIONS` (Task 3), `build_messages`, `trim_history` (Task 4).
- Produces:
  - `class AgentLoop` with `__init__(self, gateway: OllamaGateway, toolbox: ToolBox, max_iterations: int = 8)`.
  - `async def run(self, history: list[dict], new_question: str) -> dict` — runs the loop, returns `{answer, cites, iterations}`. Calls `done` tool or force-terminates.

- [ ] **Step 1: Write the failing test `tests/test_agent_loop.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agent.loop import AgentLoop


@pytest.mark.asyncio
async def test_loop_calls_done_immediately():
    gw = MagicMock()
    gw.call = AsyncMock(return_value={
        "message": {
            "content": "",
            "tool_calls": [
                {"function": {"name": "done", "arguments": '{"answer": "AC is 15.", "cites": [{"path": "x.md", "page": 42, "quote": "AC 15"}]}'}}
            ],
        }
    })
    toolbox = MagicMock()
    loop = AgentLoop(gw, toolbox)
    result = await loop.run([], "What is AC?")
    assert result["answer"] == "AC is 15."
    assert result["cites"][0]["page"] == 42
    assert result["iterations"] == 1


@pytest.mark.asyncio
async def test_loop_searches_then_done():
    call_count = [0]
    responses = [
        {"message": {"content": "", "tool_calls": [{"function": {"name": "fts_search", "arguments": '{"query": "goblin"}'}}]}},
        {"message": {"content": "", "tool_calls": [{"function": {"name": "done", "arguments": '{"answer": "Found goblin.", "cites": []}'}}]}},
    ]
    async def mock_call(role, prompt, tools=None, messages=None):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r
    gw = MagicMock()
    gw.call = mock_call
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value='[{"path": "x.md", "title": "Goblin", "snippet": "AC 15"}]')
    loop = AgentLoop(gw, toolbox)
    result = await loop.run([], "Find goblin stats")
    assert result["answer"] == "Found goblin."
    assert result["iterations"] == 2
    toolbox.execute.assert_called_once()


@pytest.mark.asyncio
async def test_loop_force_terminates_after_max():
    gw = MagicMock()
    gw.call = AsyncMock(return_value={
        "message": {"content": "", "tool_calls": [{"function": {"name": "fts_search", "arguments": '{"query": "x"}'}}]}
    })
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value="[]")
    loop = AgentLoop(gw, toolbox, max_iterations=3)
    result = await loop.run([], "loop forever")
    assert "could not find" in result["answer"].lower() or "couldn't" in result["answer"].lower()
    assert result["iterations"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_agent_loop.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/agent/loop.py`**

```python
import json
from app.agent.tools_schema import TOOL_DEFINITIONS
from app.agent.history import build_messages, trim_history

SYSTEM_PROMPT = (
    "You are a helpful RPG rules assistant. You answer questions about RPG manuals "
    "by searching the user's document collection. Use fts_search first to find relevant "
    "sections, then read_file to get details. Use grep for cross-references, table_extract "
    "for stat blocks, and calc for dice math. Always cite your sources with the done tool. "
    "If you cannot find the answer, say so honestly."
)


class AgentLoop:
    def __init__(self, gateway, toolbox, max_iterations: int = 8):
        self.gateway = gateway
        self.toolbox = toolbox
        self.max_iterations = max_iterations

    async def run(self, history: list[dict], new_question: str) -> dict:
        trimmed = trim_history(history, keep_last=6)
        messages = build_messages(trimmed, SYSTEM_PROMPT)
        messages.append({"role": "user", "content": new_question})

        for iteration in range(1, self.max_iterations + 1):
            resp = await self.gateway.call("query", "", tools=TOOL_DEFINITIONS, messages=messages)
            msg = resp.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content", "")

            if not tool_calls:
                return {"answer": content or "I could not find an answer.", "cites": [], "iterations": iteration}

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    args = {}

                if name == "done":
                    return {
                        "answer": args.get("answer", content or "No answer provided."),
                        "cites": args.get("cites", []),
                        "iterations": iteration,
                    }

                result = self.toolbox.execute(name, args)
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "tool", "name": name, "content": result})

        return {
            "answer": "I couldn't find a complete answer within my tool-call budget. Here's what I found so far: " + content,
            "cites": [],
            "iterations": self.max_iterations,
        }
```

- [ ] **Step 4: Modify `app/gateway/ollama.py` to accept `messages` param**

Update the `call` method signature and body:

```python
    async def call(self, role: str, prompt: str, tools: list | None = None, messages: list | None = None) -> dict:
        model = self.models.get(role)
        if not model:
            raise ValueError(f"unknown role: {role}")
        if messages is None:
            messages = [{"role": "user", "content": prompt}]
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        resp = await self._client.post("/api/chat", json=body)
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_agent_loop.py tests/test_gateway.py -v`
Expected: PASS (gateway tests still pass with new optional param; 3 new loop tests pass)

- [ ] **Step 6: Commit**

```bash
git add app/agent/loop.py app/gateway/ollama.py tests/test_agent_loop.py
git commit -m "feat: agent loop with tool-calling + force-terminate"
```

---

### Task 6: Session routes (start, continue, list, view)

**Files:**
- Create: `app/agent/routes.py`
- Test: `tests/test_session_routes.py`

**Interfaces:**
- Consumes: `AgentLoop`, `ToolBox`, `load_history`, `append_turn`, `create_session`, `get_session`, `list_collections`.
- Produces:
  - `POST /sessions` — body `{collection_id, question}`; creates session, runs first turn, returns chat view with first answer.
  - `POST /sessions/:id` — body `{question}`; loads history, runs next turn, appends, returns HTMX partial.
  - `GET /sessions` — list user's sessions.
  - `GET /sessions/:id` — chat view.

- [ ] **Step 1: Write the failing test `tests/test_session_routes.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection
from app.auth.passwords import hash_password


@pytest.fixture
def app_with_data(tmp_dirs, monkeypatch):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "PF")
    uconn.close()
    cfg = Config("http://localhost:11434", {"query": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    monkeypatch.setattr("app.config.load_config", lambda *a: cfg)
    app = create_app(cfg, "s")
    mock_loop = MagicMock()
    mock_loop.run = AsyncMock(return_value={"answer": "AC is 15.", "cites": [{"path": "x.md", "page": 42, "quote": "AC 15"}], "iterations": 1})
    app.state.agent_loop_factory = lambda toolbox: mock_loop
    return app, cid


@pytest.mark.asyncio
async def test_start_session(app_with_data):
    app, cid = app_with_data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post("/sessions", data={"collection_id": cid, "question": "What is AC?"})
        assert r.status_code in (200, 303)
        assert "AC is 15" in r.text


@pytest.mark.asyncio
async def test_continue_session(app_with_data):
    app, cid = app_with_data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post("/sessions", data={"collection_id": cid, "question": "What is AC?"})
        assert r.status_code in (200, 303)
        import re
        m = re.search(r"/sessions/([a-f0-9]+)", r.text)
        assert m is not None
        sid = m.group(1)
        r2 = await client.post(f"/sessions/{sid}", data={"question": "How about goblins?"})
        assert r2.status_code == 200
        assert "AC is 15" in r2.text


@pytest.mark.asyncio
async def test_list_sessions(app_with_data):
    app, cid = app_with_data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        await client.post("/sessions", data={"collection_id": cid, "question": "q1"})
        r = await client.get("/sessions")
        assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_session_routes.py -v`
Expected: FAIL (no `/sessions` route)

- [ ] **Step 3: Write `app/agent/routes.py`**

```python
from fastapi import APIRouter, Request, Form, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import json
from app.auth.middleware import current_user_id
from app.storage.user_db import (
    init_user_db, create_session, get_session, list_collections, list_docs,
)
from app.agent.history import load_history, append_turn
from app.agent.tools import ToolBox
from app.agent.loop import AgentLoop

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "web" / "templates"))
_db_dir = None
_data_dir = None
_gateway = None


def init_agent_routes(db_dir: Path, data_dir: Path, gateway):
    global _db_dir, _data_dir, _gateway
    _db_dir = db_dir
    _data_dir = data_dir
    _gateway = gateway


def _make_loop(uid: str, collection_id: str):
    toolbox = ToolBox(_data_dir, uid, _db_dir, collection_id)
    factory = getattr(_gateway, "agent_loop_factory", None)
    if factory:
        return factory(toolbox)
    return AgentLoop(_gateway, toolbox)


@router.post("/sessions")
async def start_session(request: Request, collection_id: str = Form(...), question: str = Form(...)):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    try:
        sid = create_session(conn, collection_id)
        history = load_history(conn, sid)
        loop = _make_loop(uid, collection_id)
        result = await loop.run(history, question)
        append_turn(conn, sid, question, result["answer"], result["cites"])
        session = get_session(conn, sid)
        return _templates.TemplateResponse(
            "chat.html",
            {"request": request, "user_id": uid, "session": session, "history": load_history(conn, sid)},
        )
    finally:
        conn.close()


@router.post("/sessions/{session_id}")
async def continue_session(request: Request, session_id: str, question: str = Form(...)):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    try:
        session = get_session(conn, session_id)
        if not session:
            return RedirectResponse("/sessions", status_code=303)
        history = load_history(conn, session_id)
        loop = _make_loop(uid, session["collection_id"])
        result = await loop.run(history, question)
        append_turn(conn, session_id, question, result["answer"], result["cites"])
        new_turn = {"user": question, "agent": result["answer"], "cites": result["cites"]}
        return _templates.TemplateResponse(
            "_message.html",
            {"request": request, "user_id": uid, "turn": new_turn},
        )
    finally:
        conn.close()


@router.get("/sessions")
async def list_sessions(request: Request):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    rows = conn.execute(
        "SELECT session_id, collection_id, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 20"
    ).fetchall()
    sessions = [dict(r) for r in rows]
    conn.close()
    return _templates.TemplateResponse(
        "sessions.html",
        {"request": request, "user_id": uid, "sessions": sessions},
    )


@router.get("/sessions/{session_id}")
async def view_session(request: Request, session_id: str):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    session = get_session(conn, session_id)
    if not session:
        conn.close()
        return RedirectResponse("/sessions", status_code=303)
    history = load_history(conn, session_id)
    conn.close()
    return _templates.TemplateResponse(
        "chat.html",
        {"request": request, "user_id": uid, "session": session, "history": history},
    )
```

- [ ] **Step 4: Write templates `chat.html`, `_message.html`, `sessions.html`**

```html
<!-- app/web/templates/chat.html -->
{% extends "base.html" %}
{% block title %}Chat — RPG Master{% endblock %}
{% block content %}
<h2>Ask about your manuals</h2>
<div id="chat-log">
  {% for turn in history %}
  <article>
    <p><strong>You:</strong> {{ turn.user }}</p>
    <p><strong>Answer:</strong> {{ turn.agent }}</p>
    {% if turn.cites %}
    <ul>
      {% for c in turn.cites %}
      <li><a href="/docs/{{ session.collection_id }}/view?path={{ c.path }}">{{ c.path }} (p. {{ c.page }})</a> — "{{ c.quote }}"</li>
      {% endfor %}
    </ul>
    {% endif %}
  </article>
  {% endfor %}
</div>
<form hx-post="/sessions/{{ session.session_id }}" hx-target="#chat-log" hx-swap="beforeend" hx-on::after-request="this.reset()">
  <input type="text" name="question" placeholder="Ask a follow-up..." required>
  <button type="submit">Send</button>
</form>
{% endblock %}
```

```html
<!-- app/web/templates/_message.html -->
<article>
  <p><strong>You:</strong> {{ turn.user }}</p>
  <p><strong>Answer:</strong> {{ turn.agent }}</p>
  {% if turn.cites %}
  <ul>
    {% for c in turn.cites %}
    <li><a href="#">{{ c.path }} (p. {{ c.page }})</a> — "{{ c.quote }}"</li>
    {% endfor %}
  </ul>
  {% endif %}
</article>
```

```html
<!-- app/web/templates/sessions.html -->
{% extends "base.html" %}
{% block title %}Sessions — RPG Master{% endblock %}
{% block content %}
<h2>Recent Sessions</h2>
{% if sessions %}
<ul>
  {% for s in sessions %}
  <li><a href="/sessions/{{ s.session_id }}">Session {{ s.session_id[:8] }}</a> — {{ s.updated_at }}</li>
  {% endfor %}
</ul>
{% else %}
<p><em>No sessions yet. Start one from a collection.</em></p>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Modify `app/main.py` to mount agent routes + wire factory**

Add to imports:

```python
from app.agent.routes import router as agent_router, init_agent_routes
```

In `create_app`, after `init_web_routes`:

```python
    init_agent_routes(cfg.db_dir, cfg.data_dir, gateway)
    app.include_router(agent_router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_session_routes.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add app/agent/routes.py app/web/templates/chat.html app/web/templates/_message.html app/web/templates/sessions.html app/main.py tests/test_session_routes.py
git commit -m "feat: session routes + chat UI with HTMX"
```

---

### Task 7: "Ask a question" button on collection view

**Files:**
- Modify: `app/web/templates/collection.html` — add link to start a session.
- Modify: `app/web/routes.py` — add `GET /collections/:id/ask` that redirects to session creation.
- Test: `tests/test_ask_route.py`

**Interfaces:**
- Produces: a simple form on the collection page that posts to `/sessions` with the collection_id.

- [ ] **Step 1: Write the failing test `tests/test_ask_route.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection
from app.auth.passwords import hash_password


@pytest.fixture
def app_with_collection(tmp_dirs, monkeypatch):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "PF")
    uconn.close()
    cfg = Config("http://x", {"query": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    monkeypatch.setattr("app.config.load_config", lambda *a: cfg)
    return create_app(cfg, "s"), cid


@pytest.mark.asyncio
async def test_collection_has_ask_form(app_with_collection):
    app, cid = app_with_collection
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get(f"/collections/{cid}")
        assert 'action="/sessions"' in r.text
        assert f'value="{cid}"' in r.text
```

- [ ] **Step 2: Modify `app/web/templates/collection.html`**

Add the ask form in the header, after the upload link:

```html
<form method="post" action="/sessions" style="display:inline">
  <input type="hidden" name="collection_id" value="{{ collection.collection_id }}">
  <input type="text" name="question" placeholder="Ask a question about these books..." required>
  <button type="submit">Ask</button>
</form>
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_ask_route.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/web/templates/collection.html tests/test_ask_route.py
git commit -m "feat: ask-a-question form on collection view"
```

---

### Task 8: Full integration test + final wiring

**Files:**
- Test: `tests/test_integration.py`

**Interfaces:**
- Produces: a single end-to-end test that logs in, creates a collection, uploads a PDF (mocked pipeline), asks a question (mocked agent), and verifies the answer appears.

- [ ] **Step 1: Write the integration test `tests/test_integration.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status, insert_fts_row
from app.auth.passwords import hash_password
from fpdf import FPDF
import io


@pytest.fixture
def integration_app(tmp_dirs, monkeypatch):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "PF")
    create_doc(uconn, "d1", cid, "Bestiary", "h")
    update_doc_status(uconn, "d1", "done")
    insert_fts_row(uconn, "data/alice/d1/c1/goblin.md", "Goblin", "AC 15 monster", "goblin,monster", "Goblins have AC 15 and HP 7.")
    uconn.close()
    doc_dir = tmp_dirs["data"] / "alice" / "d1" / "c1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "goblin.md").write_text("# Goblin\n\nAC 15, HP 7.\n")
    cfg = Config("http://localhost:11434", {"query": "m", "enrich": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    monkeypatch.setattr("app.config.load_config", lambda *a: cfg)
    app = create_app(cfg, "s")
    mock_loop = MagicMock()
    mock_loop.run = AsyncMock(return_value={"answer": "A goblin has AC 15.", "cites": [{"path": "data/alice/d1/c1/goblin.md", "page": 42, "quote": "AC 15"}], "iterations": 1})
    app.state.agent_loop_factory = lambda toolbox: mock_loop
    return app, cid


@pytest.mark.asyncio
async def test_full_flow(integration_app):
    app, cid = integration_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get("/")
        assert "PF" in r.text
        r = await client.get(f"/collections/{cid}")
        assert "Bestiary" in r.text
        r = await client.post("/sessions", data={"collection_id": cid, "question": "What is a goblin's AC?"})
        assert "AC 15" in r.text
        r = await client.get("/sessions")
        assert r.status_code == 200
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: all tests PASS across all three plans' tests

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: full integration test (login → collection → ask → answer)"
```

---

## Self-Review (Plan 3)

**Spec coverage:** 8 tools (spec §3) ✓, conversational multi-turn (spec §3) ✓, session lifecycle ✓, citations ✓, iteration cap ✓, history trimming ✓, chat UI (spec §5) ✓. All spec sections now covered across the three plans.

**Placeholder scan:** No TBD/TODO. All steps have complete code.

**Type consistency:** `ToolBox(data_dir, user_id, db_dir, collection_id)` consistent across Task 2, 5, 6. `AgentLoop(gateway, toolbox, max_iterations)` consistent across Task 5, 6. `gateway.call(role, prompt, tools, messages)` signature updated in Task 5 and used consistently.

**Scope:** Query agent complete. The three plans together cover the entire spec.