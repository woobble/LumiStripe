"""Sequencer settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...contracts.common import DashboardState
from ...contracts.sequencer import SequencerSettingsRequest, SequencerSettingsResponse
from ..dependencies import await_command, runtime_from_request

router = APIRouter()


@router.get("/api/sequencer/settings", response_model=SequencerSettingsResponse)
async def get_sequencer_settings(request: Request) -> SequencerSettingsResponse:
    return runtime_from_request(request).sequencer_settings()


@router.put("/api/sequencer/settings", response_model=DashboardState)
async def set_sequencer_settings(
    request: Request, body: SequencerSettingsRequest
) -> DashboardState:
    return await await_command(
        runtime_from_request(request).set_sequencer_settings(
            body.switch_quantization, body.bars_per_phrase
        )
    )


__all__ = ["router"]
