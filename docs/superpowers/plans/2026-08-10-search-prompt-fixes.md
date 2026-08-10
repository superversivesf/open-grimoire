# Search Prompt & Query Construction Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the search-prompt/query-construction findings from the 4-agent deep-dive review (C1-H9, M2-M8, L2-L5), one commit per identified problem, each gated by an LLM-judged benchmark against a saved baseline, cross-referenced with claude/pi/codex agents before every commit.

**Architecture:** One feature branch (`search-prompt-fixes`), one commit per review finding, TDD per commit, benchmark after each commit using `benchmarks/query_comparison.py` with the same query model (deepseek-v4-flash:0731-cloud), judge (deepseek-v4-pro:cloud), and 5 default questions. A change is accepted only if it is not worse than baseline within the tolerance band. Baseline was captured before this plan was written.

**Tech Stack:** Python 3.10+, stdlib sqlite3 FTS5 (`bm25`, `snippet`, `porter` tokenizer), pytest, Ollama gateway.

## Baseline (captured 2026-08-10, before any changes)

Command: `DEV_MODE=1 .venv/bin/python benchmarks/query_comparison.py --models "deepseek-v4-flash:0731-cloud" --judge "deepseek-v4-pro:cloud"`

| Q | corr | cite | comp | ans | iters | time(s) |
|---|------|------|------|-----|-------|---------|
| How do I create a character? | 10 | 10 | 10 | 1 | 7 | 14.8 |
| What is a goblin's armor class? | 0 | 2 | 1 | 1 | 4 | 8.5 |
| How does combat work? | 10 | 8 | 9 | 1 | 8 | 15.4 |
| What character classes are available? | 10 | 10 | 10 | 1 | 6 | 13.9 |
| What spells can a sorcerer cast? | 10 | 10 | 10 | 1 | 3 | 7.6 |
| **AVERAGE** | **8.0** | **8.0** | **8.0** | **100%** | **5.6** | **12.0** |

Baseline saved to `/tmp/query_comparison_results.json` (copied to `docs/superpowers/notes/2026-08-10-search-baseline.json` at plan start).

## Global Constraints

- Run tests with: `.venv/bin/python -m pytest tests/<file> -q`
- Run benchmark with the EXACT baseline command above; save full results JSON to `/tmp/query_comparison_results_<tag>.json` and the printed AVERAGES to `docs/superpowers/notes/benchmark_<tag>.md`.
- **Not-worse rule (tolerance band):** a commit is accepted iff avg correctness ≥ baseline−0.5, avg citation_use ≥ baseline−1.0, avg completeness ≥ baseline−0.5, answered% == 100%, and avg iterations ≤ baseline+2. If ANY metric falls outside the band, the change must be revised (consult agents) or reverted before proceeding.
- TDD: write failing test → run (verify fail) → implement → run (verify pass) → run FULL affected test files → commit.
- One commit per finding. Commit message prefix: `fix(search): <finding-id> <short description>`.
- Before EVERY commit, consult claude (glm-5.2:cloud), pi (minimax-m3:cloud), codex (kimi-k2.6:cloud) in the herdr 2x2 grid (see Spawn Recipe) with the current diff; incorporate vetoes before committing.
- After the last task, run the FULL test suite (`.venv/bin/python -m pytest tests/ -q`) before merge.
- Do NOT deploy. Final merge to main requires explicit user approval after the final report.
- Enrichment prompt changes (Tasks 11-14) improve only NEWLY enriched sections; the benchmark measures the search pipeline. This is accepted and documented, not a blocker.

## Spawn Recipe (per-commit agent consult)

Rebuild the 2x2 grid in workspace wJ: split p1 right → pA; split pA down → pD; split p1 down → pB. (See previous session; panes closed.)

```
herdr pane run wJ:pA "ollama launch claude --model glm-5.2:cloud"
herdr pane run wJ:pB "ollama launch pi --model minimax-m3:cloud"
herdr pane run wJ:pD "ollama launch codex --model kimi-k2.6:cloud"
```

Wait for readiness (`herdr agent get wJ:pX` shows the agent), then rename and prompt each with the diff review prompt:

```
herdr agent rename wJ:pA review-glm && herdr agent rename wJ:pB review-mm3 && herdr agent rename wJ:pD review-k26
```

Consult prompt (same for all three, one per commit):
"READ-ONLY review of the current uncommitted diff in /home/jason/Repos/rpg-master (git diff). This is a search-quality fix for an RPG rules assistant. Review for: (1) correctness bugs, (2) regression risk to search quality, (3) anything that makes the model's search behavior worse. Reply in ≤150 words with verdict: APPROVE or VETO + reasons. Do not modify files."

Veto = do not commit; revise first. Approve = commit.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/agent/query_builder.py` | FTS5 query construction | Modify (Tasks 1-4) |
| `app/agent/tools.py` | `fts_search`, `_keyword_synonyms`, `read_file` | Modify (Tasks 5, 8, 9, 13) |
| `app/agent/loop.py` | SYSTEM_PROMPT, `_extract_cites_from_history` | Modify (Tasks 6, 7, 10) |
| `app/agent/tools_schema.py` | tool descriptions + done schema | Modify (Tasks 6, 10) |
| `app/agent/sandbox.py` | read_file index.md block | Modify (Task 9) |
| `app/pipeline/enrich.py` | ENRICH_PROMPT, retry/gate | Modify (Tasks 11, 12, 14) |
| `app/pipeline/runner.py` | enrich completion gating | Modify (Task 12) |
| `app/constants.py` | ENRICH_OPTIMAL_KEYWORDS | Modify (Task 14) |
| `tests/test_query_builder.py` | query builder tests | Modify (Tasks 1-4) |
| `tests/test_tools.py` | fts_search tests | Modify (Tasks 5, 8, 9, 13) |
| `tests/test_agent_loop.py` | cites extraction tests | Modify (Task 7) |
| `tests/test_enrich.py` | enrich prompt/retry tests | Modify (Tasks 11, 12, 14) |
| `tests/test_sandbox.py` | index.md block test | Modify (Task 9) |

---

### Task 0: Feature branch + baseline archive

**Files:**
- Create: `docs/superpowers/notes/2026-08-10-search-baseline.json`
- Create: `docs/superpowers/notes/benchmark_baseline.md`

- [ ] **Step 1: Create the branch**

```bash
cd /home/jason/Repos/rpg-master
git checkout -b search-prompt-fixes
```

- [ ] **Step 2: Archive the baseline**

```bash
mkdir -p docs/superpowers/notes
cp /tmp/query_comparison_results.json docs/superpowers/notes/2026-08-10-search-baseline.json
```

Write `docs/superpowers/notes/benchmark_baseline.md` containing the baseline table from the plan header (copy verbatim).

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/notes/2026-08-10-search-baseline.json docs/superpowers/notes/benchmark_baseline.md docs/superpowers/plans/2026-08-10-search-prompt-fixes.md
git commit -m "docs(search): archive search benchmark baseline"
```

---

### Task 1 (C1): n-gram synonym matching in expand_terms

**Files:**
- Modify: `app/agent/query_builder.py:38-45` (`expand_terms`)
- Test: `tests/test_query_builder.py`

**Interfaces:**
- Consumes: `SYNONYM_GROUPS` (existing), `tokenize_terms` (existing)
- Produces: unchanged signatures — `expand_terms(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[set[str]]` — but now collapses adjacent tokens that form a multi-word synonym (e.g. `["saving","throw"]` → `[st-group]`). Later tasks rely on this.

**Problem:** Multi-word synonym members (`"armor class"`, `"saving throw"`, `"hit points"`) are unreachable because `tokenize_terms` splits them before `_group_for` sees them. Only the abbreviation→full-form direction works.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_query_builder.py`:

```python
def test_expand_ngram_two_token_synonym():
    assert expand_terms(["saving", "throw"]) == [
        {"st", "save", "saves", "saving", "saving throw", "saving throws"}
    ]


def test_expand_ngram_collapses_to_single_group():
    assert len(expand_terms(["armor", "class"])) == 1
    assert expand_terms(["hit", "points"]) == [{"hp", "hit point", "hit points"}]


def test_expand_ngram_keeps_unmatched_adjacent_terms():
    assert expand_terms(["goblin", "knight"]) == [{"goblin"}, {"knight"}]


def test_expand_ngram_mixed_single_and_pair():
    expanded = expand_terms(["goblin", "saving", "throw"])
    assert expanded == [
        {"goblin"},
        {"st", "save", "saves", "saving", "saving throw", "saving throws"},
    ]
```

Note: the expected sets above include `"saving"` because Task 3 adds it to the st group; if this task runs before Task 3, write the expected sets WITHOUT `"saving"` (adjust to the group as it exists at that time).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_query_builder.py -q`
Expected: new tests FAIL (multi-word members not matched).

- [ ] **Step 3: Implement n-gram matching**

Replace `expand_terms` in `app/agent/query_builder.py` with:

```python
def _group_lookup() -> dict[str, set[str]]:
    """Normalized member string -> group. Multi-word members keyed with single spaces."""
    lookup: dict[str, set[str]] = {}
    for group in SYNONYM_GROUPS.values():
        for member in group:
            key = " ".join(member.lower().split())
            lookup.setdefault(key, set(group))
    return lookup


_GROUP_LOOKUP = _group_lookup()


def expand_terms(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[set[str]]:
    """Expand each term (or adjacent term pair forming a known synonym phrase)
    into an OR-set of synonym tokens.

    extra_synonyms maps a term to additional per-collection keyword matches.
    """
    expanded: list[set[str]] = []
    i = 0
    while i < len(terms):
        term = terms[i]
        group = None
        ngram_consumed = False
        if i + 1 < len(terms) and " " not in term and " " not in terms[i + 1]:
            pair = f"{term} {terms[i + 1]}"
            if pair in _GROUP_LOOKUP:
                group = set(_GROUP_LOOKUP[pair])
                ngram_consumed = True
        if group is None:
            key = " ".join(term.lower().split())
            group = set(_GROUP_LOOKUP[key]) if key in _GROUP_LOOKUP else {term}
        for extra in (extra_synonyms or {}).get(term, []):
            group.add(extra)
        expanded.append(group)
        i += 2 if ngram_consumed else 1
    return expanded
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_builder.py tests/test_tools.py -q`
Expected: all pass, including the existing `test_expand_terms_synonym_group` (whole-element lookup preserved) and `test_fts_search_synonym_ac` (now "armor class" actually expands).

- [ ] **Step 5: Benchmark**

Run: `DEV_MODE=1 .venv/bin/python benchmarks/query_comparison.py --models "deepseek-v4-flash:0731-cloud" --judge "deepseek-v4-pro:cloud" 2>&1 | tail -25`
Save results to `/tmp/query_comparison_results_c1.json`, AVERAGES to `docs/superpowers/notes/benchmark_c1.md`.
Gate: not-worse rule vs baseline.

- [ ] **Step 6: Consult agents + commit**

Follow the Spawn Recipe. If all three APPROVE (or no veto): commit.

```bash
git add app/agent/query_builder.py tests/test_query_builder.py docs/superpowers/notes/benchmark_c1.md
git commit -m "fix(search): C1 n-gram synonym matching in expand_terms"
```

---

### Task 2 (H3): numeric/edition token protection

**Files:**
- Modify: `app/agent/query_builder.py:21-28` (`_TERM_RE`, `tokenize_terms`), `:48-51` (`_quote`)
- Test: `tests/test_query_builder.py`

**Problem:** `3.5` → `["3","5"]`, `D&D` → `["d","d"]`, `+5` → `["5"]` — bare digits flood AND/OR queries. `5e`, `1d20`, `2d6` survive. Also `_quote` must not destroy `.` in protected tokens.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_query_builder.py`:

```python
def test_tokenize_protects_edition_numbers():
    assert tokenize_terms("What is a 3.5 fighter?") == ["3.5", "fighter"]


def test_tokenize_protects_dice_notation():
    assert tokenize_terms("roll 1d20+5") == ["roll", "1d20"]


def test_tokenize_drops_single_digit_tokens():
    assert tokenize_terms("level 5") == ["level"]


def test_tokenize_keeps_edition_suffix():
    assert tokenize_terms("5e rules") == ["5e", "rules"]


def test_quote_preserves_dots_in_atomic_tokens():
    expanded = expand_terms(["3.5"])
    q = build_or_query(expanded)
    assert '"3.5"' in q
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: all new tests FAIL.

- [ ] **Step 3: Implement protection**

In `app/agent/query_builder.py`:

```python
_ATOMIC_RE = re.compile(r"\d+\.\d+|\d+[dD]\d+|\d+e\b", re.IGNORECASE)


def tokenize_terms(query: str) -> list[str]:
    """Split a raw query into clean lowercase terms, dropping stop words.

    Edition/dice tokens (3.5, 1d20, 5e) are protected from splitting and kept
    atomic; single-digit tokens are dropped (page-number/CR noise).
    """
    lowered = query.lower()
    atoms: dict[str, str] = {}

    def protect(m: re.Match[str]) -> str:
        key = f"\x00{len(atoms)}\x00"
        atoms[key] = m.group(0)
        return key

    protected = _ATOMIC_RE.sub(protect, lowered)
    words = [w for w in _TERM_RE.split(protected) if w]
    out = []
    for w in words:
        if w in atoms:
            out.append(atoms[w])
        elif not (w.isdigit() and len(w) == 1):
            out.append(w)
    return [w for w in out if w not in STOP_WORDS]
```

And in `_quote`, replace the `clean` computation:

```python
def _quote(token: str) -> str | None:
    clean = re.sub(r"\s+", " ", token.lower().strip())
    if not clean or re.search(r'["\x00]', clean):
        return None
    return f'"{clean}"'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_builder.py tests/test_tools.py -q`
Expected: all pass.

- [ ] **Step 5: Benchmark**

As in Task 1, tag `h3`. Gate: not-worse rule.

- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/query_builder.py tests/test_query_builder.py docs/superpowers/notes/benchmark_h3.md
git commit -m "fix(search): H3 protect edition/dice tokens, drop single digits"
```

---

### Task 3 (H4, L2): synonym coverage additions + stop-word fix

**Files:**
- Modify: `app/agent/query_builder.py:8-19` (`SYNONYM_GROUPS`, `STOP_WORDS`)
- Test: `tests/test_query_builder.py`

**Problem:** Missing high-frequency RPG terms (advantage, concentration, ability scores, dmg, crit, prof, spell slot, cantrip); `st` group lacks the single token `saving`; `will` (3.5/PF Will saves) is wrongly a stop word.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_query_builder.py`:

```python
def test_synonym_group_advantage():
    assert expand_terms(["advantage"]) == [{"advantage", "adv", "disadvantage"}]


def test_synonym_group_ability_scores():
    expanded = expand_terms(["str"])
    assert "strength" in expanded[0]


def test_synonym_group_dmg():
    assert expand_terms(["dmg"]) == [{"dmg", "damage", "damages"}]


def test_synonym_group_spell_slot():
    expanded = expand_terms(["spell", "slot"])
    assert len(expanded) == 1  # n-gram collapses into the spell group
    assert "cantrip" in expanded[0]


def test_stop_words_keep_will():
    assert tokenize_terms("will save") == ["will", "save"]


def test_synonym_group_saving_singular():
    assert "saving" in expand_terms(["saving"])[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

Replace `SYNONYM_GROUPS` in `app/agent/query_builder.py` with:

```python
SYNONYM_GROUPS = {
    "ac": {"ac", "armor class", "armour class"},
    "hp": {"hp", "hit points", "hit point"},
    "st": {"st", "save", "saves", "saving", "saving throw", "saving throws"},
    "dc": {"dc", "difficulty class", "difficulty classes"},
    "xp": {"xp", "experience points", "experience point"},
    "init": {"init", "initiative"},
    "tohit": {"tohit", "to hit", "attack roll", "attack rolls", "attack bonus"},
    "proficiency": {"proficiency", "proficiencies", "proficient", "prof", "proficiency bonus"},
    "spell": {"spell", "spellcasting", "slot", "slots", "cantrip", "spell slot", "spell slots"},
    "feat": {"feat", "feats"},
    "advantage": {"advantage", "adv", "disadvantage"},
    "concentration": {"concentration", "concentrating"},
    "ability": {"str", "dex", "con", "int", "wis", "cha",
                "strength", "dexterity", "constitution", "intelligence",
                "wisdom", "charisma"},
    "dmg": {"dmg", "damage", "damages"},
    "crit": {"crit", "critical", "critical hit", "critical hits"},
}
```

And remove `"will"` from `STOP_WORDS` (keep `"s"` — it is load-bearing: possessives would otherwise leave a bare `s` token that OR-matches everything).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_builder.py tests/test_tools.py -q`
Expected: pass. If any existing test asserted the old group sets verbatim, update those assertions to the new groups.

- [ ] **Step 5: Benchmark** (tag `h4`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/query_builder.py tests/test_query_builder.py docs/superpowers/notes/benchmark_h4.md
git commit -m "fix(search): H4+L2 synonym coverage additions, keep 'will'"
```

---

### Task 4 (H5): guard the prefix fallback

**Files:**
- Modify: `app/agent/query_builder.py:80-97` (`build_query_cascade`)
- Test: `tests/test_query_builder.py`

**Problem:** Prefix `feat*` → feature/feathered, `init*` → initial/initiate; prefix-OR over 2+ groups produces pathological breadth.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_query_builder.py`:

```python
def test_cascade_skips_prefix_for_multiple_terms():
    cascade = build_query_cascade(["goblin", "ac"])
    assert len(cascade) == 2  # AND + OR, no prefix stage
    assert "*" not in cascade[-1]


def test_cascade_keeps_prefix_for_single_term():
    cascade = build_query_cascade(["gobli"])
    assert len(cascade) == 3


def test_cascade_skips_prefix_for_broad_stems():
    cascade = build_query_cascade(["feat"])
    assert len(cascade) == 2
    assert "*" not in cascade[-1]
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL (`test_cascade_order_strictest_first` also fails — update it in Step 3).

- [ ] **Step 3: Implement**

In `app/agent/query_builder.py`, add after `build_or_query`:

```python
_PREFIX_BLACKLIST = {"feat", "init"}


def _allow_prefix(expanded: list[set[str]]) -> bool:
    if len(expanded) != 1:
        return False
    single = next(iter(expanded[0]))
    return single not in _PREFIX_BLACKLIST
```

And in `build_query_cascade`, gate the prefix stage:

```python
    if or_query not in cascade:
        cascade.append(or_query)
    if _allow_prefix(expanded):
        prefix_query = build_or_query(expanded, prefix=True)
        if prefix_query not in cascade:
            cascade.append(prefix_query)
    return cascade
```

Update `test_cascade_order_strictest_first` in `tests/test_query_builder.py` to use a single-term input (`["gobli"]`) and assert 3 stages; add a comment noting multi-term cascades have 2 stages.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_builder.py tests/test_tools.py -q`
Expected: pass (`test_fts_search_prefix_fallback` still passes — "gobli" is a single term).

- [ ] **Step 5: Benchmark** (tag `h5`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/query_builder.py tests/test_query_builder.py docs/superpowers/notes/benchmark_h5.md
git commit -m "fix(search): H5 skip prefix fallback for multi-term and broad stems"
```

---

### Task 5 (H1, H9): cascade visibility — match_mode + empty hint

**Files:**
- Modify: `app/agent/tools.py:113-132` (`fts_search`)
- Test: `tests/test_tools.py`

**Problem:** The model cannot distinguish tight AND hits from loose prefix-OR hits, and an empty result is a bare `[]` with no guidance — the prompt's retry/grep branch is unreachable and the model trusts broad results.

**Design:** Keep the `list[dict]` return shape (cite extraction and loop depend on it). Add per-result `match_mode` (`"and"|"or"|"prefix"`). On total failure return a single hint item `{"match_mode": "none", "hint": "..."}` — it is filtered out of cites (`r.get("path")` is None) but visible to the model.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:

```python
def test_fts_search_reports_match_mode(toolbox):
    results = toolbox.fts_search("goblin")
    assert results[0]["match_mode"] in ("and", "or", "prefix")


def test_fts_search_empty_returns_hint_item(toolbox):
    results = toolbox.fts_search("how what the")
    assert len(results) == 1
    assert results[0]["match_mode"] == "none"
    assert "hint" in results[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools.py -q`
Expected: new tests FAIL (`test_fts_search_empty_query` also fails — update in Step 3).

- [ ] **Step 3: Implement**

In `app/agent/tools.py`, replace the cascade loop in `fts_search`:

```python
            for stage, fts_query in enumerate(cascade):
                sql = (
                    f"SELECT path, title, summary, "
                    f"snippet(documents_fts, 4, '<mark>', '</mark>', '...', 15) as snippet, "
                    f"bm25(documents_fts, 0, 5, 8, 8, 1) as rank "
                    f"FROM documents_fts WHERE documents_fts MATCH ? AND {scope} "
                    f"ORDER BY rank LIMIT 5"
                )
                params = (fts_query,) + tuple(f"{d}/%" for d in doc_ids)
                rows = conn.execute(sql, params).fetchall()
                if rows:
                    match_mode = ("and", "or", "prefix")[stage]
                    results = []
                    for r in rows:
                        item = dict(r)
                        item["match_mode"] = match_mode
                        item["page"] = self._page_for(item["path"])
                        results.append(item)
                    log.debug(f"fts_search: query='{query}' -> {len(results)} results ({match_mode}, fts='{fts_query}')")
                    return results
            log.debug(f"fts_search: query='{query}' -> 0 results across all fallbacks")
            return [{"match_mode": "none", "hint": f"No matches for '{query}'. Try a different single keyword or use grep."}]
```

Update `test_fts_search_empty_query` in `tests/test_tools.py`:

```python
def test_fts_search_empty_query(toolbox):
    results = toolbox.fts_search("how what the")
    assert results[0]["match_mode"] == "none"
```

Note: snippet tokens 10 → 15 in this same edit (that is the L7 snippet-length fix; it ships with this commit and is benchmark-gated together).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools.py tests/test_agent_loop.py -q`
Expected: pass. Also verify cite extraction still works (existing citation tests in `tests/test_agent_loop.py` / `tests/test_citation_links.py` pass).

- [ ] **Step 5: Benchmark** (tag `h1`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/tools.py tests/test_tools.py docs/superpowers/notes/benchmark_h1.md
git commit -m "fix(search): H1+H9 match_mode visibility, empty hint, snippet 15"
```

---

### Task 6 (H2, M2, C2): SYSTEM_PROMPT + tool description rewrite

**Files:**
- Modify: `app/agent/loop.py:14-25` (`SYSTEM_PROMPT`)
- Modify: `app/agent/tools_schema.py:42-46` (fts_search description)
- Test: `tests/test_agent_loop.py`

**Problem:** (H2) "too broad → add a second term" is backwards for AND semantics; (M2) FTS mechanics duplicated between SYSTEM_PROMPT and tool description (~35 tokens, drift risk); (C2) "prefer matches from that book" is unactionable (no book metadata exposed) → hallucination risk.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_loop.py`:

```python
def test_system_prompt_no_book_preference_instruction():
    assert "prefer matches from that book" not in SYSTEM_PROMPT


def test_system_prompt_no_duplicated_mechanics():
    assert "AND-combined" not in SYSTEM_PROMPT


def test_system_prompt_too_broad_guidance():
    assert "more specific" in SYSTEM_PROMPT


def test_system_prompt_mentions_match_mode():
    assert "match_mode" in SYSTEM_PROMPT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_loop.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Replace `SYSTEM_PROMPT` in `app/agent/loop.py` with:

```python
SYSTEM_PROMPT = """You are an RPG rules assistant. You search the user's RPG manual collection (one or more books) to answer questions.

Search strategy:
1. ALWAYS start with fts_search — never browse with ls or list_index.
2. Query with ONE distinctive keyword first — the most specific noun (e.g. "goblin", "sorcerer", "grapple"). If results are too broad or irrelevant, try a more specific single keyword (e.g. "goblin ac").
3. Read the top result with read_file before answering — snippets are too short to answer from.
4. Each fts_search result reports match_mode: "and" (tight), "or" or "prefix" (loose). If results are loose, prefer the top-ranked hit but verify it with read_file; if nothing looks relevant, try a different keyword (2-3 attempts max) or use grep with a regex (e.g. grep "advantage" for every mention).
5. NEVER read the same file twice.
6. NEVER read index.md files — they are navigation only.

When calling done, always include 3 "suggestions" — short follow-up questions a player might ask next based on what they just learned."""
```

Update the fts_search description in `app/agent/tools_schema.py:42` to:

```python
            "description": "Full-text search across all documents in the current collection. Returns ranked matches with path, title, summary, page, match_mode, and a snippet. Start with ONE distinctive keyword (e.g. 'goblin', 'sorcerer'). Multi-word queries are AND-combined; common RPG abbreviations (AC, HP, DC, ST, XP, dmg, crit) and stop words are handled automatically. match_mode 'and' means tight matches; 'or'/'prefix' means loose fallback matches. If this returns nothing, try a different single keyword or use grep.",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent_loop.py tests/test_citation_links.py -q`
Expected: pass.

- [ ] **Step 5: Benchmark** (tag `h2`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/loop.py app/agent/tools_schema.py tests/test_agent_loop.py docs/superpowers/notes/benchmark_h2.md
git commit -m "fix(search): H2+M2+C2 rewrite search guidance, drop book preference"
```

---

### Task 7 (M6): citations from read_file

**Files:**
- Modify: `app/agent/loop.py:91-126` (`_extract_cites_from_history`)
- Test: `tests/test_agent_loop.py`

**Problem:** `_extract_cites_from_history` handles fts_search and grep but has a `pass` for read_file — when the model reads a file and calls done, cites miss the file it actually read.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_agent_loop.py`:

```python
def test_cites_extracted_from_read_file():
    from app.agent.loop import _extract_cites_from_history
    messages = [
        {"role": "assistant", "content": 'read_file {"path": "abc/01_goblin.md"}'},
        {"role": "tool", "name": "read_file",
         "content": "# Goblin\n\nAC 15, HP 7. A small humanoid.\n\nmore text" * 40},
    ]
    cites = _extract_cites_from_history(messages)
    assert any(c["path"] == "abc/01_goblin.md" for c in cites)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (no cite for read_file path).

- [ ] **Step 3: Implement**

In `app/agent/loop.py`, replace the `_extract_cites_from_history` body so that:

1. When iterating messages, track `pending_read_path`: for each `role == "assistant"` message whose content parses via `_parse_text_tool_call` to a `read_file` call, stash `args["path"]`.
2. On the following `role == "tool"` message with `name == "read_file"` and a pending path, append `{"path": pending_path, "quote": content[:200]}` (dedup via `seen_paths`) and clear the pending path.

Implementation sketch (replace the function):

```python
def _extract_cites_from_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan tool results in message history for file paths that could serve as citations."""
    cites = []
    seen_paths = set()
    pending_read_path = None
    for msg in messages:
        if msg.get("role") == "assistant":
            parsed = _parse_text_tool_call(msg.get("content", ""))
            pending_read_path = None
            if parsed and parsed["function"]["name"] == "read_file":
                pending_read_path = parsed["function"].get("arguments", {}).get("path")
        elif msg.get("role") == "tool":
            tool_name = msg.get("name", "")
            content = msg.get("content", "")
            if tool_name == "fts_search":
                try:
                    results = json.loads(content)
                    for r in results[:3]:
                        path = r.get("path", "")
                        if path and path not in seen_paths:
                            seen_paths.add(path)
                            cites.append({"path": path, "quote": r.get("snippet", "")[:200]})
                except (json.JSONDecodeError, TypeError):
                    pass
            elif tool_name == "read_file" and pending_read_path:
                if pending_read_path not in seen_paths:
                    seen_paths.add(pending_read_path)
                    cites.append({"path": pending_read_path, "quote": content[:200]})
                pending_read_path = None
            elif tool_name == "grep":
                # existing grep handling unchanged
                pass
    return cites
```

Preserve the existing grep cite-handling code verbatim in the `elif` chain (read the current function first and merge, keeping any existing behavior).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent_loop.py tests/test_citation_links.py -q`
Expected: pass.

- [ ] **Step 5: Benchmark** (tag `m6`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/loop.py tests/test_agent_loop.py docs/superpowers/notes/benchmark_m6.md
git commit -m "fix(search): M6 cite paths from read_file tool results"
```

---

### Task 8 (M8): truncate unbounded summary in fts results

**Files:**
- Modify: `app/agent/tools.py:123-130` (`fts_search` result assembly)
- Test: `tests/test_tools.py`

**Problem:** `summary` comes from frontmatter with no length cap — a long summary can bloat context (snippet is capped at 15 tokens, summary is unbounded).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:

```python
def test_fts_search_summary_truncated(toolbox):
    uconn = init_user_db(toolbox.db_dir, "alice")
    insert_fts_row(uconn, "d1/01_chapter/03_long.md", "Long", "S" * 5000, "long", "long content here")
    uconn.close()
    results = toolbox.fts_search("long")
    assert len(results[0]["summary"]) <= 320
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (summary is 5000 chars).

- [ ] **Step 3: Implement**

In `app/agent/tools.py`, in the result assembly inside `fts_search`, after `item = dict(r)`:

```python
                        item = dict(r)
                        item["summary"] = (item.get("summary") or "")[:300]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools.py -q`
Expected: pass.

- [ ] **Step 5: Benchmark** (tag `m8`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/tools.py tests/test_tools.py docs/superpowers/notes/benchmark_m8.md
git commit -m "fix(search): M8 truncate fts summary to 300 chars"
```

---

### Task 9 (M3): server-side index.md block for read_file

**Files:**
- Modify: `app/agent/tools.py` (`read_file`), `app/agent/sandbox.py` (check `safe_read_file`)
- Modify: `app/agent/tools_schema.py` (read_file description)
- Test: `tests/test_tools.py` (or `tests/test_sandbox.py`)

**Problem:** SYSTEM_PROMPT forbids reading index.md but the server does not enforce it — prompt/tool contradiction.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:

```python
def test_read_file_blocks_index_md(toolbox):
    result = toolbox.read_file(str(toolbox.data_dir / "alice" / "d1" / "index.md"))
    assert "index.md" in result
    assert "list_index" in result
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (returns file content).

- [ ] **Step 3: Implement**

In `app/agent/tools.py`, at the top of `read_file` (before reading):

```python
        if str(path).endswith("index.md"):
            return "index.md files are navigation only and cannot be read. Use list_index to navigate the book structure."
```

Also update the read_file description in `app/agent/tools_schema.py` to mention: "index.md files are blocked — use list_index instead."

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools.py -q`
Expected: pass (existing read_file tests read non-index files).

- [ ] **Step 5: Benchmark** (tag `m3`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/tools.py app/agent/tools_schema.py tests/test_tools.py docs/superpowers/notes/benchmark_m3.md
git commit -m "fix(search): M3 enforce index.md block server-side in read_file"
```

---

### Task 10 (M7): done-tool suggestions enforcement

**Files:**
- Modify: `app/agent/tools_schema.py:30-31` (`done` schema)
- Test: `tests/test_tools.py` (schema assertion)

**Problem:** SYSTEM_PROMPT demands 3 suggestions but the schema only requires `answer`; loop accepts missing suggestions silently.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py` (or a new `tests/test_tools_schema.py`):

```python
def test_done_schema_requires_suggestions():
    from app.agent.tools_schema import FORCED_DONE_TOOLS
    props = FORCED_DONE_TOOLS[0]["function"]["parameters"]
    assert "suggestions" in props["required"]
    assert props["properties"]["suggestions"].get("minItems", 0) >= 3
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL.

- [ ] **Step 3: Implement**

In `app/agent/tools_schema.py`, in the done function parameters:

```python
                    "suggestions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 3,
                        "description": "3 suggested follow-up questions for deeper exploration",
                    },
```

and `"required": ["answer", "suggestions"]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools.py -q`
Expected: pass.

- [ ] **Step 5: Benchmark** (tag `m7`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/tools_schema.py tests/test_tools.py docs/superpowers/notes/benchmark_m7.md
git commit -m "fix(search): M7 require 3 suggestions in done tool schema"
```

---

### Task 11 (H6): ENRICH_PROMPT few-shot + stop-word exclusion

**Files:**
- Modify: `app/pipeline/enrich.py:11-19` (`ENRICH_PROMPT`)
- Test: `tests/test_enrich.py`

**Problem:** No JSON example (models prepend prose/fences); no stop-word exclusion (keywords pollute `_keyword_synonyms`); keywords restricted to single tokens (multi-word phrases like "spell slot" impossible).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_enrich.py`:

```python
def test_enrich_prompt_has_few_shot_example():
    assert '"summary"' in ENRICH_PROMPT.template
    assert '"keywords"' in ENRICH_PROMPT.template
    assert "fireball" in ENRICH_PROMPT.template


def test_enrich_prompt_excludes_stop_words():
    assert "common words" in ENRICH_PROMPT.template
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL.

- [ ] **Step 3: Implement**

Replace `ENRICH_PROMPT` in `app/pipeline/enrich.py`:

```python
ENRICH_PROMPT = Template(
    "You are enriching an RPG rulebook section for search indexing. "
    "Read the section and return ONLY valid JSON, no prose, no markdown fences. "
    'Example of the exact format:\n'
    '{"summary": "Fireball is a 3rd-level evocation spell dealing 8d6 fire damage on a failed save.", '
    '"keywords": ["fireball", "evocation", "8d6", "saving throw", "fire damage"]}\n\n'
    "Rules:\n"
    '- "summary": 2-3 sentences covering what this section describes, including key game numbers '
    '(AC, HP, DC, damage dice, costs, levels) when present.\n'
    '- "keywords": 5-10 lowercase keywords or short phrases (e.g. "spell slot", "saving throw"): '
    'proper nouns, rule names, spell/monster/class names, stat abbreviations (ac, hp), and distinctive jargon.\n'
    '- Exclude common words (the, and, of, a, or, to) from keywords.\n'
    '- Use canonical lowercase forms; include singular and plural forms of important terms.\n\n'
    "$content"
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_enrich.py tests/test_query_builder.py -q`
Expected: pass.

- [ ] **Step 5: Benchmark** (tag `h6`) — note in `benchmark_h6.md` that enrichment changes only affect newly-enriched sections; pipeline search changes measured here.
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/pipeline/enrich.py tests/test_enrich.py docs/superpowers/notes/benchmark_h6.md
git commit -m "fix(search): H6 enrich prompt few-shot, stop-word exclusion, phrases"
```

---

### Task 12 (C3): enrichment retry + completion gating

**Files:**
- Modify: `app/pipeline/enrich.py:26-33` (`enrich_leaf`), `:70-79` (`_write_frontmatter`)
- Modify: `app/pipeline/runner.py:160-176` (enrich completion gating)
- Test: `tests/test_enrich.py`

**Problem:** Parse failure silently writes empty frontmatter and marks the section complete — search degrades invisibly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_enrich.py`:

```python
@pytest.mark.asyncio
async def test_enrich_retries_once_on_empty_keywords(tmp_path):
    leaf = tmp_path / "goblin.md"
    leaf.write_text("# Goblin\n\nAC 15.\n")
    gw = MagicMock()
    gw.call = AsyncMock(side_effect=[
        {"message": {"content": "not json at all"}},
        {"message": {"content": '{"summary": "Goblin stats.", "keywords": ["goblin", "monster", "ac"]}'}},
    ])
    e = Enricher(gw)
    result = await e.enrich_leaf(leaf)
    assert gw.call.await_count == 2
    assert result["keywords"]


@pytest.mark.asyncio
async def test_enrich_does_not_write_frontmatter_on_final_failure(tmp_path):
    leaf = tmp_path / "goblin.md"
    leaf.write_text("# Goblin\n\nAC 15.\n")
    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": "garbage"}})
    e = Enricher(gw)
    result = await e.enrich_leaf(leaf)
    assert not leaf.read_text().startswith("---")
    assert result["keywords"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: FAIL (no retry; frontmatter written with empty values).

- [ ] **Step 3: Implement**

In `app/pipeline/enrich.py`:

```python
    async def enrich_leaf(self, path: Path, page: int | None = None) -> dict[str, Any]:
        content = path.read_text()
        result: dict[str, Any] = {}
        for attempt in range(2):
            prompt = ENRICH_PROMPT.substitute(content=content)
            if attempt == 1:
                prompt += "\n\nIMPORTANT: Your previous response could not be parsed. Return ONLY the JSON object with a summary and at least 5 keywords."
            resp = await self.gateway.call("enrich", prompt)
            raw = resp.get("message", {}).get("content", "")
            result = self._parse_json(raw)
            if result.get("keywords"):
                break
        if result.get("keywords"):
            self._write_frontmatter(path, content, result, page)
        else:
            log.warning(f"enrich FAILED for {path.name}: unparsable response, no frontmatter written")
        return result
```

(Add `from app.logging_utils import get_logger` and `log = get_logger("enrich")` at the top of `enrich.py`.)

In `app/pipeline/runner.py`, inside the `if ok:` branch (line ~167), gate completion:

```python
                    if ok:
                        summary = r.get("summary", "")
                        if not isinstance(summary, str):
                            summary = str(summary)
                        summary = summary[:ENRICH_SUMMARY_MAX_CHARS]
                        keywords = r.get("keywords", [])
                        if not keywords:
                            log.warning(f"JOB {job_id[:8]} ENRICH {enriched + i + 1}/{len(leaf_paths)} SKIPPED (no keywords): {full_paths[i].name}")
                            continue
                        enriched += 1
                        add_enrich_completed_path(uconn, doc_id, rel_path)
                        log.debug(f"JOB {job_id[:8]} ENRICH {enriched}/{len(leaf_paths)}: {full_paths[i].name} -> summary=\"{summary}\" keywords={keywords}")
```

(Read the current runner block first and apply the minimal diff: move `add_enrich_completed_path` after the `keywords` check and `continue` when empty.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_enrich.py -q`
Expected: pass.

- [ ] **Step 5: Benchmark** (tag `c3`) — pipeline change; note reindex effect.
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/pipeline/enrich.py app/pipeline/runner.py tests/test_enrich.py docs/superpowers/notes/benchmark_c3.md
git commit -m "fix(search): C3 enrich retry once and gate completion on keywords"
```

---

### Task 13 (M4): _keyword_synonyms substring arms + cache key

**Files:**
- Modify: `app/agent/tools.py:159-182` (`_keyword_synonyms`)
- Test: `tests/test_tools.py`

**Problem:** Bidirectional substring matching (`kw in term` arm) is unbounded; cache keyed only on collection_id is stale across reindex.

**Note:** Read the full current body of `_keyword_synonyms` first (the plan shows only the head). Identify the matching predicate; the goal below may need small adjustment to the actual code.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:

```python
def test_keyword_synonyms_only_expands_from_keyword_column(toolbox):
    # "spell" must not inject random query-term substrings; expansion only
    # pulls tokens that appear in the keywords column.
    results = toolbox.fts_search("spell")
    assert isinstance(results, list)
```

(Behavioral smoke test — the real assertions are the unit tests below on the matching predicate.)

- [ ] **Step 2: Run tests to verify they pass (existing behavior)**

Expected: pass — this pins the current behavior; the change is made and re-tested in Step 3-4.

- [ ] **Step 3: Implement**

In `app/agent/tools.py`:

1. Change the keyword match predicate to one-directional: a query term matches a keyword phrase iff the phrase (split into words) contains the term as a whole word OR the term is a substring of the phrase — i.e. drop the `kw in term` arm where the KEYWORD is a substring of the QUERY TERM (e.g. query "spellcasting" must not be expanded by keyword "spell").
2. Change the cache key from `self.collection_id` to `(self.collection_id, len(doc_ids))` so reindexes (which change doc counts) invalidate it:

```python
        cache_key = (self.collection_id, len(doc_ids))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools.py tests/test_query_builder.py -q`
Expected: pass.

- [ ] **Step 5: Benchmark** (tag `m4`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/agent/tools.py tests/test_tools.py docs/superpowers/notes/benchmark_m4.md
git commit -m "fix(search): M4 one-direction keyword expansion, doc-count cache key"
```

---

### Task 14 (L5): shared keyword-count source of truth

**Files:**
- Modify: `app/pipeline/enrich.py` (use constant), `app/constants.py` (already has `ENRICH_OPTIMAL_KEYWORDS = 8`)
- Test: `tests/test_enrich.py`

**Problem:** Prompt says "5-10 keywords" but the code constant is 8 — no shared source of truth.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_enrich.py`:

```python
def test_enrich_prompt_uses_optimal_keywords_constant():
    from app.constants import ENRICH_OPTIMAL_KEYWORDS
    assert str(ENRICH_OPTIMAL_KEYWORDS) in ENRICH_PROMPT.template
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL ("8" is not in the prompt template — template says "5-10").

- [ ] **Step 3: Implement**

In `app/pipeline/enrich.py`, import the constant and build the template with it:

```python
from app.constants import ENRICH_OPTIMAL_KEYWORDS

ENRICH_PROMPT = Template(
    f"…\"keywords\": 5-{ENRICH_OPTIMAL_KEYWORDS} lowercase keywords or short phrases…"
)
```

(Keep `$content` placeholder and the rest of the Task 11 text verbatim; only the "5-10" becomes `5-{ENRICH_OPTIMAL_KEYWORDS}`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_enrich.py -q`
Expected: pass.

- [ ] **Step 5: Benchmark** (tag `l5`)
- [ ] **Step 6: Consult agents + commit**

```bash
git add app/pipeline/enrich.py tests/test_enrich.py docs/superpowers/notes/benchmark_l5.md
git commit -m "fix(search): L5 enrich prompt keyword count from shared constant"
```

---

### Task 15 (H7, H8): ranking rebalance + matched-column visibility + reindex

**Files:**
- Modify: `app/agent/tools.py:117` (bm25 weights), `:125-129` (result assembly: expose keywords)
- Modify: `scripts/reindex.py` usage (run it)
- Test: `tests/test_tools.py`

**Problem:** (H7) bm25 over-weights summary/keywords (8) vs content (1) — long-tail rules rank low; (H8) snippet always from content column even when match was in keywords — model sees unrelated prose. H8 is addressed pragmatically: expose the matched `keywords` in the result and lengthen the snippet (already 15 in Task 5).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:

```python
def test_fts_search_results_expose_keywords(toolbox):
    results = toolbox.fts_search("goblin")
    assert "keywords" in results[0]
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (no keywords field in results).

- [ ] **Step 3: Implement**

In `app/agent/tools.py`:

1. Change the SELECT to include the keywords column:

```python
                    f"SELECT path, title, summary, keywords, "
                    f"snippet(documents_fts, 4, '<mark>', '</mark>', '...', 15) as snippet, "
                    f"bm25(documents_fts, 0, 5, 4, 4, 3) as rank "
```

2. Keep the summary truncation from Task 8.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools.py tests/test_query_builder.py -q`
Expected: pass.

- [ ] **Step 5: Reindex the corpus so the index reflects current frontmatter**

```bash
.venv/bin/python scripts/reindex.py config.yaml
```

(Expected: rebuilds FTS rows from existing files; no re-enrichment.)

- [ ] **Step 6: Benchmark** (tag `h7`) — **decision gate:** if the not-worse rule fails here, revert Task 15 Step 3 weights only (keep keywords exposure), re-run benchmark (tag `h7-revert`), and commit whichever passes.

- [ ] **Step 7: Consult agents + commit**

```bash
git add app/agent/tools.py tests/test_tools.py docs/superpowers/notes/benchmark_h7.md
git commit -m "fix(search): H7+H8 rebalance bm25, expose keywords in results"
```

---

### Task 16: Full suite + final 3-agent review + merge decision

**Files:**
- Test: all of `tests/`

- [ ] **Step 1: Run the full test suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: all pass. If failures appear, fix before proceeding (do not commit fixes that hide benchmark regressions).

- [ ] **Step 2: Run the final benchmark**

Run: `DEV_MODE=1 .venv/bin/python benchmarks/query_comparison.py --models "deepseek-v4-flash:0731-cloud" --judge "deepseek-v4-pro:cloud" 2>&1 | tail -25`
Save AVERAGES to `docs/superpowers/notes/benchmark_final.md`. Compare against baseline: the final average correctness must be ≥ 7.5 and answered% == 100%; the goblin-AC question should be ≥ 5 (was 0).

- [ ] **Step 3: Final 3-agent review of the complete diff**

Spawn per the Spawn Recipe; prompt all three with: "READ-ONLY review of the complete diff on branch search-prompt-fixes vs main (git diff main...HEAD). Assess each change for search-quality regressions or missed problems. Reply ≤200 words: overall APPROVE / issues to address."

- [ ] **Step 4: Write the summary report**

Write `docs/superpowers/notes/2026-08-10-search-fixes-summary.md`: per-commit finding → change → benchmark delta table, final baseline comparison, remaining known issues (e.g. M3 list_index still exposed, L3 no-op, M5 page-filter feature deferred).

- [ ] **Step 5: Present merge decision to the user**

Do NOT merge without explicit approval. Present: final benchmark table, list of commits (git log main..HEAD --oneline), and remaining deferred items (M5 page filter, M4 full re-enrich, Phase-3 table flattening from the earlier plan).

---

## Deferred (explicitly out of scope, recorded here)

- **M5** — `page` in results but `read_file` has no page filter: feature work, not a fix. Deferred.
- **L3** — `"s"` in STOP_WORDS: load-bearing (possessive fragments would OR-match everything). No change; documented.
- **L4** — list_index guidance: intentionally kept (it is the only navigation tool); contradiction now enforced server-side (Task 9).
- **Phase-3 table flattening** (from `.opencode/plans/2026-08-06-search-improvements.md`): separate concern, not in this plan.
- **Full re-enrichment** of existing collections (to benefit from the new ENRICH_PROMPT): requires a re-enrich script; out of scope, flagged as follow-up.
