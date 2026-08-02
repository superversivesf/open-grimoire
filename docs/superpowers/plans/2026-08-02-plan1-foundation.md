# RPG Manual Query Engine — Plan 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the runnable foundation: storage layer, per-user SQLite, auth, LLM gateway skeleton, and a minimal web app (login + empty library page). No upload, no pipeline, no agent yet.

**Architecture:** FastAPI app with layered storage (`shared.sqlite` for users/config, `db/<user_id>.sqlite` per user for their data). Auth via signed session cookies. LLM gateway wraps Ollama HTTP, role-based, config-driven. One deployable process.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Jinja2 templates, HTMX, pico.css, `argon2-cffi`, `pyyaml`, `httpx` (for Ollama client), `pytest`, `pytest-asyncio`, `httpx` (test client).

## Global Constraints

- Python 3.11+
- Per-user SQLite: one `db/<user_id>.sqlite` file per user; `db/shared.sqlite` for app-level state.
- Filesystem root: `data/<user_id>/` per user; all paths validated against this root.
- Config at `config.yaml`; role-based model mapping (`query`, `enrich`, `structure`, `vision`).
- Ollama default host: `http://localhost:11434`.
- Password hashing: `argon2-cffi`.
- Session cookie: HMAC-signed, contains `user_id` + expiry.
- No JS build step; HTMX via CDN script tag in base template.
- Tests use `pytest`; every task ends with a passing test.
- Commit after each task.

---

## File Structure

```
rpg-master/
├── config.yaml                       # app config (ollama host, model roles)
├── pyproject.toml                    # deps + project metadata
├── app/
│   ├── __init__.py
│   ├── main.py                       # FastAPI app factory, route mounting
│   ├── config.py                     # load config.yaml, expose settings
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── shared_db.py              # shared.sqlite: users, app_config, queue_jobs
│   │   ├── user_db.py                # per-user sqlite: open/get/create
│   │   └── paths.py                  # path validation + user data dir helpers
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── passwords.py              # argon2 hash/verify
│   │   ├── session.py                # HMAC sign/verify session cookie
│   │   ├── middleware.py             # FastAPI middleware: resolve user from cookie
│   │   └── routes.py                 # /login, /logout
│   ├── gateway/
│   │   ├── __init__.py
│   │   └── ollama.py                 # role-based Ollama HTTP client
│   ├── web/
│   │   ├── __init__.py
│   │   ├── routes.py                 # / (library), /collections (stub)
│   │   └── templates/
│   │       ├── base.html             # pico.css + HTMX CDN
│   │       ├── login.html
│   │       └── library.html          # empty library + collections grid (empty)
│   └── cli/
│       ├── __init__.py
│       └── user.py                   # `python -m app.cli user create`
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # fixtures: tmp data/db dirs, test config
│   ├── test_paths.py
│   ├── test_shared_db.py
│   ├── test_user_db.py
│   ├── test_passwords.py
│   ├── test_session.py
│   ├── test_auth_routes.py
│   ├── test_gateway.py
│   ├── test_web_routes.py
│   └── test_cli_user.py
├── data/                             # gitignored, runtime-created
└── db/                               # gitignored, runtime-created
```

---

### Task 1: Project scaffold + config loader

**Files:**
- Create: `pyproject.toml`
- Create: `config.yaml`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `app.config.load_config(path: str) -> Config` where `Config` is a dataclass with `ollama_host: str`, `models: dict[str, str]` (role→model name), `data_dir: Path`, `db_dir: Path`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "rpg-master"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "jinja2>=3.1",
    "argon2-cffi>=23.1",
    "pyyaml>=6.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Write `config.yaml`**

```yaml
ollama:
  host: http://localhost:11434

models:
  query: qwen2.5:7b-instruct-q4
  enrich: gemma3:4b-it-q4
  structure: qwen2.5:7b-instruct-q4
  vision: gemma3:4b-it-q4

paths:
  data_dir: ./data
  db_dir: ./db
```

- [ ] **Step 3: Write `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
data/
db/
*.egg-info/
.venv/
```

- [ ] **Step 4: Write the failing test `tests/test_config.py`**

```python
from pathlib import Path
from app.config import load_config, Config


def test_load_config_reads_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """
ollama:
  host: http://localhost:11434
models:
  query: qwen2.5:7b-instruct-q4
  enrich: gemma3:4b-it-q4
  structure: qwen2.5:7b-instruct-q4
  vision: gemma3:4b-it-q4
paths:
  data_dir: ./data
  db_dir: ./db
"""
    )
    cfg = load_config(str(cfg_file))
    assert isinstance(cfg, Config)
    assert cfg.ollama_host == "http://localhost:11434"
    assert cfg.models["query"] == "qwen2.5:7b-instruct-q4"
    assert cfg.models["enrich"] == "gemma3:4b-it-q4"
    assert cfg.data_dir == Path("./data")
    assert cfg.db_dir == Path("./db")


def test_load_config_defaults_models_to_empty(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("ollama:\n  host: http://x\npaths:\n  data_dir: ./d\n  db_dir: ./b\n")
    cfg = load_config(str(cfg_file))
    assert cfg.models == {}
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 6: Write `app/__init__.py` (empty)**

```python
```

- [ ] **Step 7: Write `app/config.py`**

```python
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class Config:
    ollama_host: str
    models: dict[str, str] = field(default_factory=dict)
    data_dir: Path = Path("./data")
    db_dir: Path = Path("./db")


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    ollama = raw.get("ollama", {})
    models = raw.get("models", {})
    paths = raw.get("paths", {})
    return Config(
        ollama_host=ollama.get("host", "http://localhost:11434"),
        models=dict(models),
        data_dir=Path(paths.get("data_dir", "./data")),
        db_dir=Path(paths.get("db_dir", "./db")),
    )
```

- [ ] **Step 8: Write `tests/__init__.py` (empty) and `tests/conftest.py`**

```python
# tests/__init__.py
```

```python
# tests/conftest.py
import pytest
from pathlib import Path
import tempfile
import os


@pytest.fixture
def tmp_dirs(tmp_path):
    data_dir = tmp_path / "data"
    db_dir = tmp_path / "db"
    data_dir.mkdir()
    db_dir.mkdir()
    return {"data": data_dir, "db": db_dir}
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml config.yaml .gitignore app/__init__.py app/config.py tests/__init__.py tests/conftest.py tests/test_config.py
git commit -m "feat: project scaffold + config loader"
```

---

### Task 2: Path validation + user data dir helpers

**Files:**
- Create: `app/storage/__init__.py`
- Create: `app/storage/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: `Config.data_dir`, `Config.db_dir` from Task 1.
- Produces:
  - `user_data_dir(data_dir: Path, user_id: str) -> Path` — returns and creates `data_dir/user_id/`.
  - `user_db_path(db_dir: Path, user_id: str) -> Path` — returns `db_dir/<user_id>.sqlite`.
  - `validate_user_path(data_dir: Path, user_id: str, target: str) -> Path` — resolves target against `data_dir/user_id/`, rejects escapes.

- [ ] **Step 1: Write the failing test `tests/test_paths.py`**

```python
import pytest
from pathlib import Path
from app.storage.paths import user_data_dir, user_db_path, validate_user_path


def test_user_data_dir_creates_and_returns(tmp_dirs):
    d = user_data_dir(tmp_dirs["data"], "alice")
    assert d == tmp_dirs["data"] / "alice"
    assert d.is_dir()


def test_user_db_path_returns_path(tmp_dirs):
    p = user_db_path(tmp_dirs["db"], "alice")
    assert p == tmp_dirs["db"] / "alice.sqlite"


def test_validate_user_path_accepts_inside(tmp_dirs):
    target = str(tmp_dirs["data"] / "alice" / "doc1" / "index.md")
    result = validate_user_path(tmp_dirs["data"], "alice", target)
    assert result == (tmp_dirs["data"] / "alice" / "doc1" / "index.md").resolve()


def test_validate_user_path_rejects_dotdot(tmp_dirs):
    target = str(tmp_dirs["data"] / "alice" / ".." / "bob" / "secret.md")
    with pytest.raises(ValueError, match="outside"):
        validate_user_path(tmp_dirs["data"], "alice", target)


def test_validate_user_path_rejects_absolute(tmp_dirs):
    with pytest.raises(ValueError, match="outside"):
        validate_user_path(tmp_dirs["data"], "alice", "/etc/passwd")


def test_validate_user_path_rejects_symlink_escape(tmp_dirs):
    alice_dir = tmp_dirs["data"] / "alice"
    alice_dir.mkdir()
    outside = tmp_dirs["data"] / "outside.txt"
    outside.write_text("nope")
    link = alice_dir / "link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        validate_user_path(tmp_dirs["data"], "alice", str(link))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/storage/__init__.py` (empty) and `app/storage/paths.py`**

```python
# app/storage/__init__.py
```

```python
# app/storage/paths.py
from pathlib import Path
import os


def user_data_dir(data_dir: Path, user_id: str) -> Path:
    p = data_dir / user_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_db_path(db_dir: Path, user_id: str) -> Path:
    return db_dir / f"{user_id}.sqlite"


def validate_user_path(data_dir: Path, user_id: str, target: str) -> Path:
    user_root = (data_dir / user_id).resolve()
    resolved = Path(target).resolve()
    if not str(resolved).startswith(str(user_root) + os.sep) and resolved != user_root:
        raise ValueError(f"path outside user tree: {target}")
    return resolved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_paths.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/storage/__init__.py app/storage/paths.py tests/test_paths.py
git commit -m "feat: path validation + user data dir helpers"
```

---

### Task 3: Shared DB (users table + app config)

**Files:**
- Create: `app/storage/shared_db.py`
- Test: `tests/test_shared_db.py`

**Interfaces:**
- Consumes: `Config.db_dir` from Task 1.
- Produces:
  - `init_shared_db(db_dir: Path) -> sqlite3.Connection` — creates `shared.sqlite`, schema, returns connection.
  - `create_user(conn, username: str, password_hash: str, is_admin: bool=False) -> str` — inserts user, returns `user_id` (uuid4 hex).
  - `get_user_by_username(conn, username: str) -> dict | None` — returns `{user_id, username, password_hash, is_admin, created_at}` or None.
  - `get_user_by_id(conn, user_id: str) -> dict | None`
  - `list_users(conn) -> list[dict]`

- [ ] **Step 1: Write the failing test `tests/test_shared_db.py`**

```python
import sqlite3
from app.storage.shared_db import (
    init_shared_db, create_user, get_user_by_username, get_user_by_id, list_users,
)


def test_init_creates_schema(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    assert "users" in tables
    assert "app_config" in tables
    conn.close()


def test_create_user_returns_id(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", "hash123")
    assert isinstance(uid, str) and len(uid) == 32
    conn.close()


def test_get_user_by_username(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", "hash123", is_admin=True)
    u = get_user_by_username(conn, "alice")
    assert u["user_id"] == uid
    assert u["username"] == "alice"
    assert u["password_hash"] == "hash123"
    assert u["is_admin"] == 1
    conn.close()


def test_get_user_by_username_missing_returns_none(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    assert get_user_by_username(conn, "nobody") is None
    conn.close()


def test_get_user_by_id(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "bob", "h")
    u = get_user_by_id(conn, uid)
    assert u["username"] == "bob"
    conn.close()


def test_list_users(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", "h1")
    create_user(conn, "bob", "h2")
    users = list_users(conn)
    assert len(users) == 2
    assert {u["username"] for u in users} == {"alice", "bob"}
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_shared_db.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/storage/shared_db.py`**

```python
import sqlite3
import uuid
from pathlib import Path


def init_shared_db(db_dir: Path) -> sqlite3.Connection:
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_dir / "shared.sqlite")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    return conn


def create_user(conn, username: str, password_hash: str, is_admin: bool = False) -> str:
    user_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO users (user_id, username, password_hash, is_admin) VALUES (?, ?, ?, ?)",
        (user_id, username, password_hash, 1 if is_admin else 0),
    )
    conn.commit()
    return user_id


def get_user_by_username(conn, username: str) -> dict | None:
    row = conn.execute(
        "SELECT user_id, username, password_hash, is_admin, created_at FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    return dict(row) if row else None


def get_user_by_id(conn, user_id: str) -> dict | None:
    row = conn.execute(
        "SELECT user_id, username, password_hash, is_admin, created_at FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def list_users(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT user_id, username, is_admin, created_at FROM users ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_shared_db.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/storage/shared_db.py tests/test_shared_db.py
git commit -m "feat: shared db with users table"
```

---

### Task 4: Per-user DB (schema: collections, docs, sessions)

**Files:**
- Create: `app/storage/user_db.py`
- Test: `tests/test_user_db.py`

**Interfaces:**
- Consumes: `Config.db_dir`, `user_db_path` from Task 2.
- Produces:
  - `init_user_db(db_dir: Path, user_id: str) -> sqlite3.Connection` — creates `<user_id>.sqlite` with `collections`, `docs`, `sessions` tables (no FTS5 yet — added in Plan 2). Returns connection.
  - `create_collection(conn, name: str) -> str` — returns collection_id.
  - `list_collections(conn) -> list[dict]`
  - `create_doc(conn, doc_id: str, collection_id: str, title: str, sha256: str) -> None`
  - `list_docs(conn, collection_id: str | None = None) -> list[dict]`
  - `create_session(conn, collection_id: str) -> str` — returns session_id.
  - `get_session(conn, session_id: str) -> dict | None`

- [ ] **Step 1: Write the failing test `tests/test_user_db.py`**

```python
import sqlite3
from app.storage.user_db import (
    init_user_db, create_collection, list_collections, create_doc, list_docs,
    create_session, get_session,
)


def test_init_creates_schema(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    assert "collections" in tables
    assert "docs" in tables
    assert "sessions" in tables
    conn.close()


def test_create_collection_returns_id(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "Pathfinder shelf")
    assert isinstance(cid, str) and len(cid) == 32
    conn.close()


def test_list_collections(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    create_collection(conn, "Pathfinder")
    create_collection(conn, "D&D")
    cols = list_collections(conn)
    assert len(cols) == 2
    assert {c["name"] for c in cols} == {"Pathfinder", "D&D"}
    conn.close()


def test_create_doc_and_list(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "PF")
    create_doc(conn, "doc1", cid, "Bestiary", "abc123")
    docs = list_docs(conn, cid)
    assert len(docs) == 1
    assert docs[0]["title"] == "Bestiary"
    assert docs[0]["sha256"] == "abc123"
    conn.close()


def test_list_docs_all_when_no_collection(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    c1 = create_collection(conn, "A")
    c2 = create_collection(conn, "B")
    create_doc(conn, "d1", c1, "t1", "h1")
    create_doc(conn, "d2", c2, "t2", "h2")
    docs = list_docs(conn)
    assert len(docs) == 2
    conn.close()


def test_create_session_and_get(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "PF")
    sid = create_session(conn, cid)
    s = get_session(conn, sid)
    assert s["collection_id"] == cid
    assert s["history_json"] == "[]"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_user_db.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/storage/user_db.py`**

```python
import sqlite3
import uuid
from pathlib import Path
from app.storage.paths import user_db_path


def init_user_db(db_dir: Path, user_id: str) -> sqlite3.Connection:
    p = user_db_path(db_dir, user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            collection_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS docs (
            doc_id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            title TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            page_count INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (collection_id) REFERENCES collections(collection_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            history_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (collection_id) REFERENCES collections(collection_id)
        )
        """
    )
    conn.commit()
    return conn


def create_collection(conn, name: str) -> str:
    cid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO collections (collection_id, name) VALUES (?, ?)",
        (cid, name),
    )
    conn.commit()
    return cid


def list_collections(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT collection_id, name, created_at FROM collections ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def create_doc(conn, doc_id: str, collection_id: str, title: str, sha256: str) -> None:
    conn.execute(
        "INSERT INTO docs (doc_id, collection_id, title, sha256) VALUES (?, ?, ?, ?)",
        (doc_id, collection_id, title, sha256),
    )
    conn.commit()


def list_docs(conn, collection_id: str | None = None) -> list[dict]:
    if collection_id:
        rows = conn.execute(
            "SELECT doc_id, collection_id, title, sha256, status, created_at FROM docs WHERE collection_id = ? ORDER BY created_at",
            (collection_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT doc_id, collection_id, title, sha256, status, created_at FROM docs ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def create_session(conn, collection_id: str) -> str:
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sessions (session_id, collection_id) VALUES (?, ?)",
        (sid, collection_id),
    )
    conn.commit()
    return sid


def get_session(conn, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT session_id, collection_id, history_json, created_at, updated_at FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_user_db.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/storage/user_db.py tests/test_user_db.py
git commit -m "feat: per-user db with collections, docs, sessions"
```

---

### Task 5: Password hashing (argon2)

**Files:**
- Create: `app/auth/__init__.py`
- Create: `app/auth/passwords.py`
- Test: `tests/test_passwords.py`

**Interfaces:**
- Produces:
  - `hash_password(plain: str) -> str`
  - `verify_password(plain: str, hashed: str) -> bool`

- [ ] **Step 1: Write the failing test `tests/test_passwords.py`**

```python
from app.auth.passwords import hash_password, verify_password


def test_hash_and_verify():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h) is True


def test_verify_wrong_password():
    h = hash_password("secret123")
    assert verify_password("wrong", h) is False


def test_hash_is_unique_per_call():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_passwords.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/auth/__init__.py` (empty) and `app/auth/passwords.py`**

```python
# app/auth/__init__.py
```

```python
# app/auth/passwords.py
from argon2 import PasswordHasher

_ph = PasswordHasher()


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        _ph.verify(hashed, plain)
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_passwords.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/auth/__init__.py app/auth/passwords.py tests/test_passwords.py
git commit -m "feat: argon2 password hashing"
```

---

### Task 6: Session cookie (HMAC sign/verify)

**Files:**
- Create: `app/auth/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Produces:
  - `sign_session(user_id: str, secret: str, ttl_seconds: int = 86400) -> str` — returns `base64(user_id:expiry).hmac`.
  - `verify_session(token: str, secret: str) -> str | None` — returns `user_id` if valid and not expired, else None.

- [ ] **Step 1: Write the failing test `tests/test_session.py`**

```python
import time
from app.auth.session import sign_session, verify_session


def test_sign_and_verify():
    token = sign_session("alice123", "secretkey", ttl_seconds=3600)
    assert verify_session(token, "secretkey") == "alice123"


def test_verify_wrong_secret():
    token = sign_session("alice123", "secretkey")
    assert verify_session(token, "wrong") is None


def test_verify_expired():
    token = sign_session("alice123", "secretkey", ttl_seconds=-1)
    assert verify_session(token, "secretkey") is None


def test_verify_tampered():
    token = sign_session("alice123", "secretkey")
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert verify_session(tampered, "secretkey") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/auth/session.py`**

```python
import base64
import hmac
import hashlib
import json
import time


def sign_session(user_id: str, secret: str, ttl_seconds: int = 86400) -> str:
    payload = {"user_id": user_id, "exp": int(time.time()) + ttl_seconds}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_session(token: str, secret: str) -> str | None:
    try:
        raw, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload.get("user_id")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_session.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/auth/session.py tests/test_session.py
git commit -m "feat: HMAC session cookie sign/verify"
```

---

### Task 7: Auth middleware + login/logout routes

**Files:**
- Create: `app/auth/middleware.py`
- Create: `app/auth/routes.py`
- Create: `app/web/templates/base.html`
- Create: `app/web/templates/login.html`
- Test: `tests/test_auth_routes.py`

**Interfaces:**
- Consumes: `hash_password`, `verify_password` (Task 5); `sign_session`, `verify_session` (Task 6); `init_shared_db`, `get_user_by_username` (Task 3).
- Produces:
  - `app.auth.middleware.auth_middleware` — FastAPI middleware that sets `request.state.user_id` from cookie, or None.
  - `app.auth.routes.router` — APIRouter with `GET /login`, `POST /login`, `POST /logout`.
  - Helper `current_user_id(request) -> str | None` — reads `request.state.user_id`.

- [ ] **Step 1: Write the failing test `tests/test_auth_routes.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.auth.passwords import hash_password


@pytest.fixture
def app_with_user(tmp_dirs, monkeypatch):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    cfg = Config(
        ollama_host="http://localhost:11434",
        models={},
        data_dir=tmp_dirs["data"],
        db_dir=tmp_dirs["db"],
    )
    monkeypatch.setattr("app.config.load_config", lambda *a: cfg)
    app = create_app(cfg, session_secret="testsecret")
    return app


@pytest.mark.asyncio
async def test_get_login_page(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/login")
        assert r.status_code == 200
        assert "login" in r.text.lower()


@pytest.mark.asyncio
async def test_login_success_sets_cookie(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "alice", "password": "pw123"})
        assert r.status_code in (200, 303)
        assert "session" in r.cookies


@pytest.mark.asyncio
async def test_login_wrong_password(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "alice", "password": "wrong"})
        assert r.status_code in (200, 401)
        assert "session" not in r.cookies


@pytest.mark.asyncio
async def test_login_unknown_user(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "nobody", "password": "x"})
        assert r.status_code in (200, 401)
        assert "session" not in r.cookies


@pytest.mark.asyncio
async def test_logout_clears_cookie(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw123"})
        r = await client.post("/logout")
        assert r.status_code in (200, 303)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write `app/web/templates/base.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}RPG Master{% endblock %}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/css@1/pico.min.css">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
</head>
<body>
  <nav class="container-fluid">
    <ul><li><strong>RPG Master</strong></li></ul>
    <ul>
      {% if user_id %}<li><a href="/">Library</a></li><li><form method="post" action="/logout" style="display:inline"><button type="submit" class="secondary">Logout</button></form></li>{% endif %}
    </ul>
  </nav>
  <main class="container">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 4: Write `app/web/templates/login.html`**

```html
{% extends "base.html" %}
{% block title %}Login — RPG Master{% endblock %}
{% block content %}
<article>
  <h2>Login</h2>
  {% if error %}<small style="color:var(--pico-color-red-500)">{{ error }}</small>{% endif %}
  <form method="post" action="/login">
    <label>Username<input type="text" name="username" required></label>
    <label>Password<input type="password" name="password" required></label>
    <button type="submit">Login</button>
  </form>
</article>
{% endblock %}
```

- [ ] **Step 5: Write `app/auth/middleware.py`**

```python
from starlette.middleware.base import BaseHTTPMiddleware
from app.auth.session import verify_session


def current_user_id(request) -> str | None:
    return getattr(request.state, "user_id", None)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, session_secret: str):
        super().__init__(app)
        self.session_secret = session_secret

    async def dispatch(self, request, call_next):
        token = request.cookies.get("session")
        request.state.user_id = verify_session(token, self.session_secret) if token else None
        return await call_next(request)
```

- [ ] **Step 6: Write `app/auth/routes.py`**

```python
from fastapi import APIRouter, Request, Form, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.auth.passwords import verify_password
from app.auth.session import sign_session
from app.storage.shared_db import init_shared_db, get_user_by_username

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "web" / "templates"))
_db_dir = None


def init_auth_routes(db_dir: Path):
    global _db_dir
    _db_dir = db_dir


@router.get("/login")
async def login_page(request: Request):
    return _templates.TemplateResponse("login.html", {"request": request, "user_id": None})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = init_shared_db(_db_dir)
    user = get_user_by_username(conn, username)
    conn.close()
    if not user or not verify_password(password, user["password_hash"]):
        return _templates.TemplateResponse(
            "login.html",
            {"request": request, "user_id": None, "error": "Invalid username or password"},
            status_code=401,
        )
    token = sign_session(user["user_id"], request.app.state.session_secret)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("session", token, httponly=True, max_age=86400, samesite="lax")
    return resp


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp
```

- [ ] **Step 7: Write `app/main.py` (minimal — full library route comes in Task 9)**

```python
from fastapi import FastAPI, Request
from pathlib import Path
from app.config import Config
from app.auth.middleware import AuthMiddleware
from app.auth.routes import router as auth_router, init_auth_routes


def create_app(cfg: Config, session_secret: str) -> FastAPI:
    app = FastAPI(title="RPG Master")
    app.state.config = cfg
    app.state.session_secret = session_secret
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.db_dir.mkdir(parents=True, exist_ok=True)
    init_auth_routes(cfg.db_dir)
    app.add_middleware(AuthMiddleware, session_secret=session_secret)
    app.include_router(auth_router)
    return app
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_auth_routes.py -v`
Expected: PASS (5 tests)

- [ ] **Step 9: Commit**

```bash
git add app/auth/middleware.py app/auth/routes.py app/web/templates/base.html app/web/templates/login.html app/main.py tests/test_auth_routes.py
git commit -m "feat: auth middleware + login/logout routes"
```

---

### Task 8: LLM gateway (role-based Ollama client)

**Files:**
- Create: `app/gateway/__init__.py`
- Create: `app/gateway/ollama.py`
- Test: `tests/test_gateway.py`

**Interfaces:**
- Consumes: `Config.ollama_host`, `Config.models` from Task 1.
- Produces:
  - `class OllamaGateway` with `__init__(self, host: str, models: dict[str, str])`.
  - `async def call(self, role: str, prompt: str, tools: list | None = None) -> dict` — maps role→model, POSTs to Ollama `/api/chat`, returns parsed response. Raises `ValueError` if role unknown.
  - `async def pull(self, model: str) -> None` — POSTs to `/api/pull` (best-effort; used by CLI).

- [ ] **Step 1: Write the failing test `tests/test_gateway.py`**

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.gateway.ollama import OllamaGateway


@pytest.mark.asyncio
async def test_call_maps_role_to_model():
    gw = OllamaGateway("http://ollama:11434", {"query": "qwen:7b", "enrich": "gemma:4b"})
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": {"content": "hello"}}
    mock_resp.raise_for_status = MagicMock()
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.post = AsyncMock(return_value=mock_resp)
        result = await gw.call("query", "hi")
        assert result["message"]["content"] == "hello"
        called_kwargs = client_inst.post.call_args
        body = called_kwargs.kwargs["json"]
        assert body["model"] == "qwen:7b"
        assert body["messages"][0]["content"] == "hi"


@pytest.mark.asyncio
async def test_call_unknown_role_raises():
    gw = OllamaGateway("http://x", {"query": "m"})
    with pytest.raises(ValueError, match="unknown role"):
        await gw.call("bogus", "hi")


@pytest.mark.asyncio
async def test_call_with_tools():
    gw = OllamaGateway("http://x", {"query": "m"})
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": {"content": "ok"}}
    mock_resp.raise_for_status = MagicMock()
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.post = AsyncMock(return_value=mock_resp)
        await gw.call("query", "hi", tools=[{"type": "function", "function": {"name": "f"}}])
        body = client_inst.post.call_args.kwargs["json"]
        assert "tools" in body
        assert len(body["tools"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/gateway/__init__.py` (empty) and `app/gateway/ollama.py`**

```python
# app/gateway/__init__.py
```

```python
# app/gateway/ollama.py
import httpx


class OllamaGateway:
    def __init__(self, host: str, models: dict[str, str]):
        self.host = host.rstrip("/")
        self.models = models
        self._client = httpx.AsyncClient(base_url=self.host, timeout=120.0)

    async def call(self, role: str, prompt: str, tools: list | None = None) -> dict:
        model = self.models.get(role)
        if not model:
            raise ValueError(f"unknown role: {role}")
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        resp = await self._client.post("/api/chat", json=body)
        resp.raise_for_status()
        return resp.json()

    async def pull(self, model: str) -> None:
        resp = await self._client.post("/api/pull", json={"name": model}, timeout=None)
        resp.raise_for_status()

    async def close(self):
        await self._client.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gateway.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/gateway/__init__.py app/gateway/ollama.py tests/test_gateway.py
git commit -m "feat: role-based Ollama gateway"
```

---

### Task 9: Library route + empty collections grid

**Files:**
- Create: `app/web/__init__.py`
- Create: `app/web/routes.py`
- Create: `app/web/templates/library.html`
- Modify: `app/main.py` (mount web router, require auth for `/`)
- Test: `tests/test_web_routes.py`

**Interfaces:**
- Consumes: `current_user_id` (Task 7), `init_user_db`, `list_collections` (Task 4).
- Produces:
  - `app.web.routes.router` — APIRouter with `GET /` (library) and `POST /collections` (create).
  - Library route requires auth (redirect to `/login` if no user); shows empty collections grid for new users.

- [ ] **Step 1: Write the failing test `tests/test_web_routes.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.auth.passwords import hash_password


@pytest.fixture
def app_with_user(tmp_dirs, monkeypatch):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    cfg = Config(
        ollama_host="http://localhost:11434",
        models={},
        data_dir=tmp_dirs["data"],
        db_dir=tmp_dirs["db"],
    )
    app = create_app(cfg, session_secret="testsecret")
    return app


@pytest.mark.asyncio
async def test_library_requires_auth(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/")
        assert r.status_code in (303, 307)
        assert "/login" in r.headers.get("location", "")


@pytest.mark.asyncio
async def test_library_empty_after_login(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw123"})
        r = await client.get("/")
        assert r.status_code == 200
        assert "collections" in r.text.lower() or "No collections" in r.text


@pytest.mark.asyncio
async def test_create_collection(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw123"})
        r = await client.post("/collections", data={"name": "Pathfinder shelf"})
        assert r.status_code in (200, 303)
        r2 = await client.get("/")
        assert "Pathfinder shelf" in r2.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_routes.py -v`
Expected: FAIL (no `/` route yet)

- [ ] **Step 3: Write `app/web/__init__.py` (empty), `app/web/routes.py`, and `app/web/templates/library.html`**

```python
# app/web/__init__.py
```

```python
# app/web/routes.py
from fastapi import APIRouter, Request, Form, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.auth.middleware import current_user_id
from app.storage.user_db import init_user_db, list_collections, create_collection

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_db_dir = None
_data_dir = None


def init_web_routes(db_dir: Path, data_dir: Path):
    global _db_dir, _data_dir
    _db_dir = db_dir
    _data_dir = data_dir


@router.get("/")
async def library(request: Request):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    cols = list_collections(conn)
    conn.close()
    return _templates.TemplateResponse(
        "library.html",
        {"request": request, "user_id": uid, "collections": cols},
    )


@router.post("/collections")
async def create_collection_route(request: Request, name: str = Form(...)):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    create_collection(conn, name)
    conn.close()
    return RedirectResponse("/", status_code=303)
```

```html
<!-- app/web/templates/library.html -->
{% extends "base.html" %}
{% block title %}Library — RPG Master{% endblock %}
{% block content %}
<header>
  <h2>Your Library</h2>
  <form method="post" action="/collections" style="display:flex; gap:0.5rem; align-items:end">
    <label>New collection<input type="text" name="name" placeholder="e.g. Pathfinder shelf" required></label>
    <button type="submit">Create</button>
  </form>
</header>
{% if collections %}
<div class="grid">
  {% for c in collections %}
  <article>
    <h3>{{ c.name }}</h3>
    <small>Created {{ c.created_at }}</small>
  </article>
  {% endfor %}
</div>
{% else %}
<p><em>No collections yet. Create one above to start uploading books.</em></p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Modify `app/main.py` to mount web router**

Replace the body of `create_app` with:

```python
from app.web.routes import router as web_router, init_web_routes

def create_app(cfg: Config, session_secret: str) -> FastAPI:
    app = FastAPI(title="RPG Master")
    app.state.config = cfg
    app.state.session_secret = session_secret
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.db_dir.mkdir(parents=True, exist_ok=True)
    init_auth_routes(cfg.db_dir)
    init_web_routes(cfg.db_dir, cfg.data_dir)
    app.add_middleware(AuthMiddleware, session_secret=session_secret)
    app.include_router(auth_router)
    app.include_router(web_router)
    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_web_routes.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add app/web/__init__.py app/web/routes.py app/web/templates/library.html app/main.py tests/test_web_routes.py
git commit -m "feat: library route + empty collections grid"
```

---

### Task 10: CLI user creation

**Files:**
- Create: `app/cli/__init__.py`
- Create: `app/cli/user.py`
- Test: `tests/test_cli_user.py`

**Interfaces:**
- Consumes: `init_shared_db`, `create_user` (Task 3), `hash_password` (Task 5).
- Produces: `python -m app.cli user create --username <name> [--admin] [--password <pw>]` (prompts for password if not given).

- [ ] **Step 1: Write the failing test `tests/test_cli_user.py`**

```python
import pytest
from click.testing import CliRunner
from app.cli.user import create_cmd
from app.storage.shared_db import init_shared_db, get_user_by_username
from app.auth.passwords import verify_password


def test_cli_create_user(tmp_dirs, monkeypatch):
    monkeypatch.setattr("app.cli.user._db_dir", tmp_dirs["db"])
    runner = CliRunner()
    result = runner.invoke(create_cmd, ["--username", "alice", "--password", "pw123"])
    assert result.exit_code == 0
    conn = init_shared_db(tmp_dirs["db"])
    u = get_user_by_username(conn, "alice")
    assert u is not None
    assert verify_password("pw123", u["password_hash"])
    assert u["is_admin"] == 0
    conn.close()


def test_cli_create_admin(tmp_dirs, monkeypatch):
    monkeypatch.setattr("app.cli.user._db_dir", tmp_dirs["db"])
    runner = CliRunner()
    result = runner.invoke(create_cmd, ["--username", "admin", "--password", "x", "--admin"])
    assert result.exit_code == 0
    conn = init_shared_db(tmp_dirs["db"])
    u = get_user_by_username(conn, "admin")
    assert u["is_admin"] == 1
    conn.close()
```

- [ ] **Step 2: Add `click` to deps in `pyproject.toml`**

Add `"click>=8.1"` to the `dependencies` list.

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_cli_user.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write `app/cli/__init__.py` (empty) and `app/cli/user.py`**

```python
# app/cli/__init__.py
```

```python
# app/cli/user.py
import click
from pathlib import Path
from app.storage.shared_db import init_shared_db, create_user
from app.auth.passwords import hash_password

_db_dir: Path | None = None


def _resolve_db_dir() -> Path:
    global _db_dir
    if _db_dir is not None:
        return _db_dir
    from app.config import load_config
    cfg = load_config("config.yaml")
    _db_dir = cfg.db_dir
    return _db_dir


@click.group()
def cli():
    pass


@cli.command("create")
@click.option("--username", required=True)
@click.option("--password", default=None)
@click.option("--admin", is_flag=True, default=False)
def create_cmd(username, password, admin):
    if not password:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    conn = init_shared_db(_resolve_db_dir())
    try:
        create_user(conn, username, hash_password(password), is_admin=admin)
        click.echo(f"Created user '{username}'{' (admin)' if admin else ''}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise click.exceptions.Exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    cli()
```

Also create `app/cli/__main__.py` so `python -m app.cli` works:

```python
# app/cli/__main__.py
from app.cli.user import cli

cli()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cli_user.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add app/cli/__init__.py app/cli/__main__.py app/cli/user.py tests/test_cli_user.py pyproject.toml
git commit -m "feat: CLI user creation"
```

---

### Task 11: Wire up entrypoint + smoke test

**Files:**
- Create: `app/__main__.py`
- Modify: `app/main.py` (add `if __name__` block or keep entrypoint separate)
- Test: manual smoke (no pytest here — just ensure `uvicorn` can start)

**Interfaces:**
- Produces: `python -m app` starts uvicorn on `127.0.0.1:8000`, reading `config.yaml` from cwd.

- [ ] **Step 1: Write `app/__main__.py`**

```python
import uvicorn
from app.main import create_app
from app.config import load_config


def main():
    cfg = load_config("config.yaml")
    app = create_app(cfg, session_secret="change-me-in-production")
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the full test suite passes**

Run: `pytest -v`
Expected: all tests PASS (Foundation complete)

- [ ] **Step 3: Smoke test (manual)**

Run: `python -m app.cli user create --username admin --password admin --admin`
Then: `python -m app`
Open `http://127.0.0.1:8000/login` — log in as admin/admin — see empty library.
(Ctrl-C to stop.)

- [ ] **Step 4: Commit**

```bash
git add app/__main__.py
git commit -m "feat: app entrypoint + uvicorn startup"
```

---

## Self-Review (Plan 1)

**Spec coverage:** Storage layer (spec §6) ✓, auth (spec §5) ✓, LLM gateway (spec §4) ✓, web app skeleton (spec §5) ✓. Upload, pipeline, agent are deferred to Plans 2 and 3 (intentional — this plan is Foundation only).

**Placeholder scan:** No TBD/TODO. All steps have complete code.

**Type consistency:** `create_app(cfg, session_secret)` signature consistent across Task 7, 9, 11. `init_auth_routes(db_dir)` and `init_web_routes(db_dir, data_dir)` signatures match their call sites. `Config` dataclass fields match all consumers.

**Scope:** Foundation only. Next: Plan 2 (Processing Pipeline) builds on this.