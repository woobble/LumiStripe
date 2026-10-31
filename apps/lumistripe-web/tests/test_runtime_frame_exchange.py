from __future__ import annotations

import threading
import time

import numpy as np
import pytest
from lumistripe_web.runtime.frame_exchange import LatestFrameExchange


def _frames(value: int) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.full((8, 4), value, dtype=np.uint8),
        np.full((5, 4), value, dtype=np.uint8),
    )


def test_latest_frame_replaces_pending_set_without_mutating_inflight_set() -> None:
    exchange = LatestFrameExchange((8, 5))
    first_source = _frames(10)
    assert exchange.submit(first_source)

    first = exchange.take(timeout=0.1)
    assert first is not None
    first_slot_id = id(first)
    assert first.sequence == 1
    assert all(np.all(frame == 10) for frame in first.frames)

    # Source ownership returns to the renderer as soon as submit completes.
    for source in first_source:
        source[:] = 99
    assert all(np.all(frame == 10) for frame in first.frames)

    assert exchange.submit(_frames(20))
    assert exchange.submit(_frames(30))
    assert all(np.all(frame == 10) for frame in first.frames)
    exchange.release(first)

    latest = exchange.take(timeout=0.1)
    assert latest is not None
    assert latest.sequence == 3
    assert id(latest) != first_slot_id
    assert all(np.all(frame == 30) for frame in latest.frames)
    assert exchange.stats().dropped_count == 1
    exchange.release(latest)

    assert exchange.submit(_frames(40))
    reused = exchange.take(timeout=0.1)
    assert reused is not None
    assert tuple(map(id, reused.frames)) == tuple(map(id, first.frames))
    exchange.release(reused)


def test_exchange_keeps_multistrip_frames_atomic_under_concurrent_load() -> None:
    exchange = LatestFrameExchange((8, 5))
    producer_done = threading.Event()
    observed_sequences: list[int] = []
    errors: list[AssertionError | RuntimeError | ValueError] = []
    source = _frames(0)

    def produce() -> None:
        try:
            for value in range(1, 501):
                for frame in source:
                    frame[:] = value % 256
                assert exchange.submit(source)
        except (AssertionError, RuntimeError, ValueError) as exc:
            errors.append(exc)
        finally:
            producer_done.set()

    def consume() -> None:
        try:
            while not producer_done.is_set() or exchange.stats().pending:
                slot = exchange.take(timeout=0.01)
                if slot is None:
                    continue
                expected = slot.sequence % 256
                assert all(np.all(frame == expected) for frame in slot.frames)
                observed_sequences.append(slot.sequence)
                time.sleep(0.0002)
                exchange.release(slot)
        except (AssertionError, RuntimeError, ValueError) as exc:
            errors.append(exc)

    consumer = threading.Thread(target=consume)
    producer = threading.Thread(target=produce)
    consumer.start()
    producer.start()
    producer.join(timeout=2.0)
    assert not producer.is_alive()
    consumer.join(timeout=2.0)
    assert not consumer.is_alive()
    assert errors == []
    assert observed_sequences
    assert observed_sequences == sorted(observed_sequences)
    assert observed_sequences[-1] == 500
    assert exchange.stats().dropped_count > 0


def test_exchange_rejects_mismatched_frames_and_drains_pending_on_close() -> None:
    exchange = LatestFrameExchange((8, 5))
    with pytest.raises(ValueError, match="frame count"):
        exchange.submit((_frames(1)[0],))
    with pytest.raises(ValueError, match="frame 1"):
        exchange.submit(
            (
                np.zeros((8, 4), dtype=np.uint8),
                np.zeros((4, 4), dtype=np.uint8),
            )
        )

    assert exchange.submit(_frames(7))
    exchange.close()
    assert not exchange.submit(_frames(8))
    slot = exchange.take(timeout=0.1)
    assert slot is not None
    assert slot.sequence == 1
    assert all(np.all(frame == 7) for frame in slot.frames)
    exchange.release(slot)
    assert exchange.take(timeout=0.01) is None


def test_exchange_can_discard_pending_frame_when_closing() -> None:
    exchange = LatestFrameExchange((8, 5))
    assert exchange.submit(_frames(7))
    exchange.close(discard_pending=True)

    assert exchange.take(timeout=0.01) is None
    stats = exchange.stats()
    assert stats.closed
    assert not stats.pending
    assert stats.dropped_count == 1
