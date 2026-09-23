from __future__ import annotations

import threading
import time
from pathlib import Path

import lumistripe_web.runtime as runtime_module
import numpy as np
import pytest
from lumistripe import (
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
)
from lumistripe_web.runtime import (
    LumiStripeRuntime,
    OutputGateController,
    RuntimeCommandError,
    RuntimeSettings,
    UnknownAnimationError,
    _default_controller_factory,
)
from lumistripe_web.runtime.worker import _frame_deadline_missed, _FrameTimingWindow
from lumistripe_web.settings import (
    AudioTuningProfile,
    CalibrationSettingsStore,
    StartupPlaybackSettings,
    StripeOutputSettings,
    StripeTopologySettings,
)


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


class FailingStripe(TrackingStripe):
    def flush(self) -> None:
        raise RuntimeError("render failed")


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
            last_callback_age=0.0,
            last_frame_age=0.0,
            processor=AudioProcessorStats(input_rms=0.12, normalization_gain=1.4),
        )

    def device_name(self) -> str:
        return self.name

    def reconfigure(self, config) -> None:
        self.config = config

    def close(self) -> None:
        self.closed = True


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
        runtime._capture_preview_frame()

        assert runtime.preview_frame().outputs[0] == bytes((50, 110, 120, 255, 5, 20, 30, 255))

        runtime._output_gates[0].set_blackout(True)
        runtime._capture_preview_frame()
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
