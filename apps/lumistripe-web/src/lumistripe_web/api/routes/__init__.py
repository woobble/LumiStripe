"""Feature routers assembled by :mod:`lumistripe_web.api`."""

from .audio import router as audio_router
from .auth import router as auth_router
from .control import router as control_router
from .startup import router as startup_router
from .stripes import router as stripes_router
from .system import router as system_router
from .websocket import router as websocket_router

__all__ = [
    "audio_router",
    "auth_router",
    "control_router",
    "startup_router",
    "stripes_router",
    "system_router",
    "websocket_router",
]
