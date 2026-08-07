from __future__ import annotations

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
from lumistripe_web.settings import AudioTuningProfile, CalibrationSettingsStore


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

        saved = runtime.finish_calibration(started.session_id, save=True).result(timeout=1)

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
