"""Animation settings routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from ...contracts.animations import (
    AnimationSettingsResponse,
    AnimationSettingsUpdateRequest,
)
from ...runtime import (
    InvalidAnimationSettingsError,
    UnknownAnimationError,
)
from ..dependencies import await_command, runtime_from_request

router = APIRouter()


def _settings_response(
    request: Request, animation_name: str, stripe_id: str | None
) -> AnimationSettingsResponse:
    try:
        return runtime_from_request(request).animation_settings(
            animation_name, stripe_id
        )
    except UnknownAnimationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidAnimationSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


@router.get(
    "/api/animations/{animation_name}/settings",
    response_model=AnimationSettingsResponse,
)
async def get_animation_settings(
    request: Request,
    animation_name: str = Path(min_length=1, max_length=100),
    stripe_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> AnimationSettingsResponse:
    return _settings_response(request, animation_name, stripe_id)


@router.put(
    "/api/animations/{animation_name}/settings",
    response_model=AnimationSettingsResponse,
)
async def update_animation_settings(
    request: Request,
    body: AnimationSettingsUpdateRequest,
    animation_name: str = Path(min_length=1, max_length=100),
) -> AnimationSettingsResponse:
    runtime = runtime_from_request(request)
    await await_command(
        runtime.set_animation_settings(
            animation_name, body.values, stripe_id=body.stripe_id
        )
    )
    return _settings_response(request, animation_name, body.stripe_id)


@router.post(
    "/api/animations/{animation_name}/settings/reset",
    response_model=AnimationSettingsResponse,
)
async def reset_animation_settings(
    request: Request,
    animation_name: str = Path(min_length=1, max_length=100),
    stripe_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> AnimationSettingsResponse:
    runtime = runtime_from_request(request)
    await await_command(
        runtime.reset_animation_settings(animation_name, stripe_id=stripe_id)
    )
    return _settings_response(request, animation_name, stripe_id)


__all__ = ["router"]
