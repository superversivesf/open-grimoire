import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection
from app.auth.passwords import hash_password


@pytest.fixture
def app_with_data(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "PF")
    uconn.close()
    app = create_app(test_config, session_secret="s")
    mock_loop = MagicMock()
    mock_loop.run = AsyncMock(return_value={"answer": "AC is 15.", "cites": [{"path": "x.md", "page": 42, "quote": "AC 15"}], "iterations": 1})
    app.state.agent_loop_factory = lambda toolbox: mock_loop
    return app, cid


@pytest.mark.asyncio
async def test_start_session(app_with_data):
    app, cid = app_with_data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post("/sessions", data={"collection_id": cid, "question": "What is AC?"})
        assert r.status_code in (200, 303)
        assert "AC is 15" in r.text


@pytest.mark.asyncio
async def test_continue_session(app_with_data):
    app, cid = app_with_data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post("/sessions", data={"collection_id": cid, "question": "What is AC?"})
        assert r.status_code in (200, 303)
        import re
        m = re.search(r"/sessions/([a-f0-9]+)", r.text)
        assert m is not None
        sid = m.group(1)
        r2 = await client.post(f"/sessions/{sid}", data={"question": "How about goblins?"})
        assert r2.status_code == 200
        assert "AC is 15" in r2.text


@pytest.mark.asyncio
async def test_list_sessions(app_with_data):
    app, cid = app_with_data
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        await client.post("/sessions", data={"collection_id": cid, "question": "q1"})
        r = await client.get("/sessions")
        assert r.status_code == 200