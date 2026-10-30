"""Authenticated WebSocket streams for dashboard state and previews."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException

from ...auth import SESSION_COOKIE, PairingAuth
from ...contracts.audio import AudioTelemetry
from ...runtime import LumiStripeRuntime
from ...runtime.cadence import (
    AUDIO_TELEMETRY_INTERVAL_SECONDS,
    DASHBOARD_STATE_INTERVAL_SECONDS,
    PREVIEW_FRAME_INTERVAL_SECONDS,
)
from ..protocols.preview import encode_preview_frame

WEBSOCKET_INTERVAL_SECONDS = DASHBOARD_STATE_INTERVAL_SECONDS
AUDIO_WEBSOCKET_INTERVAL_SECONDS = AUDIO_TELEMETRY_INTERVAL_SECONDS
PREVIEW_WEBSOCKET_INTERVAL_SECONDS = PREVIEW_FRAME_INTERVAL_SECONDS

router = APIRouter()


def _require_access(websocket: WebSocket) -> PairingAuth:
    origin = websocket.headers.get("origin")
    if origin is not None:
        try:
            parsed = urlsplit(origin)
        except ValueError as exc:
            raise WebSocketException(code=1008, reason="invalid origin") from exc
        scheme = "https" if websocket.url.scheme == "wss" else "http"
        if (parsed.scheme != scheme or parsed.netloc != websocket.headers.get("host")
                or parsed.path or parsed.query or parsed.fragment):
            raise WebSocketException(code=1008, reason="invalid origin")
    access: PairingAuth = websocket.app.state.access
    if not access.authenticated(websocket.cookies.get(SESSION_COOKIE)):
        raise WebSocketException(code=4401, reason="pairing required")
    return access


@router.websocket("/ws/state")
async def websocket_state(websocket: WebSocket) -> None:
    _require_access(websocket)
    await websocket.accept()
    runtime: LumiStripeRuntime = websocket.app.state.runtime
    last_revision = -1
    try:
        while True:
            if not _session_active(websocket):
                await websocket.close(code=4401, reason="pairing required")
                return
            snapshot = runtime.snapshot()
            if snapshot.revision != last_revision:
                await websocket.send_json(snapshot.model_dump(mode="json"))
                last_revision = snapshot.revision
                if not snapshot.running:
                    await websocket.close(code=1011, reason="runtime unavailable")
                    return
            await asyncio.sleep(WEBSOCKET_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return
    except RuntimeError:
        await _close_runtime_failure(websocket)


@router.websocket("/ws/audio")
async def websocket_audio(websocket: WebSocket) -> None:
    _require_access(websocket)
    await websocket.accept()
    runtime: LumiStripeRuntime = websocket.app.state.runtime
    try:
        while True:
            if not _session_active(websocket):
                await websocket.close(code=4401, reason="pairing required")
                return
            telemetry: AudioTelemetry = runtime.audio_telemetry()
            await websocket.send_json(telemetry.model_dump(mode="json"))
            await asyncio.sleep(AUDIO_WEBSOCKET_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return
    except RuntimeError:
        await _close_runtime_failure(websocket)


@router.websocket("/ws/preview")
async def websocket_preview(websocket: WebSocket) -> None:
    _require_access(websocket)
    await websocket.accept()
    runtime: LumiStripeRuntime = websocket.app.state.runtime
    last_sequence = -1
    try:
        while True:
            if not _session_active(websocket):
                await websocket.close(code=4401, reason="pairing required")
                return
            frame = runtime.preview_frame()
            if frame.sequence != last_sequence:
                await websocket.send_bytes(encode_preview_frame(frame))
                last_sequence = frame.sequence
                if not runtime.snapshot().running:
                    await websocket.close(code=1011, reason="runtime unavailable")
                    return
            await asyncio.sleep(PREVIEW_WEBSOCKET_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return
    except RuntimeError:
        await _close_runtime_failure(websocket)


async def _close_runtime_failure(websocket: WebSocket) -> None:
    try:
        await websocket.close(code=1011, reason="runtime unavailable")
    except (WebSocketDisconnect, RuntimeError):
        return


def _session_active(websocket: WebSocket) -> bool:
    access: PairingAuth = websocket.app.state.access
    return access.authenticated(websocket.cookies.get(SESSION_COOKIE))


__all__ = ["router"]
