from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lumistripe_web.app import create_app
from lumistripe_web.runtime import RuntimeSettings


def _port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def _request(
    client: TestClient | str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    timeout_seconds: float = 2,
) -> tuple[int, Any]:
    if isinstance(client, TestClient):
        response = client.request(method, path, json=body)
        return response.status_code, response.json()

    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{client}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.mark.skipif(
    not os.environ.get("LUMISTRIPE_RUST_WEB_BINARY"),
    reason="Rust web binary is built for the Python 3.12 parity job",
)
def test_python_and_rust_control_endpoints_match_with_isolated_mock_outputs() -> None:
    rust_web = Path(os.environ["LUMISTRIPE_RUST_WEB_BINARY"]).resolve()
    rust_engine = rust_web.with_name("lumistripe-engine")
    if not rust_web.is_file() or not rust_engine.is_file():
        pytest.fail("Rust web and engine binaries must be built for API parity")

    with TemporaryDirectory(prefix="lumistripe-api-parity-") as temporary:
        root = Path(temporary)
        python_settings = root / "python-settings.json"
        rust_settings = root / "rust-settings.json"
        log_path = root / "rust-web.log"
        port = _port()
        command = [
            str(rust_web),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--pixels",
            "8",
            "--output-backend",
            "mock",
            "--settings-file",
            str(rust_settings),
            "--engine-binary",
            str(rust_engine),
        ]
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdout=subprocess.DEVNULL,
                stderr=log,
            )
            try:
                rust_client = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        log.flush()
                        pytest.fail(
                            "Rust web process exited during startup:\n"
                            + log_path.read_text(encoding="utf-8")
                        )
                    try:
                        status, body = _request(rust_client, "GET", "/api/health")
                        if status == 200 and body == {"status": "ok"}:
                            break
                    except (OSError, TimeoutError):
                        time.sleep(0.05)
                else:
                    log.flush()
                    pytest.fail(
                        "Rust web process did not become ready:\n"
                        + log_path.read_text(encoding="utf-8")
                    )

                app = create_app(
                    RuntimeSettings(pixels=8, settings_file=python_settings)
                )
                with TestClient(app) as python_client:
                    stripe_topology_request: dict[str, Any] | None = None
                    exact_cases = [
                        ("GET", "/api/health", None),
                        ("GET", "/api/identity", None),
                        (
                            "PUT",
                            "/api/identity",
                            {
                                "application_name": "Parity Test",
                                "spotify_device_name": "Studio",
                            },
                        ),
                        ("GET", "/api/identity", None),
                        ("GET", "/api/startup", None),
                        ("PUT", "/api/startup", {"restore_last_state": False}),
                        ("GET", "/api/startup", None),
                        ("GET", "/api/transitions/settings", None),
                        (
                            "GET",
                            "/api/sequencer/settings",
                            None,
                        ),
                        ("GET", "/api/sequencer/sequences", None),
                        ("GET", "/api/stripes", None),
                        ("GET", "/api/animations/rainbow_cycle/settings", None),
                    ]
                    for method, path, body in exact_cases:
                        python_result = _request(python_client, method, path, body)
                        rust_result = _request(rust_client, method, path, body)
                        if path == "/api/stripes":
                            python_status, python_topology = python_result
                            rust_status, rust_topology = rust_result
                            assert rust_status == python_status == 200
                            stripe_topology_request = {
                                key: value for key, value in python_topology.items()
                            }
                            stripe_topology_request["outputs"] = [
                                {
                                    key: value
                                    for key, value in output.items()
                                    if key not in {"last_output_at", "error"}
                                }
                                for output in python_topology["outputs"]
                            ]
                            assert len(rust_topology["outputs"]) == len(
                                python_topology["outputs"]
                            )
                            for python_output, rust_output in zip(
                                python_topology["outputs"],
                                rust_topology["outputs"],
                                strict=True,
                            ):
                                python_time = python_output.pop("last_output_at")
                                rust_time = rust_output.pop("last_output_at")
                                assert python_time is not None
                                assert rust_time is not None
                                datetime.fromisoformat(rust_time)
                            assert rust_topology == python_topology
                            continue
                        assert rust_result == python_result, (
                            f"{method} {path}\nRust: {rust_result!r}\nPython: {python_result!r}"
                        )

                    assert stripe_topology_request is not None
                    python_status, python_body = _request(
                        python_client,
                        "PUT",
                        "/api/stripes",
                        stripe_topology_request,
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "PUT",
                        "/api/stripes",
                        stripe_topology_request,
                    )
                    assert rust_status == python_status == 200, (
                        f"topology update: Python {python_status}; Rust {rust_status} "
                        f"({rust_body.get('detail', rust_body)!r})"
                    )
                    assert rust_body["stripe_topology"]["layout"] == (
                        python_body["stripe_topology"]["layout"]
                    )
                    assert [
                        (output["id"], output["pixels"])
                        for output in rust_body["stripe_topology"]["outputs"]
                    ] == [
                        (output["id"], output["pixels"])
                        for output in python_body["stripe_topology"]["outputs"]
                    ]

                    stripe_test_body = {
                        "stripe_id": stripe_topology_request["outputs"][0]["id"],
                        "pattern": "blue",
                    }
                    python_status, python_body = _request(
                        python_client,
                        "POST",
                        "/api/stripes/test",
                        stripe_test_body,
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "POST",
                        "/api/stripes/test",
                        stripe_test_body,
                            timeout_seconds=8,
                    )
                    assert rust_status == python_status == 200
                    assert rust_body["stripe_topology"]["layout"] == (
                        python_body["stripe_topology"]["layout"]
                    )

                    python_status, python_calibration = _request(
                        python_client,
                        "POST",
                        "/api/calibration/session",
                        {"output_index": 0},
                    )
                    rust_status, rust_calibration = _request(
                        rust_client,
                        "POST",
                        "/api/calibration/session",
                        {"output_index": 0},
                    )
                    assert rust_status == python_status == 200
                    python_session = python_calibration["session_id"]
                    rust_session = rust_calibration["session_id"]
                    calibration_update = {
                        "red": 240,
                        "green": 128,
                        "blue": 96,
                        "pattern": "red",
                    }
                    python_status, python_body = _request(
                        python_client,
                        "PUT",
                        f"/api/calibration/session/{python_session}",
                        calibration_update,
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "PUT",
                        f"/api/calibration/session/{rust_session}",
                        calibration_update,
                    )
                    assert rust_status == python_status == 200
                    assert rust_body["calibration"]["pattern"] == (
                        python_body["calibration"]["pattern"]
                    )
                    python_status, python_body = _request(
                        python_client,
                        "POST",
                        f"/api/calibration/session/{python_session}/finish",
                        {"save": False},
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "POST",
                        f"/api/calibration/session/{rust_session}/finish",
                        {"save": False},
                    )
                    assert rust_status == python_status == 200
                    assert rust_body["calibration"]["active"] == (
                        python_body["calibration"]["active"]
                    )

                    for method, path, body, fields in [
                        (
                            "PUT",
                            "/api/mode",
                            {"mode": "solid", "color": "#123ABC"},
                            ("mode", "solid_color", "blackout"),
                        ),
                        (
                            "PUT",
                            "/api/brightness",
                            {"brightness": 0.4},
                            ("brightness",),
                        ),
                        (
                            "POST",
                            "/api/blackout",
                            {"enabled": True},
                            ("blackout",),
                        ),
                    ]:
                        python_status, python_body = _request(
                            python_client, method, path, body
                        )
                        rust_status, rust_body = _request(
                            rust_client, method, path, body
                        )
                        assert rust_status == python_status == 200, f"{method} {path}"
                        for field in fields:
                            assert rust_body[field] == python_body[field], (
                                f"{method} {path}: {field}"
                            )

                    animation_status, animation = _request(
                        python_client, "GET", "/api/animations/rainbow_cycle/settings"
                    )
                    rust_animation_status, rust_animation = _request(
                        rust_client, "GET", "/api/animations/rainbow_cycle/settings"
                    )
                    assert rust_animation_status == animation_status == 200
                    assert rust_animation == animation
                    animation_values = {**animation["values"], "speed": 70}
                    animation_update = {
                        "values": animation_values,
                    }
                    python_status, python_body = _request(
                        python_client,
                        "PUT",
                        "/api/animations/rainbow_cycle/settings",
                        animation_update,
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "PUT",
                        "/api/animations/rainbow_cycle/settings",
                        animation_update,
                    )
                    assert rust_status == python_status == 200
                    assert rust_body == python_body
                    for method, body in [
                        (
                            "PUT",
                            {
                                "mode": "animation",
                                "color": "#123456",
                                "animation": "rainbow_cycle",
                                "brightness": 0.45,
                            },
                        ),
                        ("PUT", {"enabled": False, "duration_ms": 600, "effect": "wipe"}),
                    ]:
                        path = (
                            "/api/music-idle"
                            if "mode" in body
                            else "/api/transitions/settings"
                        )
                        python_status, python_body = _request(
                            python_client, method, path, body
                        )
                        rust_status, rust_body = _request(
                            rust_client, method, path, body
                        )
                        assert rust_status == python_status == 200, f"{method} {path}"
                        result_field = "music_idle" if path == "/api/music-idle" else "transition_settings"
                        assert rust_body[result_field] == python_body[result_field]

                    sequencer_settings = {
                        "switch_quantization": "phrase",
                        "bars_per_phrase": 4,
                    }
                    python_status, python_body = _request(
                        python_client,
                        "PUT",
                        "/api/sequencer/settings",
                        sequencer_settings,
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "PUT",
                        "/api/sequencer/settings",
                        sequencer_settings,
                    )
                    assert rust_status == python_status == 200
                    assert rust_body["sequencer_settings"] == python_body["sequencer_settings"]

                    sequence = {
                        "id": "parity-sequence",
                        "name": "Parity Sequence",
                        "repeat": True,
                        "steps": [
                            {"animation_name": "rainbow_cycle", "duration_bars": 2}
                        ],
                    }
                    python_status, python_body = _request(
                        python_client,
                        "POST",
                        "/api/sequencer/sequences",
                        sequence,
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "POST",
                        "/api/sequencer/sequences",
                        sequence,
                    )
                    assert rust_status == python_status == 200
                    assert rust_body["sequence_library"] == python_body["sequence_library"]

                    active_sequence = {"sequence_id": "parity-sequence"}
                    python_status, python_body = _request(
                        python_client,
                        "PUT",
                        "/api/sequencer/active",
                        active_sequence,
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "PUT",
                        "/api/sequencer/active",
                        active_sequence,
                    )
                    assert rust_status == python_status == 200
                    assert rust_body["sequence_library"] == python_body["sequence_library"]

                    for method, path, body in [
                        (
                            "PUT",
                            "/api/sequencer/sequences/parity-sequence",
                            {
                                "name": "Updated Sequence",
                                "repeat": False,
                                "steps": [
                                    {"animation_name": "rainbow_cycle", "duration_bars": 4}
                                ],
                            },
                        ),
                        (
                            "DELETE",
                            "/api/sequencer/sequences/parity-sequence",
                            None,
                        ),
                    ]:
                        python_status, python_body = _request(
                            python_client, method, path, body
                        )
                        rust_status, rust_body = _request(
                            rust_client, method, path, body
                        )
                        assert rust_status == python_status == 200, f"{method} {path}"
                        assert rust_body["sequence_library"] == python_body["sequence_library"]

                    audio_source = {"source": "demo"}
                    python_status, python_body = _request(
                        python_client, "PUT", "/api/audio/source", audio_source
                    )
                    rust_status, rust_body = _request(
                        rust_client, "PUT", "/api/audio/source", audio_source
                    )
                    assert rust_status == python_status == 200, (
                        f"audio source status: Python {python_status}; "
                        f"Rust {rust_status} ({rust_body.get('detail', rust_body)!r})"
                    )
                    for field in ("source", "active_source", "monitoring", "sync_route"):
                        assert rust_body[field] == python_body[field], (
                            f"audio source {field}: Python={python_body[field]!r}, "
                            f"Rust={rust_body[field]!r}, error={rust_body.get('error')!r}"
                        )
                    python_audio_settings = python_body
                    rust_audio_settings = rust_body

                    python_status, python_body = _request(
                        python_client,
                        "PUT",
                        "/api/audio/sync-offset",
                        {"offset_ms": 23.5},
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "PUT",
                        "/api/audio/sync-offset",
                        {"offset_ms": 23.5},
                    )
                    assert rust_status == python_status == 200
                    for field in ("sync_route", "sync_offset_ms"):
                        assert rust_body[field] == python_body[field], f"sync offset: {field}"

                    python_status, python_body = _request(
                        python_client,
                        "POST",
                        "/api/audio/sync-calibration",
                        {"active": False},
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "POST",
                        "/api/audio/sync-calibration",
                        {"active": False},
                    )
                    assert rust_status == python_status == 200
                    assert rust_body == python_body

                    python_audio_devices = {
                        device["name"] for device in python_audio_settings["devices"]
                    }
                    rust_audio_devices = {
                        device["name"] for device in rust_audio_settings["devices"]
                    }
                    shared_audio_devices = sorted(
                        python_audio_devices & rust_audio_devices
                    )
                    if shared_audio_devices:
                        profile_device = shared_audio_devices[0]
                        audio_profile = {
                            "device": profile_device,
                            "settings": {
                                "target_level": 0.42,
                                "input_noise_floor": 0.012,
                            },
                        }
                        python_status, python_body = _request(
                            python_client,
                            "PUT",
                            "/api/audio/settings",
                            audio_profile,
                        )
                        rust_status, rust_body = _request(
                            rust_client,
                            "PUT",
                            "/api/audio/settings",
                            audio_profile,
                        )
                        assert rust_status == python_status == 200
                        assert rust_body["settings"] == python_body["settings"]
                        reset_audio = {"device": profile_device}
                        python_status, python_body = _request(
                            python_client,
                            "POST",
                            "/api/audio/settings/reset",
                            reset_audio,
                        )
                        rust_status, rust_body = _request(
                            rust_client,
                            "POST",
                            "/api/audio/settings/reset",
                            reset_audio,
                        )
                        assert rust_status == python_status == 200
                        assert rust_body["settings"] == python_body["settings"]

                    python_status, python_body = _request(
                        python_client,
                        "PUT",
                        "/api/mode",
                        {"mode": "dynamic"},
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "PUT",
                        "/api/mode",
                        {"mode": "dynamic"},
                    )
                    assert rust_status == python_status == 200
                    for field in (
                        "mode",
                        "configured_audio_source",
                        "active_audio_source",
                        "audio_monitoring",
                    ):
                        assert rust_body[field] == python_body[field], f"dynamic demo: {field}"
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
