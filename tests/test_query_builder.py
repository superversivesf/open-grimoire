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
    expanded = expand_terms(["spell"], {"spell": ["spellcasting", "sorcery"]})
    assert expanded[0] == {"spell", "spellcasting", "slot", "slots", "cantrip", "spell slot", "spell slots", "sorcery"}


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


def test_tokenize_protects_edition_numbers():
    assert tokenize_terms("What is a 3.5 fighter?") == ["3.5", "fighter"]


def test_tokenize_protects_dice_notation():
    assert tokenize_terms("roll 1d20+5") == ["roll", "1d20"]


def test_tokenize_drops_single_digit_tokens():
    assert tokenize_terms("level 5") == ["level"]


def test_tokenize_keeps_edition_suffix():
    assert tokenize_terms("5e rules") == ["5e", "rules"]


def test_tokenize_handles_3_5e_edition():
    assert tokenize_terms("3.5e fighter") == ["3.5e", "fighter"]


def test_tokenize_does_not_protect_fake_editions():
    assert tokenize_terms("2e10 damage") == ["2e10", "damage"]


def test_quote_preserves_dots_in_atomic_tokens():
    expanded = expand_terms(["3.5"])
    q = build_or_query(expanded)
    assert '"3.5"' in q


def test_synonym_group_advantage():
    assert expand_terms(["advantage"]) == [{"advantage", "adv", "disadvantage"}]


def test_synonym_group_ability_scores():
    expanded = expand_terms(["str"])
    assert "strength" in expanded[0]
    # short forms with common-English collisions stay plain tokens
    assert expand_terms(["con"]) == [{"con"}]
    assert expand_terms(["int"]) == [{"int"}]
    assert expand_terms(["wis"]) == [{"wis"}]


def test_synonym_group_dmg():
    assert expand_terms(["dmg"]) == [{"dmg", "damage", "damages"}]


def test_synonym_group_spell_slot():
    expanded = expand_terms(["spell", "slot"])
    assert len(expanded) == 1  # n-gram collapses into the spell group
    assert "cantrip" in expanded[0]


def test_stop_words_keep_will():
    assert tokenize_terms("will save") == ["save"]


def test_synonym_group_saving_singular():
    assert "saving" in expand_terms(["saving"])[0]


def test_expand_ngram_two_token_synonym():
    assert expand_terms(["saving", "throw"]) == [
        {"st", "save", "saves", "saving", "saving throw", "saving throws"}
    ]


def test_expand_ngram_collapses_to_single_group():
    assert len(expand_terms(["armor", "class"])) == 1
    assert expand_terms(["hit", "points"]) == [{"hp", "hit point", "hit points"}]


def test_expand_ngram_keeps_unmatched_adjacent_terms():
    assert expand_terms(["goblin", "knight"]) == [{"goblin"}, {"knight"}]


def test_expand_ngram_mixed_single_and_pair():
    expanded = expand_terms(["goblin", "saving", "throw"])
    assert expanded == [
        {"goblin"},
        {"st", "save", "saves", "saving", "saving throw", "saving throws"},
    ]


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
