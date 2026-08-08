# Move Enrichment Worker Off the Web Event Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the enrichment pipeline in a separate thread so the web app's event loop stays responsive during long enrichment jobs (serial, one-at-a-time — no parallel LLM calls).

**Architecture:** The `QueueWorker` currently runs via `asyncio.create_task(worker.run_forever())` in the **same event loop** as FastAPI (`app/main.py`). The pipeline does synchronous blocking work (poppler/tesseract subprocesses, serial LLM enrichment, SQLite commits), stalling page loads and agent chat. Fix: launch the worker in a **dedicated daemon thread** running its own event loop (`threading.Thread(target=lambda: asyncio.run(worker.run_forever()))`). Three prerequisites (agent consensus, 3/3): (1) **WAL + busy_timeout** on both SQLite DBs so the web loop and worker thread write concurrently without `database is locked`; (2) **thread-safe stop** — `loop.add_signal_handler` raises off the main thread, so stop must use `call_soon_threadsafe`; (3) a **dedicated `OllamaGateway` for the worker** — `httpx.AsyncClient` is bound to the loop that created it, and the shared `app.state.gateway` would raise `RuntimeError: attached to a different loop`.

**Cross-checked by claude/codex/pi (3/3 approved with fixes):** I1 — gateway close must run in the same loop that created it (wrap `run_forever` + `close` in one coroutine); I2 — "force-cancel" is not real (stop only lands between jobs); document accurate shutdown semantics; I3 — tests must genuinely fail pre-change (use real lifespan, assert clean exit); I4 — build the worker gateway at `create_app` time (lazy AsyncClient, no runner swap); I5 — `uvicorn --workers > 1` breaks the serial constraint (doc note); codex — heartbeat must keep firing during long sync stages (wrap sync stages in `asyncio.to_thread` inside `run_job`); pi — WAL host-volume backup caveat belongs in docs.

**Tech Stack:** Python `threading` + `asyncio` (stdlib), SQLite WAL mode, existing `QueueWorker`/lease/heartbeat system, existing `OllamaGateway`. Zero new dependencies.

## Global Constraints

- Enrichment stays **strictly serial** (one job at a time) — no parallel LLM calls.
- Zero new runtime dependencies.
- Existing tests must stay green: `test_worker.py`, `test_worker_lease.py`, `test_app_startup.py`, `test_runner.py`.
- The lease/heartbeat job-claim system (`claim_next_job`, `heartbeat_job`, `JOB_LEASE_SECONDS`, `MAX_JOB_ATTEMPTS`) is unchanged — a job abandoned mid-flight is reclaimed on next start.
- `app.state.worker` must remain set so `test_app_startup.py` passes.
- SQLite connections are per-call and never shared across threads — `check_same_thread` stays default.

---

### Task 1: SQLite WAL + busy_timeout (concurrency foundation)

**Files:**
- Modify: `app/storage/migrations.py` (`init_shared_db_with_migrations` ~line 250, `init_user_db_with_migrations` ~line 240)
- Test: `tests/test_wal_pragmas.py` (new)

**Interfaces:**
- Produces: `_apply_connection_pragmas(conn)` helper — sets `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=30000`, `PRAGMA synchronous=NORMAL` on every new connection.

- [ ] **Step 1: Write the failing test**

```python
"""WAL + busy_timeout pragmas on every SQLite connection."""

import sqlite3
from app.storage.migrations import init_shared_db_with_migrations, init_user_db_with_migrations


def test_shared_db_uses_wal(tmp_path):
    conn = init_shared_db_with_migrations(tmp_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    conn.close()


def test_user_db_uses_wal(tmp_path):
    conn = init_user_db_with_migrations(tmp_path, "alice")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    conn.close()


def test_concurrent_writes_no_lock(tmp_path):
    """Two threads writing the same DB must not hit 'database is locked'."""
    import threading
    errors = []

    def writer(name):
        try:
            for _ in range(20):
                conn = init_shared_db_with_migrations(tmp_path)
                conn.execute("INSERT INTO app_config (key, value) VALUES (?, ?)", (f"{name}-{_}", "v"))
                conn.commit()
                conn.close()
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=writer, args=("a",))
    t2 = threading.Thread(target=writer, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert errors == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wal_pragmas.py -v`
Expected: FAIL — journal_mode is `delete` (default), busy_timeout is 5000

- [ ] **Step 3: Implement the pragma helper**

```python
def _apply_connection_pragmas(conn: sqlite3.Connection) -> None:
    """WAL + busy_timeout so the web loop and worker thread can write
    concurrently without 'database is locked'."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
```

Call it in both `init_shared_db_with_migrations` and `init_user_db_with_migrations` immediately after `sqlite3.connect(...)` and before running migrations.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wal_pragmas.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `pytest tests/ -q -m "not e2e"`
Expected: all pass (WAL is transparent to existing tests)

- [ ] **Step 6: Commit**

```bash
git add app/storage/migrations.py tests/test_wal_pragmas.py
git commit -m "feat(db): WAL + busy_timeout on all SQLite connections (worker-thread concurrency)"
```

---

### Task 2: Thread-safe stop for QueueWorker

**Files:**
- Modify: `app/queue/worker.py`
- Test: `tests/test_worker.py` (existing — must stay green), `tests/test_worker_thread.py` (new, Task 4)

**Interfaces:**
- Consumes: nothing new
- Produces: `QueueWorker.stop()` becomes cross-thread-safe; `run_forever()` no longer installs signal handlers (uvicorn owns SIGTERM/SIGINT for the app; the worker thread's loop cannot use `add_signal_handler`).

- [ ] **Step 1: Write the failing test**

```python
def test_stop_from_other_thread(tmp_path):
    """stop() must work when called from a different thread than run_forever."""
    import threading
    runner = MagicMock()
    runner.run_job = AsyncMock()
    w = QueueWorker(runner, tmp_path, poll_interval=0.01)
    t = threading.Thread(target=lambda: asyncio.run(w.run_forever()))
    t.start()
    time.sleep(0.2)
    w.stop()
    t.join(timeout=5)
    assert not t.is_alive()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker_thread.py::test_stop_from_other_thread -v`
Expected: FAIL — `add_signal_handler` raises `RuntimeError` off the main thread, or stop doesn't reach the loop

- [ ] **Step 3: Implement**

```python
# In __init__:
self._loop: asyncio.AbstractEventLoop | None = None

# In run_forever(), first line:
self._loop = asyncio.get_running_loop()
# DELETE the add_signal_handler block entirely (uvicorn owns signals).

# stop():
def stop(self) -> None:
    if self._loop is not None and self._loop.is_running():
        self._loop.call_soon_threadsafe(self._stop_event.set)
    else:
        self._stop_event.set()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker.py tests/test_worker_lease.py tests/test_worker_thread.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/queue/worker.py tests/test_worker_thread.py
git commit -m "feat(worker): thread-safe stop, drop signal handlers (uvicorn owns signals)"
```

---

### Task 3: Dedicated worker gateway + thread launcher in create_app

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_app_startup.py` (existing — must stay green), `tests/test_worker_thread.py` (extend)

**Interfaces:**
- Consumes: `OllamaGateway`, `QueueWorker`, `PipelineRunner`
- Produces: `app.state.worker` (unchanged), `app.state.worker_thread` (new), `app.state.worker_gateway` (new). Startup launches the worker thread; shutdown stops it gracefully.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_worker_runs_in_thread_not_blocking_loop(tmp_dirs, test_config):
    """A slow job must not block the app's event loop."""
    import time as time_mod
    from app.main import create_app
    from app.storage.shared_db import init_shared_db, enqueue_job

    app = create_app(test_config, "testsecret")
    # Stub the runner with a slow job
    slow_runner = MagicMock()
    async def slow_run(job):
        time_mod.sleep(1.0)  # blocking sleep — would stall the loop if in-loop
        return None
    slow_runner.run_job = slow_run
    app.state.worker.runner = slow_runner

    conn = init_shared_db(test_config.db_dir)
    enqueue_job(conn, "alice", "d1", "/x.pdf")
    conn.close()

    # Start the worker thread manually (lifespan not running in tests)
    import threading
    t = threading.Thread(target=lambda: asyncio.run(app.state.worker.run_forever()), daemon=True)
    t.start()
    try:
        t0 = time_mod.monotonic()
        # The app loop must stay responsive while the worker sleeps
        await asyncio.sleep(0.2)
        elapsed = time_mod.monotonic() - t0
        assert elapsed < 0.5, f"event loop blocked for {elapsed:.2f}s"
    finally:
        app.state.worker.stop()
        t.join(timeout=5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker_thread.py::test_worker_runs_in_thread_not_blocking_loop -v`
Expected: FAIL — with the current in-loop worker, `asyncio.sleep(0.2)` takes ~1.2s

- [ ] **Step 3: Implement**

```python
# In create_app, replace the startup/shutdown blocks:

    runner = PipelineRunner(gateway, cfg.data_dir, cfg.db_dir)
    worker = QueueWorker(runner, cfg.db_dir, poll_interval=WORKER_POLL_INTERVAL)
    app.state.worker = worker
    # Build the worker gateway at create_app time (lazy AsyncClient — no
    # loop binding until first use; no runner swap needed).
    worker_gateway = OllamaGateway(cfg.ollama_host, cfg.models, num_ctx=cfg.num_ctx)
    app.state.worker_gateway = worker_gateway

    @app.on_event("startup")
    async def _start_worker() -> None:
        worker.runner = PipelineRunner(worker_gateway, cfg.data_dir, cfg.db_dir)

        async def _run_worker() -> None:
            try:
                await worker.run_forever()
            finally:
                # Close in the SAME loop that created the client (I1)
                await worker_gateway.close()

        app.state.worker_thread = threading.Thread(
            target=lambda: asyncio.run(_run_worker()), daemon=True
        )
        app.state.worker_thread.start()

    @app.on_event("shutdown")
    async def _close_gateway() -> None:
        worker.stop()
        t = getattr(app.state, "worker_thread", None)
        if t is not None:
            t.join(timeout=30)
        await gateway.close()
```

Note: `app.state.worker.runner` is swapped to the worker-gateway runner at startup so the thread uses its own gateway; the web app keeps `app.state.gateway`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker_thread.py tests/test_app_startup.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `pytest tests/ -q -m "not e2e"`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_worker_thread.py
git commit -m "feat(worker): run enrichment in a dedicated thread with its own gateway"
```

---

### Task 4: Graceful shutdown + zombie-thread guard

**Files:**
- Modify: `app/main.py` (shutdown path), `tests/test_worker_thread.py` (extend)

**Interfaces:**
- Consumes: Task 3's `app.state.worker_thread`
- Produces: shutdown does `stop() → join(30) → (if alive) cancel via call_soon_threadsafe → join(5)`; no zombie threads.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_shutdown_leaves_no_zombie_thread(tmp_dirs, test_config):
    from app.main import create_app
    app = create_app(test_config, "testsecret")
    # Simulate the startup wiring
    import threading
    app.state.worker_thread = threading.Thread(target=lambda: None, daemon=True)
    app.state.worker_thread.start()
    # Run the shutdown hook
    await app.router.shutdown()
    assert not app.state.worker_thread.is_alive()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker_thread.py::test_shutdown_leaves_no_zombie_thread -v`
Expected: FAIL — shutdown hook doesn't join the thread

- [ ] **Step 3: Implement**

```python
    @app.on_event("shutdown")
    async def _close_gateway() -> None:
        worker.stop()
        t = getattr(app.state, "worker_thread", None)
        if t is not None:
            t.join(timeout=30)
        # NOTE (I2): stop() only lands between jobs — a job mid-flight runs
        # to completion (or until the process exits; the daemon flag
        # guarantees exit). The lease reclaims abandoned jobs on next start.
        # A thread stuck in a sync subprocess (tesseract/poppler) cannot be
        # interrupted from outside — task.cancel only lands at the next await.
        await gateway.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker_thread.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_worker_thread.py
git commit -m "feat(worker): graceful shutdown — stop, join, force-cancel fallback"
```

---

### Task 5: Heartbeat during long sync stages (codex finding)

**Files:**
- Modify: `app/pipeline/runner.py` (`run_job` — wrap sync stages in `asyncio.to_thread`)
- Test: `tests/test_worker_lease.py` (extend — long job keeps lease fresh)

**Interfaces:**
- Consumes: nothing new
- Produces: `run_job`'s sync stages (extract, structure, index) run via `asyncio.to_thread` so the worker's own loop stays free to fire heartbeats during long jobs (a >5-min job would otherwise exceed `JOB_LEASE_SECONDS=300`).

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_heartbeat_fires_during_long_sync_stage(tmp_dirs):
    """A job with a long sync stage must keep its lease fresh (heartbeat fires)."""
    from app.storage.shared_db import init_shared_db, enqueue_job, claim_next_job, get_job
    from app.pipeline.runner import PipelineRunner
    from unittest.mock import MagicMock, AsyncMock
    import time

    conn = init_shared_db(tmp_dirs["db"])
    jid = enqueue_job(conn, "alice", "d1", "/x.pdf")
    job = claim_next_job(conn)
    conn.close()

    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "s", "keywords": []}'}})
    runner = PipelineRunner(gw, tmp_dirs["data"], tmp_dirs["db"])
    # Patch the extract stage to block for 2s (longer than heartbeat interval)
    async def slow_extract(pdf_path):
        time.sleep(2.0)
        return [{"page": 1, "text": "Chapter 1: Combat", "ocr": False}]
    runner._extract = slow_extract  # or patch Extractor.extract

    # Run the job in a thread with its own loop (like the real worker)
    import threading, asyncio
    t = threading.Thread(target=lambda: asyncio.run(runner.run_job(job)), daemon=True)
    t.start()
    time.sleep(1.0)  # mid-job
    conn = init_shared_db(tmp_dirs["db"])
    j = get_job(conn, jid)
    conn.close()
    assert j["lease_expires_at"] is not None, "lease must be refreshed mid-job"
    t.join(timeout=10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_worker_lease.py::test_heartbeat_fires_during_long_sync_stage -v`
Expected: FAIL — with the sync stage blocking the worker loop, the heartbeat task never fires

- [ ] **Step 3: Implement**

In `app/pipeline/runner.py::run_job`, wrap the blocking sync stages in `asyncio.to_thread`:

```python
# Stage 1: Extract (sync subprocess calls — poppler/tesseract)
blocks = await asyncio.to_thread(extractor.extract, pdf_path)
# Stage 2: Structure (sync LLM call via gateway — already async, keep as-is)
# Stage 3: Tier (sync file writes)
leaf_paths = await asyncio.to_thread(tier_document, tree, udata, doc_id, doc_title)
# Stage 4: Enrich (async LLM calls — keep as-is; per-leaf commits are short)
```

The worker's own loop stays free to run the heartbeat task during stages 1 and 3. Stage 4's LLM calls are already async (`await gateway.call`), so the loop is free there too.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_worker_lease.py tests/test_runner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/pipeline/runner.py tests/test_worker_lease.py
git commit -m "feat(worker): offload sync pipeline stages so heartbeats keep firing"
```

---

### Task 6: End-to-end verification + docs

**Files:**
- Modify: `README.md` (operations section), `config.yaml` (comment about distinct enrich model)

- [ ] **Step 1: Full regression run**

Run: `pytest tests/ -q -m "not e2e"`
Expected: all pass

- [ ] **Step 2: Manual smoke test (dev)**

Run: `docker compose up -d --build prod`; upload a large PDF; while it enriches, load the library page and ask a chat question — both must stay responsive. Watch `docker compose logs -f prod` for worker heartbeat + job progress.

- [ ] **Step 3: Document**

README operations section: worker now runs in a background thread; container stop waits up to 30s, then the daemon thread is abandoned — a job mid-flight runs to completion or is reclaimed by the lease on next start (attempts capped at 3). A thread stuck in a sync subprocess (tesseract/poppler) cannot be interrupted from outside. WAL caveat: `journal_mode=WAL` silently falls back to `delete` on unsupported filesystems (NFS-type mounts) — this deployment uses ext4 bind mounts, which support WAL fine; `backup.sh` uses `sqlite3 .backup` (WAL-safe consistent snapshot). Config note: if `models.enrich` is the same model the agent chat uses, chat still queues behind enrichment at the Ollama level — give enrichment a distinct model in `config.yaml` for full concurrency. `uvicorn --workers > 1` would spawn a worker thread per process → concurrent jobs — keep single-process (`python -m app`).

- [ ] **Step 4: Commit**

```bash
git add README.md config.yaml
git commit -m "docs: worker-thread architecture, shutdown semantics, enrich-model note"
```

---

## Summary

| Task | What | Effort |
|------|------|--------|
| 1 | WAL + busy_timeout (concurrency foundation) | ~1h |
| 2 | Thread-safe stop, drop signal handlers | ~1h |
| 3 | Dedicated worker gateway + thread launcher | ~2h |
| 4 | Graceful shutdown (accurate semantics) | ~1h |
| 5 | Heartbeat during long sync stages (codex finding) | ~1h |
| 6 | E2E verification + docs | ~1h |
| **Total** | | **~7h** |

**Key risks (agent consensus):** (1) SQLite concurrency — WAL + busy_timeout is the prerequisite, do Task 1 first; (2) `httpx.AsyncClient` loop binding — the worker needs its own gateway, closed in the same loop (I1); (3) `add_signal_handler` off the main thread — removed, uvicorn owns signals; (4) shutdown semantics — stop only lands between jobs; a mid-flight job runs to completion or is lease-reclaimed (I2); (5) heartbeat starvation during long sync stages — offload via `asyncio.to_thread` (codex); (6) Ollama contention — if enrich and chat share a model, chat queues at the Ollama level (config-level, documented).

**Rejected alternatives (agent consensus):** separate OS process (pi's pick — most robust long-term, but ~10h and overkill for one machine; the lease system already supports it later if needed); async-ify the pipeline (most invasive, no benefit — sync subprocess/SQLite still need offloading).
