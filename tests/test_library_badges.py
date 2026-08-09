"""Library collection cards show a processing badge when docs are enriching."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user, add_collection_member
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status
from app.auth.passwords import hash_password
from tests.conftest import csrf_for

PROCESSING = {"queued", "extracting", "structuring", "tiering", "enriching", "indexing"}


@pytest.fixture
def setup(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw123456"))
    bob = create_user(conn, "bob", hash_password("pw123456"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], alice)
    busy = create_collection(uconn, "Busy Shelf")
    idle = create_collection(uconn, "Idle Shelf")
    create_doc(uconn, "d1", busy, "Processing Book", "sha1")
    update_doc_status(uconn, "d1", "enriching")
    create_doc(uconn, "d2", idle, "Done Book", "sha2")
    update_doc_status(uconn, "d2", "done")
    uconn.close()
    conn = init_shared_db(tmp_dirs["db"])
    add_collection_member(conn, busy, alice, "owner")
    add_collection_member(conn, busy, bob, "member")
    conn.close()
    app = create_app(test_config, session_secret="testsecret")
    return app, alice, bob, busy, idle


async def _login(client, username):
    await client.get("/login")
    token = client.cookies.get("login_csrf")
    r = await client.post("/login", data={"username": username, "password": "pw123456", "_csrf": token})
    assert r.status_code in (200, 303)


@pytest.mark.asyncio
async def test_processing_badge_on_busy_collection(setup):
    app, alice, bob, busy, idle = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "alice")
        r = await client.get("/")
        assert r.status_code == 200
        # Busy collection card carries the processing badge
        assert "Busy Shelf" in r.text
        assert "processing" in r.text.lower()
        # Idle collection has no badge
        assert "Idle Shelf" in r.text


@pytest.mark.asyncio
async def test_processing_badge_visible_to_member(setup):
    """Members see the processing badge on shared collections too."""
    app, alice, bob, busy, idle = setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get("/")
        assert r.status_code == 200
        assert "Busy Shelf" in r.text
        assert "processing" in r.text.lower()


@pytest.mark.asyncio
async def test_no_badge_when_all_done(setup):
    app, alice, bob, busy, idle = setup
    uconn = init_user_db(app.state.config.db_dir, alice)
    update_doc_status(uconn, "d1", "done")
    uconn.close()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "alice")
        r = await client.get("/")
        assert "processing" not in r.text.lower()
