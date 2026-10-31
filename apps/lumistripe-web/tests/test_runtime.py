from __future__ import annotations

import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import ClassVar

import lumistripe_web.runtime as runtime_module
import lumistripe_web.runtime.worker as worker_implementation
import numpy as np
import pytest
from lumistripe import (
    AnimationSequence,
    AnimationSequenceStep,
    AudioFrame,
    AudioInputDevice,
    AudioInputHealth,
    AudioProcessorStats,
    ColorCorrection,
    MultiController,
    MusicFeatures,
    PlaybackMode,
    Rgb,
    Stripe,
    SwitchQuantization,
    TransitionEffect,
)
from lumistripe.gpio.spi import SPIConfig
from lumistripe_web.runtime import (
    LumiStripeRuntime,
    OutputGateController,
    RuntimeCommandError,
    RuntimeSettings,
    UnknownAnimationError,
    _default_controller_factory,
)
from lumistripe_web.runtime.cadence import (
    AUDIO_TELEMETRY_INTERVAL_SECONDS,
    DASHBOARD_STATE_INTERVAL_SECONDS,
    PREVIEW_FRAME_INTERVAL_SECONDS,
)
from lumistripe_web.runtime.commands import _Command
from lumistripe_web.runtime.state import _CalibrationSession
from lumistripe_web.runtime.worker import (
    RuntimeWorker,
    _frame_deadline_missed,
    _FrameTimingWindow,
    _next_frame_due,
)
from lumistripe_web.settings import (
    AudioTuningProfile,
    CalibrationSettingsStore,
    StartupPlaybackSettings,
    StripeOutputSettings,
    StripeTopologySettings,
    TransitionSettings,
)
from lumistripe_web.spotify import SpotifyStatus


class TrackingStripe(Stripe):
    def __init__(self, length: int) -> None:
        super().__init__(length)
        self.flush_count = 0
        self.force_flush_count = 0
        self.close_count = 0

    def flush(self) -> None:
        self.flush_count += 1
        super().flush()

    def force_flush(self) -> None:
        self.force_flush_count += 1
        super().force_flush()

    def close(self) -> None:
        self.close_count += 1


class FakeSPIStripe(TrackingStripe):
    def __init__(self, config, length: int) -> None:
        super().__init__(length)
        self.config = config


class ThreadAwareSPIStripe(FakeSPIStripe):
    instances: ClassVar[list[ThreadAwareSPIStripe]] = []

    def __init__(self, config, length: int) -> None:
        super().__init__(config, length)
        self.thread_ids = {threading.get_ident()}
        self.frames: list[np.ndarray] = []
        type(self).instances.append(self)

    def flush(self) -> None:
        self.thread_ids.add(threading.get_ident())
        self.frames.append(self.pixels().copy())
        super().flush()

    def force_flush(self) -> None:
        self.thread_ids.add(threading.get_ident())
        self.frames.append(self.pixels().copy())
        super().force_flush()

    def close(self) -> None:
        self.thread_ids.add(threading.get_ident())
        super().close()


class StalledSPIStripe(ThreadAwareSPIStripe):
    entered: ClassVar[threading.Event] = threading.Event()
    release: ClassVar[threading.Event] = threading.Event()

    def flush(self) -> None:
        type(self).entered.set()
        type(self).release.wait()
        super().flush()


class FailingReplacementSPIStripe(ThreadAwareSPIStripe):
    def __init__(self, config, length: int) -> None:
        if config.device == "/dev/unavailable":
            raise RuntimeError("SPI device unavailable")
        super().__init__(config, length)


class FailingStripe(TrackingStripe):
    def flush(self) -> None:
        raise RuntimeError("render failed")


class RecoveringStripe(TrackingStripe):
    def __init__(self, length: int, *, failures: int = 1) -> None:
        super().__init__(length)
        self.failures = failures
        self.flush_attempts = 0

    def flush(self) -> None:
        self.flush_attempts += 1
        if self.flush_attempts <= self.failures:
            raise OSError("device temporarily unavailable")
        super().flush()


class BlockingStripe(TrackingStripe):
    def __init__(self, length: int) -> None:
        super().__init__(length)
        self.entered = threading.Event()
        self.release = threading.Event()

    def flush(self) -> None:
        self.entered.set()
        self.release.wait()
        super().flush()


class FakeAudioInput:
    def __init__(self, name: str, config) -> None:
        self.name = name
        self.config = config
        self.closed = False
        self.sequence = 0
        self.last_callback_age: float | None = 0.0

    def read(self) -> AudioFrame:
        self.sequence += 1
        return AudioFrame(
            rms=0.2,
            bands=(0.1, 0.2, 0.3, 0.4, 0.3, 0.2, 0.1, 0.05),
            sequence=self.sequence,
            timestamp=time.monotonic(),
            fresh=True,
        )

    def read_features(self) -> MusicFeatures:
        return MusicFeatures(energy=0.2, onset_strength=0.1, silence=False)

    def health(self) -> AudioInputHealth:
        return AudioInputHealth(
            callback_count=self.sequence,
            last_callback_age=self.last_callback_age,
            last_frame_age=0.0,
            processor=AudioProcessorStats(input_rms=0.12, normalization_gain=1.4),
        )

    def device_name(self) -> str:
        return self.name

    def reconfigure(self, config) -> None:
        self.config = config

    def close(self) -> None:
        self.closed = True


def test_stalled_audio_monitor_reopens_capture_after_callback_grace(
    tmp_path: Path,
) -> None:
    opened: list[FakeAudioInput] = []

    def audio_factory(device, config):
        del device
        audio_input = FakeAudioInput("USB Mic", config)
        opened.append(audio_input)
        return audio_input

    worker = RuntimeWorker(
        RuntimeSettings(
            audio_source="mic",
            audio_device="USB Mic",
            settings_file=tmp_path / "settings.json",
        ),
        audio_factory=audio_factory,
    )
    try:
        worker._initialize_audio_monitor()
        assert len(opened) == 1
        first = opened[0]
        first.last_callback_age = 0.75

        worker._maintain_audio_monitor(force=True)

        assert first.closed
        assert len(opened) == 2
        assert worker._audio_input is opened[1]
        assert worker._audio_monitor_error is None
    finally:
        worker._cleanup()


def test_audio_monitor_startup_grace_keeps_new_stream_open(
    tmp_path: Path,
) -> None:
    opened: list[FakeAudioInput] = []

    def audio_factory(device, config):
        del device
        audio_input = FakeAudioInput("USB Mic", config)
        audio_input.last_callback_age = None
        opened.append(audio_input)
        return audio_input

    worker = RuntimeWorker(
        RuntimeSettings(
            audio_source="mic",
            audio_device="USB Mic",
            settings_file=tmp_path / "settings.json",
        ),
        audio_factory=audio_factory,
    )
    try:
        worker._initialize_audio_monitor()
        worker._maintain_audio_monitor(force=True)

        assert len(opened) == 1
        assert opened[0].closed is False
        assert worker._audio_input is opened[0]
    finally:
        worker._cleanup()


def test_output_gate_blackout_preserves_latest_buffered_frame() -> None:
    stripe = TrackingStripe(3)
    gate = OutputGateController(stripe)
    gate.fill(Rgb(1, 2, 3))

    gate.set_blackout(True)
    assert gate.blackout is True
    assert stripe.force_flush_count == 1
    np.testing.assert_array_equal(stripe.pixels()[0, :3], np.array([1, 2, 3]))

    gate.fill(Rgb(10, 20, 30))
    gate.flush()
    assert stripe.flush_count == 0
    np.testing.assert_array_equal(stripe.pixels()[0, :3], np.array([10, 20, 30]))

    gate.set_blackout(False)
    assert gate.blackout is False
    assert stripe.force_flush_count == 2
    assert gate.last_successful_update_at is not None
    assert gate.last_successful_update_age_seconds is not None


def test_output_gate_retries_transient_io_failures_with_backoff() -> None:
    stripe = RecoveringStripe(2)
    gate = OutputGateController(stripe)

    gate.flush()
    assert gate.last_error == "OSError: device temporarily unavailable"
    assert stripe.flush_attempts == 1

    gate.flush()
    assert stripe.flush_attempts == 1

    time.sleep(0.11)
    gate.flush()
    assert stripe.flush_attempts == 2
    assert gate.last_error is None
    assert gate.last_successful_update_at is not None


def test_output_gate_restores_frame_when_blackout_write_fails() -> None:
    class FailingForceFlushStripe(TrackingStripe):
        def force_flush(self) -> None:
            self.force_flush_count += 1
            if self.force_flush_count == 1:
                raise OSError("GPIO line temporarily unavailable")
            super().force_flush()

    stripe = FailingForceFlushStripe(1)
    gate = OutputGateController(stripe)
    gate.fill(Rgb(10, 20, 30))

    with pytest.raises(OSError, match="temporarily unavailable"):
        gate.set_blackout(True)

    assert gate.blackout is False
    assert gate.last_error == "OSError: GPIO line temporarily unavailable"
    np.testing.assert_array_equal(stripe.pixels()[0], np.array([10, 20, 30, 255]))


def test_frame_deadline_uses_the_effective_animation_interval() -> None:
    assert not _frame_deadline_missed(
        due_at_s=0.0,
        completed_at_s=0.020,
        expected_delay_s=0.050,
    )
    assert _frame_deadline_missed(
        due_at_s=0.0,
        completed_at_s=0.020,
        expected_delay_s=0.010,
    )


def test_next_frame_schedule_preserves_period_and_skips_catchup() -> None:
    assert _next_frame_due(
        due_at_s=10.0,
        completed_at_s=10.004,
        expected_delay_s=0.020,
    ) == pytest.approx(10.020)
    assert _next_frame_due(
        due_at_s=10.0,
        completed_at_s=10.025,
        expected_delay_s=0.020,
    ) == pytest.approx(10.025)
    assert _next_frame_due(
        due_at_s=10.0,
        completed_at_s=10.004,
        expected_delay_s=0.010,
    ) == pytest.approx(10.016)


def test_frame_timing_window_prunes_old_outcomes() -> None:
    window = _FrameTimingWindow(window_seconds=10.0)
    window.record(0.0, missed_deadline=True)
    window.record(5.0, missed_deadline=False)

    current = window.stats(5.0)
    assert current.frame_count == 2
    assert current.missed_frame_count == 1
    assert current.miss_rate == pytest.approx(0.5)

    recent = window.stats(10.1)
    assert recent.frame_count == 1
    assert recent.missed_frame_count == 0
    assert recent.miss_rate == 0.0


def test_renderer_deadline_diagnostic_uses_recent_miss_rate() -> None:
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=2))
    runtime._frame_rate = 20.0
    runtime._last_render_time_ms = 18.0
    runtime._missed_frame_count = 947
    now = time.monotonic()
    for index in range(40):
        runtime._frame_timing.record(
            now - 1.0 + index * 0.01,
            missed_deadline=index < 4,
        )

    issues = runtime._diagnostic_issues(running=True, uptime_seconds=10.0)
    assert not any(issue.title == "Renderer missed frame deadlines" for issue in issues)

    for index in range(2):
        runtime._frame_timing.record(now, missed_deadline=True)
    issues = runtime._diagnostic_issues(running=True, uptime_seconds=10.0)
    issue = next(
        issue for issue in issues if issue.title == "Renderer missed frame deadlines"
    )
    assert "6 of the last 42 frames" in issue.message
    assert "947 misses have been recorded since startup" in issue.message


def test_frame_publications_follow_client_cadences(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = RuntimeWorker(RuntimeSettings(pixels=2))
    now = [0.0]
    events: list[str] = []
    runtime._last_audio_telemetry_publication_at_s = 0.0
    runtime._last_dashboard_publication_at_s = 0.0
    runtime._last_preview_publication_at_s = 0.0
    monkeypatch.setattr(worker_implementation.time, "monotonic", lambda: now[0])

    def publish_telemetry() -> None:
        events.append("audio")
        runtime._last_audio_telemetry_publication_at_s = now[0]

    def capture_preview(*, use_pending: bool = False) -> None:
        assert use_pending
        events.append("preview")
        runtime._last_preview_publication_at_s = now[0]

    def publish_state(**kwargs: object) -> None:
        assert kwargs == {"running": True, "capture_preview": False}
        events.append("state")
        runtime._last_dashboard_publication_at_s = now[0]

    monkeypatch.setattr(runtime, "_update_audio_telemetry", publish_telemetry)
    monkeypatch.setattr(runtime, "_capture_preview_frame", capture_preview)
    monkeypatch.setattr(runtime, "_publish", publish_state)

    now[0] = 0.01
    runtime._publish_frame_updates()
    assert events == []

    now[0] = DASHBOARD_STATE_INTERVAL_SECONDS
    runtime._publish_frame_updates()
    assert events == ["audio", "preview", "state"]

    events.clear()
    now[0] += PREVIEW_FRAME_INTERVAL_SECONDS + 0.001
    runtime._publish_frame_updates()
    assert events == ["preview"]

    events.clear()
    now[0] = DASHBOARD_STATE_INTERVAL_SECONDS + AUDIO_TELEMETRY_INTERVAL_SECONDS + 0.002
    runtime._publish_frame_updates()
    assert events == ["audio", "preview"]


def test_power_budget_scales_rendered_frame_without_changing_requested_brightness(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    CalibrationSettingsStore(path).save_stripes(
        StripeTopologySettings(
            outputs=(StripeOutputSettings(id="left", name="Left", pixels=4),),
            power_budget_enabled=True,
            power_budget_watts=0.6,
        )
    )
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=4, settings_file=path))
    runtime.start()
    try:
        runtime.set_mode(PlaybackMode.SOLID, solid_color="#FFFFFF").result(timeout=1)
        deadline = time.monotonic() + 1
        while runtime.snapshot().power_budget.estimated_watts < 1.0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        state = runtime.snapshot()
        assert state.brightness == pytest.approx(1.0)
        assert state.power_budget.applied_scale == pytest.approx(0.5)
        assert state.power_budget.limiting_output_id is None
        assert any(issue.title == "Power budget is limiting brightness" for issue in state.diagnostic_issues)
        assert runtime.preview_frame().outputs[0][3] == 127
    finally:
        runtime.stop()


@pytest.mark.parametrize("layout", ["mirrored", "continuous", "independent"])
def test_zone_mapping_expands_physical_frames_for_every_layout(
    tmp_path: Path, layout: str
) -> None:
    topology = StripeTopologySettings(
        layout=layout,  # type: ignore[arg-type]
        outputs=(
            StripeOutputSettings(
                id="left",
                name="Left",
                pixels=8,
                zone_count=4,
                zone_pattern="alternate",
            ),
            StripeOutputSettings(
                id="right",
                name="Right",
                pixels=6,
                spi_device="/dev/spidev1.0",
                zone_count=3,
            ),
        ),
    )
    worker = RuntimeWorker(RuntimeSettings(settings_file=tmp_path / "settings.json"))
    worker._topology = topology
    worker._install_topology(worker._build_topology(topology))
    try:
        if layout == "continuous":
            worker._raw_controller.set_pixels(
                np.array(
                    [[10, 0, 0, 255], [20, 0, 0, 255], [30, 0, 0, 255], [40, 0, 0, 255]],
                    dtype=np.uint8,
                )
            )
            expected_left = [10, 20, 20, 10, 10, 20, 20, 10]
            expected_right = [30, 40, 30, 40, 30, 40]
        elif layout == "mirrored":
            worker._raw_controller.set_pixels(
                np.array([[10, 0, 0, 255], [20, 0, 0, 255]], dtype=np.uint8)
            )
            expected_left = [10, 20, 20, 10, 10, 20, 20, 10]
            expected_right = [10, 20, 10, 20, 10, 20]
        else:
            for renderer, values in zip(
                worker._output_renderers,
                ([10, 20], [30, 40]),
                strict=True,
            ):
                renderer.set_pixels(
                    np.array([[value, 0, 0, 255] for value in values], dtype=np.uint8)
                )
            expected_left = [10, 20, 20, 10, 10, 20, 20, 10]
            expected_right = [30, 40, 30, 40, 30, 40]

        worker._flush_power_limited_outputs()
        worker._capture_preview_frame()

        for index, expected in enumerate((expected_left, expected_right)):
            assert worker._output_gates[index].length == len(expected)
            np.testing.assert_array_equal(
                worker._output_gates[index].pixels()[:, 0], expected
            )
            preview = worker.preview_frame().outputs[index]
            assert len(preview) == len(expected) * 4
            np.testing.assert_array_equal(
                np.frombuffer(preview, dtype=np.uint8).reshape(-1, 4)[:, 0], expected
            )
    finally:
        worker._cleanup()


def test_zone_mapping_power_limit_and_blackout_use_expanded_physical_frame(
    tmp_path: Path,
) -> None:
    topology = StripeTopologySettings(
        outputs=(
            StripeOutputSettings(
                id="left",
                name="Left",
                pixels=8,
                zone_count=4,
            ),
        ),
        power_budget_enabled=True,
        power_budget_watts=0.6,
    )
    worker = RuntimeWorker(RuntimeSettings(settings_file=tmp_path / "settings.json"))
    worker._topology = topology
    worker._install_topology(worker._build_topology(topology))
    try:
        worker._raw_controller.fill(Rgb(255, 255, 255))
        worker._flush_power_limited_outputs()
        worker._capture_preview_frame()

        assert worker._power_budget_state.estimated_watts == pytest.approx(2.4)
        assert worker._power_budget_state.applied_scale == pytest.approx(0.25)
        physical = worker._output_gates[0].pixels()
        assert physical.shape == (8, 4)
        np.testing.assert_array_equal(physical[:, 3], np.full(8, 255, dtype=np.uint8))
        preview = np.frombuffer(
            worker.preview_frame().outputs[0], dtype=np.uint8
        ).reshape(-1, 4)
        assert preview.shape == (8, 4)
        np.testing.assert_array_equal(preview[:, :3], np.full((8, 3), 255))
        np.testing.assert_array_equal(preview[:, 3], np.full(8, 63, dtype=np.uint8))

        assert worker._controller is not None
        worker._controller.set_blackout(True)
        worker._flush_power_limited_outputs()
        worker._capture_preview_frame()
        assert worker.preview_frame().outputs[0] == bytes(8 * 4)
    finally:
        worker._cleanup()


def test_zone_mapping_wraps_static_and_dynamic_animation_frames(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    CalibrationSettingsStore(path).save_stripes(
        StripeTopologySettings(
            outputs=(
                StripeOutputSettings(
                    id="zoned",
                    name="Zoned",
                    pixels=40,
                    zone_count=4,
                    zone_pattern="alternate",
                ),
            ),
        )
    )
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=40, settings_file=path))
    runtime.start()
    try:
        runtime.select_animation("comet").result(timeout=1)

        def current_zones(after_sequence: int) -> np.ndarray:
            deadline = time.monotonic() + 2
            frame = runtime.preview_frame()
            while frame.sequence <= after_sequence:
                assert time.monotonic() < deadline
                time.sleep(0.01)
                frame = runtime.preview_frame()
            return np.frombuffer(frame.outputs[0], dtype=np.uint8).reshape(4, 10, 4)

        static_zones = current_zones(0)
        np.testing.assert_array_equal(static_zones[0], static_zones[2])
        np.testing.assert_array_equal(static_zones[1], static_zones[3])
        np.testing.assert_array_equal(static_zones[1], static_zones[0][::-1])

        previous_sequence = runtime.preview_frame().sequence
        runtime.set_mode(PlaybackMode.DYNAMIC).result(timeout=1)
        dynamic_zones = current_zones(previous_sequence)
        np.testing.assert_array_equal(dynamic_zones[0], dynamic_zones[2])
        np.testing.assert_array_equal(dynamic_zones[1], dynamic_zones[3])
        np.testing.assert_array_equal(dynamic_zones[1], dynamic_zones[0][::-1])
    finally:
        runtime.stop()


def test_zone_mapping_keeps_calibration_and_identification_physical(
    tmp_path: Path,
) -> None:
    topology = StripeTopologySettings(
        outputs=(
            StripeOutputSettings(
                id="zoned",
                name="Zoned",
                pixels=8,
                zone_count=4,
            ),
        ),
    )
    worker = RuntimeWorker(RuntimeSettings(settings_file=tmp_path / "settings.json"))
    worker._topology = topology
    worker._install_topology(worker._build_topology(topology))
    try:
        worker._test_stripe("zoned", "identify", None)

        physical = worker._correction_controllers[0].pixels()
        assert physical.shape == (8, 4)
        assert np.count_nonzero(np.any(physical[:, :3] > 0, axis=1)) == 1
        assert worker._output_renderers[0].length == 2

        correction = worker._correction_controllers[0]
        worker._calibration = _CalibrationSession(
            session_id="calibration",
            output_index=0,
            pattern="red",
            original_corrections=(correction.correction,),
            original_frames=(correction.pixels().copy(),),
            original_blackout=False,
            last_activity_at=time.monotonic(),
        )
        worker._render_calibration_pattern()

        assert correction.pixels().shape == (8, 4)
        np.testing.assert_array_equal(
            correction.pixels(),
            np.tile(np.array([[255, 0, 0, 255]], dtype=np.uint8), (8, 1)),
        )
    finally:
        worker._cleanup()


@pytest.mark.parametrize(
    ("supply_capacity_watts", "pi_reserve_watts", "expected_led_watts", "scale"),
    ((15.6, 15.0, 0.6, 0.5), (15.0, 16.0, 0.0, 0.0)),
)
def test_shared_supply_budget_reserves_pi_power_and_scales_leds(
    tmp_path: Path,
    supply_capacity_watts: float,
    pi_reserve_watts: float,
    expected_led_watts: float,
    scale: float,
) -> None:
    path = tmp_path / "settings.json"
    CalibrationSettingsStore(path).save_stripes(
        StripeTopologySettings(
            outputs=(StripeOutputSettings(id="left", name="Left", pixels=4),),
            shared_supply_enabled=True,
            supply_capacity_watts=supply_capacity_watts,
            pi_reserve_watts=pi_reserve_watts,
        )
    )
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=4, settings_file=path))
    runtime.start()
    try:
        runtime.set_mode(PlaybackMode.SOLID, solid_color="#FFFFFF").result(timeout=1)
        deadline = time.monotonic() + 1
        while runtime.snapshot().power_budget.estimated_watts < 1.0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        state = runtime.snapshot()
        assert state.power_budget.enabled is True
        assert state.power_budget.shared_supply_enabled is True
        assert state.power_budget.supply_capacity_watts == supply_capacity_watts
        assert state.power_budget.pi_reserve_watts == pi_reserve_watts
        assert state.power_budget.available_led_watts == pytest.approx(expected_led_watts)
        assert state.power_budget.budget_watts == pytest.approx(expected_led_watts)
        assert state.power_budget.applied_scale == pytest.approx(scale)
        assert runtime.preview_frame().outputs[0][3] == int(255 * scale)
    finally:
        runtime.stop()


def test_power_budget_uses_tighter_per_output_limit_for_independent_outputs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    CalibrationSettingsStore(path).save_stripes(
        StripeTopologySettings(
            layout="independent",
            outputs=(
                StripeOutputSettings(id="left", name="Left", pixels=2, power_limit_watts=0.3),
                StripeOutputSettings(
                    id="right",
                    name="Right",
                    pixels=2,
                    spi_device="/dev/spidev1.0",
                    power_limit_watts=10.0,
                ),
            ),
            power_budget_enabled=True,
            power_budget_watts=10.0,
        )
    )
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=2, settings_file=path))
    runtime.start()
    try:
        runtime.set_mode(PlaybackMode.SOLID, solid_color="#FFFFFF").result(timeout=1)
        deadline = time.monotonic() + 1
        while runtime.snapshot().power_budget.applied_scale >= 1.0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        state = runtime.snapshot()
        assert state.power_budget.limiting_output_id == "left"
        assert state.power_budget.applied_scale == pytest.approx(0.5)
    finally:
        runtime.stop()


def test_preview_frame_is_an_immutable_rgba_snapshot(tmp_path: Path) -> None:
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=3, settings_file=tmp_path / "settings.json")
    )
    runtime.start()
    try:
        deadline = time.monotonic() + 1
        frame = runtime.preview_frame()
        while not frame.outputs or not any(frame.outputs[0]):
            assert time.monotonic() < deadline
            time.sleep(0.01)
            frame = runtime.preview_frame()

        assert frame.sequence > 0
        assert len(frame.outputs) == 1
        assert len(frame.outputs[0]) == 3 * 4
        captured = frame.outputs[0]

        runtime.set_mode(PlaybackMode.SOLID, solid_color="#FF0000").result(timeout=1)
        assert frame.outputs[0] == captured
    finally:
        runtime.stop()


def test_preview_frame_applies_correction_reversal_and_blackout(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = CalibrationSettingsStore(path)
    store.save_stripes(
        StripeTopologySettings(
            layout="independent",
            outputs=(
                StripeOutputSettings(id="left", name="Left", pixels=2, reversed=True),
            ),
        )
    )
    store.save({"left": ColorCorrection(red=128)})
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=2, settings_file=path))
    runtime.start()
    try:
        correction = runtime._correction_controllers[0]
        correction.set_pixels(
            np.array([[10, 20, 30, 255], [100, 110, 120, 255]], dtype=np.uint8)
        )
        correction.force_flush()
        runtime._capture_preview_frame(use_pending=False)

        assert runtime.preview_frame().outputs[0] == bytes((50, 110, 120, 255, 5, 20, 30, 255))

        runtime._output_gates[0].set_blackout(True)
        runtime._capture_preview_frame(use_pending=False)
        assert runtime.preview_frame().outputs[0] == bytes(8)
    finally:
        runtime.stop()


def test_runtime_controls_simulation_and_cleans_up() -> None:
    stripe = TrackingStripe(8)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8),
        controller_factory=lambda settings: stripe,
    )
    runtime.start()

    assert runtime.healthy is True
    assert runtime.snapshot().runtime == "simulation"
    assert runtime.snapshot().output_backend == "simulation"
    assert runtime.snapshot().output_devices == ()
    assert len(runtime.animations()) > 40

    dynamic = runtime.set_mode(PlaybackMode.DYNAMIC).result(timeout=1)
    assert dynamic.mode is PlaybackMode.DYNAMIC
    assert dynamic.audio_status == "Using internal demo beat."
    assert dynamic.audio_health == "demo"
    assert dynamic.application_version

    bright = runtime.set_brightness(0.35).result(timeout=1)
    assert bright.brightness == pytest.approx(0.35)

    blacked_out = runtime.set_blackout(True).result(timeout=1)
    assert blacked_out.blackout is True
    assert blacked_out.brightness == pytest.approx(0.35)

    restored = runtime.set_blackout(False).result(timeout=1)
    assert restored.blackout is False
    assert restored.brightness == pytest.approx(0.35)

    deadline = time.monotonic() + 2
    while runtime.snapshot().frame_rate == 0.0:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    diagnostics = runtime.snapshot()
    assert diagnostics.uptime_seconds > 0.0
    assert diagnostics.frame_rate > 0.0
    assert diagnostics.last_output_at is not None
    assert diagnostics.last_output_age_seconds is not None
    assert diagnostics.diagnostic_issues == ()

    runtime.stop()
    assert runtime.snapshot().running is False
    assert stripe.close_count == 1
    assert stripe.force_flush_count >= 3


def test_brightness_commands_coalesce_while_a_frame_is_in_progress() -> None:
    stripe = BlockingStripe(8)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8),
        controller_factory=lambda settings: stripe,
    )
    runtime.start()
    assert stripe.entered.wait(1)

    first = runtime.set_brightness(0.2)
    second = runtime.set_brightness(0.8)
    stripe.release.set()
    try:
        assert first.result(timeout=1).brightness == pytest.approx(0.8)
        assert second.result(timeout=1).brightness == pytest.approx(0.8)
    finally:
        runtime.stop()


def test_command_submission_is_rejected_once_shutdown_begins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stripe = BlockingStripe(8)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8), controller_factory=lambda settings: stripe
    )
    runtime.start()
    assert stripe.entered.wait(1)
    reached_admission = threading.Event()
    original_key = runtime._coalesce_key

    def signal_admission(name: str, value: object) -> tuple[str, str | None] | None:
        reached_admission.set()
        return original_key(name, value)

    monkeypatch.setattr(runtime, "_coalesce_key", signal_admission)
    futures: list[Future[object]] = []
    submitter = threading.Thread(target=lambda: futures.append(runtime.set_brightness(0.5)))
    try:
        with runtime._command_lock:
            submitter.start()
            assert reached_admission.wait(1)
            runtime._stop_event.set()
        submitter.join(1)
        assert not submitter.is_alive()
        with pytest.raises(worker_implementation.RuntimeUnavailableError):
            futures[0].result(timeout=1)
    finally:
        stripe.release.set()
        runtime.stop()


def test_full_command_queue_rejects_new_work_and_does_not_block_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_implementation, "MAX_PENDING_COMMANDS", 2)
    stripe = BlockingStripe(8)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8), controller_factory=lambda settings: stripe
    )
    runtime.start()
    assert stripe.entered.wait(1)
    queued = [runtime.set_mode(PlaybackMode.STATIC) for _ in range(2)]
    rejected = runtime.set_mode(PlaybackMode.STATIC)
    with pytest.raises(worker_implementation.RuntimeUnavailableError, match="queue is full"):
        rejected.result(timeout=1)
    stopper = threading.Thread(target=runtime.stop)
    stopper.start()
    assert runtime._stop_event.wait(1)
    stripe.release.set()
    stopper.join(2)
    assert not stopper.is_alive()
    for future in queued:
        with pytest.raises(worker_implementation.RuntimeUnavailableError):
            future.result(timeout=1)


def test_cleanup_continues_and_rejects_pending_after_backend_stop_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stripe = BlockingStripe(8)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8), controller_factory=lambda settings: stripe
    )
    runtime.start()
    assert stripe.entered.wait(1)
    original_bluetooth_stop = runtime._bluetooth.stop
    original_spotify_stop = runtime._spotify.stop
    spotify_stopped = threading.Event()

    def fail_bluetooth_stop() -> None:
        original_bluetooth_stop()
        raise RuntimeError("bluetooth stop failed")

    def stop_spotify() -> None:
        original_spotify_stop()
        spotify_stopped.set()

    monkeypatch.setattr(runtime._bluetooth, "stop", fail_bluetooth_stop)
    monkeypatch.setattr(runtime._spotify, "stop", stop_spotify)
    pending = runtime.set_mode(PlaybackMode.STATIC)
    stopper = threading.Thread(target=runtime.stop)
    stopper.start()
    assert runtime._stop_event.wait(1)
    stripe.release.set()
    stopper.join(2)
    assert not stopper.is_alive()
    with pytest.raises(worker_implementation.RuntimeUnavailableError):
        pending.result(timeout=1)
    assert spotify_stopped.is_set()
    assert stripe.close_count == 1
    assert runtime.snapshot().running is False
    assert runtime.snapshot().error == "bluetooth stop failed"


def test_coalesced_brightness_waiters_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_implementation, "MAX_COALESCED_COMMAND_WAITERS", 2)
    stripe = BlockingStripe(8)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8), controller_factory=lambda settings: stripe
    )
    runtime.start()
    assert stripe.entered.wait(1)
    first = runtime.set_brightness(0.2)
    second = runtime.set_brightness(0.8)
    rejected = runtime.set_brightness(0.4)
    try:
        with pytest.raises(worker_implementation.RuntimeUnavailableError, match="queue is full"):
            rejected.result(timeout=1)
        stripe.release.set()
        assert first.result(timeout=1).brightness == pytest.approx(0.8)
        assert second.result(timeout=1).brightness == pytest.approx(0.8)
    finally:
        stripe.release.set()
        runtime.stop()


def test_cancelled_future_during_completion_does_not_break_coalesced_commands() -> None:
    class CancellingFuture(Future[object]):
        def set_result(self, result: object) -> None:
            self.cancel()
            super().set_result(result)

    cancelled = CancellingFuture()
    remaining: Future[object] = Future()
    command = _Command("brightness", None, cancelled, [remaining])
    RuntimeWorker._resolve_command(command, result="applied")
    assert cancelled.cancelled()
    assert remaining.result(timeout=1) == "applied"


def test_fatal_worker_failure_invokes_exit_callback_after_cleanup() -> None:
    stripe = FailingStripe(8)
    exits: list[int] = []
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8),
        controller_factory=lambda settings: stripe,
        fatal_exit=exits.append,
    )
    runtime.start()
    try:
        deadline = time.monotonic() + 1
        while not exits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert exits == [1]
        assert runtime.snapshot().error == "render failed"
        assert stripe.close_count == 1
    finally:
        runtime.stop()


@pytest.mark.parametrize("temporary_test", [False, True])
def test_failed_topology_rollback_stops_worker_and_requests_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temporary_test: bool,
) -> None:
    exits: list[int] = []
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=4, settings_file=tmp_path / "settings.json"),
        fatal_exit=exits.append,
    )
    runtime.start()

    def fail_build(topology: StripeTopologySettings) -> None:
        raise OSError("forced topology build failure")

    monkeypatch.setattr(runtime, "_build_topology", fail_build)
    topology = StripeTopologySettings(
        outputs=(StripeOutputSettings(id="draft", name="Draft", pixels=4),)
    )
    try:
        operation = (
            runtime.test_stripe("draft", "identify", topology)
            if temporary_test
            else runtime.apply_stripe_topology(topology)
        )
        with pytest.raises(RuntimeCommandError, match="rollback failed"):
            operation.result(timeout=1)

        deadline = time.monotonic() + 1
        while not exits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert exits == [1]
        assert runtime.healthy is False
        assert runtime.snapshot().running is False
        assert "rollback failed" in (runtime.snapshot().error or "")
        with pytest.raises(worker_implementation.RuntimeUnavailableError):
            runtime.set_brightness(0.5).result(timeout=1)
    finally:
        runtime.stop()


@pytest.mark.parametrize("temporary_test", [False, True])
def test_topology_cleanup_failure_restores_previous_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    temporary_test: bool,
) -> None:
    stripes: list[TrackingStripe] = []

    class FirstCloseFailingStripe(TrackingStripe):
        def __init__(self, length: int) -> None:
            super().__init__(length)
            stripes.append(self)

        def close(self) -> None:
            super().close()
            if self is stripes[0]:
                raise OSError("old output close failed")

    monkeypatch.setattr(worker_implementation, "Stripe", FirstCloseFailingStripe)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=4, settings_file=tmp_path / "settings.json")
    )
    runtime.start()
    original_topology = runtime.snapshot().stripe_topology
    topology = StripeTopologySettings(
        outputs=(StripeOutputSettings(id="draft", name="Draft", pixels=4),)
    )
    try:
        operation = (
            runtime.test_stripe("draft", "red", topology)
            if temporary_test
            else runtime.apply_stripe_topology(topology)
        )
        with pytest.raises(RuntimeCommandError, match="could not release current outputs"):
            operation.result(timeout=1)

        assert runtime.healthy is True
        assert runtime.snapshot().running is True
        assert runtime.snapshot().stripe_topology == original_topology
        assert stripes[0].close_count == 1
        assert len(stripes) >= 2
        assert stripes[1].close_count == 0
    finally:
        runtime.stop()


def test_transient_output_io_failure_keeps_runtime_running_and_reports_output_error() -> None:
    stripe = RecoveringStripe(8, failures=5)
    exits: list[int] = []
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8),
        controller_factory=lambda settings: stripe,
        fatal_exit=exits.append,
    )
    runtime.start()
    try:
        deadline = time.monotonic() + 5.0
        outputs = ()
        while time.monotonic() < deadline:
            state = runtime.snapshot()
            outputs = state.stripe_topology.outputs
            if outputs and outputs[0].error is not None:
                break
            time.sleep(0.01)
        assert runtime.snapshot().running is True
        assert outputs[0].error == "OSError: device temporarily unavailable"
        assert exits == []

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            state = runtime.snapshot()
            outputs = state.stripe_topology.outputs
            if outputs and outputs[0].error is None and stripe.flush_attempts >= 2:
                break
            time.sleep(0.01)
        assert runtime.snapshot().running is True
        assert outputs[0].error is None
        assert stripe.flush_attempts >= 2
    finally:
        runtime.stop()
    assert exits == []


def test_startup_timeout_stops_late_worker_and_requests_process_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "WORKER_STOP_GRACE_SECONDS", 0.01)
    entered = threading.Event()
    release = threading.Event()
    stripe = TrackingStripe(8)
    exits: list[int] = []

    def blocked_factory(settings: RuntimeSettings) -> TrackingStripe:
        entered.set()
        release.wait()
        return stripe

    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8), controller_factory=blocked_factory, fatal_exit=exits.append
    )
    try:
        with pytest.raises(worker_implementation.RuntimeUnavailableError, match="startup timed out"):
            runtime.start(timeout=0.01)
        assert entered.is_set()
        assert runtime._stop_event.is_set()
        assert exits == [1]
    finally:
        release.set()
        runtime.stop(timeout=2)
    assert stripe.close_count == 1
    assert runtime.snapshot().running is False


def test_stalled_worker_invokes_exit_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "WORKER_STARTUP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(runtime_module, "WORKER_WATCHDOG_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(runtime_module, "WORKER_STOP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(runtime_module, "FRAME_STALL_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(runtime_module, "FRAME_STALL_MIN_SECONDS", 0.02)
    monkeypatch.setattr(runtime_module, "FRAME_STALL_MAX_SECONDS", 0.05)

    stripe = BlockingStripe(8)
    exits: list[int] = []
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8),
        controller_factory=lambda settings: stripe,
        fatal_exit=exits.append,
    )
    runtime.start()
    assert stripe.entered.wait(1)
    try:
        deadline = time.monotonic() + 1
        while not exits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert exits == [1]
    finally:
        stripe.release.set()
        runtime.stop(timeout=1)
    assert "stopped making progress" in (runtime.snapshot().error or "")


@pytest.mark.parametrize(
    "command_limit,expect_exit,blackout",
    [(0.5, False, False), (0.05, True, False), (0.05, True, True)],
)
def test_watchdog_distinguishes_slow_commands_from_stalled_commands(
    monkeypatch: pytest.MonkeyPatch, command_limit: float, expect_exit: bool, blackout: bool
) -> None:
    monkeypatch.setattr(runtime_module, "WORKER_STARTUP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(runtime_module, "WORKER_WATCHDOG_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(runtime_module, "WORKER_STOP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(runtime_module, "FRAME_STALL_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(runtime_module, "FRAME_STALL_MIN_SECONDS", 0.02)
    monkeypatch.setattr(runtime_module, "FRAME_STALL_MAX_SECONDS", 0.05)
    entered = threading.Event()
    release = threading.Event()
    exited = threading.Event()

    class SlowSpotify:
        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def status(self) -> SpotifyStatus:
            return SpotifyStatus(configured=True, connected=True)

        def control(self, action: str, value: object | None = None) -> SpotifyStatus:
            entered.set()
            release.wait()
            return self.status()

    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8),
        spotify_manager=SlowSpotify(),
        fatal_exit=lambda code: exited.set(),
    )
    monkeypatch.setattr(worker_implementation, "COMMAND_STALL_MAX_SECONDS", command_limit)
    runtime.start()
    try:
        if blackout:
            runtime.set_blackout(True).result(timeout=1)
        future = runtime.control_spotify("play")
        assert entered.wait(1)
        assert exited.wait(0.15) is expect_exit
        release.set()
        if not expect_exit:
            future.result(timeout=1)
    finally:
        release.set()
        runtime.stop(timeout=2)


def test_microphone_monitor_stays_active_outside_dynamic_mode_and_saves_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = FakeAudioInput("USB Mic", None)
    monkeypatch.setattr(
        runtime_module,
        "list_input_device_details",
        lambda: [AudioInputDevice(index=2, name="USB Mic")],
    )
    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=4,
            audio_source="mic",
            audio_device="2",
            settings_file=tmp_path / "settings.json",
        ),
        controller_factory=lambda settings: TrackingStripe(settings.pixels),
        audio_factory=lambda device, config: fake,
    )
    runtime.start()
    try:
        assert runtime.snapshot().mode is PlaybackMode.STATIC
        deadline = time.monotonic() + 1
        while runtime.audio_telemetry().sequence == 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert runtime.audio_telemetry().input_level == pytest.approx(0.12)
        assert runtime.audio_telemetry().gate_preview is True

        profile = AudioTuningProfile(
            target_level=0.5,
            dynamic_response=0.8,
            energy_threshold=0.1,
        )
        response = runtime.apply_audio_settings("2", profile).result(timeout=1)

        assert response.monitoring is True
        assert response.settings.target_level == pytest.approx(0.5)
        assert response.settings.dynamic_response == pytest.approx(0.8)
        assert fake.config.normalization.target_level == pytest.approx(0.5)
        assert runtime.playback.config.dynamic_response == pytest.approx(0.8)
        saved, warning = CalibrationSettingsStore(tmp_path / "settings.json").load_all()
        assert warning is None
        assert saved.audio_profiles["USB Mic"] == profile
    finally:
        runtime.stop()


def test_failed_microphone_swap_keeps_current_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current = FakeAudioInput("USB Mic", None)
    monkeypatch.setattr(
        runtime_module,
        "list_input_device_details",
        lambda: [
            AudioInputDevice(index=1, name="USB Mic"),
            AudioInputDevice(index=2, name="Broken Mic"),
        ],
    )

    def audio_factory(device, config):
        del config
        if device == "2":
            raise RuntimeError("device busy")
        return current

    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=4,
            audio_source="mic",
            audio_device="1",
            settings_file=tmp_path / "settings.json",
        ),
        controller_factory=lambda settings: TrackingStripe(settings.pixels),
        audio_factory=audio_factory,
    )
    runtime.start()
    try:
        with pytest.raises(RuntimeCommandError, match="device busy"):
            runtime.apply_audio_settings("2", AudioTuningProfile()).result(timeout=1)
        assert runtime.audio_settings().active_device_name == "USB Mic"
        assert current.closed is False
    finally:
        runtime.stop()


def test_saved_audio_device_takes_precedence_over_cli_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    CalibrationSettingsStore(path).save_audio("Saved Mic", {})
    opened: list[str | None] = []

    def audio_factory(device, config):
        del config
        opened.append(device)
        return FakeAudioInput("Saved Mic", None)

    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=4,
            audio_source="mic",
            audio_device="CLI Mic",
            settings_file=path,
        ),
        controller_factory=lambda settings: TrackingStripe(settings.pixels),
        audio_factory=audio_factory,
    )
    runtime.start()
    try:
        assert opened == ["Saved Mic"]
    finally:
        runtime.stop()


def test_startup_restore_remembers_all_target_changes_only(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = CalibrationSettingsStore(path)
    store.save_stripes(
        StripeTopologySettings(
            layout="independent",
            outputs=(
                StripeOutputSettings(id="left", name="Left", pixels=4),
                StripeOutputSettings(id="right", name="Right", pixels=4, spi_device="/dev/spidev1.0"),
            ),
        )
    )
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=4, settings_file=path))
    runtime.start()
    try:
        runtime.set_startup_restore(True).result(timeout=1)
        runtime.set_mode(PlaybackMode.SOLID, solid_color="#AA1100").result(timeout=1)
        runtime.set_brightness(0.25).result(timeout=1)
        runtime.set_brightness(0.8, stripe_id="left").result(timeout=1)
        runtime.set_blackout(True).result(timeout=1)
    finally:
        runtime.stop()

    saved, warning = store.load_all()
    assert warning is None
    assert saved.remembered_playback.brightness == pytest.approx(0.25)

    restored = LumiStripeRuntime(RuntimeSettings(pixels=4, settings_file=path))
    restored.start()
    try:
        states = restored.snapshot().stripe_playback
        assert len(states) == 2
        assert all(item.mode is PlaybackMode.SOLID for item in states)
        assert all(item.solid_color == "#AA1100" for item in states)
        assert all(item.brightness == pytest.approx(0.25) for item in states)
        assert all(item.blackout is True for item in states)
    finally:
        restored.stop()


def test_unknown_startup_animation_falls_back_with_warning(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    CalibrationSettingsStore(path).save_startup(
        True, StartupPlaybackSettings(animation="removed-animation")
    )
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=4, settings_file=path))
    runtime.start()
    try:
        assert runtime.healthy is True
        assert any(
            issue.title == "Dashboard settings were not fully loaded"
            and "removed-animation" in issue.message
            for issue in runtime.snapshot().diagnostic_issues
        )
    finally:
        runtime.stop()


def test_select_animation_switches_to_static() -> None:
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=8))
    runtime.start()
    try:
        runtime.set_mode(PlaybackMode.DYNAMIC).result(timeout=1)
        target = runtime.animations()[1].name
        state = runtime.select_animation(target).result(timeout=1)
        assert state.mode is PlaybackMode.STATIC
        assert state.animation == target
        assert state.audio_status == "No audio source active."
    finally:
        runtime.stop()


def test_blackout_and_shutdown_are_immediate_during_animation_transition() -> None:
    stripe = TrackingStripe(8)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=8),
        controller_factory=lambda settings: stripe,
    )
    runtime.start()
    try:
        current = runtime.snapshot().animation
        target = next(item.name for item in runtime.animations() if item.name != current)
        runtime.select_animation(target).result(timeout=1)
        assert runtime.playback.transition_active is True

        flushes_before_blackout = stripe.force_flush_count
        state = runtime.set_blackout(True).result(timeout=1)
        assert state.blackout is True
        assert stripe.force_flush_count == flushes_before_blackout + 1
        assert runtime.playback.transition_active is True
    finally:
        runtime.stop()

    assert stripe.force_flush_count >= 2
    np.testing.assert_array_equal(
        stripe.pixels(), np.tile(np.array([0, 0, 0, 255], dtype=np.uint8), (8, 1))
    )


def test_color_calibration_renders_immediately_during_animation_transition() -> None:
    stripe = TrackingStripe(4)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=4),
        controller_factory=lambda settings: stripe,
    )
    started = None
    runtime.start()
    try:
        current = runtime.snapshot().animation
        target = next(item.name for item in runtime.animations() if item.name != current)
        runtime.select_animation(target).result(timeout=1)
        assert runtime.playback.transition_active is True

        flushes_before_calibration = stripe.force_flush_count
        started = runtime.start_calibration(0).result(timeout=1)

        assert started.state.calibration.active is True
        assert runtime.playback.transition_active is False
        assert stripe.force_flush_count == flushes_before_calibration + 1
        np.testing.assert_array_equal(stripe.pixels()[0], [255, 255, 255, 255])
    finally:
        if started is not None and runtime.snapshot().calibration.active:
            runtime.finish_calibration(
                started.session_id,
                save=False,
            ).result(timeout=1)
        runtime.stop()


def test_set_solid_color_selects_solid_mode_and_publishes_color() -> None:
    stripe = TrackingStripe(4)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=4),
        controller_factory=lambda settings: stripe,
    )
    runtime.start()
    try:
        state = runtime.set_mode(PlaybackMode.SOLID, solid_color="#12aBcD").result(
            timeout=1
        )
        assert state.mode is PlaybackMode.SOLID
        assert state.solid_color == "#12ABCD"

        deadline = time.monotonic() + 1
        while stripe.pixels()[0].tolist() != [18, 171, 205, 255]:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert stripe.pixels().tolist() == [[18, 171, 205, 255]] * 4
    finally:
        runtime.stop()


def test_unknown_animation_rejects_command_without_stopping_runtime() -> None:
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=8))
    runtime.start()
    try:
        with pytest.raises(UnknownAnimationError, match="unknown animation"):
            runtime.select_animation("not-real").result(timeout=1)
        assert runtime.healthy is True
    finally:
        runtime.stop()


def test_custom_sequence_applies_to_rebuilt_independent_outputs_and_clears_on_deactivation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=4, settings_file=path, audio_source="demo")
    )
    runtime.start()
    try:
        runtime.set_mode(PlaybackMode.DYNAMIC).result(timeout=1)
        runtime.set_audio_sync_offset(-85.0).result(timeout=1)
        sequence = AnimationSequence(
            id="demo-sequence",
            name="Demo sequence",
            repeat=True,
            steps=(AnimationSequenceStep("rainbow_cycle", 2),),
        )
        runtime.create_animation_sequence(sequence).result(timeout=1)
        state = runtime.set_active_animation_sequence(sequence.id).result(timeout=1)

        assert state.mode is PlaybackMode.DYNAMIC
        assert state.sequence_library.active_sequence_id == sequence.id
        assert runtime.playback.config.animation_sequence == sequence

        topology = StripeTopologySettings(
            layout="independent",
            outputs=(
                StripeOutputSettings(id="left", name="Left", pixels=4),
                StripeOutputSettings(
                    id="right", name="Right", pixels=4, spi_device="/dev/spidev1.0"
                ),
            ),
        )
        rebuilt = runtime.apply_stripe_topology(topology).result(timeout=2)
        assert {item.stripe_id for item in rebuilt.stripe_playback} == {"left", "right"}
        assert all(
            item.sequence_playback.selected_sequence_id == sequence.id
            for item in rebuilt.stripe_playback
        )
        assert all(
            engine.config.animation_sequence == sequence
            and engine.sync_offset_s == pytest.approx(-0.085)
            for _, engine in runtime._playbacks.values()
        )

        deactivated = runtime.set_active_animation_sequence(None).result(timeout=1)
        assert deactivated.sequence_library.active_sequence_id is None
        assert runtime.playback.config.animation_sequence is None
        assert all(
            engine.config.animation_sequence is None
            for _, engine in runtime._playbacks.values()
        )
        assert deactivated.mode is PlaybackMode.DYNAMIC
    finally:
        runtime.stop()


def test_sequence_persistence_failure_keeps_selected_runtime_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=4, settings_file=tmp_path / "settings.json"))
    runtime.start()
    sequence = AnimationSequence(
        id="stable",
        name="Stable",
        repeat=True,
        steps=(AnimationSequenceStep("rainbow_cycle", 1),),
    )
    try:
        runtime.create_animation_sequence(sequence).result(timeout=1)

        def fail_save(*_args: object, **_kwargs: object) -> None:
            raise OSError("read-only settings file")

        monkeypatch.setattr(runtime._settings_store, "save_animation_sequences", fail_save)
        with pytest.raises(RuntimeCommandError, match="could not save animation sequences"):
            runtime.set_active_animation_sequence(sequence.id).result(timeout=1)

        assert runtime.playback.config.animation_sequence is None
        assert runtime.snapshot().sequence_library.active_sequence_id is None
        assert runtime.animation_sequences().active_sequence_id is None
    finally:
        runtime.stop()


def test_dynamic_mode_rejects_disabled_audio() -> None:
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=8, audio_source="off"))
    runtime.start()
    try:
        with pytest.raises(RuntimeCommandError, match="requires demo or microphone"):
            runtime.set_mode(PlaybackMode.DYNAMIC).result(timeout=1)
        state = runtime.snapshot()
        assert state.mode is PlaybackMode.STATIC
        assert state.diagnostic_issues[0].title == "Last command failed"
        assert "try the command again" in state.diagnostic_issues[0].action
    finally:
        runtime.stop()


def test_runtime_reports_controller_startup_failure() -> None:
    def fail_controller(settings: RuntimeSettings) -> Stripe:
        del settings
        raise RuntimeError("GPIO unavailable")

    runtime = LumiStripeRuntime(
        RuntimeSettings(hardware=True),
        controller_factory=fail_controller,
    )
    runtime.start()

    deadline = time.monotonic() + 1
    while runtime.snapshot().error is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert runtime.healthy is False
    assert runtime.snapshot().error == "GPIO unavailable"
    issue = runtime.snapshot().diagnostic_issues[0]
    assert issue.severity == "critical"
    assert "GPIO permissions" in issue.action
    runtime.stop()


def test_hardware_runtime_defaults_to_primary_spi_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "SPIStripe", FakeSPIStripe)

    controller = _default_controller_factory(RuntimeSettings(hardware=True, pixels=8))

    assert isinstance(controller, FakeSPIStripe)
    assert controller.config.device == "/dev/spidev0.0"
    assert controller.config.speed_hz == 1_000_000


def test_hardware_runtime_supports_mirrored_secondary_spi_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "SPIStripe", FakeSPIStripe)
    settings = RuntimeSettings(
        hardware=True,
        pixels=8,
        spi_device_2="/dev/spidev1.0",
        spi_speed_hz_2=500_000,
    )

    controller = _default_controller_factory(settings)

    assert isinstance(controller, MultiController)
    assert [child.config.device for child in controller.controllers] == [
        "/dev/spidev0.0",
        "/dev/spidev1.0",
    ]
    assert controller.controllers[1].config.speed_hz == 500_000


def test_default_hardware_runtime_owns_spi_on_output_thread_and_reconfigures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ThreadAwareSPIStripe.instances.clear()
    monkeypatch.setattr(runtime_module, "SPIStripe", ThreadAwareSPIStripe)
    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=6,
            audio_source="off",
            settings_file=tmp_path / "settings.json",
        )
    )
    runtime.start()
    try:
        output_thread = runtime._output_thread
        assert output_thread is not None
        first = ThreadAwareSPIStripe.instances[0]
        assert first.thread_ids == {output_thread._thread.ident}
        assert output_thread._thread.ident != runtime._thread.ident

        runtime.set_mode(PlaybackMode.SOLID, solid_color="#123456").result(timeout=1)
        deadline = time.monotonic() + 1
        while not first.frames or not np.all(first.frames[-1][:, :3] == (18, 52, 86)):
            assert time.monotonic() < deadline
            time.sleep(0.01)

        runtime.set_blackout(True).result(timeout=1)
        deadline = time.monotonic() + 1
        while first.frames[-1][:, :3].max() != 0:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        runtime.set_blackout(False).result(timeout=1)

        topology = StripeTopologySettings(
            layout="independent",
            outputs=(
                StripeOutputSettings(
                    id="replacement",
                    name="Replacement",
                    pixels=4,
                    spi_device="/dev/spidev1.0",
                ),
            ),
        )
        runtime.apply_stripe_topology(topology).result(timeout=2)
        second = ThreadAwareSPIStripe.instances[-1]
        assert second is not first
        assert first.close_count == 1
        assert second.thread_ids == {output_thread._thread.ident}
        deadline = time.monotonic() + 1
        while not second.frames:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        runtime.stop()

    assert first.close_count == 1
    assert second.close_count == 1
    assert first.thread_ids == {output_thread._thread.ident}
    assert second.thread_ids == {output_thread._thread.ident}


def test_injected_hardware_controller_is_owned_by_output_thread(
    tmp_path: Path,
) -> None:
    ThreadAwareSPIStripe.instances.clear()

    def build_controller(settings: RuntimeSettings) -> ThreadAwareSPIStripe:
        return ThreadAwareSPIStripe(
            SPIConfig(device=settings.spi_device, speed_hz=settings.spi_speed_hz),
            settings.pixels,
        )

    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=6,
            audio_source="off",
            settings_file=tmp_path / "settings.json",
        ),
        controller_factory=build_controller,
    )
    runtime.start()
    try:
        output_thread = runtime._output_thread
        assert output_thread is not None
        stripe = ThreadAwareSPIStripe.instances[0]
        assert stripe.thread_ids == {output_thread._thread.ident}
        assert output_thread._thread.ident != runtime._thread.ident

        runtime.set_mode(PlaybackMode.SOLID, solid_color="#123456").result(timeout=1)
        deadline = time.monotonic() + 1
        while not stripe.frames:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    finally:
        runtime.stop()

    assert stripe.close_count == 1
    assert stripe.thread_ids == {output_thread._thread.ident}


def test_hardware_exchange_preserves_color_correction_and_power_scaling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ThreadAwareSPIStripe.instances.clear()
    monkeypatch.setattr(runtime_module, "SPIStripe", ThreadAwareSPIStripe)
    settings_file = tmp_path / "settings.json"
    store = CalibrationSettingsStore(settings_file)
    store.save({"primary": ColorCorrection(red=128, green=64, blue=32)})
    store.save_stripes(
        StripeTopologySettings(
            layout="mirrored",
            outputs=(
                StripeOutputSettings(
                    id="primary",
                    name="Primary",
                    pixels=6,
                    power_limit_watts=0.01,
                ),
            ),
            power_budget_enabled=True,
            power_budget_watts=0.03,
        )
    )
    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=6,
            audio_source="off",
            settings_file=settings_file,
        )
    )
    runtime.start()
    try:
        runtime.set_mode(PlaybackMode.SOLID, solid_color="#FFFFFF").result(timeout=1)
        stripe = ThreadAwareSPIStripe.instances[0]
        deadline = time.monotonic() + 1
        while True:
            if stripe.frames:
                frame = stripe.frames[-1]
                state = runtime.snapshot()
                if (
                    np.all(frame[:, :3] == (128, 64, 32))
                    and np.all(frame[:, 3] < 255)
                    and state.power_budget.applied_scale < 1.0
                ):
                    break
            assert time.monotonic() < deadline
            time.sleep(0.01)

        frame = stripe.frames[-1]
        state = runtime.snapshot()
        np.testing.assert_array_equal(frame[:, :3], [[128, 64, 32]] * 6)
        assert state.power_budget.applied_scale < 1.0
        assert np.all(frame[:, 3] < 255)
    finally:
        runtime.stop()


def test_hardware_rendering_continues_while_output_is_blocked_and_watchdog_is_separate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ThreadAwareSPIStripe.instances.clear()
    StalledSPIStripe.entered.clear()
    StalledSPIStripe.release.clear()
    monkeypatch.setattr(runtime_module, "SPIStripe", StalledSPIStripe)
    monkeypatch.setattr(runtime_module, "OUTPUT_STALL_SECONDS", 0.2)
    exits: list[int] = []
    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=6,
            audio_source="off",
            settings_file=tmp_path / "settings.json",
        ),
        fatal_exit=exits.append,
    )
    runtime.start()
    try:
        assert StalledSPIStripe.entered.wait(1.0)
        with runtime._worker_state_lock:
            previous_render = runtime._last_step_completed_s
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with runtime._worker_state_lock:
                current_render = runtime._last_step_completed_s
            if current_render is not None and current_render != previous_render:
                break
            time.sleep(0.01)
        assert current_render != previous_render

        deadline = time.monotonic() + 2.0
        while not exits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert exits == [1]
    finally:
        StalledSPIStripe.release.set()
        runtime.stop()
    assert runtime.snapshot().error == "the LED output thread stopped making progress"


def test_topology_reconfiguration_does_not_pause_rendering_behind_spi(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ThreadAwareSPIStripe.instances.clear()
    StalledSPIStripe.entered.clear()
    StalledSPIStripe.release.clear()
    monkeypatch.setattr(runtime_module, "SPIStripe", StalledSPIStripe)
    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=6,
            audio_source="off",
            settings_file=tmp_path / "settings.json",
        )
    )
    runtime.start()
    try:
        assert StalledSPIStripe.entered.wait(1.0)
        topology = StripeTopologySettings(
            layout="independent",
            outputs=(
                StripeOutputSettings(
                    id="new",
                    name="New output",
                    pixels=4,
                    spi_device="/dev/spidev1.0",
                ),
            ),
        )
        command = runtime.apply_stripe_topology(topology)
        deadline = time.monotonic() + 1.0
        while runtime._pending_topology_change is None:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        with runtime._worker_state_lock:
            previous_render = runtime._last_step_completed_s
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with runtime._worker_state_lock:
                current_render = runtime._last_step_completed_s
            if current_render is not None and current_render != previous_render:
                break
            time.sleep(0.01)
        assert current_render != previous_render
        assert not command.done()

        StalledSPIStripe.release.set()
        command.result(timeout=2.0)
        assert runtime._topology == topology
    finally:
        StalledSPIStripe.release.set()
        runtime.stop()


def test_failed_hardware_reconfiguration_restores_previous_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ThreadAwareSPIStripe.instances.clear()
    monkeypatch.setattr(runtime_module, "SPIStripe", FailingReplacementSPIStripe)
    runtime = LumiStripeRuntime(
        RuntimeSettings(
            hardware=True,
            pixels=6,
            audio_source="off",
            settings_file=tmp_path / "settings.json",
        )
    )
    runtime.start()
    previous_topology = runtime._topology
    first = FailingReplacementSPIStripe.instances[0]
    try:
        replacement = StripeTopologySettings(
            layout="mirrored",
            outputs=(
                StripeOutputSettings(
                    id="broken",
                    name="Broken",
                    pixels=6,
                    spi_device="/dev/unavailable",
                ),
            ),
        )
        with pytest.raises(RuntimeCommandError, match="configuration was not applied"):
            runtime.apply_stripe_topology(replacement).result(timeout=2.0)

        assert runtime._topology == previous_topology
        assert runtime.healthy is True
        assert first.close_count == 1
        restored = FailingReplacementSPIStripe.instances[-1]
        assert restored is not first
        assert restored.config.device == previous_topology.outputs[0].spi_device
    finally:
        runtime.stop()


def test_runtime_settings_validate_secondary_spi_configuration() -> None:
    with pytest.raises(ValueError, match="secondary SPI speed requires"):
        RuntimeSettings(spi_speed_hz_2=500_000)


def test_calibration_isolates_output_and_cancel_restores_state(tmp_path: Path) -> None:
    primary = TrackingStripe(4)
    secondary = TrackingStripe(4)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=4, settings_file=tmp_path / "settings.json"),
        controller_factory=lambda settings: MultiController([primary, secondary]),
    )
    runtime.start()
    try:
        runtime.set_mode(PlaybackMode.SOLID, solid_color="#204060").result(timeout=1)
        runtime.set_brightness(0.5).result(timeout=1)
        started = runtime.start_calibration(0).result(timeout=1)

        assert started.state.calibration.active is True
        assert started.state.calibration.output_index == 0
        np.testing.assert_array_equal(primary.pixels()[0], [255, 255, 255, 255])
        np.testing.assert_array_equal(secondary.pixels()[0], [0, 0, 0, 255])

        updated = runtime.update_calibration(
            started.session_id,
            ColorCorrection(128, 200, 255),
            "red",
        ).result(timeout=1)
        assert updated.color_corrections[0].red == 128
        np.testing.assert_array_equal(primary.pixels()[0], [128, 0, 0, 255])

        with pytest.raises(RuntimeCommandError, match="finish or cancel"):
            runtime.set_brightness(0.8).result(timeout=1)

        restored = runtime.finish_calibration(
            started.session_id,
            save=False,
        ).result(timeout=1)
        assert restored.calibration.active is False
        assert restored.mode is PlaybackMode.SOLID
        assert restored.brightness == pytest.approx(0.5)
        assert restored.color_corrections[0].red == 255
        assert runtime.settings.settings_file.exists() is False
    finally:
        runtime.stop()


def test_calibration_save_persists_selected_profile_and_restores_blackout(
    tmp_path: Path,
) -> None:
    primary = TrackingStripe(2)
    secondary = TrackingStripe(2)
    settings_path = tmp_path / "settings.json"
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=2, settings_file=settings_path),
        controller_factory=lambda settings: MultiController([primary, secondary]),
    )
    runtime.start()
    try:
        runtime.set_blackout(True).result(timeout=1)
        started = runtime.start_calibration(1).result(timeout=1)
        assert started.state.blackout is False
        runtime.update_calibration(
            started.session_id,
            ColorCorrection(210, 220, 230),
            "blue",
        ).result(timeout=1)

        saved = runtime.finish_calibration(started.session_id, save=True).result(
            timeout=1
        )

        assert saved.blackout is True
        assert saved.color_corrections[1].red == 210
        encoded = settings_path.read_text(encoding="utf-8")
        assert '"secondary"' in encoded
        assert '"red": 210' in encoded
        np.testing.assert_array_equal(primary.pixels()[0], [0, 0, 0, 255])
        np.testing.assert_array_equal(secondary.pixels()[0], [0, 0, 0, 255])
    finally:
        runtime.stop()


def test_calibration_save_failure_keeps_session_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=2, settings_file=tmp_path / "settings.json")
    )
    runtime.start()
    try:
        started = runtime.start_calibration(0).result(timeout=1)

        def fail_save(profiles: object) -> None:
            del profiles
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr(runtime._settings_store, "save", fail_save)
        with pytest.raises(RuntimeCommandError, match="could not save"):
            runtime.finish_calibration(started.session_id, save=True).result(timeout=1)
        assert runtime.snapshot().calibration.active is True
        runtime.finish_calibration(started.session_id, save=False).result(timeout=1)
    finally:
        runtime.stop()


def test_abandoned_calibration_times_out_and_restores_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_module, "CALIBRATION_TIMEOUT_SECONDS", 0.01)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=2, settings_file=tmp_path / "settings.json")
    )
    runtime.start()
    try:
        runtime.start_calibration(0).result(timeout=1)
        deadline = time.monotonic() + 1
        while runtime.snapshot().calibration.active:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert runtime.snapshot().calibration.active is False
    finally:
        runtime.stop()


def test_invalid_calibration_settings_are_actionable(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("not-json", encoding="utf-8")
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=2, settings_file=path))
    runtime.start()
    try:
        issue = next(
            item
            for item in runtime.snapshot().diagnostic_issues
            if item.title == "Dashboard settings were not fully loaded"
        )
        assert "invalid" in issue.message.lower()
        assert "dashboard settings file" in issue.action
    finally:
        runtime.stop()


def test_saved_correction_is_loaded_and_applied_on_startup(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    CalibrationSettingsStore(path).save(
        {
            "primary": ColorCorrection(128, 192, 255),
            "secondary": ColorCorrection(),
        }
    )
    stripe = TrackingStripe(2)
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=2, settings_file=path),
        controller_factory=lambda settings: stripe,
    )
    runtime.start()
    try:
        state = runtime.set_mode(PlaybackMode.SOLID, solid_color="#FFFFFF").result(
            timeout=1
        )
        deadline = time.monotonic() + 1
        while stripe.pixels()[0].tolist() != [128, 192, 255, 255]:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert state.color_corrections[0].red == 128
    finally:
        runtime.stop()


def test_live_topology_supports_unequal_continuous_and_zero_outputs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=2, settings_file=path))
    runtime.start()
    try:
        topology = StripeTopologySettings(
            layout="continuous",
            outputs=(
                StripeOutputSettings(id="a", name="A", pixels=3),
                StripeOutputSettings(
                    id="b", name="B", pixels=5, spi_device="/dev/spidev1.0"
                ),
            ),
        )
        state = runtime.apply_stripe_topology(topology).result(timeout=1)
        assert state.stripe_topology.layout == "continuous"
        assert [item.pixels for item in state.stripe_topology.outputs] == [3, 5]

        empty = runtime.apply_stripe_topology(
            StripeTopologySettings(layout="independent")
        ).result(timeout=1)
        assert empty.running is True
        assert empty.stripe_topology.outputs == ()
        assert CalibrationSettingsStore(path).load_all()[
            0
        ].stripe_topology == StripeTopologySettings(layout="independent")
    finally:
        runtime.stop()


def test_independent_layout_accepts_targeted_playback_commands(tmp_path: Path) -> None:
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=2, settings_file=tmp_path / "settings.json")
    )
    runtime.start()
    try:
        topology = StripeTopologySettings(
            layout="independent",
            outputs=(
                StripeOutputSettings(id="a", name="A", pixels=2),
                StripeOutputSettings(
                    id="b", name="B", pixels=2, spi_device="/dev/spidev1.0"
                ),
            ),
        )
        runtime.apply_stripe_topology(topology).result(timeout=1)
        state = runtime.set_brightness(0.25, stripe_id="b").result(timeout=1)
        playback = {item.stripe_id: item for item in state.stripe_playback}
        assert playback["a"].brightness == 1.0
        assert playback["b"].brightness == 0.25

        target = next(
            item.name for item in runtime.animations() if item.name != playback["a"].animation
        )
        runtime.select_animation(target, stripe_id="a").result(timeout=1)
        assert runtime._playbacks["a"][1].transition_active is True
        assert runtime._playbacks["b"][1].transition_active is False
    finally:
        runtime.stop()


def test_invalid_saved_settings_block_updates_without_changing_runtime(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = b'{"version":999,"future_value":"keep"}'
    path.write_bytes(original)
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=2, settings_file=path))
    runtime.start()
    try:
        before = runtime.transition_settings()
        with pytest.raises(RuntimeCommandError, match="without losing data"):
            runtime.set_transition_settings(False, 900).result(timeout=1)
        assert runtime.transition_settings() == before
        assert path.read_bytes() == original
    finally:
        runtime.stop()


def test_transition_settings_apply_to_existing_and_future_independent_outputs(
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "settings.json"
    runtime = LumiStripeRuntime(RuntimeSettings(pixels=2, settings_file=settings_path))
    runtime.start()
    try:
        topology = StripeTopologySettings(
            layout="independent",
            outputs=(
                StripeOutputSettings(id="left", name="Left", pixels=2),
                StripeOutputSettings(
                    id="right", name="Right", pixels=2, spi_device="/dev/spidev1.0"
                ),
            ),
        )
        runtime.apply_stripe_topology(topology).result(timeout=1)
        runtime.set_sequencer_settings(SwitchQuantization.BEAT, 8).result(timeout=1)
        selected = {
            stripe_id: engine.player.current_index()
            for stripe_id, (_, engine) in runtime._playbacks.items()
        }
        coordinators = {
            stripe_id: (engine.beat_clock_tracker, engine._sequence_coordinator)
            for stripe_id, (_, engine) in runtime._playbacks.items()
        }

        state = runtime.set_transition_settings(
            True, 900, TransitionEffect.WIPE
        ).result(timeout=1)

        assert state.mode is PlaybackMode.STATIC
        assert state.transition_settings.enabled is True
        assert state.transition_settings.duration_ms == 900
        assert state.transition_settings.effect is TransitionEffect.WIPE
        assert runtime.playback.player.transition_ms == 900
        assert runtime.playback.player.transition_effect is TransitionEffect.WIPE
        assert runtime.playback.config.transition_effect is TransitionEffect.WIPE
        assert all(
            engine.player.transition_ms == 900
            for _, engine in runtime._playbacks.values()
        )
        assert all(
            engine.player.transition_effect is TransitionEffect.WIPE
            and engine.config.transition_effect is TransitionEffect.WIPE
            for _, engine in runtime._playbacks.values()
        )
        assert runtime.playback.config.switch_quantization is SwitchQuantization.BEAT
        assert all(
            engine.config.switch_quantization is SwitchQuantization.BEAT
            for _, engine in runtime._playbacks.values()
        )
        assert selected == {
            stripe_id: engine.player.current_index()
            for stripe_id, (_, engine) in runtime._playbacks.items()
        }
        assert coordinators == {
            stripe_id: (engine.beat_clock_tracker, engine._sequence_coordinator)
            for stripe_id, (_, engine) in runtime._playbacks.items()
        }

        updated_topology = StripeTopologySettings(
            layout="independent",
            outputs=(
                topology.outputs[0],
                StripeOutputSettings(
                    id="new-right", name="New Right", pixels=2,
                    spi_device="/dev/spidev1.0",
                ),
            ),
        )
        runtime.apply_stripe_topology(updated_topology).result(timeout=1)
        assert runtime._playbacks["new-right"][1].player.transition_ms == 900
        assert runtime._playbacks["new-right"][1].player.transition_effect is TransitionEffect.WIPE

        disabled = runtime.set_transition_settings(False, 900).result(timeout=1)
        assert disabled.transition_settings.enabled is False
        assert runtime.playback.player.transition_ms == 0
        assert all(
            engine.player.transition_ms == 0
            for _, engine in runtime._playbacks.values()
        )

        saved, warning = CalibrationSettingsStore(settings_path).load_all()
        assert warning is None
        assert saved.transition_settings == TransitionSettings(
            enabled=False, duration_ms=900, effect=TransitionEffect.WIPE
        )
    finally:
        runtime.stop()


def test_draft_stripe_test_temporarily_swaps_and_restores_topology(
    tmp_path: Path,
) -> None:
    runtime = LumiStripeRuntime(
        RuntimeSettings(pixels=2, settings_file=tmp_path / "settings.json")
    )
    runtime.start()
    try:
        original = runtime._topology
        draft = StripeTopologySettings(
            layout="mirrored",
            outputs=(StripeOutputSettings(id="draft", name="Draft", pixels=5),),
        )
        state = runtime.test_stripe("draft", "identify", draft).result(timeout=1)
        assert state.stripe_topology.outputs[0].id == "draft"

        assert runtime._stripe_test is not None
        runtime._stripe_test.expires_at = 0
        deadline = time.monotonic() + 1
        while runtime._topology != original:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert (
            CalibrationSettingsStore(tmp_path / "settings.json")
            .load_all()[0]
            .stripe_topology
            is None
        )
    finally:
        runtime.stop()
