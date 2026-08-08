"""Versioned database migration system.

Each database (shared and per-user) maintains its own schema version.
Migrations are applied in order on initialization.
"""

import sqlite3
from pathlib import Path
from typing import Callable

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
        ALTER TABLE queue_jobs ADD COLUMN lease_expires_at TEXT;
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

# Migration 2's ALTER TABLE is not intrinsically idempotent: if it succeeds
# but a later statement in the same script fails, the version stays at 1 and
# the retry hits "duplicate column name". Guard the ALTER in Python before
# executing the script so a partial failure is retry-safe.
_GUARDED_ALTERS: dict[int, tuple[str, str]] = {
    2: ("users", "status"),
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
    """Idempotent ALTER TABLE ADD COLUMN — skip if the column already exists."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(ddl)


def migrate_shared_db(conn: sqlite3.Connection) -> None:
    """Apply pending migrations to shared database.

    Failures propagate and the version only advances after a successful
    script. ALTER-based migrations are guarded for column existence first
    (see _GUARDED_ALTERS) so a partial failure can be retried safely.
    """
    current = _get_shared_version(conn)
    for version, name, sql in SHARED_MIGRATIONS:
        if version > current:
            print(f"  [migrate] shared: v{version} - {name}")
            guard = _GUARDED_ALTERS.get(version)
            if guard:
                _add_column(conn, guard[0], guard[1], f"ALTER TABLE {guard[0]} ADD COLUMN {guard[1]} TEXT NOT NULL DEFAULT 'active'")
            conn.executescript(sql)
            _set_shared_version(conn, version)
    conn.commit()


def migrate_user_db(conn: sqlite3.Connection) -> None:
    """Apply pending migrations to user database.

    Failures propagate — a failed migration must never advance the
    version, or the schema drifts silently. Migrations are idempotent
    (CREATE ... IF NOT EXISTS), so a partial failure can be retried.
    """
    current = _get_user_version(conn)
    for version, name, sql in USER_MIGRATIONS:
        if version > current:
            print(f"  [migrate] user: v{version} - {name}")
            conn.executescript(sql)
            _set_user_version(conn, version)
    conn.commit()


def init_shared_db_with_migrations(db_dir: Path) -> sqlite3.Connection:
    """Initialize shared database and run migrations."""
    db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_dir / "shared.sqlite")
    conn.row_factory = sqlite3.Row
    migrate_shared_db(conn)
    return conn


def init_user_db_with_migrations(db_dir: Path, user_id: str) -> sqlite3.Connection:
    """Initialize user database and run migrations."""
    from app.storage.paths import user_db_path
    p = user_db_path(db_dir, user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    migrate_user_db(conn)
    return conn