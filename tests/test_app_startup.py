import pytest
from app.main import create_app
from app.config import Config


@pytest.mark.asyncio
async def test_app_starts_with_worker(tmp_dirs, test_config):
    app = create_app(test_config, session_secret="s")
    assert hasattr(app.state, "worker")
    assert app.state.worker is not None