"""Startup restore settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...contracts.startup import StartupSettingsRequest, StartupSettingsResponse
from ..dependencies import await_command, runtime_from_request

router = APIRouter()


@router.get("/api/startup", response_model=StartupSettingsResponse)
async def startup_settings(request: Request) -> StartupSettingsResponse:
    return runtime_from_request(request).startup_settings()


@router.put("/api/startup", response_model=StartupSettingsResponse)
async def update_startup_settings(
    request: Request, body: StartupSettingsRequest
) -> StartupSettingsResponse:
    return await await_command(
        runtime_from_request(request).set_startup_restore(body.restore_last_state)
    )


__all__ = ["router"]
