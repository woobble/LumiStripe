"""Pairing and session endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from ...auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    InvalidPairingCodeError,
    PairingRateLimitedError,
)
from ...contracts.common import AccessStatus, PairingRequest

router = APIRouter()


def _access_from_request(request: Request):
    return request.app.state.access


@router.get("/api/auth/status", response_model=AccessStatus)
async def access_status(request: Request) -> AccessStatus:
    access = _access_from_request(request)
    return AccessStatus(
        required=access.required,
        authenticated=access.authenticated(request.cookies.get(SESSION_COOKIE)),
    )


@router.post("/api/auth/pair", response_model=AccessStatus)
async def pair(
    request: Request,
    response: Response,
    body: PairingRequest,
) -> AccessStatus:
    access = _access_from_request(request)
    client_id = request.client.host if request.client is not None else "unknown"
    try:
        session = access.pair(body.code, client_id)
    except InvalidPairingCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except PairingRateLimitedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        path="/",
    )
    return AccessStatus(required=access.required, authenticated=True)


@router.post("/api/auth/logout", response_model=AccessStatus)
async def logout(request: Request, response: Response) -> AccessStatus:
    access = _access_from_request(request)
    access.revoke(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
    return AccessStatus(required=access.required, authenticated=not access.required)


__all__ = ["router"]
