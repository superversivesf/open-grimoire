"""Shared collection agent path — sessions and ToolBox resolve to owner's FTS."""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock
from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user, add_collection_member
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status, insert_fts_row
from app.auth.passwords import hash_password
from app.agent.tools import ToolBox
from tests.conftest import csrf_for

DOC_ID_A = "38dfd1fd2c4249f193f923458891812f"


@pytest.fixture
def shared_agent_setup(tmp_dirs, test_config):
    """alice owns c1 with a goblin doc; bob is member."""
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw123456"))
    bob = create_user(conn, "bob", hash_password("pw123456"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], alice)
    cid = create_collection(uconn, "Shared Shelf")
    create_doc(uconn, DOC_ID_A, cid, "Goblin Book", "sha1")
    update_doc_status(uconn, DOC_ID_A, "done")
    insert_fts_row(uconn, f"{DOC_ID_A}/01_goblin.md", "Goblin", "Goblin stats.", "goblin", "Goblins have AC 15.")
    uconn.close()
    doc_dir = tmp_dirs["data"] / alice / DOC_ID_A
    doc_dir.mkdir(parents=True)
    (doc_dir / "01_goblin.md").write_text("# Goblin\n\nAC 15, HP 7.\n")
    conn = init_shared_db(tmp_dirs["db"])
    add_collection_member(conn, cid, alice, "owner")
    add_collection_member(conn, cid, bob, "member")
    conn.close()
    app = create_app(test_config, session_secret="testsecret")
    return app, alice, bob, cid


async def _login(client, username):
    await client.get("/login")
    token = client.cookies.get("login_csrf")
    r = await client.post("/login", data={"username": username, "password": "pw123456", "_csrf": token})
    assert r.status_code in (200, 303)


def test_toolbox_resolves_owner_fts(shared_agent_setup, tmp_dirs):
    """ToolBox with owner_uid must search the owner's DB, not the member's."""
    app, alice, bob, cid = shared_agent_setup
    toolbox = ToolBox(tmp_dirs["data"], bob, tmp_dirs["db"], cid, owner_uid=alice)
    results = toolbox.fts_search("goblin")
    assert len(results) >= 1
    assert "goblin" in results[0]["path"].lower()


def test_toolbox_owner_read_file(shared_agent_setup, tmp_dirs):
    """ToolBox with owner_uid must read files from the owner's tree."""
    app, alice, bob, cid = shared_agent_setup
    toolbox = ToolBox(tmp_dirs["data"], bob, tmp_dirs["db"], cid, owner_uid=alice)
    content = toolbox.read_file(f"{DOC_ID_A}/01_goblin.md")
    assert "AC 15" in content


@pytest.mark.asyncio
async def test_member_sessions_use_owner_fts(shared_agent_setup, tmp_dirs):
    """A member starting a session against the shared collection must get
    answers from the owner's FTS index."""
    app, alice, bob, cid = shared_agent_setup
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
        await _login(client, "bob")
        r = await client.post(
            "/sessions",
            data={"collection_id": cid, "question": "What is a goblin?", "_csrf": csrf_for(client)},
        )
        assert r.status_code == 200
        assert "Found 1 goblin docs" in r.text


@pytest.mark.asyncio
async def test_non_member_cannot_start_session(shared_agent_setup):
    app, alice, bob, cid = shared_agent_setup
    conn = init_shared_db(app.state.config.db_dir)
    eve = create_user(conn, "eve", hash_password("pw123456"))
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "eve")
        r = await client.post(
            "/sessions",
            data={"collection_id": cid, "question": "hi", "_csrf": csrf_for(client)},
            follow_redirects=False,
        )
        assert r.status_code == 303
