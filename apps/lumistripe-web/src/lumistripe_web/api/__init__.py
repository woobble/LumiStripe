"""HTTP and WebSocket API assembly for the LumiStripe dashboard."""

from fastapi import APIRouter

from .protocols.preview import encode_preview_frame as encode_preview_frame
from .routes import (
    audio_router,
    auth_router,
    control_router,
    startup_router,
    stripes_router,
    system_router,
    websocket_router,
)

router = APIRouter()
for feature_router in (
    auth_router,
    system_router,
    control_router,
    stripes_router,
    audio_router,
    startup_router,
    websocket_router,
):
    router.include_router(feature_router)

__all__ = ["encode_preview_frame", "router"]
