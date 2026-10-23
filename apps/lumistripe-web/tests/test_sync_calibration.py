from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from lumistripe_web.runtime.sync_calibration import (
    SYNC_PATTERN_PERIOD_S,
    AudioSyncCalibrationTone,
    sync_calibration_led_on,
)


class FakeOutputStream:
    def __init__(self, **kwargs: object) -> None:
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False
        self.device = kwargs["device"]

    def start(self) -> None:
        self.started = True
        self.callback(
            np.empty((512, 1), dtype=np.float32),
            512,
            SimpleNamespace(currentTime=1000.0, outputBufferDacTime=1000.02),
            None,
        )

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class FakeSoundDevice:
    def __init__(
        self,
        devices: list[dict[str, object]],
        default_output_device: int = 4,
    ) -> None:
        self.devices = devices
        self.default_output_device = default_output_device
        self.streams: list[FakeOutputStream] = []

    def query_devices(self) -> list[dict[str, object]]:
        return self.devices

    def query_hostapis(self) -> list[dict[str, object]]:
        return [
            {"name": "PulseAudio", "default_output_device": self.default_output_device},
            {"name": "ALSA", "default_output_device": 1},
        ]

    def OutputStream(self, **kwargs: object) -> FakeOutputStream:
        stream = FakeOutputStream(**kwargs)
        self.streams.append(stream)
        return stream


def test_calibration_tone_uses_pipewire_pulse_default_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sounddevice = FakeSoundDevice(
        [
            {
                "name": "default",
                "index": 1,
                "hostapi": 1,
                "max_output_channels": 2,
                "default_samplerate": 48_000.0,
            },
            {
                "name": "pulse",
                "index": 4,
                "hostapi": 0,
                "max_output_channels": 2,
                "default_samplerate": 48_000.0,
            },
        ]
    )

    player = AudioSyncCalibrationTone(sounddevice)
    stream = sounddevice.streams[0]
    assert stream.device == 4
    assert stream.started
    with pytest.raises(RuntimeError, match="not been armed"):
        _ = player.start_at_monotonic
    player.arm()
    assert player.start_at_monotonic > 0.0

    output = np.empty((512, 1), dtype=np.float32)
    with monkeypatch.context() as patcher:
        patcher.setattr(
            np,
            "arange",
            lambda *args, **kwargs: pytest.fail("callback constructed a sample array"),
        )
        stream.callback(
            output,
            512,
            SimpleNamespace(currentTime=1001.0, outputBufferDacTime=1001.54),
            SimpleNamespace(output_underflow=True, output_overflow=False),
        )
    assert np.max(np.abs(output)) > 0.0
    diagnostics = player.diagnostics()
    assert diagnostics.callback_count == 2
    assert diagnostics.underflow_count == 1
    assert diagnostics.overflow_count == 0
    assert diagnostics.callback_p95_ms >= 0.0

    before_wrap = np.empty((512, 1), dtype=np.float32)
    after_wrap = np.empty((512, 1), dtype=np.float32)
    wrap_time = player.start_at_monotonic  # anchor is independent of DAC time
    stream.callback(
        before_wrap,
        512,
        SimpleNamespace(currentTime=1002.0, outputBufferDacTime=1004.5173333333),
        None,
    )
    stream.callback(
        after_wrap,
        512,
        SimpleNamespace(currentTime=1002.01, outputBufferDacTime=1004.52),
        None,
    )
    assert wrap_time > 0.0
    np.testing.assert_allclose(before_wrap[128:, 0], after_wrap[:384, 0])

    failed_output = np.empty((512, 1), dtype=np.float32)
    stream.callback(
        failed_output,
        512,
        SimpleNamespace(currentTime=float("nan"), outputBufferDacTime=1004.54),
        None,
    )
    assert np.max(np.abs(failed_output)) == 0.0
    assert player.diagnostics().callback_error_count == 1

    player.close()
    assert stream.stopped
    assert stream.closed


def test_calibration_tone_requires_a_pipewire_or_pulse_output() -> None:
    sounddevice = FakeSoundDevice(
        [
            {
                "name": "default",
                "index": 1,
                "hostapi": 1,
                "max_output_channels": 2,
                "default_samplerate": 48_000.0,
            }
        ]
    )

    with pytest.raises(RuntimeError, match="PipeWire/Pulse output"):
        AudioSyncCalibrationTone(sounddevice)


def test_calibration_tone_uses_default_output_from_pulse_host_api() -> None:
    sounddevice = FakeSoundDevice(
        [
            {
                "name": "HDMI Stereo",
                "index": 4,
                "hostapi": 0,
                "max_output_channels": 2,
                "default_samplerate": 48_000.0,
            }
        ],
        default_output_device=4,
    )

    player = AudioSyncCalibrationTone(sounddevice)

    assert sounddevice.streams[0].device == 4
    player.close()


def test_nonuniform_calibration_pattern_aligns_led_and_offset() -> None:
    start = 100.0

    assert sync_calibration_led_on(start, start, 0.0)
    assert not sync_calibration_led_on(start + 0.4, start, 0.0)
    assert sync_calibration_led_on(start + 0.2, start, 0.0)
    assert sync_calibration_led_on(start + 0.1, start, 0.1)
    assert not sync_calibration_led_on(start + SYNC_PATTERN_PERIOD_S, start, 0.1)
