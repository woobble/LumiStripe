from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lumistripe_web.app import create_app
from lumistripe_web.networking import (
    NetworkManagerError,
    NetworkManagerService,
)
from lumistripe_web.runtime import RuntimeSettings

HELPER_PATH = Path(__file__).resolve().parents[3] / "deploy" / "lumistripe_network.py"
SPEC = importlib.util.spec_from_file_location("lumistripe_network_helper", HELPER_PATH)
assert SPEC is not None and SPEC.loader is not None
network_helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(network_helper)


class FakeNetworkService:
    def status(self) -> dict[str, Any]:
        return {
            "manager_available": True,
            "error": None,
            "adapters": [
                {
                    "interface": "wlan0",
                    "state": "connected",
                    "connection": "LumiStripe hotspot",
                    "current_ssid": "LumiStripe",
                    "hotspot": True,
                    "connectable": False,
                    "reason": "This adapter is providing a hotspot.",
                },
                {
                    "interface": "wlan1",
                    "state": "disconnected",
                    "connection": None,
                    "current_ssid": None,
                    "hotspot": False,
                    "connectable": True,
                    "reason": None,
                },
            ],
        }

    def scan(self, interface: str) -> dict[str, Any]:
        if interface == "wlan0":
            raise NetworkManagerError(
                "The hotspot adapter cannot connect to an internet network.",
                code="invalid",
            )
        return {
            "interface": interface,
            "networks": [
                {
                    "ssid": "Party Wi-Fi",
                    "security": "WPA2",
                    "signal": 88,
                    "secured": True,
                },
                {"ssid": "Guest", "security": "Open", "signal": 42, "secured": False},
            ],
        }

    def connect(self, interface: str, ssid: str, password: str) -> dict[str, Any]:
        if interface == "wlan0":
            raise NetworkManagerError(
                "The hotspot adapter cannot connect to an internet network.",
                code="invalid",
            )
        if password != "correct-horse":
            raise NetworkManagerError(
                "Could not connect to that Wi-Fi network. Check the password and try again.",
                code="connection_failed",
            )
        return {"interface": interface, "ssid": ssid, "connected": True}


def test_network_api_lists_adapters_scans_connects_and_protects_hotspot() -> None:
    service = FakeNetworkService()
    app = create_app(RuntimeSettings(pixels=8), network_service=service)  # type: ignore[arg-type]
    with TestClient(app) as client:
        status = client.get("/api/network/status")
        assert status.status_code == 200
        assert status.json()["adapters"][0]["hotspot"] is True
        assert status.json()["adapters"][1]["connectable"] is True

        scan = client.post("/api/network/scan", json={"interface": "wlan1"})
        assert scan.status_code == 200
        assert scan.json()["networks"][0]["ssid"] == "Party Wi-Fi"

        connected = client.post(
            "/api/network/connect",
            json={
                "interface": "wlan1",
                "ssid": "Party Wi-Fi",
                "password": "correct-horse",
            },
        )
        assert connected.status_code == 200
        assert connected.json() == {
            "interface": "wlan1",
            "ssid": "Party Wi-Fi",
            "connected": True,
        }
        assert "password" not in connected.json()

        hotspot_scan = client.post("/api/network/scan", json={"interface": "wlan0"})
        assert hotspot_scan.status_code == 409
        assert "hotspot adapter" in hotspot_scan.json()["detail"].lower()

        bad_password = client.post(
            "/api/network/connect",
            json={"interface": "wlan1", "ssid": "Party Wi-Fi", "password": "wrong-pass"},
        )
        assert bad_password.status_code == 409
        assert "wrong-pass" not in bad_password.text

        invalid = client.post(
            "/api/network/connect",
            json={
                "interface": "wlan1",
                "ssid": "Party Wi-Fi",
                "password": "correct-horse",
                "extra": "ignored?",
            },
        )
        assert invalid.status_code == 422

        oversized_password = "s" * 64
        oversized = client.post(
            "/api/network/connect",
            json={"interface": "wlan1", "ssid": "Party Wi-Fi", "password": oversized_password},
        )
        assert oversized.status_code == 422
        assert oversized_password not in oversized.text


def test_network_api_requires_pairing() -> None:
    app = create_app(
        RuntimeSettings(pixels=8),
        pairing_code="1234",
        network_service=FakeNetworkService(),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        assert client.get("/api/network/status").status_code == 401
        assert client.post("/api/auth/pair", json={"code": "1234"}).status_code == 200
        assert client.get("/api/network/status").status_code == 200


def test_network_service_sends_password_on_stdin_not_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        call["command"] = command
        call["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"ok":true,"interface":"wlan1","ssid":"Party Wi-Fi","connected":true}',
        )

    monkeypatch.setattr("lumistripe_web.networking.subprocess.run", fake_run)

    result = NetworkManagerService().connect("wlan1", "Party Wi-Fi", "correct-horse")

    assert result["connected"] is True
    assert "correct-horse" not in repr(call["command"])
    assert '"password": "correct-horse"' in call["input"]


def test_nmcli_rows_preserve_escaped_delimiters() -> None:
    assert network_helper._split_nmcli_row(r"wlan1:wifi:connected:Guest\:Network") == [
        "wlan1",
        "wifi",
        "connected",
        "Guest:Network",
    ]


def test_scan_deduplicates_networks_by_strongest_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        network_helper,
        "_run_nmcli",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout="Party\\: Wi-Fi:WPA2:40\nParty\\: Wi-Fi:WPA2:82\nGuest:--:30\n"
        ),
    )

    networks = network_helper._get_networks("wlan1")

    assert networks == [
        {"ssid": "Party: Wi-Fi", "security": "WPA2", "signal": 82, "secured": True},
        {"ssid": "Guest", "security": "Open", "signal": 30, "secured": False},
    ]


def test_personal_wifi_security_validation() -> None:
    assert network_helper._key_management("WPA2", "correct-horse") == "wpa-psk"
    assert network_helper._key_management("WPA3", "correct-horse") == "sae"
    assert network_helper._key_management("Open", "") is None
    with pytest.raises(network_helper.NetworkOperationError, match="8 to 63"):
        network_helper._key_management("WPA2", "short")
    with pytest.raises(network_helper.NetworkOperationError, match="not supported"):
        network_helper._key_management("WPA2 802.1X", "correct-horse")


def test_connect_stores_password_in_networkmanager_profile_not_process_args(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(network_helper, "NETWORK_CONNECTION_DIR", tmp_path)
    monkeypatch.setattr(
        network_helper,
        "_adapter_for_connection",
        lambda interface: {"interface": interface, "state": "disconnected"},
    )
    monkeypatch.setattr(
        network_helper,
        "_get_networks",
        lambda interface: [
            {"ssid": "Party Wi-Fi", "security": "WPA2", "signal": 80, "secured": True}
        ],
    )
    commands: list[tuple[str, ...]] = []

    def run_nmcli(*arguments: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(network_helper, "_run_nmcli", run_nmcli)

    result = network_helper._connect(
        {"interface": "wlan1", "ssid": "Party Wi-Fi", "password": "correct-horse"}
    )

    profile = next(tmp_path.glob("*.nmconnection"))
    assert result["connected"] is True
    assert "psk=correct-horse" in profile.read_text(encoding="utf-8")
    assert profile.stat().st_mode & 0o777 == 0o600
    assert all("correct-horse" not in argument for command in commands for argument in command)


def test_failed_connect_removes_unusable_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(network_helper, "NETWORK_CONNECTION_DIR", tmp_path)
    monkeypatch.setattr(
        network_helper,
        "_adapter_for_connection",
        lambda interface: {"interface": interface, "state": "disconnected"},
    )
    monkeypatch.setattr(
        network_helper,
        "_get_networks",
        lambda interface: [
            {"ssid": "Party Wi-Fi", "security": "WPA2", "signal": 80, "secured": True}
        ],
    )

    def fail_activation(*arguments: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "up" in arguments:
            raise network_helper.NetworkOperationError("operation_failed", "generic")
        return subprocess.CompletedProcess(arguments, 0, stdout="")

    monkeypatch.setattr(network_helper, "_run_nmcli", fail_activation)
    monkeypatch.setattr(
        network_helper.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=""),
    )

    with pytest.raises(network_helper.NetworkOperationError, match="Check the password"):
        network_helper._connect(
            {"interface": "wlan1", "ssid": "Party Wi-Fi", "password": "correct-horse"}
        )
    assert list(tmp_path.glob("*.nmconnection")) == []


def test_hotspot_adapter_is_refused_and_manager_status_can_be_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        network_helper,
        "_get_adapters",
        lambda: [
            {
                "interface": "wlan0",
                "hotspot": True,
                "connectable": False,
                "state": "connected",
            }
        ],
    )
    with pytest.raises(network_helper.NetworkOperationError, match="hotspot adapter"):
        network_helper._adapter_for_connection("wlan0")

    monkeypatch.setattr(network_helper, "_network_manager_running", lambda: False)
    assert network_helper.dispatch({"operation": "status"})["manager_available"] is False
