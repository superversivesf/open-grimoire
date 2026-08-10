"""resolve_collection — the authorization seam for shared collections."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.storage.resolver import resolve_collection
from app.storage.shared_db import (
    init_shared_db, create_user, add_collection_member, remove_collection_member,
    get_membership,
)
from app.storage.user_db import init_user_db, create_collection
from app.auth.passwords import hash_password
from app.main import create_app
from tests.conftest import csrf_for, login


def test_resolve_private_collection(tmp_dirs):
    uid = "alice"
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "Mine")
    uconn.close()
    r = resolve_collection(tmp_dirs["db"], cid, uid)
    assert r["owner_uid"] == uid
    assert r["role"] == "owner"
    assert resolve_collection(tmp_dirs["db"], cid, "bob") is None


def test_resolve_shared_collection(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw"))
    bob = create_user(conn, "bob", hash_password("pw"))
    add_collection_member(conn, "c1", alice, "owner")
    add_collection_member(conn, "c1", bob, "member")
    conn.close()
    r = resolve_collection(tmp_dirs["db"], "c1", bob)
    assert r["owner_uid"] == alice
    assert r["role"] == "member"
    assert resolve_collection(tmp_dirs["db"], "c1", "eve") is None


def test_resolve_after_removal(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw"))
    bob = create_user(conn, "bob", hash_password("pw"))
    add_collection_member(conn, "c1", alice, "owner")
    add_collection_member(conn, "c1", bob, "member")
    remove_collection_member(conn, "c1", bob)
    conn.close()
    assert resolve_collection(tmp_dirs["db"], "c1", bob) is None


def test_resolve_missing_collection(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    assert resolve_collection(tmp_dirs["db"], "nonexistent", alice) is None


# ─── share/unshare endpoints ─────────────────────────────────────────
@pytest.fixture
def app_with_collection(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw123456"))
    bob = create_user(conn, "bob", hash_password("pw123456"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], alice)
    cid = create_collection(uconn, "Shared Shelf")
    uconn.close()
    app = create_app(test_config, session_secret="testsecret")
    return app, alice, bob, cid


@pytest.mark.asyncio
async def test_owner_shares_collection(app_with_collection):
    app, alice, bob, cid = app_with_collection
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "alice", password="pw123456")
        r = await client.post(
            f"/collections/{cid}/share",
            data={"username": "bob", "role": "member", "_csrf": csrf_for(client)},
        )
        assert r.status_code in (200, 303)
        conn = init_shared_db(app.state.config.db_dir)
        assert get_membership(conn, cid, bob)["role"] == "member"
        conn.close()


@pytest.mark.asyncio
async def test_non_owner_cannot_share(app_with_collection):
    app, alice, bob, cid = app_with_collection
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.post(
            f"/collections/{cid}/share",
            data={"username": "alice", "role": "member", "_csrf": csrf_for(client)},
        )
        assert r.status_code == 303
        conn = init_shared_db(app.state.config.db_dir)
        assert get_membership(conn, cid, bob) is None
        conn.close()


@pytest.mark.asyncio
async def test_owner_unshares_member(app_with_collection):
    app, alice, bob, cid = app_with_collection
    conn = init_shared_db(app.state.config.db_dir)
    add_collection_member(conn, cid, bob, "member")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "alice", password="pw123456")
        r = await client.post(
            f"/collections/{cid}/unshare",
            data={"username": "bob", "_csrf": csrf_for(client)},
        )
        assert r.status_code in (200, 303)
        conn = init_shared_db(app.state.config.db_dir)
        assert get_membership(conn, cid, bob) is None
        conn.close()
