from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lumistripe.playback.engine import demo_snapshot
from lumistripe_web.api.protocols.preview import (
    PREVIEW_HEADER,
    PREVIEW_MAGIC,
    PREVIEW_MAX_OUTPUTS,
    PREVIEW_MAX_PIXELS,
    PREVIEW_OUTPUT_HEADER,
    PREVIEW_VERSION,
)
from lumistripe_web.app import create_app
from lumistripe_web.contracts.audio import (
    AudioSettingsResponse,
    AudioTelemetry,
    SpotifyStatusResponse,
)
from lumistripe_web.contracts.bluetooth import BluetoothStatusResponse
from lumistripe_web.contracts.common import DashboardState
from lumistripe_web.contracts.network import NetworkStatusResponse
from lumistripe_web.contracts.system import SystemStats, UpdateStatus
from lumistripe_web.runtime import LumiStripeRuntime, RuntimeSettings
from lumistripe_web.spotify import SpotifyClient
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as websocket_connect
from websockets.sync.server import serve as websocket_serve


def _port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def _request(
    client: TestClient | str,
    method: str,
    path: str,
    body: Any | None = None,
    # Native control commands have a bounded five-second engine acknowledgement.
    timeout_seconds: float = 8,
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


def _assert_response_matches_contract(body: Any, contract: type[BaseModel]) -> None:
    assert isinstance(body, dict)
    assert set(body) == set(contract.model_fields)
    contract.model_validate(body)


@pytest.mark.skipif(
    not os.environ.get("LUMISTRIPE_RUST_WEB_BINARY"),
    reason="Rust web binary is built for the Python 3.12 parity job",
)
def test_python_and_rust_pairing_authentication_matches() -> None:
    rust_web = Path(os.environ["LUMISTRIPE_RUST_WEB_BINARY"]).resolve()
    rust_engine = rust_web.with_name("lumistripe-engine")
    if not rust_web.is_file() or not rust_engine.is_file():
        pytest.fail("Rust web and engine binaries must be built for API parity")

    with TemporaryDirectory(prefix="lumistripe-auth-parity-") as temporary:
        root = Path(temporary)
        port = _port()
        settings_file = root / "rust-settings.json"
        log_path = root / "rust-web.log"
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
            str(settings_file),
            "--engine-binary",
            str(rust_engine),
        ]
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=root,
                stdout=subprocess.DEVNULL,
                stderr=log,
                env={
                    **os.environ,
                    "LUMI_PAIRING_CODE": "1234",
                    "LUMI_UPDATE_STATE_FILE": str(root / "update-status.json"),
                    "LUMI_PROJECT_DIR": str(root / "rust-project"),
                },
            )
            rust_client = f"http://127.0.0.1:{port}"
            rust_opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(CookieJar())
            )

            def request_rust(
                method: str, path: str, body: Any | None = None
            ) -> tuple[int, Any, Any]:
                data = None if body is None else json.dumps(body).encode("utf-8")
                request = urllib.request.Request(
                    f"{rust_client}{path}",
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method=method,
                )
                try:
                    with rust_opener.open(request, timeout=8) as response:
                        return response.status, json.loads(response.read()), response.headers
                except urllib.error.HTTPError as error:
                    return error.code, json.loads(error.read()), error.headers

            try:
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
                    RuntimeSettings(
                        pixels=8,
                        settings_file=root / "python-settings.json",
                    ),
                    pairing_code="1234",
                    update_state_file=root / "python-update-status.json",
                    project_dir=root / "python-project",
                )
                with TestClient(app) as python_client:

                    def compare(
                        method: str, path: str, body: Any | None = None
                    ) -> tuple[Any, Any]:
                        python_response = python_client.request(
                            method, path, json=body
                        )
                        rust_status, rust_body, rust_headers = request_rust(
                            method, path, body
                        )
                        assert rust_status == python_response.status_code, (
                            f"{method} {path}: Python {python_response.status_code}; "
                            f"Rust {rust_status}"
                        )
                        assert rust_body == python_response.json(), (
                            f"{method} {path}: Python {python_response.json()!r}; "
                            f"Rust {rust_body!r}"
                        )
                        return python_response, rust_headers

                    def cookie_attributes(header: str) -> frozenset[str]:
                        return frozenset(
                            attribute.strip().lower()
                            for attribute in header.split(";")[1:]
                        )

                    for path in ("/api/auth/status", "/api/animations/rainbow_cycle/settings"):
                        compare("GET", path)

                    with (
                        pytest.raises(WebSocketDisconnect) as python_ws_close,
                        python_client.websocket_connect("/ws/state"),
                    ):
                        pass
                    with (
                        pytest.raises(ConnectionClosed) as rust_ws_close,
                        websocket_connect(
                            f"ws://127.0.0.1:{port}/ws/state",
                            open_timeout=3,
                            close_timeout=3,
                        ) as websocket,
                    ):
                        websocket.recv(timeout=3)
                    assert python_ws_close.value.code == 4401
                    assert rust_ws_close.value.rcvd is not None
                    assert rust_ws_close.value.rcvd.code == python_ws_close.value.code
                    assert rust_ws_close.value.rcvd.reason == python_ws_close.value.reason

                    with (
                        pytest.raises(WebSocketDisconnect) as python_origin_close,
                        python_client.websocket_connect(
                            "/ws/state", headers={"origin": "https://evil.example"}
                        ),
                    ):
                        pass
                    with (
                        pytest.raises(ConnectionClosed) as rust_origin_close,
                        websocket_connect(
                            f"ws://127.0.0.1:{port}/ws/state",
                            origin="https://evil.example",
                            open_timeout=3,
                            close_timeout=3,
                        ) as websocket,
                    ):
                        websocket.recv(timeout=3)
                    assert python_origin_close.value.code == 1008
                    assert python_origin_close.value.reason == "invalid origin"
                    assert rust_origin_close.value.rcvd is not None
                    assert rust_origin_close.value.rcvd.code == python_origin_close.value.code
                    assert rust_origin_close.value.rcvd.reason == python_origin_close.value.reason

                    rejected, _ = compare(
                        "POST", "/api/auth/pair", {"code": "0000"}
                    )
                    assert rejected.status_code == 401

                    paired, rust_headers = compare(
                        "POST", "/api/auth/pair", {"code": "1234"}
                    )
                    assert paired.status_code == 200
                    python_cookie = paired.headers["set-cookie"]
                    rust_cookie = rust_headers.get("Set-Cookie")
                    assert rust_cookie is not None
                    assert cookie_attributes(rust_cookie) == cookie_attributes(
                        python_cookie
                    )
                    for cookie in (python_cookie, rust_cookie):
                        session = cookie.split(";", 1)[0].split("=", 1)[1]
                        assert session not in {"", "1234"}

                    compare("GET", "/api/auth/status")
                    compare("GET", "/api/animations/rainbow_cycle/settings")
                    logged_out, rust_headers = compare("POST", "/api/auth/logout")
                    assert logged_out.status_code == 200
                    for header in (
                        logged_out.headers["set-cookie"],
                        rust_headers.get("Set-Cookie", ""),
                    ):
                        attributes = cookie_attributes(header)
                        cookie = header.split(";", 1)[0].split("=", 1)[1].strip('"')
                        assert cookie == ""
                        assert "max-age=0" in attributes
                        assert "path=/" in attributes
                        assert "samesite=strict" in attributes

                    compare("GET", "/api/auth/status")
                    unauthorized, _ = compare(
                        "GET", "/api/animations/rainbow_cycle/settings"
                    )
                    assert unauthorized.status_code == 401

                    for _ in range(4):
                        limited_attempt, rust_headers = compare(
                            "POST", "/api/auth/pair", {"code": "0000"}
                        )
                        assert limited_attempt.status_code == 401
                    limited, rust_headers = compare(
                        "POST", "/api/auth/pair", {"code": "1234"}
                    )
                    assert limited.status_code == 429
                    for retry_after in (
                        limited.headers["retry-after"],
                        rust_headers["Retry-After"],
                    ):
                        assert 1 <= int(retry_after) <= 60
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def test_rust_openapi_preserves_fastapi_operations_and_status_codes() -> None:
    rust_openapi_path = Path(__file__).parents[1] / "frontend" / "openapi.json"
    rust_openapi = json.loads(rust_openapi_path.read_text(encoding="utf-8"))
    python_openapi = create_app().openapi()
    rust_dashboard_schema = rust_openapi["components"]["schemas"]["DashboardState"]
    selector_diagnostics_schema = rust_dashboard_schema["properties"][
        "selector_diagnostics"
    ]
    assert selector_diagnostics_schema["type"] == "array"
    assert selector_diagnostics_schema["items"]["$ref"] == (
        "#/components/schemas/DynamicSelectorDiagnostics"
    )
    rust_compatibility_openapi = {
        **rust_openapi,
        "components": {
            **rust_openapi["components"],
            "schemas": {
                **rust_openapi["components"]["schemas"],
                "DashboardState": {
                    **rust_dashboard_schema,
                    "properties": {
                        name: schema
                        for name, schema in rust_dashboard_schema["properties"].items()
                        if name != "selector_diagnostics"
                    },
                },
            },
        },
    }
    http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}

    def operations(document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        return {
            (method.upper(), path): operation
            for path, path_item in document["paths"].items()
            for method, operation in path_item.items()
            if method.lower() in http_methods
        }

    def schema_signature(
        schema: dict[str, Any],
        document: dict[str, Any],
        seen_refs: frozenset[str] = frozenset(),
        include_constraints: bool = False,
    ) -> tuple[Any, ...]:
        reference = schema.get("$ref")
        if reference is not None:
            if reference in seen_refs:
                return ("recursive-reference", reference.rsplit("/", 1)[-1])
            target: Any = document
            for part in reference.removeprefix("#/").split("/"):
                target = target[part]
            return schema_signature(
                target, document, seen_refs | {reference}, include_constraints
            )

        alternatives = schema.get("anyOf", schema.get("oneOf"))
        schema_type = schema.get("type")
        if alternatives is not None:
            union_signature = tuple(
                sorted(
                    (
                        schema_signature(
                            item, document, seen_refs, include_constraints
                        )
                        for item in alternatives
                    ),
                    key=repr,
                )
            )
            schema_type = None
        elif isinstance(schema_type, list):
            union_signature = tuple(
                sorted(
                    (
                        schema_signature(
                            {
                                "type": item,
                                **(
                                    {
                                        key: schema[key]
                                        for key in (
                                            "minimum",
                                            "maximum",
                                            "exclusiveMinimum",
                                            "exclusiveMaximum",
                                            "multipleOf",
                                            "minLength",
                                            "maxLength",
                                            "pattern",
                                            "minItems",
                                            "maxItems",
                                            "uniqueItems",
                                            "minProperties",
                                            "maxProperties",
                                        )
                                        if key in schema
                                        and include_constraints
                                    }
                                ),
                                **(
                                    {"format": schema["format"]}
                                    if item == "string"
                                    and "format" in schema
                                    else {}
                                ),
                            },
                            document,
                            seen_refs,
                            include_constraints,
                        )
                        for item in schema_type
                    ),
                    key=repr,
                )
            )
            schema_type = None
        else:
            union_signature = ()

        constraint_keys = {
            "integer": (
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "multipleOf",
            ),
            "number": (
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "multipleOf",
            ),
            "string": ("minLength", "maxLength", "pattern"),
            "array": ("minItems", "maxItems", "uniqueItems"),
            "object": ("minProperties", "maxProperties"),
        }.get(schema_type, ())
        constraints = (
            {
                key: schema[key]
                for key in constraint_keys
                if key in schema
            }
            if include_constraints
            else {}
        )
        if "minimum" in constraints and constraints.get("minimum") == constraints.get(
            "exclusiveMinimum"
        ):
            constraints.pop("minimum")
        if "maximum" in constraints and constraints.get("maximum") == constraints.get(
            "exclusiveMaximum"
        ):
            constraints.pop("maximum")
        if (
            schema_type == "string"
            and "format" in schema
        ):
            constraints["format"] = schema["format"]

        enum = schema.get("enum")
        return (
            schema_type,
            union_signature,
            tuple(sorted(constraints.items())),
            tuple(sorted(enum, key=repr)) if enum is not None else None,
            schema.get("const"),
            tuple(sorted(schema.get("required", []))),
            tuple(
                (
                    name,
                    schema_signature(
                        value, document, seen_refs, include_constraints
                    ),
                )
                for name, value in sorted(schema.get("properties", {}).items())
            ),
            schema_signature(
                schema["items"], document, seen_refs, include_constraints
            )
            if "items" in schema
            else None,
            schema_signature(
                schema["additionalProperties"],
                document,
                seen_refs,
                include_constraints,
            )
            if isinstance(schema.get("additionalProperties"), dict)
            else schema.get("additionalProperties"),
            tuple(
                sorted(
                    (
                        schema_signature(
                            item, document, seen_refs, include_constraints
                        )
                        for item in schema.get("allOf", [])
                    ),
                    key=repr,
                )
            ),
        )

    def parameter_schema_signature(
        parameter: dict[str, Any], document: dict[str, Any]
    ) -> tuple[Any, ...]:
        schema = parameter.get("schema", {})
        if not parameter.get("required", False):
            alternatives_key = next(
                (key for key in ("anyOf", "oneOf") if key in schema), None
            )
            if alternatives_key is not None:
                alternatives = [
                    alternative
                    for alternative in schema[alternatives_key]
                    if alternative.get("type") != "null"
                ]
                if len(alternatives) == 1:
                    schema = alternatives[0]
            elif isinstance(schema.get("type"), list):
                non_null_types = [kind for kind in schema["type"] if kind != "null"]
                if len(non_null_types) == 1:
                    schema = {**schema, "type": non_null_types[0]}
        return schema_signature(schema, document, include_constraints=True)

    rust_operations = operations(rust_openapi)
    python_operations = operations(python_openapi)
    assert rust_operations.keys() == python_operations.keys(), (
        f"Rust-only operations: {sorted(rust_operations.keys() - python_operations.keys())}; "
        f"FastAPI-only operations: {sorted(python_operations.keys() - rust_operations.keys())}"
    )
    conflict_operations = {
        ("PUT", "/api/audio/bluetooth/power"),
        ("PUT", "/api/audio/bluetooth/alias"),
        ("POST", "/api/audio/bluetooth/scan"),
        ("POST", "/api/audio/bluetooth/pair"),
        ("POST", "/api/audio/bluetooth/connect"),
        ("POST", "/api/audio/bluetooth/forget"),
        ("POST", "/api/audio/bluetooth/disconnect"),
        ("PUT", "/api/audio/output"),
        ("PUT", "/api/audio/output/volume"),
        ("PUT", "/api/audio/output/mute"),
    }
    for operation_key in conflict_operations:
        assert "409" in rust_operations[operation_key]["responses"], operation_key

    for operation_key, rust_operation in rust_operations.items():
        python_operation = python_operations[operation_key]
        rust_success_codes = {
            code for code in rust_operation["responses"] if code.startswith("2")
        }
        python_success_codes = {
            code for code in python_operation["responses"] if code.startswith("2")
        }
        assert rust_success_codes == python_success_codes, operation_key
        assert set(python_operation["responses"]) <= set(
            rust_operation["responses"]
        ), operation_key
        rust_body = rust_operation.get("requestBody")
        python_body = python_operation.get("requestBody")
        assert (rust_body is None) == (python_body is None), operation_key
        if rust_body is not None and python_body is not None:
            assert rust_body.get("required", True) == python_body.get("required", True), (
                operation_key
            )
            assert rust_body["content"].keys() == python_body["content"].keys(), (
                operation_key
            )
            for media_type in rust_body["content"]:
                rust_schema = rust_body["content"][media_type].get("schema")
                python_schema = python_body["content"][media_type].get("schema")
                if rust_schema is not None and python_schema is not None:
                    assert schema_signature(
                        rust_schema, rust_openapi, include_constraints=True
                    ) == schema_signature(
                        python_schema, python_openapi, include_constraints=True
                    ), operation_key
        def parameters(
            operation: dict[str, Any],
        ) -> dict[tuple[str, str, bool], dict[str, Any]]:
            return {
                (
                    parameter["in"],
                    parameter["name"],
                    parameter.get("required", False),
                ): parameter
                for parameter in operation.get("parameters", [])
            }

        rust_parameters = parameters(rust_operation)
        python_parameters = parameters(python_operation)
        assert rust_parameters.keys() == python_parameters.keys(), operation_key
        for parameter_key, rust_parameter in rust_parameters.items():
            python_parameter = python_parameters[parameter_key]
            assert parameter_schema_signature(
                rust_parameter, rust_openapi
            ) == parameter_schema_signature(python_parameter, python_openapi), (
                operation_key,
                parameter_key,
            )
        for status_code in rust_success_codes:
            rust_response = rust_operation["responses"][status_code]
            python_response = python_operation["responses"][status_code]
            rust_content = rust_response.get("content", {})
            python_content = python_response.get("content", {})
            assert rust_content.keys() == python_content.keys(), operation_key
            for media_type in rust_content:
                rust_schema = rust_content[media_type].get("schema")
                python_schema = python_content[media_type].get("schema")
                if rust_schema is not None and python_schema is not None:
                    assert schema_signature(
                        rust_schema, rust_compatibility_openapi
                    ) == schema_signature(python_schema, python_openapi), (
                        operation_key,
                        status_code,
                    )

    for schema_name in ("SystemStats", "UpdateStatus"):
        rust_schema = rust_openapi["components"]["schemas"][schema_name]
        python_schema = python_openapi["components"]["schemas"][schema_name]
        assert rust_schema["properties"].keys() == python_schema["properties"].keys(), (
            schema_name
        )
        assert set(rust_schema.get("required", [])) == set(
            python_schema.get("required", [])
        ), schema_name

    rust_install_state_schema = rust_openapi["components"]["schemas"]["UpdateStatus"][
        "properties"
    ]["install_state"]
    rust_install_state_name = rust_install_state_schema["$ref"].rsplit("/", 1)[-1]
    rust_install_states = rust_openapi["components"]["schemas"][rust_install_state_name][
        "enum"
    ]
    python_install_states = python_openapi["components"]["schemas"]["UpdateStatus"][
        "properties"
    ]["install_state"]["enum"]
    assert rust_install_states == python_install_states


def _read_fresh_audio_telemetry(websocket: Any, *, rust: bool) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if rust:
            payload = websocket.recv(timeout=max(0.05, deadline - time.monotonic()))
            telemetry = json.loads(payload)
        else:
            telemetry = websocket.receive_json()
        if isinstance(telemetry, dict) and telemetry.get("fresh") is True:
            return telemetry
    raise AssertionError("active demo audio did not produce a fresh telemetry frame")


def _soloist_fixture_handler(
    websocket: Any, observed_commands: queue.Queue[dict[str, Any]]
) -> None:
    websocket.send(
        json.dumps(
            {
                "type": "auth_state",
                "logged_in": True,
                "is_active": False,
                "device_name": "Soloist fixture",
            }
        )
    )
    playback_state = {
        "type": "playback_state",
        "status": "playing",
        "position": {"position_ms": 4200, "speed": 0},
        "volume": 68,
        "options": {"shuffle": True, "repeat": "context"},
        "is_active": False,
        "item": {
            "uri": "spotify:track:parity-fixture",
            "decorations": {
                "identity": {"name": "Parity fixture"},
                "visual_identity": {
                    "cover": [{"url": "https://example.invalid/cover.jpg"}]
                },
                "playback": {"duration_ms": 180000},
                "parent": {
                    "entity": {
                        "decorations": {"identity": {"name": "Parity album"}}
                    }
                },
                "creators": [
                    {
                        "entity": {
                            "decorations": {"identity": {"name": "Parity artist"}}
                        }
                    }
                ],
            },
        },
    }
    repeat_state = {"track": False, "context": True}
    try:
        while True:
            message = websocket.recv()
            if message is None:
                return
            command = json.loads(message)
            action = command.get("command")
            if action == "get_state":
                websocket.send(json.dumps(playback_state))
                continue
            observed_commands.put(command)
            websocket.send(
                json.dumps({"type": "command_result", "command": action})
            )
            if action == "pause":
                websocket.send(
                    json.dumps({"type": "playback_changed", "status": "paused"})
                )
            elif action == "play":
                websocket.send(
                    json.dumps({"type": "playback_changed", "status": "playing"})
                )
            elif action == "set_volume":
                websocket.send(
                    json.dumps(
                        {"type": "volume_changed", "volume": command["volume"]}
                    )
                )
            elif action == "seek":
                websocket.send(
                    json.dumps(
                        {
                            "type": "position_sync",
                            "position": {
                                "position_ms": command["position_ms"],
                                "speed": 0,
                            },
                        }
                    )
                )
            elif action == "set_shuffle":
                websocket.send(
                    json.dumps(
                        {
                            "type": "options_changed",
                            "options": {"shuffle": command["enabled"]},
                        }
                    )
                )
            elif action in {"set_repeat_track", "set_repeat_context"}:
                repeat_state[action.removeprefix("set_repeat_")] = command["enabled"]
                repeat = (
                    "track"
                    if repeat_state["track"]
                    else "context"
                    if repeat_state["context"]
                    else "off"
                )
                websocket.send(
                    json.dumps(
                        {"type": "options_changed", "options": {"repeat": repeat}}
                    )
                )
    except ConnectionClosed:
        return


def _wait_for_spotify_state(
    client: TestClient | str, *, status: str, volume: int
) -> dict[str, Any]:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response_status, body = _request(client, "GET", "/api/audio/spotify")
        if (
            response_status == 200
            and body.get("status") == status
            and body.get("volume") == volume
        ):
            return body
        time.sleep(0.05)
    raise AssertionError(
        f"Spotify state did not reach status={status!r}, volume={volume}"
    )


def _assert_spotify_control_parity(
    clients: tuple[TestClient | str, TestClient | str],
    observed_commands: queue.Queue[dict[str, Any]],
    action: str,
    expected_commands: list[dict[str, Any]],
    value: float | bool | str | None = None,
) -> None:
    request_body: dict[str, Any] = {"action": action}
    if value is not None:
        request_body["value"] = value
    for client in clients:
        status, body = _request(
            client, "POST", "/api/audio/spotify/control", request_body
        )
        assert status == 200
        _assert_response_matches_contract(body, SpotifyStatusResponse)
    expected = expected_commands * len(clients)
    actual = [observed_commands.get(timeout=2) for _ in expected]
    assert actual == expected, f"{action} commands: {actual!r}"


def _assert_dashboard_snapshot_parity(python_body: Any, rust_body: Any) -> None:
    _assert_response_matches_contract(python_body, DashboardState)
    assert isinstance(rust_body, dict)
    selector_diagnostics = rust_body.get("selector_diagnostics")
    assert isinstance(selector_diagnostics, list)
    rust_compatibility_body = {
        field: value for field, value in rust_body.items() if field != "selector_diagnostics"
    }
    _assert_response_matches_contract(rust_compatibility_body, DashboardState)
    stable_fields = (
        "running",
        "mode",
        "solid_color",
        "animation",
        "brightness",
        "blackout",
        "music_active",
        "configured_audio_source",
        "active_audio_source",
        "audio_monitoring",
    )
    for field in stable_fields:
        assert rust_body[field] == python_body[field], (
            f"state websocket {field}: Python={python_body[field]!r}, "
            f"Rust={rust_body[field]!r}"
        )


def _preview_pixels(packet: bytes) -> tuple[bytes, ...]:
    magic, version, flags, _sequence, output_count = PREVIEW_HEADER.unpack_from(packet)
    assert magic == PREVIEW_MAGIC
    assert version == PREVIEW_VERSION
    assert flags == 0
    assert output_count <= PREVIEW_MAX_OUTPUTS

    offset = PREVIEW_HEADER.size
    pixel_payloads = []
    for _ in range(output_count):
        (pixel_count,) = PREVIEW_OUTPUT_HEADER.unpack_from(packet, offset)
        offset += PREVIEW_OUTPUT_HEADER.size
        assert pixel_count <= PREVIEW_MAX_PIXELS
        payload_end = offset + pixel_count * 4
        assert payload_end <= len(packet)
        pixel_payloads.append(packet[offset:payload_end])
        offset = payload_end
    assert offset == len(packet)
    return tuple(pixel_payloads)


def _read_stable_preview(socket: Any) -> bytes:
    deadline = time.monotonic() + 3
    previous_pixels: tuple[bytes, ...] | None = None
    stable_frames = 0
    while time.monotonic() < deadline:
        if hasattr(socket, "receive_bytes"):
            packet = socket.receive_bytes()
        else:
            packet = socket.recv(timeout=max(0.05, deadline - time.monotonic()))
        assert isinstance(packet, bytes)
        pixels = _preview_pixels(packet)
        if pixels == previous_pixels:
            stable_frames += 1
            if stable_frames >= 2:
                return packet
        else:
            previous_pixels = pixels
            stable_frames = 0
    raise AssertionError("preview frame did not stabilize after selecting solid color")


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
        python_update_status_file = root / "python-update-status.json"
        rust_update_status_file = root / "rust-update-status.json"
        log_path = root / "rust-web.log"
        port = _port()
        observed_spotify_commands: queue.Queue[dict[str, Any]] = queue.Queue()
        soloist_server = websocket_serve(
            lambda websocket: _soloist_fixture_handler(
                websocket, observed_spotify_commands
            ),
            "127.0.0.1",
            0,
            ping_interval=None,
        )
        soloist_port = soloist_server.socket.getsockname()[1]
        soloist_thread = threading.Thread(
            target=soloist_server.serve_forever, daemon=True
        )
        soloist_thread.start()
        soloist_uri = f"ws://127.0.0.1:{soloist_port}"
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
                env={
                    **os.environ,
                    "LUMI_UPDATE_STATE_FILE": str(rust_update_status_file),
                    "LUMI_PROJECT_DIR": str(root / "rust-project"),
                    "LUMI_SPOTIFY_WS": soloist_uri,
                    "LUMISTRIPE_ENABLE_SPOTIFY": "true",
                },
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

                def runtime_factory(settings: RuntimeSettings) -> LumiStripeRuntime:
                    return LumiStripeRuntime(
                        settings,
                        spotify_manager=SpotifyClient(soloist_uri, enabled=True),
                    )

                app = create_app(
                    RuntimeSettings(
                        pixels=8,
                        settings_file=python_settings,
                        spotify_enabled=True,
                    ),
                    runtime_factory=runtime_factory,
                    update_state_file=python_update_status_file,
                    project_dir=root / "python-project",
                )
                with TestClient(app) as python_client:
                    stripe_topology_request: dict[str, Any] | None = None
                    exact_cases = [
                        ("GET", "/api/health", None),
                        ("GET", "/api/animations", None),
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
                        ("PUT", "/api/startup", {"restore_last_state": "yes"}),
                        ("GET", "/api/startup", None),
                        ("GET", "/api/transitions/settings", None),
                        (
                            "GET",
                            "/api/sequencer/settings",
                            None,
                        ),
                        ("GET", "/api/sequencer/sequences", None),
                        ("GET", "/api/auth/status", None),
                        ("POST", "/api/auth/logout", None),
                        ("GET", "/api/auth/status", None),
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

                    rust_openapi_path = (
                        Path(__file__).parents[1] / "frontend" / "openapi.json"
                    )
                    rust_openapi = json.loads(
                        rust_openapi_path.read_text(encoding="utf-8")
                    )
                    concrete_path_values = {
                        "animation_name": "rainbow_cycle",
                        "session_id": "missing-session",
                        "sequence_id": "missing-sequence",
                    }
                    for path, path_item in rust_openapi["paths"].items():
                        concrete_path = path
                        for name, value in concrete_path_values.items():
                            concrete_path = concrete_path.replace(
                                "{" + name + "}", value
                            )
                        for method, operation in path_item.items():
                            if (
                                method.lower() not in {
                                    "post",
                                    "put",
                                    "patch",
                                    "delete",
                                }
                                or "requestBody" not in operation
                            ):
                                continue
                            python_status, python_body = _request(
                                python_client, method.upper(), concrete_path, []
                            )
                            rust_status, rust_body = _request(
                                rust_client, method.upper(), concrete_path, []
                            )
                            assert python_status == rust_status == 422, (
                                f"invalid JSON body {method.upper()} {concrete_path}: "
                                f"Python={python_status} {python_body!r}; "
                                f"Rust={rust_status} {rust_body!r}"
                            )
                            assert python_body == rust_body, (
                                f"invalid JSON body {method.upper()} {concrete_path}: "
                                f"Python={python_body!r}; Rust={rust_body!r}"
                            )

                    # These services expose live host/provider state, so their
                    # values can legitimately differ between the two process
                    # snapshots. Compare HTTP semantics and validate both
                    # payloads against the same strict FastAPI contract.
                    live_status_cases: tuple[tuple[str, type[BaseModel]], ...] = (
                        ("/api/system/stats", SystemStats),
                        ("/api/system/update", UpdateStatus),
                        ("/api/network/status", NetworkStatusResponse),
                        ("/api/audio/spotify", SpotifyStatusResponse),
                        ("/api/audio/bluetooth", BluetoothStatusResponse),
                    )
                    for path, contract in live_status_cases:
                        python_status, python_body = _request(python_client, "GET", path)
                        rust_status, rust_body = _request(
                            rust_client, "GET", path, timeout_seconds=8
                        )
                        assert rust_status == python_status, (
                            f"GET {path}: Python {python_status}; Rust {rust_status}; "
                            f"Rust response {rust_body!r}"
                        )
                        if python_status == 200:
                            _assert_response_matches_contract(python_body, contract)
                            _assert_response_matches_contract(rust_body, contract)
                        else:
                            assert isinstance(python_body, dict)
                            assert isinstance(rust_body, dict)
                            assert set(python_body) == set(rust_body) == {"detail"}

                    python_spotify = _wait_for_spotify_state(
                        python_client, status="playing", volume=68
                    )
                    rust_spotify = _wait_for_spotify_state(
                        rust_client, status="playing", volume=68
                    )
                    _assert_response_matches_contract(
                        python_spotify, SpotifyStatusResponse
                    )
                    _assert_response_matches_contract(
                        rust_spotify, SpotifyStatusResponse
                    )
                    for field in (
                        "configured",
                        "connected",
                        "logged_in",
                        "is_active",
                        "device_name",
                        "status",
                        "track",
                        "duration_ms",
                        "position_ms",
                        "volume",
                        "shuffle",
                        "repeat",
                    ):
                        assert rust_spotify[field] == python_spotify[field], (
                            f"Spotify status {field}: Python={python_spotify[field]!r}, "
                            f"Rust={rust_spotify[field]!r}"
                        )

                    spotify_clients = (python_client, rust_client)
                    _assert_spotify_control_parity(
                        spotify_clients,
                        observed_spotify_commands,
                        "pause",
                        [{"type": "command", "command": "pause"}],
                    )
                    python_paused = _wait_for_spotify_state(
                        python_client, status="paused", volume=68
                    )
                    rust_paused = _wait_for_spotify_state(
                        rust_client, status="paused", volume=68
                    )
                    assert rust_paused["status"] == python_paused["status"]

                    _assert_spotify_control_parity(
                        spotify_clients,
                        observed_spotify_commands,
                        "play",
                        [{"type": "command", "command": "play"}],
                    )
                    python_playing = _wait_for_spotify_state(
                        python_client, status="playing", volume=68
                    )
                    rust_playing = _wait_for_spotify_state(
                        rust_client, status="playing", volume=68
                    )
                    assert rust_playing["status"] == python_playing["status"]

                    for action, command in (
                        ("skip_next", "skip_next"),
                        ("skip_prev", "skip_prev"),
                    ):
                        _assert_spotify_control_parity(
                            spotify_clients,
                            observed_spotify_commands,
                            action,
                            [{"type": "command", "command": command}],
                        )

                    _assert_spotify_control_parity(
                        spotify_clients,
                        observed_spotify_commands,
                        "seek",
                        [
                            {
                                "type": "command",
                                "command": "seek",
                                "position_ms": 12345,
                            }
                        ],
                        value=12345,
                    )
                    _assert_spotify_control_parity(
                        spotify_clients,
                        observed_spotify_commands,
                        "set_volume",
                        [
                            {
                                "type": "command",
                                "command": "set_volume",
                                "volume": 73,
                            }
                        ],
                        value=73,
                    )
                    python_spotify = _wait_for_spotify_state(
                        python_client, status="playing", volume=73
                    )
                    rust_spotify = _wait_for_spotify_state(
                        rust_client, status="playing", volume=73
                    )
                    assert rust_spotify["volume"] == python_spotify["volume"] == 73

                    _assert_spotify_control_parity(
                        spotify_clients,
                        observed_spotify_commands,
                        "set_shuffle",
                        [
                            {
                                "type": "command",
                                "command": "set_shuffle",
                                "enabled": False,
                            }
                        ],
                        value=False,
                    )
                    python_spotify = _wait_for_spotify_state(
                        python_client, status="playing", volume=73
                    )
                    rust_spotify = _wait_for_spotify_state(
                        rust_client, status="playing", volume=73
                    )
                    assert rust_spotify["shuffle"] == python_spotify["shuffle"] is False

                    _assert_spotify_control_parity(
                        spotify_clients,
                        observed_spotify_commands,
                        "set_repeat",
                        [
                            {
                                "type": "command",
                                "command": "set_repeat_track",
                                "enabled": True,
                            },
                            {
                                "type": "command",
                                "command": "set_repeat_context",
                                "enabled": False,
                            },
                        ],
                        value="track",
                    )
                    python_spotify = _wait_for_spotify_state(
                        python_client, status="playing", volume=73
                    )
                    rust_spotify = _wait_for_spotify_state(
                        rust_client, status="playing", volume=73
                    )
                    assert rust_spotify["repeat"] == python_spotify["repeat"] == "track"
                    for field in (
                        "track",
                        "duration_ms",
                        "position_ms",
                        "shuffle",
                        "repeat",
                        "volume",
                    ):
                        assert rust_spotify[field] == python_spotify[field], (
                            f"Spotify status {field}: Python={python_spotify[field]!r}, "
                            f"Rust={rust_spotify[field]!r}"
                        )
                    spotify_error_cases = (
                        (
                            {"action": "pause", "value": "unexpected"},
                            "pause does not accept a value",
                        ),
                        (
                            {"action": "seek", "value": "123"},
                            "seek position requires a number",
                        ),
                        (
                            {"action": "seek", "value": -1},
                            "seek position must not be negative",
                        ),
                        (
                            {"action": "set_volume"},
                            "volume requires a number",
                        ),
                        (
                            {"action": "set_volume", "value": 101},
                            "volume must be between 0 and 100",
                        ),
                        (
                            {"action": "set_shuffle", "value": 1},
                            "shuffle requires a boolean value",
                        ),
                        (
                            {"action": "set_repeat", "value": "random"},
                            "repeat must be off, context, or track",
                        ),
                    )
                    for request_body, detail in spotify_error_cases:
                        python_error = _request(
                            python_client,
                            "POST",
                            "/api/audio/spotify/control",
                            request_body,
                        )
                        rust_error = _request(
                            rust_client,
                            "POST",
                            "/api/audio/spotify/control",
                            request_body,
                        )
                        assert python_error == rust_error == (409, {"detail": detail})
                    assert observed_spotify_commands.empty()

                    python_update_status = _request(
                        python_client, "GET", "/api/system/update"
                    )
                    rust_update_status = _request(
                        rust_client, "GET", "/api/system/update"
                    )
                    assert rust_update_status == python_update_status
                    update_failure_fixture = (
                        Path(__file__).resolve().parents[3]
                        / "rust"
                        / "test-fixtures"
                        / "system-update-api-install-failures-v1.json"
                    )
                    update_failure_cases = json.loads(
                        update_failure_fixture.read_text(encoding="utf-8")
                    )
                    for case in update_failure_cases:
                        serialized_status = json.dumps(case["status"])
                        python_update_status_file.write_text(
                            serialized_status, encoding="utf-8"
                        )
                        rust_update_status_file.write_text(
                            serialized_status, encoding="utf-8"
                        )
                        python_result = _request(
                            python_client, "POST", "/api/system/update/install"
                        )
                        rust_result = _request(
                            rust_client, "POST", "/api/system/update/install"
                        )
                        expected = (
                            case["status_code"],
                            {"detail": case["detail"]},
                        )
                        assert python_result == rust_result == expected, case["name"]
                        if case["name"] == "unsupported":
                            python_check = _request(
                                python_client, "POST", "/api/system/update/check"
                            )
                            rust_check = _request(
                                rust_client, "POST", "/api/system/update/check"
                            )
                            assert python_check == rust_check == expected

                    invalid_cases = (
                        ("PUT", "/api/music-idle", []),
                        ("PUT", "/api/mode", {"mode": "unknown"}),
                        (
                            "PUT",
                            "/api/mode",
                            {"mode": "dynamic", "music_recognition_enabled": "invalid"},
                        ),
                        (
                            "PUT",
                            "/api/mode",
                            {"mode": "solid", "color": "not-a-color"},
                        ),
                        ("PUT", "/api/brightness", {"brightness": 1.01}),
                        ("PUT", "/api/animation", {"name": ""}),
                        ("POST", "/api/blackout", {"enabled": "not-a-bool"}),
                        ("POST", "/api/auth/pair", {"code": 1234}),
                        ("PUT", "/api/startup", {"restore_last_state": "invalid"}),
                        (
                            "PUT",
                            "/api/stripes",
                            {"layout": "mirrored", "outputs": [], "power_budget_enabled": "invalid"},
                        ),
                        (
                            "PUT",
                            "/api/stripes",
                            {
                                "layout": "mirrored",
                                "outputs": [
                                    {"id": "main", "name": "Main", "pixels": 8, "reversed": "invalid"}
                                ],
                            },
                        ),
                        (
                            "PUT",
                            "/api/transitions/settings",
                            {"enabled": True, "duration_ms": 99, "effect": "wipe"},
                        ),
                        (
                            "PUT",
                            "/api/sequencer/settings",
                            {"switch_quantization": "unknown", "bars_per_phrase": 8},
                        ),
                        (
                            "POST",
                            "/api/sequencer/sequences",
                            {"id": "invalid", "name": "Invalid", "repeat": True, "steps": []},
                        ),
                        (
                            "POST",
                            "/api/sequencer/sequences",
                            {
                                "id": "invalid-animation",
                                "name": "Invalid Animation",
                                "repeat": True,
                                "steps": [
                                    {
                                        "animation_name": "not-a-dynamic-animation",
                                        "duration_bars": 2,
                                    }
                                ],
                            },
                        ),
                        (
                            "POST",
                            "/api/sequencer/sequences",
                            {
                                "id": "ineligible-effect",
                                "name": "Ineligible Effect",
                                "repeat": True,
                                "steps": [
                                    {
                                        "animation_name": "bass_drop",
                                        "duration_bars": 2,
                                    }
                                ],
                            },
                        ),
                        (
                            "PUT",
                            "/api/stripes",
                            {"layout": "invalid", "outputs": []},
                        ),
                        (
                            "PUT",
                            "/api/stripes",
                            {
                                "layout": "mirrored",
                                "outputs": [
                                    {
                                        "id": "small",
                                        "name": "Small",
                                        "pixels": 2,
                                        "zone_count": 3,
                                    }
                                ],
                            },
                        ),
                        (
                            "PUT",
                            "/api/stripes",
                            {
                                "layout": "mirrored",
                                "outputs": [
                                    {
                                        "id": "small",
                                        "name": "Small",
                                        "pixels": 2,
                                        "zone_pattern": "shift",
                                    }
                                ],
                            },
                        ),
                        (
                            "PUT",
                            "/api/stripes",
                            {
                                "layout": "mirrored",
                                "power_budget_enabled": True,
                                "power_budget_watts": None,
                                "outputs": [],
                            },
                        ),
                        (
                            "PUT",
                            "/api/stripes",
                            {
                                "layout": "independent",
                                "outputs": [
                                    {"id": "same", "name": "One", "pixels": 8},
                                    {"id": "same", "name": "Two", "pixels": 8},
                                ],
                            },
                        ),
                        (
                            "PUT",
                            "/api/stripes",
                            {
                                "layout": "independent",
                                "outputs": [
                                    {
                                        "id": "left",
                                        "name": "Left",
                                        "pixels": 8,
                                        "spi_device": "/dev/spidev0.0",
                                    },
                                    {
                                        "id": "right",
                                        "name": "Right",
                                        "pixels": 8,
                                        "spi_device": "/dev/spidev0.0",
                                    },
                                ],
                            },
                        ),
                        (
                            "PUT",
                            "/api/stripes",
                            {
                                "layout": "independent",
                                "outputs": [
                                    {
                                        "id": "left",
                                        "name": "Left",
                                        "pixels": 8,
                                        "backend": "gpio",
                                        "data_pin": 10,
                                        "clock_pin": 11,
                                    },
                                    {
                                        "id": "right",
                                        "name": "Right",
                                        "pixels": 8,
                                        "backend": "gpio",
                                        "data_pin": 10,
                                        "clock_pin": 12,
                                    },
                                ],
                            },
                        ),
                        (
                            "PUT",
                            "/api/stripes",
                            {
                                "layout": "mirrored",
                                "shared_supply_enabled": True,
                                "supply_capacity_watts": 0,
                                "outputs": [],
                            },
                        ),
                        (
                            "POST",
                            "/api/stripes/test",
                            {"stripe_id": "main", "pattern": "rainbow"},
                        ),
                        (
                            "POST",
                            "/api/calibration/session",
                            {"output_index": -1},
                        ),
                        (
                            "POST",
                            "/api/calibration/session/invalid/finish",
                            {"save": "invalid"},
                        ),
                        (
                            "PUT",
                            "/api/calibration/session/invalid",
                            {"red": -1, "green": 0, "blue": 0, "pattern": "ultraviolet"},
                        ),
                        (
                            "PUT",
                            "/api/animations/rainbow_cycle/settings",
                            {"values": []},
                        ),
                        ("PUT", "/api/audio/source", {"source": "cassette"}),
                        ("PUT", "/api/audio/sync-offset", {"offset_ms": 501}),
                        ("POST", "/api/audio/sync-calibration", {"active": "invalid"}),
                        (
                            "POST",
                            "/api/audio/calibration/session",
                            {"device": "microphone", "two_step": "invalid"},
                        ),
                        (
                            "POST",
                            "/api/audio/calibration/session/invalid/finish",
                            {"apply": "invalid"},
                        ),
                        ("PUT", "/api/audio/device", {"device": ""}),
                        (
                            "PUT",
                            "/api/audio/settings",
                            {"device": "", "settings": {}},
                        ),
                        ("POST", "/api/audio/settings/reset", {"device": ""}),
                        ("PUT", "/api/audio/output", {"selector": ""}),
                        (
                            "PUT",
                            "/api/audio/output/mute",
                            {"selector": "", "muted": False},
                        ),
                        ("PUT", "/api/audio/bluetooth/power", {"powered": 3}),
                        ("PUT", "/api/audio/bluetooth/alias", {"alias": ""}),
                        (
                            "POST",
                            "/api/audio/bluetooth/pair",
                            {"address": 7},
                        ),
                        (
                            "POST",
                            "/api/audio/bluetooth/connect",
                            {"address": 7},
                        ),
                        (
                            "POST",
                            "/api/audio/bluetooth/disconnect",
                            {"address": 7},
                        ),
                        (
                            "POST",
                            "/api/audio/bluetooth/forget",
                            {"address": 7},
                        ),
                        (
                            "POST",
                            "/api/network/scan",
                            {"interface": "x" * 16},
                        ),
                        (
                            "POST",
                            "/api/network/connect",
                            {
                                "interface": "wlan0",
                                "ssid": "x" * 33,
                                "password": "correct-horse",
                            },
                        ),
                        (
                            "POST",
                            "/api/network/connect",
                            {
                                "interface": "wlan0",
                                "ssid": "Party Wi-Fi",
                                "password": "x" * 64,
                            },
                        ),
                        (
                            "PUT",
                            "/api/audio/output/volume",
                            {"selector": "default", "volume": 1.5},
                        ),
                        (
                            "POST",
                            "/api/audio/spotify/control",
                            {"action": "launch"},
                        ),
                        ("PUT", "/api/sequencer/active", {"sequence_id": []}),
                        ("PUT", "/api/sequencer/sequences/invalid", {}),
                    )
                    for method, path, body in invalid_cases:
                        python_status, python_body = _request(
                            python_client, method, path, body
                        )
                        rust_status, rust_body = _request(
                            rust_client, method, path, body
                        )
                        assert rust_status == python_status == 422, (
                            f"{method} {path}: Python {python_status}; "
                            f"Rust {rust_status}"
                        )
                        assert type(rust_body["detail"]) is type(
                            python_body["detail"]
                        ), (
                            f"{method} {path}: Python validation detail is "
                            f"{type(python_body['detail']).__name__}; Rust is "
                            f"{type(rust_body['detail']).__name__}"
                        )
                        assert rust_body == python_body, (
                            f"{method} {path} validation details differ: "
                            f"Python={python_body!r}; Rust={rust_body!r}"
                        )
                        if (method, path) in {
                            ("POST", "/api/stripes/test"),
                            ("PUT", "/api/audio/source"),
                            ("PUT", "/api/audio/device"),
                            ("PUT", "/api/audio/settings"),
                            ("POST", "/api/audio/settings/reset"),
                            ("POST", "/api/audio/calibration/session"),
                            ("POST", "/api/network/scan"),
                            ("PUT", "/api/audio/output"),
                            ("PUT", "/api/audio/output/mute"),
                        } or (
                            method == "POST"
                            and path == "/api/network/connect"
                            and (
                                len(body.get("ssid", "")) > 32
                                or len(body.get("password", "")) > 63
                            )
                        ):
                            assert rust_body == python_body, (
                                f"{method} {path} validation details differ: "
                                f"Python={python_body!r}; Rust={rust_body!r}"
                            )
                        pydantic_bool_field = {
                            ("PUT", "/api/mode"): "music_recognition_enabled",
                            ("POST", "/api/blackout"): "enabled",
                            ("PUT", "/api/startup"): "restore_last_state",
                            ("PUT", "/api/stripes"): "power_budget_enabled",
                            ("POST", "/api/audio/sync-calibration"): "active",
                            ("POST", "/api/audio/calibration/session"): "two_step",
                            (
                                "POST",
                                "/api/audio/calibration/session/invalid/finish",
                            ): "apply",
                            ("PUT", "/api/audio/bluetooth/power"): "powered",
                        }.get((method, path))
                        if pydantic_bool_field is not None and (
                            body.get(pydantic_bool_field) is not None
                        ):
                            assert rust_body == python_body, (
                                f"{method} {path} boolean validation details differ: "
                                f"Python={python_body!r}; Rust={rust_body!r}"
                            )
                        if method == "PUT" and path == "/api/stripes" and any(
                            output.get("reversed") == "invalid"
                            for output in body.get("outputs", [])
                        ):
                            assert rust_body == python_body, (
                                f"{method} {path} nested boolean validation details differ: "
                                f"Python={python_body!r}; Rust={rust_body!r}"
                            )
                        if (
                            method == "POST"
                            and path == "/api/network/connect"
                            and len(body.get("password", "")) > 63
                        ):
                            assert body["password"] not in json.dumps(rust_body), (
                                "Rust validation errors must not echo Wi-Fi passwords"
                            )

                    invalid_bluetooth_address = {
                        "address": "AA:BB:CC:DD:EE:FG",
                    }
                    python_bluetooth_error = _request(
                        python_client,
                        "POST",
                        "/api/audio/bluetooth/pair",
                        invalid_bluetooth_address,
                    )
                    rust_bluetooth_error = _request(
                        rust_client,
                        "POST",
                        "/api/audio/bluetooth/pair",
                        invalid_bluetooth_address,
                    )
                    assert rust_bluetooth_error == python_bluetooth_error == (
                        409,
                        {"detail": "Bluetooth address must look like AA:BB:CC:DD:EE:FF"},
                    )
                    invalid_bluetooth_alias = {"alias": "Bad\nName"}
                    python_alias_error = _request(
                        python_client,
                        "PUT",
                        "/api/audio/bluetooth/alias",
                        invalid_bluetooth_alias,
                    )
                    rust_alias_error = _request(
                        rust_client,
                        "PUT",
                        "/api/audio/bluetooth/alias",
                        invalid_bluetooth_alias,
                    )
                    assert rust_alias_error == python_alias_error == (
                        409,
                        {"detail": "Bluetooth name cannot contain control characters"},
                    )

                    invalid_identity = {
                        "application_name": " ",
                        "spotify_device_name": "LumiStripe",
                    }
                    python_identity_error = _request(
                        python_client, "PUT", "/api/identity", invalid_identity
                    )
                    rust_identity_error = _request(
                        rust_client, "PUT", "/api/identity", invalid_identity
                    )
                    assert rust_identity_error == python_identity_error == (
                        409,
                        {"detail": "application name must be 1 to 64 printable characters"},
                    )

                    python_audio_status, _ = _request(
                        python_client,
                        "PUT",
                        "/api/audio/source",
                        {"source": "demo"},
                    )
                    rust_audio_status, _ = _request(
                        rust_client,
                        "PUT",
                        "/api/audio/source",
                        {"source": "demo"},
                    )
                    assert rust_audio_status == python_audio_status == 200

                    python_mode_status, _ = _request(
                        python_client,
                        "PUT",
                        "/api/mode",
                        {"mode": "dynamic"},
                    )
                    rust_mode_status, _ = _request(
                        rust_client,
                        "PUT",
                        "/api/mode",
                        {"mode": "dynamic"},
                    )
                    assert rust_mode_status == python_mode_status == 200

                    rust_websocket_url = rust_client.replace("http://", "ws://", 1)
                    with (
                        python_client.websocket_connect("/ws/state") as python_socket,
                        websocket_connect(
                            f"{rust_websocket_url}/ws/state",
                            open_timeout=3,
                            close_timeout=1,
                            proxy=None,
                        ) as rust_socket,
                    ):
                        python_initial = python_socket.receive_json()
                        rust_initial = json.loads(rust_socket.recv(timeout=3))
                        _assert_dashboard_snapshot_parity(python_initial, rust_initial)

                        python_status, python_brightness = _request(
                            python_client,
                            "PUT",
                            "/api/brightness",
                            {"brightness": 0.25},
                        )
                        rust_status, rust_brightness = _request(
                            rust_client,
                            "PUT",
                            "/api/brightness",
                            {"brightness": 0.25},
                        )
                        assert rust_status == python_status == 200
                        assert rust_brightness["brightness"] == (
                            python_brightness["brightness"]
                        )
                        python_changed = python_socket.receive_json()
                        while (
                            python_changed["revision"] < python_brightness["revision"]
                            or python_changed["brightness"]
                            != python_brightness["brightness"]
                        ):
                            python_changed = python_socket.receive_json()
                        rust_changed = json.loads(rust_socket.recv(timeout=3))
                        while (
                            rust_changed["revision"] < rust_brightness["revision"]
                            or rust_changed["brightness"] != rust_brightness["brightness"]
                        ):
                            rust_changed = json.loads(rust_socket.recv(timeout=3))

                    _assert_dashboard_snapshot_parity(python_changed, rust_changed)
                    assert python_changed["brightness"] == 0.25
                    assert python_changed["revision"] > python_initial["revision"]
                    assert rust_changed["revision"] > rust_initial["revision"]

                    with (
                        python_client.websocket_connect("/ws/state") as python_socket,
                        websocket_connect(
                            f"{rust_websocket_url}/ws/state",
                            open_timeout=3,
                            close_timeout=1,
                            proxy=None,
                        ) as rust_socket,
                    ):
                        python_reconnected = python_socket.receive_json()
                        rust_reconnected = json.loads(rust_socket.recv(timeout=3))
                    _assert_dashboard_snapshot_parity(python_reconnected, rust_reconnected)
                    assert python_reconnected["brightness"] == rust_reconnected["brightness"] == 0.25

                    with python_client.websocket_connect("/ws/audio") as python_socket:
                        python_audio = _read_fresh_audio_telemetry(
                            python_socket, rust=False
                        )
                    with websocket_connect(
                        f"{rust_websocket_url}/ws/audio",
                        open_timeout=3,
                        close_timeout=1,
                        proxy=None,
                    ) as rust_socket:
                        rust_audio = _read_fresh_audio_telemetry(
                            rust_socket, rust=True
                        )
                    _assert_response_matches_contract(python_audio, AudioTelemetry)
                    _assert_response_matches_contract(rust_audio, AudioTelemetry)
                    assert len(python_audio["bands"]) == len(rust_audio["bands"]) == 8
                    for backend, telemetry in (
                        ("Python", python_audio),
                        ("Rust", rust_audio),
                    ):
                        assert telemetry["sequence"] > 0, (
                            f"{backend} demo audio sequence should advance"
                        )
                        assert telemetry["processed_level"] > 0.0, (
                            f"{backend} demo audio should produce a non-zero level"
                        )
                        assert max(telemetry["bands"]) > 0.0, (
                            f"{backend} demo audio should produce non-zero bands"
                        )
                        expected_frame = demo_snapshot(
                            telemetry["sequence"] - 1
                        ).frame
                        assert telemetry["processed_level"] == pytest.approx(
                            expected_frame.rms, abs=1e-6
                        ), f"{backend} demo audio level should match the shared fixture"
                        assert telemetry["bands"] == pytest.approx(
                            expected_frame.bands, abs=1e-6
                        ), f"{backend} demo bands should match the shared fixture"
                        assert telemetry["beat"] is expected_frame.beat
                    for field in (
                        "configured_input_noise_floor",
                        "configured_noise_floor",
                        "sync_offset_ms",
                        "sync_route",
                    ):
                        assert rust_audio[field] == python_audio[field], (
                            f"audio telemetry {field}: Python={python_audio[field]!r}, "
                            f"Rust={rust_audio[field]!r}"
                        )
                    python_mode_status, python_mode = _request(
                        python_client,
                        "PUT",
                        "/api/mode",
                        {"mode": "solid", "color": "#123ABC"},
                    )
                    rust_mode_status, rust_mode = _request(
                        rust_client,
                        "PUT",
                        "/api/mode",
                        {"mode": "solid", "color": "#123ABC"},
                    )
                    assert rust_mode_status == python_mode_status == 200
                    assert rust_mode["solid_color"] == python_mode["solid_color"]

                    with python_client.websocket_connect("/ws/preview") as python_socket:
                        python_preview = _read_stable_preview(python_socket)
                    with websocket_connect(
                        f"{rust_websocket_url}/ws/preview",
                        open_timeout=3,
                        close_timeout=1,
                        proxy=None,
                    ) as rust_socket:
                        rust_preview = _read_stable_preview(rust_socket)
                    assert isinstance(rust_preview, bytes)
                    assert _preview_pixels(python_preview) == _preview_pixels(rust_preview)

                    assert stripe_topology_request is not None
                    python_status, python_body = _request(
                        python_client,
                        "PUT",
                        "/api/stripes",
                        {
                            **stripe_topology_request,
                            "power_budget_enabled": "yes",
                            "power_budget_watts": 20.0,
                            "shared_supply_enabled": "no",
                            "outputs": [
                                {**output, "reversed": "no"}
                                for output in stripe_topology_request["outputs"]
                            ],
                        },
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "PUT",
                        "/api/stripes",
                        {
                            **stripe_topology_request,
                            "power_budget_enabled": "yes",
                            "power_budget_watts": 20.0,
                            "shared_supply_enabled": "no",
                            "outputs": [
                                {**output, "reversed": "no"}
                                for output in stripe_topology_request["outputs"]
                            ],
                        },
                    )
                    assert rust_status == python_status == 200, (
                        f"topology update: Python {python_status}; Rust {rust_status} "
                        f"({rust_body.get('detail', rust_body)!r})"
                    )
                    assert rust_body["stripe_topology"]["layout"] == (
                        python_body["stripe_topology"]["layout"]
                    )
                    assert rust_body["stripe_topology"]["power_budget_enabled"] is True
                    assert python_body["stripe_topology"]["power_budget_enabled"] is True
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

                    unknown_stripe_test = {
                        "stripe_id": "missing-stripe",
                        "pattern": "blue",
                    }
                    python_unknown_stripe_test = _request(
                        python_client,
                        "POST",
                        "/api/stripes/test",
                        unknown_stripe_test,
                    )
                    rust_unknown_stripe_test = _request(
                        rust_client,
                        "POST",
                        "/api/stripes/test",
                        unknown_stripe_test,
                    )
                    assert python_unknown_stripe_test == rust_unknown_stripe_test

                    unknown_target_cases = (
                        (
                            "PUT",
                            "/api/brightness",
                            {"brightness": 0.3, "stripe_id": "missing-stripe"},
                        ),
                        (
                            "POST",
                            "/api/blackout",
                            {"enabled": True, "stripe_id": "missing-stripe"},
                        ),
                        (
                            "PUT",
                            "/api/animation",
                            {
                                "name": "rainbow_cycle",
                                "stripe_id": "missing-stripe",
                            },
                        ),
                        (
                            "PUT",
                            "/api/music-idle",
                            {
                                "mode": "solid",
                                "color": "#123456",
                                "animation": "",
                                "brightness": 0.3,
                                "stripe_id": "missing-stripe",
                            },
                        ),
                    )
                    for method, path, body in unknown_target_cases:
                        python_result = _request(
                            python_client, method, path, body
                        )
                        rust_result = _request(rust_client, method, path, body)
                        assert python_result == rust_result, (
                            f"{method} {path} with missing stripe: "
                            f"Python={python_result!r}; Rust={rust_result!r}"
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
                    python_calibration_blocked = _request(
                        python_client,
                        "PUT",
                        "/api/brightness",
                        {"brightness": 0.5},
                    )
                    rust_calibration_blocked = _request(
                        rust_client,
                        "PUT",
                        "/api/brightness",
                        {"brightness": 0.5},
                    )
                    assert python_calibration_blocked == rust_calibration_blocked
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
                    python_stale_calibration = _request(
                        python_client,
                        "POST",
                        f"/api/calibration/session/{python_session}/finish",
                        {"save": False},
                    )
                    rust_stale_calibration = _request(
                        rust_client,
                        "POST",
                        f"/api/calibration/session/{rust_session}/finish",
                        {"save": False},
                    )
                    assert python_stale_calibration == rust_stale_calibration

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
                    python_status, python_reset = _request(
                        python_client,
                        "POST",
                        "/api/animations/rainbow_cycle/settings/reset",
                    )
                    rust_status, rust_reset = _request(
                        rust_client,
                        "POST",
                        "/api/animations/rainbow_cycle/settings/reset",
                    )
                    assert rust_status == python_status == 200
                    assert rust_reset == python_reset
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

                    for idle_settings in (
                        {"mode": "animation", "animation": "missing-animation"},
                        {"mode": "animation", "animation": ""},
                    ):
                        python_idle_error = _request(
                            python_client,
                            "PUT",
                            "/api/music-idle",
                            idle_settings,
                        )
                        rust_idle_error = _request(
                            rust_client,
                            "PUT",
                            "/api/music-idle",
                            idle_settings,
                        )
                        assert python_idle_error == rust_idle_error, (
                            f"PUT /api/music-idle {idle_settings!r}: "
                            f"Python={python_idle_error!r}; Rust={rust_idle_error!r}"
                        )

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

                    sequence_error_cases = (
                        (
                            "POST",
                            "/api/sequencer/sequences",
                            sequence,
                            409,
                        ),
                        (
                            "PUT",
                            "/api/sequencer/sequences/missing-sequence",
                            {
                                "name": "Missing Sequence",
                                "repeat": True,
                                "steps": [
                                    {
                                        "animation_name": "rainbow_cycle",
                                        "duration_bars": 2,
                                    }
                                ],
                            },
                            404,
                        ),
                        (
                            "PUT",
                            "/api/sequencer/active",
                            {"sequence_id": "missing-sequence"},
                            404,
                        ),
                        (
                            "DELETE",
                            "/api/sequencer/sequences/missing-sequence",
                            None,
                            404,
                        ),
                        ("PUT", "/api/animation", {"name": "missing-animation"}, 404),
                        ("GET", "/api/animations/missing-animation/settings", None, 404),
                    )
                    for method, path, body, expected_status in sequence_error_cases:
                        python_result = _request(python_client, method, path, body)
                        rust_result = _request(rust_client, method, path, body)
                        assert python_result == rust_result, (
                            f"{method} {path}: Python={python_result!r}; "
                            f"Rust={rust_result!r}"
                        )
                        assert python_result[0] == expected_status, (
                            f"{method} {path}: {python_result!r}"
                        )

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

                    for index in range(20):
                        limited_sequence = {
                            **sequence,
                            "id": f"limit-{index}",
                            "name": f"Limit {index}",
                        }
                        python_result = _request(
                            python_client,
                            "POST",
                            "/api/sequencer/sequences",
                            limited_sequence,
                        )
                        rust_result = _request(
                            rust_client,
                            "POST",
                            "/api/sequencer/sequences",
                            limited_sequence,
                        )
                        assert python_result[0] == rust_result[0] == 200

                    limit_case = {
                        **sequence,
                        "id": "over-limit",
                        "name": "Over limit",
                    }
                    python_limit_error = _request(
                        python_client,
                        "POST",
                        "/api/sequencer/sequences",
                        limit_case,
                    )
                    rust_limit_error = _request(
                        rust_client,
                        "POST",
                        "/api/sequencer/sequences",
                        limit_case,
                    )
                    assert python_limit_error == rust_limit_error == (
                        409,
                        {"detail": "at most 20 sequences may be saved"},
                    )

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
                    python_state_status, python_state = _request(
                        python_client, "GET", "/api/state"
                    )
                    rust_state_status, rust_state = _request(
                        rust_client, "GET", "/api/state"
                    )
                    assert rust_state_status == python_state_status == 200
                    _assert_dashboard_snapshot_parity(python_state, rust_state)
                    python_audio_settings = python_body
                    rust_audio_settings = rust_body
                    _assert_response_matches_contract(
                        python_audio_settings, AudioSettingsResponse
                    )
                    _assert_response_matches_contract(
                        rust_audio_settings, AudioSettingsResponse
                    )
                    python_status, python_audio_settings = _request(
                        python_client, "GET", "/api/audio/settings"
                    )
                    rust_status, rust_audio_settings = _request(
                        rust_client, "GET", "/api/audio/settings"
                    )
                    assert rust_status == python_status == 200
                    _assert_response_matches_contract(
                        python_audio_settings, AudioSettingsResponse
                    )
                    _assert_response_matches_contract(
                        rust_audio_settings, AudioSettingsResponse
                    )
                    for field in (
                        "source",
                        "active_source",
                        "monitoring",
                        "settings",
                        "configured_noise_floor",
                        "sync_route",
                        "sync_offset_ms",
                    ):
                        assert rust_audio_settings[field] == python_audio_settings[field], (
                            f"GET /api/audio/settings {field}: "
                            f"Python={python_audio_settings[field]!r}, "
                            f"Rust={rust_audio_settings[field]!r}"
                        )

                    python_calibration_error = _request(
                        python_client,
                        "POST",
                        "/api/audio/calibration/session",
                        {"device": "USB microphone"},
                    )
                    rust_calibration_error = _request(
                        rust_client,
                        "POST",
                        "/api/audio/calibration/session",
                        {"device": "USB microphone"},
                    )
                    assert python_calibration_error[0] == 409
                    assert rust_calibration_error == python_calibration_error, (
                        "starting microphone calibration without a microphone: "
                        f"Python={python_calibration_error!r}, "
                        f"Rust={rust_calibration_error!r}"
                    )

                    missing_session_cases = (
                        (
                            "GET",
                            "/api/audio/calibration/session/missing-session",
                            None,
                            404,
                            "audio calibration session not found",
                        ),
                        (
                            "POST",
                            "/api/audio/calibration/session/missing-session/music",
                            None,
                            409,
                            "calibration is not waiting for music",
                        ),
                        (
                            "POST",
                            "/api/audio/calibration/session/missing-session/finish",
                            {"apply": False},
                            409,
                            "audio calibration session not found",
                        ),
                    )
                    for method, path, body, expected_status, expected_detail in (
                        missing_session_cases
                    ):
                        python_result = _request(python_client, method, path, body)
                        rust_result = _request(rust_client, method, path, body)
                        expected_result = (
                            expected_status,
                            {"detail": expected_detail},
                        )
                        assert python_result == rust_result == expected_result, (
                            f"{method} {path}: expected={expected_result!r}, "
                            f"Python={python_result!r}, Rust={rust_result!r}"
                        )

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
                        {"active": "no"},
                    )
                    rust_status, rust_body = _request(
                        rust_client,
                        "POST",
                        "/api/audio/sync-calibration",
                        {"active": "no"},
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

                    assert stripe_topology_request is not None
                    independent_topology = {
                        **stripe_topology_request,
                        "layout": "independent",
                    }
                    python_topology_result = _request(
                        python_client,
                        "PUT",
                        "/api/stripes",
                        independent_topology,
                    )
                    rust_topology_result = _request(
                        rust_client,
                        "PUT",
                        "/api/stripes",
                        independent_topology,
                    )
                    assert python_topology_result[0] == rust_topology_result[0] == 200
                    for method, path, body in unknown_target_cases:
                        python_result = _request(
                            python_client, method, path, body
                        )
                        rust_result = _request(rust_client, method, path, body)
                        assert python_result == rust_result, (
                            f"{method} {path} with unknown independent stripe: "
                            f"Python={python_result!r}; Rust={rust_result!r}"
                        )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                soloist_server.shutdown()
                soloist_thread.join(timeout=2)
