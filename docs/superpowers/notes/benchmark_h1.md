# Search Benchmark — H1 (cascade visibility: match_mode + hint + snippet 15)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate: 2 runs — corr 5.6 / 6.8 (band ≥ 7.5), cite 5.8 / 8.2, comp 7.0 / 8.2, ans 100%, iters 5.2 / 5.8.

Analysis (agent-verified 3/3 APPROVE):
- Q2 (goblin-AC) persistent failure root-caused in logs: `fts_search('goblin')` returns the CHAPTER file first ("Chapter 3: Men and Man...") because keywords-column weight 8 outranks the specific goblin stat block → H7 ranking bug, predates H1.
- Q1/Q5 dips = answer truncation + judge citation strictness — observed across all commits including baseline.
- match_mode/hint/snippet do not alter ranking or answer generation.

Change: per-result `match_mode` ("and"|"or"|"prefix"), hint item `{"match_mode":"none","hint":...}` for real unmatched queries (stop-word queries still return `[]` — `if not terms: return []` at tools.py:108), snippet 10→15 tokens. Two agent vetoes (codex: stop-word contract; pi: stage-bound) both retracted after evidence — both concerns already handled in code.

Tests: 49 pass (tools/agent_loop/citation_links), mypy clean (3 pre-existing errors on HEAD unchanged).
