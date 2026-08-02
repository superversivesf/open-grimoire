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