"""Coverage for previously untested paths: /admin authz, SSE stream, upload size."""

import io
import json
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import MagicMock
from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection, create_session
from app.auth.passwords import hash_password
from tests.conftest import csrf_for


@pytest.fixture
def app_with_users(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "admin", hash_password("adminpw"), is_admin=True)
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    return create_app(test_config, session_secret="testsecret")


# ─── /admin authorization ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_admin_requires_login(app_with_users):
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        r = await client.get("/admin", follow_redirects=False)
        assert r.status_code == 303
        assert "/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_admin_rejects_non_admin(app_with_users):
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw123"})
        r = await client.get("/admin", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_admin_allows_admin(app_with_users):
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await client.post("/login", data={"username": "admin", "password": "adminpw"})
        r = await client.get("/admin")
        assert r.status_code == 200
        assert "admin" in r.text.lower()


# ─── SSE streaming endpoint ───────────────────────────────────────────
@pytest.fixture
def app_with_session(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "C")
    sid = create_session(uconn, cid, "First question")
    uconn.close()
    app = create_app(test_config, session_secret="testsecret")
    mock_loop = MagicMock()
    async def mock_run_stream(history, question):
        yield {"type": "thinking", "message": "searching..."}
        yield {"type": "done", "answer": "AC is 15.", "cites": [], "suggestions": [], "iterations": 1}
    mock_loop.run_stream = mock_run_stream
    app.state.agent_loop_factory = lambda toolbox: mock_loop
    return app, uid, sid


@pytest.mark.asyncio
async def test_sse_stream_returns_events(app_with_session):
    app, uid, sid = app_with_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post(
            f"/sessions/{sid}/stream",
            data={"question": "What is AC?", "_csrf": csrf_for(client)},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = r.text
        assert "searching" in body
        assert "AC is 15." in body


@pytest.mark.asyncio
async def test_sse_stream_requires_login(app_with_session):
    app, uid, sid = app_with_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(f"/sessions/{sid}/stream", data={"question": "hi"})
        assert r.status_code == 303
        assert "/login" in r.headers["location"]


# ─── Upload size enforcement ───────────────────────────────────────────
@pytest.fixture
def app_and_user(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "PF")
    uconn.close()
    app = create_app(test_config, session_secret="s")
    return app, cid, uid


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(app_and_user, monkeypatch):
    app, cid, uid = app_and_user
    import app.web.routes as routes
    monkeypatch.setattr(routes, "MAX_UPLOAD_BYTES", 1024)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        big = b"%PDF-1.4\n" + b"x" * 4096
        r = await client.post(
            "/upload",
            data={"collection_id": cid, "_csrf": csrf_for(client, "s")},
            files=[("files", ("big.pdf", big, "application/pdf"))],
        )
        assert r.status_code in (200, 303)
        from app.storage.user_db import init_user_db, list_docs
        uconn = init_user_db(app.state.config.db_dir, uid)
        docs = list_docs(uconn, cid)
        uconn.close()
        assert len(docs) == 0, "oversized file must be skipped"