"""Registration flow tests — pending status, rate limit, CSRF, config gate."""

import dataclasses
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user, get_user_by_username
from app.auth.passwords import hash_password


@pytest.fixture
def app_with_user(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123456"))
    conn.close()
    return create_app(test_config, session_secret="testsecret")


@pytest.fixture
def app_with_registration(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123456"))
    conn.close()
    reg_cfg = dataclasses.replace(test_config, allow_registration=True)
    return create_app(reg_cfg, session_secret="testsecret")


@pytest.mark.asyncio
async def test_register_disabled_by_default(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/register", follow_redirects=False)
        assert r.status_code == 303
        assert "/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_register_flow(app_with_registration):
    app = app_with_registration
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/register")
        token = client.cookies.get("login_csrf")
        r = await client.post("/register", data={"username": "newbie", "password": "pw123456", "_csrf": token})
        assert r.status_code in (200, 303)
        conn = init_shared_db(app.state.config.db_dir)
        row = get_user_by_username(conn, "newbie")
        assert row["status"] == "pending"
        conn.close()


@pytest.mark.asyncio
async def test_register_duplicate_username_generic_response(app_with_registration):
    app = app_with_registration
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/register")
        token = client.cookies.get("login_csrf")
        r1 = await client.post("/register", data={"username": "alice", "password": "pw123456", "_csrf": token})
        assert r1.status_code in (200, 303)
        assert "exists" not in r1.text.lower()


@pytest.mark.asyncio
async def test_pending_user_cannot_login(app_with_registration):
    app = app_with_registration
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/register")
        token = client.cookies.get("login_csrf")
        await client.post("/register", data={"username": "newbie", "password": "pw123456", "_csrf": token})
        await client.get("/login")
        token = client.cookies.get("login_csrf")
        r = await client.post("/login", data={"username": "newbie", "password": "pw123456", "_csrf": token})
        assert r.status_code == 401
        assert "pending" in r.text.lower()
        assert "session" not in r.cookies


@pytest.mark.asyncio
async def test_register_without_csrf_rejected(app_with_registration, monkeypatch):
    import app.auth.csrf as csrf_mod
    monkeypatch.setattr(csrf_mod, "CSRF_ENABLED", True)
    app = app_with_registration
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/register")
        r = await client.post("/register", data={"username": "newbie", "password": "pw123456"})
        assert r.status_code == 403
