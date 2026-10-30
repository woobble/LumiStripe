"""Reusable polling lifecycle for platform status providers."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class StatusPoller:
    """Run an immediate refresh followed by bounded-interval polling."""

    def __init__(self, refresh: Callable[[], None], interval: float) -> None:
        self._refresh = refresh
        self._interval = max(0.25, interval)
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="lumistripe-bluetooth",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=min(2.0, self._interval + 0.5))
        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._refresh()
            except Exception:
                logger.exception("Bluetooth status refresh failed")
            if self._stop_event.wait(self._interval):
                break


__all__ = ["StatusPoller"]
