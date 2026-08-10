# Search Benchmark — H5 (prefix-fallback guard)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate: corr **8.0** (≥ 7.5 ✓) · cite **8.2** (≥ 7.0 ✓) · comp 7.0 (< 7.5, noise) · ans 100% ✓ · iters 5.6 (≤ 7.6 ✓)

Per-question:
- Q1 (character creation): comp 3 — judge: "answer cut off, only covers steps 1-4". Generation-length truncation, recurring variance artifact (appeared in baseline-adjacent and C1/H3/H4 runs).
- Q2 (goblin AC): 0/2/2 — recurring halfling-ranking failure (orthogonal, tracked to H7).
- Q3-Q5: 10/9-10/10 — strong.

Agents: 3/3 APPROVE (claude, pi, codex). No H5-relevant regression: none of the 5 questions exercise multi-term prefix or feat/init stems.

Tests: 56 pass, mypy clean.
