"""Audio source, Bluetooth, output, and calibration routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...contracts.audio import (
    AudioCalibrationFinishRequest,
    AudioCalibrationSessionResponse,
    AudioCalibrationStartRequest,
    AudioDeviceRequest,
    AudioResetRequest,
    AudioSettingsRequest,
    AudioSettingsResponse,
    AudioSourceRequest,
)
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
from ...settings import AudioTuningProfile
from ..dependencies import await_command, runtime_from_request

router = APIRouter()


@router.get("/api/audio/settings", response_model=AudioSettingsResponse)
async def audio_settings(request: Request) -> AudioSettingsResponse:
    return runtime_from_request(request).audio_settings()


@router.put("/api/audio/source", response_model=AudioSettingsResponse)
async def select_audio_source(
    request: Request, body: AudioSourceRequest
) -> AudioSettingsResponse:
    return await await_command(runtime_from_request(request).set_audio_source(body.source))


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
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/bluetooth/alias", response_model=BluetoothStatusResponse)
async def bluetooth_alias(
    request: Request, body: BluetoothAliasRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).set_bluetooth_alias(body.alias)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/scan", response_model=BluetoothStatusResponse)
async def bluetooth_scan(request: Request) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).start_bluetooth_scan()
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/pair", response_model=BluetoothStatusResponse)
async def bluetooth_pair(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).pair_bluetooth_device(body.address)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/connect", response_model=BluetoothStatusResponse)
async def bluetooth_connect(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).connect_bluetooth_device(
            body.address, body.role
        )
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/forget", response_model=BluetoothStatusResponse)
async def bluetooth_forget(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).forget_bluetooth_device(body.address)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/disconnect", response_model=BluetoothStatusResponse)
async def bluetooth_disconnect(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).disconnect_bluetooth_device(body.address)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/output", response_model=BluetoothStatusResponse)
async def select_audio_output(
    request: Request, body: AudioOutputSelectionRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).set_audio_output(body.selector)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/output/volume", response_model=BluetoothStatusResponse)
async def set_audio_output_volume(
    request: Request, body: AudioOutputVolumeRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).set_audio_output_volume(
            body.selector, body.volume
        )
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/output/mute", response_model=BluetoothStatusResponse)
async def set_audio_output_mute(
    request: Request, body: AudioOutputMuteRequest
) -> BluetoothStatusResponse:
    try:
        return runtime_from_request(request).set_audio_output_mute(
            body.selector, body.muted
        )
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/device", response_model=AudioSettingsResponse)
async def select_audio_device(
    request: Request, body: AudioDeviceRequest
) -> AudioSettingsResponse:
    return await await_command(runtime_from_request(request).select_audio_device(body.device))


@router.put("/api/audio/settings", response_model=AudioSettingsResponse)
async def update_audio_settings(
    request: Request, body: AudioSettingsRequest
) -> AudioSettingsResponse:
    profile = AudioTuningProfile(**body.settings.model_dump())
    return await await_command(
        runtime_from_request(request).apply_audio_settings(body.device, profile)
    )


@router.post("/api/audio/settings/reset", response_model=AudioSettingsResponse)
async def reset_audio_settings(
    request: Request, body: AudioResetRequest
) -> AudioSettingsResponse:
    return await await_command(runtime_from_request(request).reset_audio_settings(body.device))


@router.post(
    "/api/audio/calibration/session",
    response_model=AudioCalibrationSessionResponse,
)
async def start_audio_calibration(
    request: Request, body: AudioCalibrationStartRequest
) -> AudioCalibrationSessionResponse:
    return await await_command(
        runtime_from_request(request).start_audio_calibration(
            body.device, body.duration_seconds
        )
    )


@router.get(
    "/api/audio/calibration/session/{session_id}",
    response_model=AudioCalibrationSessionResponse,
)
async def audio_calibration_status(
    request: Request, session_id: str
) -> AudioCalibrationSessionResponse:
    try:
        return runtime_from_request(request).audio_calibration_status(session_id)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/api/audio/calibration/session/{session_id}/finish",
    response_model=AudioSettingsResponse,
)
async def finish_audio_calibration(
    request: Request, session_id: str, body: AudioCalibrationFinishRequest
) -> AudioSettingsResponse:
    result = await await_command(
        runtime_from_request(request).finish_audio_calibration(
            session_id,
            apply=body.apply,
            target_level=body.target_level,
            noise_floor=body.noise_floor,
        )
    )
    if isinstance(result, AudioCalibrationSessionResponse):
        raise HTTPException(status_code=409, detail="audio calibration is not complete")
    return result


__all__ = ["router"]
