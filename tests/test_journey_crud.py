"""Journey test: collection + doc CRUD lifecycle + reprocess flow.

Create collection → upload doc → view doc tree → reprocess → delete doc → verify gone.
"""
import pytest
import io
import shutil
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user, enqueue_job, claim_next_job
from app.storage.user_db import (
    init_user_db, create_collection, create_doc, list_docs, get_doc,
    update_doc_status, insert_fts_row,
)
from app.auth.passwords import hash_password
from app.pipeline.runner import PipelineRunner
from fpdf import FPDF


def _make_pdf(text: str) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, text=text)
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


@pytest.fixture
def app_with_user(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    cfg = Config("http://localhost:11434", {"query": "m", "enrich": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    app = create_app(cfg, session_secret="s")
    return app, uid


@pytest.mark.asyncio
async def test_collection_lifecycle(app_with_user, tmp_dirs):
    """Create collection via web → upload doc → process → delete doc via web."""
    app, uid = app_with_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})

        r = await client.post("/collections", data={"name": "My Bestiary"})
        assert r.status_code == 303
        r = await client.get("/")
        assert "My Bestiary" in r.text

        uconn = init_user_db(tmp_dirs["db"], uid)
        cols = uconn.execute("SELECT collection_id FROM collections WHERE name = 'My Bestiary'").fetchone()
        cid = cols["collection_id"]
        uconn.close()

        pdf_bytes = _make_pdf("Chapter 1: Monsters\nGoblins have AC 15.")
        r = await client.post(
            "/upload",
            data={"collection_id": cid},
            files=[("files", ("book.pdf", pdf_bytes, "application/pdf"))],
        )
        assert r.status_code == 303

        uconn = init_user_db(tmp_dirs["db"], uid)
        docs = list_docs(uconn, cid)
        assert len(docs) == 1
        doc_id = docs[0]["doc_id"]
        uconn.close()

        sconn = init_shared_db(tmp_dirs["db"])
        job = claim_next_job(sconn)
        sconn.close()

        gw = MagicMock()
        gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "Monster rules.", "keywords": ["monster"]}'}})
        runner = PipelineRunner(gw, tmp_dirs["data"], tmp_dirs["db"])
        await runner.run_job(job)

        r = await client.get(f"/docs/{doc_id}")
        assert r.status_code == 200
        assert "book" in r.text.lower()

        r = await client.post(f"/docs/{doc_id}/delete")
        assert r.status_code == 303

        uconn = init_user_db(tmp_dirs["db"], uid)
        assert get_doc(uconn, doc_id) is None, "doc should be deleted from DB"
        uconn.close()

        doc_dir = tmp_dirs["data"] / uid / doc_id
        assert not doc_dir.exists(), "doc directory should be removed"


@pytest.mark.asyncio
async def test_reprocess_flow(app_with_user, tmp_dirs):
    """Upload → process → reprocess → verify doc re-queued and re-processed."""
    app, uid = app_with_user

    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "Repro Test")
    doc_id = "repro_doc"
    create_doc(uconn, doc_id, cid, "Repro Book", "sha")
    update_doc_status(uconn, doc_id, "done")
    uconn.close()

    doc_dir = tmp_dirs["data"] / uid / doc_id
    doc_dir.mkdir(parents=True)
    pdf_path = doc_dir / "original.pdf"
    pdf_path.write_bytes(_make_pdf("Chapter 1: Combat\nGoblins have AC 15."))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})

        r = await client.post(f"/docs/{doc_id}/reprocess")
        assert r.status_code == 303

    uconn = init_user_db(tmp_dirs["db"], uid)
    d = get_doc(uconn, doc_id)
    assert d["status"] == "queued", "doc should be re-queued"
    uconn.close()

    sconn = init_shared_db(tmp_dirs["db"])
    job = claim_next_job(sconn)
    assert job is not None
    assert job["doc_id"] == doc_id
    sconn.close()

    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "Reprocessed.", "keywords": ["combat"]}'}})
    runner = PipelineRunner(gw, tmp_dirs["data"], tmp_dirs["db"])
    await runner.run_job(job)

    uconn = init_user_db(tmp_dirs["db"], uid)
    d = get_doc(uconn, doc_id)
    assert d["status"] == "done", "doc should be done after reprocessing"
    uconn.close()