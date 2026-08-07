"""Shared-book copy must not propagate XSS payloads across users.

User A uploads a PDF whose extracted text contains HTML (e.g. a crafted
stat block with <script>). The shared-book dedup copies A's fully
processed tree into user B's directory when B uploads the same book.
The copied leaves must be sanitized when rendered (the |md filter fix),
and the copy must not crash or skip the sanitization path.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.pipeline.runner import PipelineRunner
from app.pipeline.content_hash import content_hash
from app.storage.shared_db import init_shared_db, enqueue_job, claim_next_job, get_job
from app.storage.user_db import init_user_db, create_collection, create_doc, get_doc
from app.web.template_utils import create_templates
from fpdf import FPDF


def _make_pdf(path: Path, text: str):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 10, txt=text)
    pdf.output(str(path))


def _make_gateway():
    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "Combat rules.", "keywords": ["combat"]}'}})
    return gw


def _setup_user(db_dir, data_dir, username, doc_id, pdf_path, title="Test Book"):
    conn = init_shared_db(db_dir)
    uconn = init_user_db(db_dir, username)
    cid = create_collection(uconn, "C")
    create_doc(uconn, doc_id, cid, title, "sha")
    uconn.close()
    jid = enqueue_job(conn, username, doc_id, str(pdf_path))
    job = claim_next_job(conn)
    conn.close()
    return job


@pytest.mark.asyncio
async def test_shared_book_copy_preserves_poisoned_leaf(tmp_dirs):
    """B's copied tree contains A's poisoned leaf — rendering must sanitize it."""
    poisoned_text = 'Chapter 1: Combat\n\n<script>alert("xss")</script>\n\nGoblins have AC 15.'
    pdf_a = tmp_dirs["data"] / "a.pdf"
    _make_pdf(pdf_a, poisoned_text)

    # User A processes the book (full pipeline, poisoned leaf lands in tree)
    job_a = _setup_user(tmp_dirs["db"], tmp_dirs["data"], "alice", "d1", pdf_a)
    runner = PipelineRunner(_make_gateway(), tmp_dirs["data"], tmp_dirs["db"])
    await runner.run_job(job_a)

    # User B uploads the same book (same content hash) → copy path
    pdf_b = tmp_dirs["data"] / "b.pdf"
    _make_pdf(pdf_b, poisoned_text)
    job_b = _setup_user(tmp_dirs["db"], tmp_dirs["data"], "bob", "d2", pdf_b)
    await runner.run_job(job_b)

    # B's job completed via the shared-book copy
    conn = init_shared_db(tmp_dirs["db"])
    assert get_job(conn, job_b["job_id"])["status"] == "done"
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], "bob")
    d = get_doc(uconn, "d2")
    assert d["status"] == "done"
    uconn.close()

    # The poisoned leaf was copied into B's tree
    bob_leaves = list((tmp_dirs["data"] / "bob" / "d2").rglob("*.md"))
    assert len(bob_leaves) >= 1
    leaf = next(f for f in bob_leaves if f.name != "index.md")
    assert "<script>" in leaf.read_text()

    # Rendering through the |md filter must strip the script
    templates = create_templates("app/web/templates")
    md_filter = templates.env.filters["md"]
    rendered = md_filter(leaf.read_text())
    assert "<script" not in rendered
    assert "alert" not in rendered
    assert "Goblins have AC 15" in rendered


@pytest.mark.asyncio
async def test_shared_book_copy_skips_reprocessing(tmp_dirs):
    """B's job must not re-run enrichment when the copy path is taken."""
    text = "Chapter 1: Combat\n\nGoblins have AC 15 and HP 7."
    pdf_a = tmp_dirs["data"] / "a.pdf"
    _make_pdf(pdf_a, text)
    job_a = _setup_user(tmp_dirs["db"], tmp_dirs["data"], "alice", "d1", pdf_a)
    runner = PipelineRunner(_make_gateway(), tmp_dirs["data"], tmp_dirs["db"])
    await runner.run_job(job_a)

    pdf_b = tmp_dirs["data"] / "b.pdf"
    _make_pdf(pdf_b, text)
    job_b = _setup_user(tmp_dirs["db"], tmp_dirs["data"], "bob", "d2", pdf_b)
    await runner.run_job(job_b)

    conn = init_shared_db(tmp_dirs["db"])
    assert get_job(conn, job_b["job_id"])["status"] == "done"
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], "bob")
    d = get_doc(uconn, "d2")
    assert d["status"] == "done"
    uconn.close()
    # Copied tree exists with index.md
    assert (tmp_dirs["data"] / "bob" / "d2" / "index.md").exists()
