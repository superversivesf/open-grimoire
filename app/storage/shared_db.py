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

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS query_log (
            log_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT,
            collection_id TEXT,
            model TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT,
            iterations INTEGER DEFAULT 0,
            citations INTEGER DEFAULT 0,
            est_input_tokens INTEGER DEFAULT 0,
            est_output_tokens INTEGER DEFAULT 0,
            elapsed_sec REAL DEFAULT 0,
            done_called INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_query_log_user ON query_log(user_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_query_log_created ON query_log(created_at)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS enrich_log (
            log_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            model TEXT NOT NULL,
            sections INTEGER NOT NULL,
            succeeded INTEGER DEFAULT 0,
            est_input_tokens INTEGER DEFAULT 0,
            est_output_tokens INTEGER DEFAULT 0,
            elapsed_sec REAL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_enrich_log_user ON enrich_log(user_id, created_at)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_books (
            content_hash TEXT PRIMARY KEY,
            title TEXT,
            page_count INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_books (
            user_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, doc_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_books_hash ON user_books(content_hash)")
    conn.commit()
    return conn


def register_shared_book(conn, content_hash: str, title: str = "", page_count: int = 0) -> None:
    """Register a book in the shared registry (if not already there)."""
    conn.execute(
        "INSERT OR IGNORE INTO shared_books (content_hash, title, page_count) VALUES (?, ?, ?)",
        (content_hash, title, page_count),
    )
    conn.commit()


def link_user_book(conn, user_id: str, doc_id: str, content_hash: str, collection_id: str) -> None:
    """Link a user's doc to a shared book."""
    conn.execute(
        "INSERT OR REPLACE INTO user_books (user_id, doc_id, content_hash, collection_id) VALUES (?, ?, ?, ?)",
        (user_id, doc_id, content_hash, collection_id),
    )
    conn.commit()


def find_shared_book(conn, content_hash: str) -> dict | None:
    """Find a shared book by content hash."""
    row = conn.execute(
        "SELECT content_hash, title, page_count, created_at FROM shared_books WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    return dict(row) if row else None


def find_existing_user_for_book(conn, content_hash: str) -> dict | None:
    """Find an existing user who has this book, for sharing."""
    row = conn.execute(
        "SELECT user_id, doc_id FROM user_books WHERE content_hash = ? ORDER BY created_at LIMIT 1",
        (content_hash,),
    ).fetchone()
    return dict(row) if row else None


def unlink_user_book(conn, doc_id: str) -> None:
    """Remove a user's book link (used when deleting a doc)."""
    conn.execute("DELETE FROM user_books WHERE doc_id = ?", (doc_id,))
    conn.commit()


def log_query(conn, user_id: str, model: str, question: str, answer: str = "",
              iterations: int = 0, citations: int = 0,
              est_input_tokens: int = 0, est_output_tokens: int = 0,
              elapsed_sec: float = 0, done_called: bool = False,
              session_id: str = "", collection_id: str = "") -> str:
    log_id = _uuid.uuid4().hex
    conn.execute(
        """INSERT INTO query_log
           (log_id, user_id, session_id, collection_id, model, question, answer,
            iterations, citations, est_input_tokens, est_output_tokens, elapsed_sec, done_called)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (log_id, user_id, session_id, collection_id, model, question[:500],
         answer[:2000], iterations, citations, est_input_tokens, est_output_tokens,
         elapsed_sec, 1 if done_called else 0),
    )
    conn.commit()
    return log_id


def log_enrichment(conn, user_id: str, doc_id: str, model: str, sections: int,
                   succeeded: int = 0, est_input_tokens: int = 0,
                   est_output_tokens: int = 0, elapsed_sec: float = 0) -> str:
    log_id = _uuid.uuid4().hex
    conn.execute(
        """INSERT INTO enrich_log
           (log_id, user_id, doc_id, model, sections, succeeded,
            est_input_tokens, est_output_tokens, elapsed_sec)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (log_id, user_id, doc_id, model, sections, succeeded,
         est_input_tokens, est_output_tokens, elapsed_sec),
    )
    conn.commit()
    return log_id


def get_usage_summary(conn, days: int = 30) -> dict:
    """Get usage summary for the last N days."""
    since = f"datetime('now', '-{days} days')"

    queries = conn.execute(
        f"SELECT count(*) as count, sum(iterations) as total_iters, "
        f"sum(est_input_tokens) as total_input, sum(est_output_tokens) as total_output, "
        f"sum(elapsed_sec) as total_time, sum(citations) as total_citations "
        f"FROM query_log WHERE created_at >= {since}"
    ).fetchone()

    enrichments = conn.execute(
        f"SELECT count(*) as count, sum(sections) as total_sections, "
        f"sum(est_input_tokens) as total_input, sum(est_output_tokens) as total_output, "
        f"sum(elapsed_sec) as total_time "
        f"FROM enrich_log WHERE created_at >= {since}"
    ).fetchone()

    by_model = conn.execute(
        f"SELECT model, count(*) as count, sum(iterations) as total_iters, "
        f"sum(est_input_tokens) as total_input, sum(est_output_tokens) as total_output, "
        f"sum(elapsed_sec) as total_time, sum(citations) as total_citations "
        f"FROM query_log WHERE created_at >= {since} GROUP BY model ORDER BY count DESC"
    ).fetchall()

    by_user = conn.execute(
        f"SELECT q.user_id, u.username, count(*) as query_count, "
        f"sum(q.iterations) as total_iters, sum(q.est_input_tokens) as total_input, "
        f"sum(q.est_output_tokens) as total_output, sum(q.elapsed_sec) as total_time "
        f"FROM query_log q LEFT JOIN users u ON q.user_id = u.user_id "
        f"WHERE q.created_at >= {since} GROUP BY q.user_id ORDER BY query_count DESC"
    ).fetchall()

    recent_queries = conn.execute(
        f"SELECT q.log_id, q.question, q.answer, q.model, q.iterations, "
        f"q.citations, q.elapsed_sec, q.created_at, u.username "
        f"FROM query_log q LEFT JOIN users u ON q.user_id = u.user_id "
        f"WHERE q.created_at >= {since} ORDER BY q.created_at DESC LIMIT 20"
    ).fetchall()

    recent_enrichments = conn.execute(
        f"SELECT e.log_id, e.doc_id, e.model, e.sections, e.succeeded, "
        f"e.elapsed_sec, e.created_at, u.username "
        f"FROM enrich_log e LEFT JOIN users u ON e.user_id = u.user_id "
        f"WHERE e.created_at >= {since} ORDER BY e.created_at DESC LIMIT 10"
    ).fetchall()

    return {
        "days": days,
        "queries": dict(queries) if queries else {},
        "enrichments": dict(enrichments) if enrichments else {},
        "by_model": [dict(r) for r in by_model] if by_model else [],
        "by_user": [dict(r) for r in by_user] if by_user else [],
        "recent_queries": [dict(r) for r in recent_queries] if recent_queries else [],
        "recent_enrichments": [dict(r) for r in recent_enrichments] if recent_enrichments else [],
    }


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