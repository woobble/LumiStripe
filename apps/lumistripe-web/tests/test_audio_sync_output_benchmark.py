from __future__ import annotations

import sys
from types import SimpleNamespace

from scripts import benchmark_audio_sync_output


def test_benchmark_verifies_route_before_arming_and_measuring(
    monkeypatch,
) -> None:
    events: list[tuple[object, ...]] = []
    input_ids = frozenset({"42"})

    class FakeBluetoothManager:
        def __init__(self, *, enabled: bool) -> None:
            events.append(("manager_init", enabled))

        def calibration_output_input_ids(self) -> frozenset[str]:
            events.append(("input_ids",))
            return input_ids

        def verify_calibration_output_route(
            self, before_input_ids: frozenset[str], expected_sink: str
        ) -> None:
            assert before_input_ids == input_ids
            assert expected_sink == "alsa_output.usb-speakers"
            events.append(("route_verified",))

        def stop(self) -> None:
            events.append(("manager_stop",))

    class FakeCalibrationTone:
        def __init__(self) -> None:
            events.append(("player_init",))

        def arm(self) -> None:
            events.append(("arm",))

        def diagnostics(self) -> SimpleNamespace:
            events.append(("diagnostics",))
            return SimpleNamespace(
                callback_count=1,
                sample_rate=48_000.0,
                block_duration_ms=512 / 48_000 * 1000,
                callback_p50_ms=0.1,
                callback_p95_ms=0.2,
                callback_p99_ms=0.3,
                interval_jitter_p95_ms=0.1,
                underflow_count=0,
                overflow_count=0,
                callback_error_count=0,
            )

        def close(self) -> None:
            events.append(("player_close",))

    monkeypatch.setattr(
        benchmark_audio_sync_output, "BluetoothManager", FakeBluetoothManager
    )
    monkeypatch.setattr(
        benchmark_audio_sync_output, "AudioSyncCalibrationTone", FakeCalibrationTone
    )
    monkeypatch.setattr(
        benchmark_audio_sync_output.time,
        "sleep",
        lambda duration: events.append(("sleep", duration)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark_audio_sync_output.py",
            "--sink",
            "alsa_output.usb-speakers",
            "--duration",
            "2",
        ],
    )

    benchmark_audio_sync_output.main()

    assert events == [
        ("manager_init", True),
        ("input_ids",),
        ("player_init",),
        ("route_verified",),
        ("arm",),
        ("sleep", 2.0),
        ("diagnostics",),
        ("player_close",),
        ("manager_stop",),
    ]
