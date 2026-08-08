"""Regression tests for bugs found in the Feature A cross-check.

BUG 1: library page 500s for members (KeyError: user_id)
BUG 2: members can't read shared doc content (doc_view_leaf used own tree)
BUG 3: doc_search_path only searched the requester's tree
BUG 4: owner self-share demoted themselves
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import (
    init_shared_db, create_user, add_collection_member, get_membership,
)
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status
from app.auth.passwords import hash_password
from tests.conftest import csrf_for

DOC_ID_A = "38dfd1fd2c4249f193f923458891812f"


@pytest.fixture
def setup(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw123456"))
    bob = create_user(conn, "bob", hash_password("pw123456"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], alice)
    cid = create_collection(uconn, "Shared Shelf")
    create_doc(uconn, DOC_ID_A, cid, "Goblin Book", "sha1")
    update_doc_status(uconn, DOC_ID_A, "done")
    uconn.close()
    doc_dir = tmp_dirs["data"] / alice / DOC_ID_A
    doc_dir.mkdir(parents=True)
    (doc_dir / "01_goblin.md").write_text("---\nsummary: \"Goblin.\"\nkeywords: [goblin]\n---\n\n# Goblin\n\nAC 15, HP 7.\n")
    conn = init_shared_db(tmp_dirs["db"])
    add_collection_member(conn, cid, alice, "owner")
    add_collection_member(conn, cid, bob, "member")
    conn.close()
    app = create_app(test_config, session_secret="testsecret")
    return app, alice, bob, cid


async def _login(client, username):
    await client.get("/login")
    token = client.cookies.get("login_csrf")
    r = await client.post("/login", data={"username": username, "password": "pw123456", "_csrf": token})
    assert r.status_code in (200, 303)


@pytest.mark.asyncio
async def test_bug1_library_renders_for_member(setup):
    """BUG 1: GET / (landing page) must not 500 for a member."""
    app, alice, bob, cid = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get("/")
        assert r.status_code == 200
        assert "Shared Shelf" in r.text


@pytest.mark.asyncio
async def test_bug2_member_reads_shared_doc_content(setup):
    """BUG 2: doc_view_leaf must serve the owner's tree to members."""
    app, alice, bob, cid = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get(f"/docs/{DOC_ID_A}/view", params={"path": "01_goblin.md"})
        assert r.status_code == 200
        assert "AC 15" in r.text
        assert "file not found" not in r.text


@pytest.mark.asyncio
async def test_bug3_member_search_finds_shared_doc(setup):
    """BUG 3: /docs/search must find files in shared collections' owner trees."""
    app, alice, bob, cid = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get("/docs/search", params={"path": "01_goblin.md"}, follow_redirects=False)
        assert r.status_code == 303
        assert f"/docs/{DOC_ID_A}/view" in r.headers["location"]


@pytest.mark.asyncio
async def test_bug4_owner_cannot_self_share(setup):
    """BUG 4: sharing with yourself must be a no-op, not a demotion."""
    app, alice, bob, cid = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "alice")
        r = await client.post(
            f"/collections/{cid}/share",
            data={"username": "alice", "role": "member", "_csrf": csrf_for(client)},
        )
        assert r.status_code in (200, 303)
        conn = init_shared_db(app.state.config.db_dir)
        # No member row for alice (owner) — or if one exists it must not demote
        m = get_membership(conn, cid, alice)
        assert m is None or m["role"] == "owner"
        conn.close()
        # alice still sees the share UI (still owner)
        r2 = await client.get(f"/collections/{cid}")
        assert r2.status_code == 200
