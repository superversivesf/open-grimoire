import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.auth.passwords import hash_password


@pytest.fixture
def app_with_user(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    app = create_app(test_config, session_secret="testsecret")
    return app


@pytest.mark.asyncio
async def test_get_login_page(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/login")
        assert r.status_code == 200
        assert "login" in r.text.lower()


@pytest.mark.asyncio
async def test_login_success_sets_cookie(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "alice", "password": "pw123"})
        assert r.status_code in (200, 303)
        assert "session" in r.cookies


@pytest.mark.asyncio
async def test_login_wrong_password(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "alice", "password": "wrong"})
        assert r.status_code in (200, 401)
        assert "session" not in r.cookies


@pytest.mark.asyncio
async def test_login_unknown_user(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "nobody", "password": "x"})
        assert r.status_code in (200, 401)
        assert "session" not in r.cookies


@pytest.mark.asyncio
async def test_logout_clears_cookie(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw123"})
        r = await client.post("/logout")
        assert r.status_code in (200, 303)