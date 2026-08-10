"""Shared collection agent path — sessions and ToolBox resolve to owner's FTS."""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock
from app.agent.tools import ToolBox
from tests.conftest import csrf_for, login, DOC_ID_A

@pytest.fixture
def shared_agent_setup(shared_collection_fixture):
    """alice owns c1 with a goblin doc; bob is member; eve is nobody."""
    app, alice, bob, eve, cid = shared_collection_fixture
    return app, alice, bob, eve, cid


def test_toolbox_resolves_owner_fts(shared_agent_setup, tmp_dirs):
    """ToolBox with owner_uid must search the owner's DB, not the member's."""
    app, alice, bob, _eve, cid = shared_agent_setup
    toolbox = ToolBox(tmp_dirs["data"], bob, tmp_dirs["db"], cid, owner_uid=alice)
    results = toolbox.fts_search("goblin")
    assert len(results) >= 1
    assert "goblin" in results[0]["path"].lower()


def test_toolbox_owner_read_file(shared_agent_setup, tmp_dirs):
    """ToolBox with owner_uid must read files from the owner's tree."""
    app, alice, bob, _eve, cid = shared_agent_setup
    toolbox = ToolBox(tmp_dirs["data"], bob, tmp_dirs["db"], cid, owner_uid=alice)
    content = toolbox.read_file(f"{DOC_ID_A}/01_goblin.md")
    assert "AC 15" in content


@pytest.mark.asyncio
async def test_member_sessions_use_owner_fts(shared_agent_setup, tmp_dirs):
    """A member starting a session against the shared collection must get
    answers from the owner's FTS index."""
    app, alice, bob, _eve, cid = shared_agent_setup
    # Mock the loop to capture the ToolBox it receives
    captured = {}

    def make_factory():
        def factory(toolbox):
            captured["toolbox"] = toolbox
            mock_loop = MagicMock()
            async def mock_run(history, question):
                results = toolbox.fts_search("goblin")
                return {"answer": f"Found {len(results)} goblin docs.", "cites": [], "iterations": 1}
            mock_loop.run = mock_run
            return mock_loop
        return factory

    app.state.agent_loop_factory = make_factory()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.post(
            "/sessions",
            data={"collection_id": cid, "question": "What is a goblin?", "_csrf": csrf_for(client)},
        )
        assert r.status_code == 200
        assert "Found 1 goblin docs" in r.text


@pytest.mark.asyncio
async def test_non_member_cannot_start_session(shared_agent_setup):
    app, alice, bob, eve, cid = shared_agent_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "eve", password="pw123456")
        r = await client.post(
            "/sessions",
            data={"collection_id": cid, "question": "hi", "_csrf": csrf_for(client)},
            follow_redirects=False,
        )
        assert r.status_code == 303
