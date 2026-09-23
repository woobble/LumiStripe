"""Reusable polling lifecycle for platform status providers."""

from __future__ import annotations

import threading
from collections.abc import Callable


class StatusPoller:
    """Run an immediate refresh followed by bounded-interval polling."""

    def __init__(self, refresh: Callable[[], None], interval: float) -> None:
        self._refresh = refresh
        self._interval = max(0.25, interval)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
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
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=min(2.0, self._interval + 0.5))
        self._thread = None

    def _run(self) -> None:
        self._refresh()
        while not self._stop_event.wait(self._interval):
            self._refresh()


__all__ = ["StatusPoller"]
