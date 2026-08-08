"""Admin approve/reject of pending registrations."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import (
    init_shared_db, create_user, create_user_with_status,
    get_user_by_username, set_user_status,
)
from app.auth.passwords import hash_password
from tests.conftest import csrf_for


@pytest.fixture
def app_with_users(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "admin", hash_password("adminpw"), is_admin=True)
    create_user(conn, "alice", hash_password("pw123456"))
    conn.close()
    return create_app(test_config, session_secret="testsecret")


async def _login(client, username, password):
    await client.get("/login")
    token = client.cookies.get("login_csrf")
    r = await client.post("/login", data={"username": username, "password": password, "_csrf": token})
    assert r.status_code in (200, 303)


@pytest.mark.asyncio
async def test_admin_approves_pending_user(app_with_users, tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user_with_status(conn, "newbie", hash_password("pw123456"), status="pending")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await _login(client, "admin", "adminpw")
        r = await client.post(f"/admin/users/{uid}/approve", data={"_csrf": csrf_for(client)})
        assert r.status_code in (200, 303)
        conn = init_shared_db(tmp_dirs["db"])
        assert get_user_by_username(conn, "newbie")["status"] == "active"
        conn.close()


@pytest.mark.asyncio
async def test_admin_rejects_pending_user(app_with_users, tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user_with_status(conn, "newbie", hash_password("pw123456"), status="pending")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await _login(client, "admin", "adminpw")
        r = await client.post(f"/admin/users/{uid}/reject", data={"_csrf": csrf_for(client)})
        assert r.status_code in (200, 303)
        conn = init_shared_db(tmp_dirs["db"])
        assert get_user_by_username(conn, "newbie")["status"] == "rejected"
        conn.close()


@pytest.mark.asyncio
async def test_non_admin_cannot_approve(app_with_users, tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user_with_status(conn, "newbie", hash_password("pw123456"), status="pending")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await _login(client, "alice", "pw123456")
        r = await client.post(f"/admin/users/{uid}/approve", data={"_csrf": csrf_for(client)})
        assert r.status_code == 303
        conn = init_shared_db(tmp_dirs["db"])
        assert get_user_by_username(conn, "newbie")["status"] == "pending"
        conn.close()


@pytest.mark.asyncio
async def test_approved_user_can_login(app_with_users, tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    create_user_with_status(conn, "newbie", hash_password("pw123456"), status="pending")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await _login(client, "admin", "adminpw")
        conn = init_shared_db(tmp_dirs["db"])
        from app.storage.shared_db import get_user_by_username
        uid = get_user_by_username(conn, "newbie")["user_id"]
        conn.close()
        await client.post(f"/admin/users/{uid}/approve", data={"_csrf": csrf_for(client)})
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await _login(client, "newbie", "pw123456")
        assert "session" in client.cookies


@pytest.mark.asyncio
async def test_admin_creates_user_in_app(app_with_users, tmp_dirs):
    """Admins can create users directly from the admin dashboard."""
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await _login(client, "admin", "adminpw")
        r = await client.post(
            "/admin/users/create",
            data={"username": "carol", "password": "pw123456", "is_admin": "0", "_csrf": csrf_for(client)},
        )
        assert r.status_code in (200, 303)
        conn = init_shared_db(tmp_dirs["db"])
        u = get_user_by_username(conn, "carol")
        assert u is not None
        assert u["status"] == "active"
        assert u["is_admin"] == 0
        conn.close()


@pytest.mark.asyncio
async def test_admin_creates_admin_user_in_app(app_with_users, tmp_dirs):
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await _login(client, "admin", "adminpw")
        r = await client.post(
            "/admin/users/create",
            data={"username": "boss", "password": "pw123456", "is_admin": "1", "_csrf": csrf_for(client)},
        )
        assert r.status_code in (200, 303)
        conn = init_shared_db(tmp_dirs["db"])
        u = get_user_by_username(conn, "boss")
        assert u["is_admin"] == 1
        conn.close()


@pytest.mark.asyncio
async def test_non_admin_cannot_create_user(app_with_users, tmp_dirs):
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await _login(client, "alice", "pw123456")
        r = await client.post(
            "/admin/users/create",
            data={"username": "mallory", "password": "pw123456", "is_admin": "0", "_csrf": csrf_for(client)},
        )
        assert r.status_code == 303
        conn = init_shared_db(tmp_dirs["db"])
        assert get_user_by_username(conn, "mallory") is None
        conn.close()
