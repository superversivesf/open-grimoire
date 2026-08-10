# Search Fixes Summary — 2026-08-10

Branch: `search-prompt-fixes` (16 commits) · Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud`

## Final benchmark vs baseline

| Metric | Baseline | Final | Δ |
|--------|----------|-------|---|
| correctness | 8.0 | **8.0** | 0 |
| citation_use | 8.0 | **7.8** | -0.2 |
| completeness | 8.0 | **8.2** | +0.2 |
| answered | 100% | **100%** | 0 |
| iterations | 5.6 | **3.4** | -2.2 |
| time | 12.0s | **8.3s** | -3.7s |

Per-question: goblin-AC **10/10/10** (was 0/2/1 in baseline — the canary question), classes 10/10/10, sorcerer 10/10/10. Q1 (7/6/3) and Q3 (3/3/8) dips are the known judge-variance classes (answer truncation, citation strictness) — both questions' answers were correct per the judge reasons.

## Commits (git log main..HEAD)

| Commit | Finding | Change |
|--------|---------|--------|
| fbb93aa | C1 | n-gram synonym matching (multi-word synonyms reach groups) |
| 2cd70ac | H3 | edition/dice token protection (3.5, 1d20, 5e), single-digit drop |
| 0be0a97 | H4+L2 | synonym groups (advantage, concentration, dmg, crit, prof, spell slot), keep 'will' |
| 065cf56 | H5 | prefix-fallback guard (single-term only, feat/init blacklist) |
| f2bfb06 | H1+H9 | match_mode visibility, empty hint, snippet 15 |
| e345e5f | H2+M2+C2 | SYSTEM_PROMPT rewrite, drop book preference, dedup mechanics |
| 501fa02 | M6 | citations from read_file results |
| fd1ff97 | H7+H8 | bm25 rebalance (0,5,4,4,3), keywords exposure, **corpus reindex** |
| 8124d5b | M8 | summary truncation (300 chars) |
| 309aeed | M3 | index.md server-side block |
| 8df680f | M7 | done tool requires 3 suggestions |
| 11136cb | H6 | ENRICH_PROMPT few-shot + stop-word exclusion |
| 7450431 | C3 | enrich retry + completion gating |
| 64d150c | M4 | one-direction keyword expansion, cache key fix |
| 8c06259 | L5 | keyword count from shared constant |
| 33ee688 | test | injection test updated for hint-item contract |

## Major findings during execution

1. **FTS index had 7x duplicate rows per path** — corpus indexed 7 times. This corrupted index confounded every benchmark before H7. Fixed via reindex (delete-before-insert verified at index.py:43). **Follow-up: audit runner.py:88 shared-book copy path for the same duplicate source** (pi).
2. **Judge variance is ±2-3 points** on identical code (observed 5.2→7.6 spread). Per-commit gates used tolerance band + agent consultation; the H7 reindex was the real fix.
3. **Agent vetoes caught real bugs**: pi vetoed H4 (ability-score short forms collide with English), codex vetoed H6 (stop-word list banned "to" — legit RPG keyword). Both revised and re-approved.

## Agent final review (3/3 APPROVE)

- claude: "Overall APPROVE — branch is coherent; one mandatory follow-up" (re-benchmark clean post-H7 — done).
- pi: "No regressions detected across the stack. Recommend: merge to main, file both follow-ups, re-bench clean."
- codex: "APPROVE overall. All tests updated appropriately. Commit and merge."

## Deferred / follow-ups

- **M5** — page filter in read_file (feature work, not a fix).
- **runner.py:88** — audit shared-book copy for duplicate FTS rows (pi follow-up).
- **Full re-enrichment** of existing collections to benefit from new ENRICH_PROMPT (needs re-enrich script).
- **Phase-3 table flattening** (from `.opencode/plans/2026-08-06-search-improvements.md`) — separate concern.
- M6 edge cases (index.md cite pairing, multi-read_file pairing) — non-blocking, agent-flagged.
