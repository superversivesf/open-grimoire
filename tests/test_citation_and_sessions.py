"""Tests for citation link resolution with real-world path formats.

Tests the problematic cases:
1. LLM returns path without doc_id prefix (e.g. "18_raveller_creation/index.md")
2. LLM returns path with doc_id prefix (e.g. "doc_id/22_choosing_a.md")
3. LLM returns a path that's a directory, not a file
4. Session names should use the first question, not the session ID
"""
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import (
    init_user_db, create_collection, create_doc, insert_fts_row,
    update_doc_status, create_session, get_session,
)
from app.auth.passwords import hash_password

DOC_ID_A = "38dfd1fd2c4249f193f923458891812f"
DOC_ID_B = "2f87ff50a9724db78ef86e772fdabfca"
FILENAME_A = "18_raveller_creation.md"
FILENAME_B = "22_choosing_a.md"
CONTENT_A = "# Raveller Creation\n\nCharacter creation rules."
CONTENT_B = "# Choosing A\n\nChoose your character type."


@pytest.fixture
def app_with_docs(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()

    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "RPG Books")
    create_doc(uconn, DOC_ID_A, cid, "Traveller Core", "sha_a")
    create_doc(uconn, DOC_ID_B, cid, "Dragon Warriors", "sha_b")
    update_doc_status(uconn, DOC_ID_A, "done")
    update_doc_status(uconn, DOC_ID_B, "done")
    insert_fts_row(uconn, f"{DOC_ID_A}/{FILENAME_A}", "RAVELLER CREATION", "Character creation", "creation,character", CONTENT_A)
    insert_fts_row(uconn, f"{DOC_ID_B}/{FILENAME_B}", "CHOOSING A", "Choose character type", "choosing,character", CONTENT_B)
    uconn.close()

    # Create files on disk
    for doc_id, filename, content in [(DOC_ID_A, FILENAME_A, CONTENT_A), (DOC_ID_B, FILENAME_B, CONTENT_B)]:
        doc_dir = tmp_dirs["data"] / uid / doc_id
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / filename).write_text(content)

    cfg = Config("http://localhost:11434", {"query": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    app = create_app(cfg, "s")
    return app, uid, cid


class TestCitationLinkFormats:
    """Test citation links with various path formats the LLM returns."""

    @pytest.mark.asyncio
    async def test_link_with_doc_id_prefix(self, app_with_docs):
        """LLM returns: 'doc_id/filename' — should link to /docs/{doc_id}/view?path={filename}"""
        app, uid, cid = app_with_docs
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            # This is the good case — FTS path with doc_id
            r = await client.get(f"/docs/{DOC_ID_B}/view?path={FILENAME_B}")
            assert r.status_code == 200
            assert "Choosing A" in r.text

    @pytest.mark.asyncio
    async def test_search_bare_filename_no_doc_id(self, app_with_docs):
        """LLM returns: '18_raveller_creation.md' (no doc_id, correct filename) — search should find it."""
        app, uid, cid = app_with_docs
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            r = await client.get(f"/docs/search?path={FILENAME_A}", follow_redirects=False)
            assert r.status_code == 303
            assert DOC_ID_A in r.headers["location"]
            assert FILENAME_A in r.headers["location"]

    @pytest.mark.asyncio
    async def test_search_hallucinated_dir_path(self, app_with_docs):
        """LLM returns: '18_raveller_creation/index.md' (hallucinated directory + index.md) — search should find the real file."""
        app, uid, cid = app_with_docs
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            # The LLM hallucinated a directory path — search should find the file by partial name match
            r = await client.get("/docs/search?path=18_raveller_creation/index.md", follow_redirects=False)
            assert r.status_code == 303
            assert DOC_ID_A in r.headers["location"]
            assert "raveller_creation" in r.headers["location"]

    @pytest.mark.asyncio
    async def test_search_partial_filename(self, app_with_docs):
        """LLM returns a partial/garbled filename — search should try partial matching."""
        app, uid, cid = app_with_docs
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            # Try searching for just the key part
            r = await client.get("/docs/search?path=raveller_creation.md", follow_redirects=False)
            assert r.status_code == 303
            assert DOC_ID_A in r.headers["location"]

    @pytest.mark.asyncio
    async def test_search_strips_index_md(self, app_with_docs):
        """LLM appends /index.md to a filename — search should strip it and find the .md file."""
        app, uid, cid = app_with_docs
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            r = await client.get(f"/docs/search?path={FILENAME_A.replace('.md', '')}/index.md", follow_redirects=False)
            assert r.status_code == 303
            assert DOC_ID_A in r.headers["location"]


class TestSessionNames:
    """Test that sessions have useful names instead of just IDs."""

    @pytest.mark.asyncio
    async def test_session_has_first_question_as_name(self, app_with_docs, tmp_dirs):
        """When a session is created, it should store the first question as its name."""
        app, uid, cid = app_with_docs
        from unittest.mock import AsyncMock, MagicMock
        # Mock the agent loop so we don't need a real LLM
        mock_loop = MagicMock()
        mock_loop.run = AsyncMock(return_value={
            "answer": "Test answer.", "cites": [], "suggestions": [],
            "iterations": 1,
        })
        app.state.agent_loop_factory = lambda toolbox: mock_loop

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            r = await client.post("/sessions", data={
                "collection_id": cid,
                "question": "How do I create a character in traveller?",
            })
            assert r.status_code in (200, 303)

        # Check the session in the DB has a name
        uconn = init_user_db(tmp_dirs["db"], uid)
        sessions = uconn.execute("SELECT session_id, name FROM sessions ORDER BY created_at DESC LIMIT 1").fetchall()
        assert len(sessions) == 1
        session = sessions[0]
        # The session should have a 'name' column with the first question
        name = session["name"] if "name" in session.keys() else None
        assert name is not None, "Session should have a name column"
        assert "create a character" in name or "traveller" in name.lower()
        uconn.close()

    @pytest.mark.asyncio
    async def test_sessions_list_shows_names(self, app_with_docs, tmp_dirs):
        """The sessions list page should show the question name, not just the ID."""
        app, uid, cid = app_with_docs
        from unittest.mock import AsyncMock, MagicMock
        mock_loop = MagicMock()
        mock_loop.run = AsyncMock(return_value={
            "answer": "Test answer.", "cites": [], "suggestions": [],
            "iterations": 1,
        })
        app.state.agent_loop_factory = lambda toolbox: mock_loop

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            await client.post("/sessions", data={
                "collection_id": cid,
                "question": "What is the meaning of life?",
            })
            r = await client.get("/sessions")
            assert r.status_code == 200
            assert "meaning of life" in r.text.lower(), "Sessions page should show the question as the session name"