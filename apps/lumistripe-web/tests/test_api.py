from __future__ import annotations

from pathlib import Path

import lumistripe_web.runtime as runtime_module
import pytest
from fastapi.testclient import TestClient
from lumistripe import AudioInputDevice
from lumistripe_web.app import build_parser, create_app, main
from lumistripe_web.runtime import LumiStripeRuntime, RuntimeSettings
from starlette.websockets import WebSocketDisconnect


def test_control_api_round_trip() -> None:
    app = create_app(RuntimeSettings(pixels=8))
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}

        initial = client.get("/api/state")
        assert initial.status_code == 200
        assert initial.json()["runtime"] == "simulation"
        assert initial.json()["running"] is True
        assert initial.json()["application_version"]
        assert initial.json()["uptime_seconds"] >= 0
        assert "frame_rate" in initial.json()
        assert "audio_health" in initial.json()
        assert "last_output_at" in initial.json()
        assert "diagnostic_issues" in initial.json()

        animations = client.get("/api/animations")
        assert animations.status_code == 200
        options = animations.json()["items"]
        assert len(options) > 40
        assert {"name", "mood", "dynamic_safe"} <= options[0].keys()

        mode = client.put("/api/mode", json={"mode": "dynamic"})
        assert mode.status_code == 200
        assert mode.json()["mode"] == "dynamic"
        assert mode.json()["audio_status"] == "Using internal demo beat."

        solid = client.put("/api/mode", json={"mode": "solid", "color": "#12aBcD"})
        assert solid.status_code == 200
        assert solid.json()["mode"] == "solid"
        assert solid.json()["solid_color"] == "#12ABCD"

        brightness = client.put("/api/brightness", json={"brightness": 0.4})
        assert brightness.status_code == 200
        assert brightness.json()["brightness"] == 0.4

        blackout = client.post("/api/blackout", json={"enabled": True})
        assert blackout.status_code == 200
        assert blackout.json()["blackout"] is True

        animation = client.put(
            "/api/animation",
            json={"name": options[1]["name"]},
        )
        assert animation.status_code == 200
        assert animation.json()["animation"] == options[1]["name"]
        assert animation.json()["mode"] == "static"


def test_api_maps_validation_and_runtime_errors() -> None:
    app = create_app(RuntimeSettings(pixels=8, audio_source="off"))
    with TestClient(app) as client:
        assert client.put("/api/brightness", json={"brightness": 2}).status_code == 422
        assert (
            client.put("/api/mode", json={"mode": "solid", "color": "red"}).status_code
            == 422
        )
        assert client.put("/api/animation", json={"name": "missing"}).status_code == 404
        conflict = client.put("/api/mode", json={"mode": "dynamic"})
        assert conflict.status_code == 409


def test_audio_settings_api_and_telemetry_websocket(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        runtime_module,
        "list_input_device_details",
        lambda: [AudioInputDevice(index=2, name="USB Mic")],
    )
    app = create_app(
        RuntimeSettings(pixels=8, settings_file=tmp_path / "settings.json")
    )
    with TestClient(app) as client:
        settings = client.get("/api/audio/settings")
        assert settings.status_code == 200
        assert settings.json()["devices"][0]["name"] == "USB Mic"

        values = settings.json()["settings"]
        values["target_level"] = 0.5
        updated = client.put(
            "/api/audio/settings",
            json={"device": "2", "settings": values},
        )
        assert updated.status_code == 200
        assert updated.json()["active_device"] == "2"
        assert updated.json()["settings"]["target_level"] == 0.5

        invalid = dict(values, target_level=0.99)
        assert client.put(
            "/api/audio/settings",
            json={"device": "2", "settings": invalid},
        ).status_code == 422

        with client.websocket_connect("/ws/audio") as websocket:
            telemetry = websocket.receive_json()
            assert len(telemetry["bands"]) == 8
            assert telemetry["gate_preview"] is True


def test_calibration_api_session_lifecycle(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    app = create_app(RuntimeSettings(pixels=8, settings_file=settings_path))
    with TestClient(app) as client:
        started = client.post(
            "/api/calibration/session",
            json={"output_index": 0},
        )
        assert started.status_code == 200
        session_id = started.json()["session_id"]
        assert started.json()["state"]["calibration"]["active"] is True

        invalid = client.put(
            f"/api/calibration/session/{session_id}",
            json={"red": 256, "green": 200, "blue": 180, "pattern": "white"},
        )
        assert invalid.status_code == 422

        updated = client.put(
            f"/api/calibration/session/{session_id}",
            json={"red": 240, "green": 200, "blue": 180, "pattern": "green"},
        )
        assert updated.status_code == 200
        assert updated.json()["calibration"]["pattern"] == "green"
        assert updated.json()["color_corrections"][0]["green"] == 200

        blocked = client.put("/api/brightness", json={"brightness": 0.5})
        assert blocked.status_code == 409

        finished = client.post(
            f"/api/calibration/session/{session_id}/finish",
            json={"save": True},
        )
        assert finished.status_code == 200
        assert finished.json()["calibration"]["active"] is False
        assert settings_path.exists()

        stale = client.post(
            f"/api/calibration/session/{session_id}/finish",
            json={"save": False},
        )
        assert stale.status_code == 409


def test_health_reports_failed_runtime() -> None:
    def failed_runtime(settings: RuntimeSettings) -> LumiStripeRuntime:
        return LumiStripeRuntime(
            settings,
            controller_factory=lambda current: (_ for _ in ()).throw(
                RuntimeError("controller failed")
            ),
        )

    app = create_app(RuntimeSettings(), runtime_factory=failed_runtime)
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 503
        assert response.json()["error"] == "controller failed"


def test_websocket_sends_initial_and_changed_state() -> None:
    app = create_app(RuntimeSettings(pixels=8))
    with TestClient(app) as client, client.websocket_connect("/ws/state") as websocket:
        initial = websocket.receive_json()
        assert initial["running"] is True

        response = client.put("/api/brightness", json={"brightness": 0.25})
        assert response.status_code == 200
        changed = websocket.receive_json()
        assert changed["revision"] > initial["revision"]
        assert changed["brightness"] == 0.25


def test_compiled_spa_is_served_after_api_routes(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        "<h1>LumiStripe dashboard</h1>", encoding="utf-8"
    )
    app = create_app(RuntimeSettings(pixels=8), static_dir=tmp_path)

    with TestClient(app) as client:
        assert client.get("/").text == "<h1>LumiStripe dashboard</h1>"
        assert client.get("/settings", headers={"Accept": "text/html"}).text == (
            "<h1>LumiStripe dashboard</h1>"
        )
        assert client.get("/api/health").json() == {"status": "ok"}


def test_cli_defaults_to_simulation() -> None:
    args = build_parser().parse_args([])
    assert args.hardware is False
    assert args.output_backend == "spi"
    assert args.spi_device == "/dev/spidev0.0"
    assert args.spi_speed == 1_000_000
    assert args.audio_source == "auto"
    assert args.pixels == 80
    assert args.pairing_code is None
    assert args.settings_file == RuntimeSettings().settings_file


def test_cli_rejects_incomplete_secondary_spi_configuration() -> None:
    with pytest.raises(SystemExit):
        main(["--spi-speed-2", "500000"])
    with pytest.raises(SystemExit):
        main(
            [
                "--output-backend",
                "gpio",
                "--spi-device-2",
                "/dev/spidev1.0",
            ]
        )


def test_pairing_code_protects_api_and_websocket() -> None:
    app = create_app(RuntimeSettings(pixels=8), pairing_code="1234")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/status").json() == {
            "required": True,
            "authenticated": False,
        }
        assert client.get("/api/state").status_code == 401

        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws/state"),
        ):
            pass
        assert exc_info.value.code == 4401
        with (
            pytest.raises(WebSocketDisconnect) as audio_exc_info,
            client.websocket_connect("/ws/audio"),
        ):
            pass
        assert audio_exc_info.value.code == 4401

        rejected = client.post("/api/auth/pair", json={"code": "0000"})
        assert rejected.status_code == 401
        assert rejected.json()["detail"] == "The pairing code is incorrect."

        paired = client.post("/api/auth/pair", json={"code": "1234"})
        assert paired.status_code == 200
        assert paired.json() == {"required": True, "authenticated": True}
        cookie = paired.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie
        assert "1234" not in cookie

        assert client.get("/api/state").status_code == 200
        with client.websocket_connect("/ws/state") as websocket:
            assert websocket.receive_json()["running"] is True
        with client.websocket_connect("/ws/audio") as websocket:
            assert websocket.receive_json()["health"] == "inactive"

        logged_out = client.post("/api/auth/logout")
        assert logged_out.json() == {"required": True, "authenticated": False}
        assert client.get("/api/state").status_code == 401


def test_pairing_endpoint_rate_limits_fifth_failure() -> None:
    app = create_app(RuntimeSettings(pixels=8), pairing_code="1234")
    with TestClient(app) as client:
        for _ in range(4):
            assert (
                client.post("/api/auth/pair", json={"code": "0000"}).status_code == 401
            )
        limited = client.post("/api/auth/pair", json={"code": "0000"})
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"


def test_cli_validates_pairing_code() -> None:
    assert build_parser().parse_args(["--pairing-code", "0427"]).pairing_code == "0427"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--pairing-code", "123"])
