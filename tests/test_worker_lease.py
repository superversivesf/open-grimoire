"""Worker job-claim lease tests — crashed jobs must be reclaimed."""

from pathlib import Path
from typing import Any

import pytest
from app.storage.shared_db import init_shared_db, enqueue_job, claim_next_job, complete_job, get_job


def _enqueue(conn, user="alice", doc="d1", path="/x.pdf"):
    return enqueue_job(conn, user, doc, path)


def test_claim_sets_processing_and_lease(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    job = claim_next_job(conn)
    assert job is not None
    assert job["status"] == "processing"
    assert job["lease_expires_at"] is not None
    assert job["attempts"] == 1
    conn.close()


def test_claim_increments_attempts(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    claim_next_job(conn)
    # Simulate crash: lease expires, job is reclaimed
    conn.execute("UPDATE queue_jobs SET lease_expires_at = '2000-01-01' WHERE job_id = ?", (jid,))
    conn.commit()
    job = claim_next_job(conn)
    assert job is not None
    assert job["attempts"] == 2
    conn.close()


def test_claim_reclaims_expired_lease(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    claim_next_job(conn)
    # Simulate crash: lease expires
    conn.execute("UPDATE queue_jobs SET lease_expires_at = '2000-01-01' WHERE job_id = ?", (jid,))
    conn.commit()
    job = claim_next_job(conn)
    assert job is not None
    assert job["job_id"] == jid
    assert job["status"] == "processing"
    conn.close()


def test_claim_skips_active_lease(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    claim_next_job(conn)
    # Lease still active — must NOT be reclaimed
    job = claim_next_job(conn)
    assert job is None
    conn.close()


def test_claim_gives_up_after_max_attempts(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    for _ in range(3):
        claim_next_job(conn)
        conn.execute("UPDATE queue_jobs SET lease_expires_at = '2000-01-01' WHERE job_id = ?", (jid,))
        conn.commit()
    # 4th claim: attempts already at max — must not be reclaimed
    job = claim_next_job(conn)
    assert job is None
    conn.close()


def test_heartbeat_refreshes_lease(tmp_dirs):
    import time
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    job = claim_next_job(conn)
    old_lease = job["lease_expires_at"]
    time.sleep(1.1)
    from app.storage.shared_db import heartbeat_job
    heartbeat_job(conn, jid)
    refreshed = get_job(conn, jid)
    assert refreshed["lease_expires_at"] > old_lease
    conn.close()


def test_complete_job_clears_lease(tmp_dirs):
    conn = init_shared_db(tmp_dirs["db"])
    jid = _enqueue(conn)
    claim_next_job(conn)
    complete_job(conn, jid)
    job = get_job(conn, jid)
    assert job["status"] == "done"
    assert job["lease_expires_at"] is None
    conn.close()


def test_heartbeat_fires_during_long_sync_stage(tmp_dirs: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A job with a long sync stage must keep its lease fresh (heartbeat fires).

    Regression for the codex finding: with the sync extract stage blocking the
    worker's loop, the heartbeat task never fires and a job longer than
    JOB_LEASE_SECONDS gets reclaimed as crashed.
    """
    import asyncio
    import threading
    import time
    from unittest.mock import AsyncMock, MagicMock

    from app.pipeline.extract import Extractor
    from app.pipeline.runner import PipelineRunner
    from app.queue.worker import QueueWorker
    from app.storage.shared_db import init_shared_db, enqueue_job, get_job

    # Shrink the lease so the test runs in seconds instead of 5 minutes.
    monkeypatch.setattr("app.constants.JOB_LEASE_SECONDS", 2)
    monkeypatch.setattr("app.queue.worker.JOB_LEASE_SECONDS", 2)

    conn = init_shared_db(tmp_dirs["db"])
    jid = enqueue_job(conn, "alice", "d1", "/x.pdf")
    conn.close()

    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "s", "keywords": []}'}})
    runner = PipelineRunner(gw, tmp_dirs["data"], tmp_dirs["db"])

    # Patch the real extract path to block for 3.5s — longer than the 2s lease.
    def slow_extract(self: Extractor, pdf_path: Path) -> list[dict[str, Any]]:
        time.sleep(3.5)
        return [{"page": 1, "text": "Chapter 1: Combat", "ocr": False}]
    monkeypatch.setattr(Extractor, "extract", slow_extract)

    worker = QueueWorker(runner, tmp_dirs["db"], poll_interval=0.05)
    t = threading.Thread(target=lambda: asyncio.run(worker.run_forever()), daemon=True)
    t.start()
    try:
        # Wait until the job is claimed and the slow extract stage is running.
        deadline = time.monotonic() + 5.0
        while True:
            conn = init_shared_db(tmp_dirs["db"])
            j = get_job(conn, jid)
            conn.close()
            if j and j["status"] == "processing":
                break
            assert time.monotonic() < deadline, "job never claimed"
            time.sleep(0.05)
        # Mid-stage, past the original 2s lease: the lease must have been
        # refreshed by the heartbeat — only possible if the loop stayed free.
        time.sleep(2.5)
        conn = init_shared_db(tmp_dirs["db"])
        fresh = conn.execute(
            "SELECT lease_expires_at > datetime('now') FROM queue_jobs WHERE job_id = ?",
            (jid,),
        ).fetchone()[0]
        conn.close()
        assert fresh, "lease expired during long sync stage — heartbeat never fired"
    finally:
        worker.stop()
        t.join(timeout=10)
