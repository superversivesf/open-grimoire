"""Tests for admin bootstrap — must never default to a known password."""

import pytest
from pathlib import Path
from app.cli.bootstrap import ensure_admin_user
from app.storage.shared_db import init_shared_db, get_user_by_username
from app.auth.passwords import verify_password


def test_creates_admin_with_provided_password(tmp_dirs):
    password = ensure_admin_user(tmp_dirs["db"], admin_password="s3cure-pass")
    assert password == "s3cure-pass"
    conn = init_shared_db(tmp_dirs["db"])
    u = get_user_by_username(conn, "admin")
    assert u is not None
    assert u["is_admin"] == 1
    assert verify_password("s3cure-pass", u["password_hash"])
    conn.close()


def test_generates_random_password_when_none_provided(tmp_dirs):
    password = ensure_admin_user(tmp_dirs["db"])
    assert password is not None
    assert len(password) >= 16
    conn = init_shared_db(tmp_dirs["db"])
    u = get_user_by_username(conn, "admin")
    assert u is not None
    assert verify_password(password, u["password_hash"])
    conn.close()


def test_random_password_is_not_predictable(tmp_dirs):
    p1 = ensure_admin_user(tmp_dirs["db"])
    p2 = ensure_admin_user(tmp_dirs["db"] / "other")
    assert p1 != p2


def test_does_not_overwrite_existing_admin(tmp_dirs):
    ensure_admin_user(tmp_dirs["db"], admin_password="first-pass")
    password = ensure_admin_user(tmp_dirs["db"], admin_password="second-pass")
    assert password is None
    conn = init_shared_db(tmp_dirs["db"])
    u = get_user_by_username(conn, "admin")
    assert verify_password("first-pass", u["password_hash"])
    conn.close()
