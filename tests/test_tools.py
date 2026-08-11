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
    # stop-word-only queries tokenize to nothing and return []
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


def test_grep_paths_are_user_relative(toolbox):
    """grep result paths must be readable by read_file (user-relative, no user_id prefix)."""
    result = toolbox.grep("AC 15")
    assert len(result) >= 1
    path = result[0]["path"]
    assert not path.startswith("alice/"), f"grep path must not include user_id prefix: {path}"
    content = toolbox.read_file(path)
    assert "Goblin" in content


def test_grep_rejects_pathological_regex(toolbox):
    """Catastrophic-backtracking patterns must be rejected, not hang."""
    result = toolbox.grep("(a+)+$")
    assert result == [] or result == "error: pattern rejected"


def test_grep_rejects_oversized_pattern(toolbox):
    result = toolbox.grep("a" * 600)
    assert result == [] or result == "error: pattern rejected"


def test_grep_times_out_on_catastrophic_backtracking(toolbox):
    """A pattern that backtracks catastrophically must time out, not hang."""
    # Long line of 'a's with no 'b' — (a+)+b backtracks exponentially
    (toolbox.data_dir / "alice" / "d1" / "01_chapter" / "03_bomb.md").write_text(
        "a" * 60 + "\n"
    )
    import time
    t0 = time.monotonic()
    result = toolbox.grep("(a+)+b")
    elapsed = time.monotonic() - t0
    assert elapsed < 5, f"grep took {elapsed:.1f}s — ReDoS not mitigated"
    assert result == [] or isinstance(result, str)


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


def test_calc_rejects_huge_dice_count(toolbox):
    result = toolbox.calc("999999999d6")
    assert result.startswith("error"), f"expected error for huge dice count, got: {result}"


def test_calc_rejects_huge_dice_sides(toolbox):
    result = toolbox.calc("2d999999999")
    assert result.startswith("error"), f"expected error for huge dice sides, got: {result}"


def test_calc_rejects_oversized_expression(toolbox):
    result = toolbox.calc("1+1" * 500)
    assert result.startswith("error"), f"expected error for oversized expression, got: {result}"


def test_calc_rejects_compound_types(toolbox):
    result = toolbox.calc("[1,2,3]")
    assert result.startswith("error"), f"expected error for compound types, got: {result}"


def test_ls(toolbox):
    result = toolbox.ls(str(toolbox.data_dir / "alice" / "d1"))
    assert "index.md" in result


def test_fts_search_reports_match_mode(toolbox):
    results = toolbox.fts_search("goblin")
    assert results[0]["match_mode"] in ("title", "and", "or", "prefix")


def test_fts_search_empty_returns_hint_item(toolbox):
    # a real query with no matches anywhere returns a hint item, not []
    results = toolbox.fts_search("zzzzzzyxwv")
    assert len(results) == 1
    assert results[0]["match_mode"] == "none"
    assert "hint" in results[0]


def test_fts_search_results_expose_keywords(toolbox):
    results = toolbox.fts_search("goblin")
    assert "keywords" in results[0]


def test_fts_search_summary_truncated(toolbox):
    uconn = init_user_db(toolbox.db_dir, "alice")
    insert_fts_row(uconn, "d1/01_chapter/03_long.md", "Long", "S" * 5000, "long", "long content here")
    uconn.close()
    results = toolbox.fts_search("long")
    assert len(results[0]["summary"]) <= 320


def test_read_file_blocks_index_md(toolbox):
    result = toolbox.read_file(str(toolbox.data_dir / "alice" / "d1" / "index.md"))
    assert "index.md" in result
    assert "list_index" in result


def test_done_schema_requires_suggestions():
    from app.agent.tools_schema import FORCED_DONE_TOOLS
    props = FORCED_DONE_TOOLS[0]["function"]["parameters"]
    assert "suggestions" in props["required"]
    assert props["properties"]["suggestions"].get("minItems", 0) >= 3


def test_keyword_synonyms_only_expands_from_keyword_column(toolbox):
    # A query term that is a substring of a keyword phrase expands (t in kw);
    # a keyword that is a substring of the query term does NOT (kw in t dropped).
    uconn = init_user_db(toolbox.db_dir, "alice")
    insert_fts_row(uconn, "d1/01_chapter/04_spellcasting.md", "Spellcasting", "Spell rules.", "spellcasting,spell", "Spellcasting rules here.")
    uconn.close()
    results = toolbox.fts_search("spellcasting")
    assert isinstance(results, list)
    assert len(results) >= 1


def test_execute_dispatch(toolbox):
    result = toolbox.execute("fts_search", {"query": "goblin"})
    import json
    parsed = json.loads(result)
    assert len(parsed) >= 1