from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import lumistripe_web.runtime.rust_engine as rust_engine_module
import lumistripe_web.runtime.worker as runtime_worker_module
import numpy as np
import pytest
from lumistripe import Stripe
from lumistripe.animation import AnimationPlayer
from lumistripe.animation.catalog import animation_catalog
from lumistripe.audio.beat import BeatClockTracker
from lumistripe.audio.config import AudioConfig
from lumistripe.audio.types import (
    AudioFrame,
    AudioProcessorStats,
    AudioSnapshot,
    BeatEvent,
    MusicFeatures,
)
from lumistripe.gpio.spi import _FrameEncoder, encode_legacy_frame
from lumistripe.playback import PlaybackMode, SwitchQuantization
from lumistripe.selector import DynamicSelector, DynamicSelectorConfig
from lumistripe_web.app import build_parser
from lumistripe_web.runtime import RuntimeSettings
from lumistripe_web.runtime.rust_engine import (
    EngineAudioStatus,
    RustAudioDevice,
    RustEngineError,
    RustEngineOutputThread,
    RustEngineProcess,
    RustOutputSpec,
    RustRenderAck,
    RustRenderAnimationSequence,
    RustRenderAnimationSequenceStep,
    RustRenderAudioInput,
    RustRenderBeatEvent,
    RustRenderCycling,
    RustRenderDynamicIdle,
    RustRenderDynamicSelector,
    RustRenderEffect,
    RustRenderMusicActivityConfig,
    RustRenderOutput,
    RustRenderSelectorFeatures,
    RustRenderStructureEvent,
    RustRenderTransition,
    _decode_response,
    _encode_request,
    audio_config_to_wire,
)
from lumistripe_web.runtime.worker import RuntimeWorker
from lumistripe_web.settings import (
    AudioTuningProfile,
    CalibrationSettingsStore,
    StartupPlaybackSettings,
    StripeOutputSettings,
    StripeTopologySettings,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_FIXTURES = REPOSITORY_ROOT / "rust" / "test-fixtures" / "protocol-v3"
PROTOCOL_V2_FIXTURES = REPOSITORY_ROOT / "rust" / "test-fixtures" / "protocol-v2"


def _engine_binary() -> Path:
    configured = os.environ.get("LUMISTRIPE_ENGINE_BINARY")
    binary = (
        Path(configured)
        if configured
        else REPOSITORY_ROOT / "rust" / "target" / "debug" / "lumistripe-engine"
    )
    if not binary.is_file():
        pytest.skip("build the lumistripe-engine binary to run the IPC integration test")
    return binary


def _mock_spec(output_id: str, pixel_count: int, *, reversed: bool = False) -> RustOutputSpec:
    return RustOutputSpec(output_id, pixel_count, reversed, "mock")


def test_runtime_defaults_to_python_and_rust_hardware_is_enabled_explicitly() -> None:
    assert RuntimeSettings().engine_backend == "python"
    assert RuntimeSettings().audio_backend == "python"
    assert RuntimeSettings(engine_backend="rust").engine_backend == "rust"
    assert RuntimeSettings(engine_backend="rust", hardware=True).hardware is True
    assert RuntimeSettings(engine_backend="rust", audio_backend="rust").audio_backend == "rust"
    with pytest.raises(ValueError, match="requires the Rust engine"):
        RuntimeSettings(audio_backend="rust")


def test_cli_defaults_to_python_and_accepts_rust_hardware() -> None:
    defaults = build_parser().parse_args([])
    assert defaults.engine_backend == "python"
    assert defaults.audio_backend == "python"
    assert defaults.engine_binary is None
    selected = build_parser().parse_args(
        [
            "--engine-backend",
            "rust",
            "--audio-backend",
            "rust",
            "--engine-binary",
            "/tmp/lumistripe-engine",
            "--hardware",
        ]
    )
    assert selected.engine_backend == "rust"
    assert selected.audio_backend == "rust"
    assert selected.engine_binary == Path("/tmp/lumistripe-engine")
    assert selected.hardware is True


def test_python_codec_matches_shared_v3_fixtures() -> None:
    request = json.loads(_encode_request("fixture-health-3", "health"))
    expected_request = json.loads(
        (PROTOCOL_FIXTURES / "health-request.json").read_text(encoding="utf-8")
    )
    assert request == expected_request

    response_bytes = (
        (PROTOCOL_FIXTURES / "health-response.json").read_bytes().rstrip(b"\n")
    )
    result = _decode_response(response_bytes, "fixture-health-3")
    assert result == {
        "type": "health",
        "status": "ready",
        "configured": False,
        "output_count": 0,
        "pixel_counts": [],
        "frames_rendered": 0,
        "last_sequence": None,
        "dropped_frames": 0,
        "failed_frames": 0,
        "last_error": None,
        "output_rate": 0.0,
        "frame_latency_ms": None,
        "queue_latency_ms": None,
        "transmission_ms": None,
        "frame_latency_p50_ms": None,
        "frame_latency_p95_ms": None,
        "frame_latency_p99_ms": None,
        "sequential_skew_ms": None,
        "last_successful_update_epoch_ms": None,
    }


def test_audio_config_serializes_to_flat_versioned_wire_shape() -> None:
    config = AudioConfig()
    wire = audio_config_to_wire(config)
    assert wire["microphone_enabled"] is False
    assert wire["input_noise_floor"] == config.microphone.input_noise_floor
    assert wire["high_pass_hz"] == config.microphone.high_pass_hz
    assert wire["noise_floor"] == config.smoothing.noise_floor
    assert wire["smoothing_enabled"] is True
    assert wire["target_level"] == config.normalization.target_level
    assert wire["normalization_enabled"] is True
    assert wire["drop_bass_threshold"] == config.analysis.drop_bass_threshold


def test_runtime_native_audio_uses_rust_capture_and_feeds_calibration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = RustAudioDevice(
        selector="pipewire:usb-mic",
        host="PipeWire",
        name="USB Mic",
        is_default=True,
        sample_rate=44_100,
        channels=1,
    )
    now = time.monotonic()
    now_ns = time.monotonic_ns()
    frame = {
        "rms": 0.24,
        "bands": [0.1] * 8,
        "beat": False,
        "beat_strength": 0.0,
        "sequence": 1,
        "timestamp": now,
        "fresh": True,
    }
    features = asdict(MusicFeatures(energy=0.3, volume=0.24, bands=(0.1,) * 8))
    features["bands"] = [0.1] * 8
    processor = asdict(AudioProcessorStats(input_rms=0.24, input_peak=0.31))
    status = EngineAudioStatus(
        active=True,
        device=device,
        frame=frame,
        features=features,
        structure={
            "phase": "groove",
            "confidence": 0.8,
            "buildup_progress": 0.0,
            "drop_event": False,
        },
        processor=processor,
        capture={
            "callback_count": 1,
            "callback_errors": 0,
            "overflowed_batches": 0,
            "dropped_samples": 0,
            "last_callback_ns": now_ns,
            "last_error_ns": None,
        },
        last_frame_ns=now_ns,
        last_error=None,
        audio_update_sequence=1,
        dropped_audio_updates=0,
        dropped_structure_events=0,
    )

    class FakeRustAudioThread(RustEngineOutputThread):
        def __init__(self) -> None:
            super().__init__((), ())
            self.started: tuple[str | None, AudioConfig] | None = None
            self.configured: AudioConfig | None = None

        def audio_devices(self) -> tuple[RustAudioDevice, ...]:
            return (device,)

        def start_audio(self, selector: str | None, config: AudioConfig) -> bool:
            self.started = (selector, config)
            return True

        def audio_status(self) -> EngineAudioStatus:
            return status

        def configure_audio(self, config: AudioConfig) -> bool:
            self.configured = config
            return True

    monkeypatch.setattr(
        runtime_worker_module,
        "HardwareGainController",
        lambda name: SimpleNamespace(
            status=SimpleNamespace(
                supported=False,
                writable=False,
                backend=None,
                control=None,
                value=None,
                error=None,
            ),
            set_normalized=lambda value: None,
        ),
    )
    audio_thread = FakeRustAudioThread()
    worker = RuntimeWorker(
        RuntimeSettings(
            engine_backend="rust",
            audio_backend="rust",
            audio_source="mic",
            settings_file=tmp_path / "settings.json",
        ),
        audio_factory=lambda *_: pytest.fail("Rust audio must not open Python capture"),
    )
    worker._output_thread = audio_thread
    worker._selected_audio_device = "USB Mic"

    worker._open_microphone_monitor()

    assert audio_thread.started is not None
    assert audio_thread.started[0] == "pipewire:usb-mic"
    assert worker._audio_input is None
    assert worker._audio_monitoring_active() is True
    assert worker._audio_frame.fresh is True
    assert worker._music_features.energy == pytest.approx(0.3)
    assert worker._rust_music_structure_state.phase.value == "groove"
    audio_settings = worker.audio_settings()
    assert audio_settings.monitoring is True
    assert audio_settings.devices[0].selector == "pipewire:usb-mic"
    worker._apply_audio_settings(
        "pipewire:usb-mic", AudioTuningProfile(target_level=0.5)
    )
    assert audio_thread.configured is not None
    assert audio_thread.configured.microphone.enabled is True
    calibration = worker._start_audio_calibration("pipewire:usb-mic", 0.5)
    assert calibration.status == "capturing"


def test_python_and_native_c_encoders_match_shared_sm16716_golden_frame() -> None:
    pixels = np.array([[255, 0, 16, 127], [2, 128, 64, 255]], dtype=np.uint8)
    expected_hex = (PROTOCOL_V2_FIXTURES / "sm16716-reference.hex").read_text(
        encoding="ascii"
    ).strip()

    assert encode_legacy_frame(pixels).tobytes().hex() == expected_hex
    assert _FrameEncoder(len(pixels)).encode(pixels).tobytes().hex() == expected_hex


def test_one_rust_process_configures_and_transmits_all_outputs() -> None:
    process = RustEngineProcess(binary_path=_engine_binary())
    connection = None
    try:
        specs = (
            RustOutputSpec("main", 3, False, "mock_spi"),
            _mock_spec("secondary", 2, reversed=True),
        )
        process.configure(specs)
        connection = process.open_frame_stream()
        frames = (
            np.array(
                [[1, 2, 3, 255], [4, 5, 6, 255], [7, 8, 9, 255]],
                dtype=np.uint8,
            ),
            np.array([[10, 11, 12, 255], [13, 14, 15, 255]], dtype=np.uint8),
        )
        dropped = process.send_frame_set(connection, 17, frames)
        assert dropped >= 0

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            health = process.health()
            if health.frames_rendered >= 1:
                break
            time.sleep(0.005)
        assert health.configured is True
        assert health.output_count == 2
        assert health.pixel_counts == (3, 2)
        assert health.frames_rendered == 1
        assert health.last_sequence == 17
        assert health.failed_frames == 0
    finally:
        if connection is not None:
            connection.close()
        process.close()
    assert process.is_running is False


def test_rust_process_audio_controls_report_devices_status_and_stop() -> None:
    process = RustEngineProcess(binary_path=_engine_binary())
    try:
        devices = process.audio_devices()
        assert all(device.selector and device.name for device in devices)
        status = process.audio_status()
        assert status.active is False
        assert status.device is None
        assert status.frame["fresh"] is False

        with pytest.raises(RustEngineError, match="audio_failure"):
            process.start_audio("lumistripe-no-such-input-device", AudioConfig())
        failed_status = process.audio_status()
        assert failed_status.active is False
        assert failed_status.last_error is not None
        recovery_deadline = time.monotonic() + 3.0
        while time.monotonic() < recovery_deadline:
            failed_status = process.audio_status()
            if failed_status.last_error is not None and failed_status.last_error.startswith(
                "audio device recovery failed:"
            ):
                break
            time.sleep(0.05)
        assert failed_status.active is False
        assert failed_status.last_error is not None
        assert failed_status.last_error.startswith("audio device recovery failed:")
        process.stop_audio()
        assert process.audio_status().last_error is None
    finally:
        process.close()


def test_rust_process_renders_typed_scene_inputs_without_pixel_frames() -> None:
    process = RustEngineProcess(binary_path=_engine_binary())
    try:
        assert process.render_capabilities() == (
            "rainbow",
            "rainbow_cycle",
            "theater_chase",
            "strobe",
            "pulse",
            "police",
            "bpm",
            "wave",
            "sinelon",
            "bouncing_ball",
            "color_wipe",
            "juggle",
            "comet",
            "aurora",
            "fire",
            "twinkle",
            "rainbow_strobe",
            "laser_sweep",
            "disco_sparkle",
            "dual_comet",
            "dance_floor",
            "red_rave_sweep",
            "red_rave_chase",
            "red_blackout_strobe",
            "glow_rush",
            "rave_pulse",
            "rave_scanner",
            "neon_confetti",
            "strobe_chase",
            "plasma_rave",
            "dual_laser",
            "disco_comet",
            "neon_storm",
            "peak_mirror",
            "shockwave",
            "center_burst",
            "mirror_flash",
            "spectrum_flash",
            "club_flash",
            "color_burst",
            "drop_explosion",
            "drop_wave",
            "bass_drop",
            "beat_explosion",
            "beat_ripple",
            "beat_tunnel",
            "beat_wave",
            "hard_beat",
            "confetti",
            "comet_storm",
            "electric_storm",
            "firework_burst",
            "lightning_strike",
            "pixel_explosion",
        )
        process.configure((_mock_spec("main", 64), _mock_spec("secondary", 32)))
        with pytest.raises(RustEngineError, match="invalid_render"):
            process.render_frame(
                sequence=22,
                timestamp_ns=1_234_000_000,
                delta_ns=16_000_000,
                brightness=0.8,
                outputs=(
                    RustRenderOutput("not_ported", {}),
                    RustRenderOutput("strobe", {}),
                ),
            )
        with pytest.raises(RustEngineError, match="invalid_render"):
            process.render_frame(
                sequence=23,
                timestamp_ns=1_250_000_000,
                delta_ns=16_000_000,
                brightness=0.8,
                outputs=(
                    RustRenderOutput("rainbow", {}),
                    RustRenderOutput("strobe", {"flash_rate": 999}),
                ),
            )
        invalid_effect_ack = process.render_frame(
            sequence=24,
            timestamp_ns=1_266_000_000,
            delta_ns=16_000_000,
            brightness=0.8,
            outputs=(
                RustRenderOutput(
                    "rainbow",
                    {},
                    effects=(RustRenderEffect("unknown_effect", {}),),
                ),
                RustRenderOutput("strobe", {"flash_rate": 72}),
            ),
        )
        assert invalid_effect_ack.sequence == 24
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            health = process.health()
            if health.failed_frames >= 1:
                break
            time.sleep(0.005)
        assert health.failed_frames == 1
        assert health.frames_rendered == 0

        acknowledgement = process.render_frame(
            sequence=25,
            timestamp_ns=1_282_000_000,
            delta_ns=16_000_000,
            brightness=0.8,
            outputs=(
                RustRenderOutput("rainbow", {"palette": "neon"}),
                RustRenderOutput(
                    "strobe",
                    {"flash_rate": 72},
                    effects=(RustRenderEffect("hard_beat", {}, 1.0),),
                    base_intensity=0.5,
                    max_overlay_strength=0.8,
                ),
            ),
            audio=RustRenderAudioInput(
                rms=0.4,
                bands=(0.8, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.1),
                beat=True,
                beat_strength=0.9,
            ),
            power_limiting_enabled=True,
            power_budget_watts=12.0,
        )
        assert acknowledgement.sequence == 25
        assert acknowledgement.replaced is False
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            health = process.health()
            if health.frames_rendered >= 1:
                break
            time.sleep(0.005)
        assert health.frames_rendered == 1
        assert health.last_sequence == 25
        assert health.failed_frames == 1
        assert health.render_time_ms is not None and health.render_time_ms >= 0.0
        assert health.estimated_power_watts is not None
        assert health.applied_power_scale is not None
        assert 0.0 <= health.applied_power_scale <= 1.0
        assert health.output_power_watts is not None
        assert len(health.output_power_watts) == 2

        transition_ack = process.render_frame(
            sequence=26,
            timestamp_ns=1_298_000_000,
            delta_ns=16_000_000,
            brightness=0.8,
            outputs=(
                RustRenderOutput(
                    "strobe",
                    {"flash_rate": 72},
                    zone_count=2,
                    zone_pattern="alternate",
                    transition=RustRenderTransition(
                        1_282_000_000, 100_000_000, "crossfade"
                    ),
                    blackout=True,
                ),
                RustRenderOutput("strobe", {"flash_rate": 72}),
            ),
        )
        assert transition_ack.sequence == 26
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            health = process.health()
            if health.frames_rendered >= 2:
                break
            time.sleep(0.005)
        assert health.frames_rendered == 2
        assert health.last_sequence == 26
    finally:
        process.close()


def test_rust_process_reports_sequence_state_waiting_for_a_confident_clock() -> None:
    process = RustEngineProcess(binary_path=_engine_binary())
    try:
        process.configure((_mock_spec("main", 3),))
        process.render_frame(
            sequence=1,
            timestamp_ns=time.monotonic_ns(),
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(
                RustRenderOutput(
                    "pulse",
                    {},
                    cycling=RustRenderCycling(
                        order="sequential",
                        timing="fixed",
                        interval_ns=1,
                        seed=7,
                        choices=(("pulse", {}), ("comet", {})),
                        transition_duration_ns=100_000_000,
                        transition_mode="crossfade",
                        selector=RustRenderDynamicSelector(
                            min_duration_ns=0,
                            max_duration_ns=1_000_000_000,
                            switch_cooldown_ns=0,
                            drop_cooldown_ns=0,
                            randomness=0.0,
                            history_size=0,
                            seed=7,
                            switch_margin=0.0,
                        ),
                        sequence=RustRenderAnimationSequence(
                            id="night-drive",
                            repeat=True,
                            steps=(RustRenderAnimationSequenceStep("pulse", 1),),
                        ),
                    ),
                ),
            ),
            audio=RustRenderAudioInput(
                rms=0.0,
                bands=(0.0,) * 8,
                beat=False,
                beat_strength=0.0,
                feature_sequence=1,
                fallback_bpm=120.0,
            ),
            scheduler_fps=60,
        )
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            health = process.health()
            if health.sequence_playback:
                break
            time.sleep(0.005)
        assert len(health.sequence_playback) == 1
        state = health.sequence_playback[0]
        assert state.sequence_id == "night-drive"
        assert state.waiting_for_clock is True
        assert state.active is False
        assert state.current_step_index is None
    finally:
        process.close()


def test_rust_render_audio_input_requires_finite_eight_band_values() -> None:
    with pytest.raises(ValueError, match="eight bands"):
        RustRenderAudioInput(0.0, (0.0,) * 7, False, 0.0).to_wire()  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        RustRenderAudioInput(
            0.0,
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, float("nan")),
            False,
            0.0,
        ).to_wire()


def test_rust_render_audio_input_serializes_monotonic_beat_events() -> None:
    audio = RustRenderAudioInput(
        rms=0.2,
        bands=(0.1,) * 8,
        beat=False,
        beat_strength=0.0,
        feature_timestamp_ns=1_200_000_000,
        feature_sequence=43,
        fallback_bpm=128.0,
        beat_events=(RustRenderBeatEvent(42, 1_234_000_000),),
    )

    assert audio.to_wire() == {
        "rms": 0.2,
        "bands": [0.1] * 8,
        "beat": False,
        "beat_strength": 0.0,
        "feature_timestamp_ns": 1_200_000_000,
        "feature_sequence": 43,
        "fallback_bpm": 128.0,
        "beat_events": [
            {"sequence": 42, "timestamp_ns": 1_234_000_000, "strength": 0.0}
        ],
    }


def test_rust_audio_input_preserves_capture_timestamp() -> None:
    snapshot = AudioSnapshot(frame=AudioFrame(timestamp=12.5))

    audio = RuntimeWorker._rust_audio_input(snapshot)

    assert audio is not None
    assert audio.feature_timestamp_ns == 12_500_000_000


def test_rust_render_audio_input_serializes_structure_event_edges() -> None:
    event = RustRenderStructureEvent(
        output_index=1,
        sequence=3,
        timestamp_ns=2_500_000_000,
        phase="drop",
        confidence=0.9,
        buildup_progress=0.7,
        drop_event=True,
        section_change=True,
    )
    audio = RustRenderAudioInput(
        rms=0.2,
        bands=(0.1,) * 8,
        beat=False,
        beat_strength=0.0,
        structure_events=(event,),
    )

    assert audio.to_wire()["structure_events"] == [
        {
            "output_index": 1,
            "sequence": 3,
            "timestamp_ns": 2_500_000_000,
            "phase": "drop",
            "confidence": 0.9,
            "buildup_progress": 0.7,
            "drop_event": True,
            "section_change": True,
        }
    ]
    with pytest.raises(ValueError, match="one-shot edge"):
        RustRenderStructureEvent(
            output_index=0,
            sequence=1,
            timestamp_ns=0,
            phase="groove",
            confidence=0.0,
            buildup_progress=0.0,
        ).to_wire()


def test_worker_replays_beat_events_until_rust_acknowledges_them() -> None:
    worker = RuntimeWorker.__new__(RuntimeWorker)
    worker._rust_pending_beat_events = deque()
    worker._rust_dropped_beat_events = 0
    worker._rust_pending_structure_events = {}
    worker._rust_last_audio_update_retry_at_s = 1.0
    snapshot = AudioSnapshot.from_parts(
        AudioFrame(),
        MusicFeatures(),
        beat_events=(BeatEvent(7, 1.7, 0.8), BeatEvent(5, 1.2, 0.7)),
    )

    worker._remember_rust_beat_events(snapshot)
    worker._remember_rust_beat_events(snapshot)
    replay = tuple(worker._rust_pending_beat_events)
    audio = RuntimeWorker._rust_audio_input(
        snapshot,
        feature_sequence=3,
        beat_events=replay,
    )

    assert audio is not None
    assert [event.sequence for event in audio.beat_events] == [5, 7]
    assert [event.sequence for event in replay] == [5, 7]
    worker._acknowledge_rust_beat_events(5)
    assert [event.sequence for event in worker._rust_pending_beat_events] == [7]
    worker._acknowledge_rust_beat_events(7)
    assert not worker._rust_pending_beat_events
    assert worker._rust_last_audio_update_retry_at_s is None


def test_worker_replays_structure_edges_per_dynamic_output_until_acknowledged() -> None:
    worker = RuntimeWorker.__new__(RuntimeWorker)
    worker._rust_pending_beat_events = deque()
    worker._rust_pending_structure_events = {}
    worker._rust_structure_source_sequences = {}
    worker._rust_structure_event_sequences = {}
    worker._rust_last_audio_update_retry_at_s = 1.0
    structure = SimpleNamespace(
        phase=SimpleNamespace(value="drop"),
        confidence=0.9,
        buildup_progress=0.7,
        drop_event=True,
    )
    outputs = (
        StripeOutputSettings(id="left", name="Left", pixels=8),
        StripeOutputSettings(id="right", name="Right", pixels=8),
    )
    engines = (
        SimpleNamespace(mode=PlaybackMode.STATIC),
        SimpleNamespace(mode=PlaybackMode.DYNAMIC, music_structure_state=structure),
    )
    snapshot = AudioSnapshot.from_parts(
        AudioFrame(sequence=10, timestamp=2.5, fresh=True),
        MusicFeatures(section_change=True),
    )

    first = worker._remember_rust_structure_events(snapshot, outputs, engines)
    duplicate = worker._remember_rust_structure_events(snapshot, outputs, engines)

    assert first is not None
    assert duplicate == first
    assert len(first) == 1
    assert first[0].output_index == 1
    assert first[0].sequence == 1
    assert first[0].drop_event is True
    assert first[0].section_change is True

    worker._acknowledge_rust_structure_events("right", 1)
    assert not worker._rust_pending_structure_events["right"]
    assert worker._remember_rust_structure_events(snapshot, outputs, engines) == ()


def test_worker_bounds_unacknowledged_beat_replay_and_counts_drops() -> None:
    worker = RuntimeWorker.__new__(RuntimeWorker)
    worker._rust_pending_beat_events = deque()
    worker._rust_dropped_beat_events = 0
    worker._rust_last_audio_update_retry_at_s = None
    events = tuple(
        BeatEvent(sequence, float(sequence), 0.5)
        for sequence in range(1, 260)
    )
    snapshot = AudioSnapshot.from_parts(
        AudioFrame(), MusicFeatures(), beat_events=events
    )

    worker._remember_rust_beat_events(snapshot)

    assert len(worker._rust_pending_beat_events) == 256
    assert worker._rust_pending_beat_events[0].sequence == 4
    assert worker._rust_dropped_beat_events == 3


def test_rust_render_output_validates_and_encodes_effect_layers() -> None:
    output = RustRenderOutput(
        "rainbow",
        {"palette": "neon"},
        effects=(
            RustRenderEffect(
                "hard_beat", {}, 0.75, trigger_beat=True, trigger_beat_strength=0.9
            ),
        ),
        base_intensity=0.4,
        max_overlay_strength=0.6,
    )
    assert output.to_wire() == {
        "animation": "rainbow",
        "settings": {"palette": "neon"},
        "effects": [
            {
                "name": "hard_beat",
                "settings": {},
                "intensity": 0.75,
                "trigger_beat": True,
                "trigger_beat_strength": 0.9,
            }
        ],
        "base_intensity": 0.4,
        "max_overlay_strength": 0.6,
    }
    zoned = RustRenderOutput("rainbow", {}, zone_count=3, zone_pattern="alternate")
    assert zoned.to_wire() == {
        "animation": "rainbow",
        "settings": {},
        "zone_count": 3,
        "zone_pattern": "alternate",
    }
    transitioned = RustRenderOutput(
        "rainbow",
        {},
        transition=RustRenderTransition(1_000_000_000, 250_000_000, "wipe"),
        blackout=True,
    )
    assert transitioned.to_wire() == {
        "animation": "rainbow",
        "settings": {},
        "transition": {
            "started_at_ns": 1_000_000_000,
            "duration_ns": 250_000_000,
            "mode": "wipe",
        },
        "blackout": True,
    }
    cycling = RustRenderOutput(
        "rainbow",
        {},
        cycling=RustRenderCycling(
            order="sequential",
            timing="per_animation",
            interval_ns=30_000_000_000,
            seed=7,
            choices=(("rainbow", {}), ("rainbow_cycle", {"speed": 2})),
            transition_duration_ns=200_000_000,
            transition_mode="wipe",
        ),
    )
    assert cycling.to_wire()["cycling"] == {
        "order": "sequential",
        "timing": "per_animation",
        "interval_ns": 30_000_000_000,
        "seed": 7,
        "choices": [
            {"animation": "rainbow", "settings": {}},
            {"animation": "rainbow_cycle", "settings": {"speed": 2}},
        ],
        "transition_duration_ns": 200_000_000,
        "transition_mode": "wipe",
    }
    sequence = RustRenderCycling(
        order="sequential",
        timing="fixed",
        interval_ns=1,
        seed=7,
        choices=(("pulse", {}), ("comet", {})),
        transition_duration_ns=100_000_000,
        transition_mode="fade_black",
        selector=RustRenderDynamicSelector(
            min_duration_ns=0,
            max_duration_ns=1_000_000_000,
            switch_cooldown_ns=0,
            drop_cooldown_ns=0,
            randomness=0.0,
            history_size=0,
            seed=7,
            switch_margin=0.0,
        ),
        sequence=RustRenderAnimationSequence(
            id="night-drive",
            repeat=True,
            steps=(
                RustRenderAnimationSequenceStep("pulse", 2),
                RustRenderAnimationSequenceStep("comet", 1),
            ),
        ),
    )
    assert sequence.to_wire() == {
        "order": "sequential",
        "timing": "fixed",
        "interval_ns": 1,
        "seed": 7,
        "choices": [
            {"animation": "pulse", "settings": {}},
            {"animation": "comet", "settings": {}},
        ],
        "transition_duration_ns": 100_000_000,
        "transition_mode": "fade_black",
        "selector": {
            "min_duration_ns": 0,
            "max_duration_ns": 1_000_000_000,
            "switch_cooldown_ns": 0,
            "drop_cooldown_ns": 0,
            "randomness": 0.0,
            "history_size": 0,
            "seed": 7,
            "switch_margin": 0.0,
            "switch_quantization": "off",
            "bars_per_phrase": 8,
            "clock_confidence_threshold": 0.65,
            "subdivisions_per_beat": 1,
            "sync_offset_ns": 0,
        },
        "sequence": {
            "id": "night-drive",
            "repeat": True,
            "steps": [
                {"animation": "pulse", "duration_bars": 2},
                {"animation": "comet", "duration_bars": 1},
            ],
        },
    }
    calibrated = RustRenderOutput(
        "rainbow",
        {},
        color_correction=(128, 255, 64),
        voltage_v=12.0,
        full_white_current_a=0.08,
        power_limit_watts=4.5,
    )
    assert calibrated.to_wire() == {
        "animation": "rainbow",
        "settings": {},
        "color_correction": [128, 255, 64],
        "voltage_v": 12.0,
        "full_white_current_a": 0.08,
        "power_limit_watts": 4.5,
    }
    scene = RustRenderOutput(
        "rainbow", {}, scene_revision=12, brightness=0.4
    )
    assert scene.to_wire() == {
        "animation": "rainbow",
        "settings": {},
        "scene_revision": 12,
        "brightness": 0.4,
    }
    scene_slice = RustRenderOutput(
        "rainbow", {}, scene_pixel_count=5, scene_offset=2
    )
    assert scene_slice.to_wire() == {
        "animation": "rainbow",
        "settings": {},
        "scene_pixel_count": 5,
        "scene_offset": 2,
    }
    activity = RustRenderMusicActivityConfig(
        idle_enter_frames=60,
        activation_delay_ns=0,
        feature_attack=0.28,
        feature_release=0.08,
        energy_threshold=0.03,
        onset_threshold=0.025,
        beat_density_threshold=0.05,
        brightness_threshold=0.08,
        spectral_balance_ratio=0.35,
        activation_hysteresis_ratio=1.35,
        beat_density_window_ns=2_000_000_000,
    )
    idle = RustRenderDynamicIdle("off", None, {}, (1, 2, 3), 0.5)
    dynamic_scene = RustRenderOutput(
        "rainbow",
        {},
        audio_enabled=False,
        cycling=RustRenderCycling(
            order="sequential",
            timing="fixed",
            interval_ns=1,
            seed=7,
            choices=(("rainbow", {}),),
            transition_duration_ns=0,
            transition_mode="crossfade",
            selector=RustRenderDynamicSelector(
                min_duration_ns=0,
                max_duration_ns=1_000_000_000,
                switch_cooldown_ns=0,
                drop_cooldown_ns=0,
                randomness=0.0,
                history_size=0,
                seed=7,
                switch_margin=0.0,
                switch_quantization="phrase",
                bars_per_phrase=16,
                clock_confidence_threshold=0.72,
                subdivisions_per_beat=4,
                sync_offset_ns=-125_000_000,
                activity=activity,
            ),
            dynamic_idle=idle,
        ),
    ).to_wire()
    assert dynamic_scene["audio_enabled"] is False
    assert dynamic_scene["cycling"]["selector"]["switch_quantization"] == "phrase"
    assert dynamic_scene["cycling"]["selector"]["bars_per_phrase"] == 16
    assert dynamic_scene["cycling"]["selector"]["clock_confidence_threshold"] == 0.72
    assert dynamic_scene["cycling"]["selector"]["subdivisions_per_beat"] == 4
    assert dynamic_scene["cycling"]["selector"]["sync_offset_ns"] == -125_000_000
    assert dynamic_scene["cycling"]["dynamic_idle"] == {
        "mode": "off",
        "animation": None,
        "settings": {},
        "color": [1, 2, 3],
        "brightness": 0.5,
    }
    resampled_scene = RustRenderOutput(
        "rainbow", {}, scene_pixel_count=5, scene_resample=True
    )
    assert resampled_scene.to_wire() == {
        "animation": "rainbow",
        "settings": {},
        "scene_pixel_count": 5,
        "scene_resample": True,
    }
    solid_scene = RustRenderOutput("rainbow", {}, solid_color=(128, 64, 32))
    assert solid_scene.to_wire() == {
        "animation": "rainbow",
        "settings": {},
        "solid_color": [128, 64, 32],
    }
    with pytest.raises(ValueError, match="positive"):
        RustRenderOutput("rainbow", {}, zone_count=0).to_wire()
    with pytest.raises(ValueError, match="pattern"):
        RustRenderOutput("rainbow", {}, zone_pattern="zigzag").to_wire()
    with pytest.raises(ValueError, match="duration"):
        RustRenderTransition(1, 0).to_wire()
    with pytest.raises(ValueError, match="inside its scene canvas"):
        RustRenderOutput("rainbow", {}, scene_pixel_count=5, scene_offset=5).to_wire()
    with pytest.raises(ValueError, match="whole scene canvas"):
        RustRenderOutput("rainbow", {}, scene_resample=True).to_wire()
    with pytest.raises(ValueError, match="solid scenes"):
        RustRenderOutput(
            "rainbow",
            {},
            effects=(RustRenderEffect("hard_beat", {}),),
            solid_color=(128, 64, 32),
        ).to_wire()
    with pytest.raises(ValueError, match="intensity"):
        RustRenderEffect("hard_beat", {}, 1.1).to_wire()
    with pytest.raises(ValueError, match="correction"):
        RustRenderOutput("rainbow", {}, color_correction=(255, 256, 255)).to_wire()
    with pytest.raises(ValueError, match="power limit"):
        RustRenderOutput("rainbow", {}, power_limit_watts=0.0).to_wire()
    with pytest.raises(ValueError, match="at most two"):
        RustRenderOutput(
            "rainbow",
            {},
            effects=(
                RustRenderEffect("hard_beat", {}),
                RustRenderEffect("beat_ripple", {}),
                RustRenderEffect("club_flash", {}),
            ),
        ).to_wire()


def test_rust_output_thread_uses_bounded_exchange_and_reports_health() -> None:
    output = RustEngineOutputThread(
        (2, 1),
        (_mock_spec("main", 2), _mock_spec("secondary", 1)),
        binary_path=_engine_binary(),
    )
    output.start()
    try:
        assert output.process_is_running is True
        process = output._process
        assert process is not None and process._process is not None
        process_id = process._process.pid
        for sequence in range(8):
            assert output.exchange is not None
            output.exchange.submit(
                (
                    np.full((2, 4), sequence, dtype=np.uint8),
                    np.full((1, 4), sequence, dtype=np.uint8),
                )
            )
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and output.stats().transmitted_frames == 0:
            time.sleep(0.01)
        stats = output.stats()
        assert stats.transmitted_frames >= 1
        assert stats.failed_flushes == 0
        assert stats.queue_latency_ms is not None
        assert stats.transmission_ms is not None
        assert stats.frame_latency_p50_ms is not None
        assert stats.frame_latency_p95_ms is not None
        assert stats.frame_latency_p99_ms is not None
        assert stats.ipc_latency_ms is not None
        assert stats.sequential_skew_ms is not None
        assert output.is_healthy(max_heartbeat_age_seconds=1.0)

        frames_before_reconfigure = output.stats().transmitted_frames
        exchange = output.reconfigure((1,), (_mock_spec("replacement", 1),))
        assert exchange is not None
        assert process.is_running is True
        assert process._process is not None and process._process.pid == process_id
        exchange.submit((np.full((1, 4), 42, dtype=np.uint8),))
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            stats = output.stats()
            if stats.transmitted_frames > frames_before_reconfigure:
                break
            time.sleep(0.01)
        assert output.stats().failed_flushes == 0
    finally:
        output.stop()
    assert output.process_is_running is False


def test_rust_output_thread_exposes_direct_render_and_power_telemetry() -> None:
    output = RustEngineOutputThread(
        (64,),
        (_mock_spec("main", 64),),
        binary_path=_engine_binary(),
    )
    worker = RuntimeWorker(RuntimeSettings(pixels=64))
    worker._output_thread = output
    output.start()
    try:
        acknowledgement = output.submit_render_frame(
            sequence=1,
            timestamp_ns=1_000_000,
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(RustRenderOutput("rainbow", {}),),
            power_limiting_enabled=True,
            power_budget_watts=0.5,
        )
        assert acknowledgement.sequence == 1

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            stats = output.stats()
            if stats.render_time_ms is not None:
                break
            time.sleep(0.01)
        assert stats.render_time_ms is not None and stats.render_time_ms >= 0.0
        assert stats.estimated_power_watts is not None
        assert stats.estimated_power_watts > 0.5
        assert stats.applied_power_scale is not None
        assert stats.applied_power_scale < 1.0
        assert stats.output_power_watts is not None
        assert len(stats.output_power_watts) == 1
        assert stats.limiting_output_index is None
        assert stats.preview_outputs is not None
        assert len(stats.preview_outputs) == 1
        assert len(stats.preview_outputs[0]) == 64 * 4
        worker._capture_preview_frame(use_pending=False)
        assert len(worker.preview_frame().outputs) == 1
        assert len(worker.preview_frame().outputs[0]) == 64 * 4
        assert stats.transmitted_frames == 1
    finally:
        worker._output_thread = None
        output.stop()


def test_rust_output_thread_schedules_repeated_frames_without_new_scene_requests() -> None:
    output = RustEngineOutputThread(
        (16,),
        (_mock_spec("main", 16),),
        binary_path=_engine_binary(),
    )
    output.start()
    try:
        output.submit_render_frame(
            sequence=1,
            timestamp_ns=time.monotonic_ns(),
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(RustRenderOutput("rainbow", {}),),
            scheduler_fps=100,
        )
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            stats = output.stats()
            if stats.transmitted_frames >= 3:
                break
            time.sleep(0.01)
        assert stats.transmitted_frames >= 3
        assert stats.scheduler_missed_deadlines >= 0
    finally:
        output.stop()


def test_rust_output_thread_orders_scene_controls_and_coalesces_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rust_engine_module, "_MAX_PENDING_SCENE_UPDATES", 2)
    output = RustEngineOutputThread(
        (8,),
        (_mock_spec("main", 8),),
        binary_path=_engine_binary(),
    )
    output.start()
    process = output._process
    assert process is not None
    original_update_scene = process.update_scene
    original_update_audio = process.update_audio
    first_scene_started = threading.Event()
    allow_first_scene = threading.Event()
    scene_revisions: list[int] = []
    audio_sequences: list[int] = []

    def delayed_scene(**kwargs: object) -> bool:
        revision = int(kwargs["revision"])
        scene_revisions.append(revision)
        if revision == 1:
            first_scene_started.set()
            assert allow_first_scene.wait(1.0)
        return original_update_scene(**kwargs)  # type: ignore[arg-type]

    def record_audio(**kwargs: object) -> bool:
        audio_sequences.append(int(kwargs["sequence"]))
        return original_update_audio(**kwargs)  # type: ignore[arg-type]

    process.update_scene = delayed_scene  # type: ignore[method-assign]
    process.update_audio = record_audio  # type: ignore[method-assign]
    try:
        first_timestamp_ns = time.monotonic_ns()
        output.submit_scene_update(
            revision=1,
            timestamp_ns=first_timestamp_ns,
            brightness=1.0,
            outputs=(RustRenderOutput("rainbow", {}),),
            power_limiting_enabled=False,
            power_budget_watts=None,
            scheduler_fps=100,
        )
        assert first_scene_started.wait(1.0)
        for revision in (2, 3):
            output.submit_scene_update(
                revision=revision,
                timestamp_ns=time.monotonic_ns(),
                brightness=1.0,
                outputs=(RustRenderOutput("rainbow", {}),),
                power_limiting_enabled=False,
                power_budget_watts=None,
                scheduler_fps=100,
            )
        with pytest.raises(RustEngineError, match="control queue is full"):
            output.submit_scene_update(
                revision=4,
                timestamp_ns=time.monotonic_ns(),
                brightness=1.0,
                outputs=(RustRenderOutput("rainbow", {}),),
                power_limiting_enabled=False,
                power_budget_watts=None,
                scheduler_fps=100,
            )
        for sequence in (1, 2, 3):
            output.submit_audio_update(
                sequence=sequence,
                timestamp_ns=time.monotonic_ns(),
                audio=None,
            )
        pending = output.stats()
        assert pending.pending_scene_updates == 2
        assert pending.pending_audio_update is True
        assert pending.audio_updates_replaced == 2

        allow_first_scene.set()
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            stats = output.stats()
            if (
                stats.scene_control_revision == 3
                and stats.audio_update_sequence == 3
            ):
                break
            time.sleep(0.01)
        assert scene_revisions == [1, 2, 3]
        assert audio_sequences == [3]
        assert stats.scene_control_revision == 3
        assert stats.audio_update_sequence == 3
        assert stats.pending_scene_updates == 0
        assert stats.pending_audio_update is False
    finally:
        allow_first_scene.set()
        output.stop()


def test_rust_output_thread_reports_rust_owned_cycling_selection() -> None:
    output = RustEngineOutputThread(
        (8,),
        (_mock_spec("main", 8),),
        binary_path=_engine_binary(),
    )
    output.start()
    try:
        timestamp_ns = time.monotonic_ns()
        beat_start_ns = timestamp_ns - 3_000_000_000
        audio = RustRenderAudioInput(
            rms=0.5,
            bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1),
            beat=True,
            beat_strength=0.9,
            fallback_bpm=120.0,
            beat_events=tuple(
                RustRenderBeatEvent(
                    sequence=index + 1,
                    timestamp_ns=beat_start_ns + index * 500_000_000,
                )
                for index in range(7)
            ),
        )
        output.submit_render_frame(
            sequence=1,
            timestamp_ns=timestamp_ns,
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(
                RustRenderOutput(
                    "rainbow",
                    {},
                    cycling=RustRenderCycling(
                        order="sequential",
                        timing="fixed",
                        interval_ns=200_000_000,
                        seed=7,
                        choices=(("rainbow", {}), ("rainbow_cycle", {})),
                        transition_duration_ns=50_000_000,
                        transition_mode="wipe",
                    ),
                ),
            ),
            audio=audio,
            scheduler_fps=100,
        )
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            stats = output.stats()
            if (
                stats.selected_animations == ("rainbow_cycle",)
                and stats.beat_clocks
            ):
                break
            time.sleep(0.005)
        assert stats.selected_animations == ("rainbow_cycle",)
        assert stats.scene_revisions == (1,)
        assert stats.cycling_active == (True,)
        assert len(stats.beat_clocks) == 1
        assert stats.beat_clocks[0].bpm == pytest.approx(120.0, abs=0.1)
        assert stats.beat_clocks[0].beat_index >= 7
    finally:
        output.stop()


def test_rust_dynamic_initial_selection_matches_python_selector() -> None:
    config = DynamicSelectorConfig(
        min_duration_s=0.0,
        max_duration_s=45.0,
        switch_cooldown_s=0.0,
        randomness=0.15,
        history_size=5,
        seed=7,
        switch_margin=0.12,
    )
    player = AnimationPlayer.from_catalog(animation_catalog())
    features = MusicFeatures(
        bpm=130.0,
        energy=0.8,
        volume=0.8,
        energy_level=0.8,
        bass=0.9,
        bass_energy=0.9,
        mid_energy=0.3,
        treble_energy=0.6,
        beat_confidence=0.9,
        beat=True,
        beat_strength=0.9,
        drop_detected=True,
    )
    python_selector = DynamicSelector(config)
    expected = python_selector.propose(player, features, now_s=10.0)
    current = player.name_at(player.current_index())
    assert current is not None
    choices = tuple(
        (
            name,
            player.animation_settings(name) or {},
        )
        for index in player.automatic_indices()
        if (name := player.name_at(index)) is not None
    )

    output = RustEngineOutputThread(
        (8,),
        (_mock_spec("main", 8),),
        binary_path=_engine_binary(),
    )
    output.start()
    try:
        output.submit_render_frame(
            sequence=1,
            timestamp_ns=time.monotonic_ns(),
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(
                RustRenderOutput(
                    current,
                    player.animation_settings(current) or {},
                    cycling=RustRenderCycling(
                        order="sequential",
                        timing="fixed",
                        interval_ns=1,
                        seed=7,
                        choices=choices,
                        transition_duration_ns=100_000_000,
                        transition_mode="crossfade",
                        selector=RustRenderDynamicSelector(
                            min_duration_ns=0,
                            max_duration_ns=45_000_000_000,
                            switch_cooldown_ns=0,
                            drop_cooldown_ns=15_000_000_000,
                            randomness=config.randomness,
                            history_size=config.history_size,
                            seed=7,
                            switch_margin=config.switch_margin,
                            activity=RustRenderMusicActivityConfig(
                                idle_enter_frames=60,
                                activation_delay_ns=0,
                                feature_attack=0.28,
                                feature_release=0.08,
                                energy_threshold=0.03,
                                onset_threshold=0.025,
                                beat_density_threshold=0.05,
                                brightness_threshold=0.08,
                                spectral_balance_ratio=0.35,
                                activation_hysteresis_ratio=1.35,
                                beat_density_window_ns=2_000_000_000,
                            ),
                        ),
                        dynamic_idle=RustRenderDynamicIdle(
                            "off", None, {}, (0, 0, 0), 1.0
                        ),
                    ),
                ),
            ),
            audio=RustRenderAudioInput(
                rms=0.7,
                bands=(0.9, 0.8, 0.6, 0.4, 0.3, 0.2, 0.1, 0.1),
                beat=True,
                beat_strength=features.beat_strength,
                fallback_bpm=features.bpm,
                selector_features=RustRenderSelectorFeatures(
                    energy_level=features.energy_level,
                    energy=features.energy,
                    volume=features.volume,
                    brightness=features.brightness,
                    onset_strength=features.onset_strength,
                    bass_energy=features.bass_energy,
                    bass=features.bass,
                    mid_energy=features.mid_energy,
                    treble_energy=features.treble_energy,
                    beat_confidence=features.beat_confidence,
                    bpm=features.bpm,
                    beat=features.beat,
                    silence=features.silence,
                    drop_detected=features.drop_detected,
                    section_change=features.section_change,
                ),
            ),
            scheduler_fps=100,
        )

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            stats = output.stats()
            if stats.selected_animations == (expected.selected_name,):
                break
            time.sleep(0.005)
        assert stats.selected_animations == (expected.selected_name,)
        assert stats.cycling_active == (True,)
        assert stats.dynamic_gate_states == ("music",)
    finally:
        output.stop()


def test_rust_scheduled_dynamic_playback_matches_python_clock_for_three_minute_beat_history() -> None:
    output = RustEngineOutputThread(
        (8,),
        (_mock_spec("main", 8),),
        binary_path=_engine_binary(),
    )
    output.start()
    choices = (("pulse", {}), ("comet", {}))
    cycling = RustRenderCycling(
        order="sequential",
        timing="fixed",
        interval_ns=1,
        seed=7,
        choices=choices,
        transition_duration_ns=100_000_000,
        transition_mode="crossfade",
        selector=RustRenderDynamicSelector(
            min_duration_ns=0,
            max_duration_ns=45_000_000_000,
            switch_cooldown_ns=0,
            drop_cooldown_ns=15_000_000_000,
            randomness=0.15,
            history_size=5,
            seed=7,
            switch_margin=0.12,
            activity=RustRenderMusicActivityConfig(
                idle_enter_frames=60,
                activation_delay_ns=0,
                feature_attack=0.28,
                feature_release=0.08,
                energy_threshold=0.03,
                onset_threshold=0.025,
                beat_density_threshold=0.05,
                brightness_threshold=0.08,
                spectral_balance_ratio=0.35,
                activation_hysteresis_ratio=1.35,
                beat_density_window_ns=2_000_000_000,
            ),
        ),
        dynamic_idle=RustRenderDynamicIdle("off", None, {}, (0, 0, 0), 1.0),
    )
    try:
        timestamp_ns = time.monotonic_ns()
        period_ns = round(60.0 / 128.0 * 1_000_000_000)
        beat_count = 384
        first_event_ns = timestamp_ns - (beat_count - 1) * period_ns
        beat_events_list: list[RustRenderBeatEvent] = []
        for index in range(beat_count):
            if index > 0 and index % 97 == 41:
                # Simulate a dropped audio event while preserving its sequence gap.
                continue
            jitter_ns = ((index % 7) - 3) * 500_000
            event = RustRenderBeatEvent(
                sequence=index + 1,
                timestamp_ns=first_event_ns + index * period_ns + jitter_ns,
            )
            if index == 190:
                # A delayed, higher-sequence event must not move the clock backward.
                event = RustRenderBeatEvent(
                    sequence=event.sequence,
                    timestamp_ns=first_event_ns + 150 * period_ns,
                )
            beat_events_list.append(event)
            if index > 0 and index % 113 == 17:
                # Duplicate delivery is expected to be idempotent.
                beat_events_list.append(event)
        beat_events = tuple(beat_events_list)
        python_tracker = BeatClockTracker()
        python_tracker.update(
            tuple(
                BeatEvent(
                    event.sequence,
                    event.timestamp_ns / 1_000_000_000,
                    0.9,
                )
                for event in beat_events
            ),
            now_s=timestamp_ns / 1_000_000_000,
            fallback_bpm=120.0,
        )
        output.submit_render_frame(
            sequence=1,
            timestamp_ns=timestamp_ns,
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(RustRenderOutput("pulse", {}, cycling=cycling),),
            audio=RustRenderAudioInput(
                rms=0.5,
                bands=(0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1),
                beat=True,
                beat_strength=0.9,
                feature_timestamp_ns=timestamp_ns,
                feature_sequence=beat_count,
                fallback_bpm=120.0,
                beat_events=beat_events,
                selector_features=RustRenderSelectorFeatures(
                    energy_level=0.8,
                    energy=0.8,
                    volume=0.8,
                    brightness=0.8,
                    onset_strength=0.8,
                    bass_energy=0.9,
                    bass=0.9,
                    mid_energy=0.6,
                    treble_energy=0.5,
                    beat_confidence=0.9,
                    bpm=128.0,
                    beat=True,
                    silence=False,
                    drop_detected=True,
                    section_change=False,
                ),
            ),
            scheduler_fps=100,
        )

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            stats = output.stats()
            if (
                stats.beat_clocks
                and stats.dynamic_gate_states == ("music",)
                and stats.transmitted_frames > 0
                and stats.audio_feature_to_led_latency_ms is not None
            ):
                break
            time.sleep(0.005)
        assert len(stats.beat_clocks) == 1
        actual = stats.beat_clocks[0]
        expected = python_tracker.state(actual.sampled_at_ns / 1_000_000_000)
        assert actual.bpm == pytest.approx(expected.bpm, abs=0.01)
        assert actual.beat_index == expected.beat_index
        assert actual.last_event_sequence == max(
            event.sequence for event in beat_events
        )
        assert actual.phase == pytest.approx(expected.phase, abs=0.03)
        assert actual.confidence == pytest.approx(expected.confidence, abs=0.03)
        assert actual.next_beat_at_ns == pytest.approx(
            expected.next_beat_at * 1_000_000_000,
            abs=25_000_000,
        )
        assert stats.dynamic_gate_states == ("music",)
        assert stats.cycling_active == (True,)
        assert stats.selected_animations[0] in {name for name, _ in choices}
        assert stats.audio_feature_to_led_latency_ms is not None
        assert stats.audio_feature_to_led_p50_ms is not None
        assert stats.audio_feature_to_led_p95_ms is not None
        assert stats.audio_feature_to_led_p99_ms is not None
    finally:
        output.stop()


def test_rust_output_thread_coalesces_render_ipc_off_the_caller_thread() -> None:
    output = RustEngineOutputThread(
        (64,),
        (_mock_spec("main", 64),),
        binary_path=_engine_binary(),
    )
    output.start()
    process = output._process
    assert process is not None
    original_render = process.render_frame
    render_started = threading.Event()
    allow_render = threading.Event()

    def delayed_render(**kwargs: object) -> RustRenderAck:
        render_started.set()
        assert allow_render.wait(1.0)
        return original_render(**kwargs)  # type: ignore[arg-type]

    process.render_frame = delayed_render  # type: ignore[method-assign]
    try:
        first = output.submit_render_frame(
            sequence=1,
            timestamp_ns=time.monotonic_ns(),
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(RustRenderOutput("rainbow", {}),),
        )
        assert first.replaced is False
        assert render_started.wait(1.0)

        second = output.submit_render_frame(
            sequence=2,
            timestamp_ns=time.monotonic_ns(),
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(RustRenderOutput("rainbow", {}),),
        )
        third = output.submit_render_frame(
            sequence=3,
            timestamp_ns=time.monotonic_ns(),
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(RustRenderOutput("rainbow", {}),),
        )
        assert second.replaced is False
        assert third.replaced is True
        assert output.stats().dropped_frames >= 1
        allow_render.set()
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            health = process.health()
            if health.last_sequence == 3:
                break
            time.sleep(0.01)
        assert health.last_sequence == 3
    finally:
        allow_render.set()
        output.stop()


def test_rust_output_thread_exposes_asynchronous_render_rejections() -> None:
    output = RustEngineOutputThread(
        (8,),
        (_mock_spec("main", 8),),
        binary_path=_engine_binary(),
    )
    output.start()
    try:
        output.submit_render_frame(
            sequence=1,
            timestamp_ns=time.monotonic_ns(),
            delta_ns=16_000_000,
            brightness=1.0,
            outputs=(RustRenderOutput("not_a_catalog_animation", {}),),
        )
        deadline = time.monotonic() + 1.0
        render_error = None
        while time.monotonic() < deadline and render_error is None:
            render_error = output.take_render_error()
            if render_error is None:
                time.sleep(0.01)
        assert render_error is not None
        assert "invalid_render" in render_error
    finally:
        output.stop()


def test_runtime_worker_uses_rust_process_for_simulated_output(tmp_path: Path) -> None:
    worker = RuntimeWorker(
        RuntimeSettings(
            pixels=3,
            engine_backend="rust",
            engine_binary=_engine_binary(),
            audio_source="demo",
            settings_file=tmp_path / "settings.json",
        )
    )
    worker.start(timeout=3.0)
    try:
        assert isinstance(worker._output_thread, RustEngineOutputThread)
        assert worker._output_thread.process_is_running is True
        deadline = time.monotonic() + 1.0
        while worker._output_thread.stats().transmitted_frames == 0:
            assert time.monotonic() < deadline, "runtime did not submit Rust output frames"
            time.sleep(0.01)
        stats = worker._output_thread.stats()
        assert stats.render_time_ms is not None
        while worker.snapshot().scheduler_time_ms is None:
            assert time.monotonic() < deadline, "playback scheduler timing was not published"
            time.sleep(0.01)
        assert worker.snapshot().scheduler_time_ms >= 0.0
        assert stats.preview_outputs is not None
        assert worker._output_thread.exchange is not None
        assert worker._output_thread.exchange.stats().submitted_count == 0
        assert worker.snapshot().running is True
        previous_sequence = worker._rust_render_sequence
        worker.set_sequencer_settings(SwitchQuantization.BEAT, 8).result(timeout=1.0)
        worker.set_mode(PlaybackMode.DYNAMIC).result(timeout=1.0)
        deadline = time.monotonic() + 5.0
        while (
            worker._rust_render_sequence <= previous_sequence
            or worker._output_thread.stats().cycling_active != (True,)
            or worker._output_thread.stats().dynamic_gate_states != ("music",)
            or worker._output_thread.stats().selected_animations
            != (worker.playback.player.name_at(worker.playback.player.current_index()),)
        ):
            assert time.monotonic() < deadline, (
                "Rust did not own the active Dynamic selection"
            )
            time.sleep(0.01)
        assert worker.playback.dynamic_selector.current_name is None
        assert worker._rust_scene_render_error is None
        assert worker._output_thread.exchange.stats().submitted_count == 0
        rust_clock = worker._output_thread.stats().beat_clocks[0]
        assert worker.playback.beat_clock_state.beat_index == rust_clock.beat_index
        assert worker.playback.beat_clock_state.bpm == pytest.approx(rust_clock.bpm)

        python_frame = worker.player.frame
        transmitted_frames = worker._output_thread.stats().transmitted_frames
        time.sleep(0.12)
        assert worker.player.frame == python_frame
        assert worker._output_thread.stats().transmitted_frames > transmitted_frames

        previous_sequence = worker._rust_render_sequence
        worker.set_mode(PlaybackMode.SOLID, solid_color="#804020").result(timeout=1.0)
        deadline = time.monotonic() + 1.0
        expected_solid = bytes([128, 64, 32, 255]) * 3
        while (
            worker._rust_render_sequence <= previous_sequence
            or worker._output_thread.stats().preview_outputs != (expected_solid,)
        ):
            assert time.monotonic() < deadline, "solid scene was not rendered by Rust"
            time.sleep(0.01)
        assert worker._output_thread.exchange.stats().submitted_count == 0
        scene_sequence = worker._rust_render_sequence
        transmitted_frames = worker._output_thread.stats().transmitted_frames
        time.sleep(0.12)
        assert worker._rust_render_sequence == scene_sequence
        assert worker._output_thread.stats().transmitted_frames > transmitted_frames
    finally:
        worker.stop(timeout=5.0)
    assert worker._output_thread is None


def test_audio_free_static_rust_playback_does_not_advance_python_animation_state(
    tmp_path: Path,
) -> None:
    worker = RuntimeWorker(
        RuntimeSettings(
            pixels=3,
            engine_backend="rust",
            engine_binary=_engine_binary(),
            audio_source="off",
            settings_file=tmp_path / "settings.json",
        )
    )
    worker.start(timeout=3.0)
    try:
        assert isinstance(worker._output_thread, RustEngineOutputThread)
        output = worker._output_thread
        deadline = time.monotonic() + 1.0
        while output.stats().transmitted_frames < 3:
            assert time.monotonic() < deadline, "Rust scheduler did not render the scene"
            time.sleep(0.01)
        python_frame = worker.player.frame
        scene_sequence = worker._rust_render_sequence
        transmitted_frames = output.stats().transmitted_frames
        time.sleep(0.12)
        assert worker.player.frame == python_frame
        assert worker._rust_render_sequence == scene_sequence
        assert output.stats().transmitted_frames > transmitted_frames
        assert output.exchange is not None
        assert output.exchange.stats().submitted_count == 0
    finally:
        worker.stop()


def test_runtime_worker_renders_continuous_outputs_in_rust(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    CalibrationSettingsStore(settings_file).save_stripes(
        StripeTopologySettings(
            layout="continuous",
            outputs=(
                StripeOutputSettings(id="left", name="Left", pixels=3),
                StripeOutputSettings(
                    id="right",
                    name="Right",
                    pixels=5,
                    zone_count=2,
                    spi_device="/dev/spidev1.0",
                ),
            ),
        )
    )
    worker = RuntimeWorker(
        RuntimeSettings(
            pixels=3,
            engine_backend="rust",
            engine_binary=_engine_binary(),
            audio_source="off",
            settings_file=settings_file,
        )
    )
    worker.start(timeout=3.0)
    try:
        assert isinstance(worker._output_thread, RustEngineOutputThread)
        deadline = time.monotonic() + 1.0
        while worker._output_thread.stats().render_time_ms is None:
            assert time.monotonic() < deadline, "continuous scene was not rendered by Rust"
            time.sleep(0.01)
        stats = worker._output_thread.stats()
        assert stats.preview_outputs is not None
        assert tuple(map(len, stats.preview_outputs)) == (3 * 4, 5 * 4)
        assert worker._output_thread.exchange is not None
        assert worker._output_thread.exchange.stats().submitted_count == 0
        assert worker._rust_scene_render_error is None
    finally:
        worker.stop(timeout=5.0)


def test_runtime_worker_resamples_mirrored_outputs_in_rust(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    CalibrationSettingsStore(settings_file).save_stripes(
        StripeTopologySettings(
            layout="mirrored",
            outputs=(
                StripeOutputSettings(id="short", name="Short", pixels=3),
                StripeOutputSettings(
                    id="long",
                    name="Long",
                    pixels=5,
                    spi_device="/dev/spidev1.0",
                ),
            ),
        )
    )
    worker = RuntimeWorker(
        RuntimeSettings(
            pixels=3,
            engine_backend="rust",
            engine_binary=_engine_binary(),
            audio_source="off",
            settings_file=settings_file,
        )
    )
    worker.start(timeout=3.0)
    try:
        assert isinstance(worker._output_thread, RustEngineOutputThread)
        deadline = time.monotonic() + 1.0
        while worker._output_thread.stats().render_time_ms is None:
            assert time.monotonic() < deadline, "mirrored scene was not rendered by Rust"
            time.sleep(0.01)
        stats = worker._output_thread.stats()
        assert stats.preview_outputs is not None
        assert tuple(map(len, stats.preview_outputs)) == (3 * 4, 5 * 4)
        assert worker._output_thread.exchange is not None
        assert worker._output_thread.exchange.stats().submitted_count == 0
        assert worker._rust_scene_render_error is None
    finally:
        worker.stop(timeout=5.0)


def test_worker_keeps_python_render_buffers_when_rust_output_is_selected(
    tmp_path: Path,
) -> None:
    worker = RuntimeWorker(
        RuntimeSettings(
            pixels=3,
            engine_backend="rust",
            engine_binary=_engine_binary(),
            settings_file=tmp_path / "settings.json",
        )
    )
    topology = worker._build_topology(worker._topology)
    try:
        assert isinstance(topology.corrections[0]._inner, Stripe)
    finally:
        topology.raw.close()


def test_engine_blacks_out_and_exits_when_its_owner_process_dies() -> None:
    owner_script = """
import sys
import time
from pathlib import Path
from lumistripe_web.runtime.rust_engine import RustEngineProcess, RustOutputSpec

process = RustEngineProcess(binary_path=Path(sys.argv[1]))
process.configure((RustOutputSpec("owner-test", 1, False, "mock"),))
print(f"{process._process.pid} {process._socket_path}", flush=True)
time.sleep(30)
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", owner_script, str(_engine_binary())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    engine_pid: int | None = None
    socket_path: Path | None = None
    try:
        assert owner.stdout is not None
        ready, _, _ = select.select([owner.stdout], [], [], 5.0)
        assert ready, "owner process did not start the Rust engine"
        engine_pid_text, socket_path_text = owner.stdout.readline().split()
        engine_pid = int(engine_pid_text)
        socket_path = Path(socket_path_text)
        assert socket_path.exists()

        owner.kill()
        owner.wait(timeout=2.0)
        deadline = time.monotonic() + 3.0
        while socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not socket_path.exists(), "orphaned engine kept its IPC socket and outputs"
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=2.0)
        if engine_pid is not None and socket_path is not None and socket_path.exists():
            try:
                os.kill(engine_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if socket_path is not None:
            shutil.rmtree(socket_path.parent, ignore_errors=True)
        if owner.stdout is not None:
            owner.stdout.close()
        if owner.stderr is not None:
            owner.stderr.close()


def test_rust_runtime_restores_saved_startup_playback_before_render(
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.json"
    CalibrationSettingsStore(settings_file).save_startup(
        True,
        StartupPlaybackSettings(
            mode=PlaybackMode.STATIC,
            animation="comet",
            brightness=0.37,
        ),
    )
    worker = RuntimeWorker(
        RuntimeSettings(
            pixels=8,
            engine_backend="rust",
            engine_binary=_engine_binary(),
            audio_source="off",
            settings_file=settings_file,
        )
    )
    worker.start(timeout=3.0)
    try:
        assert isinstance(worker._output_thread, RustEngineOutputThread)
        output = worker._output_thread
        deadline = time.monotonic() + 1.0
        stats = output.stats()
        while (
            stats.transmitted_frames == 0
            or stats.selected_animations != ("comet",)
        ):
            assert time.monotonic() < deadline, (
                "Rust did not render the restored startup scene"
            )
            time.sleep(0.01)
            stats = output.stats()

        assert worker.playback.mode is PlaybackMode.STATIC
        assert worker.player.name_at(worker.player.current_index()) == "comet"
        assert worker.player.brightness == pytest.approx(0.37)
    finally:
        worker.stop(timeout=5.0)


def test_rust_engine_crash_requests_runtime_recovery(tmp_path: Path) -> None:
    exits: list[int] = []
    worker = RuntimeWorker(
        RuntimeSettings(
            pixels=8,
            engine_backend="rust",
            engine_binary=_engine_binary(),
            audio_source="off",
            settings_file=tmp_path / "settings.json",
        ),
        fatal_exit=exits.append,
    )
    worker.start(timeout=3.0)
    try:
        assert isinstance(worker._output_thread, RustEngineOutputThread)
        output = worker._output_thread
        deadline = time.monotonic() + 1.0
        while output.stats().transmitted_frames == 0:
            assert time.monotonic() < deadline, "Rust engine did not start rendering"
            time.sleep(0.01)

        process = output._process
        assert process is not None and process._process is not None
        process._process.kill()
        process._process.wait(timeout=2.0)

        deadline = time.monotonic() + 3.0
        while not exits and time.monotonic() < deadline:
            time.sleep(0.01)
        assert exits == [1]
        assert worker.snapshot().error == "the LED output thread stopped making progress"
    finally:
        worker.stop(timeout=5.0)
    assert worker._output_thread is None
