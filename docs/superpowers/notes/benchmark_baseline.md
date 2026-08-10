# Search Benchmark — Baseline (2026-08-10)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions · `benchmarks/query_comparison.py`

| Q | corr | cite | comp | ans | iters | time(s) |
|---|------|------|------|-----|-------|---------|
| How do I create a character? | 10 | 10 | 10 | 1 | 7 | 14.8 |
| What is a goblin's armor class? | 0 | 2 | 1 | 1 | 4 | 8.5 |
| How does combat work? | 10 | 8 | 9 | 1 | 8 | 15.4 |
| What character classes are available? | 10 | 10 | 10 | 1 | 6 | 13.9 |
| What spells can a sorcerer cast? | 10 | 10 | 10 | 1 | 3 | 7.6 |
| **AVERAGE** | **8.0** | **8.0** | **8.0** | **100%** | **5.6** | **12.0** |

Full per-question results: `docs/superpowers/notes/2026-08-10-search-baseline.json`

Tolerance band (not-worse rule): corr ≥ 7.5, cite ≥ 7.0, comp ≥ 7.5, answered == 100%, iters ≤ 7.6.
