"""Security regression tests for the Phase 1 audit fixes.

Covers:
  1.3  Session secret enforcement  (app/config.py)
  1.2  SQL injection parameterization (app/agent/tools.py fts_search)
  1.5  Admin flag carried in session token (app/auth/session.py + middleware)
  1.3  Session token forgery rejected  (app/auth/session.py)
  1.4  Rate-limit gating on login    (app/auth/routes.py)
  1.1  Path traversal (symlink escape) (app/storage/paths.py)
"""
import os
import pytest

from app.config import load_config


# ─── 1.3 Session secret enforcement ──────────────────────────────
def test_load_config_rejects_missing_secret(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("ollama:\n  host: http://x\nmodels: {}\nserver:\n  host: 0.0.0.0\n")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("DEV_MODE", raising=False)
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        load_config(str(cfg))


def test_load_config_rejects_default_secret(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("server:\n  secret: change-me-in-production\nollama:\n  host: http://x\nmodels: {}\n")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("DEV_MODE", raising=False)
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        load_config(str(cfg))


def test_load_config_dev_mode_allows_missing_secret(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("ollama:\n  host: http://x\nmodels: {}\nserver:\n  host: 0.0.0.0\n")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("DEV_MODE", "1")
    c = load_config(str(cfg))
    assert c.session_secret == "dev-secret-not-for-production"


def test_load_config_env_secret_overrides(tmp_path, monkeypatch):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("ollama:\n  host: http://x\nmodels: {}\nserver:\n  host: 0.0.0.0\n")
    monkeypatch.setenv("SESSION_SECRET", "real-production-secret")
    monkeypatch.delenv("DEV_MODE", raising=False)
    assert load_config(str(cfg)).session_secret == "real-production-secret"


# ─── 1.2 SQL injection: fts_search parameterizes doc_ids ──────────
@pytest.fixture
def toolbox_with_injection_doc_id(tmp_dirs):
    """A ToolBox whose collection contains a doc_id crafted as a SQL injection
    payload. If fts_search interpolated doc_ids into SQL, this would either
    error or escape the collection scope. Parameterization treats it as a
    literal."""
    from app.agent.tools import ToolBox
    from app.storage.user_db import init_user_db, create_collection, create_doc, insert_fts_row

    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "C")
    # Payload that would break out of a string-interpolated LIKE clause.
    evil = "d1' OR '1'='1"
    create_doc(uconn, evil, cid, "Book", "h")
    insert_fts_row(
        uconn, evil + "/01_chapter/01_goblin.md", "Goblin", "Goblin stats.",
        "goblin,monster", "Goblins are small humanoids with AC 15 and HP 7.",
    )
    # A second, unrelated collection whose docs must NOT leak into the query.
    other_cid = create_collection(uconn, "Other")
    create_doc(uconn, "d2", other_cid, "Secret", "h")
    insert_fts_row(uconn, "d2/leak.md", "Leak", "leak summary", "leak", "top secret leak content")
    uconn.close()
    doc_dir = tmp_dirs["data"] / "alice" / evil
    doc_dir.mkdir(parents=True)
    (doc_dir / "index.md").write_text("# Book\n")
    return ToolBox(tmp_dirs["data"], "alice", tmp_dirs["db"], cid)


def test_fts_search_handles_injection_doc_id(toolbox_with_injection_doc_id):
    # Must not raise — doc_id is bound as a parameter, not interpolated.
    results = toolbox_with_injection_doc_id.fts_search("goblin")
    assert len(results) == 1
    assert "goblin" in results[0]["path"].lower()


def test_fts_search_injection_does_not_leak_other_collection(toolbox_with_injection_doc_id):
    results = toolbox_with_injection_doc_id.fts_search("leak")
    # "leak" lives only in the OTHER collection; the scoped LIKE must exclude it.
    assert results == []


# ─── 1.5 + token forgery: session signing ─────────────────────────
def test_sign_session_carries_admin_flag():
    from app.auth.session import sign_session, verify_session
    token = sign_session("alice", "secret", is_admin=True)
    uid, is_admin = verify_session(token, "secret")
    assert uid == "alice"
    assert is_admin is True


def test_sign_session_default_not_admin():
    from app.auth.session import sign_session, verify_session
    token = sign_session("alice", "secret")
    uid, is_admin = verify_session(token, "secret")
    assert uid == "alice"
    assert is_admin is False


def test_verify_session_rejects_wrong_secret():
    from app.auth.session import sign_session, verify_session
    token = sign_session("alice", "real-secret")
    # Verification with a different secret must fail closed.
    assert verify_session(token, "other-secret") == (None, False)


def test_verify_session_rejects_tampered_payload():
    from app.auth.session import sign_session, verify_session
    import base64
    token = sign_session("alice", "secret", is_admin=False)
    raw, _sig = token.rsplit(".", 1)
    # Flip the is_admin flag in the payload, keep the old (now invalid) signature.
    import json
    payload = json.loads(base64.urlsafe_b64decode(raw))
    payload["is_admin"] = True
    tampered_raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    tampered = f"{tampered_raw}.{_sig}"
    uid, is_admin = verify_session(tampered, "secret")
    # Tampering must not elevate privileges.
    assert uid is None
    assert is_admin is False


def test_verify_session_rejects_expired_token():
    from app.auth.session import sign_session, verify_session
    token = sign_session("alice", "secret", ttl_seconds=-1)  # already expired
    assert verify_session(token, "secret") == (None, False)


# ─── 1.4 Rate-limit gating on login ───────────────────────────────
def test_is_rate_limited_true_in_production(monkeypatch):
    from app.auth import routes
    monkeypatch.delenv("DEV_MODE", raising=False)
    monkeypatch.delenv("TEST_MODE", raising=False)
    assert routes._is_rate_limited() is True


def test_is_rate_limited_false_in_dev(monkeypatch):
    from app.auth import routes
    monkeypatch.setenv("DEV_MODE", "1")
    monkeypatch.delenv("TEST_MODE", raising=False)
    assert routes._is_rate_limited() is False


def test_login_returns_429_when_rate_limit_exceeded(tmp_dirs, test_config):
    """With the limiter enabled, the 6th login within a minute must get 429
    (5/minute limit). Exercises the real slowapi decorator + handler path."""
    from httpx import AsyncClient, ASGITransport
    from app.main import create_app
    from app.storage.shared_db import init_shared_db, create_user
    from app.auth.passwords import hash_password
    import app.auth.routes as routes

    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw123"))
    conn.close()

    app = create_app(test_config, session_secret="testsecret")

    # Enable the limiter the decorator bound, and start from a clean slate.
    limiter = routes._get_limiter()
    limiter.enabled = True
    limiter._storage.reset()

    import asyncio

    async def run():
        codes = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(6):
                r = await client.post("/login", data={"username": "alice", "password": "wrong"})
                codes.append(r.status_code)
        return codes

    try:
        codes = asyncio.run(run())
    finally:
        limiter.enabled = False
        limiter._storage.reset()

    # First 5 within the limit: auth failure (401) but NOT rate-limited.
    assert codes[:5] == [401] * 5
    # 6th exceeds 5/minute → 429 from the RateLimitExceeded handler.
    assert codes[5] == 429


# ─── 1.1 Path traversal: symlink escape (consolidated) ────────────
def test_validate_user_path_rejects_symlink_escape(tmp_dirs):
    from pathlib import Path
    from app.storage.paths import validate_user_path

    alice = tmp_dirs["data"] / "alice"
    alice.mkdir()
    outside = tmp_dirs["data"] / "outside.txt"
    outside.write_text("nope")
    link = alice / "escape"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        validate_user_path(tmp_dirs["data"], "alice", str(link))


def test_validate_user_path_rejects_absolute_etc(tmp_dirs):
    from app.storage.paths import validate_user_path
    with pytest.raises(ValueError, match="outside"):
        validate_user_path(tmp_dirs["data"], "alice", "/etc/passwd")