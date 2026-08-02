import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status
from app.auth.passwords import hash_password


@pytest.fixture
def app_and_doc(tmp_dirs, monkeypatch):
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
    (doc_dir / "index.md").write_text("# Book\n\n- [Chapter 1](01_chapter_1/index.md)\n")
    chap = doc_dir / "01_chapter_1"
    chap.mkdir()
    (chap / "index.md").write_text("# Chapter 1\n\n- [Section](01_section.md)\n")
    (chap / "01_section.md").write_text("# Section\n\nContent here.\n")
    cfg = Config("http://x", {}, tmp_dirs["data"], tmp_dirs["db"])
    app = create_app(cfg, "s")
    return app, "d1"


@pytest.mark.asyncio
async def test_doc_view_shows_status(app_and_doc):
    app, did = app_and_doc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get(f"/docs/{did}")
        assert r.status_code == 200
        assert "Book" in r.text
        assert "done" in r.text.lower()


@pytest.mark.asyncio
async def test_doc_view_shows_tree(app_and_doc):
    app, did = app_and_doc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get(f"/docs/{did}")
        assert "Chapter 1" in r.text


@pytest.mark.asyncio
async def test_doc_view_unknown_redirects(app_and_doc):
    app, _ = app_and_doc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get("/docs/nonexistent")
        assert r.status_code == 303