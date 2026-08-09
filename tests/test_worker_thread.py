"""Worker thread-safety tests — stop() must work cross-thread."""

import asyncio
import threading
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.queue.worker import QueueWorker


def test_stop_from_other_thread(tmp_path):
    """stop() must work when called from a different thread than run_forever."""
    runner = MagicMock()
    runner.run_job = AsyncMock()
    w = QueueWorker(runner, tmp_path, poll_interval=0.01)

    errors: list[BaseException] = []

    def target() -> None:
        try:
            asyncio.run(w.run_forever())
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=target)
    t.start()
    time.sleep(0.2)
    w.stop()
    t.join(timeout=5)

    assert errors == [], f"worker thread crashed: {errors}"
    assert w._loop is not None, "run_forever must record its running loop"
    assert not t.is_alive(), "worker thread must exit cleanly after stop()"


@pytest.mark.asyncio
async def test_worker_runs_in_thread_not_blocking_loop(tmp_dirs, test_config):
    """A slow job must not block the app's event loop (real app lifespan)."""
    import time as time_mod
    from unittest.mock import MagicMock

    from app.main import create_app
    from app.storage.shared_db import init_shared_db, enqueue_job, get_job, complete_job

    app = create_app(test_config, "testsecret")
    slow_runner = MagicMock()

    async def slow_run(job):
        time_mod.sleep(1.0)  # blocking sleep — stalls the loop if in-loop
        conn = init_shared_db(test_config.db_dir)
        complete_job(conn, job["job_id"])
        conn.close()
        return None

    slow_runner.run_job = slow_run

    async with app.router.lifespan_context(app):
        # Swap the stub before enqueuing: the thread's first poll sees it.
        app.state.worker.runner = slow_runner
        conn = init_shared_db(test_config.db_dir)
        job_id = enqueue_job(conn, "alice", "d1", "/x.pdf")
        conn.close()

        # Measure loop responsiveness while the worker processes the job.
        # With an in-loop worker, the 1s blocking sleep stalls this loop and
        # the overlapping asyncio.sleep(0.2) takes ~1.2s. Use the MEDIAN gap:
        # a single scheduler hiccup under full-suite load shouldn't fail the
        # test, but a genuinely blocked loop makes most gaps ~1.2s.
        gaps = []
        # Generous anti-hang deadline: the worker thread can be starved under
        # full-suite load, delaying the job's 'done' status. The median-gap
        # assertion below is the real loop-blocking guard.
        deadline = time_mod.monotonic() + 20.0
        status = "queued"
        while status != "done":
            t0 = time_mod.monotonic()
            await asyncio.sleep(0.2)
            gaps.append(time_mod.monotonic() - t0)
            conn = init_shared_db(test_config.db_dir)
            status = get_job(conn, job_id)["status"]
            conn.close()
            assert time_mod.monotonic() < deadline, f"job stuck in {status}"
        gaps.sort()
        median_gap = gaps[len(gaps) // 2]
        assert median_gap < 0.5, f"event loop blocked (median gap {median_gap:.2f}s)"

    t = getattr(app.state, "worker_thread", None)
    assert t is not None, "worker_thread must be created by the startup hook"
    t.join(timeout=5)
    assert not t.is_alive(), "worker thread must exit cleanly after shutdown"


@pytest.mark.asyncio
async def test_shutdown_leaves_no_zombie_thread(tmp_dirs, test_config):
    """Shutdown hook must stop+join the worker thread — no zombie threads.

    FastAPI 0.141 has no public router.shutdown(); the on_shutdown hooks
    run on lifespan context exit, so enter+exit triggers the hook.
    """
    from app.main import create_app

    app = create_app(test_config, "testsecret")
    async with app.router.lifespan_context(app):
        pass
    t = getattr(app.state, "worker_thread", None)
    assert t is not None, "startup hook must create the worker thread"
    assert not t.is_alive(), "shutdown hook must join the worker thread (no zombie)"
