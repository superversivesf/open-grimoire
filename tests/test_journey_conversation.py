"""Journey test: multi-turn conversation with session history building.

Start a session → ask question (mocked answer) → follow-up question →
verify history has 2 turns → verify history persists across requests.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection
from app.auth.passwords import hash_password
from app.agent.history import load_history
import re


@pytest.fixture
def app_with_data(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "PF")
    uconn.close()
    app = create_app(test_config, session_secret="s")

    answers = [
        {"answer": "A goblin has AC 15.", "cites": [{"path": "x.md", "page": 42, "quote": "AC 15"}], "iterations": 1},
        {"answer": "To grapple, roll an attack.", "cites": [{"path": "y.md", "page": 55, "quote": "grapple"}], "iterations": 1},
        {"answer": "Yes, you can grapple two creatures.", "cites": [], "iterations": 1},
    ]
    call_count = [0]
    def make_factory():
        mock_loop = MagicMock()
        async def mock_run(history, question):
            result = answers[min(call_count[0], len(answers) - 1)]
            call_count[0] += 1
            return result
        mock_loop.run = mock_run
        return lambda toolbox: mock_loop
    app.state.agent_loop_factory = make_factory()
    return app, uid, cid


@pytest.mark.asyncio
async def test_multi_turn_conversation(app_with_data, tmp_dirs):
    """Ask 3 questions, verify history builds up across turns."""
    app, uid, cid = app_with_data

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})

        r = await client.post("/sessions", data={"collection_id": cid, "question": "What is a goblin's AC?"})
        assert "AC 15" in r.text
        sid = re.search(r"/sessions/([a-f0-9]+)", r.text)
        assert sid is not None
        session_id = sid.group(1)

        r2 = await client.post(f"/sessions/{session_id}", data={"question": "How do I grapple?"})
        assert r2.status_code == 200
        assert "grapple" in r2.text.lower()

        r3 = await client.post(f"/sessions/{session_id}", data={"question": "Can I grapple two at once?"})
        assert r3.status_code == 200
        assert "two" in r3.text.lower()

    uconn = init_user_db(tmp_dirs["db"], uid)
    history = load_history(uconn, session_id)
    assert len(history) == 3, f"expected 3 turns, got {len(history)}"
    assert history[0]["user"] == "What is a goblin's AC?"
    assert history[0]["agent"] == "A goblin has AC 15."
    assert history[0]["cites"][0]["page"] == 42
    assert history[1]["user"] == "How do I grapple?"
    assert history[1]["agent"] == "To grapple, roll an attack."
    assert history[2]["user"] == "Can I grapple two at once?"
    assert history[2]["agent"] == "Yes, you can grapple two creatures."
    uconn.close()


@pytest.mark.asyncio
async def test_session_history_persists_across_requests(app_with_data, tmp_dirs):
    """History from a previous turn is visible when viewing the session."""
    app, uid, cid = app_with_data

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})

        r = await client.post("/sessions", data={"collection_id": cid, "question": "What is AC?"})
        sid = re.search(r"/sessions/([a-f0-9]+)", r.text).group(1)

        r2 = await client.get(f"/sessions/{sid}")
        assert r2.status_code == 200
        assert "AC 15" in r2.text, "previous answer should be visible in session view"