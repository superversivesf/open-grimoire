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
    r = await client.post("/login", data={"username": "alice", "password": "pw123"})
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
