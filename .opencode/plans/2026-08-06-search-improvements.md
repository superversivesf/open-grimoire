# Search & Prompt Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix search quality (OR-fallback, synonyms, stop-words, weighted ranking, richer results) and rewrite the agent/enrichment prompts so the model searches effectively, then re-run the enrichment model benchmark to pick the best enrich model.

**Architecture:** Three phases. Phase 1 = pure query-construction module (`app/agent/query_builder.py`) consumed by a rewritten `ToolBox.fts_search` (stop-word filtering, synonym expansion incl. per-collection keywords, AND→OR→prefix fallback cascade, `bm25` weighted ranking, `page` extracted from file frontmatter — no FTS schema change). Phase 2 = prompt rewrites in `loop.py` (SYSTEM_PROMPT + nudge messages), `tools_schema.py` (fts_search description), `enrich.py` (exported `ENRICH_PROMPT` constant, also imported by the benchmark script to prevent drift). Phase 3 = markdown tables flattened to plain text at index time + `scripts/reindex.py` to rebuild existing indexes.

**Tech Stack:** Python 3.10+, stdlib `sqlite3` with FTS5 (`bm25()`, `snippet()`, prefix `"term"*`).

## Global Constraints

- No new dependencies — FTS5 `bm25()`/`snippet()` verified available (SQLite 3.45.1).
- No FTS5 schema change (`page` comes from file frontmatter at search time).
- Existing `documents_fts` rows must remain queryable (old rows lack nothing new; only Phase 3 changes stored content, handled by reindex).
- TDD: write failing test → run → implement → run → commit. Commit after every task.
- Benchmark scripts must NOT inline the enrich prompt — import `ENRICH_PROMPT` from `app.pipeline.enrich`.
- Run tests with: `.venv/bin/python -m pytest tests/<file> -q`
- All new query logic lives in `app/agent/query_builder.py` — no query string manipulation inside `tools.py`.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/agent/query_builder.py` | Pure FTS5 query construction: tokenize, stop-words, synonyms, cascade | Create |
| `app/agent/tools.py` | `fts_search` rewrite: cascade execution, bm25, page extraction, keyword expansion | Modify |
| `app/agent/loop.py` | SYSTEM_PROMPT + nudge messages rewrite | Modify |
| `app/agent/tools_schema.py` | fts_search tool description | Modify |
| `app/pipeline/enrich.py` | `ENRICH_PROMPT` constant, richer extraction instructions | Modify |
| `app/pipeline/index.py` | Table flattening at index time | Modify |
| `scripts/reindex.py` | Rebuild FTS index for all users/docs | Create |
| `tests/test_query_builder.py` | Unit tests for query_builder | Create |
| `tests/test_tools.py` | fts_search behavior tests (fallback, synonyms, page) | Modify |
| `tests/test_index.py` | Table-flattening test | Modify |
| `tests/test_enrich.py` | Prompt content assertion | Modify |
| `tests/enrich_comparison.py` | Import `ENRICH_PROMPT` instead of inline copy | Modify |

---

### Task 1: Query Builder Module

**Files:**
- Create: `app/agent/query_builder.py`
- Test: `tests/test_query_builder.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `STOP_WORDS: set[str]`
  - `SYNONYM_GROUPS: dict[str, set[str]]`
  - `tokenize_terms(query: str) -> list[str]`
  - `expand_terms(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[set[str]]`
  - `build_and_query(expanded: list[set[str]]) -> str`
  - `build_or_query(expanded: list[set[str]], prefix: bool = False) -> str`
  - `build_query_cascade(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_query_builder.py`:

```python
from app.agent.query_builder import (
    tokenize_terms, expand_terms, build_and_query,
    build_or_query, build_query_cascade,
)


def test_tokenize_drops_stop_words():
    assert tokenize_terms("How does a goblin fight?") == ["goblin", "fight"]


def test_tokenize_handles_hyphens_and_punct():
    assert tokenize_terms("pre-generated stat-block") == ["pre", "generated", "stat", "block"]


def test_expand_terms_keeps_plain_terms():
    assert expand_terms(["goblin"]) == [{"goblin"}]


def test_expand_terms_synonym_group():
    assert expand_terms(["ac"]) == [{"ac", "armor class", "armour class"}]
    assert expand_terms(["armor class"]) == [{"ac", "armor class", "armour class"}]


def test_expand_terms_merges_extra_synonyms():
    assert expand_terms(["spell"], {"spell": ["spellcasting", "sorcery"]}) == [
        {"spell", "spellcasting", "sorcery"}
    ]


def test_build_and_query_groups():
    expanded = expand_terms(["goblin", "ac"])
    q = build_and_query(expanded)
    assert "(" in q and " OR " in q
    assert '"goblin"' in q and '"ac"' in q and '"armor class"' in q


def test_build_or_query_prefix():
    expanded = expand_terms(["goblin", "hp"])
    q = build_or_query(expanded, prefix=True)
    assert '"goblin"*' in q and '"hp"*' not in q  # short terms no prefix


def test_cascade_order_strictest_first():
    cascade = build_query_cascade(["goblin", "ac"])
    assert len(cascade) == 3
    # 1: AND of groups; 2: OR; 3: OR with prefix
    assert " OR " in cascade[1]
    assert "*" in cascade[2]


def test_cascade_empty_for_all_stop_words():
    assert build_query_cascade(["how", "does", "the"]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_query_builder.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.agent.query_builder'`

- [ ] **Step 3: Implement the module**

Create `app/agent/query_builder.py`:

```python
"""FTS5 query construction: sanitization, stop-words, synonyms, fallback cascade."""
import re

STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "at", "it", "is", "are", "was", "were", "be", "been", "being", "do",
    "does", "did", "can", "could", "would", "should", "will", "what", "how",
    "why", "when", "where", "which", "who", "i", "me", "my", "we", "you",
    "your", "not", "no", "as", "by", "from", "up", "down", "there", "that",
    "this", "these", "those", "about", "into", "than", "then", "if",
}

SYNONYM_GROUPS = {
    "ac": {"ac", "armor class", "armour class"},
    "hp": {"hp", "hit points", "hit point"},
    "st": {"st", "save", "saves", "saving throw", "saving throws"},
    "dc": {"dc", "difficulty class", "difficulty classes"},
    "xp": {"xp", "experience points", "experience point"},
    "init": {"init", "initiative"},
    "tohit": {"tohit", "to hit", "attack roll", "attack rolls", "attack bonus"},
    "proficiency": {"proficiency", "proficiencies", "proficient"},
    "spell": {"spell", "spells", "spellcasting", "spell list"},
    "feat": {"feat", "feats"},
}

_TERM_RE = re.compile(r"[^a-z0-9]+")


def tokenize_terms(query: str) -> list[str]:
    """Split a raw query into clean lowercase terms, dropping stop words."""
    lowered = query.lower()
    words = [w for w in _TERM_RE.split(lowered) if w]
    return [w for w in words if w not in STOP_WORDS]


def _group_for(term: str) -> set | None:
    for group in SYNONYM_GROUPS.values():
        if term in group:
            return group
    return None


def expand_terms(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[set[str]]:
    """Expand each term into an OR-set of synonym tokens.

    extra_synonyms maps a term to additional per-collection keyword matches.
    """
    expanded = []
    for term in terms:
        group = _group_for(term)
        members = set(group) if group else {term}
        for extra in (extra_synonyms or {}).get(term, []):
            members.add(extra)
        expanded.append(members)
    return expanded


def _quote(token: str) -> str | None:
    clean = _TERM_RE.sub(" ", token).strip()
    return f'"{clean}"' if clean else None


def build_and_query(expanded: list[set[str]]) -> str:
    """Implicit AND between term groups; OR inside each group."""
    groups = []
    for members in expanded:
        quoted = [q for m in sorted(members) if (q := _quote(m))]
        if quoted:
            groups.append("(" + " OR ".join(quoted) + ")")
    return " ".join(groups)


def build_or_query(expanded: list[set[str]], prefix: bool = False) -> str:
    """Everything OR'd; optional prefix wildcards on terms >= 4 chars."""
    tokens = set()
    for members in expanded:
        for m in members:
            q = _quote(m)
            if not q:
                continue
            if prefix and len(m) >= 4:
                q = f"{q}*"
            tokens.add(q)
    return " OR ".join(sorted(tokens))


def build_query_cascade(terms: list[str], extra_synonyms: dict[str, list[str]] | None = None) -> list[str]:
    """Return FTS5 queries from strictest to loosest.

    1. AND of synonym groups (stop words removed)
    2. OR of everything
    3. OR of everything with prefix wildcards
    """
    if not terms:
        return []
    expanded = expand_terms(terms, extra_synonyms)
    cascade = [build_and_query(expanded)]
    or_query = build_or_query(expanded)
    if or_query not in cascade:
        cascade.append(or_query)
    prefix_query = build_or_query(expanded, prefix=True)
    if prefix_query not in cascade:
        cascade.append(prefix_query)
    return cascade
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_builder.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/query_builder.py tests/test_query_builder.py
git commit -m "feat: add FTS5 query builder with stop-words, synonyms, fallback cascade"
```

---

### Task 2: Rewrite fts_search

**Files:**
- Modify: `app/agent/tools.py:35-62` (fts_search), add helpers `_page_for`, `_keyword_synonyms`
- Modify: `tests/test_tools.py`

**Interfaces:**
- Consumes: `build_query_cascade`, `tokenize_terms` from Task 1
- Produces: `fts_search(query: str) -> list[dict]` — each result now has keys `path`, `title`, `summary`, `snippet`, `rank`, `page` (int | None). Same public signature; `execute` dispatch unchanged.

- [ ] **Step 1: Write the failing tests**

Replace the `test_fts_search` section of `tests/test_tools.py` with:

```python
def test_fts_search(toolbox):
    results = toolbox.fts_search("goblin")
    assert len(results) >= 1
    assert "goblin" in results[0]["path"].lower() or "Goblin" in results[0]["title"]


def test_fts_search_drops_stop_words(toolbox):
    results = toolbox.fts_search("how does the goblin work?")
    assert len(results) >= 1
    assert results[0]["title"] == "Goblin"


def test_fts_search_synonym_ac(toolbox):
    results = toolbox.fts_search("armor class")
    assert len(results) >= 1


def test_fts_search_results_have_summary_and_page(toolbox):
    results = toolbox.fts_search("goblin")
    assert results[0]["summary"] == "Goblin stats."
    assert results[0]["page"] == 42


def test_fts_search_and_then_or_fallback(toolbox):
    # Row 1 has goblin+ac; row 2 has knight+ac. "goblin knight" fails AND, succeeds OR.
    results = toolbox.fts_search("goblin knight")
    assert len(results) == 2


def test_fts_search_prefix_fallback(toolbox):
    results = toolbox.fts_search("gobli")  # no exact token; prefix cascade catches it
    assert len(results) >= 1


def test_fts_search_empty_query(toolbox):
    assert toolbox.fts_search("how what the") == []
```

The `toolbox` fixture in `tests/test_tools.py` must seed two rows and a frontmatter file with `page: 42`. Replace the fixture body's seed section:

```python
@pytest.fixture
def toolbox(tmp_dirs):
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Book", "h")
    insert_fts_row(uconn, "alice/d1/c1/s1.md", "Goblin", "Goblin stats.", "goblin,monster", "Goblins are small humanoids with AC 15 and HP 7.")
    insert_fts_row(uconn, "alice/d1/c1/s2.md", "Knight", "Knight stats.", "knight,armor", "Knights are armored warriors with AC 16 and HP 13.")
    uconn.close()
    doc_dir = tmp_dirs["data"] / "alice" / "d1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "index.md").write_text("# Book\n\n- [Chapter 1](01_chapter/index.md)\n")
    chap = doc_dir / "01_chapter"
    chap.mkdir()
    (chap / "index.md").write_text("# Chapter 1\n\n- [Goblin](01_goblin.md)\n")
    (chap / "01_goblin.md").write_text("---\nsummary: \"Goblin stats.\"\nkeywords: [goblin]\npage: 42\n---\n\n# Goblin\n\n| Name | AC | HP |\n|------|----|----|\n| Goblin | 15 | 7 |\n\nAC 15, HP 7.\n")
    return ToolBox(tmp_dirs["data"], "alice", tmp_dirs["db"], cid)
```

Note: `_page_for` resolves `self.data_dir / path`, so the inserted FTS paths must be relative to `data_dir` — change them from `"d1/c1/s1.md"` to `"alice/d1/c1/s1.md"` (and `s2.md` same) so frontmatter lookup works. Existing tests `test_read_file`, `test_list_index`, `test_grep`, `test_table_extract`, `test_ls`, `test_execute_dispatch` keep passing (they use absolute paths already).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools.py -q`
Expected: FAIL — synonym, fallback, page, prefix tests fail against old implementation

- [ ] **Step 3: Implement the new fts_search**

Replace the `fts_search` method in `app/agent/tools.py` and add two helpers:

```python
    def fts_search(self, query: str) -> list[dict]:
        conn = init_user_db(self.db_dir, self.user_id)
        try:
            doc_rows = conn.execute(
                "SELECT doc_id FROM docs WHERE collection_id = ?", (self.collection_id,)
            ).fetchall()
            doc_ids = [r["doc_id"] for r in doc_rows]
            if not doc_ids:
                log.debug(f"fts_search: no docs in collection {self.collection_id}")
                return []
            terms = tokenize_terms(query)
            if not terms:
                return []
            extra = self._keyword_synonyms(conn, doc_ids, terms)
            cascade = build_query_cascade(terms, extra)
            scope = "(" + " OR ".join(f"path LIKE ?" for _ in doc_ids) + ")"
            for fts_query in cascade:
                sql = (
                    f"SELECT path, title, summary, "
                    f"snippet(documents_fts, 4, '<mark>', '</mark>', '...', 10) as snippet, "
                    f"bm25(documents_fts, 0, 5, 8, 8, 1) as rank "
                    f"FROM documents_fts WHERE documents_fts MATCH ? AND {scope} "
                    f"ORDER BY rank LIMIT 5"
                )
                params = (fts_query,) + tuple(f"{d}/%" for d in doc_ids)
                rows = conn.execute(sql, params).fetchall()
                if rows:
                    results = []
                    for r in rows:
                        item = dict(r)
                        item["page"] = self._page_for(item["path"])
                        results.append(item)
                    log.debug(f"fts_search: query='{query}' -> {len(results)} results (fts='{fts_query}')")
                    return results
            log.debug(f"fts_search: query='{query}' -> 0 results across all fallbacks")
            return []
        except Exception as e:
            log.error(f"fts_search ERROR: {e}\n{traceback.format_exc()}")
            return []
        finally:
            conn.close()

    def _page_for(self, path: str) -> int | None:
        try:
            full = self.data_dir / path
            if not full.is_file():
                return None
            text = full.read_text()
            if not text.startswith("---"):
                return None
            end = text.find("\n---\n", 4)
            if end == -1:
                return None
            for line in text[4:end].splitlines():
                if line.startswith("page:"):
                    return int(line[5:].strip())
        except (ValueError, OSError):
            pass
        return None

    def _keyword_synonyms(self, conn, doc_ids: list[str], terms: list[str]) -> dict[str, list[str]]:
        """Per-collection keyword expansion: term -> keyword tokens containing it."""
        if not terms:
            return {}
        placeholders = " OR ".join("path LIKE ?" for _ in doc_ids)
        rows = conn.execute(
            f"SELECT keywords FROM documents_fts WHERE {placeholders}",
            tuple(f"{d}/%" for d in doc_ids),
        ).fetchall()
        all_keywords = set()
        for r in rows:
            for kw in (r["keywords"] or "").split(","):
                kw = kw.strip().lower()
                if kw:
                    all_keywords.add(kw)
        extra = {}
        for t in terms:
            if len(t) < 4:
                continue
            hits = {kw for kw in all_keywords if t in kw or kw in t}
            if hits:
                extra[t] = sorted(hits)
        return extra
```

Update the imports at the top of `app/agent/tools.py`:

```python
from app.agent.query_builder import build_query_cascade, tokenize_terms
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools.py tests/test_user_db_fts.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/agent/tools.py tests/test_tools.py
git commit -m "feat: rewrite fts_search with fallback cascade, bm25 weighting, page extraction"
```

---

### Task 3: Rewrite Prompts (agent + enrichment)

**Files:**
- Modify: `app/agent/loop.py:12-25` (SYSTEM_PROMPT) and nudge messages
- Modify: `app/agent/tools_schema.py:54-62` (fts_search description)
- Modify: `app/pipeline/enrich.py` (`ENRICH_PROMPT` constant + `enrich_leaf`)
- Modify: `tests/test_enrich.py`
- Modify: `tests/enrich_comparison.py` (import prompt, delete inline copy)

**Interfaces:**
- Consumes: nothing new
- Produces: `ENRICH_PROMPT: str` in `app/pipeline/enrich.py` (public constant with `{content}` placeholder)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_enrich.py`:

```python
from app.pipeline.enrich import Enricher, ENRICH_PROMPT


@pytest.mark.asyncio
async def test_enrich_prompt_asks_for_numbers_and_jargon(tmp_path):
    leaf = tmp_path / "spell.md"
    leaf.write_text("# Fireball\n\nDeals 8d6 damage.\n")
    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "Fireball spell.", "keywords": ["fireball", "evocation", "8d6"]}'}})
    e = Enricher(gw)
    await e.enrich_leaf(leaf, page=1)
    prompt_used = gw.call.await_args.args[1]
    assert "AC" in prompt_used
    assert "keywords" in prompt_used
    assert "Fireball\n\nDeals 8d6 damage." in prompt_used
```

Add to `tests/test_agent_loop.py`:

```python
from app.agent.loop import AgentLoop, SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_system_prompt_mentions_search_strategy():
    assert "fts_search" in SYSTEM_PROMPT
    assert "ONE distinctive keyword" in SYSTEM_PROMPT
    assert "grep" in SYSTEM_PROMPT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_enrich.py tests/test_agent_loop.py -q`
Expected: FAIL — `ENRICH_PROMPT` undefined; SYSTEM_PROMPT lacks new guidance

- [ ] **Step 3: Update the enrichment prompt**

In `app/pipeline/enrich.py`, add the constant above the class and use it in `enrich_leaf`:

```python
ENRICH_PROMPT = (
    "You are enriching an RPG rulebook section for search indexing. "
    "Read the section and return ONLY valid JSON, no prose:\n"
    '{"summary": "2-3 sentences covering what this section describes, including key game numbers '
    '(AC, HP, DC, damage dice, costs, levels) when present.", '
    '"keywords": ["5-10 lowercase keywords: proper nouns, rule names, spell/monster/class names, '
    'stat abbreviations (ac, hp), and distinctive jargon"]}\n\n'
    "{content}"
)


class Enricher:
    def __init__(self, gateway):
        self.gateway = gateway

    async def enrich_leaf(self, path: Path, page: int | None = None) -> dict:
        content = path.read_text()
        prompt = ENRICH_PROMPT.format(content=content)
        resp = await self.gateway.call("enrich", prompt)
        raw = resp.get("message", {}).get("content", "")
        result = self._parse_json(raw)
        self._write_frontmatter(path, content, result, page)
        return result
```

- [ ] **Step 4: Update the fts_search tool description**

In `app/agent/tools_schema.py`, replace the `fts_search` description and query property:

```python
        "function": {
            "name": "fts_search",
            "description": "Full-text search across all documents in the current collection. Returns ranked matches with path, title, summary, page, and a snippet. Start with ONE distinctive keyword (e.g. 'goblin', 'sorcerer'). Multi-word queries are AND-combined; abbreviations (AC, HP, DC) and stop words are handled automatically. If this returns nothing, try a different single keyword or use grep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search terms, e.g. 'goblin' or 'goblin ac'. Use the most distinctive noun first."},
                },
                "required": ["query"],
            },
        },
```

- [ ] **Step 5: Rewrite SYSTEM_PROMPT in loop.py**

Replace lines 12-25 of `app/agent/loop.py`:

```python
SYSTEM_PROMPT = """You are an RPG rules assistant. You search the user's RPG manual collection (one or more books) to answer questions.

Search strategy:
1. ALWAYS start with fts_search — never browse with ls or list_index.
2. Query with ONE distinctive keyword first — the most specific noun (e.g. "goblin", "sorcerer", "grapple"). If results are too broad, add a second term (e.g. "goblin ac"). Terms are AND-combined; stop words ("how", "what", "is") are ignored and abbreviations (AC, HP, DC, ST, XP) are expanded automatically.
3. Read the top result with read_file before answering — snippets are too short to answer from.
4. If fts_search returns nothing, try one different single keyword (2-3 attempts max), or use grep with a regex to find cross-references (e.g. grep "advantage" for every mention).
5. NEVER read the same file twice.
6. If the question names a book (e.g. "in the Player's Handbook"), prefer matches from that book.
7. NEVER read index.md files — they are navigation only.

When calling done, always include 3 "suggestions" — short follow-up questions a player might ask next based on what they just learned."""
```

- [ ] **Step 6: Update nudge messages in loop.py**

Replace the four nudge strings with concrete fallback hints:

- Iteration-6 nudge (`"You have searched enough. Please call the done tool now with your answer based on what you've found. If you didn't find the answer, say so."`) → `"You have searched enough. Please call the done tool now with your answer based on what you've found. If you didn't find the answer, try grep or a different keyword, then say so."`
- 3-dedup-read block (`"You keep trying to read the same files. Call done NOW with your answer based on what you've already read. If you don't have enough information, say so in your answer."`) → `"You keep trying to read the same files. Call done NOW with your answer based on what you've already read, or use grep to locate the exact passage. If you don't have enough information, say so in your answer."`
- Iteration-8 force (`"You have enough information. Call done now with your answer and citations."`) → `"You have enough information. Call done now with your answer and citations. Cite the exact paths from your fts_search results."`
- Earlier iteration-6 nudge (`"You have searched enough. Call done now with your answer."`, two occurrences) → `"You have searched enough. Call the done tool now with your answer based on what you've found. If your searches found nothing, try grep with a regex, or a single different keyword. If you still can't find it, say so in your answer."`

- [ ] **Step 7: Remove the inline prompt from enrich_comparison.py**

In `tests/enrich_comparison.py`, replace the inline prompt construction inside `test_enrich_model`:

```python
        prompt = (
            "Read this RPG manual section and produce a JSON object with "
            "a 1-2 sentence 'summary' and a list of 3-8 'keywords' (lowercase). "
            "Return ONLY valid JSON, no prose.\n\n"
            f"{sample['content']}"
        )
```

with:

```python
        from app.pipeline.enrich import ENRICH_PROMPT
        prompt = ENRICH_PROMPT.format(content=sample["content"])
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_enrich.py tests/test_agent_loop.py tests/test_tools_schema.py -q`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add app/agent/loop.py app/agent/tools_schema.py app/pipeline/enrich.py tests/test_enrich.py tests/test_agent_loop.py tests/enrich_comparison.py
git commit -m "feat: rewrite agent and enrichment prompts for effective search"
```

---

### Task 4: Flatten Tables at Index Time + Reindex Script

**Files:**
- Modify: `app/pipeline/index.py` (`index_document` + helpers)
- Create: `scripts/reindex.py`
- Modify: `tests/test_index.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `scripts/reindex.py` — standalone script; run with `.venv/bin/python scripts/reindex.py`. `index_document` signature unchanged but now stores flattened table text.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_index.py`:

```python
def test_index_document_flattens_tables(tmp_dirs):
    from app.storage.user_db import init_user_db
    doc_dir = tmp_dirs["data"] / "d1" / "01_chapter"
    doc_dir.mkdir(parents=True)
    leaf = doc_dir / "01_section.md"
    leaf.write_text("---\nsummary: \"Goblin stats.\"\nkeywords: [goblin, AC]\npage: 42\n---\n\n# Goblin\n\n| Name | AC | HP |\n|------|----|----|\n| Goblin | 15 | 7 |\n")
    conn = init_user_db(tmp_dirs["db"], "alice")
    index_document(conn, [str(leaf.relative_to(tmp_dirs["data"]))], tmp_dirs["data"], "d1")
    row = conn.execute("SELECT content FROM documents_fts").fetchone()
    assert "|" not in row["content"]
    assert "Goblin 15 7" in row["content"]
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_index.py -q`
Expected: FAIL — content still contains pipes

- [ ] **Step 3: Implement table flattening**

In `app/pipeline/index.py`, add helpers and update `index_document`:

```python
def _flatten_table_line(line: str) -> str:
    cells = [c.strip() for c in line.strip("|").split("|")]
    return " ".join(cells)
```

In `index_document`, change the final insert to clean tables:

```python
        body = _clean_content(body)
        insert_fts_row(conn, rel, title, str(summary), str(keywords), body)
```

Add `_clean_content`:

```python
def _clean_content(body: str) -> str:
    out = []
    for line in body.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue  # separator row
            out.append(" ".join(cells))
        else:
            out.append(line)
    return "\n".join(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_index.py -q`
Expected: all PASS

- [ ] **Step 5: Create the reindex script**

Create `scripts/reindex.py`:

```python
"""Re-run FTS5 indexing for all docs of all users.

Usage: .venv/bin/python scripts/reindex.py [config.yaml]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import load_config
from app.storage.shared_db import init_shared_db, list_users
from app.storage.user_db import init_user_db, list_collections, list_docs
from app.storage.paths import user_data_dir
from app.pipeline.index import index_document


def main() -> None:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent.parent / "config.yaml")
    cfg = load_config(cfg_path)
    sconn = init_shared_db(cfg.db_dir)
    users = list_users(sconn)
    total_docs = 0
    for user in users:
        uid = user["user_id"]
        uconn = init_user_db(cfg.db_dir, uid)
        for col in list_collections(uconn):
            for doc in list_docs(uconn, col["collection_id"]):
                doc_id = doc["doc_id"]
                udata = user_data_dir(cfg.data_dir, uid)
                doc_dir = udata / doc_id
                if not doc_dir.exists():
                    continue
                leaf_files = sorted(f for f in doc_dir.rglob("*.md") if f.name != "index.md")
                leaf_paths = [str(f.relative_to(udata)) for f in leaf_files]
                if not leaf_paths:
                    continue
                index_document(uconn, leaf_paths, udata, doc_id)
                total_docs += 1
                print(f"reindexed {uid[:8]}/{doc_id[:8]}: {len(leaf_paths)} sections")
        uconn.close()
    sconn.close()
    print(f"done: {total_docs} docs reindexed")


if __name__ == "__main__":
    main()
```

(`list_users(sconn)` returns rows with `user_id` key — confirmed by `tests/cross_model_test.py` usage `uid = users[0]["user_id"]`.)

- [ ] **Step 6: Commit**

```bash
git add app/pipeline/index.py tests/test_index.py scripts/reindex.py
git commit -m "feat: flatten markdown tables in FTS index, add reindex script"
```

---

### Task 5: Full Verification + Enrichment Model Benchmark

**Files:**
- Modify: `config.yaml` (only if a different enrich model wins)
- No code changes expected

**Goal:** Prove the full suite passes, reindex production data, then re-run the enrichment benchmark to pick the best enrich model with the new prompt.

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS (if any fail, fix before proceeding; the shared-book copy path in `runner.py` is unaffected since it copies FTS rows verbatim).

- [ ] **Step 2: Reindex existing books**

Run: `.venv/bin/python scripts/reindex.py`
Expected: prints `reindexed <uid>/<docid>: N sections` per doc, then `done: M docs reindexed`.

- [ ] **Step 3: Smoke-test search on real data**

Run: `.venv/bin/python tests/cross_model_test.py`
Expected: FTS results per question improved vs. pre-change baseline (stop-word queries now return hits). This also exercises the new `fts_search` against real enriched books.

- [ ] **Step 4: Run the enrichment model benchmark**

Ensure Ollama is running, then:

```bash
.venv/bin/python tests/enrich_comparison.py --mode enrich --samples 20 --models "phi4-mini:3.8b,deepseek-v4-flash:0731-cloud,qwen2.5:7b"
```

Expected: ranking table by quality score (JSON%, summary words, keyword count, topic match, time).

- [ ] **Step 5: Decide the enrich model and update config**

If the winning model differs from `models.enrich` in `config.yaml` (currently `deepseek-v4-flash:0731-cloud`), update `config.yaml`:

```yaml
models:
  enrich: <winning-model-name>
```

Also run the query-mode benchmark to confirm the query model choice is unaffected:

```bash
.venv/bin/python tests/enrich_comparison.py --mode query --models "deepseek-v4-flash:0731-cloud,phi4-mini:3.8b"
```

- [ ] **Step 6: Re-enrich + reindex with the winning model (if config changed)**

If `models.enrich` changed, re-enrich the first book's sections with the winner and reindex (pattern from `tests/cross_model_test.py` `enrich_with_model`; production re-enrich uses the pipeline's Stage 4). Then run `.venv/bin/python scripts/reindex.py` once more.

- [ ] **Step 7: Commit (only if config changed)**

```bash
git add config.yaml
git commit -m "chore: switch enrich model to <winning-model-name>"
```

- [ ] **Step 8: Record results**

Save the benchmark summary output (console table + ranking) to `docs/superpowers/notes/2026-08-06-enrich-benchmark.md` for future reference.

---

## Self-Review

**Spec coverage:**
- Phase 1 (engine): OR-fallback (Task 2 cascade), synonyms hardcoded + per-collection keywords (Task 1 `SYNONYM_GROUPS` + Task 2 `_keyword_synonyms`), bm25 weighting (Task 2), richer results incl. page (Task 2), prefix fallback (Task 1), error handling (Task 2 try/except retained). ✓
- Phase 2 (prompts): SYSTEM_PROMPT (Task 3 step 5), schema description (step 4), nudge messages (step 6), Enricher prompt (step 3). ✓
- Phase 3 (indexing): table flattening (Task 4), reindex script (Task 4). ✓
- Benchmark rerun: Task 5. ✓

**Placeholder scan:** All steps contain complete code or exact replacement text. ✓

**Type consistency:** `tokenize_terms`, `build_query_cascade` used in Task 2 match Task 1 signatures. `ENRICH_PROMPT` defined in Task 3, imported in `tests/enrich_comparison.py` same task. `_page_for` returns `int | None`; `fts_search` results documented as `page: int | None`. ✓
