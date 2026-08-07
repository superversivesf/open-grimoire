import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status, insert_fts_row
from app.auth.passwords import hash_password


@pytest.fixture
def integration_app(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "PF")
    create_doc(uconn, "d1", cid, "Bestiary", "h")
    update_doc_status(uconn, "d1", "done")
    insert_fts_row(uconn, "data/alice/d1/c1/goblin.md", "Goblin", "AC 15 monster", "goblin,monster", "Goblins have AC 15 and HP 7.")
    uconn.close()
    doc_dir = tmp_dirs["data"] / uid / "d1" / "c1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "goblin.md").write_text("# Goblin\n\nAC 15, HP 7.\n")
    app = create_app(test_config, "s")
    mock_loop = MagicMock()
    mock_loop.run = AsyncMock(return_value={"answer": "A goblin has AC 15.", "cites": [{"path": "data/alice/d1/c1/goblin.md", "page": 42, "quote": "AC 15"}], "iterations": 1})
    app.state.agent_loop_factory = lambda toolbox: mock_loop
    return app, cid


@pytest.mark.asyncio
async def test_full_flow(integration_app):
    app, cid = integration_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get("/")
        assert "PF" in r.text
        r = await client.get(f"/collections/{cid}")
        assert "Bestiary" in r.text
        r = await client.post("/sessions", data={"collection_id": cid, "question": "What is a goblin's AC?"})
        assert "AC 15" in r.text
        r = await client.get("/sessions")
        assert r.status_code == 200