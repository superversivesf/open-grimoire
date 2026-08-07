from pathlib import Path
from app.storage.user_db import init_user_db, insert_fts_row
from app.pipeline.index import index_document, parse_frontmatter


def test_parse_frontmatter(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("---\nsummary: \"A goblin.\"\nkeywords: [goblin, monster]\npage: 42\n---\n\n# Goblin\n\nAC 15.\n")
    fm, body = parse_frontmatter(f)
    assert fm["summary"] == "A goblin."
    assert "goblin" in fm["keywords"]
    assert fm["page"] == 42
    assert "# Goblin" in body


def test_parse_no_frontmatter(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("# No FM\n\nJust text.\n")
    fm, body = parse_frontmatter(f)
    assert fm == {}
    assert "Just text" in body


def test_index_document_inserts_rows(tmp_dirs):
    from app.storage.user_db import init_user_db
    doc_dir = tmp_dirs["data"] / "d1" / "01_chapter"
    doc_dir.mkdir(parents=True)
    leaf = doc_dir / "01_section.md"
    leaf.write_text("---\nsummary: \"Goblin stats.\"\nkeywords: [goblin, AC]\npage: 42\n---\n\n# Goblin\n\nAC 15, HP 7.\n")
    conn = init_user_db(tmp_dirs["db"], "alice")
    index_document(conn, [str(leaf.relative_to(tmp_dirs["data"]))], tmp_dirs["data"], "d1")
    rows = conn.execute("SELECT path, title, summary, keywords FROM documents_fts").fetchall()
    assert len(rows) == 1
    assert "goblin" in rows[0]["keywords"].lower()
    assert "AC 15" in rows[0]["summary"] or "Goblin stats" in rows[0]["summary"]
    conn.close()


def test_index_document_flattens_tables(tmp_dirs):
    from app.storage.user_db import init_user_db
    doc_dir = tmp_dirs["data"] / "d1" / "01_chapter"
    doc_dir.mkdir(parents=True)
    leaf = doc_dir / "01_section.md"
    leaf.write_text("---\nsummary: \"Goblin stats.\"\nkeywords: [goblin, AC]\npage: 42\n---\n\n# Goblin\n\n| Name | AC | HP |\n|------|----|----|\n| Goblin | 15 | 7 |\n")
    conn = init_user_db(tmp_dirs["db"], "alice")
    index_document(conn, [str(leaf.relative_to(tmp_dirs["data"]))], tmp_dirs["data"], "d1")
    row = conn.execute("SELECT content FROM documents_fts").fetchone()
    assert "|" not in row["content"]
    assert "Goblin 15 7" in row["content"]
    conn.close()


def test_index_document_replaces_old_rows(tmp_dirs):
    from app.storage.user_db import init_user_db, insert_fts_row
    conn = init_user_db(tmp_dirs["db"], "alice")
    # Use correct path format: doc_id/relative_path (no user prefix, no data prefix)
    insert_fts_row(conn, "d1/old.md", "Old", "s", "k", "old content")
    doc_dir = tmp_dirs["data"] / "d1" / "01_chapter"
    doc_dir.mkdir(parents=True)
    leaf = doc_dir / "01_section.md"
    leaf.write_text("# New\n\nNew content.\n")
    index_document(conn, [str(leaf.relative_to(tmp_dirs["data"]))], tmp_dirs["data"], "d1")
    rows = conn.execute("SELECT path FROM documents_fts").fetchall()
    assert len(rows) == 1
    assert "old" not in rows[0]["path"]
    conn.close()