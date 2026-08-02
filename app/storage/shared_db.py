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