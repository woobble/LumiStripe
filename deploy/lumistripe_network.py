"""Root-owned NetworkManager bridge for the paired LumiStripe dashboard."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

NETWORK_CONNECTION_DIR = Path("/etc/NetworkManager/system-connections")
INTERFACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
WIFI_CONNECTION_PREFIX = "lumistripe-wifi-"
MAX_REQUEST_BYTES = 8192


class NetworkOperationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _run_nmcli(*arguments: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["/usr/bin/nmcli", "--wait", str(timeout), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NetworkOperationError(
            "unavailable", "NetworkManager is unavailable."
        ) from exc
    if result.returncode != 0:
        raise NetworkOperationError(
            "operation_failed", "NetworkManager could not complete the request."
        )
    return result


def _split_nmcli_row(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line:
        if escaped:
            current.append({"n": "\n", "r": "\r", "t": "\t"}.get(character, character))
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def _network_manager_running() -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/nmcli", "--terse", "--fields", "RUNNING", "general"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip().lower() == "running"


def _interface_mode(interface: str) -> str | None:
    try:
        result = subprocess.run(
            ["/usr/sbin/iw", "dev", interface, "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"^\s*type\s+(\S+)", result.stdout, re.MULTILINE | re.IGNORECASE)
    return match.group(1).lower() if match else None


def _connected_ssid(connection: str) -> str | None:
    if not connection or connection == "--":
        return None
    try:
        result = _run_nmcli(
            "--get-values",
            "802-11-wireless.ssid",
            "connection",
            "show",
            connection,
            timeout=5,
        )
    except NetworkOperationError:
        return None
    value = result.stdout.strip()
    return value or None


def _get_adapters() -> list[dict[str, Any]]:
    rows = _run_nmcli(
        "--terse",
        "--escape",
        "yes",
        "--fields",
        "DEVICE,TYPE,STATE,CONNECTION",
        "device",
        "status",
    ).stdout.splitlines()
    adapters: list[dict[str, Any]] = []
    for line in rows:
        fields = _split_nmcli_row(line)
        if len(fields) != 4 or fields[1].lower() != "wifi":
            continue
        interface, _, state, connection = fields
        mode = _interface_mode(interface)
        is_hotspot = mode in {"ap", "p2p-go"}
        can_verify_mode = mode is not None
        adapters.append(
            {
                "interface": interface,
                "state": state,
                "connection": None if connection in ("", "--") else connection,
                "current_ssid": _connected_ssid(connection),
                "hotspot": is_hotspot if can_verify_mode else None,
                "connectable": can_verify_mode and not is_hotspot,
                "reason": (
                    "This adapter is providing a hotspot."
                    if is_hotspot
                    else "Could not verify this adapter's Wi-Fi mode."
                    if not can_verify_mode
                    else None
                ),
            }
        )
    return adapters


def _validate_interface(value: object) -> str:
    if not isinstance(value, str) or not INTERFACE_PATTERN.fullmatch(value):
        raise NetworkOperationError("invalid", "Choose a valid Wi-Fi adapter.")
    return value


def _adapter_for_connection(interface: object) -> dict[str, Any]:
    selected = _validate_interface(interface)
    adapters = _get_adapters()
    adapter = next((item for item in adapters if item["interface"] == selected), None)
    if adapter is None:
        raise NetworkOperationError("invalid", "The selected Wi-Fi adapter is unavailable.")
    if adapter["hotspot"] is True:
        raise NetworkOperationError(
            "invalid", "The hotspot adapter cannot connect to an internet network."
        )
    if not adapter["connectable"]:
        raise NetworkOperationError(
            "invalid", "Could not verify that this adapter is safe to use."
        )
    return adapter


def _get_networks(interface: str) -> list[dict[str, Any]]:
    output = _run_nmcli(
        "--terse",
        "--escape",
        "yes",
        "--fields",
        "SSID,SECURITY,SIGNAL",
        "device",
        "wifi",
        "list",
        "--rescan",
        "yes",
        "ifname",
        interface,
        timeout=25,
    ).stdout
    strongest_by_ssid: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        fields = _split_nmcli_row(line)
        if len(fields) != 3:
            continue
        ssid, security, raw_signal = fields
        if not ssid or ssid == "--" or any(
            ord(character) < 32 or ord(character) == 127 for character in ssid
        ):
            continue
        try:
            signal = max(0, min(100, int(raw_signal)))
        except ValueError:
            continue
        entry = {
            "ssid": ssid,
            "security": "Open" if security in ("", "--") else security,
            "signal": signal,
            "secured": security not in ("", "--"),
        }
        current = strongest_by_ssid.get(ssid)
        if current is None or signal > current["signal"]:
            strongest_by_ssid[ssid] = entry
    return sorted(
        strongest_by_ssid.values(), key=lambda network: (-network["signal"], network["ssid"].casefold())
    )


def _key_management(security: str, password: str) -> str | None:
    if security == "Open":
        if password:
            raise NetworkOperationError(
                "invalid", "This network is open; leave the password blank."
            )
        return None
    upper = security.upper()
    if "802.1X" in upper or "EAP" in upper or "WEP" in upper:
        raise NetworkOperationError(
            "unsupported", "This network security type is not supported."
        )
    if "WPA2" in upper and ("WPA3" in upper or "SAE" in upper):
        # NetworkManager's wpa-psk mode supports WPA2/WPA3 Personal transition
        # networks and lets WPA2-only adapters connect through the WPA2 path.
        key_management = "wpa-psk"
    elif "WPA3" in upper or "SAE" in upper:
        key_management = "sae"
    elif "WPA" in upper:
        key_management = "wpa-psk"
    else:
        raise NetworkOperationError(
            "unsupported", "This network security type is not supported."
        )
    password_bytes = password.encode("utf-8")
    if not 8 <= len(password_bytes) <= 63:
        raise NetworkOperationError(
            "invalid", "Wi-Fi passwords must be 8 to 63 UTF-8 bytes."
        )
    if any(character in password for character in "\x00\r\n"):
        raise NetworkOperationError("invalid", "The Wi-Fi password is invalid.")
    return key_management


def _keyfile_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _write_profile(
    path: Path,
    *,
    connection_id: str,
    connection_uuid: str,
    interface: str,
    ssid: str,
    password: str,
    key_management: str | None,
    autoconnect: bool,
) -> None:
    lines = [
        "[connection]",
        f"id={_keyfile_escape(connection_id)}",
        f"uuid={connection_uuid}",
        "type=wifi",
        f"interface-name={interface}",
        f"autoconnect={'true' if autoconnect else 'false'}",
        "autoconnect-priority=20",
        "",
        "[802-11-wireless]",
        "mode=infrastructure",
        f"ssid={_keyfile_escape(ssid)}",
        "hidden=false",
        "",
    ]
    if key_management is not None:
        lines.extend(
            [
                "[802-11-wireless-security]",
                f"key-mgmt={key_management}",
                f"psk={_keyfile_escape(password)}",
                "psk-flags=0",
                "",
            ]
        )
    lines.extend(["[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".lumistripe-", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("\n".join(lines))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)


def _connect(payload: dict[str, Any]) -> dict[str, Any]:
    interface = _validate_interface(payload.get("interface"))
    adapter = _adapter_for_connection(interface)
    ssid = payload.get("ssid")
    password = payload.get("password", "")
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32:
        raise NetworkOperationError("invalid", "Choose a valid Wi-Fi network.")
    if any(ord(character) < 32 or ord(character) == 127 for character in ssid):
        raise NetworkOperationError("invalid", "The Wi-Fi network name is invalid.")
    if not isinstance(password, str) or len(password) > 63:
        raise NetworkOperationError("invalid", "The Wi-Fi password is invalid.")

    networks = _get_networks(interface)
    selected = next((item for item in networks if item["ssid"] == ssid), None)
    if selected is None:
        raise NetworkOperationError(
            "invalid", "That network is no longer visible. Scan again and retry."
        )
    key_management = _key_management(selected["security"], password)
    interface_hash = hashlib.sha256(interface.encode("utf-8")).hexdigest()[:8]
    ssid_hash = hashlib.sha256(ssid.encode("utf-8")).hexdigest()[:12]
    profile_uuid = str(uuid.uuid4())
    profile_name = f"{WIFI_CONNECTION_PREFIX}{interface_hash}-{ssid_hash}-{profile_uuid}"
    profile_path = NETWORK_CONNECTION_DIR / f"{profile_name}.nmconnection"
    connection_id = f"LumiStripe Wi-Fi on {interface}: {ssid}"

    _write_profile(
        profile_path,
        connection_id=connection_id,
        connection_uuid=profile_uuid,
        interface=interface,
        ssid=ssid,
        password=password,
        key_management=key_management,
        autoconnect=False,
    )
    try:
        _run_nmcli("connection", "load", str(profile_path), timeout=10)
        _run_nmcli(
            "connection",
            "up",
            "uuid",
            profile_uuid,
            "ifname",
            interface,
            timeout=40,
        )
        _write_profile(
            profile_path,
            connection_id=connection_id,
            connection_uuid=profile_uuid,
            interface=interface,
            ssid=ssid,
            password=password,
            key_management=key_management,
            autoconnect=True,
        )
        _run_nmcli("connection", "reload", timeout=10)
        for old_profile in NETWORK_CONNECTION_DIR.glob(
            f"{WIFI_CONNECTION_PREFIX}{interface_hash}-{ssid_hash}-*.nmconnection"
        ):
            if old_profile != profile_path:
                old_profile.unlink(missing_ok=True)
        _run_nmcli("connection", "reload", timeout=10)
    except NetworkOperationError as exc:
        try:
            subprocess.run(
                ["/usr/bin/nmcli", "connection", "delete", "uuid", profile_uuid],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        profile_path.unlink(missing_ok=True)
        raise NetworkOperationError(
            "connection_failed",
            "Could not connect to that Wi-Fi network. Check the password, router security mode, and adapter compatibility.",
        ) from exc

    return {
        "interface": interface,
        "ssid": ssid,
        "connected": True,
        "state": adapter["state"],
    }


def dispatch(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise NetworkOperationError("invalid", "The network request is invalid.")
    operation = payload.get("operation")
    if operation == "status":
        if not _network_manager_running():
            return {
                "ok": True,
                "manager_available": False,
                "error": "NetworkManager is unavailable.",
                "adapters": [],
            }
        return {
            "ok": True,
            "manager_available": True,
            "error": None,
            "adapters": _get_adapters(),
        }
    if not _network_manager_running():
        raise NetworkOperationError("unavailable", "NetworkManager is unavailable.")
    if operation == "scan":
        interface = _validate_interface(payload.get("interface"))
        _adapter_for_connection(interface)
        return {"ok": True, "interface": interface, "networks": _get_networks(interface)}
    if operation == "connect":
        return {"ok": True, **_connect(payload)}
    raise NetworkOperationError("invalid", "The network request is invalid.")


def main() -> int:
    if len(sys.argv) != 1:
        return 2
    try:
        request = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(request) > MAX_REQUEST_BYTES:
            raise NetworkOperationError("invalid", "The network request is too large.")
        payload = json.loads(request)
        response = dispatch(payload)
    except NetworkOperationError as exc:
        response = {"ok": False, "error_code": exc.code, "error": str(exc)}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, UnicodeEncodeError, RecursionError):
        response = {
            "ok": False,
            "error_code": "invalid",
            "error": "The network request is invalid.",
        }
    json.dump(response, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
