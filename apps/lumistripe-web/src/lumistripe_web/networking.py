"""Client for the root-owned NetworkManager bridge installed on Raspberry Pi."""

from __future__ import annotations

import json
import subprocess
from typing import Any


class NetworkManagerError(RuntimeError):
    def __init__(self, message: str, *, code: str = "operation_failed") -> None:
        super().__init__(message)
        self.code = code


class NetworkManagerService:
    """Send narrow, structured network operations to the installed helper."""

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            result = subprocess.run(
                ["/usr/bin/sudo", "-n", "/usr/local/sbin/lumistripe-network"],
                input=json.dumps(payload),
                check=False,
                capture_output=True,
                text=True,
                timeout=55,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NetworkManagerError(
                "Network management is unavailable. Install or update the Pi appliance. Otherwise, retry shortly.",
                code="unavailable",
            ) from exc
        if result.returncode != 0:
            raise NetworkManagerError(
                "Network management is unavailable. Install or update the Pi appliance.",
                code="unavailable",
            )
        try:
            response = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise NetworkManagerError(
                "Network management returned an invalid response.", code="unavailable"
            ) from exc
        if not isinstance(response, dict):
            raise NetworkManagerError(
                "Network management returned an invalid response.", code="unavailable"
            )
        if response.get("ok") is not True:
            code = response.get("error_code")
            message = response.get("error")
            if not isinstance(code, str) or not isinstance(message, str):
                raise NetworkManagerError(
                    "NetworkManager could not complete the request."
                )
            raise NetworkManagerError(message, code=code)
        return response

    def status(self) -> dict[str, Any]:
        return self._request({"operation": "status"})

    def scan(self, interface: str) -> dict[str, Any]:
        return self._request({"operation": "scan", "interface": interface})

    def connect(self, interface: str, ssid: str, password: str) -> dict[str, Any]:
        return self._request(
            {
                "operation": "connect",
                "interface": interface,
                "ssid": ssid,
                "password": password,
            }
        )
