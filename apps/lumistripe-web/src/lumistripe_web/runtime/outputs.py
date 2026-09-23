"""Output topology primitives used by the runtime worker."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
import numpy.typing as npt
from lumistripe import Color, Controller, PixelBuffer


class OutputGateController(Controller):
    """Suppress output flushes while preserving the latest rendered frame."""

    def __init__(self, inner: Controller) -> None:
        self._inner = inner
        self._blackout = False
        self._last_successful_update_at: datetime | None = None
        self._last_successful_update_monotonic: float | None = None

    @property
    def blackout(self) -> bool:
        return self._blackout

    @property
    def last_successful_update_at(self) -> datetime | None:
        return self._last_successful_update_at

    @property
    def last_successful_update_age_seconds(self) -> float | None:
        if self._last_successful_update_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self._last_successful_update_monotonic)

    def _record_successful_update(self) -> None:
        self._last_successful_update_at = datetime.now(UTC)
        self._last_successful_update_monotonic = time.monotonic()

    def set_blackout(self, enabled: bool) -> None:
        if enabled == self._blackout:
            return
        if enabled:
            buffered_frame = self._inner.pixels().copy()
            self._inner.clear()
            self._inner.force_flush()
            self._record_successful_update()
            self._inner.set_pixels(buffered_frame)
            self._blackout = True
            return
        self._blackout = False
        self.force_flush()

    @property
    def length(self) -> int:
        return self._inner.length

    def pixels(self) -> PixelBuffer:
        return self._inner.pixels()

    def pixel(self, index: int) -> Color:
        return self._inner.pixel(index)

    def set_pixel(self, index: int, color: Color) -> None:
        self._inner.set_pixel(index, color)

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        self._inner.set_pixels(colors)

    def fill(self, color: Color) -> None:
        self._inner.fill(color)

    def clear(self) -> None:
        self._inner.clear()

    def flush(self) -> None:
        if not self._blackout:
            self._inner.flush()
            self._record_successful_update()

    def force_flush(self) -> None:
        if not self._blackout:
            self._inner.force_flush()
            self._record_successful_update()

    def close(self) -> None:
        self._inner.close()


def copy_output_pixels(controller: Controller) -> np.ndarray:
    """Return an owned frame for preview and power-budget calculations."""

    return controller.pixels().copy()


__all__ = ["OutputGateController", "copy_output_pixels"]
