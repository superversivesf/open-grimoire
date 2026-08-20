# app/queue/worker.py
import asyncio
from pathlib import Path
from typing import Any
from app.storage.shared_db import init_shared_db, claim_next_job, heartbeat_job
from app.logging_utils import get_logger, set_job_id
from app.constants import WORKER_POLL_INTERVAL, JOB_LEASE_SECONDS

log = get_logger("worker")

# Upper bound for a single job run. Enrichment of a large book at low
# concurrency can legitimately take 1-2 hours; a hung stage must not wedge
# the worker forever, but the bound must be generous enough that slow
# enrichment isn't abandoned mid-way.
JOB_RUN_TIMEOUT = 8 * 3600


class QueueWorker:
    def __init__(self, runner: Any, db_dir: Path, poll_interval: float = WORKER_POLL_INTERVAL) -> None:
        self.runner = runner
        self.db_dir = db_dir
        self.poll_interval = poll_interval
        self._stop_event = asyncio.Event()
        self._current_job_id: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def _heartbeat_loop(self, job_id: str) -> None:
        """Refresh the job lease while it is being processed."""
        interval = max(1.0, JOB_LEASE_SECONDS / 3)
        conn = init_shared_db(self.db_dir)
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(interval)
                try:
                    heartbeat_job(conn, job_id)
                except Exception:
                    log.warning(f"heartbeat failed for job {job_id[:8]}, reconnecting")
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = init_shared_db(self.db_dir)
        except asyncio.CancelledError:
            pass
        finally:
            conn.close()

    async def run_once(self) -> bool:
        conn = init_shared_db(self.db_dir)
        try:
            try:
                job = claim_next_job(conn)
            except Exception:
                # Transient DB failure (lock, WAL hiccup) must not kill the
                # worker thread — log and let the next poll retry.
                log.exception("claim_next_job failed; retrying on next poll")
                return False
            if not job:
                return False
            self._current_job_id = job["job_id"]
            set_job_id(self._current_job_id)
            conn.close()
            conn = None
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(job["job_id"]))
            try:
                # Bound the whole job so a hung enrich stage (e.g. a wedged
                # gateway client) can't wedge the worker forever.
                await asyncio.wait_for(self.runner.run_job(job), timeout=JOB_RUN_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning(f"job {job['job_id'][:8]} exceeded {JOB_RUN_TIMEOUT}s, marking failed")
                self._fail_job(job, "timeout")
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
            return True
        finally:
            if conn:
                conn.close()
            self._current_job_id = None
            set_job_id(None)

    def _fail_job(self, job: dict[str, Any], error: str) -> None:
        """Mark a job failed (terminal status) so it is never reclaimed."""
        from app.storage.shared_db import complete_job
        from app.storage.user_db import init_user_db, update_doc_status
        conn = init_shared_db(self.db_dir)
        try:
            complete_job(conn, job["job_id"], error=error)
        except Exception:
            log.exception(f"failed to mark job {job['job_id'][:8]} failed")
        finally:
            conn.close()
        try:
            uconn = init_user_db(self.db_dir, job["user_id"])
            try:
                update_doc_status(uconn, job["doc_id"], "failed")
            finally:
                uconn.close()
        except Exception:
            log.exception(f"failed to mark doc {job['doc_id'][:8]} failed")

    async def run_forever(self) -> None:
        """Run the worker until stopped."""
        self._loop = asyncio.get_running_loop()

        log.info("worker_started", poll_interval=self.poll_interval)
        try:
            while not self._stop_event.is_set():
                ran = await self.run_once()
                if not ran:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
                    except asyncio.TimeoutError:
                        pass
        finally:
            # If we were processing a job, wait for it to complete
            if self._current_job_id:
                log.info("shutdown_waiting_for_job", job_id=self._current_job_id[:8])
            log.info("worker_stopped")

    def stop(self) -> None:
        """Signal the worker to stop (cross-thread safe)."""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        else:
            self._stop_event.set()