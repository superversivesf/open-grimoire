from pathlib import Path
from app.pipeline.tier import tier_document, slugify


def test_slugify_basic():
    assert slugify("Chapter 1: Combat!") == "chapter_1_combat"
    assert slugify("Magic & Spells") == "magic_spells"
    assert slugify("Chapter 1: Combat", 1) == "01_chapter_1_combat"


def test_tier_writes_doc_index(tmp_dirs):
    tree = [
        {"title": "Chapter 1: Combat", "level": 1, "page_start": 1, "page_end": 2, "text": "Combat rules.", "children": [
            {"title": "Initiative", "level": 2, "page_start": 1, "page_end": 1, "text": "Roll initiative.", "children": []},
        ]},
    ]
    leaves = tier_document(tree, tmp_dirs["data"], "d1", "Bestiary")
    doc_index = (tmp_dirs["data"] / "d1" / "index.md").read_text()
    assert "Bestiary" in doc_index
    assert "Chapter 1: Combat" in doc_index
    assert len(leaves) == 1
    assert "initiative" in leaves[0]


def test_tier_writes_chapter_index(tmp_dirs):
    tree = [
        {"title": "Combat", "level": 1, "page_start": 1, "page_end": 2, "text": "", "children": [
            {"title": "Initiative", "level": 2, "page_start": 1, "page_end": 1, "text": "Roll initiative.", "children": []},
            {"title": "Attacks", "level": 2, "page_start": 2, "page_end": 2, "text": "Attack rolls.", "children": []},
        ]},
    ]
    tier_document(tree, tmp_dirs["data"], "d1", "Book")
    chap_index = (tmp_dirs["data"] / "d1" / "01_combat" / "index.md").read_text()
    assert "Initiative" in chap_index
    assert "Attacks" in chap_index


def test_tier_leaf_has_content(tmp_dirs):
    tree = [
        {"title": "C1", "level": 1, "page_start": 1, "page_end": 1, "text": "content here", "children": []},
    ]
    leaves = tier_document(tree, tmp_dirs["data"], "d1", "Book")
    content = (tmp_dirs["data"] / leaves[0]).read_text()
    assert "content here" in content