from __future__ import annotations

import asyncio
import struct
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
    AudioCalibrationFinishRequest,
    AudioCalibrationSessionResponse,
    AudioCalibrationStartRequest,
    AudioDeviceRequest,
    AudioOutputMuteRequest,
    AudioOutputSelectionRequest,
    AudioOutputVolumeRequest,
    AudioResetRequest,
    AudioSettingsRequest,
    AudioSettingsResponse,
    AudioSourceRequest,
    AudioTelemetry,
    BlackoutRequest,
    BluetoothAliasRequest,
    BluetoothDeviceRequest,
    BluetoothPowerRequest,
    BluetoothStatusResponse,
    BrightnessRequest,
    CalibrationFinishRequest,
    CalibrationSessionResponse,
    CalibrationStartRequest,
    CalibrationUpdateRequest,
    DashboardState,
    ModeRequest,
    PairingRequest,
    StartupSettingsRequest,
    StartupSettingsResponse,
    StripeTestRequest,
    StripeTopology,
    StripeTopologyRequest,
)
from .runtime import (
    LumiStripeRuntime,
    PreviewFrame,
    RuntimeCommandError,
    RuntimeUnavailableError,
    UnknownAnimationError,
)
from .settings import AudioTuningProfile, StripeOutputSettings, StripeTopologySettings

COMMAND_TIMEOUT_SECONDS = 5.0
WEBSOCKET_INTERVAL_SECONDS = 0.25
AUDIO_WEBSOCKET_INTERVAL_SECONDS = 1.0 / 15.0
PREVIEW_WEBSOCKET_INTERVAL_SECONDS = 1.0 / 30.0
PREVIEW_HEADER = struct.Struct("<4sBBIH")
PREVIEW_OUTPUT_HEADER = struct.Struct("<I")
PREVIEW_MAGIC = b"LSFP"
PREVIEW_VERSION = 1

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
        _runtime_from_request(request).set_mode(
            body.mode, solid_color=body.color, stripe_id=body.stripe_id,
            music_recognition_enabled=body.music_recognition_enabled,
        )
    )


@router.put("/api/brightness", response_model=DashboardState)
async def set_brightness(request: Request, body: BrightnessRequest) -> DashboardState:
    return await _await_command(
        _runtime_from_request(request).set_brightness(
            body.brightness, stripe_id=body.stripe_id
        )
    )


@router.put("/api/animation", response_model=DashboardState)
async def select_animation(request: Request, body: AnimationRequest) -> DashboardState:
    return await _await_command(
        _runtime_from_request(request).select_animation(
            body.name, stripe_id=body.stripe_id
        )
    )


@router.post("/api/blackout", response_model=DashboardState)
async def set_blackout(request: Request, body: BlackoutRequest) -> DashboardState:
    return await _await_command(
        _runtime_from_request(request).set_blackout(
            body.enabled, stripe_id=body.stripe_id
        )
    )


@router.get("/api/stripes", response_model=StripeTopology)
async def stripe_topology(request: Request) -> StripeTopology:
    return _runtime_from_request(request).snapshot().stripe_topology


@router.put("/api/stripes", response_model=DashboardState)
async def update_stripe_topology(
    request: Request, body: StripeTopologyRequest
) -> DashboardState:
    try:
        topology = _topology_settings(body)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _await_command(
        _runtime_from_request(request).apply_stripe_topology(topology)
    )


@router.post("/api/stripes/test", response_model=DashboardState)
async def test_stripe(request: Request, body: StripeTestRequest) -> DashboardState:
    try:
        topology = _topology_settings(body.topology) if body.topology else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _await_command(
        _runtime_from_request(request).test_stripe(
            body.stripe_id, body.pattern, topology
        )
    )


def _topology_settings(body: StripeTopologyRequest) -> StripeTopologySettings:
    return StripeTopologySettings(
        layout=body.layout,
        outputs=tuple(
            StripeOutputSettings(
                **output.model_dump(exclude={"last_output_at", "error"})
            )
            for output in body.outputs
        ),
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


@router.put("/api/audio/source", response_model=AudioSettingsResponse)
async def select_audio_source(
    request: Request, body: AudioSourceRequest
) -> AudioSettingsResponse:
    return await _await_command(
        _runtime_from_request(request).set_audio_source(body.source)
    )


@router.get("/api/audio/bluetooth", response_model=BluetoothStatusResponse)
async def bluetooth_status(request: Request) -> BluetoothStatusResponse:
    return _runtime_from_request(request).bluetooth_status()


@router.put("/api/audio/bluetooth/power", response_model=BluetoothStatusResponse)
async def bluetooth_power(
    request: Request, body: BluetoothPowerRequest
) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).set_bluetooth_power(body.powered)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/bluetooth/alias", response_model=BluetoothStatusResponse)
async def bluetooth_alias(
    request: Request, body: BluetoothAliasRequest
) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).set_bluetooth_alias(body.alias)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/scan", response_model=BluetoothStatusResponse)
async def bluetooth_scan(request: Request) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).start_bluetooth_scan()
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/pair", response_model=BluetoothStatusResponse)
async def bluetooth_pair(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).pair_bluetooth_device(body.address)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/connect", response_model=BluetoothStatusResponse)
async def bluetooth_connect(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).connect_bluetooth_device(
            body.address, body.role
        )
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/forget", response_model=BluetoothStatusResponse)
async def bluetooth_forget(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).forget_bluetooth_device(body.address)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/audio/bluetooth/disconnect", response_model=BluetoothStatusResponse)
async def bluetooth_disconnect(
    request: Request, body: BluetoothDeviceRequest
) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).disconnect_bluetooth_device(body.address)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/output", response_model=BluetoothStatusResponse)
async def select_audio_output(
    request: Request, body: AudioOutputSelectionRequest
) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).set_audio_output(body.selector)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/output/volume", response_model=BluetoothStatusResponse)
async def set_audio_output_volume(
    request: Request, body: AudioOutputVolumeRequest
) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).set_audio_output_volume(
            body.selector, body.volume
        )
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/output/mute", response_model=BluetoothStatusResponse)
async def set_audio_output_mute(
    request: Request, body: AudioOutputMuteRequest
) -> BluetoothStatusResponse:
    try:
        return _runtime_from_request(request).set_audio_output_mute(
            body.selector, body.muted
        )
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/api/audio/device", response_model=AudioSettingsResponse)
async def select_audio_device(
    request: Request, body: AudioDeviceRequest
) -> AudioSettingsResponse:
    return await _await_command(
        _runtime_from_request(request).select_audio_device(body.device)
    )


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


@router.post("/api/audio/calibration/session", response_model=AudioCalibrationSessionResponse)
async def start_audio_calibration(request: Request, body: AudioCalibrationStartRequest) -> AudioCalibrationSessionResponse:
    return await _await_command(_runtime_from_request(request).start_audio_calibration(body.device, body.duration_seconds))


@router.get("/api/audio/calibration/session/{session_id}", response_model=AudioCalibrationSessionResponse)
async def audio_calibration_status(request: Request, session_id: str) -> AudioCalibrationSessionResponse:
    try:
        return _runtime_from_request(request).audio_calibration_status(session_id)
    except RuntimeCommandError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/audio/calibration/session/{session_id}/finish", response_model=AudioSettingsResponse)
async def finish_audio_calibration(request: Request, session_id: str, body: AudioCalibrationFinishRequest) -> AudioSettingsResponse:
    result = await _await_command(_runtime_from_request(request).finish_audio_calibration(session_id, apply=body.apply, target_level=body.target_level, noise_floor=body.noise_floor))
    if isinstance(result, AudioCalibrationSessionResponse):
        raise HTTPException(status_code=409, detail="audio calibration is not complete")
    return result


@router.get("/api/startup", response_model=StartupSettingsResponse)
async def startup_settings(request: Request) -> StartupSettingsResponse:
    return _runtime_from_request(request).startup_settings()


@router.put("/api/startup", response_model=StartupSettingsResponse)
async def update_startup_settings(
    request: Request, body: StartupSettingsRequest
) -> StartupSettingsResponse:
    return await _await_command(
        _runtime_from_request(request).set_startup_restore(body.restore_last_state)
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


@router.websocket("/ws/preview")
async def websocket_preview(websocket: WebSocket) -> None:
    access: PairingAuth = websocket.app.state.access
    if not access.authenticated(websocket.cookies.get(SESSION_COOKIE)):
        raise WebSocketException(code=4401, reason="pairing required")
    await websocket.accept()
    runtime: LumiStripeRuntime = websocket.app.state.runtime
    last_sequence = -1
    try:
        while True:
            frame = runtime.preview_frame()
            if frame.sequence != last_sequence:
                await websocket.send_bytes(encode_preview_frame(frame))
                last_sequence = frame.sequence
                if not runtime.snapshot().running:
                    await websocket.close(code=1011, reason="runtime unavailable")
                    return
            await asyncio.sleep(PREVIEW_WEBSOCKET_INTERVAL_SECONDS)
    except (WebSocketDisconnect, RuntimeError):
        return


def encode_preview_frame(frame: PreviewFrame) -> bytes:
    """Encode an immutable runtime frame for the browser preview stream."""
    packet = bytearray(
        PREVIEW_HEADER.pack(
            PREVIEW_MAGIC,
            PREVIEW_VERSION,
            0,
            frame.sequence & 0xFFFFFFFF,
            len(frame.outputs),
        )
    )
    for output in frame.outputs:
        if len(output) % 4:
            raise ValueError("preview output must contain complete RGBA pixels")
        packet.extend(PREVIEW_OUTPUT_HEADER.pack(len(output) // 4))
        packet.extend(output)
    return bytes(packet)


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
