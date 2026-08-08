"""Shared collection read path tests — members see owner's docs, non-members blocked."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user, add_collection_member
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status
from app.auth.passwords import hash_password
from tests.conftest import csrf_for

DOC_ID_A = "38dfd1fd2c4249f193f923458891812f"


@pytest.fixture
def shared_setup(tmp_dirs, test_config):
    """alice owns c1 with a doc; bob is member; eve is nobody."""
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw123456"))
    bob = create_user(conn, "bob", hash_password("pw123456"))
    eve = create_user(conn, "eve", hash_password("pw123456"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], alice)
    cid = create_collection(uconn, "Shared Shelf")
    create_doc(uconn, DOC_ID_A, cid, "Goblin Book", "sha1")
    update_doc_status(uconn, DOC_ID_A, "done")
    uconn.close()
    doc_dir = tmp_dirs["data"] / alice / DOC_ID_A
    doc_dir.mkdir(parents=True)
    (doc_dir / "original.pdf").write_bytes(b"%PDF-1.4 fake")
    (doc_dir / "cover.jpg").write_bytes(b"jpegdata")
    conn = init_shared_db(tmp_dirs["db"])
    add_collection_member(conn, cid, alice, "owner")
    add_collection_member(conn, cid, bob, "member")
    conn.close()
    app = create_app(test_config, session_secret="testsecret")
    return app, alice, bob, eve, cid


async def _login(client, username):
    await client.get("/login")
    token = client.cookies.get("login_csrf")
    r = await client.post("/login", data={"username": username, "password": "pw123456", "_csrf": token})
    assert r.status_code in (200, 303)


@pytest.mark.asyncio
async def test_member_reads_shared_collection(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get(f"/collections/{cid}")
        assert r.status_code == 200
        assert "Goblin Book" in r.text


@pytest.mark.asyncio
async def test_member_sees_docs_in_table(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get(f"/collections/{cid}/table")
        assert r.status_code == 200
        assert "Goblin Book" in r.text


@pytest.mark.asyncio
async def test_non_member_blocked_from_collection(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "eve")
        r = await client.get(f"/collections/{cid}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_member_reads_owner_doc(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get(f"/docs/{DOC_ID_A}")
        assert r.status_code == 200
        assert "Goblin Book" in r.text


@pytest.mark.asyncio
async def test_member_gets_owner_cover(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get(f"/docs/{DOC_ID_A}/cover", follow_redirects=False)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("image/jpeg")


@pytest.mark.asyncio
async def test_non_member_blocked_from_doc(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "eve")
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
        await _login(client, "bob")
        r = await client.get(f"/collections/{private_cid}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
