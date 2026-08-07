import pytest
import io
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection, list_collections, list_docs
from app.auth.passwords import hash_password


@pytest.fixture
def app_and_user(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    # Key user db by the real user_id (uuid) the app uses via current_user_id(),
    # not the username, so the app and test share the same db file.
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "PF")
    uconn.close()
    app = create_app(test_config, session_secret="s")
    return app, cid, uid


@pytest.mark.asyncio
async def test_upload_single_pdf(app_and_user):
    app, cid, uid = app_and_user
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, txt="Chapter 1: Test")
    buf = io.BytesIO()
    pdf.output(buf)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post(
            "/upload",
            data={"collection_id": cid},
            files=[("files", ("book.pdf", buf.getvalue(), "application/pdf"))],
        )
        assert r.status_code == 303
        assert f"/collections/{cid}" in r.headers["location"]
        uconn = init_user_db(app.state.config.db_dir, uid)
        docs = list_docs(uconn, cid)
        assert len(docs) == 1
        assert docs[0]["status"] == "queued"
        uconn.close()


@pytest.mark.asyncio
async def test_upload_multiple_pdfs(app_and_user):
    app, cid, uid = app_and_user
    from fpdf import FPDF
    def make_pdf(text):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, txt=text)
        buf = io.BytesIO()
        pdf.output(buf)
        return buf.getvalue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post(
            "/upload",
            data={"collection_id": cid},
            files=[
                ("files", ("a.pdf", make_pdf("Chapter A"), "application/pdf")),
                ("files", ("b.pdf", make_pdf("Chapter B"), "application/pdf")),
            ],
        )
        assert r.status_code == 303
        uconn = init_user_db(app.state.config.db_dir, uid)
        docs = list_docs(uconn, cid)
        assert len(docs) == 2
        uconn.close()


@pytest.mark.asyncio
async def test_collection_view_shows_books(app_and_user):
    app, cid, uid = app_and_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get(f"/collections/{cid}")
        assert r.status_code == 200
        assert "PF" in r.text