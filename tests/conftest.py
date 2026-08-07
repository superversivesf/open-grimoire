import pytest
from pathlib import Path
import tempfile
import os
from app.config import Config
from app.auth.session import get_csrf_token


def csrf_for(client, secret: str = "testsecret") -> str:
    """Extract the CSRF token from the client's session cookie."""
    session = client.cookies.get("session")
    if session and session.startswith('"') and session.endswith('"'):
        session = session[1:-1]
    token = get_csrf_token(session, secret)
    assert token is not None, "no CSRF token in session cookie"
    return token


@pytest.fixture(autouse=True)
def disable_csrf_dependency(request, monkeypatch):
    """No-op the require_csrf dependency for most tests.

    The dedicated CSRF tests (test_csrf_token.py) exercise the real
    dependency; everything else focuses on other behavior and would
    otherwise need a token on every POST.
    """
    if "test_csrf_token" in request.node.fspath.strpath:
        return
    import app.auth.csrf as csrf_mod
    monkeypatch.setattr(csrf_mod, "CSRF_ENABLED", False)


@pytest.fixture
def tmp_dirs(tmp_path):
    data_dir = tmp_path / "data"
    db_dir = tmp_path / "db"
    data_dir.mkdir()
    db_dir.mkdir()
    return {"data": data_dir, "db": db_dir}


@pytest.fixture
def test_config(tmp_dirs):
    """Config for tests with DEV_MODE=1 to allow default secret."""
    os.environ["DEV_MODE"] = "1"
    return Config(
        ollama_host="http://localhost:11434",
        session_secret="test-secret",
        data_dir=tmp_dirs["data"],
        db_dir=tmp_dirs["db"],
        models={},
        cookie_secure=False,
    )


@pytest.fixture(autouse=True)
def disable_rate_limiter():
    """Disable rate limiting for all tests.

    The @limiter.limit decorator binds the module singleton limiter at import
    time, so swapping the module-level `_limiter` no longer affects it. Instead
    we disable the real singleton via `.enabled = False` (which the decorator
    checks at call time) and clear its in-memory storage to stop counts from
    one test leaking into another.
    """
    import app.auth.routes as routes
    limiter = routes._get_limiter()
    original_enabled = limiter.enabled
    limiter.enabled = False
    # Clear any accumulated hits from prior tests.
    try:
        limiter._storage.reset()
    except Exception:
        pass
    yield
    limiter.enabled = original_enabled