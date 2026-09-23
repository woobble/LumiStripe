"""Bluetooth device and PipeWire output routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...contracts.bluetooth import (
    AudioOutputMuteRequest,
    AudioOutputSelectionRequest,
    AudioOutputVolumeRequest,
    BluetoothAliasRequest,
    BluetoothDeviceRequest,
    BluetoothPowerRequest,
    BluetoothStatusResponse,
)
from ...runtime import RuntimeCommandError
from ..dependencies import runtime_from_request

router = APIRouter()


def _conflict(exc: RuntimeCommandError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/api/audio/bluetooth", response_model=BluetoothStatusResponse)
async def bluetooth_status(request: Request) -> BluetoothStatusResponse:
    return runtime_from_request(request).bluetooth_status()


@router.put("/api/audio/bluetooth/power", response_model=BluetoothStatusResponse)
async def bluetooth_power(
    request: Request, body: BluetoothPowerRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).set_bluetooth_power(body.powered)
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


@router.put("/api/audio/bluetooth/alias", response_model=BluetoothStatusResponse)
async def bluetooth_alias(
    request: Request, body: BluetoothAliasRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).set_bluetooth_alias(body.alias)
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


@router.post("/api/audio/bluetooth/scan", response_model=BluetoothStatusResponse)
async def bluetooth_scan(request: Request) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).start_bluetooth_scan()
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


@router.post("/api/audio/bluetooth/pair", response_model=BluetoothStatusResponse)
async def bluetooth_pair(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).pair_bluetooth_device(body.address)
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


@router.post("/api/audio/bluetooth/connect", response_model=BluetoothStatusResponse)
async def bluetooth_connect(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).connect_bluetooth_device(
            body.address, body.role
        )
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


@router.post("/api/audio/bluetooth/forget", response_model=BluetoothStatusResponse)
async def bluetooth_forget(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).forget_bluetooth_device(body.address)
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


@router.post("/api/audio/bluetooth/disconnect", response_model=BluetoothStatusResponse)
async def bluetooth_disconnect(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).disconnect_bluetooth_device(body.address)
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


@router.put("/api/audio/output", response_model=BluetoothStatusResponse)
async def select_audio_output(
    request: Request, body: AudioOutputSelectionRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).set_audio_output(body.selector)
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


@router.put("/api/audio/output/volume", response_model=BluetoothStatusResponse)
async def set_audio_output_volume(
    request: Request, body: AudioOutputVolumeRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).set_audio_output_volume(
            body.selector, body.volume
        )
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


@router.put("/api/audio/output/mute", response_model=BluetoothStatusResponse)
async def set_audio_output_mute(
    request: Request, body: AudioOutputMuteRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).set_audio_output_mute(
            body.selector, body.muted
        )
    except RuntimeCommandError as exc:
        raise _conflict(exc) from exc


__all__ = ["router"]
