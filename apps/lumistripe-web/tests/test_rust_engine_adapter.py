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
from lumistripe_web.app import build_parser
from lumistripe_web.runtime import RuntimeSettings
from lumistripe_web.runtime.rust_engine import (
    RustEngineOutputThread,
    RustEngineProcess,
    RustOutputSpec,
    _decode_response,
    _encode_request,
)
from lumistripe_web.runtime.worker import RuntimeWorker

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


def test_runtime_worker_uses_rust_process_for_simulated_output(tmp_path: Path) -> None:
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
        assert worker._output_thread.process_is_running is True
        deadline = time.monotonic() + 1.0
        while worker._output_thread.stats().transmitted_frames == 0:
            assert time.monotonic() < deadline, "runtime did not submit Rust output frames"
            time.sleep(0.01)
        assert worker.snapshot().running is True
    finally:
        worker.stop(timeout=5.0)
    assert worker._output_thread is None


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
