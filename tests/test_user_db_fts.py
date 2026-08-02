from app.storage.user_db import (
    init_user_db, create_collection, create_doc, insert_fts_row,
    delete_fts_rows_for_doc, update_doc_status, get_doc, delete_doc,
)


def test_fts_table_created(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    assert "documents_fts" in tables
    conn.close()


def test_insert_and_search_fts(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    insert_fts_row(conn, "data/alice/d1/c1/s1.md", "Goblin", "AC 15 monster", "goblin,monster", "Goblins are small humanoids with AC 15.")
    rows = conn.execute("SELECT path FROM documents_fts WHERE documents_fts MATCH 'goblin'").fetchall()
    assert len(rows) == 1
    assert rows[0]["path"] == "data/alice/d1/c1/s1.md"
    conn.close()


def test_delete_fts_rows_for_doc(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    insert_fts_row(conn, "data/alice/d1/c1/s1.md", "A", "s", "k", "goblin content")
    insert_fts_row(conn, "data/alice/d1/c1/s2.md", "B", "s", "k", "orc content")
    insert_fts_row(conn, "data/alice/d2/c1/s1.md", "C", "s", "k", "dragon content")
    delete_fts_rows_for_doc(conn, "d1")
    rows = conn.execute("SELECT path FROM documents_fts").fetchall()
    assert len(rows) == 1
    assert rows[0]["path"] == "data/alice/d2/c1/s1.md"
    conn.close()


def test_update_doc_status(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "C")
    create_doc(conn, "d1", cid, "Book", "h")
    update_doc_status(conn, "d1", "processing")
    d = get_doc(conn, "d1")
    assert d["status"] == "processing"
    conn.close()


def test_delete_doc_removes_row_and_fts(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "C")
    create_doc(conn, "d1", cid, "Book", "h")
    insert_fts_row(conn, "data/alice/d1/c1/s1.md", "A", "s", "k", "content")
    delete_doc(conn, "d1")
    assert get_doc(conn, "d1") is None
    rows = conn.execute("SELECT path FROM documents_fts").fetchall()
    assert len(rows) == 0
    conn.close()