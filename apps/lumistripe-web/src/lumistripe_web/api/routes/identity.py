"""Application and Spotify player naming routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...contracts.identity import IdentitySettingsRequest, IdentitySettingsResponse
from ..dependencies import await_command, runtime_from_request

router = APIRouter()


@router.get("/api/identity", response_model=IdentitySettingsResponse)
async def identity_settings(request: Request) -> IdentitySettingsResponse:
    return runtime_from_request(request).identity_settings()


@router.put("/api/identity", response_model=IdentitySettingsResponse)
async def update_identity_settings(
    request: Request, body: IdentitySettingsRequest
) -> IdentitySettingsResponse:
    return await await_command(
        runtime_from_request(request).set_identity_settings(
            body.application_name, body.spotify_device_name
        )
    )


__all__ = ["router"]
