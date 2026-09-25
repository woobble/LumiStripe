"""Typed commands accepted by the runtime worker.

Commands contain data only. Hardware mutation stays in the worker's execution
loop in ``runtime.__init__`` so API threads cannot mutate controllers directly.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Literal

from lumistripe.controller import ColorCorrection
from lumistripe.playback import PlaybackMode

from ..settings import AudioTuningProfile, StripeTopologySettings

CalibrationPattern = Literal["white", "red", "green", "blue"]


@dataclass(slots=True)
class _Command:
    name: str
    value: object
    future: Future[object]
    coalesced_futures: list[Future[object]] = field(default_factory=list)

    def futures(self) -> tuple[Future[object], ...]:
        return (self.future, *self.coalesced_futures)


@dataclass(frozen=True, slots=True)
class _ModeCommand:
    mode: PlaybackMode
    color: str | None = None
    stripe_id: str | None = None
    music_recognition_enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class _TargetCommand:
    value: object
    stripe_id: str | None = None


@dataclass(frozen=True, slots=True)
class _StripeTopologyCommand:
    topology: StripeTopologySettings


@dataclass(frozen=True, slots=True)
class _StripeTestCommand:
    stripe_id: str
    pattern: str
    topology: StripeTopologySettings | None = None


@dataclass(frozen=True, slots=True)
class _CalibrationStartCommand:
    output_index: int


@dataclass(frozen=True, slots=True)
class _CalibrationUpdateCommand:
    session_id: str
    correction: ColorCorrection
    pattern: CalibrationPattern


@dataclass(frozen=True, slots=True)
class _CalibrationFinishCommand:
    session_id: str
    save: bool


@dataclass(frozen=True, slots=True)
class _AudioSettingsCommand:
    device: str
    profile: AudioTuningProfile


@dataclass(frozen=True, slots=True)
class _AudioDeviceCommand:
    device: str


@dataclass(frozen=True, slots=True)
class _AudioSourceCommand:
    source: str


@dataclass(frozen=True, slots=True)
class _SpotifyCommand:
    action: str
    value: object | None = None


@dataclass(frozen=True, slots=True)
class _AudioCalibrationStartCommand:
    device: str
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class _AudioCalibrationFinishCommand:
    session_id: str
    apply: bool
    target_level: float | None = None
    noise_floor: float | None = None


@dataclass(frozen=True, slots=True)
class _StartupSettingsCommand:
    restore_last_state: bool


__all__ = [
    "_AudioCalibrationFinishCommand",
    "_AudioCalibrationStartCommand",
    "_AudioDeviceCommand",
    "_AudioSettingsCommand",
    "_AudioSourceCommand",
    "_CalibrationFinishCommand",
    "_CalibrationStartCommand",
    "_CalibrationUpdateCommand",
    "_Command",
    "_ModeCommand",
    "_SpotifyCommand",
    "_StartupSettingsCommand",
    "_StripeTestCommand",
    "_StripeTopologyCommand",
    "_TargetCommand",
]
