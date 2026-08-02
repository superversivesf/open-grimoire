import sqlite3
from app.storage.user_db import (
    init_user_db, create_collection, list_collections, create_doc, list_docs,
    create_session, get_session,
)


def test_init_creates_schema(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    assert "collections" in tables
    assert "docs" in tables
    assert "sessions" in tables
    conn.close()


def test_create_collection_returns_id(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "Pathfinder shelf")
    assert isinstance(cid, str) and len(cid) == 32
    conn.close()


def test_list_collections(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    create_collection(conn, "Pathfinder")
    create_collection(conn, "D&D")
    cols = list_collections(conn)
    assert len(cols) == 2
    assert {c["name"] for c in cols} == {"Pathfinder", "D&D"}
    conn.close()


def test_create_doc_and_list(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "PF")
    create_doc(conn, "doc1", cid, "Bestiary", "abc123")
    docs = list_docs(conn, cid)
    assert len(docs) == 1
    assert docs[0]["title"] == "Bestiary"
    assert docs[0]["sha256"] == "abc123"
    conn.close()


def test_list_docs_all_when_no_collection(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    c1 = create_collection(conn, "A")
    c2 = create_collection(conn, "B")
    create_doc(conn, "d1", c1, "t1", "h1")
    create_doc(conn, "d2", c2, "t2", "h2")
    docs = list_docs(conn)
    assert len(docs) == 2
    conn.close()


def test_create_session_and_get(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "PF")
    sid = create_session(conn, cid)
    s = get_session(conn, sid)
    assert s["collection_id"] == cid
    assert s["history_json"] == "[]"
    conn.close()