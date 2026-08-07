import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.auth.passwords import hash_password
from tests.conftest import csrf_for


@pytest.fixture
def app_with_user(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    app = create_app(test_config, session_secret="testsecret")
    return app


@pytest.mark.asyncio
async def test_library_requires_auth(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/")
        assert r.status_code in (303, 307)
        assert "/login" in r.headers.get("location", "")


@pytest.mark.asyncio
async def test_library_empty_after_login(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw123"})
        r = await client.get("/")
        assert r.status_code == 200
        assert "collections" in r.text.lower() or "No collections" in r.text


@pytest.mark.asyncio
async def test_create_collection(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw123"})
        r = await client.post("/collections", data={"name": "Pathfinder shelf", "_csrf": csrf_for(client)})
        assert r.status_code in (200, 303)
        r2 = await client.get("/")
        assert "Pathfinder shelf" in r2.text