"""Background job runner. De-duplicates work on the cache key so a webhook and an
extension request for the same issue share one run."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="triage-job")
        self._inflight: dict[str, str] = {}
        self._lock = threading.Lock()

    def inflight_run(self, key: str) -> str | None:
        with self._lock:
            return self._inflight.get(key)

    def submit(self, key: str, run_id: str, fn: Callable[[], None]) -> tuple[str, bool]:
        """Returns (run_id, started). If the key is already running, returns the existing run."""
        with self._lock:
            existing = self._inflight.get(key)
            if existing:
                return existing, False
            self._inflight[key] = run_id

        def wrapped() -> None:
            try:
                fn()
            except Exception:  # the orchestrator should never raise; log if it does
                log.exception("job %s crashed", run_id)
            finally:
                with self._lock:
                    self._inflight.pop(key, None)

        self._pool.submit(wrapped)
        return run_id, True

    def run_sync(self, fn: Callable[[], None]) -> Future:
        return self._pool.submit(fn)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
