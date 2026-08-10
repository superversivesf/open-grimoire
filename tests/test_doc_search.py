"""Tests for /docs/search (doc_search_path in app/web/routes.py).

Contract: given a file path, redirect to the doc that contains it, using
the FTS-built path index first and the filesystem as a fallback. Paths
that match nothing — including path-traversal attempts — fall back to "/".
"""
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import (
    init_user_db, create_collection, create_doc, update_doc_status,
    insert_fts_row,
)
from app.auth.passwords import hash_password

# 32-char hex — matches the direct-doc_id branch of doc_search_path.
DIRECT_DOC = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@pytest.fixture
def app_with_docs(tmp_dirs, test_config):
    conn = init_shared_db(tmp_dirs["db"])
    uid = create_user(conn, "alice", hash_password("pw"))
    conn.close()

    uconn = init_user_db(tmp_dirs["db"], uid)
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Book", "h")
    update_doc_status(uconn, "d1", "done")
    # FTS index (path stored as "<doc_id>/<rel path>")
    insert_fts_row(uconn, "d1/01_goblin.md", "Goblin", "Goblin stats", "goblin", "AC 15")
    insert_fts_row(uconn, "d1/chapter_2/02_orc.md", "Orc", "Orc stats", "orc", "AC 13")
    # Second doc with 32-char id for the direct-doc_id branch.
    create_doc(uconn, DIRECT_DOC, cid, "Other", "h")
    update_doc_status(uconn, DIRECT_DOC, "done")
    insert_fts_row(uconn, f"{DIRECT_DOC}/03_dragon.md", "Dragon", "Dragon", "dragon", "breath")
    uconn.close()

    data = tmp_dirs["data"]
    d1 = data / uid / "d1"
    d1.mkdir(parents=True)
    (d1 / "01_goblin.md").write_text("# Goblin\n\nAC 15.")
    ch2 = d1 / "chapter_2"
    ch2.mkdir()
    (ch2 / "02_orc.md").write_text("# Orc\n\nAC 13.")
    # A file on disk with NO FTS row — exercises the filesystem fallback.
    (d1 / "loose_note.md").write_text("# Loose note\n")
    direct = data / uid / DIRECT_DOC
    direct.mkdir(parents=True)
    (direct / "03_dragon.md").write_text("# Dragon\n\nBreath weapon.")
    # Decoy outside any user's tree: a traversal must never resolve here.
    etc = data / "etc"
    etc.mkdir()
    (etc / "passwd").write_text("root:x:0:0:root:/root:/bin/bash")

    app = create_app(test_config, "testsecret")
    return app, uid


async def _login_and_search(app, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        return await client.get("/docs/search", params={"path": path}, follow_redirects=False)


@pytest.mark.asyncio
async def test_search_exact_filename_match_via_fts(app_with_docs):
    app, _ = app_with_docs
    r = await _login_and_search(app, "01_goblin.md")
    assert r.status_code == 303
    assert "/docs/d1/view?path=01_goblin.md" in r.headers["location"]


@pytest.mark.asyncio
async def test_search_full_path_match_via_fts(app_with_docs):
    app, _ = app_with_docs
    r = await _login_and_search(app, "d1/01_goblin.md")
    assert r.status_code == 303
    # Full-path match strips the doc_id prefix from the redirect query.
    assert "/docs/d1/view?path=01_goblin.md" in r.headers["location"]


@pytest.mark.asyncio
async def test_search_nested_path_match_via_fts(app_with_docs):
    app, _ = app_with_docs
    r = await _login_and_search(app, "chapter_2/02_orc.md")
    assert r.status_code == 303
    assert "/docs/d1/view?path=chapter_2/02_orc.md" in r.headers["location"]


@pytest.mark.asyncio
async def test_search_partial_filename_match(app_with_docs):
    """Searching a filename stem (no extension, underscore folded) resolves
    via the fuzzy stem match."""
    app, _ = app_with_docs
    r = await _login_and_search(app, "01_goblin")
    assert r.status_code == 303
    assert "/docs/d1/view?path=01_goblin.md" in r.headers["location"]


@pytest.mark.asyncio
async def test_search_fuzzy_keyword_match(app_with_docs):
    """A bare keyword that appears in a path matches that path."""
    app, _ = app_with_docs
    r = await _login_and_search(app, "goblin")
    assert r.status_code == 303
    assert "/docs/d1/view" in r.headers["location"]


@pytest.mark.asyncio
async def test_search_no_match_falls_back_to_root(app_with_docs):
    app, _ = app_with_docs
    r = await _login_and_search(app, "no_such_file.md")
    assert r.status_code == 303
    assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_search_filesystem_fallback_for_unindexed_file(app_with_docs):
    """A file that exists on disk but has no FTS row is found by rglob."""
    app, _ = app_with_docs
    r = await _login_and_search(app, "loose_note.md")
    assert r.status_code == 303
    assert "/docs/d1/view?path=loose_note.md" in r.headers["location"]


@pytest.mark.asyncio
async def test_search_direct_doc_id_prefix(app_with_docs):
    """A path whose first segment is a 32-char doc_id resolves directly."""
    app, _ = app_with_docs
    r = await _login_and_search(app, f"{DIRECT_DOC}/03_dragon.md")
    assert r.status_code == 303
    assert f"/docs/{DIRECT_DOC}/view?path=03_dragon.md" in r.headers["location"]


@pytest.mark.asyncio
async def test_search_path_traversal_does_not_resolve_outside_tree(app_with_docs):
    """Traversal attempts must fall back to '/' — never redirect to a file
    outside the user's doc trees (decoy at <data>/etc/passwd)."""
    app, _ = app_with_docs
    for traversal in ("../../etc/passwd", "../../../../etc/passwd", "d1/../../../etc/passwd"):
        r = await _login_and_search(app, traversal)
        assert r.status_code == 303
        assert r.headers["location"] == "/", f"traversal {traversal!r} must not resolve"
        assert "passwd" not in r.headers["location"]


@pytest.mark.asyncio
async def test_search_traversal_ending_in_indexed_filename_is_canonicalized(app_with_docs):
    """A traversal-style path that ends in an indexed filename redirects to
    the canonical doc view with only the safe relative filename in the query —
    the traversal prefix must not leak into the redirect target."""
    app, _ = app_with_docs
    r = await _login_and_search(app, "../../alice/d1/01_goblin.md")
    assert r.status_code == 303
    assert r.headers["location"] == "/docs/d1/view?path=01_goblin.md"
    assert ".." not in r.headers["location"]


@pytest.mark.asyncio
async def test_search_requires_login(app_with_docs):
    app, _ = app_with_docs
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/docs/search", params={"path": "01_goblin.md"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
