"""Versioned database migration system.

Each database (shared and per-user) maintains its own schema version.
Migrations are applied in order on initialization.
"""

import sqlite3
import threading
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Connection Pooling ───────────────────────────────────────────────
# sqlite3.Connection cannot carry attributes, so pooled connections use a
# subclass carrying the pool key. close() returns the connection to the
# calling thread's pool; _real_close() is the escape hatch used on
# overflow / shutdown. Pools are thread-local because SQLite connections
# are bound to the thread that created them.
_MAX_POOL_SIZE = 4
_thread_pools = threading.local()


class PoolConn(sqlite3.Connection):
    _pool_key: str = ""

    def close(self) -> None:
        release_connection(self)

    def _real_close(self) -> None:
        super().close()


def _pool_key_for(db_path: Path) -> str:
    return str(db_path.resolve())


def _pool_bucket(key: str) -> list["PoolConn"]:
    buckets = getattr(_thread_pools, "buckets", None)
    if buckets is None:
        buckets = {}
        _thread_pools.buckets = buckets
    return buckets.setdefault(key, [])


def _make_connection(db_path: Path, row_factory: bool = True) -> "PoolConn":
    conn = sqlite3.connect(str(db_path), factory=PoolConn)
    conn._pool_key = _pool_key_for(db_path)
    if row_factory:
        conn.row_factory = sqlite3.Row
    _apply_connection_pragmas(conn)
    return conn


def acquire_connection(db_path: Path, row_factory: bool = True) -> "PoolConn":
    """Get a pooled connection for this thread, or create a new one."""
    key = _pool_key_for(db_path)
    bucket = _pool_bucket(key)
    while bucket:
        conn = bucket.pop()
        try:
            conn.execute("SELECT 1")
            conn.rollback()  # clear any leaked transaction
            return conn
        except sqlite3.ProgrammingError:
            conn._real_close()
    return _make_connection(db_path, row_factory)


def release_connection(conn: "PoolConn") -> None:
    """Return a connection to this thread's pool (up to a cap), else close."""
    bucket = _pool_bucket(conn._pool_key)
    if len(bucket) < _MAX_POOL_SIZE:
        bucket.append(conn)
        return
    conn._real_close()


def close_all_pools() -> None:
    """Close every pooled connection for the CURRENT thread (shutdown)."""
    buckets = getattr(_thread_pools, "buckets", None)
    if buckets is None:
        return
    for bucket in buckets.values():
        while bucket:
            bucket.pop()._real_close()
    _thread_pools.buckets = {}

# ─── Shared Database Migrations ──────────────────────────────────────

SHARED_MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "create_initial_schema", """
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
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
        );
        CREATE INDEX IF NOT EXISTS idx_queue_status ON queue_jobs(status, created_at);
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
        );
        CREATE INDEX IF NOT EXISTS idx_query_log_user ON query_log(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_query_log_created ON query_log(created_at);
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
        );
        CREATE INDEX IF NOT EXISTS idx_enrich_log_user ON enrich_log(user_id, created_at);
        CREATE TABLE IF NOT EXISTS shared_books (
            content_hash TEXT PRIMARY KEY,
            title TEXT,
            page_count INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS user_books (
            user_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, doc_id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_books_hash ON user_books(content_hash);
    """),
    (2, "add_user_status_and_collection_members", """
        CREATE TABLE IF NOT EXISTS collection_members (
            collection_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
            added_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (collection_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_collection_members_user ON collection_members(user_id);
    """),
]

# Migrations 1 and 2's ALTER TABLEs are not intrinsically idempotent: if one
# succeeds but a later statement in the same script fails, the version stays
# behind and the retry hits "duplicate column name". Keep them out of the
# scripts and execute them in Python after the script so a partial failure is
# retry-safe.
_GUARDED_ALTERS: dict[int, tuple[str, str, str]] = {
    1: ("queue_jobs", "lease_expires_at", "ALTER TABLE queue_jobs ADD COLUMN lease_expires_at TEXT"),
    2: ("users", "status", "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"),
}

# ─── User Database Migrations ────────────────────────────────────────

USER_MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "create_initial_schema", """
        CREATE TABLE IF NOT EXISTS collections (
            collection_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS docs (
            doc_id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            title TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            page_count INTEGER,
            enrich_progress INTEGER NOT NULL DEFAULT 0,
            enrich_total INTEGER NOT NULL DEFAULT 0,
            enrich_completed_paths TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (collection_id) REFERENCES collections(collection_id)
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            history_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (collection_id) REFERENCES collections(collection_id)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            path, title, summary, keywords, content, tokenize='porter'
        );
    """),
    (2, "add_turns_table", """
        CREATE TABLE IF NOT EXISTS turns (
            session_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            user_msg TEXT NOT NULL,
            agent_msg TEXT NOT NULL,
            cites_json TEXT NOT NULL DEFAULT '[]',
            suggestions_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (session_id, turn_index),
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
        CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
    """),
]


def _get_shared_version(conn: sqlite3.Connection) -> int:
    """Get current schema version for shared database."""
    # Check if app_config table exists first
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='app_config'"
    ).fetchone()
    if not row:
        return 0
    row = conn.execute("SELECT value FROM app_config WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def _set_shared_version(conn: sqlite3.Connection, version: int) -> None:
    """Set schema version for shared database."""
    # Ensure app_config table exists
    conn.execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT OR REPLACE INTO app_config (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )


def _get_user_version(conn: sqlite3.Connection) -> int:
    """Get current schema version for user database."""
    # Check if schema_version table exists
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not row:
        return 0
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(row["version"]) if row else 0


def _ensure_user_version_table(conn: sqlite3.Connection) -> None:
    """Create schema_version table if it doesn't exist."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    # Initialize with version 0 if empty
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if not row:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")


def _set_user_version(conn: sqlite3.Connection, version: int) -> None:
    """Set schema version for user database."""
    _ensure_user_version_table(conn)
    conn.execute("UPDATE schema_version SET version = ?", (version,))


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Idempotent ALTER TABLE ADD COLUMN — swallow duplicate column errors.

    Exception-based rather than check-then-act so concurrent initializers
    cannot race between the PRAGMA check and the ALTER.
    """
    try:
        conn.execute(ddl)
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "duplicate column name" not in msg:
            raise
        if "disk i/o" in msg or "database is locked" in msg or "no such table" in msg:
            raise


def migrate_shared_db(conn: sqlite3.Connection) -> None:
    """Apply pending migrations to shared database.

    Failures propagate and the version only advances after a successful
    script. ALTER-based migrations are guarded for column existence first
    (see _GUARDED_ALTERS) so a partial failure can be retried safely.
    """
    current = _get_shared_version(conn)
    for version, name, sql in SHARED_MIGRATIONS:
        if version > current:
            logger.info(f"  [migrate] shared: v{version} - {name}")
            conn.executescript(sql)
            guard = _GUARDED_ALTERS.get(version)
            if guard:
                _add_column(conn, guard[0], guard[1], guard[2])
            _set_shared_version(conn, version)
    conn.commit()


def migrate_user_db(conn: sqlite3.Connection) -> None:
    """Apply pending migrations to user database.

    Failures propagate — a failed migration must never advance the version,
    or the schema drifts silently. Migrations are idempotent (CREATE ... IF
    NOT EXISTS), so a partial failure can be retried.
    """
    current = _get_user_version(conn)
    for version, name, sql in USER_MIGRATIONS:
        if version > current:
            logger.info(f"  [migrate] user: v{version} - {name}")
            conn.executescript(sql)
            _set_user_version(conn, version)
    conn.commit()


def _apply_connection_pragmas(conn: sqlite3.Connection) -> None:
    """WAL + busy_timeout so the web loop and worker thread can write
    concurrently without 'database is locked'.

    busy_timeout must be set BEFORE journal_mode: converting to WAL takes
    an exclusive lock, and without the timeout a concurrent first-connect
    fails immediately instead of waiting."""
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")


def init_shared_db_with_migrations(db_dir: Path) -> sqlite3.Connection:
    """Initialize shared database and run migrations."""
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = _make_connection(db_dir / "shared.sqlite")
    migrate_shared_db(conn)
    return conn


def init_user_db_with_migrations(db_dir: Path, user_id: str) -> sqlite3.Connection:
    """Initialize user database and run migrations."""
    from app.storage.paths import user_db_path
    p = user_db_path(db_dir, user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = _make_connection(p)
    migrate_user_db(conn)
    return conn