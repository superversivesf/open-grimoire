# app/queue/worker.py
import asyncio
import signal
from pathlib import Path
from typing import Any
from app.storage.shared_db import init_shared_db, claim_next_job, heartbeat_job
from app.logging_utils import get_logger, set_job_id
from app.constants import WORKER_POLL_INTERVAL, JOB_LEASE_SECONDS

log = get_logger("worker")


class QueueWorker:
    def __init__(self, runner: Any, db_dir: Path, poll_interval: float = WORKER_POLL_INTERVAL) -> None:
        self.runner = runner
        self.db_dir = db_dir
        self.poll_interval = poll_interval
        self._stop_event = asyncio.Event()
        self._current_job_id: str | None = None

    async def _heartbeat_loop(self, job_id: str) -> None:
        """Refresh the job lease while it is being processed."""
        interval = max(1.0, JOB_LEASE_SECONDS / 3)
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(interval)
                conn = init_shared_db(self.db_dir)
                try:
                    heartbeat_job(conn, job_id)
                finally:
                    conn.close()
        except asyncio.CancelledError:
            pass

    async def run_once(self) -> bool:
        conn = init_shared_db(self.db_dir)
        try:
            job = claim_next_job(conn)
            if not job:
                return False
            self._current_job_id = job["job_id"]
            set_job_id(self._current_job_id)
            conn.close()
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(job["job_id"]))
            try:
                await self.runner.run_job(job)
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

    async def run_forever(self) -> None:
        """Run the worker until stopped by signal."""
        loop = asyncio.get_event_loop()

        def _signal_handler(sig: signal.Signals) -> None:
            log.info("shutdown_signal_received", signal=sig.name)
            self._stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _signal_handler, sig)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

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
        """Signal the worker to stop (for testing)."""
        self._stop_event.set()