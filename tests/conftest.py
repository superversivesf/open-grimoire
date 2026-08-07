import pytest
from pathlib import Path
import tempfile
import os
from app.config import Config


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