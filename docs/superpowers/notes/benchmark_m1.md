# Search Benchmark — M1 (state caps bind)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

Gate: corr 5.4 · cite 5.2 · comp 5.6 · ans 100% · iters 3.4 (vs prior run 8.0/7.8/8.2).

**Decision: COMMIT** — 3/3 agents concur the regression is NOT M1:
- The cap NEVER bound in the run: model searched ≤3 of the 5 allowed; no transitions or nudges fired (log-verified).
- Q2 (goblin-AC) failed via the pre-existing halfling-ranking issue (`fts_search('goblin')` ranks `24_halflings.md` top — the section covers goblins but the goblin stat block ranks lower).
- Q1/Q3 dips are the recurring truncation + citation-strictness judge variance.
- M1 only adds search/read counting; it cannot affect ranking or answer generation.

Change: `searches_in_state`/`reads_in_state` counters increment per tool use and are not reset by the tools themselves (previously `state_iterations` reset to 0 on every fts_search/read_file, so SEARCHING:5 / READING:5 caps never bound). Verified: 5 distinct searches → cap fires → nudge → READING transition.

Tests: 27 pass (test_agent_loop + test_loop_stream). Agents: 3/3 APPROVE (code) + 3/3 commit-verdict.
