"""Playback and color calibration routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from lumistripe.controller import ColorCorrection

from ...contracts.common import DashboardState
from ...contracts.control import (
    AnimationRequest,
    BlackoutRequest,
    BrightnessRequest,
    CalibrationFinishRequest,
    CalibrationSessionResponse,
    CalibrationStartRequest,
    CalibrationUpdateRequest,
    ModeRequest,
)
from ..dependencies import await_command, runtime_from_request

router = APIRouter()


@router.put("/api/mode", response_model=DashboardState)
async def set_mode(request: Request, body: ModeRequest) -> DashboardState:
    return await await_command(
        runtime_from_request(request).set_mode(
            body.mode,
            solid_color=body.color,
            stripe_id=body.stripe_id,
            music_recognition_enabled=body.music_recognition_enabled,
        )
    )


@router.put("/api/brightness", response_model=DashboardState)
async def set_brightness(request: Request, body: BrightnessRequest) -> DashboardState:
    return await await_command(
        runtime_from_request(request).set_brightness(
            body.brightness, stripe_id=body.stripe_id
        )
    )


@router.put("/api/animation", response_model=DashboardState)
async def select_animation(request: Request, body: AnimationRequest) -> DashboardState:
    return await await_command(
        runtime_from_request(request).select_animation(
            body.name, stripe_id=body.stripe_id
        )
    )


@router.post("/api/blackout", response_model=DashboardState)
async def set_blackout(request: Request, body: BlackoutRequest) -> DashboardState:
    return await await_command(
        runtime_from_request(request).set_blackout(
            body.enabled, stripe_id=body.stripe_id
        )
    )


@router.post("/api/calibration/session", response_model=CalibrationSessionResponse)
async def start_calibration(
    request: Request,
    body: CalibrationStartRequest,
) -> CalibrationSessionResponse:
    return await await_command(
        runtime_from_request(request).start_calibration(body.output_index)
    )


@router.put("/api/calibration/session/{session_id}", response_model=DashboardState)
async def update_calibration(
    request: Request,
    session_id: str,
    body: CalibrationUpdateRequest,
) -> DashboardState:
    return await await_command(
        runtime_from_request(request).update_calibration(
            session_id,
            ColorCorrection(body.red, body.green, body.blue),
            body.pattern,
        )
    )


@router.post(
    "/api/calibration/session/{session_id}/finish",
    response_model=DashboardState,
)
async def finish_calibration(
    request: Request,
    session_id: str,
    body: CalibrationFinishRequest,
) -> DashboardState:
    return await await_command(
        runtime_from_request(request).finish_calibration(
            session_id,
            save=body.save,
        )
    )


__all__ = ["router"]
