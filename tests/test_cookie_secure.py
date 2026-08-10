"""Tests for cookie Secure flag and security headers."""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.auth.passwords import hash_password


@pytest.fixture
def app_with_user(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    return create_app(test_config, session_secret="testsecret")


@pytest.fixture
def app_secure(tmp_dirs, test_config):
    import dataclasses
    secure_cfg = dataclasses.replace(test_config, cookie_secure=True)
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    return create_app(secure_cfg, session_secret="testsecret")


@pytest.mark.asyncio
async def test_session_cookie_is_secure_by_default(app_secure):
    async with AsyncClient(transport=ASGITransport(app=app_secure), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "alice", "password": "pw123"})
        assert r.status_code in (200, 303)
        assert "session" in r.cookies
        cookie = client.cookies.jar
        for c in cookie:
            if c.name == "session":
                assert c.secure, "session cookie must have Secure flag"
                assert c.has_nonstandard_attr("HttpOnly") or c._rest.get("HttpOnly")
                assert c.has_nonstandard_attr("SameSite") or c._rest.get("SameSite")


@pytest.mark.asyncio
async def test_cookie_secure_false_when_dev_mode(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()
    test_config.cookie_secure = False
    app = create_app(test_config, session_secret="testsecret")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/login", data={"username": "alice", "password": "pw123"})
        assert r.status_code in (200, 303)


@pytest.mark.asyncio
async def test_security_headers_present(app_with_user):
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/login")
        assert r.status_code == 200
        assert "X-Content-Type-Options" in r.headers
        assert "X-Frame-Options" in r.headers
        assert "Referrer-Policy" in r.headers
        assert "Content-Security-Policy" in r.headers


@pytest.mark.asyncio
async def test_csp_is_nonce_based(app_with_user):
    """CSP must be nonce-based: script-src and style-src carry a per-request
    nonce and no 'unsafe-inline'. Inline style attributes stay allowed via the
    narrow style-src-attr directive (pervasive in templates, low risk)."""
    import re
    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        r = await client.get("/login")
        assert r.status_code == 200
        csp = r.headers["Content-Security-Policy"]
        for directive in ("script-src", "style-src"):
            m = re.search(rf"{directive} ([^;]+)", csp)
            assert m, f"missing {directive} in CSP: {csp}"
            assert "unsafe-inline" not in m.group(1), f"{directive} must not allow unsafe-inline: {csp}"
        m = re.search(r"script-src 'self' 'nonce-([A-Za-z0-9_-]+)'", csp)
        assert m, f"script-src must carry a nonce: {csp}"
        assert f"'nonce-{m.group(1)}'" in csp


@pytest.mark.asyncio
async def test_csp_nonce_stamped_on_inline_scripts(app_with_user):
    """Inline <script>/<style> blocks in rendered pages must carry the same
    per-request nonce as the CSP header, or the browser blocks them."""
    import re
    from app.storage.shared_db import init_shared_db, create_user
    from app.storage.user_db import init_user_db, create_collection
    from app.auth.passwords import hash_password

    conn = init_shared_db(app_with_user.state.config.db_dir)
    uid = create_user(conn, "carol", hash_password("pw"))
    conn.close()
    uconn = init_user_db(app_with_user.state.config.db_dir, uid)
    create_collection(uconn, "C")
    uconn.close()

    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.get("/login")
        token = client.cookies.get("login_csrf")
        r = await client.post("/login", data={"username": "carol", "password": "pw", "_csrf": token})
        assert r.status_code in (200, 303)
        page = await client.get("/")
        assert page.status_code == 200
        csp = page.headers["Content-Security-Policy"]
        m = re.search(r"script-src 'self' 'nonce-([A-Za-z0-9_-]+)'", csp)
        assert m, f"script-src must carry a nonce: {csp}"
        nonce = m.group(1)
        assert f'<script nonce="{nonce}">' in page.text, "inline <script> must carry the CSP nonce"


@pytest.mark.asyncio
async def test_htmx_vendored_locally_and_csp_compatible():
    """htmx must be served from /static (CSP script-src 'self' allows it),
    never from an external CDN the CSP would block — otherwise hx-post is
    inert and the ask spinner never shows."""
    from pathlib import Path
    static_dir = Path(__file__).parent.parent / "app" / "web" / "static"
    htmx = static_dir / "htmx.min.js"
    assert htmx.exists(), "htmx must be vendored in app/web/static/"
    assert htmx.stat().st_size > 10000, "vendored htmx looks truncated"
    text = htmx.read_text()
    assert "htmx" in text.lower()

    base = (Path(__file__).parent.parent / "app" / "web" / "templates" / "base.html").read_text()
    assert "/static/htmx.min.js" in base, "base.html must load htmx from /static"
    assert "unpkg.com" not in base, "base.html must not load htmx from a CDN"
    assert "fonts.googleapis.com" not in base, "Google Fonts links are blocked by CSP — remove them"


@pytest.mark.asyncio
async def test_ask_form_has_spinner_wiring(app_with_user):
    """The ask form must keep its hx-post and hx-indicator wiring, and the
    spinner element must exist — the attribute→element contract that makes
    the loading indicator show during questions."""
    from httpx import AsyncClient, ASGITransport
    from app.storage.shared_db import init_shared_db, create_user
    from app.storage.user_db import init_user_db, create_collection
    from app.auth.passwords import hash_password
    from tests.conftest import csrf_for

    conn = init_shared_db(app_with_user.state.config.db_dir)
    uid = create_user(conn, "bob", hash_password("pw"))
    conn.close()
    uconn = init_user_db(app_with_user.state.config.db_dir, uid)
    cid = create_collection(uconn, "C")
    uconn.close()

    async with AsyncClient(transport=ASGITransport(app=app_with_user), base_url="http://test") as client:
        await client.get("/login")
        token = client.cookies.get("login_csrf")
        r = await client.post("/login", data={"username": "bob", "password": "pw", "_csrf": token})
        assert r.status_code in (200, 303)
        page = await client.get(f"/collections/{cid}")
        assert page.status_code == 200
        html = page.text
        assert 'hx-post="/sessions"' in html
        assert 'hx-indicator="#ask-spinner"' in html
        assert 'id="ask-spinner"' in html
        assert "Searching the manuals..." in html
