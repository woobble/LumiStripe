"""Authenticated WebSocket streams for dashboard state and previews."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException

from ...auth import SESSION_COOKIE, PairingAuth
from ...contracts.audio import AudioTelemetry
from ...runtime import LumiStripeRuntime
from ..protocols.preview import encode_preview_frame

WEBSOCKET_INTERVAL_SECONDS = 0.25
AUDIO_WEBSOCKET_INTERVAL_SECONDS = 1.0 / 15.0
PREVIEW_WEBSOCKET_INTERVAL_SECONDS = 1.0 / 30.0

router = APIRouter()


def _require_access(websocket: WebSocket) -> PairingAuth:
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
    _require_access(websocket)
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
    _require_access(websocket)
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


__all__ = ["router"]
