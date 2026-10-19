"""Worker calibration lifecycle without a real capture device or wall-clock waits."""

import json
from dataclasses import replace
from pathlib import Path

import lumistripe_web.runtime as runtime_module
import pytest
from lumistripe.audio.types import (
    AudioFrame,
    AudioInputDevice,
    AudioInputHealth,
    AudioProcessorStats,
)
from lumistripe.playback import AudioSource
from lumistripe_web.runtime import (
    LumiStripeRuntime,
    RuntimeCommandError,
    RuntimeSettings,
)
from lumistripe_web.runtime.commands import _AudioCalibrationFinishCommand
from lumistripe_web.runtime.worker import BLUETOOTH_PROFILE_KEY, SPOTIFY_PROFILE_KEY
from lumistripe_web.settings import AudioTuningProfile, CalibrationSettingsStore


class Microphone:
    def __init__(self) -> None:
        self.config = None
        self.name = "PCM2902"

    def device_name(self) -> str:
        return self.name

    def reconfigure(self, config) -> None:
        self.config = config

    def close(self) -> None:
        pass


@pytest.fixture
def calibration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime_module, "list_input_device_details", lambda: [AudioInputDevice(1, "PCM2902")])
    mic = Microphone()
    runtime = LumiStripeRuntime(
        RuntimeSettings(audio_source="mic", audio_device="PCM2902", settings_file=tmp_path / "settings.json"),
        audio_factory=lambda device, config: mic,
    )
    runtime._open_microphone_monitor()
    clock = [100.0]
    monkeypatch.setattr("lumistripe_web.runtime.worker.time.monotonic", lambda: clock[0])
    runtime._audio_frame = AudioFrame(sequence=1, fresh=True)
    return runtime, mic, clock


def measure(runtime, clock, level: float) -> None:
    for _ in range(12):
        clock[0] += 0.25
        runtime._audio_frame = AudioFrame(sequence=runtime._audio_frame.sequence + 1, fresh=True)
        runtime._audio_health = AudioInputHealth(processor=AudioProcessorStats(input_rms=level, input_peak=level * 3))
        runtime._tick_audio_calibration()


def test_two_step_waits_for_user_and_persists_device_profile(calibration) -> None:
    runtime, mic, clock = calibration
    started = runtime._start_audio_calibration("PCM2902", 3, True)
    measure(runtime, clock, 0.002)
    waiting = runtime._publish_audio_calibration(started.session_id)
    assert waiting.phase == "waiting_music"
    assert waiting.status == "waiting"
    # Readers cannot mutate worker-owned state through the returned snapshot.
    waiting.phase = "music"
    assert runtime.audio_calibration_status(started.session_id).phase == "waiting_music"
    assert not runtime._audio_profiles
    runtime._begin_audio_calibration_music(started.session_id)
    measure(runtime, clock, 0.025)
    complete = runtime._publish_audio_calibration(started.session_id)
    assert complete.status == "complete"
    assert complete.result.measured_music_level == 0.025
    runtime._finish_audio_calibration(_AudioCalibrationFinishCommand(started.session_id, True))
    assert mic.config.microphone.enabled
    assert mic.config.microphone.input_noise_floor == pytest.approx(0.0022)
    saved, warning = CalibrationSettingsStore(runtime.settings.settings_file).load_all()
    assert warning is None
    assert saved.audio_profiles["PCM2902"].input_noise_floor == pytest.approx(0.0022)
    assert saved.audio_profiles["PCM2902"].hardware_gain_target is None


def test_duplicates_and_stalled_input_fail_and_can_be_discarded(calibration) -> None:
    runtime, _, clock = calibration
    started = runtime._start_audio_calibration("PCM2902", 3, True)
    for _ in range(12):
        clock[0] += 0.25
        runtime._tick_audio_calibration()
    failed = runtime._publish_audio_calibration(started.session_id)
    assert failed.status == "failed"
    assert "fresh input" in failed.error
    with pytest.raises(RuntimeCommandError):
        runtime._finish_audio_calibration(_AudioCalibrationFinishCommand(started.session_id, True))
    runtime._finish_audio_calibration(_AudioCalibrationFinishCommand(started.session_id, False))
    assert not runtime._audio_profiles


def test_cancel_during_capture_and_block_gain_changes(calibration) -> None:
    runtime, _, _ = calibration
    started = runtime._start_audio_calibration("PCM2902", 3, True)
    with pytest.raises(RuntimeCommandError, match="discard"):
        runtime._apply_audio_settings("PCM2902", AudioTuningProfile(hardware_gain_target=0.8))
    with pytest.raises(RuntimeCommandError, match="discard"):
        runtime._set_audio_source("off")
    runtime._finish_audio_calibration(_AudioCalibrationFinishCommand(started.session_id, False))
    assert runtime._audio_calibration is None


@pytest.mark.parametrize("problem", ["device", "stale", "noise", "clipping"])
def test_invalid_music_measurement_cannot_be_applied(calibration, problem: str) -> None:
    runtime, mic, clock = calibration
    started = runtime._start_audio_calibration("PCM2902", 3, True)
    measure(runtime, clock, 0.002)
    runtime._begin_audio_calibration_music(started.session_id)
    if problem == "device":
        mic.name = "other"
    measure(runtime, clock, 0.002 if problem == "noise" else 0.025)
    if problem == "stale":
        runtime._audio_frame = replace(runtime._audio_frame, fresh=False)
        runtime._tick_audio_calibration()
    if problem == "clipping":
        # Include clipping in a fresh measurement before completion.
        session = runtime._audio_calibration
        session.result = None
        session.music_samples[0] = replace(session.music_samples[0], clipping_fraction=0.02)
        runtime._tick_audio_calibration()
    assert runtime._publish_audio_calibration(started.session_id).status == "failed"
    with pytest.raises(RuntimeCommandError):
        runtime._finish_audio_calibration(_AudioCalibrationFinishCommand(started.session_id, True))


def test_legacy_single_stage_remains_available(calibration) -> None:
    runtime, _, clock = calibration
    started = runtime._start_audio_calibration("PCM2902", 3)
    measure(runtime, clock, 0.002)
    assert runtime._publish_audio_calibration(started.session_id).status == "complete"
    runtime._finish_audio_calibration(_AudioCalibrationFinishCommand(started.session_id, True))
    assert runtime._audio_profiles["PCM2902"].input_noise_floor == 0.003


@pytest.mark.parametrize("source,key", [(AudioSource.BLUETOOTH, BLUETOOTH_PROFILE_KEY), (AudioSource.SPOTIFY, SPOTIFY_PROFILE_KEY)])
def test_legacy_stream_calibration_uses_source_profile_identity(calibration, source, key) -> None:
    runtime, _, clock = calibration
    runtime._monitor_audio_source = source
    session = runtime._start_audio_calibration(source.value, 3)
    assert runtime._audio_calibration.device == key
    measure(runtime, clock, 0.02)
    assert runtime._publish_audio_calibration(session.session_id).status == "complete"


def test_conditioning_is_only_enabled_for_microphone() -> None:
    profile = AudioTuningProfile(input_noise_floor=0.001)
    assert profile.audio_config(microphone=True).microphone.enabled
    assert profile.audio_config(microphone=True).normalization.max_gain == 24
    assert not profile.audio_config().microphone.enabled
    assert profile.audio_config().normalization.max_gain == 5


def test_old_saved_profile_retains_tuning_with_explicit_input_floor_default(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = CalibrationSettingsStore(path)
    store.save_audio("PCM2902", {"PCM2902": AudioTuningProfile(target_level=0.5, noise_floor=0.02)})
    encoded = json.loads(path.read_text())
    del encoded["audio_profiles"]["PCM2902"]["input_noise_floor"]
    path.write_text(json.dumps(encoded))
    saved, warning = store.load_all()
    assert warning is None
    profile = saved.audio_profiles["PCM2902"]
    assert profile.input_noise_floor == 0.003
    assert profile.target_level == 0.5
    assert profile.noise_floor == 0.02
