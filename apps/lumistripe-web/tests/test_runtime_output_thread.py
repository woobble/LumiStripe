from __future__ import annotations

import threading
import time
from collections.abc import Callable
from threading import Event, get_ident

import numpy as np
from lumistripe import Stripe
from lumistripe_web.runtime.output_thread import LedOutputThread


class RecordingStripe(Stripe):
    def __init__(
        self,
        length: int,
        name: str,
        events: list[tuple[str, str, int, tuple[int, ...] | None]],
        *,
        started: Event | None = None,
        release: Event | None = None,
    ) -> None:
        self.name = name
        self.events = events
        self.started = started
        self.release = release
        self.block_once = release is not None
        self._event_lock = threading.Lock()
        super().__init__(length)
        self._record("create")

    def flush(self) -> None:
        if self.block_once:
            self.block_once = False
            assert self.started is not None and self.release is not None
            self.started.set()
            assert self.release.wait(2.0)
        self._record("flush")
        super().flush()

    def clear(self) -> None:
        self._record("clear")
        super().clear()

    def force_flush(self) -> None:
        self._record("force_flush")
        super().force_flush()

    def close(self) -> None:
        self._record("close")

    def _record(self, action: str) -> None:
        values = tuple(int(value) for value in self.pixels()[0]) if self.length else None
        with self._event_lock:
            self.events.append((self.name, action, get_ident(), values))


def _frame(length: int, value: int) -> np.ndarray:
    result = np.full((length, 4), value, dtype=np.uint8)
    return result


def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.005)


def test_physical_controller_lifecycle_runs_on_output_thread() -> None:
    events: list[tuple[str, str, int, tuple[int, ...] | None]] = []
    caller = get_ident()

    def build() -> tuple[tuple[RecordingStripe, bool], ...]:
        return ((RecordingStripe(2, "one", events), False),)

    output = LedOutputThread((2,), build)
    output.start()
    exchange = output.exchange
    assert exchange is not None
    assert exchange.submit((_frame(2, 17),))
    _wait_for(lambda: output.stats().transmitted_frames >= 1)
    stats = output.stats()
    assert stats.frame_latency_ms is not None
    assert stats.frame_latency_p95_ms is not None
    assert stats.frame_latency_sample_count == 1
    output.stop()

    owner_ids = {event[2] for event in events}
    assert len(owner_ids) == 1
    assert caller not in owner_ids
    assert {event[1] for event in events} >= {"create", "flush", "clear", "close"}


def test_blocked_output_does_not_block_latest_frame_submission() -> None:
    events: list[tuple[str, str, int, tuple[int, ...] | None]] = []
    flush_started = Event()
    release_flush = Event()
    output = LedOutputThread(
        (1,),
        lambda: (
            (
                RecordingStripe(
                    1,
                    "slow",
                    events,
                    started=flush_started,
                    release=release_flush,
                ),
                False,
            ),
        ),
    )
    output.start()
    exchange = output.exchange
    assert exchange is not None
    assert exchange.submit((_frame(1, 0),))
    assert flush_started.wait(1.0)

    start = time.monotonic()
    for value in range(1, 8):
        assert exchange.submit((_frame(1, value),))
    assert time.monotonic() - start < 0.1
    assert exchange.stats().dropped_count == 6

    time.sleep(0.03)
    release_flush.set()
    _wait_for(lambda: output.stats().transmitted_frames >= 2)
    stats = output.stats()
    output.stop()
    transmitted_values = [
        values[0]
        for _, action, _, values in events
        if action == "flush" and values is not None
    ]
    assert transmitted_values[:2] == [0, 7]
    assert stats.frame_latency_p95_ms is not None
    assert stats.frame_latency_p95_ms >= 20.0


def test_multistrip_frames_remain_a_synchronized_set() -> None:
    events: list[tuple[str, str, int, tuple[int, ...] | None]] = []
    output = LedOutputThread(
        (1, 2),
        lambda: (
            (RecordingStripe(1, "left", events), False),
            (RecordingStripe(2, "right", events), True),
        ),
    )
    output.start()
    exchange = output.exchange
    assert exchange is not None
    for value in range(1, 20):
        assert exchange.submit((_frame(1, value), _frame(2, value)))
    _wait_for(lambda: output.stats().transmitted_frames > 0)
    output.stop()

    writes = [
        (name, values[0])
        for name, action, _, values in events
        if action == "flush" and values is not None
    ]
    assert writes
    assert len(writes) % 2 == 0
    assert all(
        writes[index][0] == "left"
        and writes[index + 1][0] == "right"
        and writes[index][1] == writes[index + 1][1]
        for index in range(0, len(writes), 2)
    )


def test_reconfigure_clears_and_closes_old_outputs_on_the_owner_thread() -> None:
    events: list[tuple[str, str, int, tuple[int, ...] | None]] = []
    output = LedOutputThread(
        (1,),
        lambda: ((RecordingStripe(1, "old", events), False),),
    )
    output.start()
    caller = get_ident()
    exchange = output.reconfigure(
        (2,), lambda: ((RecordingStripe(2, "new", events), True),)
    )
    assert exchange is not None
    assert exchange.submit((_frame(2, 31),))
    _wait_for(lambda: output.stats().transmitted_frames >= 1)
    output.stop()

    old_events = [event for event in events if event[0] == "old"]
    new_events = [event for event in events if event[0] == "new"]
    assert {event[1] for event in old_events} >= {
        "clear",
        "force_flush",
        "close",
    }
    assert len({event[2] for event in events}) == 1
    assert events[0][2] != caller
    new_flush = next(event for event in new_events if event[1] == "flush")
    assert new_flush[3] == (31, 31, 31, 31)


def test_output_thread_retries_after_driver_oserror_and_reports_recovery() -> None:
    events: list[tuple[str, str, int, tuple[int, ...] | None]] = []

    class RecoveringStripe(RecordingStripe):
        def __init__(self) -> None:
            super().__init__(1, "recovering", events)
            self.attempts = 0

        def flush(self) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("temporary SPI fault")
            super().flush()

    devices: list[RecoveringStripe] = []

    def build() -> tuple[tuple[RecoveringStripe, bool], ...]:
        device = RecoveringStripe()
        devices.append(device)
        return ((device, False),)

    output = LedOutputThread((1,), build)
    output.start()
    exchange = output.exchange
    assert exchange is not None
    assert exchange.submit((_frame(1, 9),))
    _wait_for(lambda: output.stats().failed_flushes == 1)

    time.sleep(0.11)
    assert exchange.submit((_frame(1, 23),))
    _wait_for(lambda: output.stats().last_successful_update_at is not None)
    stats = output.stats()
    output.stop()

    assert devices[0].attempts == 2
    assert stats.last_error is None
    assert stats.failed_flushes == 1

