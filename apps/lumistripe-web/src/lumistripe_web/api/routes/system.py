"""Health and dashboard snapshot routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from ...contracts.common import AnimationList, DashboardState
from ..dependencies import runtime_from_request

router = APIRouter()


@router.get("/api/health")
async def health(request: Request) -> JSONResponse:
    runtime = runtime_from_request(request)
    state = runtime.snapshot()
    if runtime.healthy:
        return JSONResponse({"status": "ok"})
    return JSONResponse(
        {"status": "unavailable", "error": state.error or "runtime is not running"},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@router.get("/api/state", response_model=DashboardState)
async def state(request: Request) -> DashboardState:
    return runtime_from_request(request).snapshot()


@router.get("/api/animations", response_model=AnimationList)
async def animations(request: Request) -> AnimationList:
    return AnimationList(items=runtime_from_request(request).animations())


__all__ = ["router"]
