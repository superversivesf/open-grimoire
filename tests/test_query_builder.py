from app.agent.query_builder import (
    tokenize_terms, expand_terms, build_and_query,
    build_or_query, build_query_cascade,
)


def test_tokenize_drops_stop_words():
    assert tokenize_terms("How does a goblin fight?") == ["goblin", "fight"]


def test_tokenize_drops_contraction_suffix():
    assert tokenize_terms("goblin's ac") == ["goblin", "ac"]


def test_cascade_no_single_letter_tokens():
    cascade = build_query_cascade(["goblin's", "ac"])
    assert '"s"' not in cascade[0]


def test_quote_lowercases_mixed_case():
    expanded = expand_terms(["Spell"], {"Spell": ["Fireball"]})
    q = build_or_query(expanded)
    assert '"spell"' in q and '"fireball"' in q


def test_tokenize_handles_hyphens_and_punct():
    assert tokenize_terms("pre-generated stat-block") == ["pre", "generated", "stat", "block"]


def test_expand_terms_keeps_plain_terms():
    assert expand_terms(["goblin"]) == [{"goblin"}]


def test_expand_terms_synonym_group():
    assert expand_terms(["ac"]) == [{"ac", "armor class", "armour class"}]
    assert expand_terms(["armor class"]) == [{"ac", "armor class", "armour class"}]


def test_expand_terms_merges_extra_synonyms():
    assert expand_terms(["spell"], {"spell": ["spellcasting", "sorcery"]}) == [
        {"spell", "spellcasting", "sorcery"}
    ]


def test_build_and_query_groups():
    expanded = expand_terms(["goblin", "ac"])
    q = build_and_query(expanded)
    assert "(" in q and " OR " in q
    assert '"goblin"' in q and '"ac"' in q and '"armor class"' in q


def test_build_or_query_prefix():
    expanded = expand_terms(["goblin", "hp"])
    q = build_or_query(expanded, prefix=True)
    assert '"goblin"*' in q and '"hp"*' not in q  # short terms no prefix


def test_cascade_order_strictest_first():
    cascade = build_query_cascade(["goblin", "ac"])
    assert len(cascade) == 3
    # 1: AND of groups; 2: OR; 3: OR with prefix
    assert " OR " in cascade[1]
    assert "*" in cascade[2]


def test_cascade_empty_for_all_stop_words():
    assert build_query_cascade(["how", "does", "the"]) == []


def test_cascade_outputs_execute_against_real_fts5():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(a, b, c)")
    conn.execute("INSERT INTO t (a, b, c) VALUES ('goblin', 'armor class', 'AC 15 hp 7')")
    for fts_query in build_query_cascade(["goblin", "ac"]):
        rows = conn.execute("SELECT a FROM t WHERE t MATCH ?", (fts_query,)).fetchall()
        assert isinstance(rows, list)
    # also single multi-word term with prefix path
    for fts_query in build_query_cascade(["armor class"]):
        conn.execute("SELECT a FROM t WHERE t MATCH ?", (fts_query,)).fetchall()
    conn.close()
