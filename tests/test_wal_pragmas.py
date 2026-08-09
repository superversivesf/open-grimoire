"""WAL + busy_timeout pragmas on every SQLite connection."""

import sqlite3
from app.storage.migrations import init_shared_db_with_migrations, init_user_db_with_migrations


def test_shared_db_uses_wal(tmp_path):
    conn = init_shared_db_with_migrations(tmp_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    conn.close()


def test_user_db_uses_wal(tmp_path):
    conn = init_user_db_with_migrations(tmp_path, "alice")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    conn.close()


def test_concurrent_writes_no_lock(tmp_path):
    """Two threads writing the same DB must not hit 'database is locked'."""
    import threading
    errors = []

    def writer(name):
        try:
            for _ in range(20):
                conn = init_shared_db_with_migrations(tmp_path)
                conn.execute("INSERT INTO app_config (key, value) VALUES (?, ?)", (f"{name}-{_}", "v"))
                conn.commit()
                conn.close()
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=writer, args=("a",))
    t2 = threading.Thread(target=writer, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert errors == []
