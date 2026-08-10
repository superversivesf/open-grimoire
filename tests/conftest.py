import pytest
from pathlib import Path
import tempfile
import os
from httpx import AsyncClient
from app.config import Config
from app.auth.session import get_csrf_token
from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user, add_collection_member
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status, insert_fts_row
from app.auth.passwords import hash_password



def csrf_for(client, secret: str = "testsecret") -> str:
    """Extract the CSRF token from the client's session cookie."""
    session = client.cookies.get("session")
    if session and session.startswith('"') and session.endswith('"'):
        session = session[1:-1]
    token = get_csrf_token(session, secret)
    assert token is not None, "no CSRF token in session cookie"
    return token


async def login(client: AsyncClient, username: str = "alice", password: str = "pw123") -> None:
    """Log in and verify session cookie is set."""
    r = await client.post("/login", data={"username": username, "password": password})
    assert r.status_code in (200, 303)
    assert "session" in r.cookies


@pytest.fixture(autouse=True)
def disable_csrf_dependency(request, monkeypatch):
    """No-op the require_csrf dependency for most tests.

    The dedicated CSRF tests (test_csrf_token.py) exercise the real
    dependency; everything else focuses on other behavior and would
    otherwise need a token on every POST.
    """
    if "test_csrf_token" in request.node.fspath.strpath:
        return
    import app.auth.csrf as csrf_mod
    monkeypatch.setattr(csrf_mod, "CSRF_ENABLED", False)


@pytest.fixture
def tmp_dirs(tmp_path):
    data_dir = tmp_path / "data"
    db_dir = tmp_path / "db"
    data_dir.mkdir()
    db_dir.mkdir()
    return {"data": data_dir, "db": db_dir}


@pytest.fixture
def test_config(tmp_dirs):
    """Config for tests with DEV_MODE=1 to allow default secret."""
    os.environ["DEV_MODE"] = "1"
    return Config(
        ollama_host="http://localhost:11434",
        session_secret="test-secret",
        data_dir=tmp_dirs["data"],
        db_dir=tmp_dirs["db"],
        models={},
        cookie_secure=False,
    )


@pytest.fixture(autouse=True)
def disable_rate_limiter():
    """Disable rate limiting for all tests.

    The @limiter.limit decorator binds the module singleton limiter at import
    time, so swapping the module-level `_limiter` no longer affects it. Instead
    we disable the real singleton via `.enabled = False` (which the decorator
    checks at call time) and clear its in-memory storage to stop counts from
    one test leaking into another.
    """
    import app.auth.routes as routes
    limiter = routes._get_limiter()
    original_enabled = limiter.enabled
    limiter.enabled = False
    # Clear any accumulated hits from prior tests.
    try:
        limiter._storage.reset()
    except Exception:
        pass
    yield
    limiter.enabled = original_enabled


DOC_ID_A = "38dfd1fd2c4249f193f923458891812f"


@pytest.fixture
def shared_collection_fixture(tmp_dirs, test_config):
    """alice owns shared c1 with a goblin doc; bob is member; eve is nobody.
    Returns (app, alice, bob, eve, cid)."""
    conn = init_shared_db(tmp_dirs["db"])
    alice = create_user(conn, "alice", hash_password("pw123456"))
    bob = create_user(conn, "bob", hash_password("pw123456"))
    eve = create_user(conn, "eve", hash_password("pw123456"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], alice)
    cid = create_collection(uconn, "Shared Shelf")
    create_doc(uconn, DOC_ID_A, cid, "Goblin Book", "sha1")
    update_doc_status(uconn, DOC_ID_A, "done")
    insert_fts_row(uconn, f"{DOC_ID_A}/01_goblin.md", "Goblin", "Goblin stats.", "goblin", "Goblins have AC 15.")
    uconn.close()
    doc_dir = tmp_dirs["data"] / alice / DOC_ID_A
    doc_dir.mkdir(parents=True)
    (doc_dir / "original.pdf").write_bytes(b"%PDF-1.4 fake")
    (doc_dir / "cover.jpg").write_bytes(b"jpegdata")
    (doc_dir / "01_goblin.md").write_text("---\nsummary: \"Goblin.\"\nkeywords: [goblin]\n---\n\n# Goblin\n\nAC 15, HP 7.\n")
    conn = init_shared_db(tmp_dirs["db"])
    add_collection_member(conn, cid, alice, "owner")
    add_collection_member(conn, cid, bob, "member")
    conn.close()
    app = create_app(test_config, session_secret="testsecret")
    return app, alice, bob, eve, cid
