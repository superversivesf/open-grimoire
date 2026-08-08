"""Migration tests — failures must abort, never advance the version."""

import sqlite3
import pytest
from app.storage.migrations import migrate_user_db, migrate_shared_db, _get_user_version, _get_shared_version, SHARED_MIGRATIONS


def _fresh_user_db(tmp_path):
    conn = sqlite3.connect(tmp_path / "user.sqlite")
    conn.row_factory = sqlite3.Row
    return conn


def test_migrate_user_db_applies_all_versions(tmp_path):
    conn = _fresh_user_db(tmp_path)
    migrate_user_db(conn)
    assert _get_user_version(conn) == 1
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(docs)")}
    assert "enrich_progress" in cols
    assert "enrich_total" in cols
    assert "enrich_completed_paths" in cols
    conn.close()


def test_migrate_user_db_idempotent(tmp_path):
    conn = _fresh_user_db(tmp_path)
    migrate_user_db(conn)
    migrate_user_db(conn)
    assert _get_user_version(conn) == 1
    conn.close()


def test_migrate_user_db_aborts_on_failure(tmp_path, monkeypatch):
    """A failing migration must raise and must NOT advance the version."""
    conn = _fresh_user_db(tmp_path)
    broken = [
        (1, "create_initial_schema", "CREATE TABLE IF NOT EXISTS collections (collection_id TEXT PRIMARY KEY);"),
        (2, "broken_migration", "THIS IS NOT SQL;"),
    ]
    monkeypatch.setattr("app.storage.migrations.USER_MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        migrate_user_db(conn)
    # Migration 1 applied; the failing migration 2 must NOT advance the version
    assert _get_user_version(conn) == 1
    conn.close()


def test_migrate_shared_db_applies_all_versions(tmp_path):
    conn = sqlite3.connect(tmp_path / "shared.sqlite")
    conn.row_factory = sqlite3.Row
    migrate_shared_db(conn)
    assert _get_shared_version(conn) == 2
    conn.close()


def test_shared_migration_v2_applies(tmp_path):
    conn = sqlite3.connect(tmp_path / "shared.sqlite")
    conn.row_factory = sqlite3.Row
    migrate_shared_db(conn)
    assert _get_shared_version(conn) == 2
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    assert "status" in cols
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='collection_members'"
    ).fetchone()
    assert row is not None
    conn.close()


def test_shared_migration_v2_retry_safe_after_partial_failure(tmp_path, monkeypatch):
    """If migration 2's script fails after the guarded ALTER, a retry must
    not hit 'duplicate column' — the ALTER must be skipped when present."""
    import app.storage.migrations as mig
    real_migrations = list(mig.SHARED_MIGRATIONS)
    conn = sqlite3.connect(tmp_path / "shared.sqlite")
    conn.row_factory = sqlite3.Row
    # Apply v1 only
    v1 = [m for m in real_migrations if m[0] == 1]
    monkeypatch.setattr("app.storage.migrations.SHARED_MIGRATIONS", v1)
    migrate_shared_db(conn)
    assert _get_shared_version(conn) == 1
    # Simulate a partial failure: v2 script fails after the ALTER ran
    conn.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    conn.commit()
    monkeypatch.setattr("app.storage.migrations.SHARED_MIGRATIONS", [v1[0], (2, "v2", "INVALID SQL;")])
    with pytest.raises(sqlite3.OperationalError):
        migrate_shared_db(conn)
    assert _get_shared_version(conn) == 1
    # Retry with the real v2 script — must succeed (guard skips the ALTER)
    monkeypatch.setattr("app.storage.migrations.SHARED_MIGRATIONS", real_migrations)
    migrate_shared_db(conn)
    assert _get_shared_version(conn) == 2
    conn.close()
