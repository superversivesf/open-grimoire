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