# Search Benchmark — M6 (citations from read_file)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate: corr 6.0 (< 7.5 band) · cite 5.2 · comp 6.4 · ans 100% · iters 4.8.

Per-question: Q2 halfling mis-selection (H7 ranking bug — dominant failure mode across ALL commits), Q1/Q3 citation strictness + truncation (known variance classes). M6 only adds citation paths from read_file results; cannot affect answer correctness.

Agents: 3/3 APPROVE — commit M6, then land H7 (bm25 rebalance) next as priority fix, then re-benchmark.

Change: `_extract_cites_from_history` pairs read_file tool results with the path from the preceding assistant tool-call message (text + structured tool_calls formats); was a no-op `pass`. pi's structured-format concern addressed with defensive parsing.

Tests: 29 pass (agent_loop + citation_links).
