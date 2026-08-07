from __future__ import annotations

import asyncio
from concurrent.futures import Future

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from fastapi.responses import JSONResponse
from lumistripe import ColorCorrection

from .auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    InvalidPairingCodeError,
    PairingAuth,
    PairingRateLimitedError,
)
from .models import (
    AccessStatus,
    AnimationList,
    AnimationRequest,
    AudioResetRequest,
    AudioSettingsRequest,
    AudioSettingsResponse,
    AudioTelemetry,
    BlackoutRequest,
    BrightnessRequest,
    CalibrationFinishRequest,
    CalibrationSessionResponse,
    CalibrationStartRequest,
    CalibrationUpdateRequest,
    DashboardState,
    ModeRequest,
    PairingRequest,
)
from .runtime import (
    LumiStripeRuntime,
    RuntimeCommandError,
    RuntimeUnavailableError,
    UnknownAnimationError,
)
from .settings import AudioTuningProfile

COMMAND_TIMEOUT_SECONDS = 5.0
WEBSOCKET_INTERVAL_SECONDS = 0.25
AUDIO_WEBSOCKET_INTERVAL_SECONDS = 1.0 / 15.0

router = APIRouter()


def _runtime_from_request(request: Request) -> LumiStripeRuntime:
    return request.app.state.runtime


def _access_from_request(request: Request) -> PairingAuth:
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


@router.get("/api/health")
async def health(request: Request) -> JSONResponse:
    runtime = _runtime_from_request(request)
    state = runtime.snapshot()
    if runtime.healthy:
        return JSONResponse({"status": "ok"})
    return JSONResponse(
        {"status": "unavailable", "error": state.error or "runtime is not running"},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@router.get("/api/state", response_model=DashboardState)
async def state(request: Request) -> DashboardState:
    return _runtime_from_request(request).snapshot()


@router.get("/api/animations", response_model=AnimationList)
async def animations(request: Request) -> AnimationList:
    return AnimationList(items=_runtime_from_request(request).animations())


@router.put("/api/mode", response_model=DashboardState)
async def set_mode(request: Request, body: ModeRequest) -> DashboardState:
    return await _await_command(
        _runtime_from_request(request).set_mode(body.mode, solid_color=body.color)
    )


@router.put("/api/brightness", response_model=DashboardState)
async def set_brightness(request: Request, body: BrightnessRequest) -> DashboardState:
    return await _await_command(
        _runtime_from_request(request).set_brightness(body.brightness)
    )


@router.put("/api/animation", response_model=DashboardState)
async def select_animation(request: Request, body: AnimationRequest) -> DashboardState:
    return await _await_command(
        _runtime_from_request(request).select_animation(body.name)
    )


@router.post("/api/blackout", response_model=DashboardState)
async def set_blackout(request: Request, body: BlackoutRequest) -> DashboardState:
    return await _await_command(
        _runtime_from_request(request).set_blackout(body.enabled)
    )


@router.post("/api/calibration/session", response_model=CalibrationSessionResponse)
async def start_calibration(
    request: Request,
    body: CalibrationStartRequest,
) -> CalibrationSessionResponse:
    return await _await_command(
        _runtime_from_request(request).start_calibration(body.output_index)
    )


@router.put("/api/calibration/session/{session_id}", response_model=DashboardState)
async def update_calibration(
    request: Request,
    session_id: str,
    body: CalibrationUpdateRequest,
) -> DashboardState:
    return await _await_command(
        _runtime_from_request(request).update_calibration(
            session_id,
            ColorCorrection(body.red, body.green, body.blue),
            body.pattern,
        )
    )


@router.post(
    "/api/calibration/session/{session_id}/finish",
    response_model=DashboardState,
)
async def finish_calibration(
    request: Request,
    session_id: str,
    body: CalibrationFinishRequest,
) -> DashboardState:
    return await _await_command(
        _runtime_from_request(request).finish_calibration(
            session_id,
            save=body.save,
        )
    )


@router.get("/api/audio/settings", response_model=AudioSettingsResponse)
async def audio_settings(request: Request) -> AudioSettingsResponse:
    return _runtime_from_request(request).audio_settings()


@router.put("/api/audio/settings", response_model=AudioSettingsResponse)
async def update_audio_settings(
    request: Request,
    body: AudioSettingsRequest,
) -> AudioSettingsResponse:
    profile = AudioTuningProfile(**body.settings.model_dump())
    return await _await_command(
        _runtime_from_request(request).apply_audio_settings(body.device, profile)
    )


@router.post("/api/audio/settings/reset", response_model=AudioSettingsResponse)
async def reset_audio_settings(
    request: Request,
    body: AudioResetRequest,
) -> AudioSettingsResponse:
    return await _await_command(
        _runtime_from_request(request).reset_audio_settings(body.device)
    )


@router.websocket("/ws/state")
async def websocket_state(websocket: WebSocket) -> None:
    access: PairingAuth = websocket.app.state.access
    if not access.authenticated(websocket.cookies.get(SESSION_COOKIE)):
        raise WebSocketException(code=4401, reason="pairing required")
    await websocket.accept()
    runtime: LumiStripeRuntime = websocket.app.state.runtime
    last_revision = -1
    try:
        while True:
            snapshot = runtime.snapshot()
            if snapshot.revision != last_revision:
                await websocket.send_json(snapshot.model_dump(mode="json"))
                last_revision = snapshot.revision
                if not snapshot.running:
                    await websocket.close(code=1011, reason="runtime unavailable")
                    return
            await asyncio.sleep(WEBSOCKET_INTERVAL_SECONDS)
    except (WebSocketDisconnect, RuntimeError):
        return


@router.websocket("/ws/audio")
async def websocket_audio(websocket: WebSocket) -> None:
    access: PairingAuth = websocket.app.state.access
    if not access.authenticated(websocket.cookies.get(SESSION_COOKIE)):
        raise WebSocketException(code=4401, reason="pairing required")
    await websocket.accept()
    runtime: LumiStripeRuntime = websocket.app.state.runtime
    try:
        while True:
            telemetry: AudioTelemetry = runtime.audio_telemetry()
            await websocket.send_json(telemetry.model_dump(mode="json"))
            await asyncio.sleep(AUDIO_WEBSOCKET_INTERVAL_SECONDS)
    except (WebSocketDisconnect, RuntimeError):
        return


async def _await_command[T](future: Future[T]) -> T:
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
