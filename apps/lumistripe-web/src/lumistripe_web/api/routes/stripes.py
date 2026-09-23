"""Physical stripe topology and test routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...contracts.common import DashboardState
from ...contracts.stripes import (
    StripeTestRequest,
    StripeTopology,
    StripeTopologyRequest,
)
from ...settings import StripeOutputSettings, StripeTopologySettings
from ..dependencies import await_command, runtime_from_request

router = APIRouter()


def topology_settings(body: StripeTopologyRequest) -> StripeTopologySettings:
    return StripeTopologySettings(
        layout=body.layout,
        power_budget_enabled=body.power_budget_enabled,
        power_budget_watts=body.power_budget_watts,
        outputs=tuple(
            StripeOutputSettings(
                **output.model_dump(exclude={"last_output_at", "error"})
            )
            for output in body.outputs
        ),
    )


@router.get("/api/stripes", response_model=StripeTopology)
async def stripe_topology(request: Request) -> StripeTopology:
    return runtime_from_request(request).snapshot().stripe_topology


@router.put("/api/stripes", response_model=DashboardState)
async def update_stripe_topology(
    request: Request, body: StripeTopologyRequest
) -> DashboardState:
    try:
        topology = topology_settings(body)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await await_command(runtime_from_request(request).apply_stripe_topology(topology))


@router.post("/api/stripes/test", response_model=DashboardState)
async def test_stripe(request: Request, body: StripeTestRequest) -> DashboardState:
    try:
        topology = topology_settings(body.topology) if body.topology else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await await_command(
        runtime_from_request(request).test_stripe(body.stripe_id, body.pattern, topology)
    )


__all__ = ["router", "topology_settings"]
