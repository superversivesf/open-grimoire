"""CSRF protection tests — cross-origin state-changing requests must be rejected."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user
from app.auth.passwords import hash_password
from tests.conftest import login


@pytest.fixture
def app_with_user(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    return create_app(test_config, session_secret="testsecret")


@pytest.mark.asyncio
async def test_post_with_matching_origin_allowed(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await login(client)
        r = await client.post("/sessions", data={"question": "hi"}, headers={"origin": "http://test"})
        assert r.status_code in (200, 303, 404, 422)


@pytest.mark.asyncio
async def test_post_with_cross_origin_rejected(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await login(client)
        r = await client.post("/sessions", data={"question": "hi"}, headers={"origin": "http://evil.example"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_post_with_cross_origin_referer_rejected(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await login(client)
        r = await client.post(
            "/sessions",
            data={"question": "hi"},
            headers={"referer": "http://evil.example/page"},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_login_with_cross_origin_rejected(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.post(
            "/login",
            data={"username": "alice", "password": "pw123"},
            headers={"origin": "http://evil.example"},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_requests_not_affected(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await login(client)
        r = await client.get("/", headers={"origin": "http://evil.example"})
        assert r.status_code == 200
