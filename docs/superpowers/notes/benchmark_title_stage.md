# Search Benchmark — Title-Stage Ranking Fix (2026-08-11)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate: corr **7.2** · cite 5.6 · comp **9.2** · ans 100% · iters 5.2 · time 12.3s

vs post-reenrichment run: 5.2/5.2/6.2 → **corr +2.0, comp +3.0**.

Per-question:
- Q2 (goblin AC): **7/8/10** (was 0/2/0) — model searched 'goblin' → title matches → refined 'goblin armour class' → correct answer. Title stage works.
- Q1: 7/5/9, Q3: 5/2/7, Q5: 7/3/10 — citation-strictness variance (known class).
- Q4: 10/10/10 ✓

Change (agent-consulted, 3/3 APPROVE after claude's veto revision):
1. `build_query_cascade` prepends a title-only stage for single-term queries: `title:(OR of expanded synonym set)` — docs with the term in their title win before keyword/content ranking. Covers plurals/synonyms.
2. `_keyword_synonyms` narrowed from substring (`t in kw`) to tokenized equality (`t in kw.split()`) — stops 'goblin' matching 'goblinoids'.
3. `match_mode` derived from query content (title stage detection + stage offset) — fixes mislabel for multi-term queries.

Verified: 'character creation' now returns CHARACTER SHEET/A CHARACTER/PROFESSION (was DODGING). Note: dev corpus has no standalone goblin file — goblin stats live inside 24_halflings.md; the judge's "halfling source" penalty is partly a citation-path artifact.

Tests: 66 pass (query_builder + tools), 96 with agent_loop + citation_links.
