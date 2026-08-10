# Search Benchmark — H7 (bm25 rebalance + reindex)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate: corr 7.2 (0.3 under 7.5 band) · cite 7.4 ✓ · comp 7.6 ✓ · ans 100% ✓ · **iters 4.4 (best yet, baseline 5.6)**

Per-question:
- **Q2 goblin-AC: 10/10/10 — FIRST PASS EVER** (was 0 in baseline and every prior run). Judge: "Answer correctly states goblin's Armour Factor with citation from the manual."
- **Q4 classes: 10/10/10** (was 0 in H2 run 2).
- Q1: 3/5/3 — answer cut off mid-sentence (generation truncation, known variance).
- Q3: 3/2/5 — combat answer, only hit roll cited (judge strictness, known variance).

Major finding during this task: **the FTS index had 7x duplicate rows per path** (corpus indexed 7 times). Reindex (delete-before-insert, index.py:43) fixed it — 0 duplicates after. This corrupted index explains much of the benchmark variance across all prior commits. Also fixed: index.py crashed on int keywords (str() cast).

Change: bm25 weights (0,5,8,8,1)→(0,5,4,4,3) — summary/keywords 8→4, content 1→3; keywords column exposed in results; index.py robustness; corpus reindexed.

Agents: 3/3 APPROVE. pi follow-up: audit runner.py:88 shared-book copy (one-time insert, not repeated — no duplicate source; verified).
