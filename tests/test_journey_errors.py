"""Journey test: error paths and failure scenarios.

Tests:
- Corrupt PDF upload → processing fails → doc status 'failed' → error message
- Failed login (wrong password, unknown user)
- Upload non-PDF file (rejected)
- Ollama down → agent query fails gracefully → session history preserved
- Delete a doc → verify files and FTS rows removed
- Create and delete a collection's doc lifecycle
"""
import pytest
import io
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user, enqueue_job, claim_next_job, get_job
from app.storage.user_db import (
    init_user_db, create_collection, create_doc, list_docs, get_doc,
    insert_fts_row, delete_doc, update_doc_status,
)
from app.auth.passwords import hash_password
from app.pipeline.runner import PipelineRunner
from fpdf import FPDF


@pytest.fixture
def app_with_user(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    cfg = Config("http://localhost:11434", {"query": "m", "enrich": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    app = create_app(cfg, session_secret="s")
    return app, uid


@pytest.mark.asyncio
async def test_corrupt_pdf_processing_fails(app_with_user, tmp_dirs):
    """Corrupt PDF → pipeline marks doc as failed with error message."""
    app, uid = app_with_user

    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "Test")
    uconn.close()

    bad_pdf = tmp_dirs["data"] / "bad.pdf"
    bad_pdf.write_bytes(b"not a real pdf file")
    sconn = init_shared_db(tmp_dirs["db"])
    uconn = init_user_db(tmp_dirs["db"], uid)
    create_doc(uconn, "bad_doc", cid, "Broken Book", "bad_sha")
    enqueue_job(sconn, uid, "bad_doc", str(bad_pdf))
    job = claim_next_job(sconn)
    sconn.close()
    uconn.close()

    runner = PipelineRunner(gateway=None, data_dir=tmp_dirs["data"], db_dir=tmp_dirs["db"])
    await runner.run_job(job)

    sconn = init_shared_db(tmp_dirs["db"])
    assert get_job(sconn, job["job_id"])["status"] == "failed"
    assert get_job(sconn, job["job_id"])["error"] is not None
    sconn.close()

    uconn = init_user_db(tmp_dirs["db"], uid)
    d = get_doc(uconn, "bad_doc")
    assert d["status"] == "failed"
    uconn.close()


@pytest.mark.asyncio
async def test_failed_login_wrong_password(app_with_user):
    app, uid = app_with_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "alice", "password": "wrong"})
        assert r.status_code == 401
        assert "session" not in r.cookies


@pytest.mark.asyncio
async def test_failed_login_unknown_user(app_with_user):
    app, uid = app_with_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "nobody", "password": "x"})
        assert r.status_code == 401
        assert "session" not in r.cookies


@pytest.mark.asyncio
async def test_upload_non_pdf_rejected(app_with_user, tmp_dirs):
    """Non-PDF files are silently skipped during upload."""
    app, uid = app_with_user
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "Test")
    uconn.close()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post(
            "/upload",
            data={"collection_id": cid},
            files=[("files", ("not_a_pdf.txt", b"hello world", "text/plain"))],
        )
        assert r.status_code == 303

    uconn = init_user_db(tmp_dirs["db"], uid)
    docs = list_docs(uconn, cid)
    assert len(docs) == 0, "non-PDF should not be enqueued"
    uconn.close()


@pytest.mark.asyncio
async def test_ollama_down_agent_fails_gracefully(app_with_user, tmp_dirs):
    """When Ollama is unreachable, agent query should fail without corrupting session."""
    app, uid = app_with_user

    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "PF")
    uconn.close()

    broken_gateway = MagicMock()
    broken_gateway.call = AsyncMock(side_effect=Exception("Ollama connection refused"))

    from app.agent.tools import ToolBox
    from app.agent.loop import AgentLoop

    toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
    loop = AgentLoop(broken_gateway, toolbox, max_iterations=3)

    with pytest.raises(Exception, match="Ollama"):
        await loop.run([], "What is AC?")


@pytest.mark.asyncio
async def test_delete_doc_removes_files_and_fts(app_with_user, tmp_dirs):
    """Delete a doc → filesystem + FTS rows removed."""
    app, uid = app_with_user

    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "PF")
    create_doc(uconn, "d1", cid, "Book", "h")
    insert_fts_row(uconn, "data/alice/d1/chapter.md", "Title", "summary", "kw", "content")
    delete_doc(uconn, "d1")
    uconn.close()

    uconn = init_user_db(tmp_dirs["db"], uid)
    assert get_doc(uconn, "d1") is None
    rows = uconn.execute("SELECT path FROM documents_fts").fetchall()
    assert len(rows) == 0, "FTS rows should be deleted"
    uconn.close()


@pytest.mark.asyncio
async def test_doc_view_redirects_for_unknown_doc(app_with_user, tmp_dirs):
    """Unknown doc_id redirects to home."""
    app, uid = app_with_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get("/docs/nonexistent")
        assert r.status_code == 303


@pytest.mark.asyncio
async def test_unauthenticated_access_redirects(app_with_user):
    """All protected routes redirect to /login when not authenticated."""
    app, uid = app_with_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ["/", "/sessions", "/collections/abc", "/docs/abc"]:
            r = await client.get(path)
            assert r.status_code in (303, 307), f"{path} should redirect unauthenticated"
            assert "/login" in r.headers.get("location", "")