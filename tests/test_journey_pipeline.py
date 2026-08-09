"""Journey test: full PDF processing pipeline end-to-end.

Upload a real PDF → queue job → run pipeline (mocked LLM for enrich) →
verify tiered markdown files exist → verify FTS5 index has rows →
run fts_search via ToolBox and get results.
"""
import pytest
import io
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection, list_docs, get_doc
from app.storage.paths import user_db_path
from app.agent.tools import ToolBox
from app.pipeline.runner import PipelineRunner
from app.storage.shared_db import enqueue_job, claim_next_job, get_job
from app.auth.passwords import hash_password
from fpdf import FPDF


def _make_pdf(pages: list[str]) -> bytes:
    pdf = FPDF()
    for text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, text=text)
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


@pytest.fixture
def app_and_user(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    app = create_app(test_config, session_secret="s")
    return app, uid


@pytest.mark.asyncio
async def test_full_pdf_processing_pipeline(app_and_user, tmp_dirs):
    """Upload a PDF, run the pipeline, verify files + FTS index."""
    app, uid = app_and_user

    pdf_bytes = _make_pdf([
        "Chapter 1: Combat",
        "Goblins have AC 15 and HP 7. They are small humanoids.",
    ])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw123"})
        cid = None
        uconn = init_user_db(tmp_dirs["db"], uid)
        cid = create_collection(uconn, "Bestiary")
        uconn.close()

        r = await client.post(
            "/upload",
            data={"collection_id": cid},
            files=[("files", ("combat.pdf", pdf_bytes, "application/pdf"))],
        )
        assert r.status_code == 303

    uconn = init_user_db(tmp_dirs["db"], uid)
    docs = list_docs(uconn, cid)
    assert len(docs) == 1
    doc_id = docs[0]["doc_id"]
    assert docs[0]["status"] == "queued"
    uconn.close()

    sconn = init_shared_db(tmp_dirs["db"])
    job = claim_next_job(sconn)
    assert job is not None
    assert job["doc_id"] == doc_id
    sconn.close()

    gw = MagicMock()
    gw.call = AsyncMock(return_value={
        "message": {"content": '{"summary": "Combat rules for goblins.", "keywords": ["goblin", "combat", "AC"]}'}
    })
    runner = PipelineRunner(gw, tmp_dirs["data"], tmp_dirs["db"])
    await runner.run_job(job)

    uconn = init_user_db(tmp_dirs["db"], uid)
    d = get_doc(uconn, doc_id)
    assert d["status"] == "done"
    uconn.close()

    sconn = init_shared_db(tmp_dirs["db"])
    assert get_job(sconn, job["job_id"])["status"] == "done"
    sconn.close()

    doc_index = tmp_dirs["data"] / uid / doc_id / "index.md"
    assert doc_index.exists(), "doc index.md should exist"
    assert "Bestiary" in doc_index.read_text() or "Chapter" in doc_index.read_text()

    uconn = init_user_db(tmp_dirs["db"], uid)
    rows = uconn.execute("SELECT path, title FROM documents_fts").fetchall()
    assert len(rows) >= 1, "FTS index should have at least one row"
    uconn.close()

    toolbox = ToolBox(tmp_dirs["data"], uid, tmp_dirs["db"], cid)
    results = toolbox.fts_search("goblin")
    assert len(results) >= 1, "fts_search should find the goblin content"
    assert "goblin" in results[0].get("snippet", "").lower() or "goblin" in results[0].get("content", "").lower(), \
        f"FTS result should contain 'goblin': {results[0]}"


@pytest.mark.asyncio
async def test_upload_oversized_file_skipped(app_and_user, tmp_dirs, monkeypatch):
    """Files over the upload cap are silently skipped."""
    import app.web.routes as routes
    monkeypatch.setattr(routes, "max_upload_bytes", lambda: 1024)
    app, uid = app_and_user

    oversized = b"\x00" * 2048
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "Test")
    uconn.close()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw123"})
        r = await client.post(
            "/upload",
            data={"collection_id": cid},
            files=[("files", ("huge.pdf", oversized, "application/pdf"))],
        )
        assert r.status_code == 303

    uconn = init_user_db(tmp_dirs["db"], uid)
    docs = list_docs(uconn, cid)
    assert len(docs) == 0, "oversized file should not be enqueued"
    uconn.close()