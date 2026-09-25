"""Audio source, device, output, and calibration routes."""

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
    SpotifyControlRequest,
    SpotifyStatusResponse,
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


@router.get("/api/audio/spotify", response_model=SpotifyStatusResponse)
async def spotify_status(request: Request) -> SpotifyStatusResponse:
    return runtime_from_request(request).spotify_status()


@router.post("/api/audio/spotify/control", response_model=SpotifyStatusResponse)
async def control_spotify(
    request: Request, body: SpotifyControlRequest
) -> SpotifyStatusResponse:
    return await await_command(
        runtime_from_request(request).control_spotify(body.action, body.value)
    )


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
