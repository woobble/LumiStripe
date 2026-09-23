"""Background operation execution for Bluetooth control actions."""

from __future__ import annotations

import threading
from collections.abc import Callable


class OperationCoordinator:
    """Run one platform operation asynchronously and report its outcome.

    The manager owns operation state and admission control; this small class
    owns only the thread lifecycle. Keeping those responsibilities separate
    prevents polling and HTTP requests from accidentally sharing a worker
    thread or mutating status from the wrong callback.
    """

    def submit(
        self,
        name: str,
        operation: Callable[[], None],
        complete: Callable[[Exception | None], None],
    ) -> None:
        def run() -> None:
            error: Exception | None = None
            try:
                operation()
            except Exception as exc:  # noqa: BLE001 - owner translates/logs platform errors
                error = exc
            finally:
                complete(error)

        threading.Thread(
            target=run,
            name=f"lumistripe-{name.split(':', 1)[0]}",
            daemon=True,
        ).start()


__all__ = ["OperationCoordinator"]
