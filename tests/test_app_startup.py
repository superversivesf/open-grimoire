import pytest
from app.main import create_app
from app.config import Config


@pytest.mark.asyncio
async def test_app_starts_with_worker(tmp_dirs, monkeypatch):
    cfg = Config("http://localhost:11434", {"query": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    app = create_app(cfg, session_secret="s")
    assert hasattr(app.state, "worker")
    assert app.state.worker is not None