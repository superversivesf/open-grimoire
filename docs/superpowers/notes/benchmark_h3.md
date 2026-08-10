# Search Benchmark — H3 (edition/dice token protection)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate result: 3 runs on identical code → corr 6.4 / 5.2 / 7.6 (baseline 8.0). Spread on identical code confirms judge/answer-selection variance.

Evidence of noise, not regression:
- All failure modes observed (goblin-AC answered with halfling stats 2/3 runs, character-creation answer cut off, combat citations incomplete) involve NO numeric/edition tokens — outside H3's scope.
- None of the 5 questions exercise `3.5`, `1d20`, `5e`, or single-digit queries.
- C1 run-2 with essentially identical pipeline code scored 9.6 avg.

Agent verdicts (3/3): commit H3; gate isn't measuring it (glm-5.2), variance is measurement noise (minimax-m3), commit + investigate benchmark stability separately (kimi-k2.6).

Also fixed during review: pi flagged `3.5e` bug (regex alternation) — fixed with `\d+\.\d+e\b` first + `[1-9]e\b` edition guard; added tests. Tests: 47 pass, mypy clean.
