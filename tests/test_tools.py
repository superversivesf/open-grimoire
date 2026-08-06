import pytest
from pathlib import Path
from app.agent.tools import ToolBox
from app.storage.user_db import init_user_db, create_collection, create_doc, insert_fts_row


@pytest.fixture
def toolbox(tmp_dirs):
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Book", "h")
    insert_fts_row(uconn, "d1/01_chapter/01_goblin.md", "Goblin", "Goblin stats.", "goblin,monster", "Goblins are small humanoids with AC 15 and HP 7.")
    insert_fts_row(uconn, "d1/01_chapter/02_knight.md", "Knight", "Knight stats.", "knight,armor", "Knights are armored warriors with AC 16 and HP 13.")
    uconn.close()
    doc_dir = tmp_dirs["data"] / "alice" / "d1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "index.md").write_text("# Book\n\n- [Chapter 1](01_chapter/index.md)\n")
    chap = doc_dir / "01_chapter"
    chap.mkdir()
    (chap / "index.md").write_text("# Chapter 1\n\n- [Goblin](01_goblin.md)\n")
    (chap / "01_goblin.md").write_text("---\nsummary: \"Goblin stats.\"\nkeywords: [goblin]\npage: 42\n---\n\n# Goblin\n\n| Name | AC | HP |\n|------|----|----|\n| Goblin | 15 | 7 |\n\nAC 15, HP 7.\n")
    return ToolBox(tmp_dirs["data"], "alice", tmp_dirs["db"], cid)


def test_fts_search(toolbox):
    results = toolbox.fts_search("goblin")
    assert len(results) >= 1
    assert "goblin" in results[0]["path"].lower() or "Goblin" in results[0]["title"]


def test_fts_search_drops_stop_words(toolbox):
    results = toolbox.fts_search("how does the goblin work?")
    assert len(results) >= 1
    assert results[0]["title"] == "Goblin"


def test_fts_search_synonym_ac(toolbox):
    results = toolbox.fts_search("armor class")
    assert len(results) >= 1


def test_fts_search_results_have_summary_and_page(toolbox):
    results = toolbox.fts_search("goblin")
    assert results[0]["summary"] == "Goblin stats."
    assert results[0]["page"] == 42


def test_fts_search_and_then_or_fallback(toolbox):
    # Row 1 has goblin+ac; row 2 has knight+ac. "goblin knight" fails AND, succeeds OR.
    results = toolbox.fts_search("goblin knight")
    assert len(results) == 2


def test_fts_search_prefix_fallback(toolbox):
    results = toolbox.fts_search("gobli")  # no exact token; prefix cascade catches it
    assert len(results) >= 1


def test_fts_search_empty_query(toolbox):
    assert toolbox.fts_search("how what the") == []


def test_read_file(toolbox):
    result = toolbox.read_file(str(toolbox.data_dir / "alice" / "d1" / "01_chapter" / "01_goblin.md"))
    assert "Goblin" in result


def test_list_index(toolbox):
    result = toolbox.list_index(str(toolbox.data_dir / "alice" / "d1" / "index.md"))
    assert len(result) >= 1
    assert "Chapter 1" in result[0]["title"]


def test_grep(toolbox):
    result = toolbox.grep("AC 15")
    assert len(result) >= 1
    assert "AC 15" in result[0]["text"]


def test_table_extract(toolbox):
    result = toolbox.table_extract(str(toolbox.data_dir / "alice" / "d1" / "01_chapter" / "01_goblin.md"))
    assert len(result) >= 1
    assert result[0]["Name"] == "Goblin"
    assert result[0]["AC"] == "15"


def test_calc_dice(toolbox):
    result = toolbox.calc("2+3")
    assert "5" in result


def test_calc_dice_roll(toolbox):
    result = toolbox.calc("1d20+5")
    import re
    m = re.search(r"\d+", result)
    assert m is not None
    val = int(m.group(0))
    assert 6 <= val <= 25


def test_ls(toolbox):
    result = toolbox.ls(str(toolbox.data_dir / "alice" / "d1"))
    assert "index.md" in result


def test_execute_dispatch(toolbox):
    result = toolbox.execute("fts_search", {"query": "goblin"})
    import json
    parsed = json.loads(result)
    assert len(parsed) >= 1