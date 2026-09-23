"""Music activity gating and reactive smoothing state."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import cast

from ..audio.types import AudioFrame, BandTuple, MusicFeatures


class MusicGateState(str, Enum):
    IDLE = "idle"
    CANDIDATE = "candidate"
    MUSIC = "music"


@dataclass(frozen=True, slots=True)
class MusicActivityConfig:
    idle_enter_frames: int = 60
    activation_delay_s: float = 0.75
    feature_attack: float = 0.28
    feature_release: float = 0.08
    energy_threshold: float = 0.03
    onset_threshold: float = 0.025
    beat_density_threshold: float = 0.05
    brightness_threshold: float = 0.08
    spectral_balance_ratio: float = 0.35
    activation_hysteresis_ratio: float = 1.35
    beat_density_window_s: float = 2.0

    def __post_init__(self) -> None:
        if self.idle_enter_frames <= 0:
            raise ValueError("idle_enter_frames must be greater than zero")
        if self.activation_delay_s < 0.0:
            raise ValueError("activation_delay_s must not be negative")
        if not 0.0 <= self.spectral_balance_ratio <= 1.0:
            raise ValueError("spectral_balance_ratio must be between zero and one")
        if self.activation_hysteresis_ratio < 1.0:
            raise ValueError("activation_hysteresis_ratio must be at least one")
        if self.beat_density_window_s <= 0.0:
            raise ValueError("beat_density_window_s must be greater than zero")
        for name in (
            "feature_attack",
            "feature_release",
            "energy_threshold",
            "onset_threshold",
            "beat_density_threshold",
            "brightness_threshold",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be between zero and one")


@dataclass(slots=True)
class MusicActivityDetector:
    config: MusicActivityConfig = field(default_factory=MusicActivityConfig)
    active: bool = False
    inactive_frames: int = 0
    energy: float = 0.0
    onset: float = 0.0
    beat_density: float = 0.0
    brightness: float = 0.0
    candidate_since_s: float | None = None
    beat_samples: deque[tuple[float, bool]] = field(default_factory=deque)

    @property
    def state(self) -> MusicGateState:
        if self.active:
            return MusicGateState.MUSIC
        if self.candidate_since_s is not None:
            return MusicGateState.CANDIDATE
        return MusicGateState.IDLE

    def reset(self) -> None:
        self.active = False
        self.inactive_frames = 0
        self.energy = 0.0
        self.onset = 0.0
        self.beat_density = 0.0
        self.brightness = 0.0
        self.candidate_since_s = None
        self.beat_samples.clear()

    def update(self, features: MusicFeatures, *, now_s: float | None = None) -> bool:
        cfg = self.config
        now = time.monotonic() if now_s is None else now_s
        self.energy = _smooth(self.energy, features.energy, cfg.feature_attack, cfg.feature_release)
        self.onset = _smooth(
            self.onset,
            features.onset_strength,
            cfg.feature_attack,
            cfg.feature_release,
        )
        if self.beat_samples and now < self.beat_samples[-1][0]:
            self.beat_samples.clear()
        self.beat_samples.append((now, features.beat))
        cutoff = now - cfg.beat_density_window_s
        while self.beat_samples and self.beat_samples[0][0] < cutoff:
            self.beat_samples.popleft()
        self.beat_density = sum(beat for _, beat in self.beat_samples) / len(self.beat_samples)
        self.brightness = _smooth(self.brightness, features.brightness, 0.2, 0.1)

        threshold_scale = 1.0 if self.active else cfg.activation_hysteresis_ratio
        signal = self.energy >= cfg.energy_threshold * threshold_scale
        rhythmic = (
            self.beat_density >= cfg.beat_density_threshold * threshold_scale
            and self.onset >= cfg.onset_threshold * threshold_scale
        )
        spectral_floor = features.mid_energy * cfg.spectral_balance_ratio
        broadband = (
            features.bass_energy >= cfg.energy_threshold * threshold_scale
            and features.treble_energy >= cfg.brightness_threshold * threshold_scale
            and min(features.bass_energy, features.treble_energy) >= spectral_floor
        )
        detected = not features.silence and signal and (rhythmic or broadband)

        if self.active:
            self.candidate_since_s = None
            if detected:
                self.inactive_frames = 0
                return True
            self.inactive_frames += 1
            if self.inactive_frames >= cfg.idle_enter_frames:
                self.active = False
            return self.active

        self.inactive_frames = 0
        if not detected:
            self.candidate_since_s = None
            return False
        if self.candidate_since_s is None:
            self.candidate_since_s = now
        if now - self.candidate_since_s >= cfg.activation_delay_s:
            self.active = True
            self.candidate_since_s = None
        return self.active


@dataclass(slots=True)
class ReactiveFrameSmoother:
    attack: float = 0.35
    release: float = 0.12
    rms: float = 0.0
    bands: list[float] = field(default_factory=lambda: [0.0] * 8)

    def reset(self) -> None:
        self.rms = 0.0
        self.bands = [0.0] * 8

    def update(self, frame: AudioFrame) -> AudioFrame:
        self.rms = _smooth(self.rms, frame.rms, self.attack, self.release)
        values = list(frame.bands[:8])
        values.extend(0.0 for _ in range(8 - len(values)))
        for index, value in enumerate(values):
            self.bands[index] = _smooth(
                self.bands[index], value, self.attack, self.release
            )
        return replace(frame, rms=self.rms, bands=cast(BandTuple, tuple(self.bands)))


def _smooth(current: float, target: float, attack: float, release: float) -> float:
    rate = attack if target > current else release
    return current + (target - current) * rate


__all__ = [
    "MusicActivityConfig",
    "MusicActivityDetector",
    "MusicGateState",
    "ReactiveFrameSmoother",
]
