"""Admin approve/reject of pending registrations."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import (
    init_shared_db, create_user, create_user_with_status,
    get_user_by_username, set_user_status,
)
from app.auth.passwords import hash_password
from tests.conftest import csrf_for, login


@pytest.fixture
def app_with_users(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "admin", hash_password("adminpw"), is_admin=True)
    create_user(conn, "alice", hash_password("pw123456"))
    conn.close()
    return create_app(test_config, session_secret="testsecret")


@pytest.mark.asyncio
async def test_admin_approves_pending_user(app_with_users, tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user_with_status(conn, "newbie", hash_password("pw123456"), status="pending")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await login(client, "admin", password="adminpw")
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
        await login(client, "admin", password="adminpw")
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
        await login(client, "alice", password="pw123456")
        r = await client.post(f"/admin/users/{uid}/approve", data={"_csrf": csrf_for(client)})
        assert r.status_code == 303
        conn = init_shared_db(tmp_dirs["db"])
        assert get_user_by_username(conn, "newbie")["status"] == "pending"
        conn.close()


@pytest.mark.asyncio
async def test_demoted_admin_loses_access_immediately(app_with_users, tmp_dirs):
    """A demoted admin's existing session must not keep admin access until
    cookie expiry — the middleware must re-verify is_admin from the DB."""
    conn = init_shared_db(tmp_dirs["db"])
    boss_uid = create_user(conn, "boss", hash_password("pw123456"), is_admin=True)
    victim_uid = create_user_with_status(conn, "newbie", hash_password("pw123456"), status="pending")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await login(client, "boss", password="pw123456")
        # Demote in the DB while the session cookie is still valid.
        conn = init_shared_db(tmp_dirs["db"])
        conn.execute("UPDATE users SET is_admin = 0 WHERE user_id = ?", (boss_uid,))
        conn.commit()
        conn.close()
        # The admin POST routes rely on request.state.is_admin — a stale
        # token flag must not let the demoted admin approve users.
        r = await client.post(f"/admin/users/{victim_uid}/approve",
                              data={"_csrf": csrf_for(client)})
        assert r.status_code == 303
        conn = init_shared_db(tmp_dirs["db"])
        assert get_user_by_username(conn, "newbie")["status"] == "pending", \
            "demoted admin must not be able to approve users"
        conn.close()


@pytest.mark.asyncio
async def test_rejected_user_loses_access_immediately(app_with_users, tmp_dirs):
    """A rejected user's existing session must not keep access until cookie
    expiry — the middleware must re-verify users.status from the DB."""
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user_with_status(conn, "newbie", hash_password("pw123456"), status="active")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await login(client, "newbie", password="pw123456")
        r = await client.get("/", follow_redirects=False)
        assert r.status_code == 200, "active user must reach the library"
        # Reject in the DB while the session cookie is still valid.
        conn = init_shared_db(tmp_dirs["db"])
        set_user_status(conn, uid, "rejected")
        conn.close()
        r = await client.get("/", follow_redirects=False)
        assert r.status_code == 303, "rejected user must lose access immediately"


@pytest.mark.asyncio
async def test_approved_user_can_login(app_with_users, tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    create_user_with_status(conn, "newbie", hash_password("pw123456"), status="pending")
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await login(client, "admin", password="adminpw")
        conn = init_shared_db(tmp_dirs["db"])
        from app.storage.shared_db import get_user_by_username
        uid = get_user_by_username(conn, "newbie")["user_id"]
        conn.close()
        await client.post(f"/admin/users/{uid}/approve", data={"_csrf": csrf_for(client)})
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await login(client, "newbie", password="pw123456")
        assert "session" in client.cookies


@pytest.mark.asyncio
async def test_admin_creates_user_in_app(app_with_users, tmp_dirs):
    """Admins can create users directly from the admin dashboard."""
    async with AsyncClient(transport=ASGITransport(app=app_with_users), base_url="http://test") as client:
        await login(client, "admin", password="adminpw")
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
        await login(client, "admin", password="adminpw")
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
        await login(client, "alice", password="pw123456")
        r = await client.post(
            "/admin/users/create",
            data={"username": "mallory", "password": "pw123456", "is_admin": "0", "_csrf": csrf_for(client)},
        )
        assert r.status_code == 303
        conn = init_shared_db(tmp_dirs["db"])
        assert get_user_by_username(conn, "mallory") is None
        conn.close()
