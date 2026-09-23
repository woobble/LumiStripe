"""Public playback policy, state, and rendering boundaries."""

from .activity import (
    MusicActivityConfig,
    MusicActivityDetector,
    MusicGateState,
    ReactiveFrameSmoother,
)
from .config import (
    AudioSource,
    CycleOrder,
    CycleTiming,
    CyclingConfig,
    PlaybackConfig,
    PlaybackMode,
)
from .engine import PlaybackEngine, demo_snapshot
from .renderer import PlaybackRenderer
from .state import PlaybackDecision, RenderPlan, build_render_plan, decide_playback

__all__ = [
    "AudioSource",
    "CycleOrder",
    "CycleTiming",
    "CyclingConfig",
    "MusicActivityConfig",
    "MusicActivityDetector",
    "MusicGateState",
    "PlaybackConfig",
    "PlaybackDecision",
    "PlaybackEngine",
    "PlaybackMode",
    "PlaybackRenderer",
    "ReactiveFrameSmoother",
    "RenderPlan",
    "build_render_plan",
    "decide_playback",
    "demo_snapshot",
]
