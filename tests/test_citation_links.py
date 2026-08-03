"""Tests for citation link resolution.

Tests the full chain:
1. FTS path format (doc_id/filename)
2. read_file with FTS path
3. doc_view_leaf route with extracted doc_id and file_path
4. doc_search route with bare filename
5. Template path extraction logic
"""
import pytest
import sys
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import (
    init_user_db, create_collection, create_doc, insert_fts_row,
    update_doc_status, list_docs, get_doc,
)
from app.auth.passwords import hash_password
from app.agent.sandbox import safe_read_file
from app.storage.paths import validate_user_path
import tempfile
import shutil


# Path format constants used across tests
DOC_ID = "02147971b88448588080ec3743838978"
FILENAME = "70_security_clearances.md"
FTS_PATH = f"{DOC_ID}/{FILENAME}"  # This is what FTS stores and LLM returns
FILE_CONTENT = "# Security Clearances\n\nAlpha Complex enforces a strict hierarchy."


@pytest.fixture
def app_with_doc(tmp_dirs):
    """Set up an app with a user, collection, doc, and FTS row."""
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()

    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "Test")
    create_doc(uconn, DOC_ID, cid, "Test Book", "sha123")
    update_doc_status(uconn, DOC_ID, "done")
    insert_fts_row(uconn, FTS_PATH, "SECURITY CLEARANCES", "Clearance summary", "clearance,security", FILE_CONTENT)
    uconn.close()

    # Create the actual file on disk at data/{uid}/{doc_id}/{filename}
    doc_dir = tmp_dirs["data"] / uid / DOC_ID
    doc_dir.mkdir(parents=True, exist_ok=True)
    (doc_dir / FILENAME).write_text(FILE_CONTENT)

    cfg = Config("http://localhost:11434", {"query": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    app = create_app(cfg, "s")
    return app, uid, cid


class TestFtsPathFormat:
    """Test that FTS paths are in the expected format: doc_id/filename"""

    def test_fts_path_has_doc_id_prefix(self, app_with_doc, tmp_dirs):
        app, uid, cid = app_with_doc
        uconn = init_user_db(tmp_dirs["db"], uid)
        rows = uconn.execute("SELECT path FROM documents_fts").fetchall()
        assert len(rows) > 0
        path = rows[0]["path"]
        parts = path.split("/")
        assert len(parts) == 2, f"Expected doc_id/filename, got: {path}"
        assert len(parts[0]) == 32, f"Expected 32-char doc_id, got: {parts[0]}"
        assert parts[1].endswith(".md"), f"Expected .md file, got: {parts[1]}"
        uconn.close()


class TestReadFileWithFtsPath:
    """Test that read_file works with FTS-style paths (doc_id/filename)"""

    def test_read_file_with_fts_path(self, app_with_doc, tmp_dirs):
        app, uid, cid = app_with_doc
        from app.agent.tools import ToolBox
        toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
        # The LLM passes the FTS path to read_file
        result = toolbox.read_file(FTS_PATH)
        assert "Security Clearances" in result, f"Expected content, got: {result[:100]}"

    def test_read_file_with_absolute_fts_path(self, app_with_doc, tmp_dirs):
        """The LLM might pass an absolute path or a path with data/ prefix."""
        app, uid, cid = app_with_doc
        from app.agent.tools import ToolBox
        toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
        abs_path = str(tmp_dirs["data"] / uid / FTS_PATH)
        result = toolbox.read_file(abs_path)
        assert "Security Clearances" in result, f"Expected content, got: {result[:100]}"


class TestDocViewRoute:
    """Test the /docs/{doc_id}/view route with various path formats"""

    @pytest.mark.asyncio
    async def test_view_with_stripped_path(self, app_with_doc, tmp_dirs):
        """Link format: /docs/{doc_id}/view?path={filename} (doc_id stripped from path)"""
        app, uid, cid = app_with_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            r = await client.get(f"/docs/{DOC_ID}/view?path={FILENAME}")
            assert r.status_code == 200
            assert "Security Clearances" in r.text

    @pytest.mark.asyncio
    async def test_view_with_full_fts_path(self, app_with_doc, tmp_dirs):
        """Link format: /docs/{doc_id}/view?path={doc_id}/{filename} (full FTS path)"""
        app, uid, cid = app_with_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            # The route should strip the doc_id prefix
            r = await client.get(f"/docs/{DOC_ID}/view?path={FTS_PATH}")
            assert r.status_code == 200
            assert "Security Clearances" in r.text

    @pytest.mark.asyncio
    async def test_view_with_wrong_doc_id(self, app_with_doc, tmp_dirs):
        """If the doc_id in the URL doesn't match, shows file not found."""
        app, uid, cid = app_with_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            r = await client.get(f"/docs/wrongdocid1234567890123456789012/view?path={FILENAME}")
            # Route returns 303 if doc not found in DB, 200 if found but file missing
            assert r.status_code in (200, 303)

    @pytest.mark.asyncio
    async def test_view_nonexistent_file(self, app_with_doc, tmp_dirs):
        """Nonexistent file should show '(file not found)'."""
        app, uid, cid = app_with_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            r = await client.get(f"/docs/{DOC_ID}/view?path=nonexistent.md")
            assert r.status_code == 200
            assert "file not found" in r.text


class TestDocSearchRoute:
    """Test the /docs/search route that finds a doc by filename"""

    @pytest.mark.asyncio
    async def test_search_by_filename(self, app_with_doc, tmp_dirs):
        """Search by bare filename should redirect to the correct doc."""
        app, uid, cid = app_with_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            r = await client.get(f"/docs/search?path={FILENAME}", follow_redirects=False)
            assert r.status_code == 303
            assert DOC_ID in r.headers["location"]
            assert FILENAME in r.headers["location"]

    @pytest.mark.asyncio
    async def test_search_by_fts_path(self, app_with_doc, tmp_dirs):
        """Search by full FTS path should also work."""
        app, uid, cid = app_with_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            r = await client.get(f"/docs/search?path={FTS_PATH}", follow_redirects=False)
            assert r.status_code == 303
            assert DOC_ID in r.headers["location"]

    @pytest.mark.asyncio
    async def test_search_nonexistent(self, app_with_doc, tmp_dirs):
        """Search for nonexistent file redirects to home."""
        app, uid, cid = app_with_doc
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/login", data={"username": "alice", "password": "pw"})
            r = await client.get("/docs/search?path=nonexistent.md", follow_redirects=False)
            assert r.status_code == 303


class TestTemplatePathExtraction:
    """Test the Jinja2 template logic that extracts doc_id from citation paths."""

    def test_extract_doc_id_from_citation(self, app_with_doc):
        """The template splits the path and checks if the first part is 32 chars."""
        path = FTS_PATH  # "02147971b88448588080ec3743838978/70_security_clearances.md"
        parts = path.split("/")
        doc_id = parts[0] if len(parts[0]) == 32 else ""
        file_path = "/".join(parts[1:]) if doc_id else path

        assert doc_id == DOC_ID
        assert file_path == FILENAME
        assert len(doc_id) == 32

    def test_no_doc_id_in_path(self):
        """If the path doesn't start with a 32-char hex, doc_id is empty."""
        path = "70_security_clearances.md"
        parts = path.split("/")
        doc_id = parts[0] if len(parts) > 1 and len(parts[0]) == 32 else ""
        file_path = "/".join(parts[1:]) if doc_id else path

        assert doc_id == ""
        assert file_path == path

    def test_built_link_url(self):
        """Test that the link URL is built correctly."""
        path = FTS_PATH
        parts = path.split("/")
        doc_id = parts[0] if len(parts[0]) == 32 else ""
        file_path = "/".join(parts[1:]) if doc_id else path

        if doc_id:
            url = f"/docs/{doc_id}/view?path={file_path}"
        else:
            url = f"/docs/search?path={path}"

        assert url == f"/docs/{DOC_ID}/view?path={FILENAME}"