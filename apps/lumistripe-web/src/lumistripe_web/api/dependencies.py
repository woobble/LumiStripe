"""Shared dependencies and command error mapping for API routers."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future

from fastapi import HTTPException, Request, status

from ..runtime import (
    LumiStripeRuntime,
    RuntimeCommandError,
    RuntimeUnavailableError,
    UnknownAnimationError,
)

COMMAND_TIMEOUT_SECONDS = 5.0


def runtime_from_request(request: Request) -> LumiStripeRuntime:
    return request.app.state.runtime


async def await_command[T](future: Future[T]) -> T:
    try:
        return await asyncio.wait_for(
            asyncio.wrap_future(future),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="runtime command timed out",
        ) from exc
    except UnknownAnimationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RuntimeCommandError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except RuntimeUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


__all__ = ["await_command", "runtime_from_request"]
