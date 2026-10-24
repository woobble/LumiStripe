"""Sequencer settings routes."""

from __future__ import annotations

from fastapi import APIRouter, Path, Request

from ...contracts.common import DashboardState
from ...contracts.sequencer import (
    SequenceActiveRequest,
    SequenceCreateRequest,
    SequenceDefinitionRequest,
    SequenceLibraryResponse,
    SequencerSettingsRequest,
    SequencerSettingsResponse,
)
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


@router.get("/api/sequencer/sequences", response_model=SequenceLibraryResponse)
async def get_animation_sequences(request: Request) -> SequenceLibraryResponse:
    return runtime_from_request(request).animation_sequences()


@router.post("/api/sequencer/sequences", response_model=DashboardState)
async def create_animation_sequence(
    request: Request, body: SequenceCreateRequest
) -> DashboardState:
    return await await_command(
        runtime_from_request(request).create_animation_sequence(body.to_sequence())
    )


@router.put("/api/sequencer/sequences/{sequence_id}", response_model=DashboardState)
async def update_animation_sequence(
    request: Request,
    body: SequenceDefinitionRequest,
    sequence_id: str = Path(min_length=1, max_length=64),
) -> DashboardState:
    return await await_command(
        runtime_from_request(request).update_animation_sequence(
            sequence_id, body.to_core(sequence_id)
        )
    )


@router.delete("/api/sequencer/sequences/{sequence_id}", response_model=DashboardState)
async def delete_animation_sequence(
    request: Request,
    sequence_id: str = Path(min_length=1, max_length=64),
) -> DashboardState:
    return await await_command(
        runtime_from_request(request).delete_animation_sequence(sequence_id)
    )


@router.put("/api/sequencer/active", response_model=DashboardState)
async def set_active_animation_sequence(
    request: Request, body: SequenceActiveRequest
) -> DashboardState:
    return await await_command(
        runtime_from_request(request).set_active_animation_sequence(body.sequence_id)
    )


__all__ = ["router"]
