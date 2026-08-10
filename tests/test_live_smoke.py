"""Live-site smoke tests — run against a deployed instance (prod or test).

This suite exercises the REAL deployed app over HTTP, not mocks. It is the
pre-deploy baseline: every change must pass these before shipping.

Run against the test container (default):
    LIVE_SMOKE_URL=http://localhost:8051 pytest tests/test_live_smoke.py -v -m live

Run against prod (read-only checks only):
    LIVE_SMOKE_URL=https://grim.superversive.net LIVE_SMOKE_WRITE=0 pytest tests/test_live_smoke.py -v -m live

Write tests (upload/ask) are skipped unless LIVE_SMOKE_WRITE=1 — they mutate
data, so they should only target the test container, never prod.
"""
import os
import re
import pytest
import httpx

pytestmark = pytest.mark.live

BASE_URL = os.environ.get("LIVE_SMOKE_URL", "http://localhost:8051").rstrip("/")
ALLOW_WRITE = os.environ.get("LIVE_SMOKE_WRITE", "0") == "1"
SMOKE_USER = os.environ.get("LIVE_SMOKE_USER", "admin")
SMOKE_PASS = os.environ.get("LIVE_SMOKE_PASS", "")


def _require_write():
    if not ALLOW_WRITE:
        pytest.skip("write tests disabled (set LIVE_SMOKE_WRITE=1 for test container only)")


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, follow_redirects=False, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    """Log in against the live site, handling login_csrf + session CSRF."""
    if not SMOKE_PASS:
        pytest.skip("set LIVE_SMOKE_PASS to run authenticated checks")
    # 1. GET /login → login_csrf cookie + form token
    r = client.get("/login")
    assert r.status_code == 200
    login_csrf = client.cookies.get("login_csrf")
    m = re.search(r'name="_csrf" value="([^"]+)"', r.text)
    assert login_csrf and m, "login page must set login_csrf cookie and form token"
    assert m.group(1) == login_csrf, "form token must match login_csrf cookie"
    # 2. POST /login with the token
    r = client.post("/login", data={
        "username": SMOKE_USER, "password": SMOKE_PASS, "_csrf": login_csrf,
    }, headers={"origin": BASE_URL})
    assert r.status_code in (200, 303), f"login failed: {r.status_code} {r.text[:200]}"
    assert "session" in client.cookies, "login must set session cookie"
    return client


# ─── Unauthenticated checks ──────────────────────────────────────────

def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["ollama"] == "ok"


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Enter the Library" in r.text
    assert "login_csrf" in client.cookies
    assert "no-store" in r.headers.get("cache-control", "")


def test_login_requires_csrf(client):
    r = client.post("/login", data={"username": SMOKE_USER, "password": SMOKE_PASS},
                    headers={"origin": BASE_URL})
    # 422 = FastAPI form validation rejects missing _csrf field;
    # 403 = require_login_csrf rejects it. Either is a valid rejection.
    assert r.status_code in (403, 422), "login without _csrf must be rejected"


def test_security_headers(client):
    r = client.get("/login")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    csp = r.headers.get("content-security-policy", "")
    # script-src must be nonce-based with NO unsafe-inline (XSS vector).
    m = re.search(r"script-src\s+([^;]+)", csp)
    assert m, "CSP must declare script-src"
    assert "nonce-" in m.group(1), "script-src must use a nonce"
    assert "unsafe-inline" not in m.group(1), "script-src must not allow unsafe-inline"
    # style-src must be nonce-based; style-src-attr may keep unsafe-inline
    # (inline style= attributes are low-risk and pervasive in templates).
    s = re.search(r"style-src\s+([^;]+)", csp)
    assert s and "nonce-" in s.group(1), "style-src must use a nonce"


# ─── Authenticated read-only checks ──────────────────────────────────

def test_library_loads(auth):
    r = auth.get("/")
    assert r.status_code == 200
    assert "Open Grimoire" in r.text


def test_library_has_collections(auth):
    r = auth.get("/")
    assert "No collections yet" not in r.text, "expected at least one collection"


def test_collection_view_has_no_empty_table_url(auth):
    """Regression: /collections//table 404 spam from empty collection_id."""
    r = auth.get("/")
    cids = re.findall(r'href="/collections/([a-f0-9]{32})"', r.text)
    assert cids, "library must list collection links"
    for cid in cids[:3]:
        page = auth.get(f"/collections/{cid}")
        assert page.status_code == 200
        assert f"/collections/{cid}/table" in page.text
        assert "/collections//table" not in page.text


def test_doc_view_and_cover(auth):
    """Find a doc via a collection and check its view/cover/pdf routes."""
    r = auth.get("/")
    cids = re.findall(r'href="/collections/([a-f0-9]{32})"', r.text)
    if not cids:
        pytest.skip("no collections")
    for cid in cids[:2]:
        page = auth.get(f"/collections/{cid}")
        doc_ids = re.findall(r'href="/docs/([a-f0-9]{32})"', page.text)
        if not doc_ids:
            continue
        did = doc_ids[0]
        v = auth.get(f"/docs/{did}")
        assert v.status_code == 200
        c = auth.get(f"/docs/{did}/cover")
        assert c.status_code in (200, 303)
        pdf = auth.get(f"/docs/{did}/pdf")
        assert pdf.status_code in (200, 303)
        return
    pytest.skip("no docs found")


def test_pdf_page_jump_viewer(auth):
    """The ?page=N viewer must render the iframe wrapper with a nonce."""
    r = auth.get("/")
    cids = re.findall(r'href="/collections/([a-f0-9]{32})"', r.text)
    if not cids:
        pytest.skip("no collections")
    for cid in cids[:2]:
        page = auth.get(f"/collections/{cid}")
        doc_ids = re.findall(r'href="/docs/([a-f0-9]{32})"', page.text)
        if not doc_ids:
            continue
        v = auth.get(f"/docs/{doc_ids[0]}/pdf?page=1")
        if v.status_code != 200:
            continue
        html = v.text
        assert "pdf-frame" in html, "PDF viewer must render the iframe wrapper"
        assert "#page=" in html, "viewer must build a #page= fragment URL"
        csp = v.headers.get("content-security-policy", "")
        m = re.search(r"script-src[^;]*'nonce-([^']+)'", csp)
        assert m, "viewer response must carry a nonce CSP"
        assert f'nonce="{m.group(1)}"' in html, "script nonce must match CSP nonce"
        return
    pytest.skip("no docs found")


def test_pdf_file_allows_framing(auth):
    """Regression: X-Frame-Options: DENY on the raw PDF breaks the viewer iframe."""
    r = auth.get("/")
    cids = re.findall(r'href="/collections/([a-f0-9]{32})"', r.text)
    if not cids:
        pytest.skip("no collections")
    for cid in cids[:2]:
        page = auth.get(f"/collections/{cid}")
        doc_ids = re.findall(r'href="/docs/([a-f0-9]{32})"', page.text)
        if not doc_ids:
            continue
        pdf = auth.get(f"/docs/{doc_ids[0]}/pdf")
        if pdf.status_code == 200 and "application/pdf" in pdf.headers.get("content-type", ""):
            assert "x-frame-options" not in pdf.headers, \
                "raw PDF must not carry X-Frame-Options (viewer iframe)"
            return
    pytest.skip("no raw PDF found")


def test_sessions_page(auth):
    r = auth.get("/sessions")
    assert r.status_code == 200


# ─── Write tests (test container only) ───────────────────────────────

def test_ask_question_end_to_end(auth):
    """Ask a question and verify the answer renders with citations."""
    _require_write()
    r = auth.get("/")
    cids = re.findall(r'href="/collections/([a-f0-9]{32})"', r.text)
    assert cids, "need a collection to ask against"
    from app.auth.session import get_csrf_token
    token = get_csrf_token(auth.cookies.get("session"), os.environ.get("LIVE_SMOKE_SECRET", ""))
    # Fallback: extract from the page's hidden _csrf field if secret unknown.
    if not token:
        page = auth.get("/")
        m = re.search(r'name="_csrf" value="([^"]+)"', page.text)
        assert m, "need a CSRF token from a rendered form"
        token = m.group(1)
    r = auth.post("/sessions", data={
        "collection_id": cids[0],
        "question": "What is armor class?",
        "_csrf": token,
    }, headers={"origin": BASE_URL})
    assert r.status_code == 200, f"ask failed: {r.status_code} {r.text[:300]}"
    assert "rpg-chat-agent" in r.text, "answer must render"
