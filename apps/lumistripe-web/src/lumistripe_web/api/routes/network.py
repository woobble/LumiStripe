"""Dashboard controls for the Pi's internet-facing Wi-Fi adapter."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...contracts.network import (
    NetworkStatusResponse,
    WifiConnectRequest,
    WifiConnectResponse,
    WifiScanRequest,
    WifiScanResponse,
)
from ...networking import NetworkManagerError, NetworkManagerService

router = APIRouter()


def _service_from_request(request: Request) -> NetworkManagerService:
    return request.app.state.network_service


def _network_error(exc: NetworkManagerError) -> HTTPException:
    code_to_status = {
        "invalid": 409,
        "unsupported": 422,
        "unavailable": 503,
        "connection_failed": 409,
    }
    return HTTPException(
        status_code=code_to_status.get(exc.code, 503), detail=str(exc)
    )


@router.get("/api/network/status", response_model=NetworkStatusResponse)
def network_status(request: Request) -> NetworkStatusResponse:
    try:
        response = _service_from_request(request).status()
    except NetworkManagerError as exc:
        raise _network_error(exc) from exc
    return NetworkStatusResponse.model_validate(response)


@router.post("/api/network/scan", response_model=WifiScanResponse)
def scan_wifi(
    request: Request, body: WifiScanRequest
) -> WifiScanResponse:
    try:
        response = _service_from_request(request).scan(body.interface)
    except NetworkManagerError as exc:
        raise _network_error(exc) from exc
    return WifiScanResponse.model_validate(response)


@router.post("/api/network/connect", response_model=WifiConnectResponse)
def connect_wifi(
    request: Request, body: WifiConnectRequest
) -> WifiConnectResponse:
    try:
        response = _service_from_request(request).connect(
            body.interface, body.ssid, body.password.get_secret_value()
        )
    except NetworkManagerError as exc:
        raise _network_error(exc) from exc
    return WifiConnectResponse.model_validate(response)


__all__ = ["router"]
