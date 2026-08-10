# Search Benchmark — H4 (synonym coverage)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate: 2 runs (first ran pre-veto code).

**Run 2 (accepted, revised code)**: corr 6.4 / cite 7.0 / comp 8.8 / 100% ans / 5.6 iters. corr below 7.5 band.

Analysis (agent-verified, 3/3 APPROVE):
- goblin-AC scored 0 — judge: "cites a halfling stat block (24_halflings.md) as a goblin's". Model searched, got results, but ranking put the halfling doc above the goblin doc. This failure predates H4 (recurred in baseline-adjacent runs) and is orthogonal to synonym expansion → tracked to H7 (bm25 keyword-weight rebalance).
- Other per-question dips (cut-off answer, citation counts) are judge variance; identical-code spread observed at 6.4→7.6 in H3 runs.

Revision during review: pi VETOed the first H4 diff — ability-score short forms (con/int/wis/cha) collide with common English ("con the wizard"), and `will` removal is wrong for Dragon Warriors (no Will saves; characteristics are Strength/Reflexes/Intelligence/Psychic Talent/Looks). Revised: ability group = {str, dex} + full names only; `will` stays in STOP_WORDS. Re-consulted: 3/3 APPROVE.

Tests: 53 pass, mypy clean.
