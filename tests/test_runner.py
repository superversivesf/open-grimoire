import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.pipeline.runner import PipelineRunner
from app.storage.shared_db import init_shared_db, enqueue_job, claim_next_job, get_job
from app.storage.user_db import init_user_db, create_collection, create_doc, get_doc
from fpdf import FPDF


def _make_pdf(path: Path):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, txt="Chapter 1: Combat")
    pdf.ln(20)
    pdf.cell(0, 10, txt="Goblins have AC 15 and HP 7.")
    pdf.output(str(path))


@pytest.mark.asyncio
async def test_runner_end_to_end(tmp_dirs):
    pdf_path = tmp_dirs["data"] / "test.pdf"
    _make_pdf(pdf_path)
    conn = init_shared_db(tmp_dirs["db"])
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Test Book", "sha123")
    uconn.close()
    jid = enqueue_job(conn, "alice", "d1", str(pdf_path))
    job = claim_next_job(conn)
    conn.close()

    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "Combat rules.", "keywords": ["combat", "goblin"]}'}})
    runner = PipelineRunner(gw, tmp_dirs["data"], tmp_dirs["db"])
    await runner.run_job(job)

    conn = init_shared_db(tmp_dirs["db"])
    assert get_job(conn, jid)["status"] == "done"
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], "alice")
    d = get_doc(uconn, "d1")
    assert d["status"] == "done"
    assert (tmp_dirs["data"] / "alice" / "d1" / "index.md").exists()
    uconn.close()


@pytest.mark.asyncio
async def test_runner_marks_failed_on_bad_pdf(tmp_dirs):
    bad_pdf = tmp_dirs["data"] / "bad.pdf"
    bad_pdf.write_bytes(b"not a pdf")
    conn = init_shared_db(tmp_dirs["db"])
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Bad", "h")
    uconn.close()
    jid = enqueue_job(conn, "alice", "d1", str(bad_pdf))
    job = claim_next_job(conn)
    conn.close()

    runner = PipelineRunner(gateway=None, data_dir=tmp_dirs["data"], db_dir=tmp_dirs["db"])
    await runner.run_job(job)

    conn = init_shared_db(tmp_dirs["db"])
    assert get_job(conn, jid)["status"] == "failed"
    assert get_job(conn, jid)["error"] is not None
    conn.close()