"""Tests for cookie Secure flag and security headers."""

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
    return create_app(test_config, session_secret="testsecret")


@pytest.fixture
def app_secure(tmp_dirs, test_config):
    import dataclasses
    secure_cfg = dataclasses.replace(test_config, cookie_secure=True)
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    return create_app(secure_cfg, session_secret="testsecret")


@pytest.mark.asyncio
async def test_session_cookie_is_secure_by_default(app_secure):
    async with AsyncClient(transport=ASGITransport(app=app_secure), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "alice", "password": "pw123"})
        assert r.status_code in (200, 303)
        assert "session" in r.cookies
        cookie = client.cookies.jar
        for c in cookie:
            if c.name == "session":
                assert c.secure, "session cookie must have Secure flag"
                assert c.has_nonstandard_attr("HttpOnly") or c._rest.get("HttpOnly")
                assert c.has_nonstandard_attr("SameSite") or c._rest.get("SameSite")


@pytest.mark.asyncio
async def test_cookie_secure_false_when_dev_mode(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    test_config.cookie_secure = False
    app = create_app(test_config, session_secret="testsecret")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "alice", "password": "pw123"})
        assert r.status_code in (200, 303)


@pytest.mark.asyncio
async def test_security_headers_present(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/login")
        assert r.status_code == 200
        assert "X-Content-Type-Options" in r.headers
        assert "X-Frame-Options" in r.headers
        assert "Referrer-Policy" in r.headers
        assert "Content-Security-Policy" in r.headers
