"""Pure playback decisions and render plans."""

from __future__ import annotations

from dataclasses import dataclass

from ..audio.types import AudioFrame, AudioSnapshot
from ..color import Color
from .activity import MusicGateState
from .config import AudioSource, PlaybackMode


@dataclass(frozen=True, slots=True)
class PlaybackDecision:
    """Policy output that does not mutate pixels or touch hardware."""

    mode: PlaybackMode
    animation_name: str | None
    music_active: bool
    gate_state: MusicGateState
    audio_enabled: bool
    clear_output: bool = False
    solid_color: Color | None = None
    brightness: float = 1.0
    should_switch: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RenderPlan:
    """The minimum input a renderer needs after policy has been evaluated."""

    decision: PlaybackDecision
    snapshot: AudioSnapshot | None = None
    audio_frame: AudioFrame | None = None
    transition_ms: int = 0
    flush: bool = True


def decide_playback(
    mode: PlaybackMode,
    *,
    animation_name: str | None,
    music_active: bool = False,
    gate_state: MusicGateState = MusicGateState.IDLE,
    audio_source: AudioSource | None = None,
    solid_color: Color | None = None,
    brightness: float = 1.0,
    should_switch: bool = False,
) -> PlaybackDecision:
    """Evaluate mode policy without selecting animations or rendering."""

    if mode is PlaybackMode.SOLID:
        return PlaybackDecision(
            mode=mode,
            animation_name=animation_name,
            music_active=False,
            gate_state=MusicGateState.IDLE,
            audio_enabled=False,
            solid_color=solid_color,
            brightness=brightness,
            reason="solid-mode",
        )
    if mode is PlaybackMode.DYNAMIC and not music_active:
        return PlaybackDecision(
            mode=mode,
            animation_name=animation_name,
            music_active=False,
            gate_state=gate_state,
            audio_enabled=False,
            clear_output=True,
            brightness=brightness,
            reason="music-gate-closed",
        )

    audio_enabled = mode is PlaybackMode.DYNAMIC or (
        audio_source not in (None, AudioSource.OFF)
    )
    return PlaybackDecision(
        mode=mode,
        animation_name=animation_name,
        music_active=music_active,
        gate_state=gate_state,
        audio_enabled=audio_enabled,
        brightness=brightness,
        should_switch=should_switch,
        reason="dynamic-music" if mode is PlaybackMode.DYNAMIC else "animation-mode",
    )


def build_render_plan(
    decision: PlaybackDecision,
    *,
    snapshot: AudioSnapshot | None = None,
    transition_ms: int = 0,
    flush: bool = True,
) -> RenderPlan:
    """Bind immutable audio/transition inputs to a policy decision."""

    return RenderPlan(
        decision=decision,
        snapshot=snapshot,
        audio_frame=None if snapshot is None or not decision.audio_enabled else snapshot.frame,
        transition_ms=max(0, transition_ms),
        flush=flush,
    )


__all__ = [
    "PlaybackDecision",
    "RenderPlan",
    "build_render_plan",
    "decide_playback",
]
