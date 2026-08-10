import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from app.storage.paths import user_db_path
from app.storage.migrations import init_user_db_with_migrations

DbConn = sqlite3.Connection


def init_user_db(db_dir: Path, user_id: str) -> DbConn:
    """Initialize user database with versioned migrations."""
    return init_user_db_with_migrations(db_dir, user_id)


def create_collection(conn: DbConn, name: str) -> str:
    cid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO collections (collection_id, name) VALUES (?, ?)",
        (cid, name),
    )
    conn.commit()
    return cid


def rename_collection(conn: DbConn, collection_id: str, name: str) -> None:
    conn.execute(
        "UPDATE collections SET name = ? WHERE collection_id = ?",
        (name, collection_id),
    )
    conn.commit()


def delete_collection(conn: DbConn, collection_id: str) -> None:
    # Delete all docs in this collection
    rows = conn.execute(
        "SELECT doc_id FROM docs WHERE collection_id = ?", (collection_id,)
    ).fetchall()
    for row in rows:
        delete_doc(conn, row["doc_id"])
    # Delete sessions for this collection
    conn.execute(
        "DELETE FROM turns WHERE session_id IN (SELECT session_id FROM sessions WHERE collection_id = ?)",
        (collection_id,),
    )
    conn.execute(
        "DELETE FROM sessions WHERE collection_id = ?", (collection_id,)
    )
    # Delete the collection
    conn.execute(
        "DELETE FROM collections WHERE collection_id = ?", (collection_id,)
    )
    conn.commit()


def list_collections(conn: DbConn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT collection_id, name, created_at FROM collections ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def create_doc(conn: DbConn, doc_id: str, collection_id: str, title: str, sha256: str) -> None:
    conn.execute(
        "INSERT INTO docs (doc_id, collection_id, title, sha256) VALUES (?, ?, ?, ?)",
        (doc_id, collection_id, title, sha256),
    )
    conn.commit()


def list_docs(conn: DbConn, collection_id: str | None = None) -> list[dict[str, Any]]:
    if collection_id:
        rows = conn.execute(
            "SELECT doc_id, collection_id, title, sha256, status, page_count, enrich_progress, enrich_total, created_at FROM docs WHERE collection_id = ? ORDER BY created_at",
            (collection_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT doc_id, collection_id, title, sha256, status, page_count, enrich_progress, enrich_total, created_at FROM docs ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def create_session(conn: DbConn, collection_id: str, name: str = "") -> str:
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sessions (session_id, collection_id, name) VALUES (?, ?, ?)",
        (sid, collection_id, name),
    )
    conn.commit()
    return sid


def get_session(conn: DbConn, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT session_id, collection_id, name, history_json, created_at, updated_at FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def list_turns(conn: DbConn, session_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT turn_index, user_msg, agent_msg, cites_json, suggestions_json FROM turns WHERE session_id = ? ORDER BY turn_index",
        (session_id,),
    ).fetchall()
    out = []
    for r in rows:
        try:
            cites = json.loads(r["cites_json"] or "[]")
        except json.JSONDecodeError:
            cites = []
        try:
            suggestions = json.loads(r["suggestions_json"] or "[]")
        except json.JSONDecodeError:
            suggestions = []
        out.append({
            "user": r["user_msg"],
            "agent": r["agent_msg"],
            "cites": cites,
            "suggestions": suggestions,
        })
    return out


def add_turn(conn: DbConn, session_id: str, user_msg: str, agent_msg: str, cites: list[dict[str, Any]] | None = None, suggestions: list[str] | None = None) -> None:
    # Serialize index computation with a transaction lock to prevent
    # race conditions when multiple threads add turns to the same session.
    # Use BEGIN IMMEDIATE to acquire a write lock immediately.
    in_transaction = conn.in_transaction
    if not in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT max(turn_index) as last FROM turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        next_index = (row["last"] if row and row["last"] is not None else -1) + 1
        conn.execute(
            "INSERT INTO turns (session_id, turn_index, user_msg, agent_msg, cites_json, suggestions_json) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, next_index, user_msg, agent_msg,
             json.dumps(cites or []), json.dumps(suggestions or [])),
        )
        conn.execute(
            "UPDATE sessions SET updated_at = datetime('now') WHERE session_id = ?",
            (session_id,),
        )
        if not in_transaction:
            conn.commit()
    except Exception:
        if not in_transaction:
            conn.rollback()
        raise


def insert_fts_row(conn: DbConn, path: str, title: str, summary: str, keywords: str, content: str) -> None:
    conn.execute(
        "INSERT INTO documents_fts (path, title, summary, keywords, content) VALUES (?, ?, ?, ?, ?)",
        (path, title, summary, keywords, content),
    )
    conn.commit()


def delete_fts_rows_for_doc(conn: DbConn, doc_id: str) -> None:
    conn.execute("DELETE FROM documents_fts WHERE path LIKE ?", (f"{doc_id}/%",))
    conn.commit()


def update_doc_status(conn: DbConn, doc_id: str, status: str) -> None:
    conn.execute("UPDATE docs SET status = ? WHERE doc_id = ?", (status, doc_id))
    conn.commit()


def update_enrich_progress(conn: DbConn, doc_id: str, progress: int, total: int) -> None:
    conn.execute(
        "UPDATE docs SET enrich_progress = ?, enrich_total = ? WHERE doc_id = ?",
        (progress, total, doc_id),
    )
    conn.commit()


def get_enrich_completed_paths(conn: DbConn, doc_id: str) -> list[str]:
    """Get list of leaf paths that have already been enriched."""
    row = conn.execute(
        "SELECT enrich_completed_paths FROM docs WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    if not row or not row["enrich_completed_paths"]:
        return []
    try:
        data = json.loads(row["enrich_completed_paths"])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def add_enrich_completed_path(conn: DbConn, doc_id: str, path: str) -> None:
    """Add a path to the list of completed enrich paths."""
    completed = get_enrich_completed_paths(conn, doc_id)
    if path not in completed:
        completed.append(path)
    conn.execute(
        "UPDATE docs SET enrich_completed_paths = ?, enrich_progress = ? WHERE doc_id = ?",
        (json.dumps(completed), len(completed), doc_id),
    )
    conn.commit()


def get_doc(conn: DbConn, doc_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT doc_id, collection_id, title, sha256, status, page_count, enrich_progress, enrich_total, created_at FROM docs WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_doc(conn: DbConn, doc_id: str) -> None:
    delete_fts_rows_for_doc(conn, doc_id)
    conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
    conn.commit()