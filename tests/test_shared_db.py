import sqlite3
from app.storage.shared_db import (
    init_shared_db, create_user, get_user_by_username, get_user_by_id, list_users,
)


def test_init_creates_schema(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    assert "users" in tables
    assert "app_config" in tables
    conn.close()


def test_create_user_returns_id(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", "hash123")
    assert isinstance(uid, str) and len(uid) == 32
    conn.close()


def test_get_user_by_username(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", "hash123", is_admin=True)
    u = get_user_by_username(conn, "alice")
    assert u["user_id"] == uid
    assert u["username"] == "alice"
    assert u["password_hash"] == "hash123"
    assert u["is_admin"] == 1
    conn.close()


def test_get_user_by_username_missing_returns_none(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    assert get_user_by_username(conn, "nobody") is None
    conn.close()


def test_get_user_by_id(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "bob", "h")
    u = get_user_by_id(conn, uid)
    assert u["username"] == "bob"
    conn.close()


def test_list_users(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", "h1")
    create_user(conn, "bob", "h2")
    users = list_users(conn)
    assert len(users) == 2
    assert {u["username"] for u in users} == {"alice", "bob"}
    conn.close()