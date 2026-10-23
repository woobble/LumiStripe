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
    SwitchQuantization,
)
from .engine import PlaybackEngine, demo_snapshot
from .quantized import QuantizedSwitchCoordinator, QuantizedSwitchDiagnostics
from .renderer import PlaybackRenderer
from .sequence import (
    AnimationSequence,
    AnimationSequenceCoordinator,
    AnimationSequenceStep,
    SequencePlaybackState,
)
from .state import PlaybackDecision, RenderPlan, build_render_plan, decide_playback

__all__ = [
    "AnimationSequence",
    "AnimationSequenceCoordinator",
    "AnimationSequenceStep",
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
    "QuantizedSwitchCoordinator",
    "QuantizedSwitchDiagnostics",
    "ReactiveFrameSmoother",
    "RenderPlan",
    "SequencePlaybackState",
    "SwitchQuantization",
    "build_render_plan",
    "decide_playback",
    "demo_snapshot",
]
