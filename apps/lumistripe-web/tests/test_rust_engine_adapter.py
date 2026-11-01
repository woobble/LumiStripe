from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from lumistripe import Rgb
from lumistripe_web.app import build_parser, main
from lumistripe_web.runtime import RuntimeSettings
from lumistripe_web.runtime.rust_engine import (
    RustEngineController,
    _decode_response,
    _encode_request,
)
from lumistripe_web.runtime.worker import RuntimeWorker

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_FIXTURES = REPOSITORY_ROOT / "rust" / "test-fixtures" / "protocol-v1"


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


def test_runtime_defaults_to_python_and_rust_backend_is_explicit() -> None:
    assert RuntimeSettings().engine_backend == "python"
    assert RuntimeSettings(engine_backend="rust").engine_backend == "rust"
    with pytest.raises(ValueError, match="simulated output only"):
        RuntimeSettings(engine_backend="rust", hardware=True)


def test_cli_defaults_to_python_and_accepts_an_explicit_rust_binary() -> None:
    defaults = build_parser().parse_args([])
    assert defaults.engine_backend == "python"
    assert defaults.engine_binary is None
    selected = build_parser().parse_args(
        ["--engine-backend", "rust", "--engine-binary", "/tmp/lumistripe-engine"]
    )
    assert selected.engine_backend == "rust"
    assert selected.engine_binary == Path("/tmp/lumistripe-engine")


def test_cli_rejects_rust_backend_with_physical_hardware() -> None:
    with pytest.raises(SystemExit):
        main(["--engine-backend", "rust", "--hardware"])


def test_python_codec_matches_shared_v1_fixtures() -> None:
    request = json.loads(_encode_request("fixture-health-1", "health"))
    expected_request = json.loads(
        (PROTOCOL_FIXTURES / "health-request.json").read_text(encoding="utf-8")
    )
    assert request == expected_request

    response_bytes = (
        (PROTOCOL_FIXTURES / "health-response.json").read_bytes().rstrip(b"\n")
    )
    result = _decode_response(response_bytes, "fixture-health-1")
    assert result == {
        "type": "health",
        "status": "ready",
        "pixel_count": 3,
        "frames_rendered": 7,
    }


def test_python_controller_integrates_with_rust_process() -> None:
    controller = RustEngineController(3, binary_path=_engine_binary())
    try:
        controller.set_pixels([Rgb(1, 2, 3), Rgb(4, 5, 6), Rgb(7, 8, 9)])
        controller.flush()
        assert controller.length == 3
        controller.clear()
        assert controller.pixels()[:, :3].tolist() == [[0, 0, 0]] * 3
        assert controller.pixels()[:, 3].tolist() == [255] * 3
        controller.flush()
        health = controller.health()
        assert health.status == "ready"
        assert health.pixel_count == 3
        assert health.frames_rendered == 2
    finally:
        controller.close()
    assert controller.process_is_running is False


def test_worker_builds_rust_controllers_only_when_selected(tmp_path: Path) -> None:
    binary = _engine_binary()
    worker = RuntimeWorker(
        RuntimeSettings(
            pixels=3,
            engine_backend="rust",
            engine_binary=binary,
            settings_file=tmp_path / "settings.json",
        )
    )
    topology = worker._build_topology(worker._topology)
    try:
        assert isinstance(topology.corrections[0]._inner, RustEngineController)
    finally:
        topology.raw.close()
