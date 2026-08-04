"""Journey test: cross-user isolation.

Two users (alice, bob) upload different documents. Verify:
- Alice cannot see Bob's collections
- Alice cannot see Bob's documents
- Alice cannot see Bob's sessions
- Alice cannot access Bob's FTS rows
- Alice cannot read Bob's files
"""
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import (
    init_user_db, create_collection, create_doc, insert_fts_row,
    list_collections, list_docs, get_session, create_session, update_doc_status,
)
from app.auth.passwords import hash_password
from app.agent.tools import ToolBox


@pytest.fixture
def app_two_users(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    alice_uid = create_user(conn, "alice", hash_password("alice_pw"))
    bob_uid = create_user(conn, "bob", hash_password("bob_pw"))
    conn.close()

    alice_uconn = init_user_db(tmp_dirs["db"], alice_uid)
    alice_cid = create_collection(alice_uconn, "Alice Bestiary")
    create_doc(alice_uconn, "alice_doc1", alice_cid, "Alice Goblin Book", "sha_a")
    insert_fts_row(alice_uconn, "alice_doc1/goblin.md", "Goblin", "AC 15", "goblin", "Goblins have AC 15.")
    update_doc_status(alice_uconn, "alice_doc1", "done")
    alice_sid = create_session(alice_uconn, alice_cid)
    alice_uconn.close()

    bob_uconn = init_user_db(tmp_dirs["db"], bob_uid)
    bob_cid = create_collection(bob_uconn, "Bob Campaign")
    create_doc(bob_uconn, "bob_doc1", bob_cid, "Bob Dragon Book", "sha_b")
    insert_fts_row(bob_uconn, "bob_doc1/dragon.md", "Dragon", "AC 19", "dragon", "Dragons have AC 19.")
    update_doc_status(bob_uconn, "bob_doc1", "done")
    bob_uconn.close()

    alice_doc_dir = tmp_dirs["data"] / alice_uid / "alice_doc1"
    alice_doc_dir.mkdir(parents=True)
    (alice_doc_dir / "index.md").write_text("# Alice Goblin Book\n")
    bob_doc_dir = tmp_dirs["data"] / bob_uid / "bob_doc1"
    bob_doc_dir.mkdir(parents=True)
    (bob_doc_dir / "index.md").write_text("# Bob Dragon Book\n")

    cfg = Config("http://localhost:11434", {"query": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    app = create_app(cfg, session_secret="s")
    return app, alice_uid, bob_uid, alice_cid, bob_cid, alice_sid


@pytest.mark.asyncio
async def test_alice_cannot_see_bobs_collections(app_two_users, tmp_dirs):
    app, alice_uid, bob_uid, alice_cid, bob_cid, alice_sid = app_two_users
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "alice_pw"})

        r = await client.get("/")
        assert "Alice Bestiary" in r.text
        assert "Bob Campaign" not in r.text

        r = await client.get(f"/collections/{bob_cid}")
        assert r.status_code == 303, "Alice should be redirected when accessing Bob's collection"


@pytest.mark.asyncio
async def test_alice_cannot_search_bobs_docs(app_two_users, tmp_dirs):
    """Alice's FTS search should not return Bob's documents."""
    app, alice_uid, bob_uid, alice_cid, bob_cid, alice_sid = app_two_users

    alice_toolbox = ToolBox(tmp_dirs["data"], alice_uid, tmp_dirs["db"], alice_cid)
    results = alice_toolbox.fts_search("dragon")
    assert len(results) == 0, "Alice should not find Bob's dragon content"

    results = alice_toolbox.fts_search("goblin")
    assert len(results) >= 1, "Alice should find her own goblin content"


@pytest.mark.asyncio
async def test_alice_cannot_read_bobs_files(app_two_users, tmp_dirs):
    """Alice's read_file should reject Bob's paths."""
    app, alice_uid, bob_uid, alice_cid, bob_cid, alice_sid = app_two_users

    alice_toolbox = ToolBox(tmp_dirs["data"], alice_uid, tmp_dirs["db"], alice_cid)

    bob_file = str(tmp_dirs["data"] / bob_uid / "bob_doc1" / "index.md")
    result = alice_toolbox.read_file(bob_file)
    assert "invalid path" in result or "not found" in result


@pytest.mark.asyncio
async def test_alice_cannot_view_bobs_session(app_two_users, tmp_dirs):
    """Alice's session lookup uses her own DB, so Bob's sessions are invisible."""
    app, alice_uid, bob_uid, alice_cid, bob_cid, alice_sid = app_two_users

    bob_uconn = init_user_db(tmp_dirs["db"], bob_uid)
    bob_sid = create_session(bob_uconn, bob_cid)
    bob_uconn.close()

    alice_uconn = init_user_db(tmp_dirs["db"], alice_uid)
    assert get_session(alice_uconn, bob_sid) is None, "Alice should not find Bob's session"
    assert get_session(alice_uconn, alice_sid) is not None, "Alice should find her own session"
    alice_uconn.close()


@pytest.mark.asyncio
async def test_alice_cannot_list_bobs_docs(app_two_users, tmp_dirs):
    """Alice's list_docs should not include Bob's documents."""
    app, alice_uid, bob_uid, alice_cid, bob_cid, alice_sid = app_two_users

    alice_uconn = init_user_db(tmp_dirs["db"], alice_uid)
    docs = list_docs(alice_uconn)
    for d in docs:
        assert "Bob" not in d["title"], "Alice should not see Bob's docs"
    alice_uconn.close()