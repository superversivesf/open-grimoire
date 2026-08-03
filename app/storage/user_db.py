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
            name TEXT NOT NULL DEFAULT '',
            history_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (collection_id) REFERENCES collections(collection_id)
        )
        """
    )
    # Add name column to existing sessions tables (migration for old DBs)
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN name TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            path, title, summary, keywords, content, tokenize='porter'
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


def rename_collection(conn, collection_id: str, name: str) -> None:
    conn.execute(
        "UPDATE collections SET name = ? WHERE collection_id = ?",
        (name, collection_id),
    )
    conn.commit()


def delete_collection(conn, collection_id: str) -> None:
    # Delete all docs in this collection
    rows = conn.execute(
        "SELECT doc_id FROM docs WHERE collection_id = ?", (collection_id,)
    ).fetchall()
    for row in rows:
        delete_doc(conn, row["doc_id"])
    # Delete sessions for this collection
    conn.execute(
        "DELETE FROM sessions WHERE collection_id = ?", (collection_id,)
    )
    # Delete the collection
    conn.execute(
        "DELETE FROM collections WHERE collection_id = ?", (collection_id,)
    )
    conn.commit()


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


def create_session(conn, collection_id: str, name: str = "") -> str:
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sessions (session_id, collection_id, name) VALUES (?, ?, ?)",
        (sid, collection_id, name),
    )
    conn.commit()
    return sid


def get_session(conn, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT session_id, collection_id, name, history_json, created_at, updated_at FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def insert_fts_row(conn, path: str, title: str, summary: str, keywords: str, content: str) -> None:
    conn.execute(
        "INSERT INTO documents_fts (path, title, summary, keywords, content) VALUES (?, ?, ?, ?, ?)",
        (path, title, summary, keywords, content),
    )
    conn.commit()


def delete_fts_rows_for_doc(conn, doc_id: str) -> None:
    conn.execute("DELETE FROM documents_fts WHERE path LIKE ?", (f"%/{doc_id}/%",))
    conn.commit()


def update_doc_status(conn, doc_id: str, status: str) -> None:
    conn.execute("UPDATE docs SET status = ? WHERE doc_id = ?", (status, doc_id))
    conn.commit()


def get_doc(conn, doc_id: str) -> dict | None:
    row = conn.execute(
        "SELECT doc_id, collection_id, title, sha256, status, page_count, created_at FROM docs WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_doc(conn, doc_id: str) -> None:
    delete_fts_rows_for_doc(conn, doc_id)
    conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
    conn.commit()