# Enrichment + Query Model Benchmark — 2026-08-06

Run after search improvements (plan: `.opencode/plans/2026-08-06-search-improvements.md`).
New enrich prompt (`ENRICH_PROMPT` in `app/pipeline/enrich.py`) used by benchmark
(`tests/enrich_comparison.py` now imports it instead of inlining).

## Enrichment benchmark

Command: `.venv/bin/python tests/enrich_comparison.py --mode enrich --samples 20 --models "phi4-mini:3.8b,deepseek-v4-flash:0731-cloud,qwen2.5:7b"`

| Model | JSON% | AvgScore | AvgTime | AvgWords | AvgKW | Topic% |
|-------|-------|----------|---------|----------|-------|--------|
| phi4-mini:3.8b | 100% | **0.81** | 1.1s | 28 | 6.8 | 50% |
| qwen2.5:7b | 100% | 0.64 | 1.9s | 36 | 11.8 | 60% |
| deepseek-v4-flash:0731-cloud | 100% | 0.51 | 2.4s | 62 | 10.7 | 55% |

Ranking: phi4-mini:3.8b > qwen2.5:7b > deepseek-v4-flash:0731-cloud

Notes: deepseek produces summaries too long (62 words avg vs optimal ~25) and too
many keywords (10.7 vs optimal ~6). phi4-mini is local/free and 2x faster.

## Query (Q&A) benchmark

Command: `.venv/bin/python tests/enrich_comparison.py --mode query --models "deepseek-v4-flash:0731-cloud,phi4-mini:3.8b"`

| Model | AvgScore | AvgTime | AvgIter | AvgCites | AvgWords | Done% |
|-------|----------|---------|---------|----------|----------|-------|
| deepseek-v4-flash:0731-cloud | **0.68** | 14.7s | 6.8 | 2.0 | 296 | 100% |
| phi4-mini:3.8b | 0.50 | 27.0s | 12.0 | 0.0 | 30 | 100% |

Ranking: deepseek-v4-flash:0731-cloud > phi4-mini:3.8b

Notes: phi4-mini exhausted its iteration budget on every question (iters=12),
produced 30-word answers with zero citations, and failed Q&A 2/5 in the
cross-model smoke test. Deepseek is the clear query model.

## Decision

- **Enrichment model: phi4-mini:3.8b** (was deepseek-v4-flash:0731-cloud in config.yaml; prod/test configs already used phi4-mini)
- **Query model: deepseek-v4-flash:0731-cloud** (unchanged)

config.yaml updated in commit 80c9a1b (enrich: deepseek-v4-flash:0731-cloud → phi4-mini:3.8b).
No re-enrichment of existing books needed — FTS index was rebuilt (9 docs) with the
previous enrichment; a production re-enrich with phi4-mini can happen opportunistically.

## Cross-model smoke test (search regression)

`tests/cross_model_test.py` after changes: ALL 5 questions return 5/5 FTS results
(LIMIT cap), including stop-word-heavy questions ("How does combat work?") that
previously returned 0 under quoted-AND semantics. Q&A rounds confirm deepseek
answers all questions with citations; phi4-mini timed out on 2/5.
