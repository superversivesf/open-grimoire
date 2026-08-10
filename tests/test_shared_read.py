"""Shared collection read path tests — members see owner's docs, non-members blocked."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status
from tests.conftest import csrf_for, login, DOC_ID_A

@pytest.fixture
def shared_setup(shared_collection_fixture):
    """alice owns c1 with a doc; bob is member; eve is nobody."""
    return shared_collection_fixture


@pytest.mark.asyncio
async def test_member_reads_shared_collection(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.get(f"/collections/{cid}")
        assert r.status_code == 200
        assert "Goblin Book" in r.text


@pytest.mark.asyncio
async def test_member_sees_docs_in_table(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.get(f"/collections/{cid}/table")
        assert r.status_code == 200
        assert "Goblin Book" in r.text


@pytest.mark.asyncio
async def test_non_member_blocked_from_collection(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "eve", password="pw123456")
        r = await client.get(f"/collections/{cid}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_member_reads_owner_doc(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.get(f"/docs/{DOC_ID_A}")
        assert r.status_code == 200
        assert "Goblin Book" in r.text


@pytest.mark.asyncio
async def test_member_gets_owner_cover(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.get(f"/docs/{DOC_ID_A}/cover", follow_redirects=False)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("image/jpeg")


@pytest.mark.asyncio
async def test_non_member_blocked_from_doc(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "eve", password="pw123456")
        r = await client.get(f"/docs/{DOC_ID_A}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        r2 = await client.get(f"/docs/{DOC_ID_A}/cover", follow_redirects=False)
        assert r2.status_code == 303


@pytest.mark.asyncio
async def test_member_does_not_see_private_collections(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    # alice's private collection
    uconn = init_user_db(app.state.config.db_dir, alice)
    private_cid = create_collection(uconn, "Private")
    uconn.close()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.get(f"/collections/{private_cid}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_collection_with_many_docs(shared_setup, tmp_dirs):
    """A collection with 50+ docs renders for members."""
    app, alice, bob, _eve, cid = shared_setup
    uconn = init_user_db(tmp_dirs["db"], alice)
    for i in range(50):
        create_doc(uconn, f"doc{i:03d}", cid, f"Book {i}", "sha")
        update_doc_status(uconn, f"doc{i:03d}", "done")
    uconn.close()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.get(f"/collections/{cid}/table")
        assert r.status_code == 200
        assert "Goblin Book" in r.text
        assert "Book 0" in r.text
        assert "Book 49" in r.text
