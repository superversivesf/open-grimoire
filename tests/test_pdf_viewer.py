"""Verify the PDF viewer page-jump wrapper renders with a CSP nonce."""
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status
from app.auth.passwords import hash_password


@pytest.fixture
def pdf_app(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Book", "h")
    update_doc_status(uconn, "d1", "done")
    uconn.close()
    doc_dir = tmp_dirs["data"] / uid / "d1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "original.pdf").write_bytes(b"%PDF-1.4 fake")
    return create_app(test_config, "testsecret")


@pytest.mark.asyncio
async def test_pdf_viewer_has_nonce_and_page_jump(pdf_app):
    async with AsyncClient(transport=ASGITransport(app=pdf_app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get("/docs/d1/pdf?page=18")
        assert r.status_code == 200
        html = r.text
        # The page-jump script must be present and carry the CSP nonce.
        assert "pdf-frame" in html
        assert "var page = 18;" in html
        assert "#page=" in html
        assert 'nonce="' in html
        # The nonce in the script tag must match the CSP header nonce.
        csp = r.headers.get("content-security-policy", "")
        import re
        m = re.search(r"script-src[^;]*'nonce-([^']+)'", csp)
        assert m, f"no nonce in CSP: {csp}"
        assert f'nonce="{m.group(1)}"' in html, "script nonce must match CSP nonce"


@pytest.mark.asyncio
async def test_pdf_served_directly_without_page(pdf_app):
    async with AsyncClient(transport=ASGITransport(app=pdf_app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get("/docs/d1/pdf")
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/pdf")


@pytest.mark.asyncio
async def test_pdf_response_allows_same_origin_framing(pdf_app):
    """The raw PDF is loaded in the viewer's <iframe> — X-Frame-Options: DENY
    would block it. The PDF response must NOT carry the DENY header."""
    async with AsyncClient(transport=ASGITransport(app=pdf_app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get("/docs/d1/pdf")
        assert r.status_code == 200
        assert "x-frame-options" not in r.headers, "PDF must be frameable (viewer iframe)"
        # The viewer page itself must still be DENY-protected.
        v = await client.get("/docs/d1/pdf?page=1")
        assert v.headers.get("x-frame-options") == "DENY"
