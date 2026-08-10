# Search Benchmark — H2 (SYSTEM_PROMPT + tool description rewrite)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate: 2 runs — corr 4.8 / 7.0 (band ≥ 7.5), cite 5.2 / 7.2, comp 5.2 / 6.8, ans 80% / 100%, iters 5.0 / 6.0.

Analysis (agent-verified 3/3 APPROVE):
- Run 1 Q1 answered=0: `fts_search('character creation')` returned DODGING as top hit → model flailed (contents.md, ls, replay loop) → done with non-answer. Root cause: keyword-weight ranking (H7), predates H2.
- Run 2 Q4: `fts_search('character classes')` returned MAGICAL COMBAT top hit → same ranking bug.
- Model behavior matches new prompt guidance in both runs (search → read → done).
- goblin-AC scored 10/10/10 in run 2 (model got it right).

Change: SYSTEM_PROMPT rewritten (removed unactionable book-preference line, removed duplicated FTS mechanics, "too broad → more specific keyword", match_mode guidance); fts_search tool description updated (match_mode semantics, expanded abbreviation list). claude's wording nit fixed ("single keyword" vs "goblin ac" example).

Tests: 28 pass (agent_loop + citation_links).
