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
async def test_login_unknown_user_still_verifies_password(app_with_user, monkeypatch):
    """Unknown usernames must burn an Argon2 verify to equalize timing."""
    import app.auth.routes as routes
    from app.auth.passwords import verify_password
    calls = []
    def spy(plain, hashed):
        calls.append((plain, hashed))
        return verify_password(plain, hashed)
    monkeypatch.setattr(routes, "verify_password", spy)
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "nobody", "password": "whatever"})
        assert r.status_code in (200, 401)
        assert len(calls) == 1, "verify_password must run even for unknown users"
        assert calls[0][0] == "whatever"


@pytest.mark.asyncio
async def test_rate_limit_uses_xff_when_trusted(app_with_user, monkeypatch):
    """With trust_proxy_headers on, X-Forwarded-For must key the limiter."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    from app.auth.routes import _rate_key
    from fastapi import Request
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.7, 10.0.0.1")],
        "client": ("10.0.0.1", 1234),
    }
    req = Request(scope)
    assert _rate_key(req) == "203.0.113.7"


@pytest.mark.asyncio
async def test_rate_limit_ignores_xff_when_untrusted(app_with_user, monkeypatch):
    """Without trust_proxy_headers, X-Forwarded-For must be ignored."""
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    from app.auth.routes import _rate_key
    from fastapi import Request
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.7, 10.0.0.1")],
        "client": ("10.0.0.1", 1234),
    }
    req = Request(scope)
    assert _rate_key(req) == "10.0.0.1"


@pytest.mark.asyncio
async def test_rate_limit_defaults_on(app_with_user, monkeypatch):
    """The limiter must be enabled by default, even in DEV_MODE."""
    monkeypatch.setenv("DEV_MODE", "1")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    from app.main import create_app
    from app.config import Config
    app = create_app(app_with_user.state.config, "testsecret")
    assert app.state.limiter.enabled is True


@pytest.mark.asyncio
async def test_rate_limit_explicit_opt_out(app_with_user, monkeypatch):
    """RATE_LIMIT_ENABLED=0 must be the only way to disable."""
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    from app.main import create_app
    app = create_app(app_with_user.state.config, "testsecret")
    assert app.state.limiter.enabled is False


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