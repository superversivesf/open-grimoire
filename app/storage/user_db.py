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