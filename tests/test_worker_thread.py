"""Worker thread-safety tests — stop() must work cross-thread."""

import asyncio
import threading
import time
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
