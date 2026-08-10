"""Shared collection write path — member upload lands in owner's tree/DB."""

import io
import pytest
from httpx import AsyncClient, ASGITransport
from app.storage.shared_db import init_shared_db, add_collection_member
from app.storage.user_db import init_user_db, create_collection, list_docs
from tests.conftest import csrf_for, login
from fpdf import FPDF


def _make_pdf(text="Chapter 1: Test"):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, txt=text)
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


@pytest.fixture
def shared_setup(shared_collection_fixture, tmp_dirs):
    """alice owns an empty shared collection; bob is member; eve is nobody."""
    app, alice, bob, eve, _cid = shared_collection_fixture
    uconn = init_user_db(tmp_dirs["db"], alice)
    cid = create_collection(uconn, "Upload Shelf")
    uconn.close()
    conn = init_shared_db(tmp_dirs["db"])
    add_collection_member(conn, cid, alice, "owner")
    add_collection_member(conn, cid, bob, "member")
    conn.close()
    return app, alice, bob, eve, cid


@pytest.mark.asyncio
async def test_member_upload_lands_in_owner_tree(shared_setup, tmp_dirs):
    app, alice, bob, _eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.post(
            "/upload",
            data={"collection_id": cid, "_csrf": csrf_for(client)},
            files=[("files", ("book.pdf", _make_pdf(), "application/pdf"))],
        )
        assert r.status_code in (200, 303)
    # Owner's DB has the doc; file exists in owner's tree
    uconn = init_user_db(app.state.config.db_dir, alice)
    docs = list_docs(uconn, cid)
    uconn.close()
    assert len(docs) == 1, "member upload must create the doc in the owner's DB"
    owner_dir = tmp_dirs["data"] / alice / docs[0]["doc_id"]
    assert (owner_dir / "original.pdf").exists(), "file must land in owner's tree"
    # Bob's DB has nothing
    bconn = init_user_db(app.state.config.db_dir, bob)
    bdocs = list_docs(bconn, cid)
    bconn.close()
    assert len(bdocs) == 0


@pytest.mark.asyncio
async def test_non_member_upload_rejected(shared_setup):
    app, alice, bob, eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "eve", password="pw123456")
        r = await client.post(
            "/upload",
            data={"collection_id": cid, "_csrf": csrf_for(client)},
            files=[("files", ("book.pdf", _make_pdf(), "application/pdf"))],
        )
        assert r.status_code in (200, 303)
    uconn = init_user_db(app.state.config.db_dir, alice)
    docs = list_docs(uconn, cid)
    uconn.close()
    assert len(docs) == 0, "non-member upload must not create docs"


@pytest.mark.asyncio
async def test_member_cannot_delete_owner_doc(shared_setup, tmp_dirs):
    """Owner-only delete: a member trying to delete the owner's doc is blocked."""
    from app.storage.user_db import create_doc, update_doc_status
    from app.storage.shared_db import unlink_user_book
    app, alice, bob, _eve, cid = shared_setup
    uconn = init_user_db(app.state.config.db_dir, alice)
    create_doc(uconn, "deldoc123", cid, "Doomed Book", "sha")
    update_doc_status(uconn, "deldoc123", "done")
    uconn.close()
    doc_dir = tmp_dirs["data"] / alice / "deldoc123"
    doc_dir.mkdir(parents=True)
    (doc_dir / "original.pdf").write_bytes(b"%PDF-1.4")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.post(f"/docs/deldoc123/delete", data={"_csrf": csrf_for(client)})
        assert r.status_code in (200, 303)
    # Doc must still exist
    uconn = init_user_db(app.state.config.db_dir, alice)
    docs = list_docs(uconn, cid)
    uconn.close()
    assert any(d["doc_id"] == "deldoc123" for d in docs), "member delete must be blocked"
    assert (tmp_dirs["data"] / alice / "deldoc123" / "original.pdf").exists()


@pytest.mark.asyncio
async def test_owner_can_delete_own_doc(shared_setup, tmp_dirs):
    """The owner can still delete docs in their collection."""
    from app.storage.user_db import create_doc, update_doc_status
    app, alice, bob, _eve, cid = shared_setup
    uconn = init_user_db(app.state.config.db_dir, alice)
    create_doc(uconn, "owndoc123", cid, "Owned Book", "sha")
    update_doc_status(uconn, "owndoc123", "done")
    uconn.close()
    doc_dir = tmp_dirs["data"] / alice / "owndoc123"
    doc_dir.mkdir(parents=True)
    (doc_dir / "original.pdf").write_bytes(b"%PDF-1.4")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "alice", password="pw123456")
        r = await client.post(f"/docs/owndoc123/delete", data={"_csrf": csrf_for(client)})
        assert r.status_code in (200, 303)
    uconn = init_user_db(app.state.config.db_dir, alice)
    docs = list_docs(uconn, cid)
    uconn.close()
    assert not any(d["doc_id"] == "owndoc123" for d in docs), "owner delete must work"


@pytest.mark.asyncio
async def test_empty_pdf_upload_creates_no_doc(shared_setup, tmp_dirs):
    """An empty .pdf (no magic bytes) must be rejected — no doc created."""
    app, alice, bob, _eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        r = await client.post(
            "/upload",
            data={"collection_id": cid, "_csrf": csrf_for(client)},
            files=[("files", ("empty.pdf", b"", "application/pdf"))],
        )
        assert r.status_code in (200, 303)
    uconn = init_user_db(app.state.config.db_dir, alice)
    docs = list_docs(uconn, cid)
    uconn.close()
    assert len(docs) == 0, "empty PDF must not create a doc"


@pytest.mark.asyncio
async def test_concurrent_uploads_all_land(shared_setup, tmp_dirs):
    """Multiple simultaneous uploads must all create docs in the owner tree."""
    import asyncio
    app, alice, bob, _eve, cid = shared_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "bob", password="pw123456")
        async def _upload(i):
            return await client.post(
                "/upload",
                data={"collection_id": cid, "_csrf": csrf_for(client)},
                files=[("files", (f"book{i}.pdf", _make_pdf(f"Chapter {i}"), "application/pdf"))],
            )
        results = await asyncio.gather(*[_upload(i) for i in range(5)])
        for r in results:
            assert r.status_code in (200, 303)
    uconn = init_user_db(app.state.config.db_dir, alice)
    docs = list_docs(uconn, cid)
    uconn.close()
    assert len(docs) == 5, "all concurrent uploads must create docs"
