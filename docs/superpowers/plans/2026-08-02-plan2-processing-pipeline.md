# RPG Manual Query Engine — Plan 2: Processing Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the upload + processing pipeline: multi-upload UI, queue, 5-stage pipeline (extract → structure → tier → enrich → index), doc view, and FTS5 index. After this plan, users can upload PDFs and get searchable tiered markdown.

**Architecture:** FastAPI background worker polls a SQLite queue table in `shared.sqlite`. Each job runs the 5-stage pipeline, writing tiered markdown to `data/<user>/<doc>/` and FTS5 rows to `db/<user_id>.sqlite`. Upload endpoint enqueues jobs; UI polls status via HTMX.

**Tech Stack:** Python 3.11+, FastAPI, `pdfplumber`, `pypdf`, `pytesseract` (+ Tesseract binary), Pillow, `httpx` (Ollama vision), existing storage from Plan 1.

## Global Constraints

- Builds on Plan 1 (storage, auth, gateway, web app must exist).
- Per-user SQLite gets a new `documents_fts` FTS5 virtual table.
- Queue in `shared.sqlite` (Option A from spec).
- Pipeline is idempotent (reprocess overwrites cleanly) and resumable (per-section status tracked in queue row payload).
- GPU stages (structure LLM, enrich, vision OCR) serialize via the single-worker queue.
- All file paths validated via `validate_user_path` from Plan 1.
- Tests use `pytest`; mock Ollama calls (don't require a running Ollama).
- Commit after each task.

---

## File Structure

```
app/
├── pipeline/
│   ├── __init__.py
│   ├── extract.py        # Stage 1: PDF → text blocks (text + OCR)
│   ├── structure.py     # Stage 2: text → hierarchy tree
│   ├── tier.py          # Stage 3: tree → markdown files
│   ├── enrich.py        # Stage 4: per-leaf summary + keywords (LLM)
│   ├── index.py         # Stage 5: FTS5 index build
│   └── runner.py        # Orchestrates 5 stages for one job
├── queue/
│   ├── __init__.py
│   ├── db.py            # queue_jobs table CRUD
│   └── worker.py        # background worker loop
├── web/
│   ├── routes.py        # +/upload, +/docs/:id, +/docs/:id/reprocess, +/collections/:id
│   └── templates/
│       ├── upload.html
│       ├── collection.html
│       └── doc.html
└── storage/
    └── user_db.py       # +FTS5 table, +doc status updates, +FTS row CRUD
tests/
├── test_extract.py
├── test_structure.py
├── test_tier.py
├── test_enrich.py
├── test_index.py
├── test_runner.py
├── test_queue.py
├── test_upload_routes.py
└── test_doc_routes.py
```

---

### Task 1: FTS5 table + doc status updates in user_db

**Files:**
- Modify: `app/storage/user_db.py`
- Test: `tests/test_user_db_fts.py`

**Interfaces:**
- Consumes: `init_user_db` from Plan 1 Task 4.
- Produces:
  - `init_user_db` now also creates `documents_fts` FTS5 virtual table `(path, title, summary, keywords, content)`.
  - `insert_fts_row(conn, path, title, summary, keywords, content) -> None`
  - `delete_fts_rows_for_doc(conn, doc_id: str) -> None` — deletes rows where path starts with `data/<user>/<doc_id>/`.
  - `update_doc_status(conn, doc_id: str, status: str) -> None`
  - `get_doc(conn, doc_id: str) -> dict | None`
  - `delete_doc(conn, doc_id: str) -> None` — removes doc row + FTS rows (filesystem cleanup is the web route's job).

- [ ] **Step 1: Write the failing test `tests/test_user_db_fts.py`**

```python
from app.storage.user_db import (
    init_user_db, create_collection, create_doc, insert_fts_row,
    delete_fts_rows_for_doc, update_doc_status, get_doc, delete_doc,
)


def test_fts_table_created(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    assert "documents_fts" in tables
    conn.close()


def test_insert_and_search_fts(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    insert_fts_row(conn, "data/alice/d1/c1/s1.md", "Goblin", "AC 15 monster", "goblin,monster", "Goblins are small humanoids with AC 15.")
    rows = conn.execute("SELECT path FROM documents_fts WHERE documents_fts MATCH 'goblin'").fetchall()
    assert len(rows) == 1
    assert rows[0]["path"] == "data/alice/d1/c1/s1.md"
    conn.close()


def test_delete_fts_rows_for_doc(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    insert_fts_row(conn, "data/alice/d1/c1/s1.md", "A", "s", "k", "goblin content")
    insert_fts_row(conn, "data/alice/d1/c1/s2.md", "B", "s", "k", "orc content")
    insert_fts_row(conn, "data/alice/d2/c1/s1.md", "C", "s", "k", "dragon content")
    delete_fts_rows_for_doc(conn, "d1")
    rows = conn.execute("SELECT path FROM documents_fts").fetchall()
    assert len(rows) == 1
    assert rows[0]["path"] == "data/alice/d2/c1/s1.md"
    conn.close()


def test_update_doc_status(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "C")
    create_doc(conn, "d1", cid, "Book", "h")
    update_doc_status(conn, "d1", "processing")
    d = get_doc(conn, "d1")
    assert d["status"] == "processing"
    conn.close()


def test_delete_doc_removes_row_and_fts(tmp_dirs):
    conn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(conn, "C")
    create_doc(conn, "d1", cid, "Book", "h")
    insert_fts_row(conn, "data/alice/d1/c1/s1.md", "A", "s", "k", "content")
    delete_doc(conn, "d1")
    assert get_doc(conn, "d1") is None
    rows = conn.execute("SELECT path FROM documents_fts").fetchall()
    assert len(rows) == 0
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_user_db_fts.py -v`
Expected: FAIL (no `insert_fts_row` etc.)

- [ ] **Step 3: Modify `app/storage/user_db.py` — add FTS5 table + new functions**

Add to `init_user_db` after the `sessions` table creation:

```python
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            path, title, summary, keywords, content, tokenize='porter'
        )
        """
    )
```

Add the new functions at the end of the file:

```python
def insert_fts_row(conn, path: str, title: str, summary: str, keywords: str, content: str) -> None:
    conn.execute(
        "INSERT INTO documents_fts (path, title, summary, keywords, content) VALUES (?, ?, ?, ?, ?)",
        (path, title, summary, keywords, content),
    )
    conn.commit()


def delete_fts_rows_for_doc(conn, doc_id: str) -> None:
    conn.execute("DELETE FROM documents_fts WHERE path LIKE ?", (f"%/{doc_id}/%",))
    conn.commit()


def update_doc_status(conn, doc_id: str, status: str) -> None:
    conn.execute("UPDATE docs SET status = ? WHERE doc_id = ?", (status, doc_id))
    conn.commit()


def get_doc(conn, doc_id: str) -> dict | None:
    row = conn.execute(
        "SELECT doc_id, collection_id, title, sha256, status, page_count, created_at FROM docs WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_doc(conn, doc_id: str) -> None:
    delete_fts_rows_for_doc(conn, doc_id)
    conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
    conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_user_db_fts.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/storage/user_db.py tests/test_user_db_fts.py
git commit -m "feat: FTS5 table + doc status/CRUD in user_db"
```

---

### Task 2: Queue DB (queue_jobs table in shared.sqlite)

**Files:**
- Modify: `app/storage/shared_db.py`
- Test: `tests/test_queue_db.py`

**Interfaces:**
- Consumes: `init_shared_db` from Plan 1.
- Produces:
  - `init_shared_db` now also creates `queue_jobs` table `(job_id TEXT PK, user_id TEXT, doc_id TEXT, pdf_path TEXT, status TEXT, attempts INTEGER, error TEXT, payload_json TEXT, created_at TEXT, updated_at TEXT)`.
  - `enqueue_job(conn, user_id, doc_id, pdf_path) -> str` — returns job_id.
  - `claim_next_job(conn) -> dict | None` — atomically claims the next `queued` job (sets status `processing`).
  - `complete_job(conn, job_id, error: str | None = None) -> None` — sets `done` or `failed`.
  - `get_job(conn, job_id) -> dict | None`
  - `list_jobs_by_user(conn, user_id) -> list[dict]`

- [ ] **Step 1: Write the failing test `tests/test_queue_db.py`**

```python
from app.storage.shared_db import (
    init_shared_db, enqueue_job, claim_next_job, complete_job, get_job, list_jobs_by_user,
)


def test_queue_table_created(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    assert "queue_jobs" in {r[0] for r in cur.fetchall()}
    conn.close()


def test_enqueue_and_claim(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = enqueue_job(conn, "alice", "d1", "/tmp/x.pdf")
    job = claim_next_job(conn)
    assert job["job_id"] == jid
    assert job["status"] == "processing"
    assert job["user_id"] == "alice"
    conn.close()


def test_claim_empty_returns_none(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    assert claim_next_job(conn) is None
    conn.close()


def test_complete_job_done(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = enqueue_job(conn, "alice", "d1", "/tmp/x.pdf")
    claim_next_job(conn)
    complete_job(conn, jid)
    job = get_job(conn, jid)
    assert job["status"] == "done"
    conn.close()


def test_complete_job_failed_with_error(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = enqueue_job(conn, "alice", "d1", "/tmp/x.pdf")
    claim_next_job(conn)
    complete_job(conn, jid, error="OCR failed")
    job = get_job(conn, jid)
    assert job["status"] == "failed"
    assert job["error"] == "OCR failed"
    conn.close()


def test_claim_is_atomic_fifo(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    j1 = enqueue_job(conn, "alice", "d1", "/x.pdf")
    j2 = enqueue_job(conn, "bob", "d2", "/y.pdf")
    job = claim_next_job(conn)
    assert job["job_id"] == j1
    job2 = claim_next_job(conn)
    assert job2["job_id"] == j2
    conn.close()


def test_list_jobs_by_user(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    enqueue_job(conn, "alice", "d1", "/x.pdf")
    enqueue_job(conn, "alice", "d2", "/y.pdf")
    enqueue_job(conn, "bob", "d3", "/z.pdf")
    jobs = list_jobs_by_user(conn, "alice")
    assert len(jobs) == 2
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_queue_db.py -v`
Expected: FAIL

- [ ] **Step 3: Modify `app/storage/shared_db.py` — add queue table + functions**

Add to `init_shared_db` after `app_config`:

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS queue_jobs (
            job_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            pdf_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON queue_jobs(status, created_at)")
```

Add the functions:

```python
import uuid as _uuid


def enqueue_job(conn, user_id: str, doc_id: str, pdf_path: str) -> str:
    job_id = _uuid.uuid4().hex
    conn.execute(
        "INSERT INTO queue_jobs (job_id, user_id, doc_id, pdf_path) VALUES (?, ?, ?, ?)",
        (job_id, user_id, doc_id, pdf_path),
    )
    conn.commit()
    return job_id


def claim_next_job(conn) -> dict | None:
    row = conn.execute(
        "SELECT job_id FROM queue_jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
    ).fetchone()
    if not row:
        return None
    job_id = row["job_id"]
    conn.execute(
        "UPDATE queue_jobs SET status = 'processing', updated_at = datetime('now') WHERE job_id = ?",
        (job_id,),
    )
    conn.commit()
    return get_job(conn, job_id)


def complete_job(conn, job_id: str, error: str | None = None) -> None:
    status = "failed" if error else "done"
    conn.execute(
        "UPDATE queue_jobs SET status = ?, error = ?, updated_at = datetime('now') WHERE job_id = ?",
        (status, error, job_id),
    )
    conn.commit()


def get_job(conn, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM queue_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs_by_user(conn, user_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM queue_jobs WHERE user_id = ? ORDER BY created_at", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_queue_db.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add app/storage/shared_db.py tests/test_queue_db.py
git commit -m "feat: queue_jobs table + claim/complete in shared_db"
```

---

### Task 3: Stage 1 — Extract (PDF → text blocks)

**Files:**
- Create: `app/pipeline/__init__.py`
- Create: `app/pipeline/extract.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `OllamaGateway` (Plan 1 Task 8) for vision OCR Tier-2 (mocked in tests).
- Produces:
  - `class Extractor` with `__init__(self, gateway: OllamaGateway | None)`.
  - `def extract(self, pdf_path: Path) -> list[dict]` — returns `[{page: int, text: str, ocr: bool, ocr_tier: int | None}]`.
  - Uses `pdfplumber` primary; falls back to `pypdf` if `pdfplumber` fails.
  - OCR Tier-1: Tesseract via `pytesseract` when page text < 50 chars.
  - OCR Tier-2: vision LLM when Tesseract output is garbage (high junk ratio). Skipped if no gateway.

- [ ] **Step 1: Write the failing test `tests/test_extract.py`**

```python
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock
from app.pipeline.extract import Extractor


def _make_text_pdf(path: Path, pages: list[str]):
    from fpdf import FPDF
    pdf = FPDF()
    for text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, txt=text)
    pdf.output(str(path))


def test_extract_text_pdf(tmp_path):
    pdf_path = tmp_path / "book.pdf"
    _make_text_pdf(pdf_path, ["Chapter 1: Combat", "Goblins have AC 15."])
    ext = Extractor(gateway=None)
    blocks = ext.extract(pdf_path)
    assert len(blocks) == 2
    assert blocks[0]["page"] == 1
    assert "Chapter 1" in blocks[0]["text"]
    assert blocks[0]["ocr"] is False
    assert blocks[1]["page"] == 2
    assert "AC 15" in blocks[1]["text"]
    assert blocks[1]["ocr"] is False


def test_extract_empty_pages_fallback_to_pypdf(tmp_path):
    pdf_path = tmp_path / "book.pdf"
    _make_text_pdf(pdf_path, ["Hello world"])
    ext = Extractor(gateway=None)
    blocks = ext.extract(pdf_path)
    assert len(blocks) == 1
    assert "Hello" in blocks[0]["text"]
```

Note: the OCR paths (Tesseract / vision) are hard to unit-test without a real scanned PDF and OCR binaries. Those paths are tested manually with a real scanned PDF; unit tests cover the text-extraction path and the fallback.

- [ ] **Step 2: Add `fpdf2` to deps in `pyproject.toml`**

Add `"fpdf2>=2.7"` to dev dependencies (test only).

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write `app/pipeline/__init__.py` (empty) and `app/pipeline/extract.py`**

```python
# app/pipeline/__init__.py
```

```python
# app/pipeline/extract.py
from pathlib import Path
import re


class Extractor:
    def __init__(self, gateway=None):
        self.gateway = gateway

    def extract(self, pdf_path: Path) -> list[dict]:
        pages = self._extract_with_pdfplumber(pdf_path)
        if pages is None:
            pages = self._extract_with_pypdf(pdf_path)
        blocks = []
        for i, text in enumerate(pages, start=1):
            clean = (text or "").strip()
            if len(clean) < 50:
                ocr_text, tier = self._ocr_page(pdf_path, i)
                if ocr_text:
                    blocks.append({"page": i, "text": ocr_text, "ocr": True, "ocr_tier": tier})
                    continue
            blocks.append({"page": i, "text": clean, "ocr": False, "ocr_tier": None})
        return blocks

    def _extract_with_pdfplumber(self, pdf_path: Path) -> list[str] | None:
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                return [(page.extract_text() or "") for page in pdf.pages]
        except Exception:
            return None

    def _extract_with_pypdf(self, pdf_path: Path) -> list[str]:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return [(page.extract_text() or "") for page in reader.pages]

    def _ocr_page(self, pdf_path: Path, page_num: int) -> tuple[str, int | None]:
        try:
            text = self._tesseract_ocr(pdf_path, page_num)
            if text and not self._is_garbage(text):
                return text, 1
        except Exception:
            pass
        if self.gateway is not None:
            try:
                text = self._vision_ocr(pdf_path, page_num)
                if text:
                    return text, 2
            except Exception:
                pass
        return "", None

    def _tesseract_ocr(self, pdf_path: Path, page_num: int) -> str:
        import pytesseract
        from pdf2image import convert_from_path
        images = convert_from_path(str(pdf_path), first_page=page_num, last_page=page_num, dpi=200)
        return "\n".join(pytesseract.image_to_string(img) for img in images)

    def _vision_ocr(self, pdf_path: Path, page_num: int) -> str:
        import asyncio
        import base64
        from pdf2image import convert_from_path
        images = convert_from_path(str(pdf_path), first_page=page_num, last_page=page_num, dpi=200)
        if not images:
            return ""
        import io
        buf = io.BytesIO()
        images[0].save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        prompt = f"Transcribe the text from this page image exactly. Image: [base64 png omitted in prompt]"
        result = asyncio.get_event_loop().run_until_complete(
            self.gateway.call("vision", prompt)
        )
        return result.get("message", {}).get("content", "")

    @staticmethod
    def _is_garbage(text: str) -> bool:
        if not text:
            return True
        letters = sum(1 for c in text if c.isalpha())
        return letters < len(text) * 0.3
```

- [ ] **Step 5: Add `pdf2image` and `pytesseract` to deps**

Add `"pdf2image>=1.17"` and `"pytesseract>=0.3.10"` and `"Pillow>=10.0"` to `pyproject.toml` dependencies.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_extract.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add app/pipeline/__init__.py app/pipeline/extract.py tests/test_extract.py pyproject.toml
git commit -m "feat: Stage 1 PDF extraction (text + OCR fallback)"
```

---

### Task 4: Stage 2 — Structure detection (text → hierarchy)

**Files:**
- Create: `app/pipeline/structure.py`
- Test: `tests/test_structure.py`

**Interfaces:**
- Consumes: `OllamaGateway` for LLM segmentation fallback (mocked in tests).
- Produces:
  - `class Structurer` with `__init__(self, gateway: OllamaGateway | None)`.
  - `def detect(self, blocks: list[dict]) -> list[dict]` — returns tree `[{title, level, page_start, page_end, text, children: [...]}]`.
  - Heuristic detection: ALL CAPS lines, numbered headings ("Chapter 1", "1.2 Combat"), font size cues from `pdfplumber` (not available in plain text; used when `blocks` carry `font_sizes` metadata — optional).
  - Fallback: single chapter containing all text.

- [ ] **Step 1: Write the failing test `tests/test_structure.py`**

```python
from app.pipeline.structure import Structurer


def test_detect_numbered_chapters():
    blocks = [
        {"page": 1, "text": "Chapter 1: Combat\nThe rules of combat."},
        {"page": 2, "text": "Chapter 2: Magic\nSpells and magic items."},
    ]
    s = Structurer(gateway=None)
    tree = s.detect(blocks)
    assert len(tree) == 2
    assert tree[0]["title"] == "Chapter 1: Combat"
    assert tree[0]["level"] == 1
    assert "rules of combat" in tree[0]["text"].lower()
    assert tree[1]["title"] == "Chapter 2: Magic"
    assert tree[1]["page_start"] == 2


def test_detect_all_caps_headings():
    blocks = [
        {"page": 1, "text": "COMBAT\nGoblins have AC 15."},
        {"page": 2, "text": "MAGIC\nFireball does 8d6."},
    ]
    s = Structurer(gateway=None)
    tree = s.detect(blocks)
    assert len(tree) == 2
    assert tree[0]["title"] == "COMBAT"
    assert tree[1]["title"] == "MAGIC"


def test_detect_subsections_numbered():
    blocks = [
        {"page": 1, "text": "Chapter 1: Combat\n1.1 Initiative\nRoll for initiative.\n1.2 Attacks\nAttack rolls."},
    ]
    s = Structurer(gateway=None)
    tree = s.detect(blocks)
    assert len(tree) == 1
    assert tree[0]["title"] == "Chapter 1: Combat"
    assert len(tree[0]["children"]) == 2
    assert tree[0]["children"][0]["title"] == "1.1 Initiative"
    assert tree[0]["children"][1]["title"] == "1.2 Attacks"


def test_no_structure_fallback_single_chapter():
    blocks = [
        {"page": 1, "text": "Just a bunch of text with no headings."},
        {"page": 2, "text": "More text here."},
    ]
    s = Structurer(gateway=None)
    tree = s.detect(blocks)
    assert len(tree) == 1
    assert tree[0]["level"] == 1
    assert tree[0]["page_start"] == 1
    assert tree[0]["page_end"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_structure.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/pipeline/structure.py`**

```python
import re


class Structurer:
    HEADING_PATTERNS = [
        re.compile(r"^(Chapter\s+\d+.*)$", re.IGNORECASE),
        re.compile(r"^(\d+\.\d+\s+.+)$"),
        re.compile(r"^(\d+\.\s+.+)$"),
        re.compile(r"^([A-Z][A-Z\s]{4,})$"),
    ]

    def __init__(self, gateway=None):
        self.gateway = gateway

    def detect(self, blocks: list[dict]) -> list[dict]:
        flat = self._scan_headings(blocks)
        if not flat:
            return [self._fallback_chapter(blocks)]
        return self._build_tree(flat, blocks)

    def _scan_headings(self, blocks: list[dict]) -> list[dict]:
        headings = []
        for b in blocks:
            for line in b["text"].splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                level = self._heading_level(stripped)
                if level:
                    headings.append({"title": stripped, "level": level, "page": b["page"]})
        return headings

    def _heading_level(self, line: str) -> int | None:
        if self.HEADING_PATTERNS[0].match(line):
            return 1
        if self.HEADING_PATTERNS[1].match(line):
            return 2
        if self.HEADING_PATTERNS[2].match(line):
            return 1
        if self.HEADING_PATTERNS[3].match(line):
            return 1
        return None

    def _build_tree(self, headings: list[dict], blocks: list[dict]) -> list[dict]:
        root = []
        stack = []
        for i, h in enumerate(headings):
            node = {"title": h["title"], "level": h["level"], "page_start": h["page"], "page_end": h["page"], "text": "", "children": []}
            next_page = headings[i + 1]["page"] if i + 1 < len(headings) else blocks[-1]["page"]
            node["page_end"] = next_page
            node["text"] = self._collect_text(blocks, h["page"], next_page, h["title"])
            while stack and stack[-1]["level"] >= node["level"]:
                stack.pop()
            if stack:
                stack[-1]["children"].append(node)
            else:
                root.append(node)
            stack.append(node)
        return root

    def _collect_text(self, blocks: list[dict], start_page: int, end_page: int, heading: str) -> str:
        chunks = []
        for b in blocks:
            if start_page <= b["page"] <= end_page:
                chunks.append(b["text"])
        return "\n".join(chunks)

    def _fallback_chapter(self, blocks: list[dict]) -> dict:
        return {
            "title": "Full Document",
            "level": 1,
            "page_start": blocks[0]["page"] if blocks else 1,
            "page_end": blocks[-1]["page"] if blocks else 1,
            "text": "\n".join(b["text"] for b in blocks),
            "children": [],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_structure.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/structure.py tests/test_structure.py
git commit -m "feat: Stage 2 structure detection"
```

---

### Task 5: Stage 3 — Tier into markdown files

**Files:**
- Create: `app/pipeline/tier.py`
- Test: `tests/test_tier.py`

**Interfaces:**
- Consumes: `validate_user_path` (Plan 1 Task 2), `user_data_dir` (Plan 1 Task 2).
- Produces:
  - `def tier_document(tree: list[dict], user_data_dir: Path, doc_id: str, doc_title: str) -> list[str]` — writes the markdown hierarchy, returns list of leaf file paths (relative to user_data_dir).

- [ ] **Step 1: Write the failing test `tests/test_tier.py`**

```python
from pathlib import Path
from app.pipeline.tier import tier_document, slugify


def test_slugify_basic():
    assert slugify("Chapter 1: Combat!") == "01_chapter_1_combat"
    assert slugify("Magic & Spells") == "magic_spells"


def test_tier_writes_doc_index(tmp_dirs):
    tree = [
        {"title": "Chapter 1: Combat", "level": 1, "page_start": 1, "page_end": 2, "text": "Combat rules.", "children": [
            {"title": "1.1 Initiative", "level": 2, "page_start": 1, "page_end": 1, "text": "Roll initiative.", "children": []},
        ]},
    ]
    leaves = tier_document(tree, tmp_dirs["data"], "d1", "Bestiary")
    doc_index = (tmp_dirs["data"] / "d1" / "index.md").read_text()
    assert "Bestiary" in doc_index
    assert "Chapter 1: Combat" in doc_index
    assert len(leaves) == 1
    assert leaves[0].endswith("01_chapter_1_combat/01_1_1_initiative.md")


def test_tier_writes_chapter_index(tmp_dirs):
    tree = [
        {"title": "Combat", "level": 1, "page_start": 1, "page_end": 2, "text": "", "children": [
            {"title": "Initiative", "level": 2, "page_start": 1, "page_end": 1, "text": "Roll initiative.", "children": []},
            {"title": "Attacks", "level": 2, "page_start": 2, "page_end": 2, "text": "Attack rolls.", "children": []},
        ]},
    ]
    tier_document(tree, tmp_dirs["data"], "d1", "Book")
    chap_index = (tmp_dirs["data"] / "d1" / "01_combat" / "index.md").read_text()
    assert "Initiative" in chap_index
    assert "Attacks" in chap_index


def test_tier_leaf_has_content(tmp_dirs):
    tree = [
        {"title": "C1", "level": 1, "page_start": 1, "page_end": 1, "text": "content here", "children": []},
    ]
    leaves = tier_document(tree, tmp_dirs["data"], "d1", "Book")
    content = (tmp_dirs["data"] / "d1" / leaves[0]).read_text()
    assert "content here" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tier.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/pipeline/tier.py`**

```python
from pathlib import Path
import re


def slugify(title: str, order: int = 0) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s]", "", title).strip().lower().replace(" ", "_")
    s = re.sub(r"_+", "_", s).strip("_")
    prefix = f"{order:02d}_"
    return prefix + s if order else s


def tier_document(tree: list[dict], data_dir: Path, doc_id: str, doc_title: str) -> list[str]:
    doc_dir = data_dir / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    leaves = []

    def write_node(node, parent_dir: Path, order: int) -> None:
        slug = slugify(node["title"], order)
        if node["children"]:
            chap_dir = parent_dir / slug
            chap_dir.mkdir(exist_ok=True)
            chap_index_lines = [f"# {node['title']}\n"]
            for i, child in enumerate(node["children"], start=1):
                child_slug = slugify(child["title"], i)
                if child["children"]:
                    write_node(child, chap_dir, i)
                else:
                    leaf_path = chap_dir / f"{child_slug}.md"
                    leaf_path.write_text(f"# {child['title']}\n\n{child['text']}\n")
                    leaves.append(str(leaf_path.relative_to(data_dir)))
                chap_index_lines.append(f"- [{child['title']}]({child_slug}.md)\n")
            (chap_dir / "index.md").write_text("".join(chap_index_lines))
        else:
            leaf_path = parent_dir / f"{slug}.md"
            leaf_path.write_text(f"# {node['title']}\n\n{node['text']}\n")
            leaves.append(str(leaf_path.relative_to(data_dir)))

    doc_index_lines = [f"# {doc_title}\n\n"]
    for i, node in enumerate(tree, start=1):
        write_node(node, doc_dir, i)
        slug = slugify(node["title"], i)
        doc_index_lines.append(f"- [{node['title']}]({slug}/index.md)\n")
    (doc_dir / "index.md").write_text("".join(doc_index_lines))
    return leaves
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tier.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/tier.py tests/test_tier.py
git commit -m "feat: Stage 3 tier into markdown files"
```

---

### Task 6: Stage 4 — Enrich (per-leaf summary + keywords)

**Files:**
- Create: `app/pipeline/enrich.py`
- Test: `tests/test_enrich.py`

**Interfaces:**
- Consumes: `OllamaGateway` (mocked in tests).
- Produces:
  - `class Enricher` with `__init__(self, gateway: OllamaGateway)`.
  - `async def enrich_leaf(self, path: Path, page: int | None) -> dict` — reads file, calls `enrich` role, returns `{summary, keywords}`. Writes front-matter back to the file.
  - `async def enrich_all(self, leaf_paths: list[Path], page_map: dict) -> list[dict]` — enriches each, returns list of results.

- [ ] **Step 1: Write the failing test `tests/test_enrich.py`**

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.pipeline.enrich import Enricher


@pytest.mark.asyncio
async def test_enrich_leaf_writes_frontmatter(tmp_path):
    leaf = tmp_path / "goblin.md"
    leaf.write_text("# Goblin\n\nAC 15, HP 7, small humanoid.\n")
    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "Goblin stat block.", "keywords": ["goblin", "monster", "AC"]}'}})
    e = Enricher(gw)
    result = await e.enrich_leaf(leaf, page=42)
    assert result["summary"] == "Goblin stat block."
    assert "goblin" in result["keywords"]
    content = leaf.read_text()
    assert content.startswith("---\n")
    assert "summary:" in content
    assert "keywords:" in content
    assert "page: 42" in content
    assert "# Goblin" in content


@pytest.mark.asyncio
async def test_enrich_leaf_handles_bad_json(tmp_path):
    leaf = tmp_path / "x.md"
    leaf.write_text("# X\n\ntext\n")
    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": "not json at all"}})
    e = Enricher(gw)
    result = await e.enrich_leaf(leaf, page=1)
    assert result["summary"] == ""
    assert result["keywords"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_enrich.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/pipeline/enrich.py`**

```python
import json
import re
from pathlib import Path


class Enricher:
    def __init__(self, gateway):
        self.gateway = gateway

    async def enrich_leaf(self, path: Path, page: int | None = None) -> dict:
        content = path.read_text()
        prompt = (
            "Read this RPG manual section and produce a JSON object with "
            "a 1-2 sentence 'summary' and a list of 3-8 'keywords' (lowercase). "
            "Return ONLY valid JSON, no prose.\n\n"
            f"{content}"
        )
        resp = await self.gateway.call("enrich", prompt)
        raw = resp.get("message", {}).get("content", "")
        result = self._parse_json(raw)
        self._write_frontmatter(path, content, result, page)
        return result

    async def enrich_all(self, leaf_paths: list[Path], page_map: dict) -> list[dict]:
        results = []
        for p in leaf_paths:
            page = page_map.get(str(p))
            r = await self.enrich_leaf(p, page)
            results.append(r)
        return results

    @staticmethod
    def _parse_json(raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        return {"summary": "", "keywords": []}

    @staticmethod
    def _write_frontmatter(path: Path, content: str, result: dict, page: int | None) -> None:
        summary = result.get("summary", "")
        keywords = result.get("keywords", [])
        kw_yaml = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)
        fm = f"---\nsummary: \"{summary}\"\nkeywords: [{kw_yaml}]\n"
        if page is not None:
            fm += f"page: {page}\n"
        fm += f"---\n\n{content}"
        path.write_text(fm)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_enrich.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/enrich.py tests/test_enrich.py
git commit -m "feat: Stage 4 enrich with LLM summary + keywords"
```

---

### Task 7: Stage 5 — FTS5 index build

**Files:**
- Create: `app/pipeline/index.py`
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `insert_fts_row`, `delete_fts_rows_for_doc` (Plan 2 Task 1).
- Produces:
  - `def index_document(conn, leaf_paths: list[str], data_dir: Path, doc_id: str) -> None` — reads each leaf, parses front-matter, inserts FTS row. Deletes old rows for doc_id first.

- [ ] **Step 1: Write the failing test `tests/test_index.py`**

```python
from pathlib import Path
from app.storage.user_db import init_user_db, insert_fts_row
from app.pipeline.index import index_document, parse_frontmatter


def test_parse_frontmatter(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("---\nsummary: \"A goblin.\"\nkeywords: [goblin, monster]\npage: 42\n---\n\n# Goblin\n\nAC 15.\n")
    fm, body = parse_frontmatter(f)
    assert fm["summary"] == "A goblin."
    assert "goblin" in fm["keywords"]
    assert fm["page"] == 42
    assert "# Goblin" in body


def test_parse_no_frontmatter(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("# No FM\n\nJust text.\n")
    fm, body = parse_frontmatter(f)
    assert fm == {}
    assert "Just text" in body


def test_index_document_inserts_rows(tmp_dirs):
    from app.storage.user_db import init_user_db
    doc_dir = tmp_dirs["data"] / "d1" / "01_chapter"
    doc_dir.mkdir(parents=True)
    leaf = doc_dir / "01_section.md"
    leaf.write_text("---\nsummary: \"Goblin stats.\"\nkeywords: [goblin, AC]\npage: 42\n---\n\n# Goblin\n\nAC 15, HP 7.\n")
    conn = init_user_db(tmp_dirs["db"], "alice")
    index_document(conn, [str(leaf.relative_to(tmp_dirs["data"]))], tmp_dirs["data"], "d1")
    rows = conn.execute("SELECT path, title, summary, keywords FROM documents_fts").fetchall()
    assert len(rows) == 1
    assert "goblin" in rows[0]["keywords"].lower()
    assert "AC 15" in rows[0]["summary"] or "Goblin stats" in rows[0]["summary"]
    conn.close()


def test_index_document_replaces_old_rows(tmp_dirs):
    from app.storage.user_db import init_user_db, insert_fts_row
    conn = init_user_db(tmp_dirs["db"], "alice")
    insert_fts_row(conn, "data/d1/old.md", "Old", "s", "k", "old content")
    doc_dir = tmp_dirs["data"] / "d1" / "01_chapter"
    doc_dir.mkdir(parents=True)
    leaf = doc_dir / "01_section.md"
    leaf.write_text("# New\n\nNew content.\n")
    index_document(conn, [str(leaf.relative_to(tmp_dirs["data"]))], tmp_dirs["data"], "d1")
    rows = conn.execute("SELECT path FROM documents_fts").fetchall()
    assert len(rows) == 1
    assert "old" not in rows[0]["path"]
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_index.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/pipeline/index.py`**

```python
import re
from pathlib import Path
from app.storage.user_db import insert_fts_row, delete_fts_rows_for_doc


def parse_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + 5:]
    fm = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            val = val.strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("[") and val.endswith("]"):
                val = [x.strip().strip('"') for x in val[1:-1].split(",") if x.strip()]
            elif val.isdigit():
                val = int(val)
            fm[key.strip()] = val
    return fm, body


def index_document(conn, leaf_paths: list[str], data_dir: Path, doc_id: str) -> None:
    delete_fts_rows_for_doc(conn, doc_id)
    for rel in leaf_paths:
        full = data_dir / rel
        fm, body = parse_frontmatter(full)
        title_match = re.search(r"^# (.+)$", body, re.MULTILINE)
        title = title_match.group(1) if title_match else full.stem
        summary = fm.get("summary", "")
        keywords = fm.get("keywords", "")
        if isinstance(keywords, list):
            keywords = ", ".join(keywords)
        insert_fts_row(conn, rel, title, str(summary), str(keywords), body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_index.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/index.py tests/test_index.py
git commit -m "feat: Stage 5 FTS5 index build"
```

---

### Task 8: Pipeline runner (orchestrates 5 stages)

**Files:**
- Create: `app/pipeline/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `Extractor`, `Structurer`, `tier_document`, `Enricher`, `index_document`, user_db CRUD, shared_db queue.
- Produces:
  - `class PipelineRunner` with `__init__(self, gateway, data_dir, db_dir)`.
  - `async def run_job(self, job: dict) -> None` — runs all 5 stages for one job, updates doc status, completes the job. On error, marks failed with message.

- [ ] **Step 1: Write the failing test `tests/test_runner.py`**

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.pipeline.runner import PipelineRunner
from app.storage.shared_db import init_shared_db, enqueue_job, claim_next_job, get_job
from app.storage.user_db import init_user_db, create_collection, create_doc, get_doc
from fpdf import FPDF


def _make_pdf(path: Path):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, txt="Chapter 1: Combat")
    pdf.ln(20)
    pdf.cell(0, 10, txt="Goblins have AC 15 and HP 7.")
    pdf.output(str(path))


@pytest.mark.asyncio
async def test_runner_end_to_end(tmp_dirs):
    pdf_path = tmp_dirs["data"] / "test.pdf"
    _make_pdf(pdf_path)
    conn = init_shared_db(tmp_dirs["db"])
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Test Book", "sha123")
    uconn.close()
    jid = enqueue_job(conn, "alice", "d1", str(pdf_path))
    job = claim_next_job(conn)
    conn.close()

    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "Combat rules.", "keywords": ["combat", "goblin"]}'}})
    runner = PipelineRunner(gw, tmp_dirs["data"], tmp_dirs["db"])
    await runner.run_job(job)

    conn = init_shared_db(tmp_dirs["db"])
    assert get_job(conn, jid)["status"] == "done"
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], "alice")
    d = get_doc(uconn, "d1")
    assert d["status"] == "done"
    assert (tmp_dirs["data"] / "alice" / "d1" / "index.md").exists()
    uconn.close()


@pytest.mark.asyncio
async def test_runner_marks_failed_on_bad_pdf(tmp_dirs):
    bad_pdf = tmp_dirs["data"] / "bad.pdf"
    bad_pdf.write_bytes(b"not a pdf")
    conn = init_shared_db(tmp_dirs["db"])
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Bad", "h")
    uconn.close()
    jid = enqueue_job(conn, "alice", "d1", str(bad_pdf))
    job = claim_next_job(conn)
    conn.close()

    runner = PipelineRunner(gateway=None, data_dir=tmp_dirs["data"], db_dir=tmp_dirs["db"])
    await runner.run_job(job)

    conn = init_shared_db(tmp_dirs["db"])
    assert get_job(conn, jid)["status"] == "failed"
    assert get_job(conn, jid)["error"] is not None
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/pipeline/runner.py`**

```python
import asyncio
from pathlib import Path
from app.pipeline.extract import Extractor
from app.pipeline.structure import Structurer
from app.pipeline.tier import tier_document
from app.pipeline.enrich import Enricher
from app.pipeline.index import index_document
from app.storage.user_db import init_user_db, update_doc_status
from app.storage.shared_db import init_shared_db, complete_job
from app.storage.paths import user_data_dir


class PipelineRunner:
    def __init__(self, gateway, data_dir: Path, db_dir: Path):
        self.gateway = gateway
        self.data_dir = data_dir
        self.db_dir = db_dir

    async def run_job(self, job: dict) -> None:
        job_id = job["job_id"]
        user_id = job["user_id"]
        doc_id = job["doc_id"]
        pdf_path = Path(job["pdf_path"])
        conn = init_shared_db(self.db_dir)
        uconn = init_user_db(self.db_dir, user_id)
        try:
            update_doc_status(uconn, doc_id, "extracting")
            extractor = Extractor(self.gateway)
            blocks = extractor.extract(pdf_path)
            if not blocks or not any(b["text"].strip() for b in blocks):
                raise ValueError("no text extracted from PDF")

            update_doc_status(uconn, doc_id, "structuring")
            structurer = Structurer(self.gateway)
            tree = structurer.detect(blocks)

            update_doc_status(uconn, doc_id, "tiering")
            udata = user_data_dir(self.data_dir, user_id)
            leaf_paths = tier_document(tree, udata, doc_id, self._doc_title(uconn, doc_id))

            update_doc_status(uconn, doc_id, "enriching")
            if self.gateway is not None:
                enricher = Enricher(self.gateway)
                full_paths = [udata / p for p in leaf_paths]
                page_map = {}
                await enricher.enrich_all(full_paths, page_map)

            update_doc_status(uconn, doc_id, "indexing")
            index_document(uconn, leaf_paths, udata, doc_id)

            update_doc_status(uconn, doc_id, "done")
            complete_job(conn, job_id)
        except Exception as e:
            update_doc_status(uconn, doc_id, "failed")
            complete_job(conn, job_id, error=str(e))
        finally:
            uconn.close()
            conn.close()

    def _doc_title(self, uconn, doc_id: str) -> str:
        from app.storage.user_db import get_doc
        d = get_doc(uconn, doc_id)
        return d["title"] if d else doc_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runner.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/runner.py tests/test_runner.py
git commit -m "feat: pipeline runner orchestrating 5 stages"
```

---

### Task 9: Queue worker loop

**Files:**
- Create: `app/queue/__init__.py`
- Create: `app/queue/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `claim_next_job`, `complete_job` (Plan 2 Task 2), `PipelineRunner` (Task 8).
- Produces:
  - `class QueueWorker` with `__init__(self, runner: PipelineRunner, db_dir: Path, poll_interval: float = 2.0)`.
  - `async def run_once(self) -> bool` — claims one job, runs it, returns True if a job ran.
  - `async def run_forever(self) -> None` — loops `run_once` with sleep; stops on `KeyboardInterrupt`.

- [ ] **Step 1: Write the failing test `tests/test_worker.py`**

```python
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.queue.worker import QueueWorker
from app.storage.shared_db import init_shared_db, enqueue_job, claim_next_job


@pytest.mark.asyncio
async def test_run_once_no_jobs_returns_false(tmp_dirs):
    runner = MagicMock()
    runner.run_job = AsyncMock()
    w = QueueWorker(runner, tmp_dirs["db"], poll_interval=0.01)
    ran = await w.run_once()
    assert ran is False
    runner.run_job.assert_not_called()


@pytest.mark.asyncio
async def test_run_once_runs_job(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    enqueue_job(conn, "alice", "d1", "/x.pdf")
    conn.close()
    runner = MagicMock()
    runner.run_job = AsyncMock()
    w = QueueWorker(runner, tmp_dirs["db"], poll_interval=0.01)
    ran = await w.run_once()
    assert ran is True
    runner.run_job.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/queue/__init__.py` (empty) and `app/queue/worker.py`**

```python
# app/queue/__init__.py
```

```python
# app/queue/worker.py
import asyncio
from pathlib import Path
from app.storage.shared_db import init_shared_db, claim_next_job


class QueueWorker:
    def __init__(self, runner, db_dir: Path, poll_interval: float = 2.0):
        self.runner = runner
        self.db_dir = db_dir
        self.poll_interval = poll_interval

    async def run_once(self) -> bool:
        conn = init_shared_db(self.db_dir)
        try:
            job = claim_next_job(conn)
            if not job:
                return False
            conn.close()
            await self.runner.run_job(job)
            return True
        finally:
            if conn:
                conn.close()

    async def run_forever(self) -> None:
        try:
            while True:
                ran = await self.run_once()
                if not ran:
                    await asyncio.sleep(self.poll_interval)
        except KeyboardInterrupt:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/queue/__init__.py app/queue/worker.py tests/test_worker.py
git commit -m "feat: queue worker loop"
```

---

### Task 10: Upload endpoint + multi-file upload UI

**Files:**
- Modify: `app/web/routes.py` — add `POST /upload`, `GET /collections/:id`, `GET /collections/:id/upload`
- Create: `app/web/templates/upload.html`
- Create: `app/web/templates/collection.html`
- Test: `tests/test_upload_routes.py`

**Interfaces:**
- Consumes: `enqueue_job`, `create_doc`, `list_docs`, `list_collections` from earlier tasks.
- Produces:
  - `POST /upload` — accepts multiple files + `collection_id` (or `new_collection_name`), saves PDFs to `data/<user>/<doc_id>/original.pdf`, creates doc rows, enqueues jobs. Returns 303 to collection view.
  - `GET /collections/:id` — shows books in collection with status badges.
  - `GET /collections/:id/upload` — upload form with collection preselected.

- [ ] **Step 1: Write the failing test `tests/test_upload_routes.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection, list_collections, list_docs
from app.auth.passwords import hash_password


@pytest.fixture
def app_and_user(tmp_dirs, monkeypatch):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "PF")
    uconn.close()
    cfg = Config("http://localhost:11434", {}, tmp_dirs["data"], tmp_dirs["db"])
    monkeypatch.setattr("app.config.load_config", lambda *a: cfg)
    app = create_app(cfg, session_secret="s")
    return app, cid


@pytest.mark.asyncio
async def test_upload_single_pdf(app_and_user):
    app, cid = app_and_user
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, txt="Chapter 1: Test")
    import io
    buf = io.BytesIO()
    pdf.output(buf)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post(
            "/upload",
            data={"collection_id": cid},
            files=[("files", ("book.pdf", buf.getvalue(), "application/pdf"))],
        )
        assert r.status_code == 303
        assert f"/collections/{cid}" in r.headers["location"]
        uconn = init_user_db(tmp_dirs := app.state.config.db_dir, "alice")
        docs = list_docs(uconn, cid)
        assert len(docs) == 1
        assert docs[0]["status"] == "queued"
        uconn.close()


@pytest.mark.asyncio
async def test_upload_multiple_pdfs(app_and_user):
    app, cid = app_and_user
    from fpdf import FPDF
    def make_pdf(text):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(0, 10, txt=text)
        buf = io.BytesIO()
        pdf.output(buf)
        return buf.getvalue()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.post(
            "/upload",
            data={"collection_id": cid},
            files=[
                ("files", ("a.pdf", make_pdf("Chapter A"), "application/pdf")),
                ("files", ("b.pdf", make_pdf("Chapter B"), "application/pdf")),
            ],
        )
        assert r.status_code == 303
        uconn = init_user_db(app.state.config.db_dir, "alice")
        docs = list_docs(uconn, cid)
        assert len(docs) == 2
        uconn.close()


@pytest.mark.asyncio
async def test_collection_view_shows_books(app_and_user):
    app, cid = app_and_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get(f"/collections/{cid}")
        assert r.status_code == 200
        assert "PF" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_upload_routes.py -v`
Expected: FAIL (no `/upload` route)

- [ ] **Step 3: Write `app/web/templates/upload.html` and `app/web/templates/collection.html`**

```html
<!-- app/web/templates/upload.html -->
{% extends "base.html" %}
{% block title %}Upload — RPG Master{% endblock %}
{% block content %}
<h2>Upload books to {{ collection.name }}</h2>
<form method="post" action="/upload" enctype="multipart/form-data">
  <input type="hidden" name="collection_id" value="{{ collection.collection_id }}">
  <label>PDF files<input type="file" name="files" accept="application/pdf" multiple required></label>
  <button type="submit">Upload</button>
</form>
<p><a href="/collections/{{ collection.collection_id }}">Back to collection</a></p>
{% endblock %}
```

```html
<!-- app/web/templates/collection.html -->
{% extends "base.html" %}
{% block title %}{{ collection.name }} — RPG Master{% endblock %}
{% block content %}
<header>
  <h2>{{ collection.name }}</h2>
  <a href="/collections/{{ collection.collection_id }}/upload" role="button">Upload books</a>
</header>
{% if docs %}
<table>
  <thead><tr><th>Title</th><th>Status</th><th>Actions</th></tr></thead>
  <tbody>
    {% for d in docs %}
    <tr>
      <td><a href="/docs/{{ d.doc_id }}">{{ d.title }}</a></td>
      <td><small>{{ d.status }}</small></td>
      <td>
        <form method="post" action="/docs/{{ d.doc_id }}/reprocess" style="display:inline"><button class="secondary" type="submit">Reprocess</button></form>
        <form method="post" action="/docs/{{ d.doc_id }}/delete" style="display:inline"><button class="secondary" type="submit">Delete</button></form>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p><em>No books yet. <a href="/collections/{{ collection.collection_id }}/upload">Upload one</a>.</em></p>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Modify `app/web/routes.py` — add upload + collection routes**

Add imports at top:

```python
import hashlib
import uuid
from fastapi import UploadFile, File, Form
from app.storage.shared_db import init_shared_db, enqueue_job
from app.storage.user_db import (
    init_user_db, list_collections, create_collection, list_docs, get_doc as _get_doc,
    create_doc, delete_doc as _delete_doc, update_doc_status,
)
from app.storage.paths import user_data_dir
```

Add routes:

```python
@router.get("/collections/{collection_id}")
async def collection_view(request: Request, collection_id: str):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    cols = list_collections(conn)
    col = next((c for c in cols if c["collection_id"] == collection_id), None)
    docs = list_docs(conn, collection_id)
    conn.close()
    if not col:
        return RedirectResponse("/", status_code=303)
    return _templates.TemplateResponse(
        "collection.html",
        {"request": request, "user_id": uid, "collection": col, "docs": docs},
    )


@router.get("/collections/{collection_id}/upload")
async def upload_form(request: Request, collection_id: str):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    cols = list_collections(conn)
    col = next((c for c in cols if c["collection_id"] == collection_id), None)
    conn.close()
    if not col:
        return RedirectResponse("/", status_code=303)
    return _templates.TemplateResponse(
        "upload.html",
        {"request": request, "user_id": uid, "collection": col},
    )


@router.post("/upload")
async def upload(request: Request, collection_id: str = Form(...), files: list[UploadFile] = File(...)):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    udata = user_data_dir(_data_dir, uid)
    sconn = init_shared_db(_db_dir)
    uconn = init_user_db(_db_dir, uid)
    try:
        for f in files:
            if not f.filename or not f.filename.lower().endswith(".pdf"):
                continue
            data = await f.read()
            sha = hashlib.sha256(data).hexdigest()
            doc_id = uuid.uuid4().hex
            doc_dir = udata / doc_id
            doc_dir.mkdir(parents=True, exist_ok=True)
            (doc_dir / "original.pdf").write_bytes(data)
            create_doc(uconn, doc_id, collection_id, f.filename.rsplit(".", 1)[0], sha)
            enqueue_job(sconn, uid, doc_id, str(doc_dir / "original.pdf"))
    finally:
        uconn.close()
        sconn.close()
    return RedirectResponse(f"/collections/{collection_id}", status_code=303)


@router.post("/docs/{doc_id}/reprocess")
async def reprocess_doc(request: Request, doc_id: str):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    uconn = init_user_db(_db_dir, uid)
    sconn = init_shared_db(_db_dir)
    try:
        d = _get_doc(uconn, doc_id)
        if not d:
            return RedirectResponse("/", status_code=303)
        pdf_path = _data_dir / uid / doc_id / "original.pdf"
        update_doc_status(uconn, doc_id, "queued")
        enqueue_job(sconn, uid, doc_id, str(pdf_path))
    finally:
        uconn.close()
        sconn.close()
    return RedirectResponse(f"/collections/{d['collection_id']}", status_code=303)


@router.post("/docs/{doc_id}/delete")
async def delete_doc_route(request: Request, doc_id: str):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    uconn = init_user_db(_db_dir, uid)
    try:
        d = _get_doc(uconn, doc_id)
        if d:
            _delete_doc(uconn, doc_id)
    finally:
        uconn.close()
    import shutil
    doc_dir = _data_dir / uid / doc_id
    if doc_dir.exists():
        shutil.rmtree(doc_dir)
    return RedirectResponse("/", status_code=303)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_upload_routes.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add app/web/routes.py app/web/templates/upload.html app/web/templates/collection.html tests/test_upload_routes.py
git commit -m "feat: upload endpoint + collection view + multi-file upload"
```

---

### Task 11: Doc view (status + tree browser)

**Files:**
- Create: `app/web/templates/doc.html`
- Modify: `app/web/routes.py` — add `GET /docs/:id`
- Test: `tests/test_doc_routes.py`

**Interfaces:**
- Consumes: `get_doc`, `validate_user_path`.
- Produces: `GET /docs/:id` — shows doc status, a browseable tree of the doc's markdown, and raw leaf content on click.

- [ ] **Step 1: Write the failing test `tests/test_doc_routes.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config
from app.storage.shared_db import init_shared_db, create_user
from app.storage.user_db import init_user_db, create_collection, create_doc, update_doc_status
from app.auth.passwords import hash_password


@pytest.fixture
def app_and_doc(tmp_dirs, monkeypatch):
    conn = init_shared_db(tmp_dirs["db"])
    create_user(conn, "alice", hash_password("pw"))
    conn.close()
    uconn = init_user_db(tmp_dirs["db"], "alice")
    cid = create_collection(uconn, "C")
    create_doc(uconn, "d1", cid, "Book", "h")
    update_doc_status(uconn, "d1", "done")
    uconn.close()
    doc_dir = tmp_dirs["data"] / "alice" / "d1"
    doc_dir.mkdir(parents=True)
    (doc_dir / "index.md").write_text("# Book\n\n- [Chapter 1](01_chapter_1/index.md)\n")
    chap = doc_dir / "01_chapter_1"
    chap.mkdir()
    (chap / "index.md").write_text("# Chapter 1\n\n- [Section](01_section.md)\n")
    (chap / "01_section.md").write_text("# Section\n\nContent here.\n")
    cfg = Config("http://x", {}, tmp_dirs["data"], tmp_dirs["db"])
    monkeypatch.setattr("app.config.load_config", lambda *a: cfg)
    return create_app(cfg, "s"), "d1"


@pytest.mark.asyncio
async def test_doc_view_shows_status(app_and_doc):
    app, did = app_and_doc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get(f"/docs/{did}")
        assert r.status_code == 200
        assert "Book" in r.text
        assert "done" in r.text.lower()


@pytest.mark.asyncio
async def test_doc_view_shows_tree(app_and_doc):
    app, did = app_and_doc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get(f"/docs/{did}")
        assert "Chapter 1" in r.text


@pytest.mark.asyncio
async def test_doc_view_unknown_redirects(app_and_doc):
    app, _ = app_and_doc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "alice", "password": "pw"})
        r = await client.get("/docs/nonexistent")
        assert r.status_code == 303
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_doc_routes.py -v`
Expected: FAIL

- [ ] **Step 3: Write `app/web/templates/doc.html`**

```html
<!-- app/web/templates/doc.html -->
{% extends "base.html" %}
{% block title %}{{ doc.title }} — RPG Master{% endblock %}
{% block content %}
<header>
  <h2>{{ doc.title }}</h2>
  <small>Status: <strong>{{ doc.status }}</strong></small>
</header>
{% if tree %}
<h3>Contents</h3>
<ul>
  {% for entry in tree %}
  <li><a href="/docs/{{ doc.doc_id }}/view?path={{ entry.path }}">{{ entry.title }}</a></li>
  {% endfor %}
</ul>
{% else %}
<p><em>Document is still processing or has no content yet.</em></p>
{% endif %}
<p><a href="/collections/{{ doc.collection_id }}">Back to collection</a></p>
{% endblock %}
```

- [ ] **Step 4: Modify `app/web/routes.py` — add doc view routes**

Add a helper to walk the doc tree and a route:

```python
@router.get("/docs/{doc_id}")
async def doc_view(request: Request, doc_id: str):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    uconn = init_user_db(_db_dir, uid)
    d = _get_doc(uconn, doc_id)
    uconn.close()
    if not d:
        return RedirectResponse("/", status_code=303)
    tree = _build_doc_tree(_data_dir, uid, doc_id)
    return _templates.TemplateResponse(
        "doc.html",
        {"request": request, "user_id": uid, "doc": d, "tree": tree},
    )


@router.get("/docs/{doc_id}/view")
async def doc_view_leaf(request: Request, doc_id: str, path: str):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    try:
        full = validate_user_path(_data_dir, uid, str(_data_dir / uid / doc_id / path))
    except ValueError:
        return RedirectResponse(f"/docs/{doc_id}", status_code=303)
    content = full.read_text() if full.exists() else "(file not found)"
    return _templates.TemplateResponse(
        "doc_leaf.html",
        {"request": request, "user_id": uid, "doc_id": doc_id, "path": path, "content": content},
    )


def _build_doc_tree(data_dir: Path, uid: str, doc_id: str) -> list[dict]:
    doc_root = data_dir / uid / doc_id
    if not doc_root.exists():
        return []
    entries = []
    for chap_dir in sorted(doc_root.iterdir()):
        if chap_dir.is_dir():
            idx = chap_dir / "index.md"
            title = chap_dir.name
            if idx.exists():
                first_line = idx.read_text().splitlines()[0]
                if first_line.startswith("# "):
                    title = first_line[2:]
            entries.append({"title": title, "path": f"{chap_dir.name}/index.md"})
    return entries
```

Create `app/web/templates/doc_leaf.html`:

```html
{% extends "base.html" %}
{% block title %}{{ path }} — RPG Master{% endblock %}
{% block content %}
<a href="/docs/{{ doc_id }}">Back to doc</a>
<pre>{{ content }}</pre>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_doc_routes.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add app/web/routes.py app/web/templates/doc.html app/web/templates/doc_leaf.html tests/test_doc_routes.py
git commit -m "feat: doc view with status + tree browser"
```

---

### Task 12: Wire worker into app startup

**Files:**
- Modify: `app/main.py` — start queue worker as a background task on startup.
- Modify: `app/__main__.py` — construct gateway + runner + worker.
- Test: `tests/test_app_startup.py`

**Interfaces:**
- Produces: app starts with a background `QueueWorker` polling for jobs. Gateway is constructed from config and attached to `app.state.gateway`.

- [ ] **Step 1: Write the failing test `tests/test_app_startup.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app
from app.config import Config


@pytest.mark.asyncio
async def test_app_starts_with_worker(tmp_dirs, monkeypatch):
    cfg = Config("http://localhost:11434", {"query": "m"}, tmp_dirs["data"], tmp_dirs["db"])
    app = create_app(cfg, session_secret="s")
    assert hasattr(app.state, "worker")
    assert app.state.worker is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_app_startup.py -v`
Expected: FAIL

- [ ] **Step 3: Modify `app/main.py`**

```python
from fastapi import FastAPI
from pathlib import Path
import asyncio
from app.config import Config
from app.auth.middleware import AuthMiddleware
from app.auth.routes import router as auth_router, init_auth_routes
from app.web.routes import router as web_router, init_web_routes
from app.gateway.ollama import OllamaGateway
from app.pipeline.runner import PipelineRunner
from app.queue.worker import QueueWorker


def create_app(cfg: Config, session_secret: str) -> FastAPI:
    app = FastAPI(title="RPG Master")
    app.state.config = cfg
    app.state.session_secret = session_secret
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.db_dir.mkdir(parents=True, exist_ok=True)
    init_auth_routes(cfg.db_dir)
    init_web_routes(cfg.db_dir, cfg.data_dir)
    app.add_middleware(AuthMiddleware, session_secret=session_secret)
    app.include_router(auth_router)
    app.include_router(web_router)

    gateway = OllamaGateway(cfg.ollama_host, cfg.models)
    app.state.gateway = gateway
    runner = PipelineRunner(gateway, cfg.data_dir, cfg.db_dir)
    worker = QueueWorker(runner, cfg.db_dir, poll_interval=2.0)
    app.state.worker = worker

    @app.on_event("startup")
    async def _start_worker():
        asyncio.create_task(worker.run_forever())

    @app.on_event("shutdown")
    async def _close_gateway():
        await gateway.close()

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_app_startup.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `pytest -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_app_startup.py
git commit -m "feat: wire queue worker into app startup"
```

---

## Self-Review (Plan 2)

**Spec coverage:** Pipeline 5 stages (spec §2) ✓, queue (spec §1) ✓, multi-upload (spec §5) ✓, collections view ✓, doc view ✓, FTS5 index ✓. Conversational agent deferred to Plan 3.

**Placeholder scan:** No TBD/TODO. All steps have complete code.

**Type consistency:** `index_document(conn, leaf_paths, data_dir, doc_id)` signature consistent across Task 7 and Task 8. `PipelineRunner(gateway, data_dir, db_dir)` consistent across Task 8, 9, 12. `QueueWorker(runner, db_dir, poll_interval)` consistent.

**Scope:** Processing pipeline complete. Next: Plan 3 (Query Agent) builds on this.