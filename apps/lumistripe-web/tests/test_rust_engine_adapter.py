from __future__ import annotations

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from lumistripe import Stripe
from lumistripe.gpio.spi import _FrameEncoder, encode_legacy_frame
from lumistripe.playback import PlaybackMode
from lumistripe_web.app import build_parser
from lumistripe_web.runtime import RuntimeSettings
from lumistripe_web.runtime.rust_engine import (
    RustEngineError,
    RustEngineOutputThread,
    RustEngineProcess,
    RustOutputSpec,
    RustRenderAudioInput,
    RustRenderEffect,
    RustRenderOutput,
    RustRenderTransition,
    _decode_response,
    _encode_request,
)
from lumistripe_web.runtime.worker import RuntimeWorker
from lumistripe_web.settings import (
    CalibrationSettingsStore,
    StripeOutputSettings,
    StripeTopologySettings,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_FIXTURES = REPOSITORY_ROOT / "rust" / "test-fixtures" / "protocol-v2"


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
    assert RuntimeSettings(engine_backend="rust").engine_backend == "rust"
    assert RuntimeSettings(engine_backend="rust", hardware=True).hardware is True


def test_cli_defaults_to_python_and_accepts_rust_hardware() -> None:
    defaults = build_parser().parse_args([])
    assert defaults.engine_backend == "python"
    assert defaults.engine_binary is None
    selected = build_parser().parse_args(
        [
            "--engine-backend",
            "rust",
            "--engine-binary",
            "/tmp/lumistripe-engine",
            "--hardware",
        ]
    )
    assert selected.engine_backend == "rust"
    assert selected.engine_binary == Path("/tmp/lumistripe-engine")
    assert selected.hardware is True


def test_python_codec_matches_shared_v2_fixtures() -> None:
    request = json.loads(_encode_request("fixture-health-2", "health"))
    expected_request = json.loads(
        (PROTOCOL_FIXTURES / "health-request.json").read_text(encoding="utf-8")
    )
    assert request == expected_request

    response_bytes = (
        (PROTOCOL_FIXTURES / "health-response.json").read_bytes().rstrip(b"\n")
    )
    result = _decode_response(response_bytes, "fixture-health-2")
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


def test_python_and_native_c_encoders_match_shared_sm16716_golden_frame() -> None:
    pixels = np.array([[255, 0, 16, 127], [2, 128, 64, 255]], dtype=np.uint8)
    expected_hex = (PROTOCOL_FIXTURES / "sm16716-reference.hex").read_text(
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
        worker.set_mode(PlaybackMode.DYNAMIC).result(timeout=1.0)
        deadline = time.monotonic() + 1.0
        while worker._rust_render_sequence <= previous_sequence:
            assert time.monotonic() < deadline, "Dynamic scene was not sent to Rust"
            time.sleep(0.01)
        assert worker._rust_scene_render_error is None
        assert worker._output_thread.exchange.stats().submitted_count == 0

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
    finally:
        worker.stop(timeout=5.0)
    assert worker._output_thread is None


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
