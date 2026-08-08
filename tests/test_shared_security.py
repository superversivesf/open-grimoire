"""Cross-user authorization regression net for shared collections.

Every route + the ToolBox sandbox: non-members must be blocked, members
must not reach non-shared collections, removed members lose access, and
the toolbox cannot escape the owner's root.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.storage.shared_db import (
    init_shared_db, create_user, add_collection_member, remove_collection_member,
)
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status
from app.auth.passwords import hash_password
from app.agent.tools import ToolBox
from tests.conftest import csrf_for

DOC_ID_A = "38dfd1fd2c4249f193f923458891812f"


@pytest.fixture
def multi_user_setup(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw123456"))
    bob = create_user(conn, "bob", hash_password("pw123456"))
    eve = create_user(conn, "eve", hash_password("pw123456"))
    conn.close()
    # alice: shared c1 (with doc) + private c2
    uconn = init_user_db(tmp_dirs["db"], alice)
    c1 = create_collection(uconn, "Shared Shelf")
    create_doc(uconn, DOC_ID_A, c1, "Goblin Book", "sha1")
    update_doc_status(uconn, DOC_ID_A, "done")
    c2 = create_collection(uconn, "Private Shelf")
    uconn.close()
    doc_dir = tmp_dirs["data"] / alice / DOC_ID_A
    doc_dir.mkdir(parents=True)
    (doc_dir / "original.pdf").write_bytes(b"%PDF-1.4")
    conn = init_shared_db(tmp_dirs["db"])
    add_collection_member(conn, c1, alice, "owner")
    add_collection_member(conn, c1, bob, "member")
    conn.close()
    app = create_app(test_config, session_secret="testsecret")
    return app, alice, bob, eve, c1, c2


async def _login(client, username):
    await client.get("/login")
    token = client.cookies.get("login_csrf")
    r = await client.post("/login", data={"username": username, "password": "pw123456", "_csrf": token})
    assert r.status_code in (200, 303)


@pytest.mark.asyncio
async def test_non_member_blocked_everywhere(multi_user_setup):
    """eve (no membership): every shared-collection route -> 303 '/'."""
    app, alice, bob, eve, c1, c2 = multi_user_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "eve")
        for path in [
            f"/collections/{c1}",
            f"/collections/{c1}/table",
            f"/collections/{c1}/upload",
            f"/docs/{DOC_ID_A}",
            f"/docs/{DOC_ID_A}/cover",
            f"/docs/{DOC_ID_A}/pdf",
        ]:
            r = await client.get(path, follow_redirects=False)
            assert r.status_code == 303, f"{path} should redirect"
            assert r.headers["location"] == "/", f"{path} should redirect to /"
        r = await client.post(
            "/sessions",
            data={"collection_id": c1, "question": "hi", "_csrf": csrf_for(client)},
            follow_redirects=False,
        )
        assert r.status_code == 303


@pytest.mark.asyncio
async def test_member_cannot_reach_private_collections(multi_user_setup):
    """bob (member of c1 only): alice's private c2 must be blocked."""
    app, alice, bob, eve, c1, c2 = multi_user_setup
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get(f"/collections/{c2}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_removed_member_loses_access(multi_user_setup):
    """Removing bob from c1 must immediately block his access."""
    app, alice, bob, eve, c1, c2 = multi_user_setup
    conn = init_shared_db(app.state.config.db_dir)
    remove_collection_member(conn, c1, bob)
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "bob")
        r = await client.get(f"/collections/{c1}", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_owner_keeps_access_after_removal(multi_user_setup):
    """The owner retains access after removing a member."""
    app, alice, bob, eve, c1, c2 = multi_user_setup
    conn = init_shared_db(app.state.config.db_dir)
    remove_collection_member(conn, c1, bob)
    conn.close()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _login(client, "alice")
        r = await client.get(f"/collections/{c1}")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_toolbox_cannot_escape_owner_root(multi_user_setup, tmp_dirs):
    """ToolBox(owner_uid=alice): path traversal outside the owner's tree rejected."""
    app, alice, bob, eve, c1, c2 = multi_user_setup
    # eve's doc to prove isolation
    uconn = init_user_db(tmp_dirs["db"], eve)
    create_doc(uconn, "evedoc123", c2, "Eve Doc", "sha")
    uconn.close()
    eve_dir = tmp_dirs["data"] / eve / "evedoc123"
    eve_dir.mkdir(parents=True)
    (eve_dir / "secret.md").write_text("EVE SECRET DATA")
    toolbox = ToolBox(tmp_dirs["data"], bob, tmp_dirs["db"], c1, owner_uid=alice)
    # Absolute-path escape attempt
    try:
        content = toolbox.read_file(f"../../{eve}/evedoc123/secret.md")
        assert "EVE SECRET DATA" not in content
    except ValueError:
        pass  # rejected by validate_user_path — also correct
    # grep cannot reach eve's tree
    results = toolbox.grep("EVE SECRET")
    assert len(results) == 0
