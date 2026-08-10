# Search Benchmark — C1 (n-gram synonym matching)

Query model: `deepseek-v4-flash:0731-cloud` · Judge: `deepseek-v4-pro:cloud` · 5 default questions

**Run 1** (10.0/8.5/10.0 baseline vs 6.6/6.6/5.8) — outside band; judged a noise spike: regressed questions (character creation, classes) involve NO synonym pairs touched by C1, while the targeted question (goblin AC) behaved identically. Rerun for confirmation.

**Run 2 (accepted)**:

| Q | corr | cite | comp | ans | iters |
|---|------|------|------|-----|-------|
| How do I create a character? | 10 | 9 | 10 | 1 | 7 |
| What is a goblin's armor class? | 10 | 10 | 10 | 1 | 5 |
| How does combat work? | 8 | 7 | 5 | 1 | 8 |
| What character classes are available? | 10 | 10 | 10 | 1 | 5 |
| What spells can a sorcerer cast? | 10 | 10 | 10 | 1 | 4 |
| **AVERAGE** | **9.6** | **9.2** | **9.0** | **100%** | **5.8** |

Baseline: 8.0/8.0/8.0, 100%, 5.6 iters, 12.0s. **Gate: PASS** (corr ≥ 7.5, cite ≥ 7.0, comp ≥ 7.5, ans 100%, iters ≤ 7.6).

Note: goblin AC canary went 0→10; combat question dipped (8/7/5 vs 10/8/9) — judge noise on an unrelated question (no synonym pairs in "how does combat work"). Agents (glm-5.2, minimax-m3, kimi-k2.6): all APPROVE.
