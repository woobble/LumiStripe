"""Bounded latest-frame handoff between rendering and LED output."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

PixelFrame: TypeAlias = npt.NDArray[np.uint8]


@dataclass(slots=True)
class _FrameSlot:
    """One preallocated frame set with exclusive producer/consumer ownership."""

    frames: tuple[PixelFrame, ...]
    sequence: int = 0
    submitted_at_ns: int = 0


@dataclass(frozen=True, slots=True)
class FrameExchangeStats:
    submitted_count: int
    consumed_count: int
    dropped_count: int
    latest_sequence: int
    pending: bool
    closed: bool


class LatestFrameExchange:
    """Copy complete RGBA output sets through a two-slot latest-wins buffer.

    The producer owns its source buffers and may reuse them as soon as
    :meth:`submit` returns. The consumer owns a slot from :meth:`take` until
    :meth:`release`; the producer never writes to that slot during that time.
    When output is behind, a new submission replaces the pending slot while an
    in-flight slot remains unchanged. A frame set is published atomically, so
    every strip in a consumed set shares one sequence number.

    Pixel storage is allocated only when the topology is constructed. The
    per-frame path uses ``numpy.copyto`` and reuses the same slot objects.
    """

    def __init__(self, output_lengths: tuple[int, ...] | list[int]) -> None:
        if not output_lengths or any(length <= 0 for length in output_lengths):
            raise ValueError("at least one output with a positive length is required")
        self._slots = tuple(
            _FrameSlot(
                tuple(
                    np.empty((length, 4), dtype=np.uint8)
                    for length in output_lengths
                )
            )
            for _ in range(2)
        )
        self._condition = threading.Condition()
        self._ready_slot: _FrameSlot | None = None
        self._inflight_slot: _FrameSlot | None = None
        self._next_sequence = 0
        self._submitted_count = 0
        self._consumed_count = 0
        self._dropped_count = 0
        self._closed = False

    @property
    def output_count(self) -> int:
        return len(self._slots[0].frames)

    @property
    def output_lengths(self) -> tuple[int, ...]:
        return tuple(frame.shape[0] for frame in self._slots[0].frames)

    def submit(self, frames: tuple[PixelFrame, ...] | list[PixelFrame]) -> bool:
        """Copy and publish one complete output set, replacing stale pending data."""
        if len(frames) != self.output_count:
            raise ValueError("frame count must match the configured output count")
        for index, (frame, slot_frame) in enumerate(
            zip(frames, self._slots[0].frames, strict=True)
        ):
            if frame.dtype != np.uint8 or frame.shape != slot_frame.shape:
                raise ValueError(
                    f"frame {index} must be uint8 with shape {slot_frame.shape}"
                )

        with self._condition:
            if self._closed:
                return False
            slot = self._ready_slot
            if slot is not None:
                self._dropped_count += 1
            elif self._inflight_slot is self._slots[0]:
                slot = self._slots[1]
            else:
                slot = self._slots[0]
            if slot is self._inflight_slot:
                # With two slots and one consumer this cannot occur. Keep the
                # invariant explicit if a future caller adds another consumer.
                raise RuntimeError("no writable frame slot is available")

            for source, destination in zip(frames, slot.frames, strict=True):
                np.copyto(destination, source, casting="no")
            self._next_sequence += 1
            slot.sequence = self._next_sequence
            slot.submitted_at_ns = time.monotonic_ns()
            self._ready_slot = slot
            self._submitted_count += 1
            self._condition.notify()
            return True

    def take(self, timeout: float | None = None) -> _FrameSlot | None:
        """Wait for and claim the newest pending output set."""
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._ready_slot is None and not self._closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0.0:
                    return None
                self._condition.wait(remaining)
            slot = self._ready_slot
            if slot is None:
                return None
            if self._inflight_slot is not None:
                raise RuntimeError("only one output consumer may own a frame slot")
            self._ready_slot = None
            self._inflight_slot = slot
            self._consumed_count += 1
            return slot

    def release(self, slot: _FrameSlot) -> None:
        """Return a consumed slot to the producer after transmission completes."""
        with self._condition:
            if slot is not self._inflight_slot:
                raise RuntimeError("frame slot is not owned by the output consumer")
            self._inflight_slot = None
            self._condition.notify_all()

    def close(self, *, discard_pending: bool = False) -> None:
        """Reject future frames and wake the output consumer.

        By default, an already-pending frame remains available for graceful
        drain. Safety shutdown paths can discard it and issue an explicit black
        frame through the output thread before closing the exchange.
        """
        with self._condition:
            self._closed = True
            if discard_pending and self._ready_slot is not None:
                self._ready_slot = None
                self._dropped_count += 1
            self._condition.notify_all()

    def stats(self) -> FrameExchangeStats:
        """Return a small immutable snapshot for low-rate diagnostics."""
        with self._condition:
            return FrameExchangeStats(
                submitted_count=self._submitted_count,
                consumed_count=self._consumed_count,
                dropped_count=self._dropped_count,
                latest_sequence=self._next_sequence,
                pending=self._ready_slot is not None,
                closed=self._closed,
            )


__all__ = ["FrameExchangeStats", "LatestFrameExchange"]
