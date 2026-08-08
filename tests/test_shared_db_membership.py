"""Shared DB membership + user-status helpers."""

import pytest
from app.storage.shared_db import (
    init_shared_db, create_user, get_user_by_username,
    add_collection_member, remove_collection_member, list_collection_members,
    get_membership, list_shared_collections_for_user,
    create_user_with_status, set_user_status, list_users_by_status,
)


def test_membership_roundtrip(tmp_path):
    conn = init_shared_db(tmp_path)
    add_collection_member(conn, "c1", "alice", "owner")
    add_collection_member(conn, "c1", "bob", "member")
    members = list_collection_members(conn, "c1")
    assert len(members) == 2
    assert get_membership(conn, "c1", "alice")["role"] == "owner"
    remove_collection_member(conn, "c1", "bob")
    assert get_membership(conn, "c1", "bob") is None
    conn.close()


def test_list_shared_collections_for_user(tmp_path):
    conn = init_shared_db(tmp_path)
    add_collection_member(conn, "c1", "alice", "owner")
    add_collection_member(conn, "c1", "bob", "member")
    add_collection_member(conn, "c2", "bob", "owner")
    got = list_shared_collections_for_user(conn, "bob")
    assert {g["collection_id"] for g in got} == {"c1", "c2"}
    got = list_shared_collections_for_user(conn, "eve")
    assert got == []
    conn.close()


def test_user_status_flow(tmp_path):
    conn = init_shared_db(tmp_path)
    uid = create_user_with_status(conn, "newbie", "hash", status="pending")
    row = get_user_by_username(conn, "newbie")
    assert row["status"] == "pending"
    set_user_status(conn, uid, "active")
    assert get_user_by_username(conn, "newbie")["status"] == "active"
    pending = list_users_by_status(conn, "pending")
    assert len(pending) == 0
    conn.close()


def test_existing_users_default_active(tmp_path):
    conn = init_shared_db(tmp_path)
    create_user(conn, "alice", "hash")
    row = get_user_by_username(conn, "alice")
    assert row["status"] == "active"
    conn.close()
