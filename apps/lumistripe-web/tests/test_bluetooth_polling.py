from __future__ import annotations

import threading

import pytest
from lumistripe_web.bluetooth_polling import StatusPoller


def test_poller_recovers_after_refresh_failure() -> None:
    recovered = threading.Event()
    calls = 0

    def refresh() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        recovered.set()

    poller = StatusPoller(refresh, 0.25)
    poller.start()
    try:
        assert recovered.wait(1)
    finally:
        poller.stop()
    assert calls >= 2


def test_timed_out_stop_does_not_start_a_second_poller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def refresh() -> None:
        entered.set()
        release.wait()

    poller = StatusPoller(refresh, 0.25)
    poller.start()
    assert entered.wait(1)
    thread = poller._thread
    assert thread is not None
    join = thread.join
    monkeypatch.setattr(thread, "join", lambda timeout=None: None)
    try:
        poller.stop()
        poller.start()
        assert poller._thread is thread
        assert poller._stop_event.is_set()
    finally:
        release.set()
        join(1)
        poller.stop()
