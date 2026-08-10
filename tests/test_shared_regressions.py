"""Regression tests for bugs found in the Feature A cross-check.

BUG 1: library page 500s for members (KeyError: user_id)
BUG 2: members can't read shared doc content (doc_view_leaf used own tree)
BUG 3: doc_search_path only searched the requester's tree
BUG 4: owner self-share demoted themselves
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.storage.shared_db import (
    init_shared_db, get_membership,
)
from tests.conftest import csrf_for, login, DOC_ID_A

@pytest.fixture
def setup(shared_collection_fixture):
    """alice owns c1 with a goblin doc; bob is member."""
    app, alice, bob, _eve, cid = shared_collection_fixture
    return app, alice, bob, cid


@pytest.mark.asyncio
async def test_bug1_library_renders_for_member(setup):
    """BUG 1: GET / (landing page) must not 500 for a member."""
    app, alice, bob, cid = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.get("/")
        assert r.status_code == 200
        assert "Shared Shelf" in r.text


@pytest.mark.asyncio
async def test_bug2_member_reads_shared_doc_content(setup):
    """BUG 2: doc_view_leaf must serve the owner's tree to members."""
    app, alice, bob, cid = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.get(f"/docs/{DOC_ID_A}/view", params={"path": "01_goblin.md"})
        assert r.status_code == 200
        assert "AC 15" in r.text
        assert "file not found" not in r.text


@pytest.mark.asyncio
async def test_bug3_member_search_finds_shared_doc(setup):
    """BUG 3: /docs/search must find files in shared collections' owner trees."""
    app, alice, bob, cid = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.get("/docs/search", params={"path": "01_goblin.md"}, follow_redirects=False)
        assert r.status_code == 303
        assert f"/docs/{DOC_ID_A}/view" in r.headers["location"]


@pytest.mark.asyncio
async def test_bug4_owner_cannot_self_share(setup):
    """BUG 4: sharing with yourself must be a no-op, not a demotion."""
    app, alice, bob, cid = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "alice", password="pw123456")
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
