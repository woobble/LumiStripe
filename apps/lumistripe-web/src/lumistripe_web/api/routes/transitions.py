"""Animation transition settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...contracts.common import DashboardState
from ...contracts.transitions import (
    TransitionSettingsRequest,
    TransitionSettingsResponse,
)
from ..dependencies import await_command, runtime_from_request

router = APIRouter()


@router.get("/api/transitions/settings", response_model=TransitionSettingsResponse)
async def get_transition_settings(request: Request) -> TransitionSettingsResponse:
    return runtime_from_request(request).transition_settings()


@router.put("/api/transitions/settings", response_model=DashboardState)
async def set_transition_settings(
    request: Request, body: TransitionSettingsRequest
) -> DashboardState:
    return await await_command(
        runtime_from_request(request).set_transition_settings(
            body.enabled,
            body.duration_ms,
            body.effect if "effect" in body.model_fields_set else None,
        )
    )


__all__ = ["router"]
