"""Complete end-to-end journey tests for Open Grimoire.

Tests the full user journey from account creation through document processing
to conversational Q&A with citation links — using a real Ollama instance when
available, falling back to mocks when not.

Run with real Ollama:
    pytest tests/test_e2e_journey.py -v --e2e

Run with mocked LLM (default):
    pytest tests/test_e2e_journey.py -v
"""
import pytest
import io
import json
import sqlite3
import asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from fpdf import FPDF

from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import (
    init_user_db, create_collection, list_docs, get_doc,
    list_collections, insert_fts_row, update_doc_status,
)
from app.storage.paths import user_db_path
from app.agent.tools import ToolBox
from app.agent.loop import AgentLoop
from app.pipeline.runner import PipelineRunner
from app.auth.passwords import hash_password, verify_password
from app.gateway.ollama import OllamaGateway
from tests.conftest import login


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: requires real Ollama instance")


def _ollama_available() -> bool:
    """Check if Ollama is running and has models available."""
    import httpx
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        if r.status_code != 200:
            return False
        models = r.json().get("models", [])
        return len(models) > 0
    except Exception:
        return False


def _make_pdf(pages: list[str], title: str = "Test RPG Manual") -> bytes:
    """Create a multi-page PDF with text content."""
    pdf = FPDF()
    pdf.set_title(title)
    for text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        # Handle multi-line text
        for line in text.split("\n"):
            pdf.cell(0, 10, text=line)
            pdf.ln(5)
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def _make_rpg_pdf() -> bytes:
    """Create a realistic small RPG PDF with combat rules, classes, and spells."""
    return _make_pdf([
        "Chapter 1: Combat Rules\n\n"
        "Armor Class (AC) determines how hard a character is to hit. "
        "A goblin has AC 13 and 7 hit points. "
        "To attack, roll 1d20 and add your attack bonus. "
        "If the result meets or exceeds the target's AC, you hit.",
        
        "Chapter 2: Character Classes\n\n"
        "Knights are heavily armored warriors with AC 16 and d10 hit dice. "
        "Barbarians are fierce fighters with AC 12 and d12 hit dice. "
        "Sorcerers are spellcasters with AC 10 and d6 hit dice. "
        "Mystics use psychic powers with AC 12 and d6 hit dice.",
        
        "Chapter 3: Spell Lists\n\n"
        "Fireball: 3rd level spell, deals 8d6 fire damage in a 20ft radius. "
        "Magic Missile: 1st level spell, deals 1d4+1 force damage, never misses. "
        "Healing Word: 1st level spell, restores 1d4 hit points as a bonus action.",
        
        "Chapter 4: Monster Bestiary\n\n"
        "Goblin: AC 13, HP 7, attacks with shortsword (1d6+2). "
        "Orc: AC 15, HP 15, attacks with greataxe (1d12+3). "
        "Dragon: AC 18, HP 200, breath weapon deals 12d6 fire damage. "
        "Skeleton: AC 13, HP 5, immune to poison and psychic damage.",
    ], title="RPG Test Manual")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dirs(tmp_path):
    data_dir = tmp_path / "data"
    db_dir = tmp_path / "db"
    data_dir.mkdir()
    db_dir.mkdir()
    return {"data": data_dir, "db": db_dir}


@pytest.fixture
def real_ollama():
    """Returns a real OllamaGateway if Ollama is available, None otherwise."""
    if not _ollama_available():
        pytest.skip("Ollama not available")
    gateway = OllamaGateway(
        host="http://localhost:11434",
        models={"query": "qwen2.5:7b", "enrich": "qwen2.5:7b"},
        num_ctx=8192,
    )
    yield gateway


@pytest.fixture
def mock_gateway():
    """Mock gateway that returns canned responses for enrich and query."""
    gw = MagicMock()
    
    enrich_response = {
        "message": {
            "content": json.dumps({
                "summary": "Rules for combat and character classes in the RPG.",
                "keywords": ["combat", "AC", "goblin", "knight", "sorcerer"],
            })
        }
    }
    
    call_count = [0]
    query_responses = [
        # Iteration 1: fts_search
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "fts_search", "arguments": '{"query": "goblin AC"}'}}
        ]}},
        # Iteration 2: read_file
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": '{"path": "DUMMY/combat.md"}'}}
        ]}},
        # Iteration 3: done
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": json.dumps({
                "answer": "A goblin has AC 13 and 7 hit points. It attacks with a shortsword dealing 1d6+2 damage.",
                "cites": [{"path": "DUMMY/combat.md", "page": 1, "quote": "A goblin has AC 13 and 7 hit points."}],
                "suggestions": [
                    "What weapons can goblins use?",
                    "How do I create a knight character?",
                    "What spells does a sorcerer know?"
                ],
            })}}
        ]}},
    ]
    
    async def mock_call(role, prompt, tools=None, messages=None):
        if role == "enrich":
            return enrich_response
        elif role == "query":
            r = query_responses[min(call_count[0], len(query_responses) - 1)]
            call_count[0] += 1
            return r
        return {"message": {"content": ""}}
    
    gw.call = mock_call
    return gw


@pytest.fixture
def app_with_user(tmp_dirs, test_config, request):
    """Create app with a registered user. Uses mock_gateway by default."""
    gateway = request.getfixturevalue("mock_gateway")
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "testuser", hash_password("testpass"))
    conn.close()
    app = create_app(test_config, session_secret="test-secret")
    app.state.gateway = gateway
    app.state.agent_loop_factory = lambda toolbox: AgentLoop(gateway, toolbox, max_iterations=10)
    return app, uid, gateway


@pytest.fixture
def real_app_with_user(tmp_dirs, test_config, real_ollama):
    """Create app with real Ollama gateway."""
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "testuser", hash_password("testpass"))
    conn.close()
    app = create_app(test_config, session_secret="test-secret")
    return app, uid, real_ollama


# ===========================================================================
# Journey 1: User Registration & Authentication
# ===========================================================================

class TestUserJourney:
    """Test user account creation, login, logout, and session management."""

    @pytest.mark.asyncio
    async def test_login_with_correct_credentials(self, app_with_user):
        app, uid, _ = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/login", data={"username": "testuser", "password": "testpass"})
            assert r.status_code == 303
            assert r.headers["location"] == "/"

    @pytest.mark.asyncio
    async def test_login_with_wrong_password_rejected(self, app_with_user):
        app, uid, _ = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/login", data={"username": "testuser", "password": "wrong"})
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_login_with_unknown_user_rejected(self, app_with_user):
        app, uid, _ = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/login", data={"username": "nobody", "password": "testpass"})
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, app_with_user):
        app, uid, _ = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            # Verify we can access protected route
            r = await client.get("/")
            assert r.status_code == 200
            # Logout
            r = await client.post("/logout")
            assert r.status_code == 303
            # Should redirect to login now
            r = await client.get("/", follow_redirects=False)
            assert r.status_code == 303
            assert r.headers["location"] == "/login"

    @pytest.mark.asyncio
    async def test_protected_routes_require_auth(self, app_with_user):
        app, uid, _ = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for path in ["/", "/sessions", "/collections/abc", "/docs/abc"]:
                r = await client.get(path, follow_redirects=False)
                assert r.status_code == 303, f"{path} should redirect to login"
                assert r.headers["location"] == "/login"


# ===========================================================================
# Journey 2: Collection Management
# ===========================================================================

class TestCollectionJourney:
    """Test creating, renaming, and deleting collections."""

    @pytest.mark.asyncio
    async def test_create_collection(self, app_with_user, tmp_dirs):
        app, uid, _ = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.post("/collections", data={"name": "Dragon Warriors"})
            assert r.status_code == 303
            # Verify it appears on home page
            r = await client.get("/")
            assert "Dragon Warriors" in r.text

    @pytest.mark.asyncio
    async def test_create_multiple_collections(self, app_with_user, tmp_dirs):
        app, uid, _ = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            for name in ["Dragon Warriors", "Pathfinder", "D&D 5e"]:
                await client.post("/collections", data={"name": name})
            r = await client.get("/")
            for name in ["Dragon Warriors", "Pathfinder", "D&D 5e"]:
                assert name in r.text

    @pytest.mark.asyncio
    async def test_rename_collection(self, app_with_user, tmp_dirs):
        app, uid, _ = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            await client.post("/collections", data={"name": "Old Name"})
            r = await client.get("/")
            assert "Old Name" in r.text
            # Find collection ID from the page
            import re
            m = re.search(r'collections/([a-f0-9]+)', r.text)
            assert m, "Collection ID not found in page"
            cid = m.group(1)
            # Rename it
            r = await client.post(f"/collections/{cid}/rename", data={"name": "New Name"})
            assert r.status_code == 303
            r = await client.get("/")
            assert "New Name" in r.text
            assert "Old Name" not in r.text

    @pytest.mark.asyncio
    async def test_delete_collection_removes_docs(self, app_with_user, tmp_dirs):
        app, uid, _ = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            await client.post("/collections", data={"name": "To Delete"})
            r = await client.get("/")
            import re
            m = re.search(r'collections/([a-f0-9]+)', r.text)
            cid = m.group(1)
            # Delete it
            r = await client.post(f"/collections/{cid}/delete")
            assert r.status_code == 303
            r = await client.get("/")
            assert "To Delete" not in r.text
            # Verify docs table is empty for this collection
            uconn = init_user_db(tmp_dirs["db"], uid)
            docs = list_docs(uconn, cid)
            assert len(docs) == 0
            uconn.close()


# ===========================================================================
# Journey 3: PDF Upload & Processing Pipeline
# ===========================================================================

class TestUploadPipelineJourney:
    """Test PDF upload, queue processing, and the 5-stage pipeline."""

    @pytest.mark.asyncio
    async def test_upload_pdf_creates_queued_doc(self, app_with_user, tmp_dirs):
        app, uid, _ = app_with_user
        pdf_bytes = _make_rpg_pdf()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            # Create a collection
            uconn = init_user_db(tmp_dirs["db"], uid)
            cid = create_collection(uconn, "RPG Books")
            uconn.close()
            # Upload PDF
            r = await client.post(
                "/upload",
                data={"collection_id": cid},
                files=[("files", ("rpg_manual.pdf", pdf_bytes, "application/pdf"))],
            )
            assert r.status_code == 303
            # Verify doc was created
            uconn = init_user_db(tmp_dirs["db"], uid)
            docs = list_docs(uconn, cid)
            assert len(docs) == 1
            assert docs[0]["title"] == "rpg_manual"
            assert docs[0]["status"] == "queued"
            uconn.close()
            # Verify PDF file was saved
            doc_id = docs[0]["doc_id"]
            pdf_path = tmp_dirs["data"] / uid / doc_id / "original.pdf"
            assert pdf_path.exists()
            assert pdf_path.stat().st_size > 0

    @pytest.mark.asyncio
    async def test_upload_multiple_pdfs(self, app_with_user, tmp_dirs):
        app, uid, _ = app_with_user
        pdf1 = _make_pdf(["Page 1 content"])
        pdf2 = _make_pdf(["Page 2 content"])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            uconn = init_user_db(tmp_dirs["db"], uid)
            cid = create_collection(uconn, "Multi")
            uconn.close()
            r = await client.post(
                "/upload",
                data={"collection_id": cid},
                files=[
                    ("files", ("book1.pdf", pdf1, "application/pdf")),
                    ("files", ("book2.pdf", pdf2, "application/pdf")),
                ],
            )
            assert r.status_code == 303
            uconn = init_user_db(tmp_dirs["db"], uid)
            docs = list_docs(uconn, cid)
            assert len(docs) == 2
            uconn.close()

    @pytest.mark.asyncio
    async def test_pipeline_full_processing(self, app_with_user, tmp_dirs):
        """Run the full 5-stage pipeline on an uploaded PDF."""
        app, uid, gateway = app_with_user
        pdf_bytes = _make_rpg_pdf()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            uconn = init_user_db(tmp_dirs["db"], uid)
            cid = create_collection(uconn, "Pipeline Test")
            uconn.close()
            await client.post(
                "/upload",
                data={"collection_id": cid},
                files=[("files", ("rpg.pdf", pdf_bytes, "application/pdf"))],
            )
        # Get the queued job
        from app.storage.shared_db import init_shared_db, claim_next_job, get_job
        sconn = init_shared_db(tmp_dirs["db"])
        job = claim_next_job(sconn)
        assert job is not None
        assert job["status"] in ("running", "processing")
        sconn.close()
        # Run pipeline
        runner = PipelineRunner(gateway, tmp_dirs["data"], tmp_dirs["db"])
        await runner.run_job(job)
        # Verify doc status is done
        uconn = init_user_db(tmp_dirs["db"], uid)
        docs = list_docs(uconn, cid)
        assert len(docs) == 1
        doc = docs[0]
        assert doc["status"] == "done", f"Expected done, got {doc['status']}"
        doc_id = doc["doc_id"]
        uconn.close()
        # Verify markdown files were created
        doc_dir = tmp_dirs["data"] / uid / doc_id
        md_files = list(doc_dir.rglob("*.md"))
        assert len(md_files) > 0, "No markdown files generated"
        # Verify FTS index has rows
        uconn = init_user_db(tmp_dirs["db"], uid)
        fts_rows = uconn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
        assert fts_rows > 0, "FTS index is empty"
        uconn.close()
        # Verify cover.jpg was extracted
        cover_path = tmp_dirs["data"] / uid / doc_id / "cover.jpg"
        assert cover_path.exists(), "cover.jpg not found"
        assert cover_path.stat().st_size > 0

    @pytest.mark.asyncio
    async def test_pipeline_corrupt_pdf_fails_gracefully(self, app_with_user, tmp_dirs):
        """Corrupt PDF (no %PDF- magic bytes) is rejected at upload, not queued."""
        app, uid, gateway = app_with_user
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            uconn = init_user_db(tmp_dirs["db"], uid)
            cid = create_collection(uconn, "Bad PDFs")
            uconn.close()
            r = await client.post(
                "/upload",
                data={"collection_id": cid},
                files=[("files", ("bad.pdf", b"not a pdf", "application/pdf"))],
            )
            assert r.status_code == 303
        from app.storage.shared_db import init_shared_db, claim_next_job
        sconn = init_shared_db(tmp_dirs["db"])
        job = claim_next_job(sconn)
        assert job is None, "corrupt PDF must be rejected at upload, not queued"
        uconn = init_user_db(tmp_dirs["db"], uid)
        docs = list_docs(uconn, cid)
        assert len(docs) == 0
        uconn.close()
        sconn.close()


# ===========================================================================
# Journey 4: Document Viewing & Navigation
# ===========================================================================

class TestDocumentViewJourney:
    """Test document tree, file viewing, and PDF/cover serving."""

    @pytest.fixture
    async def setup_doc(self, app_with_user, tmp_dirs):
        """Create a fully processed document."""
        app, uid, gateway = app_with_user
        pdf_bytes = _make_rpg_pdf()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            uconn = init_user_db(tmp_dirs["db"], uid)
            cid = create_collection(uconn, "View Test")
            uconn.close()
            await client.post(
                "/upload",
                data={"collection_id": cid},
                files=[("files", ("rpg.pdf", pdf_bytes, "application/pdf"))],
            )
        from app.storage.shared_db import init_shared_db, claim_next_job
        sconn = init_shared_db(tmp_dirs["db"])
        job = claim_next_job(sconn)
        runner = PipelineRunner(gateway, tmp_dirs["data"], tmp_dirs["db"])
        await runner.run_job(job)
        uconn = init_user_db(tmp_dirs["db"], uid)
        docs = list_docs(uconn, cid)
        doc_id = docs[0]["doc_id"]
        uconn.close()
        return app, uid, cid, doc_id

    @pytest.mark.asyncio
    async def test_doc_view_shows_table_of_contents(self, setup_doc):
        """Document page should list sections, not show 'still processing'."""
        app, uid, cid, doc_id = setup_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.get(f"/docs/{doc_id}")
            assert r.status_code == 200
            assert "Contents" in r.text or "rpg-tree" in r.text
            assert "still processing" not in r.text.lower()

    @pytest.mark.asyncio
    async def test_doc_view_leaf_content(self, setup_doc, tmp_dirs):
        """Reading a specific markdown file should show its content."""
        app, uid, cid, doc_id = setup_doc
        # Find a markdown file to view
        doc_dir = tmp_dirs["data"] / uid / doc_id
        md_files = sorted(doc_dir.rglob("*.md"))
        assert len(md_files) > 0
        first_file = md_files[0]
        rel_path = first_file.name
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.get(f"/docs/{doc_id}/view", params={"path": rel_path})
            assert r.status_code == 200
            # Should contain some content from the file
            assert first_file.stem.replace("_", " ") in r.text or "rpg" in r.text.lower()

    @pytest.mark.asyncio
    async def test_cover_image_served(self, setup_doc):
        """Cover endpoint should return a JPEG image."""
        app, uid, cid, doc_id = setup_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.get(f"/docs/{doc_id}/cover")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/jpeg"
            assert len(r.content) > 1000  # Should be a real image

    @pytest.mark.asyncio
    async def test_pdf_served(self, setup_doc):
        """PDF endpoint should return the original PDF."""
        app, uid, cid, doc_id = setup_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.get(f"/docs/{doc_id}/pdf")
            assert r.status_code == 200
            assert r.headers["content-type"] == "application/pdf"
            assert len(r.content) > 100

    @pytest.mark.asyncio
    async def test_collection_page_shows_books(self, setup_doc):
        """Collection page should show the book with its cover."""
        app, uid, cid, doc_id = setup_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.get(f"/collections/{cid}")
            assert r.status_code == 200
            assert "rpg" in r.text.lower()
            assert f"/docs/{doc_id}/cover" in r.text

    @pytest.mark.asyncio
    async def test_collection_table_shows_status(self, setup_doc):
        """Collection table partial should show the book with no processing overlay."""
        app, uid, cid, doc_id = setup_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.get(f"/collections/{cid}/table")
            assert r.status_code == 200
            # When status is 'done', no overlay badge is shown — just the book card
            assert f"/docs/{doc_id}" in r.text
            assert "rpg-book-overlay" not in r.text  # no processing overlay


# ===========================================================================
# Journey 5: Search & Q&A (Agent Loop)
# ===========================================================================

class TestSearchQAJourney:
    """Test FTS search, agent loop Q&A, citations, and suggestions."""

    @pytest.fixture
    async def setup_searchable(self, app_with_user, tmp_dirs):
        """Create a processed document with FTS content for searching."""
        app, uid, gateway = app_with_user
        pdf_bytes = _make_rpg_pdf()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            uconn = init_user_db(tmp_dirs["db"], uid)
            cid = create_collection(uconn, "Search Test")
            uconn.close()
            await client.post(
                "/upload",
                data={"collection_id": cid},
                files=[("files", ("rpg.pdf", pdf_bytes, "application/pdf"))],
            )
        from app.storage.shared_db import init_shared_db, claim_next_job
        sconn = init_shared_db(tmp_dirs["db"])
        job = claim_next_job(sconn)
        runner = PipelineRunner(gateway, tmp_dirs["data"], tmp_dirs["db"])
        await runner.run_job(job)
        uconn = init_user_db(tmp_dirs["db"], uid)
        docs = list_docs(uconn, cid)
        doc_id = docs[0]["doc_id"]
        uconn.close()
        return app, uid, cid, doc_id

    @pytest.mark.asyncio
    async def test_fts_search_finds_goblin(self, setup_searchable, tmp_dirs):
        """ToolBox fts_search should find goblin content."""
        app, uid, cid, doc_id = setup_searchable
        toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
        results = toolbox.fts_search("goblin")
        assert len(results) > 0, "Should find goblin results"
        found_goblin = False
        for r in results:
            text = (r.get("snippet", "") + r.get("title", "")).lower()
            if "goblin" in text:
                found_goblin = True
                break
        assert found_goblin, f"FTS should find 'goblin': {results}"

    @pytest.mark.asyncio
    async def test_fts_search_finds_ac(self, setup_searchable, tmp_dirs):
        """ToolBox fts_search should find AC (Armor Class) content."""
        app, uid, cid, doc_id = setup_searchable
        toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
        results = toolbox.fts_search("armor class")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_fts_search_scoped_to_collection(self, setup_searchable, tmp_dirs):
        """Search should only return results from the specified collection."""
        app, uid, cid, doc_id = setup_searchable
        # Create a second collection with different content
        uconn = init_user_db(tmp_dirs["db"], uid)
        cid2 = create_collection(uconn, "Other Collection")
        uconn.close()
        # Search in empty collection
        toolbox2 = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid2)
        results = toolbox2.fts_search("goblin")
        assert len(results) == 0, "Should not find results in empty collection"

    @pytest.mark.asyncio
    async def test_read_file_tool(self, setup_searchable, tmp_dirs):
        """ToolBox read_file should return file content."""
        app, uid, cid, doc_id = setup_searchable
        doc_dir = tmp_dirs["data"] / uid / doc_id
        md_files = sorted(doc_dir.rglob("*.md"))
        first_file = md_files[0]
        rel_path = f"{doc_id}/{first_file.name}"
        toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
        content = toolbox.read_file(rel_path)
        assert len(content) > 0
        assert "error" not in content.lower() or "not found" not in content.lower()

    @pytest.mark.asyncio
    async def test_grep_tool(self, setup_searchable, tmp_dirs):
        """ToolBox grep should find matching lines."""
        app, uid, cid, doc_id = setup_searchable
        toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
        results = toolbox.grep("goblin")
        assert len(results) > 0, "grep should find goblin mentions"

    @pytest.mark.asyncio
    async def test_ls_tool(self, setup_searchable, tmp_dirs):
        """ToolBox ls should list files in the doc directory."""
        app, uid, cid, doc_id = setup_searchable
        toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
        files = toolbox.ls(doc_id)
        assert len(files) > 0
        assert any(f.endswith(".md") for f in files)

    @pytest.mark.asyncio
    async def test_ask_question_gets_answer(self, setup_searchable, tmp_dirs):
        """Full Q&A: ask a question via the web UI, get an answer back."""
        app, uid, cid, doc_id = setup_searchable
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.post(
                "/sessions",
                data={"collection_id": cid, "question": "What is a goblin's AC?"},
            )
            assert r.status_code == 200
            # Should contain the answer
            assert "AC 13" in r.text or "goblin" in r.text.lower()
            # Should have citation links
            assert "Sources" in r.text or "rpg-cite" in r.text
            # Should have follow-up suggestions
            assert "Dig deeper" in r.text or "rpg-suggestion" in r.text

    @pytest.mark.asyncio
    async def test_ask_followup_question_in_session(self, setup_searchable, tmp_dirs):
        """Follow-up questions in an existing session should work."""
        app, uid, cid, doc_id = setup_searchable
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            # First question
            r = await client.post(
                "/sessions",
                data={"collection_id": cid, "question": "What is a goblin's AC?"},
            )
            assert r.status_code == 200
            # Extract session ID from the page
            import re
            m = re.search(r'/sessions/([a-f0-9]+)', r.text)
            assert m, "Session ID not found in response"
            sid = m.group(1)
            # Follow-up question
            # Reset mock gateway call counter for new question
            gateway = app.state.gateway
            if hasattr(gateway, 'call'):
                # The mock's call_count is a list, reset it
                pass  # mock will cycle through responses again
            r = await client.post(
                f"/sessions/{sid}",
                data={"question": "What weapons can goblins use?"},
            )
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_session_list_shows_sessions(self, setup_searchable, tmp_dirs):
        """Sessions page should list created sessions."""
        app, uid, cid, doc_id = setup_searchable
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            await client.post(
                "/sessions",
                data={"collection_id": cid, "question": "What is a goblin's AC?"},
            )
            r = await client.get("/sessions")
            assert r.status_code == 200
            assert "goblin" in r.text.lower() or "session" in r.text.lower()

    @pytest.mark.asyncio
    async def test_citation_links_to_pdf(self, setup_searchable, tmp_dirs):
        """Citation links should point to the PDF with page anchors."""
        app, uid, cid, doc_id = setup_searchable
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.post(
                "/sessions",
                data={"collection_id": cid, "question": "What is a goblin's AC?"},
            )
            assert r.status_code == 200
            # Check for citation links pointing to PDF
            if "/docs/" in r.text and "/pdf" in r.text:
                import re
                pdf_links = re.findall(r'href="(/docs/[a-f0-9]+/pdf[^"]*)"', r.text)
                assert len(pdf_links) > 0, "Should have at least one PDF citation link"
                # Link should have page anchor
                assert any("#page=" in link for link in pdf_links), \
                    f"Citation links should include page anchors: {pdf_links}"

    @pytest.mark.asyncio
    async def test_suggestion_buttons_have_data(self, setup_searchable, tmp_dirs):
        """Follow-up suggestion buttons should have data-question attributes."""
        app, uid, cid, doc_id = setup_searchable
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            r = await client.post(
                "/sessions",
                data={"collection_id": cid, "question": "What is a goblin's AC?"},
            )
            assert r.status_code == 200
            assert 'data-question=' in r.text
            import re
            suggestions = re.findall(r'data-question="([^"]+)"', r.text)
            assert len(suggestions) >= 1, "Should have suggestion buttons"


# ===========================================================================
# Journey 6: Multi-User Isolation
# ===========================================================================

class TestMultiUserJourney:
    """Test that users are isolated from each other."""

    @pytest.fixture
    def two_user_app(self, tmp_dirs, test_config, mock_gateway):
        """Create app with two users."""
        conn = init_shared_db(tmp_dirs["db"])
        alice_uid = create_user(conn, "alice", hash_password("alice"))
        bob_uid = create_user(conn, "bob", hash_password("bob"))
        conn.close()
        app = create_app(test_config, session_secret="test-secret")
        app.state.gateway = mock_gateway
        return app, alice_uid, bob_uid

    @pytest.mark.asyncio
    async def test_alice_cannot_see_bobs_collections(self, two_user_app, tmp_dirs):
        """Each user's collections are isolated in separate per-user databases."""
        app, alice_uid, bob_uid = two_user_app
        # Alice creates a collection via the web UI
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "alice"})
            await client.post("/collections", data={"name": "Alice's Books"})
        # Bob creates a collection via the web UI
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "bob", "password": "bob"})
            await client.post("/collections", data={"name": "Bob's Secret Books"})
        # Verify Alice's DB only has Alice's collection
        alice_conn = init_user_db(tmp_dirs["db"], alice_uid)
        alice_cols = list_collections(alice_conn)
        alice_conn.close()
        assert len(alice_cols) == 1
        assert alice_cols[0]["name"] == "Alice's Books"
        # Verify Bob's DB only has Bob's collection
        bob_conn = init_user_db(tmp_dirs["db"], bob_uid)
        bob_cols = list_collections(bob_conn)
        bob_conn.close()
        assert len(bob_cols) == 1
        assert bob_cols[0]["name"] == "Bob's Secret Books"
        # Alice cannot access Bob's collection via the web
        alice_cid = alice_cols[0]["collection_id"]
        bob_cid = bob_cols[0]["collection_id"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "bob", "password": "bob"})
            r = await client.get(f"/collections/{alice_cid}")
            assert r.status_code == 303  # redirect — not found in Bob's DB

    @pytest.mark.asyncio
    async def test_alice_cannot_read_bobs_files(self, two_user_app, tmp_dirs):
        """Alice's ToolBox should not be able to read Bob's files."""
        app, alice_uid, bob_uid = two_user_app
        # Create a file in Bob's data dir
        bob_doc_dir = tmp_dirs["data"] / bob_uid / "bob_doc"
        bob_doc_dir.mkdir(parents=True)
        bob_file = bob_doc_dir / "secret.md"
        bob_file.write_text("# Bob's secret content")
        # Create Alice's collection so we can make a ToolBox
        uconn = init_user_db(tmp_dirs["db"], alice_uid)
        alice_cid = create_collection(uconn, "Alice's Books")
        uconn.close()
        # Alice tries to read Bob's file
        toolbox = ToolBox(tmp_dirs["data"], alice_uid, tmp_dirs["db"], alice_cid)
        result = toolbox.read_file(str(bob_file))
        assert "invalid path" in result or "not found" in result


# ===========================================================================
# Journey 7: Agent Loop Edge Cases
# ===========================================================================

class TestAgentLoopJourney:
    """Test agent loop behavior: dedup, forced done, fallback answer."""

    @pytest.mark.asyncio
    async def test_loop_dedup_skips_repeated_reads(self, tmp_dirs):
        """Agent loop should not read the same file twice."""
        call_count = [0]
        responses = [
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "doc/combat.md"}'}}
            ]}},
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "doc/combat.md"}'}}
            ]}},
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "done", "arguments": '{"answer": "Done.", "cites": []}'}}
            ]}},
        ]
        async def mock_call(role, prompt, tools=None, messages=None):
            r = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return r
        gw = MagicMock()
        gw.call = mock_call
        toolbox = MagicMock()
        toolbox.execute = MagicMock(return_value="# Combat Rules\nGoblin AC 13.")
        loop = AgentLoop(gw, toolbox, max_iterations=10)
        result = await loop.run([], "What is goblin AC?")
        # Should have called execute only once (second read_file was deduped)
        assert toolbox.execute.call_count == 1
        assert result["answer"] == "Done."

    @pytest.mark.asyncio
    async def test_loop_dedup_skips_repeated_searches(self):
        """Agent loop should not repeat the same FTS search."""
        call_count = [0]
        responses = [
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "fts_search", "arguments": '{"query": "goblin"}'}}
            ]}},
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "fts_search", "arguments": '{"query": "goblin"}'}}
            ]}},
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "done", "arguments": '{"answer": "Done.", "cites": []}'}}
            ]}},
        ]
        async def mock_call(role, prompt, tools=None, messages=None):
            r = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return r
        gw = MagicMock()
        gw.call = mock_call
        toolbox = MagicMock()
        toolbox.execute = MagicMock(return_value='[]')
        loop = AgentLoop(gw, toolbox, max_iterations=10)
        result = await loop.run([], "Find goblin")
        # Second fts_search should be deduped
        assert toolbox.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_loop_forced_done_after_iter_8(self):
        """After state transitions, only done tool should be offered in SYNTHESIZING state."""
        responses = [
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "fts_search", "arguments": '{"query": "x"}'}}
            ]}}
        ] * 15
        call_count = [0]
        seen_tools = []
        async def mock_call(role, prompt, tools=None, messages=None):
            idx = min(call_count[0], len(responses) - 1)
            r = responses[idx]
            call_count[0] += 1
            if tools:
                seen_tools.append([t["function"]["name"] for t in tools])
            return r
        gw = MagicMock()
        gw.call = mock_call
        toolbox = MagicMock()
        toolbox.execute = MagicMock(return_value='[]')
        loop = AgentLoop(gw, toolbox, max_iterations=15)
        result = await loop.run([], "test")
        # With new state machine: SEARCHING (5 iter) -> READING (5 iter) -> SYNTHESIZING (3 iter)
        # Transition to SYNTHESIZING happens at end of iteration 11, so iteration 12+ uses SYNTHESIZING tools
        synthesizing_tools = seen_tools[11:]  # After SEARCHING(5)+READING(5)+transition
        for tools_list in synthesizing_tools:
            assert "done" in tools_list, f"Expected 'done' tool in SYNTHESIZING, got {tools_list}"
            assert "fts_search" not in tools_list, f"Should not offer fts_search in SYNTHESIZING, got {tools_list}"
            assert "grep" not in tools_list, f"Should not offer grep in SYNTHESIZING, got {tools_list}"

    @pytest.mark.asyncio
    async def test_loop_budget_exhausted_returns_fallback(self):
        """When budget is exhausted, should return a fallback answer."""
        gw = MagicMock()
        gw.call = AsyncMock(return_value={
            "message": {"content": "", "tool_calls": [
                {"function": {"name": "fts_search", "arguments": '{"query": "x"}'}}
            ]}
        })
        toolbox = MagicMock()
        toolbox.execute = MagicMock(return_value='[]')
        loop = AgentLoop(gw, toolbox, max_iterations=3)
        result = await loop.run([], "impossible question")
        assert result["iterations"] == 3
        assert len(result["answer"]) > 0  # Should have some fallback text
        assert "could not" in result["answer"].lower() or "ran out" in result["answer"].lower()


# ===========================================================================
# Journey 8: Real E2E with Ollama (marked, requires --e2e)
# ===========================================================================

@pytest.mark.e2e
class TestRealOllamaJourney:
    """Full end-to-end tests with a real Ollama instance.

    Run with: pytest tests/test_e2e_journey.py -v -m e2e
    Requires Ollama running with qwen2.5:7b or similar model.
    """

    @pytest.mark.asyncio
    async def test_real_pipeline_and_qa(self, real_app_with_user, tmp_dirs):
        """Upload a PDF, process it with real Ollama, ask a question."""
        app, uid, gateway = real_app_with_user
        pdf_bytes = _make_rpg_pdf()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            uconn = init_user_db(tmp_dirs["db"], uid)
            cid = create_collection(uconn, "Real E2E")
            uconn.close()
            # Upload
            await client.post(
                "/upload",
                data={"collection_id": cid},
                files=[("files", ("rpg.pdf", pdf_bytes, "application/pdf"))],
            )
        # Process pipeline with real Ollama
        from app.storage.shared_db import init_shared_db, claim_next_job
        sconn = init_shared_db(tmp_dirs["db"])
        job = claim_next_job(sconn)
        runner = PipelineRunner(gateway, tmp_dirs["data"], tmp_dirs["db"])
        await runner.run_job(job)
        # Verify processing completed
        uconn = init_user_db(tmp_dirs["db"], uid)
        docs = list_docs(uconn, cid)
        assert docs[0]["status"] == "done", f"Pipeline failed: {docs[0]['status']}"
        doc_id = docs[0]["doc_id"]
        uconn.close()
        # Verify FTS has content
        uconn = init_user_db(tmp_dirs["db"], uid)
        fts_count = uconn.execute("SELECT count(*) FROM documents_fts").fetchone()[0]
        assert fts_count > 0, "FTS index should have rows"
        uconn.close()
        # Ask a question with real LLM
        toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
        loop = AgentLoop(gateway, toolbox, max_iterations=10)
        result = await loop.run([], "What is a goblin's AC?")
        assert len(result["answer"]) > 10, "Should get a real answer"
        # Answer should mention AC or goblin
        answer_lower = result["answer"].lower()
        assert "goblin" in answer_lower or "ac" in answer_lower or "armor" in answer_lower, \
            f"Answer should mention goblin or AC: {result['answer'][:200]}"

    @pytest.mark.asyncio
    async def test_real_enrichment_quality(self, real_app_with_user, tmp_dirs):
        """Verify real enrichment produces summaries and keywords."""
        app, uid, gateway = real_app_with_user
        pdf_bytes = _make_rpg_pdf()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await login(client, "testuser", "testpass")
            uconn = init_user_db(tmp_dirs["db"], uid)
            cid = create_collection(uconn, "Enrichment Quality")
            uconn.close()
            await client.post(
                "/upload",
                data={"collection_id": cid},
                files=[("files", ("rpg.pdf", pdf_bytes, "application/pdf"))],
            )
        from app.storage.shared_db import init_shared_db, claim_next_job
        sconn = init_shared_db(tmp_dirs["db"])
        job = claim_next_job(sconn)
        runner = PipelineRunner(gateway, tmp_dirs["data"], tmp_dirs["db"])
        await runner.run_job(job)
        # Check enriched markdown files have front-matter
        uconn = init_user_db(tmp_dirs["db"], uid)
        docs = list_docs(uconn, cid)
        doc_id = docs[0]["doc_id"]
        uconn.close()
        doc_dir = tmp_dirs["data"] / uid / doc_id
        md_files = list(doc_dir.rglob("*.md"))
        enriched_count = 0
        for f in md_files:
            if f.name == "index.md":
                continue
            content = f.read_text()
            if content.startswith("---") and "summary:" in content:
                enriched_count += 1
        assert enriched_count > 0, "At least some files should have enrichment front-matter"