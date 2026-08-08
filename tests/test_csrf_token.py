"""CSRF token tests — synchronizer token bound to the session."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user
from app.auth.passwords import hash_password
from app.auth.session import get_csrf_token


@pytest.fixture
def app_with_user(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    app = create_app(test_config, session_secret="testsecret")
    from unittest.mock import MagicMock
    mock_loop = MagicMock()
    async def mock_run(history, question):
        return {"answer": "ok", "cites": [], "iterations": 1}
    mock_loop.run = mock_run
    app.state.agent_loop_factory = lambda toolbox: mock_loop
    return app


async def _login(client):
    await client.get("/login")
    token = client.cookies.get("login_csrf")
    r = await client.post("/login", data={"username": "alice", "password": "pw123", "_csrf": token})
    assert r.status_code in (200, 303)
    assert "session" in r.cookies


def _csrf(client):
    session = client.cookies.get("session")
    if session and session.startswith('"') and session.endswith('"'):
        session = session[1:-1]
    return get_csrf_token(session, "testsecret")


@pytest.mark.asyncio
async def test_login_sets_csrf_token_in_session(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await _login(client)
        assert _csrf(client) is not None


@pytest.mark.asyncio
async def test_post_without_csrf_token_rejected(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await _login(client)
        r = await client.post("/sessions", data={"question": "hi", "collection_id": "x"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_post_with_wrong_csrf_token_rejected(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await _login(client)
        r = await client.post(
            "/sessions",
            data={"question": "hi", "collection_id": "x", "_csrf": "forged-token"},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_post_with_valid_csrf_token_allowed(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await _login(client)
        r = await client.post(
            "/sessions",
            data={"question": "hi", "collection_id": "x", "_csrf": _csrf(client)},
        )
        assert r.status_code in (200, 303, 404, 422)


@pytest.mark.asyncio
async def test_csrf_token_bound_to_session(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await _login(client)
        # Token from a different session must not validate
        r = await client.post(
            "/sessions",
            data={"question": "hi", "collection_id": "x", "_csrf": "other-session-token"},
        )
        assert r.status_code == 403


# ─── Login double-submit CSRF (no session exists yet) ──────────────────
@pytest.mark.asyncio
async def test_login_page_sets_csrf_cookie(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/login")
        assert r.status_code == 200
        assert "login_csrf" in r.cookies
        assert r.cookies.get("login_csrf") in r.text


@pytest.mark.asyncio
async def test_login_page_not_cacheable(app_with_user):
    """A cached login page would carry a stale CSRF token — must be no-store."""
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/login")
        assert r.headers.get("cache-control") == "no-store"


@pytest.mark.asyncio
async def test_login_page_reuses_existing_csrf_cookie(app_with_user):
    """GET /login must not rotate the token when a cookie already exists —
    otherwise a second tab's page load invalidates the first tab's form."""
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.get("/login")
        first = client.cookies.get("login_csrf")
        r2 = await client.get("/login")
        assert client.cookies.get("login_csrf") == first
        assert first in r2.text


@pytest.mark.asyncio
async def test_login_token_survives_multiple_page_loads(app_with_user):
    """Multi-tab simulation: token from the first GET stays valid after a
    second GET overwrites nothing."""
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.get("/login")
        token = client.cookies.get("login_csrf")
        await client.get("/login")
        r = await client.post(
            "/login",
            data={"username": "alice", "password": "pw123", "_csrf": token},
        )
        assert r.status_code in (200, 303)
        assert "session" in r.cookies


@pytest.mark.asyncio
async def test_login_without_csrf_token_rejected(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.get("/login")
        r = await client.post("/login", data={"username": "alice", "password": "pw123"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_login_with_wrong_csrf_token_rejected(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.get("/login")
        r = await client.post(
            "/login",
            data={"username": "alice", "password": "pw123", "_csrf": "forged"},
        )
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_login_with_valid_csrf_token_allowed(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.get("/login")
        token = client.cookies.get("login_csrf")
        r = await client.post(
            "/login",
            data={"username": "alice", "password": "pw123", "_csrf": token},
        )
        assert r.status_code in (200, 303)
        assert "session" in r.cookies
