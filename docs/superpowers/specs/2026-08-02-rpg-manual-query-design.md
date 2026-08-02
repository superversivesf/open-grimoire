# RPG Manual Query Engine — Design Spec

**Date:** 2026-08-02
**Status:** Draft (pending user review)
**Author:** jason + opencode (brainstorming session)

## Purpose

A self-hosted web application for a small group of RPG players to upload RPG
manual PDFs, have them processed into tiered markdown, and ask conversational
questions answered by a local LLM (via Ollama) that searches the manuals and
cites its sources.

Built for a single 8GB NVIDIA GPU, small-group self-hosted, fully offline after
model download. The motivating use case: a Game Master (GM) at the table who
needs to quickly find rules, monster stats, and cross-references across a
shelf of PDFs.

## Goals

- Upload one or many RPG PDFs at once; queue processing.
- Process each PDF into a tiered markdown hierarchy (doc → chapter → section)
  with per-leaf summary and keywords, and a full-text search index.
- Group books into collections (e.g. "Pathfinder shelf", "D&D shelf") so
  queries are scoped to the relevant shelf.
- Ask questions conversationally, multi-turn, with the LLM remembering prior
  turns within a session.
- Answers include citations (path + page + quote) rendered as clickable links.
- Per-user document isolation; documents never cross between users.
- Model-agnostic: any Ollama-hosted model can be swapped in via config without
  code changes or reprocessing.

## Non-Goals (v1, deferred)

- Deduplication across users (same PDF uploaded by two users = two copies).
- Many-to-many doc ↔ collection (a doc belongs to exactly one collection).
- Per-session subset selection (a session scopes to an entire collection).
- Vector embeddings / semantic search (using FTS5 keyword search instead).
- Free Python REPL for the agent (using a structured tool-calling interface
  with a `calc` escape hatch for arbitrary arithmetic and dice).
- SSE token streaming of answers (start with HTMX spinner; add later if the
  wait feels bad).
- Public signup / OAuth / email auth (small group, admin creates users).
- Mobile-native app (web UI is mobile-friendly via CSS, not a separate app).

## Architecture

Five components with clean boundaries. The processing pipeline and the query
agent never talk directly; they communicate only through the storage layer.

```
┌──────────────────────────────────────────────────────────┐
│  Web App (FastAPI)                                        │
│  - Auth middleware (per-user isolation)                   │
│  - POST /upload       → enqueues processing job(s)        │
│  - POST /sessions     → starts chat session               │
│  - POST /sessions/:id → multi-turn follow-up              │
│  - GET  /collections  → user's collections grid           │
│  - Simple HTML/HTMX UI                                    │
└────────┬────────────────────────────┬────────────────────┘
         │ upload                     │ ask/follow-up
         ▼                            ▼
┌─────────────────────┐    ┌─────────────────────────────┐
│  Processing Queue    │    │  Query Agent (conversational)│
│  (SQLite table in    │    │                              │
│   shared.sqlite)     │    │  Per-session state:          │
│                      │    │   history + doc namespace    │
│  Worker loop pulls  │    │   stored in user's SQLite    │
│  jobs, runs pipeline,│    │                              │
│  marks done/failed   │    │  LLM (Ollama) + tool calls   │
│  N=1..2 workers      │    │  - fts_search()             │
│  (serialize heavy    │    │  - read_file() / grep()      │
│   work; save GPU)    │    │  - table_extract() / calc() │
│                      │    │  → answer + citations        │
└────────┬─────────────┘    └─────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────┐
│  Storage (per-user, defense in depth)                     │
│  data/<user_id>/<doc_id>/  ← tiered markdown (filesystem) │
│  db/<user_id>.sqlite       ← FTS5 + collections + sessions │
│  db/shared.sqlite          ← users, app_config, queue      │
└──────────────────────────────────────────────────────────┘
```

### Component responsibilities

1. **Web app** — HTTP boundary. Owns auth, dispatches to pipeline queue and
   agent, serves HTML/HTMX UI. Does not know PDF internals or LLM logic.
2. **Processing queue** — SQLite table in `shared.sqlite`. Workers poll and
   run the pipeline. Serializes GPU work so concurrent uploads don't fight
   for the single GPU. Survives restarts (jobs persist).
3. **Processing pipeline** — Long-running async job. Input: a PDF path.
   Output: tiered markdown + FTS index rows. Five stages (see below).
4. **Query agent** — Short-lived per-request conversational loop. Input:
   user question + session history + collection scope. Output: answer with
   citations. Tool-calling, not free code.
5. **LLM gateway** — Thin wrapper around the Ollama HTTP client. Role-based
   (`query`, `enrich`, `structure`, `vision`). Single place that knows
   Ollama's URL/model config. Swap models via config without code edits.

### Boundary principle

Pipeline writes files + FTS rows; agent reads them. Neither knows about the
other's internals. This means a pipeline change (new OCR engine, better
structure detection) requires no agent changes, and vice versa.

## Document Processing Pipeline

Five stages, each idempotent on its inputs.

```
PDF (uploaded) ─→ [1] Extract ─→ [2] Structure ─→ [3] Tier ─→ [4] Enrich ─→ [5] Index
                   text + OCR   → chapters/      → markdown   → summary +    → FTS5
                   per page     → sections       hierarchy     keywords       rows
```

### Stage 1 — Extract

Turn PDF bytes into a stream of text blocks with page numbers.

- **Primary:** `pdfplumber` for layout-aware text extraction (preserves some
  structure cues via font size and position). Fallback to `pypdf` if
  `pdfplumber` fails on a given PDF.
- **OCR fallback:** if a page yields fewer than ~50 chars of real text
  (scanned page), run OCR:
  - **Tier 1:** Tesseract via `pytesseract` — fast, free, decent on clean
    scans.
  - **Tier 2:** vision LLM (e.g. Gemma 3 vision via Ollama) for ugly scans,
    stat blocks, dense tables. Only invoked if Tesseract output is obviously
    garbage (low confidence or high junk-character ratio). Costs GPU, so it
    is the slow path and must be queued.
- Output: `[{page: int, text: str, ocr: bool, ocr_tier: int|none}]`.
- Idempotent: same PDF → same extraction. Re-run safe.

### Stage 2 — Structure detection

Split the flat text into a chapter/section hierarchy.

- Detect headings by heuristics: font size hints (from `pdfplumber`), ALL CAPS
  lines, numbered headings ("Chapter 1", "1.2 Combat"), repeated styling.
- If heuristics are weak (scanned PDFs lose font info), ask the `structure`
  LLM to segment: pass the flat text, get back a structured outline. One LLM
  pass per document, not per section.
- Output: nested tree `[{title, level, page_range, text}]`.
- Fallback: if no structure is detectable, treat the whole document as one
  chapter with no subsections. Still queryable, just less navigable.

### Stage 3 — Tier into markdown

Write the filesystem tree.

- For each leaf section (deepest in the tree): write
  `data/<user>/<doc>/<chapter>/<section>.md` with the section's content.
- For each chapter: write `data/<user>/<doc>/<chapter>/index.md` listing its
  sections (title + provisional 1-line blurb + pointer). The blurb is
  provisional here; Stage 4 enriches it.
- For the document: write `data/<user>/<doc>/index.md` listing chapters.
- Filenames: slugified, prefixed with order (`01_combat.md`) for stable
  sorting.
- Pure file writes. No LLM calls. Fast.

### Stage 4 — Enrich (summary + keywords per leaf)

Add the metadata that powers retrieval and navigation.

- For each leaf `.md`, run one short `enrich` LLM call:
  - Input: the section's text.
  - Output JSON: `{summary: "<1-2 sentences>", keywords: ["goblin", "AC",
    "stat block"]}`.
- Write summary + keywords into a YAML front-matter block at the top of each
  leaf, along with the source page number:
  ```
  ---
  summary: "AC 15, HP 7, Small humanoid, neutral evil."
  keywords: [goblin, monster, stat block]
  page: 42
  ---
  # Goblin
  ...
  ```
- Update chapter `index.md` and doc `index.md` with the aggregated summaries.
  The doc index shows chapter summaries (short); the chapter index shows
  section summaries (short).
- **Expensive stage:** one LLM call per section. A 300-page RPG book may have
  200-500 sections → 200-500 LLM calls. At ~1-2s each on a local GPU, this is
  ~5-15 minutes per book. The queue handles this; one job, runs in the
  background.
- **Caching:** hash section text; skip re-enrichment on reprocess if hash
  unchanged.

### Stage 5 — Index into FTS5

Make `fts_search("grapple prone")` return ranked paths in milliseconds.

- For each leaf `.md`, insert into the SQLite FTS5 table
  `(path, title, summary, keywords, content)`.
- The keywords column is boosted (FTS5 `rank` tweak or duplicated in content).
- Stored per-user in `db/<user_id>.sqlite`, table `documents_fts`.
- Rebuild is cheap and idempotent: delete all rows for `<doc_id>`, re-insert.

### Pipeline characteristics

- **Idempotent:** reprocessing the same PDF overwrites cleanly. Hash-check
  lets us skip unchanged sections during re-enrichment.
- **Resumable:** if the worker dies mid-document, the next run resumes from
  the last un-enriched section. Per-section status is tracked in the queue
  row's payload.
- **GPU-aware:** stages 1 (Tesseract path), 3, and 5 use CPU. Stages 2 (when
  LLM segmentation is needed), 4 (enrich), and OCR Tier-2 use GPU. The queue
  serializes the GPU stages.
- **Inspectable:** at any point, `ls` the markdown tree and read any
  `index.md` to see progress.

## Conversational Query Agent

```
User question + session history
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│  Agent loop (LLM via Ollama, tool-calling)                │
│                                                           │
│  Repeat until agent emits final answer:                   │
│    1. Reason about question + context                     │
│    2. Emit a tool call (or final answer):                 │
│       - fts_search(query)    → ranked [path, snippet]      │
│       - read_file(path)      → markdown content           │
│       - list_index(path)     → child entries              │
│       - grep(pattern)        → matching lines             │
│       - table_extract(path)  → parsed table rows          │
│       - calc(expr)           → safe arithmetic            │
│       - ls(dir)              → directory contents          │
│       - done(answer, cites)  → terminate                  │
│    3. Execute tool in sandbox                              │
│    4. Feed result back to LLM                             │
│                                                           │
│  Max 8 iterations → force terminate                       │
└──────────────────────────────────────────────────────────┘
        │
        ▼
   answer + citations  ─→ append to session history
```

### Session lifecycle

- **Start:** `POST /sessions {collection_id, first_question}` → creates a row
  in the `sessions` table `(session_id, user_id, collection_id, history_json,
  created_at, updated_at)`, runs the first agent turn, returns `{session_id,
  answer, cites}`.
- **Continue:** `POST /sessions/:id {question}` → loads history, runs the
  next agent turn, appends the result.
- **List:** `GET /sessions` → user's recent sessions.
- **Delete:** `DELETE /sessions/:id` → removes the row (or GC by age).

Sessions scope to a collection, not to individual documents. The agent's
`fts_search`, `read_file`, `grep`, etc. calls are scoped to all docs in the
session's collection automatically. The user does not pick individual books;
the collection is the scope.

### Tool set

Eight tools. All take string/JSON args, all return JSON, all sandboxed. No
arbitrary code execution; the `calc` tool is the escape hatch for arithmetic
and dice.

| Tool | Args | Returns | Notes |
|------|------|---------|-------|
| `fts_search` | `query: str` | `[{path, title, snippet, rank}]` top 10 | FTS5 query syntax; scoped to session's collection. Fast first-pass retrieval. |
| `read_file` | `path: str`, optional `lines: "N-M"` | file text (or line range) | Path validated against user's tree. Line-range for large files. |
| `list_index` | `path: str` | `[{title, summary, path}]` | Reads any `index.md`, parsed from front-matter + headings. Lets the agent navigate the hierarchy. |
| `grep` | `pattern: str`, optional `path: str` | `[{path, line, text}]` top 20 | Regex over the user's tree. Power tool for cross-refs ("every mention of 'advantage' in Combat chapter"). Capped at 20 hits to bound tokens. |
| `table_extract` | `path: str` | parsed table rows as JSON | For stat blocks / tables that survived extraction. Reads leaf file, finds markdown tables, returns structured rows. Lets the agent reason "show every monster with CR ≥ 5" by parsing the table. |
| `calc` | `expr: str` | numeric result | `simpleeval` — dice (`2d6+3`), sums, comparisons. No Python eval. |
| `ls` | `dir: str` | `[filename]` | Bounded to user's directory. For when the agent isn't sure what's there. |
| `done` | `answer: str`, `cites: [{path, page, quote}]` | terminate | Final answer with citations. |

### Sandbox guarantees

- Every `path` arg is resolved to an absolute path and checked to start with
  `data/<user_id>/`. Reject `..`, symlinks pointing outside, absolute paths.
- No filesystem writes, no network, no subprocess from any tool.
- The `calc` tool uses `simpleeval` (or equivalent) with a restricted AST —
  no `__import__`, no attribute access, no builtins. Dice are a custom
  function (`2d6+3` parses to a dice-roll call).
- Tool results are the only way the agent observes the world; it cannot
  escape the tool menu.

### Why tool-calling, not a free Python REPL

The user asked for "code-REPL agentic search." This design uses a structured
tool-calling interface instead, for these reasons:

1. Small models (7B/12B) hallucinate Python syntax. They can reliably pick
   from a small tool menu but not reliably write correct code.
2. A free Python REPL needs a real sandbox (Docker, firecracker) — heavy for
   self-hosted.
3. Most "joins and cross-refs" reduce to 2-3 tool calls in sequence, which
   tool-calling handles naturally.
4. The `calc` tool gives the arbitrary-computation escape hatch (dice, sums,
   comparisons) without a full interpreter.
5. If a class of question turns out to need more power, we can add tools
   (`grep`, `table_extract` are already included) rather than opening up a
   shell. Extensible, not locked in.

### History management (token cost)

Each turn re-sends prior history to the LLM. Two mechanisms bound cost:

1. **Window:** keep the last 6 turns verbatim (~enough to remember "we're
   talking about goblins").
2. **Summarize:** if older turns exist, prepend a one-paragraph summary
   (generated by the `query` LLM at the moment we trim) describing the prior
   context ("player asked about goblin AC, then about grappling, now
   comparing to orcs").
3. **Tool results truncated:** `fts_search` snippets are short by design;
  `read_file` output capped at ~4k tokens. If larger, the agent is told
  "file is long, use `read_file(path, lines=120-180)`" and the line-range
  arg is available.

### Citation format

Agent emits `done(answer, cites)` where `cites = [{path, page, quote}]`. The
UI renders each cite as a clickable link that opens the source markdown at
the relevant section. Page numbers come from the leaf's front-matter
(`page: 42`).

Example answer: "The goblin's AC is 15 *(Combat › Monsters › Goblin, p. 42)*."

### Iteration cap

Hard cap at 8 tool calls per turn. If the agent cannot answer in 8, it
returns a "couldn't find it, here's what I checked" message with the paths it
searched. Prevents runaway cost and loops. Tunable per-session if needed.

## LLM Gateway (role-based, model-agnostic)

Callers name a role, not a model. Swap via config, no code edits, no
reprocessing required.

### Roles

Four logical roles. Each maps to a model name in config. The gateway exposes
`gateway.call(role, prompt, tools=...)` — callers never name a model, only a
role.

| Role | Used by | Requirements |
|------|---------|--------------|
| `query` | Query agent | Tool-calling support, conversational, decent reasoning |
| `enrich` | Pipeline Stage 4 | Cheap, fast, structured JSON output (summary + keywords) |
| `structure` | Pipeline Stage 2 | Document segmentation; can reuse `query` |
| `vision` | Pipeline Stage 1 OCR Tier-2 | Vision capability for ugly scans |

### Default config (8GB VRAM)

```yaml
ollama:
  host: http://localhost:11434

models:
  query: qwen2.5:7b-instruct-q4     # tool-calling agent
  enrich: gemma3:4b-it-q4            # summary + keywords (smaller/faster ok)
  structure: qwen2.5:7b-instruct-q4  # reuse query model
  vision: gemma3:4b-it-q4            # OCR Tier-2 (needs vision)

load_policy: lazy                    # load on first call, unload after idle
```

### 8GB VRAM model recommendations

- **Single-model default:** `qwen2.5:7b-instruct-q4` (~5GB resident) —
  strongest tool-calling in the 7B class. Better at structured tool-use than
  Gemma 3 at similar size. Fits comfortably, leaves VRAM headroom.
- **Gemma by name:** `gemma3:12b-it-q4` (~6.5GB resident) — better raw
  reasoning than 7B Qwen, fits but tight. Tool-calling is workable but less
  polished than Qwen.
- **Multi-model split (optimize):**
  - `query`: `qwen2.5:7b-instruct-q4` (~5GB)
  - `enrich`: `gemma3:4b-it-q4` (~3GB) — smaller is fine for short summary
    tasks
  - `vision`: `gemma3:4b-it-q4` (same as enrich; Gemma 3 has a vision variant)
  - Ollama swaps between them; only one resident at a time in 8GB.

**Honest caveat:** Gemma 3's tool-calling is okay but Qwen 2.5/3 is
materially better at structured tool-use. Since the whole agent depends on
reliable tool-calls, start with Qwen for `query` even if Gemma is used for
other roles. Test both — config swap is free.

### Swap path

- Change `config.yaml` → reload app (or hot-reload the gateway) → next call
  uses the new model.
- No reprocessing needed when swapping the `query` model — FTS index and
  markdown are model-independent.
- Swapping the `enrich` model means reprocessing is optional (existing
  summaries stay until you reprocess; new uploads use the new model).
- Ollama handles pulling new models: `ollama pull <name>`.

### Why not embed models

The user runs Ollama separately. The app talks to it via HTTP. This keeps:
- Model management in Ollama (versioning, quantization, GPU drivers).
- The app free of model files, GPU code, tokenizer logic.
- Easy upgrades: pull a new model, change config, done.

## Web App + UI + Collections

FastAPI backend + server-rendered HTML with HTMX for interactivity. One
deployable, no JS build step.

### Data model

```
collections (user_id, collection_id, name, created_at)
docs        (user_id, doc_id, collection_id, title, sha256, status, ...)
sessions    (user_id, session_id, collection_id, history_json, created_at, updated_at)
```

A doc belongs to exactly one collection (one-to-many). Sessions scope to a
collection, not individual docs.

### Routes

```
Auth:
  GET  /login            → login form
  POST /login            → set session cookie
  POST /logout           → clear session

Library:
  GET  /                 → collections grid + recent sessions
  POST /collections      → create collection
  GET  /collections/:id  → collection view (books, sessions, upload button)
  POST /upload           → upload PDF(s), enqueue job(s) (multipart, multi-file)
  GET  /docs/:id         → doc view: status, browseable index tree, raw leaves
  POST /docs/:id/reprocess → re-queue a failed/done doc
  DELETE /docs/:id       → remove doc (+ its files + FTS rows)

Sessions:
  POST /sessions         → start new session {collection_id, first_question}
  GET  /sessions         → list user's recent sessions
  GET  /sessions/:id     → chat view (history + input box)
  POST /sessions/:id     → follow-up question (HTMX partial)

Admin (optional):
  POST /admin/users      → create user (CLI-equivalent endpoint)
  DELETE /admin/users/:id
```

### Auth

Simple session-cookie auth. No OAuth, no email.

- `users` table in `shared.sqlite`: `(user_id, username, password_hash,
  created_at, is_admin)`.
- `password_hash` via `argon2` (or `bcrypt`).
- On login, set a signed cookie (HMAC) containing `user_id` + expiry.
- Admin user created via CLI (`python -m app.cli user create`) — bootstrap
  path, no signup form.
- Admin can create other users via `/admin/users` or the same CLI.
- Middleware: every request resolves `user_id` from cookie; 401 if
  missing/expired.

### UI pages

- **Library (`GET /`):** collections grid (cards: name, # books, last session
  date), "New collection" button, recent sessions list.
- **Collection view (`GET /collections/:id`):** header (name, book count,
  "Upload books" button, "Ask a question" button), book table (title, status
  badge, actions: view / reprocess / delete), recent sessions for this
  collection.
- **Upload (`/collections/:id/upload` or `/upload?collection=new`):**
  collection selector (existing dropdown + "create new" input), multi-file
  drop zone, submit. One form, N files, one collection_id.
- **Doc view (`GET /docs/:id`):** processing status badge (polls via HTMX
  every few seconds while `processing`), tree view of `index.md` → chapters
  → sections, click a leaf to read its raw markdown.
- **Chat (`GET /sessions/:id`):** message history (user msgs left, agent
  answers right, citations rendered as inline links), input box at bottom.
  `POST /sessions/:id` sends follow-up, HTMX swaps in the new message pair.
  No full reloads during a conversation.

### Styling

Plain CSS, no framework. Functional, readable, mobile-friendly (the GM may
be on a tablet at the table). Use `pico.css` or `water.css` for sane
defaults — minimal, single stylesheet, no JS.

### Streaming answers (deferred)

The agent turn can take 10-20s (multiple tool calls). v1: show a
"thinking..." spinner via HTMX, swap in the full answer when done. Later
enhancement: stream the agent's final `done(answer, ...)` token-by-token
from Ollama via SSE, render progressively. Noticeably nicer during play, but
not needed for v1.

### Multi-upload flow

```
1. User opens upload page
2. Picks collection: existing dropdown OR "create new" text input
3. Drag-selects multiple PDFs (or file picker, multiple=true)
4. Submit → one form, N files, one collection_id
5. Backend:
   - For each file: save, SHA256, insert doc row (status=queued), enqueue job
   - All N jobs land in the queue, same collection
   - Return 202 with collection_id
6. UI redirects to collection view → shows all N jobs with statuses
7. Polls each doc's status via HTMX; collection view updates as books finish
```

The queue handles concurrency naturally: 1 worker processes serially, 2
workers in parallel. Upload 10 Pathfinder books at once; they queue up and
grind through.

### Error handling

- **Upload fails (corrupt PDF):** 400 to UI, user sees error, doc row stays
  as `failed` with message.
- **Processing crashes:** job marked `failed` in queue; visible in library;
  user can retry via `POST /docs/:id/reprocess`.
- **Agent query fails (Ollama down):** 503 to UI with "LLM unavailable" —
  graceful, no session corruption. Session history preserved.
- **Auth fail:** redirect to `/login`.

### Why not React

Self-hosted for a small group. A React SPA adds build tooling, an API client,
state management, and two deploys. Server-rendered HTML + HTMX gives 90% of
the UX for 20% of the code. The chat is the only part that benefits from JS,
and HTMX handles it. If a richer chat is wanted later (typing indicators,
tool-call visibility), a small React island can be added just for the chat
view — not a full rewrite.

## Storage + Per-User Isolation

Two storage layers, both per-user, both plain files / SQLite. No shared
document database, no cloud storage.

### Filesystem layout

```
data/
├── <user_id>/
│   ├── <doc_id>/
│   │   ├── .meta.json                 # title, sha256, collection_id, status, page_count
│   │   ├── original.pdf               # kept for reprocessing / "view source"
│   │   ├── index.md                   # doc-level index
│   │   ├── 01_chapter_slug/
│   │   │   ├── index.md               # chapter index
│   │   │   ├── 01_section.md          # leaf content + front-matter
│   │   │   └── 02_section.md
│   │   └── 02_chapter_slug/
│   │       └── ...
│   └── <doc_id>/...
├── <user_id>/...
└── <user_id>/...

db/
├── <user_id>.sqlite                   # one per user — isolation enforced at file level
│   ├── collections                    # collection metadata (name, created_at)
│   ├── docs
│   ├── sessions
│   └── documents_fts                 # FTS5 virtual table: path, title, summary, keywords, content
└── shared.sqlite                      # app-level: users, app_config, queue_jobs
```

### Per-user isolation — defense in depth

Three layers, each independently sufficient:

1. **Filesystem:** every path the agent or web app constructs starts with
   `data/<user_id>/`. Path validation: resolve to absolute, check
   `startswith(data/<user_id>/)`. Reject `..`, symlinks pointing outside.
2. **SQLite:** one `.sqlite` file per user. A user's queries never touch
   another user's database file. Cross-user queries are impossible at the
   storage layer, not just forbidden by app logic. This is the strongest
   guarantee — there is literally no code path from user A's request to user
   B's FTS rows.
3. **Agent sandbox:** tool calls (`read_file`, `grep`, `list_index`,
   `table_extract`) only ever see the requesting user's tree. The agent
   cannot name another user's path even if it tried — paths outside the
   user's root resolve to "not found".

### Why one SQLite per user (not a shared db with `user_id` columns)

- **Pro:** strongest isolation (no accidental cross-user reads), smaller
  files (one user's FTS doesn't bloat another's), easy backup/restore per
  user (copy one file), easy to delete a user (rm one db + one data dir).
- **Con:** cannot do cross-user queries (not needed — no dedup, no shared
  library), app code must open the right db per request (small wrapper,
  ~30 lines), connection pooling is per-user (fine for small group).

For a small-group self-hosted tool, the isolation win outweighs the small
code cost. A shared db would be simpler code but weaker isolation, and the
user requirement is "keep everybody's documents segregated" — one db per
user makes that a physical fact, not a policy.

### The shared SQLite (`shared.sqlite`)

Holds only app-level state: `users` table, app config (ollama host, model
names), and the global processing queue. Never holds document content.

### Queue placement: global queue in `shared.sqlite` (Option A)

The processing queue lives in `shared.sqlite` as a single `queue_jobs`
table. One worker process polls the global queue, pulls a job, looks up the
`user_id`, opens that user's db to update doc status, writes files to that
user's tree. Simpler worker loop, one place to poll. The queue is
operational state, not user content — isolation is not compromised.

### Backups / portability

- **Backup:** `tar czf backup.tar.gz data/ db/` — done. No database dumps
  needed beyond SQLite's normal file-level backup (or `.backup` command for
  live safety).
- **Restore:** untar over a fresh install.
- **Move a user:** `mv data/<user_id> db/<user_id>.sqlite` to another
  instance.
- **Delete a user:** `rm -rf data/<user_id> db/<user_id>.sqlite` + drop row
  in `shared.sqlite.users`.

### What is not stored

- Embedding vectors — FTS5 chosen, no vectors.
- Cloud references — nothing leaves the machine.
- Transient agent state — sandbox results live only in the request's
  memory, never persisted except as the final answer + cites in session
  history.

## Tech Stack

- **Language:** Python 3.11+
- **Web framework:** FastAPI + Uvicorn
- **UI:** server-rendered HTML + HTMX + pico.css (or water.css)
- **PDF extraction:** `pdfplumber` (primary), `pypdf` (fallback)
- **OCR:** `pytesseract` (Tesseract binding, Tier 1), Ollama vision model
  (Tier 2)
- **Full-text search:** SQLite FTS5 (via Python's `sqlite3` stdlib)
- **LLM:** Ollama (HTTP API), called via role-based gateway
- **Safe arithmetic:** `simpleeval` (restricted AST; custom dice function)
- **Auth:** session cookie + HMAC signing; `argon2` (or `bcrypt`) for
  password hashing
- **Queue:** SQLite table in `shared.sqlite` (no external broker)
- **Storage:** filesystem (markdown) + SQLite (FTS, sessions, queue, users)

## Open Questions

None at spec time. All design decisions above were agreed during the
brainstorming session. Deferred items are listed in "Non-Goals" and can be
revisited after v1.

## Glossary

- **Collection:** a named group of books belonging to a user (e.g.
  "Pathfinder shelf"). Sessions scope to a collection.
- **Leaf:** the deepest section in the markdown hierarchy; contains actual
  content, not an index.
- **Index file:** an `index.md` at the doc or chapter level that lists child
  entries with title + summary + pointer.
- **Tiering:** splitting a PDF into a hierarchy of markdown files: doc index
  → chapter indexes → section leaves.
- **Enrichment:** the Stage 4 LLM pass that generates a summary and keywords
  for each leaf and writes them into front-matter.
- **Role:** a logical LLM use (`query`, `enrich`, `structure`, `vision`)
  that maps to a model name in config.