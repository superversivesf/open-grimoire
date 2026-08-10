from app.pipeline.structure import Structurer


def test_detect_numbered_chapters():
    blocks = [
        {"page": 1, "text": "Chapter 1: Combat\nThe rules of combat."},
        {"page": 2, "text": "Chapter 2: Magic\nSpells and magic items."},
    ]
    s = Structurer()
    tree = s.detect(blocks)
    assert len(tree) == 2
    assert tree[0]["title"] == "Chapter 1: Combat"
    assert tree[0]["level"] == 1
    assert "rules of combat" in tree[0]["text"].lower()
    assert tree[1]["title"] == "Chapter 2: Magic"
    assert tree[1]["page_start"] == 2


def test_detect_all_caps_headings():
    blocks = [
        {"page": 1, "text": "COMBAT\nGoblins have AC 15."},
        {"page": 2, "text": "MAGIC\nFireball does 8d6."},
    ]
    s = Structurer()
    tree = s.detect(blocks)
    assert len(tree) == 2
    assert tree[0]["title"] == "COMBAT"
    assert tree[1]["title"] == "MAGIC"


def test_detect_subsections_numbered():
    blocks = [
        {"page": 1, "text": "Chapter 1: Combat\n1.1 Initiative\nRoll for initiative.\n1.2 Attacks\nAttack rolls."},
    ]
    s = Structurer()
    tree = s.detect(blocks)
    assert len(tree) == 1
    assert tree[0]["title"] == "Chapter 1: Combat"
    assert len(tree[0]["children"]) == 2
    assert tree[0]["children"][0]["title"] == "1.1 Initiative"
    assert tree[0]["children"][1]["title"] == "1.2 Attacks"


def test_no_structure_fallback_single_chapter():
    blocks = [
        {"page": 1, "text": "Just a bunch of text with no headings."},
        {"page": 2, "text": "More text here."},
    ]
    s = Structurer()
    tree = s.detect(blocks)
    assert len(tree) == 1
    assert tree[0]["level"] == 1
    assert tree[0]["page_start"] == 1
    assert tree[0]["page_end"] == 2


def test_heading_page_not_duplicated_across_chapters():
    """The page on which the next chapter's heading appears must belong to the
    new chapter only — not to both the previous and next chapter."""
    blocks = [
        {"page": 1, "text": "Chapter 1: Combat\nThe rules of combat."},
        {"page": 2, "text": "Chapter 2: Magic\nSpells and magic items."},
        {"page": 3, "text": "Chapter 3: Monsters\nBestiary entries."},
    ]
    s = Structurer()
    tree = s.detect(blocks)
    assert len(tree) == 3
    # Chapter 1 must not contain Chapter 2's heading or text
    assert "Chapter 2" not in tree[0]["text"]
    assert "Spells" not in tree[0]["text"]
    # Chapter 2 must not contain Chapter 3's heading or text
    assert "Chapter 3" not in tree[1]["text"]
    assert "Bestiary" not in tree[1]["text"]
    # Chapter 2 keeps its own content
    assert "Spells" in tree[1]["text"]
    # Last chapter keeps its final page
    assert "Bestiary" in tree[2]["text"]


def test_heading_line_itself_not_in_chapter_text():
    """The heading line is the node title; it should not be duplicated in the text."""
    blocks = [
        {"page": 1, "text": "Chapter 1: Combat\nThe rules of combat."},
    ]
    s = Structurer()
    tree = s.detect(blocks)
    assert tree[0]["title"] == "Chapter 1: Combat"
    assert "Chapter 1: Combat" not in tree[0]["text"]