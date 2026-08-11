# Search Benchmark — Post Re-Enrichment (2026-08-11)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate: corr 5.2 · cite 5.2 · comp 6.2 · ans 100% · iters 5.0 · time 18.9s

Per-question:
- Q1 (character creation): 3/2/3 — judge: "only cites sources for characteristics and professions" (citation strictness, known variance).
- Q2 (goblin AC): 0/2/0 — model read `24_halflings.md` (top hit for 'goblin'; section legitimately covers goblins, but the goblin stat block ranks lower). **Residual ranking issue.**
- Q3 (combat): 3/2/8 — "only one citation complete" (known variance).
- Q4 (classes): 10/10/10 ✓
- Q5 (sorcerer): 10/10/10 ✓

Context: corpus re-enriched (dev 537/1283 partial, prod 4209/4209, test 2/2). Re-enrichment measurably improved keyword quality (DODGING no longer carries "character creation" keywords — old enrichment error fixed). Benchmark variance remains within the known 5.2–9.6 band; Q2 halfling-vs-goblin top-hit is the residual ranking problem (keyword expansion + content-weight interplay), tracked for agent consultation.

Tests: 106/106 pass (tools, query_builder, enrich, agent_loop, citation_links).
