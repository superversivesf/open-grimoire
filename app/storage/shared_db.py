import sqlite3
import uuid
import uuid as _uuid
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_jobs (
            job_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            pdf_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON queue_jobs(status, created_at)")
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


def enqueue_job(conn, user_id: str, doc_id: str, pdf_path: str) -> str:
    job_id = _uuid.uuid4().hex
    conn.execute(
        "INSERT INTO queue_jobs (job_id, user_id, doc_id, pdf_path) VALUES (?, ?, ?, ?)",
        (job_id, user_id, doc_id, pdf_path),
    )
    conn.commit()
    return job_id


def claim_next_job(conn) -> dict | None:
    row = conn.execute(
        "SELECT job_id FROM queue_jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
    ).fetchone()
    if not row:
        return None
    job_id = row["job_id"]
    conn.execute(
        "UPDATE queue_jobs SET status = 'processing', updated_at = datetime('now') WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()
    return get_job(conn, job_id)


def complete_job(conn, job_id: str, error: str | None = None) -> None:
    status = "failed" if error else "done"
    conn.execute(
        "UPDATE queue_jobs SET status = ?, error = ?, updated_at = datetime('now') WHERE job_id = ?",
        (status, error, job_id),
    )
    conn.commit()


def get_job(conn, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM queue_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs_by_user(conn, user_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM queue_jobs WHERE user_id = ? ORDER BY created_at", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]