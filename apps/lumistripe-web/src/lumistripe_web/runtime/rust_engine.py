"""Single-process Rust output manager and compact binary frame transport."""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil, isfinite
from pathlib import Path
from queue import Empty, Full, Queue
from typing import TypeAlias

import numpy as np
import numpy.typing as npt
from lumistripe.audio.config import AudioConfig

from .frame_exchange import LatestFrameExchange
from .output_thread import (
    OUTPUT_LATENCY_SAMPLE_COUNT,
    OutputThreadStats,
    RustBeatClockStats,
    RustEffectPlaybackStats,
    RustEffectSchedulerStats,
    RustSequencePlaybackStats,
)

PixelFrame: TypeAlias = npt.NDArray[np.uint8]
JSONCommandData: TypeAlias = dict[str, object] | None

PROTOCOL_VERSION = 3
FRAME_VERSION = 2
MAX_CONTROL_MESSAGE_BYTES = 1_048_576
FRAME_MAGIC = b"LSF2"
FRAME_ACK_MAGIC = b"LSA2"
FRAME_HEADER = struct.Struct("<4sHHQI")
FRAME_ACK = struct.Struct("<4sHHQQ")
_STARTUP_TIMEOUT_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 1.0
_CONFIGURE_TIMEOUT_SECONDS = 12.0
_SHUTDOWN_TIMEOUT_SECONDS = 8.0
_HEALTH_POLL_SECONDS = 0.1
_MAX_RENDER_PREVIEW_PIXELS = 128
_MAX_RENDER_FPS = 240
_MAX_PENDING_SCENE_UPDATES = 64


class RustEngineError(OSError):
    """The optional Rust process or its local IPC protocol failed."""


@dataclass(frozen=True, slots=True)
class RustOutputSpec:
    """One configured Rust output, with its backend selected by the runtime."""

    output_id: str
    pixel_count: int
    reversed: bool
    backend: str
    device: str | None = None
    speed_hz: int | None = None
    chip: str | None = None
    data_pin: int | None = None
    clock_pin: int | None = None

    def to_wire(self) -> dict[str, object]:
        if self.backend == "mock":
            backend: dict[str, object] = {"type": "mock"}
        elif self.backend == "mock_spi":
            backend = {"type": "mock_spi"}
        elif self.backend == "spi":
            backend = {
                "type": "spi",
                "device": self.device,
                "speed_hz": self.speed_hz,
            }
        elif self.backend == "gpio":
            backend = {
                "type": "gpio",
                "chip": self.chip,
                "data_pin": self.data_pin,
                "clock_pin": self.clock_pin,
            }
        else:
            raise ValueError(f"unsupported Rust output backend: {self.backend}")
        return {
            "id": self.output_id,
            "pixel_count": self.pixel_count,
            "reversed": self.reversed,
            "backend": backend,
        }


@dataclass(frozen=True, slots=True)
class RustRenderEffect:
    name: str
    settings: Mapping[str, object]
    intensity: float = 1.0
    trigger_beat: bool = False
    trigger_beat_strength: float = 0.0

    def to_wire(self) -> dict[str, object]:
        if not self.name:
            raise ValueError("Rust render effect name cannot be empty")
        if not isfinite(self.intensity) or not 0.0 <= self.intensity <= 1.0:
            raise ValueError("Rust render effect intensity must be between zero and one")
        if not isinstance(self.trigger_beat, bool):
            raise TypeError("Rust render effect trigger_beat must be a boolean")
        if (
            not isfinite(self.trigger_beat_strength)
            or not 0.0 <= self.trigger_beat_strength <= 1.0
        ):
            raise ValueError("Rust render effect trigger strength must be between zero and one")
        result: dict[str, object] = {
            "name": self.name,
            "settings": dict(self.settings),
            "intensity": self.intensity,
        }
        if self.trigger_beat:
            result["trigger_beat"] = True
            if self.trigger_beat_strength != 0.0:
                result["trigger_beat_strength"] = self.trigger_beat_strength
        return result


@dataclass(frozen=True, slots=True)
class RustRenderTransition:
    started_at_ns: int
    duration_ns: int
    mode: str = "crossfade"

    def to_wire(self) -> dict[str, object]:
        if (
            not isinstance(self.started_at_ns, int)
            or isinstance(self.started_at_ns, bool)
            or self.started_at_ns < 0
        ):
            raise ValueError("Rust render transition start must be an unsigned integer")
        if (
            not isinstance(self.duration_ns, int)
            or isinstance(self.duration_ns, bool)
            or self.duration_ns <= 0
        ):
            raise ValueError("Rust render transition duration must be positive")
        if self.mode not in {"crossfade", "fade_black", "wipe"}:
            raise ValueError("Rust render transition mode is unsupported")
        return {
            "started_at_ns": self.started_at_ns,
            "duration_ns": self.duration_ns,
            "mode": self.mode,
        }


@dataclass(frozen=True, slots=True)
class RustRenderCycling:
    order: str
    timing: str
    interval_ns: int
    seed: int | None
    choices: tuple[tuple[str, Mapping[str, object]], ...]
    transition_duration_ns: int
    transition_mode: str
    selector: RustRenderDynamicSelector | None = None
    dynamic_idle: RustRenderDynamicIdle | None = None
    sequence: RustRenderAnimationSequence | None = None
    effect_scheduler: RustRenderEffectScheduler | None = None

    def to_wire(self) -> dict[str, object]:
        if self.order not in {"sequential", "shuffle"}:
            raise ValueError("Rust cycling order is unsupported")
        if self.timing not in {"per_animation", "fixed"}:
            raise ValueError("Rust cycling timing is unsupported")
        if (
            type(self.interval_ns) is not int
            or not 0 < self.interval_ns <= 0xFFFFFFFFFFFFFFFF
        ):
            raise ValueError("Rust cycling interval must be positive")
        if self.seed is not None and (
            type(self.seed) is not int or not 0 <= self.seed <= 0xFFFFFFFFFFFFFFFF
        ):
            raise ValueError("Rust cycling seed must be an unsigned 64-bit integer")
        if (
            type(self.transition_duration_ns) is not int
            or not 0 <= self.transition_duration_ns <= 0xFFFFFFFFFFFFFFFF
        ):
            raise ValueError("Rust cycling transition duration must be non-negative")
        if self.transition_mode not in {"crossfade", "fade_black", "wipe"}:
            raise ValueError("Rust cycling transition mode is unsupported")
        names = [animation for animation, _ in self.choices]
        if any(not animation for animation in names) or len(names) != len(set(names)):
            raise ValueError("Rust cycling choices must have unique animation names")
        result: dict[str, object] = {
            "order": self.order,
            "timing": self.timing,
            "interval_ns": self.interval_ns,
            "seed": self.seed,
            "choices": [
                {"animation": animation, "settings": dict(settings)}
                for animation, settings in self.choices
            ],
            "transition_duration_ns": self.transition_duration_ns,
            "transition_mode": self.transition_mode,
        }
        if self.selector is not None:
            result["selector"] = self.selector.to_wire()
        if self.dynamic_idle is not None:
            if self.selector is None:
                raise ValueError("Rust Dynamic idle requires a Dynamic selector")
            result["dynamic_idle"] = self.dynamic_idle.to_wire()
        if self.sequence is not None:
            if self.selector is None:
                raise ValueError("Rust animation sequences require a Dynamic selector")
            result["sequence"] = self.sequence.to_wire()
        if self.effect_scheduler is not None:
            if self.selector is None:
                raise ValueError("Rust effect scheduling requires a Dynamic selector")
            result["effect_scheduler"] = self.effect_scheduler.to_wire()
        return result


@dataclass(frozen=True, slots=True)
class RustRenderEffectChoice:
    name: str
    settings: Mapping[str, object]

    def to_wire(self) -> dict[str, object]:
        if not self.name:
            raise ValueError("Rust effect choice name cannot be empty")
        return {"name": self.name, "settings": dict(self.settings)}


@dataclass(frozen=True, slots=True)
class RustRenderEffectScheduler:
    beat_confidence_threshold: float
    onset_threshold: float
    rhythmic_cooldown_ns: int
    accent_cooldown_ns: int
    max_active_effects: int
    max_overlay_strength: float
    subdivisions_per_beat: int
    clock_confidence_threshold: float
    seed: int | None
    sync_offset_ns: int
    dynamic_response: float
    choices: tuple[RustRenderEffectChoice, ...]

    def to_wire(self) -> dict[str, object]:
        thresholds = (
            self.beat_confidence_threshold,
            self.onset_threshold,
            self.clock_confidence_threshold,
            self.dynamic_response,
        )
        if not all(isfinite(value) and 0.0 <= value <= 1.0 for value in thresholds):
            raise ValueError("Rust effect scheduler thresholds must be between zero and one")
        if type(self.rhythmic_cooldown_ns) is not int or not 0 <= self.rhythmic_cooldown_ns <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Rust rhythmic cooldown must be unsigned nanoseconds")
        if type(self.accent_cooldown_ns) is not int or not 0 <= self.accent_cooldown_ns <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Rust accent cooldown must be unsigned nanoseconds")
        if type(self.max_active_effects) is not int or self.max_active_effects not in (1, 2):
            raise ValueError("Rust active effect limit must be one or two")
        if not isfinite(self.max_overlay_strength) or not 0.0 < self.max_overlay_strength <= 1.0:
            raise ValueError("Rust effect overlay strength must be between zero and one")
        if type(self.subdivisions_per_beat) is not int or self.subdivisions_per_beat not in (1, 2, 4):
            raise ValueError("Rust effect subdivisions must be 1, 2, or 4")
        if self.seed is not None and (
            type(self.seed) is not int or not 0 <= self.seed <= 0xFFFFFFFFFFFFFFFF
        ):
            raise ValueError("Rust effect seed must be an unsigned 64-bit integer")
        if type(self.sync_offset_ns) is not int or not -(1 << 63) <= self.sync_offset_ns < (1 << 63):
            raise ValueError("Rust effect sync offset must be a signed 64-bit integer")
        names = [choice.name for choice in self.choices]
        if not names or len(names) > 19 or len(names) != len(set(names)):
            raise ValueError("Rust effect choices must have unique names")
        return {
            "beat_confidence_threshold": self.beat_confidence_threshold,
            "onset_threshold": self.onset_threshold,
            "rhythmic_cooldown_ns": self.rhythmic_cooldown_ns,
            "accent_cooldown_ns": self.accent_cooldown_ns,
            "max_active_effects": self.max_active_effects,
            "max_overlay_strength": self.max_overlay_strength,
            "subdivisions_per_beat": self.subdivisions_per_beat,
            "clock_confidence_threshold": self.clock_confidence_threshold,
            "seed": self.seed,
            "sync_offset_ns": self.sync_offset_ns,
            "dynamic_response": self.dynamic_response,
            "choices": [choice.to_wire() for choice in self.choices],
        }


@dataclass(frozen=True, slots=True)
class RustRenderAnimationSequenceStep:
    animation: str
    duration_bars: int

    def to_wire(self) -> dict[str, object]:
        if not self.animation:
            raise ValueError("Rust sequence animation must not be empty")
        if type(self.duration_bars) is not int or self.duration_bars not in (1, 2, 4, 8):
            raise ValueError("Rust sequence duration must be 1, 2, 4, or 8 bars")
        return {"animation": self.animation, "duration_bars": self.duration_bars}


@dataclass(frozen=True, slots=True)
class RustRenderAnimationSequence:
    id: str
    repeat: bool
    steps: tuple[RustRenderAnimationSequenceStep, ...]

    def to_wire(self) -> dict[str, object]:
        if not self.id.strip():
            raise ValueError("Rust sequence ID must not be empty")
        if type(self.repeat) is not bool:
            raise TypeError("Rust sequence repeat state must be a boolean")
        if not self.steps or len(self.steps) > 32:
            raise ValueError("Rust sequence must contain between one and 32 steps")
        return {
            "id": self.id,
            "repeat": self.repeat,
            "steps": [step.to_wire() for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class RustRenderDynamicSelector:
    min_duration_ns: int
    max_duration_ns: int
    switch_cooldown_ns: int
    drop_cooldown_ns: int
    randomness: float
    history_size: int
    seed: int | None
    switch_margin: float
    activity: RustRenderMusicActivityConfig | None = None
    switch_quantization: str = "off"
    bars_per_phrase: int = 8
    clock_confidence_threshold: float = 0.65
    subdivisions_per_beat: int = 1
    sync_offset_ns: int = 0

    def to_wire(self) -> dict[str, object]:
        durations = (
            self.min_duration_ns,
            self.max_duration_ns,
            self.switch_cooldown_ns,
            self.drop_cooldown_ns,
        )
        if any(
            type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF
            for value in durations
        ):
            raise ValueError("Rust Dynamic selector durations must be unsigned nanoseconds")
        if self.seed is not None and (
            type(self.seed) is not int or not 0 <= self.seed <= 0xFFFFFFFFFFFFFFFF
        ):
            raise ValueError("Rust Dynamic selector seed must be an unsigned 64-bit integer")
        if type(self.history_size) is not int or not 0 <= self.history_size <= 256:
            raise ValueError("Rust Dynamic selector history size must be between zero and 256")
        if self.switch_quantization not in {"off", "beat", "bar", "phrase"}:
            raise ValueError("Rust Dynamic switch quantization is unsupported")
        if type(self.bars_per_phrase) is not int or self.bars_per_phrase not in (4, 8, 16):
            raise ValueError("Rust Dynamic bars per phrase must be 4, 8, or 16")
        if (
            not isfinite(self.clock_confidence_threshold)
            or not 0.0 <= self.clock_confidence_threshold <= 1.0
        ):
            raise ValueError("Rust Dynamic clock confidence threshold must be between zero and one")
        if type(self.subdivisions_per_beat) is not int or self.subdivisions_per_beat not in (1, 2, 4):
            raise ValueError("Rust Dynamic subdivisions per beat must be 1, 2, or 4")
        if type(self.sync_offset_ns) is not int or not -500_000_000 <= self.sync_offset_ns <= 500_000_000:
            raise ValueError("Rust Dynamic sync offset must be between -0.5 and 0.5 seconds")
        if not all(
            isfinite(value) and value >= 0.0
            for value in (self.randomness, self.switch_margin)
        ):
            raise ValueError("Rust Dynamic selector score settings must be finite and nonnegative")
        result: dict[str, object] = {
            "min_duration_ns": self.min_duration_ns,
            "max_duration_ns": self.max_duration_ns,
            "switch_cooldown_ns": self.switch_cooldown_ns,
            "drop_cooldown_ns": self.drop_cooldown_ns,
            "randomness": self.randomness,
            "history_size": self.history_size,
            "seed": self.seed,
            "switch_margin": self.switch_margin,
            "switch_quantization": self.switch_quantization,
            "bars_per_phrase": self.bars_per_phrase,
            "clock_confidence_threshold": self.clock_confidence_threshold,
            "subdivisions_per_beat": self.subdivisions_per_beat,
            "sync_offset_ns": self.sync_offset_ns,
        }
        if self.activity is not None:
            result["activity"] = self.activity.to_wire()
        return result


@dataclass(frozen=True, slots=True)
class RustRenderMusicActivityConfig:
    idle_enter_frames: int
    activation_delay_ns: int
    feature_attack: float
    feature_release: float
    energy_threshold: float
    onset_threshold: float
    beat_density_threshold: float
    brightness_threshold: float
    spectral_balance_ratio: float
    activation_hysteresis_ratio: float
    beat_density_window_ns: int

    def to_wire(self) -> dict[str, object]:
        if type(self.idle_enter_frames) is not int or self.idle_enter_frames <= 0:
            raise ValueError("Rust music activity idle frame count must be positive")
        durations = (self.activation_delay_ns, self.beat_density_window_ns)
        if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF for value in durations):
            raise ValueError("Rust music activity durations must be unsigned nanoseconds")
        if self.beat_density_window_ns == 0:
            raise ValueError("Rust beat density window must be positive")
        unit_values = (
            self.feature_attack,
            self.feature_release,
            self.energy_threshold,
            self.onset_threshold,
            self.beat_density_threshold,
            self.brightness_threshold,
            self.spectral_balance_ratio,
        )
        if not all(isfinite(value) and 0.0 <= value <= 1.0 for value in unit_values):
            raise ValueError("Rust music activity thresholds must be between zero and one")
        if (
            not isfinite(self.activation_hysteresis_ratio)
            or self.activation_hysteresis_ratio < 1.0
        ):
            raise ValueError("Rust activity hysteresis ratio must be at least one")
        return {
            "idle_enter_frames": self.idle_enter_frames,
            "activation_delay_ns": self.activation_delay_ns,
            "feature_attack": self.feature_attack,
            "feature_release": self.feature_release,
            "energy_threshold": self.energy_threshold,
            "onset_threshold": self.onset_threshold,
            "beat_density_threshold": self.beat_density_threshold,
            "brightness_threshold": self.brightness_threshold,
            "spectral_balance_ratio": self.spectral_balance_ratio,
            "activation_hysteresis_ratio": self.activation_hysteresis_ratio,
            "beat_density_window_ns": self.beat_density_window_ns,
        }


@dataclass(frozen=True, slots=True)
class RustRenderDynamicIdle:
    mode: str
    animation: str | None
    settings: Mapping[str, object]
    color: tuple[int, int, int]
    brightness: float

    def to_wire(self) -> dict[str, object]:
        if self.mode not in {"off", "solid", "animation"}:
            raise ValueError("Rust Dynamic idle mode is unsupported")
        if (self.mode == "animation") != bool(self.animation):
            raise ValueError("Rust idle animation must be set only for animation mode")
        if len(self.color) != 3 or any(
            type(channel) is not int or not 0 <= channel <= 255 for channel in self.color
        ):
            raise ValueError("Rust Dynamic idle color must contain three bytes")
        if not isfinite(self.brightness) or not 0.0 <= self.brightness <= 1.0:
            raise ValueError("Rust Dynamic idle brightness must be between zero and one")
        return {
            "mode": self.mode,
            "animation": self.animation,
            "settings": dict(self.settings),
            "color": list(self.color),
            "brightness": self.brightness,
        }


@dataclass(frozen=True, slots=True)
class RustRenderSelectorFeatures:
    energy_level: float
    energy: float
    volume: float
    brightness: float
    onset_strength: float
    bass_energy: float
    bass: float
    mid_energy: float
    treble_energy: float
    beat_confidence: float
    bpm: float
    beat: bool
    silence: bool
    drop_detected: bool
    section_change: bool

    def to_wire(self) -> dict[str, object]:
        values = (
            self.energy_level,
            self.energy,
            self.volume,
            self.brightness,
            self.onset_strength,
            self.bass_energy,
            self.bass,
            self.mid_energy,
            self.treble_energy,
            self.beat_confidence,
            self.bpm,
        )
        if not all(isfinite(value) for value in values):
            raise ValueError("Rust Dynamic selector audio features must be finite")
        if any(
            not isinstance(value, bool)
            for value in (self.silence, self.drop_detected, self.section_change)
        ) or not isinstance(self.beat, bool):
            raise TypeError("Rust Dynamic selector flags must be booleans")
        return {
            "energy_level": self.energy_level,
            "energy": self.energy,
            "volume": self.volume,
            "brightness": self.brightness,
            "onset_strength": self.onset_strength,
            "bass_energy": self.bass_energy,
            "bass": self.bass,
            "mid_energy": self.mid_energy,
            "treble_energy": self.treble_energy,
            "beat_confidence": self.beat_confidence,
            "bpm": self.bpm,
            "beat": self.beat,
            "silence": self.silence,
            "drop_detected": self.drop_detected,
            "section_change": self.section_change,
        }


@dataclass(frozen=True, slots=True)
class RustRenderOutput:
    animation: str
    settings: Mapping[str, object]
    effects: tuple[RustRenderEffect, ...] = ()
    base_intensity: float = 1.0
    max_overlay_strength: float = 0.65
    scene_revision: int = 0
    brightness: float | None = None
    zone_count: int = 1
    zone_pattern: str = "repeat"
    transition: RustRenderTransition | None = None
    cycling: RustRenderCycling | None = None
    music_structure: RustRenderMusicStructure | None = None
    audio_enabled: bool = True
    blackout: bool = False
    reversed: bool = False
    color_correction: tuple[int, int, int] = (255, 255, 255)
    voltage_v: float = 5.0
    full_white_current_a: float = 0.06
    power_limit_watts: float | None = None
    scene_pixel_count: int | None = None
    scene_offset: int = 0
    scene_resample: bool = False
    solid_color: tuple[int, int, int] | None = None

    def to_wire(self) -> dict[str, object]:
        if not self.animation:
            raise ValueError("Rust render animation name cannot be empty")
        if not isfinite(self.base_intensity) or not 0.0 <= self.base_intensity <= 1.0:
            raise ValueError("Rust render base intensity must be between zero and one")
        if (
            not isfinite(self.max_overlay_strength)
            or not 0.0 < self.max_overlay_strength <= 1.0
        ):
            raise ValueError("Rust render overlay strength must be between zero and one")
        if len(self.effects) > 2:
            raise ValueError("Rust render output supports at most two effects")
        if (
            type(self.scene_revision) is not int
            or not 0 <= self.scene_revision <= 0xFFFFFFFFFFFFFFFF
        ):
            raise ValueError("Rust render scene revision must be a u64")
        if self.brightness is not None and (
            not isfinite(self.brightness) or not 0.0 <= self.brightness <= 1.0
        ):
            raise ValueError("Rust render output brightness must be between zero and one")
        if not isinstance(self.zone_count, int) or isinstance(self.zone_count, bool):
            raise TypeError("Rust render zone count must be an integer")
        if self.zone_count < 1:
            raise ValueError("Rust render zone count must be positive")
        if self.zone_pattern not in {"repeat", "alternate"}:
            raise ValueError("Rust render zone pattern must be repeat or alternate")
        if not isinstance(self.blackout, bool):
            raise TypeError("Rust render blackout state must be a boolean")
        if not isinstance(self.audio_enabled, bool):
            raise TypeError("Rust render audio-enabled state must be a boolean")
        if not isinstance(self.reversed, bool):
            raise TypeError("Rust render output reversal must be a boolean")
        if len(self.color_correction) != 3 or any(
            type(channel) is not int or not 0 <= channel <= 255
            for channel in self.color_correction
        ):
            raise ValueError("Rust render color correction must contain three byte channels")
        if not isfinite(self.voltage_v) or self.voltage_v <= 0.0:
            raise ValueError("Rust render voltage must be finite and positive")
        if not isfinite(self.full_white_current_a) or self.full_white_current_a <= 0.0:
            raise ValueError("Rust render full-white current must be finite and positive")
        if self.power_limit_watts is not None and (
            not isfinite(self.power_limit_watts) or self.power_limit_watts <= 0.0
        ):
            raise ValueError("Rust render output power limit must be finite and positive")
        if self.scene_pixel_count is not None and (
            type(self.scene_pixel_count) is not int or self.scene_pixel_count <= 0
        ):
            raise ValueError("Rust render scene pixel count must be a positive integer")
        if type(self.scene_offset) is not int or self.scene_offset < 0:
            raise ValueError("Rust render scene offset must be an unsigned integer")
        if self.scene_offset and (
            self.scene_pixel_count is None
            or self.scene_offset >= self.scene_pixel_count
        ):
            raise ValueError("Rust render scene offset must be inside its scene canvas")
        if not isinstance(self.scene_resample, bool):
            raise TypeError("Rust render scene resample state must be a boolean")
        if self.scene_resample and (
            self.scene_pixel_count is None or self.scene_offset != 0
        ):
            raise ValueError("Rust scene resampling needs a whole scene canvas")
        if self.solid_color is not None and (
            len(self.solid_color) != 3
            or any(type(channel) is not int or not 0 <= channel <= 255 for channel in self.solid_color)
        ):
            raise ValueError("Rust solid color must contain three byte channels")
        if self.solid_color is not None and self.effects:
            raise ValueError("Rust solid scenes do not accept effect layers")
        output: dict[str, object] = {
            "animation": self.animation,
            "settings": dict(self.settings),
        }
        if self.effects:
            output["effects"] = [effect.to_wire() for effect in self.effects]
        if self.effects or self.base_intensity != 1.0:
            output["base_intensity"] = self.base_intensity
        if self.effects:
            output["max_overlay_strength"] = self.max_overlay_strength
        if self.scene_revision != 0:
            output["scene_revision"] = self.scene_revision
        if self.brightness is not None:
            output["brightness"] = self.brightness
        if self.zone_count != 1:
            output["zone_count"] = self.zone_count
        if self.zone_pattern != "repeat":
            output["zone_pattern"] = self.zone_pattern
        if self.transition is not None:
            output["transition"] = self.transition.to_wire()
        if self.cycling is not None:
            output["cycling"] = self.cycling.to_wire()
        if self.music_structure is not None:
            output["music_structure"] = self.music_structure.to_wire()
        if not self.audio_enabled:
            output["audio_enabled"] = False
        if self.blackout:
            output["blackout"] = True
        if self.reversed:
            output["reversed"] = True
        if self.color_correction != (255, 255, 255):
            output["color_correction"] = list(self.color_correction)
        if self.voltage_v != 5.0:
            output["voltage_v"] = self.voltage_v
        if self.full_white_current_a != 0.06:
            output["full_white_current_a"] = self.full_white_current_a
        if self.power_limit_watts is not None:
            output["power_limit_watts"] = self.power_limit_watts
        if self.scene_pixel_count is not None:
            output["scene_pixel_count"] = self.scene_pixel_count
        if self.scene_offset:
            output["scene_offset"] = self.scene_offset
        if self.scene_resample:
            output["scene_resample"] = True
        if self.solid_color is not None:
            output["solid_color"] = list(self.solid_color)
        return output


@dataclass(frozen=True, slots=True)
class RustRenderMusicStructure:
    phase: str
    confidence: float
    buildup_progress: float
    drop_event: bool = False

    def to_wire(self) -> dict[str, object]:
        if self.phase not in {"idle", "breakdown", "groove", "buildup", "drop"}:
            raise ValueError("Rust music structure phase is unsupported")
        if not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Rust music structure confidence must be between zero and one")
        if not isfinite(self.buildup_progress) or not 0.0 <= self.buildup_progress <= 1.0:
            raise ValueError("Rust music structure buildup progress must be between zero and one")
        if not isinstance(self.drop_event, bool):
            raise TypeError("Rust music structure drop event must be a boolean")
        return {
            "phase": self.phase,
            "confidence": self.confidence,
            "buildup_progress": self.buildup_progress,
            "drop_event": self.drop_event,
        }


@dataclass(frozen=True, slots=True)
class RustRenderBeatEvent:
    sequence: int
    timestamp_ns: int
    strength: float = 0.0

    def to_wire(self) -> dict[str, object]:
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("Rust beat event sequence must be a non-negative integer")
        if type(self.timestamp_ns) is not int or self.timestamp_ns < 0:
            raise ValueError("Rust beat event timestamp must be a non-negative integer")
        if not isfinite(self.strength) or not 0.0 <= self.strength <= 1.0:
            raise ValueError("Rust beat event strength must be between zero and one")
        return {
            "sequence": self.sequence,
            "timestamp_ns": self.timestamp_ns,
            "strength": self.strength,
        }


@dataclass(frozen=True, slots=True)
class RustRenderStructureEvent:
    output_index: int
    sequence: int
    timestamp_ns: int
    phase: str
    confidence: float
    buildup_progress: float
    drop_event: bool = False
    section_change: bool = False
    phase_change: bool = False

    def to_wire(self) -> dict[str, object]:
        if type(self.output_index) is not int or self.output_index < 0:
            raise ValueError("Rust structure event output index must be non-negative")
        if type(self.sequence) is not int or not 0 < self.sequence <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Rust structure event sequence must be a positive u64")
        if type(self.timestamp_ns) is not int or not 0 <= self.timestamp_ns <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Rust structure event timestamp must be an unsigned 64-bit integer")
        if self.phase not in {"idle", "breakdown", "groove", "buildup", "drop"}:
            raise ValueError("Rust structure event phase is unsupported")
        if not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Rust structure event confidence must be between zero and one")
        if not isfinite(self.buildup_progress) or not 0.0 <= self.buildup_progress <= 1.0:
            raise ValueError("Rust structure event buildup progress must be between zero and one")
        if not isinstance(self.drop_event, bool) or not isinstance(self.section_change, bool):
            raise TypeError("Rust structure event flags must be booleans")
        if not (self.drop_event or self.section_change or self.phase_change):
            raise ValueError("Rust structure event must represent a one-shot edge or phase change")
        result: dict[str, object] = {
            "output_index": self.output_index,
            "sequence": self.sequence,
            "timestamp_ns": self.timestamp_ns,
            "phase": self.phase,
            "confidence": self.confidence,
            "buildup_progress": self.buildup_progress,
            "drop_event": self.drop_event,
            "section_change": self.section_change,
        }
        if self.phase_change:
            result["phase_change"] = True
        return result


@dataclass(frozen=True, slots=True)
class RustRenderEffectFeatures:
    beat_strength: float
    musical_impact: float

    def to_wire(self) -> dict[str, object]:
        if not all(
            isfinite(value) and 0.0 <= value <= 1.0
            for value in (self.beat_strength, self.musical_impact)
        ):
            raise ValueError("Rust effect features must be between zero and one")
        return {
            "beat_strength": self.beat_strength,
            "musical_impact": self.musical_impact,
        }


@dataclass(frozen=True, slots=True)
class RustRenderAudioInput:
    rms: float
    bands: tuple[float, float, float, float, float, float, float, float]
    beat: bool
    beat_strength: float
    feature_timestamp_ns: int | None = None
    feature_sequence: int | None = None
    fallback_bpm: float | None = None
    beat_events: tuple[RustRenderBeatEvent, ...] = ()
    structure_events: tuple[RustRenderStructureEvent, ...] = ()
    selector_features: RustRenderSelectorFeatures | None = None
    effect_features: RustRenderEffectFeatures | None = None
    beat_event_stream: bool = False

    def to_wire(self) -> dict[str, object]:
        if len(self.bands) != 8:
            raise ValueError("Rust render audio input requires eight bands")
        values = (self.rms, *self.bands, self.beat_strength)
        if not all(isfinite(value) for value in values) or (
            self.fallback_bpm is not None and not isfinite(self.fallback_bpm)
        ):
            raise ValueError("Rust render audio input values must be finite")
        if self.feature_sequence is not None and (
            type(self.feature_sequence) is not int
            or not 0 <= self.feature_sequence <= 0xFFFFFFFFFFFFFFFF
        ):
            raise ValueError("Rust audio feature sequence must be an unsigned 64-bit integer")
        if self.feature_timestamp_ns is not None and (
            type(self.feature_timestamp_ns) is not int
            or not 0 <= self.feature_timestamp_ns <= 0xFFFFFFFFFFFFFFFF
        ):
            raise ValueError("Rust audio feature timestamp must be an unsigned 64-bit integer")
        result: dict[str, object] = {
            "rms": self.rms,
            "bands": list(self.bands),
            "beat": self.beat,
            "beat_strength": self.beat_strength,
        }
        if self.fallback_bpm is not None:
            result["fallback_bpm"] = self.fallback_bpm
        if self.feature_sequence is not None:
            result["feature_sequence"] = self.feature_sequence
        if self.feature_timestamp_ns is not None:
            result["feature_timestamp_ns"] = self.feature_timestamp_ns
        if self.beat_events:
            result["beat_events"] = [event.to_wire() for event in self.beat_events]
        if len(self.structure_events) > 256:
            raise ValueError("Rust render structure event batch exceeds 256 events")
        previous_sequences: dict[int, int] = {}
        for event in self.structure_events:
            event.to_wire()
            previous = previous_sequences.get(event.output_index)
            if previous is not None and event.sequence <= previous:
                raise ValueError("Rust structure events must be ordered per output")
            previous_sequences[event.output_index] = event.sequence
        if self.structure_events:
            result["structure_events"] = [
                event.to_wire() for event in self.structure_events
            ]
        if self.selector_features is not None:
            result["selector_features"] = self.selector_features.to_wire()
        if self.effect_features is not None:
            result["effect_features"] = self.effect_features.to_wire()
        if self.beat_event_stream:
            result["beat_event_stream"] = True
        return result


@dataclass(frozen=True, slots=True)
class RustRenderAck:
    sequence: int
    replaced: bool


@dataclass(frozen=True, slots=True)
class RustSceneUpdateAck:
    revision: int


@dataclass(frozen=True, slots=True)
class EngineHealth:
    status: str
    configured: bool
    output_count: int
    pixel_counts: tuple[int, ...]
    frames_rendered: int
    last_sequence: int | None
    dropped_frames: int
    failed_frames: int
    scheduler_missed_deadlines: int
    selected_animations: tuple[str, ...]
    scene_revisions: tuple[int, ...]
    structure_event_sequences: tuple[int, ...]
    cycling_active: tuple[bool, ...]
    dynamic_gate_states: tuple[str, ...]
    beat_clocks: tuple[RustBeatClockStats, ...]
    sequence_playback: tuple[RustSequencePlaybackStats, ...]
    effect_schedulers: tuple[RustEffectSchedulerStats, ...]
    scene_control_revision: int | None
    audio_update_sequence: int | None
    last_error: str | None
    output_rate: float
    frame_latency_ms: float | None
    queue_latency_ms: float | None
    transmission_ms: float | None
    frame_latency_p50_ms: float | None
    frame_latency_p95_ms: float | None
    frame_latency_p99_ms: float | None
    audio_feature_to_led_latency_ms: float | None
    audio_feature_to_led_p50_ms: float | None
    audio_feature_to_led_p95_ms: float | None
    audio_feature_to_led_p99_ms: float | None
    sequential_skew_ms: float | None
    last_successful_update_at: datetime | None
    render_time_ms: float | None
    estimated_power_watts: float | None
    applied_power_scale: float | None
    output_power_watts: tuple[float, ...] | None
    limiting_output_index: int | None
    preview_outputs: tuple[bytes, ...] | None


@dataclass(frozen=True, slots=True)
class RustAudioDevice:
    selector: str
    host: str
    name: str
    is_default: bool
    sample_rate: int
    channels: int


@dataclass(frozen=True, slots=True)
class EngineAudioStatus:
    active: bool
    device: RustAudioDevice | None
    frame: Mapping[str, object]
    features: Mapping[str, object]
    structure: Mapping[str, object]
    processor: Mapping[str, object]
    capture: Mapping[str, object]
    last_frame_ns: int | None
    last_error: str | None
    audio_update_sequence: int
    dropped_audio_updates: int
    dropped_structure_events: int


def audio_config_to_wire(config: AudioConfig) -> dict[str, object]:
    """Flatten the shared Python audio config into the versioned Rust IPC shape."""
    smoothing = config.smoothing
    normalization = config.normalization
    analysis = config.analysis
    microphone = config.microphone
    return {
        "microphone_enabled": microphone.enabled,
        "input_noise_floor": microphone.input_noise_floor,
        "high_pass_hz": microphone.high_pass_hz,
        "noise_floor": smoothing.noise_floor,
        "rms_attack": smoothing.rms_attack,
        "rms_release": smoothing.rms_release,
        "band_attack": smoothing.band_attack,
        "band_release": smoothing.band_release,
        "beat_release": smoothing.beat_release,
        "smoothing_enabled": smoothing.enabled,
        "target_level": normalization.target_level,
        "min_gain": normalization.min_gain,
        "max_gain": normalization.max_gain,
        "adapt_attack": normalization.adapt_attack,
        "adapt_release": normalization.adapt_release,
        "music_threshold": normalization.music_threshold,
        "music_max_gain": normalization.music_max_gain,
        "silence_floor": normalization.silence_floor,
        "normalization_enabled": normalization.enabled,
        "dc_block_enabled": normalization.dc_block_enabled,
        "drop_bass_threshold": analysis.drop_bass_threshold,
        "drop_bass_delta_threshold": analysis.drop_bass_delta_threshold,
        "drop_onset_threshold": analysis.drop_onset_threshold,
        "section_change_threshold": analysis.section_change_threshold,
        "section_onset_threshold": analysis.section_onset_threshold,
    }


def _encode_request(
    request_id: str, command: str, data: JSONCommandData = None
) -> bytes:
    command_value: dict[str, object] = {"type": command}
    if data is not None:
        command_value["data"] = data
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "command": command_value,
    }
    payload = json.dumps(request, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    ) + b"\n"
    if len(payload) > MAX_CONTROL_MESSAGE_BYTES:
        raise RustEngineError(
            f"engine IPC request is {len(payload)} bytes; maximum is {MAX_CONTROL_MESSAGE_BYTES}"
        )
    return payload


def _decode_response(payload: bytes, expected_request_id: str) -> dict[str, object]:
    try:
        response = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RustEngineError(f"engine returned invalid JSON: {exc}") from exc
    if not isinstance(response, dict):
        raise RustEngineError("engine response must be a JSON object")
    version = response.get("protocol_version")
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise RustEngineError(f"engine response has unsupported protocol version: {version}")
    request_id = response.get("request_id")
    if request_id != expected_request_id:
        raise RustEngineError("engine response request_id does not match the request")
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("type"), str):
        raise RustEngineError("engine response result is malformed")
    if result["type"] == "error":
        code = result.get("code", "unknown")
        message = result.get("message", "engine request failed")
        raise RustEngineError(f"Rust engine {code}: {message}")
    return result


def _resolve_binary(binary_path: Path | None) -> Path:
    candidate = binary_path
    if candidate is None:
        configured = os.environ.get("LUMISTRIPE_ENGINE_BINARY")
        if configured:
            candidate = Path(configured)
    if candidate is None:
        located = shutil.which("lumistripe-engine")
        if located is not None:
            candidate = Path(located)
    if candidate is None:
        repository_binary = (
            Path(__file__).resolve().parents[5]
            / "rust"
            / "target"
            / "debug"
            / "lumistripe-engine"
        )
        if repository_binary.is_file():
            candidate = repository_binary
    if candidate is None:
        raise RustEngineError(
            "Rust backend selected but lumistripe-engine was not found; set "
            "LUMISTRIPE_ENGINE_BINARY or pass --engine-binary"
        )
    resolved = candidate.expanduser().resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RustEngineError(f"Rust engine binary is not executable: {resolved}")
    return resolved


class RustEngineProcess:
    """Own one engine subprocess and its private Unix-domain socket."""

    def __init__(self, *, binary_path: Path | None = None) -> None:
        self._binary_path = _resolve_binary(binary_path)
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="ls-")
        self._socket_path = Path(self._temporary_directory.name) / "engine.sock"
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._pixel_counts: tuple[int, ...] = ()
        self._request_lock = threading.Lock()
        try:
            self._process = subprocess.Popen(
                [
                    str(self._binary_path),
                    "--socket",
                    str(self._socket_path),
                    "--owner-pid",
                    str(os.getpid()),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self._wait_until_ready()
        except Exception:
            self.close()
            raise

    def health(self) -> EngineHealth:
        result = self._request("health")
        if result.get("type") != "health" or result.get("status") != "ready":
            raise RustEngineError("Rust engine did not report a ready health state")
        configured = _bool_field(result, "configured")
        output_count = _nonnegative_int_field(result, "output_count")
        pixel_counts_value = result.get("pixel_counts")
        if not isinstance(pixel_counts_value, list) or any(
            type(value) is not int or value <= 0 for value in pixel_counts_value
        ):
            raise RustEngineError("Rust engine health pixel_counts is invalid")
        pixel_counts = tuple(pixel_counts_value)
        if output_count != len(pixel_counts):
            raise RustEngineError("Rust engine health output_count is inconsistent")
        frames_rendered = _nonnegative_int_field(result, "frames_rendered")
        scene_control_revision = _optional_nonnegative_int_field(
            result, "scene_control_revision"
        )
        audio_update_sequence = _optional_nonnegative_int_field(
            result, "audio_update_sequence"
        )
        last_sequence_value = result.get("last_sequence")
        if last_sequence_value is not None and (
            type(last_sequence_value) is not int or last_sequence_value < 0
        ):
            raise RustEngineError("Rust engine health last_sequence is invalid")
        dropped_frames = _nonnegative_int_field(result, "dropped_frames")
        failed_frames = _nonnegative_int_field(result, "failed_frames")
        scheduler_missed_deadlines = result.get("scheduler_missed_deadlines", 0)
        if type(scheduler_missed_deadlines) is not int or scheduler_missed_deadlines < 0:
            raise RustEngineError(
                "Rust engine health scheduler_missed_deadlines is invalid"
            )
        selected_animations_value = result.get("selected_animations", [])
        if not isinstance(selected_animations_value, list) or any(
            not isinstance(name, str) or not name for name in selected_animations_value
        ):
            raise RustEngineError("Rust engine health selected_animations is invalid")
        selected_animations = tuple(selected_animations_value)
        if selected_animations and len(selected_animations) != output_count:
            raise RustEngineError(
                "Rust engine health selected_animations is inconsistent"
            )
        scene_revisions_value = result.get("scene_revisions", [])
        if not isinstance(scene_revisions_value, list) or any(
            type(revision) is not int or not 0 <= revision <= 0xFFFFFFFFFFFFFFFF
            for revision in scene_revisions_value
        ):
            raise RustEngineError("Rust engine health scene_revisions is invalid")
        scene_revisions = tuple(scene_revisions_value)
        if scene_revisions and len(scene_revisions) != output_count:
            raise RustEngineError("Rust engine health scene_revisions is inconsistent")
        structure_event_sequences_value = result.get("structure_event_sequences", [])
        if not isinstance(structure_event_sequences_value, list) or any(
            type(sequence) is not int or not 0 <= sequence <= 0xFFFFFFFFFFFFFFFF
            for sequence in structure_event_sequences_value
        ):
            raise RustEngineError(
                "Rust engine health structure_event_sequences is invalid"
            )
        structure_event_sequences = tuple(structure_event_sequences_value)
        if (
            structure_event_sequences
            and len(structure_event_sequences) != output_count
        ):
            raise RustEngineError(
                "Rust engine health structure_event_sequences is inconsistent"
            )
        cycling_active_value = result.get("cycling_active", [])
        if not isinstance(cycling_active_value, list) or any(
            not isinstance(active, bool) for active in cycling_active_value
        ):
            raise RustEngineError("Rust engine health cycling_active is invalid")
        cycling_active = tuple(cycling_active_value)
        if cycling_active and len(cycling_active) != output_count:
            raise RustEngineError("Rust engine health cycling_active is inconsistent")
        dynamic_gate_states_value = result.get("dynamic_gate_states", [])
        if not isinstance(dynamic_gate_states_value, list) or any(
            state not in {"disabled", "idle", "candidate", "music"}
            for state in dynamic_gate_states_value
        ):
            raise RustEngineError("Rust engine health dynamic_gate_states is invalid")
        dynamic_gate_states = tuple(dynamic_gate_states_value)
        if dynamic_gate_states and len(dynamic_gate_states) != output_count:
            raise RustEngineError("Rust engine health dynamic_gate_states is inconsistent")
        beat_clocks_value = result.get("beat_clocks", [])
        if not isinstance(beat_clocks_value, list):
            raise RustEngineError("Rust engine health beat_clocks is invalid")
        beat_clocks: list[RustBeatClockStats] = []
        for value in beat_clocks_value:
            if not isinstance(value, dict):
                raise RustEngineError("Rust engine health beat_clocks is invalid")
            bpm = value.get("bpm")
            phase = value.get("phase")
            confidence = value.get("confidence")
            sampled_at_ns = value.get("sampled_at_ns")
            beat_index = value.get("beat_index")
            next_beat_at_ns = value.get("next_beat_at_ns")
            last_beat_at_ns = value.get("last_beat_at_ns")
            last_event_sequence = value.get("last_event_sequence", 0)
            if (
                isinstance(bpm, bool)
                or not isinstance(bpm, (int, float))
                or isinstance(phase, bool)
                or not isinstance(phase, (int, float))
                or isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or type(sampled_at_ns) is not int
                or sampled_at_ns < 0
                or type(beat_index) is not int
                or beat_index < 0
                or type(last_event_sequence) is not int
                or last_event_sequence < 0
                or any(
                    timestamp is not None
                    and (type(timestamp) is not int or timestamp < 0)
                    for timestamp in (next_beat_at_ns, last_beat_at_ns)
                )
            ):
                raise RustEngineError("Rust engine health beat_clocks is invalid")
            bpm = float(bpm)
            phase = float(phase)
            confidence = float(confidence)
            if (
                not isfinite(bpm)
                or bpm <= 0.0
                or not isfinite(phase)
                or not 0.0 <= phase < 1.0
                or not isfinite(confidence)
                or not 0.0 <= confidence <= 1.0
            ):
                raise RustEngineError("Rust engine health beat_clocks is invalid")
            beat_clocks.append(
                RustBeatClockStats(
                    sampled_at_ns=sampled_at_ns,
                    bpm=bpm,
                    phase=phase,
                    confidence=confidence,
                    beat_index=beat_index,
                    next_beat_at_ns=next_beat_at_ns,
                    last_beat_at_ns=last_beat_at_ns,
                    last_event_sequence=last_event_sequence,
                )
            )
        if beat_clocks and len(beat_clocks) != output_count:
            raise RustEngineError("Rust engine health beat_clocks is inconsistent")
        sequence_playback_value = result.get("sequence_playback", [])
        if not isinstance(sequence_playback_value, list):
            raise RustEngineError("Rust engine health sequence_playback is invalid")
        sequence_playback: list[RustSequencePlaybackStats] = []
        for value in sequence_playback_value:
            if not isinstance(value, dict):
                raise RustEngineError("Rust engine health sequence_playback is invalid")
            sequence_id = value.get("sequence_id")
            active = value.get("active", False)
            waiting_for_clock = value.get("waiting_for_clock", False)
            current_step_index = value.get("current_step_index")
            animation = value.get("animation")
            remaining_bars = value.get("remaining_bars")
            next_transition_at_ns = value.get("next_transition_at_ns")
            if (
                sequence_id is not None
                and (not isinstance(sequence_id, str) or not sequence_id.strip())
            ) or type(active) is not bool or type(waiting_for_clock) is not bool:
                raise RustEngineError("Rust engine health sequence_playback is invalid")
            if current_step_index is not None and (
                type(current_step_index) is not int or current_step_index < 0
            ):
                raise RustEngineError("Rust engine health sequence_playback is invalid")
            if animation is not None and (
                not isinstance(animation, str) or not animation
            ):
                raise RustEngineError("Rust engine health sequence_playback is invalid")
            if remaining_bars is not None and (
                type(remaining_bars) is not int or remaining_bars < 0
            ):
                raise RustEngineError("Rust engine health sequence_playback is invalid")
            if next_transition_at_ns is not None and (
                type(next_transition_at_ns) is not int or next_transition_at_ns < 0
            ):
                raise RustEngineError("Rust engine health sequence_playback is invalid")
            if active and (
                sequence_id is None
                or current_step_index is None
                or animation is None
                or remaining_bars is None
            ):
                raise RustEngineError("Rust engine health sequence_playback is inconsistent")
            sequence_playback.append(
                RustSequencePlaybackStats(
                    sequence_id=sequence_id,
                    active=active,
                    waiting_for_clock=waiting_for_clock,
                    current_step_index=current_step_index,
                    animation=animation,
                    remaining_bars=remaining_bars,
                    next_transition_at_ns=next_transition_at_ns,
                )
            )
        if sequence_playback and len(sequence_playback) != output_count:
            raise RustEngineError("Rust engine health sequence_playback is inconsistent")
        effect_schedulers_value = result.get("effect_schedulers", [])
        if not isinstance(effect_schedulers_value, list):
            raise RustEngineError("Rust engine health effect_schedulers is invalid")
        effect_schedulers: list[RustEffectSchedulerStats] = []
        for value in effect_schedulers_value:
            if not isinstance(value, dict):
                raise RustEngineError("Rust engine health effect_schedulers is invalid")
            active_value = value.get("active", [])
            if not isinstance(active_value, list):
                raise RustEngineError("Rust engine health effect_schedulers is invalid")
            active_effects: list[RustEffectPlaybackStats] = []
            for effect in active_value:
                if not isinstance(effect, dict):
                    raise RustEngineError("Rust engine health effect_schedulers is invalid")
                name = effect.get("name")
                category = effect.get("category")
                if (
                    not isinstance(name, str)
                    or not name
                    or not isinstance(category, str)
                    or category not in {"rhythmic", "accent"}
                ):
                    raise RustEngineError("Rust engine health active effects are invalid")
                strength = _number_field(effect, "strength")
                progress = _number_field(effect, "progress")
                if strength > 1.0 or progress > 1.0:
                    raise RustEngineError("Rust engine health active effects are invalid")
                active_effects.append(
                    RustEffectPlaybackStats(
                        name=name,
                        category=category,
                        strength=strength,
                        elapsed_ns=_nonnegative_int_field(effect, "elapsed_ns"),
                        remaining_ns=_nonnegative_int_field(effect, "remaining_ns"),
                        progress=progress,
                    )
                )
            rhythmic_result = value.get("rhythmic_result")
            accent_result = value.get("accent_result")
            drop_status = value.get("drop_accent_status")
            drop_reason = value.get("drop_accent_reason")
            valid_results = {
                "not-evaluated",
                "transition",
                "below-threshold",
                "category-active",
                "capacity",
                "cooldown",
                "activated",
                "no-candidate",
            }
            if (
                not isinstance(rhythmic_result, str)
                or rhythmic_result not in valid_results
                or not isinstance(accent_result, str)
                or accent_result not in valid_results
                or not isinstance(drop_status, str)
                or drop_status not in {"idle", "pending", "activated", "skipped"}
                or (drop_reason is not None and not isinstance(drop_reason, str))
            ):
                raise RustEngineError("Rust engine health effect scheduler status is invalid")
            effect_schedulers.append(
                RustEffectSchedulerStats(
                    active=tuple(active_effects),
                    rhythmic_cooldown_remaining_ns=_nonnegative_int_field(
                        value, "rhythmic_cooldown_remaining_ns"
                    ),
                    accent_cooldown_remaining_ns=_nonnegative_int_field(
                        value, "accent_cooldown_remaining_ns"
                    ),
                    rhythmic_result=rhythmic_result,
                    accent_result=accent_result,
                    drop_accent_status=drop_status,
                    drop_accent_reason=drop_reason,
                    late_ticks_dropped=_nonnegative_int_field(value, "late_ticks_dropped"),
                    late_ticks_coalesced=_nonnegative_int_field(value, "late_ticks_coalesced"),
                    beat_lateness_p95_ns=_nonnegative_int_field(
                        value, "beat_lateness_p95_ns"
                    ),
                )
            )
        if effect_schedulers and len(effect_schedulers) != output_count:
            raise RustEngineError("Rust engine health effect_schedulers is inconsistent")
        last_error_value = result.get("last_error")
        if last_error_value is not None and not isinstance(last_error_value, str):
            raise RustEngineError("Rust engine health last_error is invalid")
        output_rate = _number_field(result, "output_rate")
        frame_latency_ms = _optional_number_field(result, "frame_latency_ms")
        queue_latency_ms = _optional_number_field(result, "queue_latency_ms")
        transmission_ms = _optional_number_field(result, "transmission_ms")
        frame_latency_p50_ms = _optional_number_field(
            result, "frame_latency_p50_ms"
        )
        frame_latency_p95_ms = _optional_number_field(result, "frame_latency_p95_ms")
        frame_latency_p99_ms = _optional_number_field(
            result, "frame_latency_p99_ms"
        )
        audio_feature_to_led_latency_ms = _optional_number_field(
            result, "audio_feature_to_led_latency_ms"
        )
        audio_feature_to_led_p50_ms = _optional_number_field(
            result, "audio_feature_to_led_p50_ms"
        )
        audio_feature_to_led_p95_ms = _optional_number_field(
            result, "audio_feature_to_led_p95_ms"
        )
        audio_feature_to_led_p99_ms = _optional_number_field(
            result, "audio_feature_to_led_p99_ms"
        )
        sequential_skew_ms = _optional_number_field(result, "sequential_skew_ms")
        updated_ms = result.get("last_successful_update_epoch_ms")
        if updated_ms is not None and (type(updated_ms) is not int or updated_ms < 0):
            raise RustEngineError(
                "Rust engine health last_successful_update_epoch_ms is invalid"
            )
        updated_at = (
            datetime.fromtimestamp(updated_ms / 1000, tz=UTC).astimezone()
            if updated_ms is not None
            else None
        )
        render_time_ms = _optional_number_field(result, "render_time_ms")
        estimated_power_watts = _optional_number_field(
            result, "estimated_power_watts"
        )
        applied_power_scale = _optional_number_field(result, "applied_power_scale")
        if applied_power_scale is not None and applied_power_scale > 1.0:
            raise RustEngineError("Rust engine health applied_power_scale is invalid")
        output_power_value = result.get("output_power_watts")
        if output_power_value is None:
            output_power_watts = None
        elif (
            not isinstance(output_power_value, list)
            or len(output_power_value) != output_count
        ):
            raise RustEngineError("Rust engine health output_power_watts is invalid")
        else:
            parsed_output_power: list[float] = []
            for value in output_power_value:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise RustEngineError(
                        "Rust engine health output_power_watts is invalid"
                    )
                watts = float(value)
                if not np.isfinite(watts) or watts < 0.0:
                    raise RustEngineError(
                        "Rust engine health output_power_watts is invalid"
                    )
                parsed_output_power.append(watts)
            output_power_watts = tuple(parsed_output_power)
        limiting_output_index_value = result.get("limiting_output_index")
        if limiting_output_index_value is not None and (
            type(limiting_output_index_value) is not int
            or not 0 <= limiting_output_index_value < output_count
        ):
            raise RustEngineError(
                "Rust engine health limiting_output_index is invalid"
            )
        preview_value = result.get("preview_outputs")
        if preview_value is None:
            preview_outputs = None
        elif not isinstance(preview_value, list) or len(preview_value) != output_count:
            raise RustEngineError("Rust engine health preview_outputs is invalid")
        else:
            decoded_previews: list[bytes] = []
            for output in preview_value:
                if (
                    not isinstance(output, list)
                    or len(output) % 4 != 0
                    or len(output) > _MAX_RENDER_PREVIEW_PIXELS * 4
                    or any(type(channel) is not int or not 0 <= channel <= 255 for channel in output)
                ):
                    raise RustEngineError("Rust engine health preview_outputs is invalid")
                decoded_previews.append(bytes(output))
            preview_outputs = tuple(decoded_previews)
        return EngineHealth(
            status="ready",
            configured=configured,
            output_count=output_count,
            pixel_counts=pixel_counts,
            frames_rendered=frames_rendered,
            last_sequence=last_sequence_value,
            dropped_frames=dropped_frames,
            failed_frames=failed_frames,
            scheduler_missed_deadlines=scheduler_missed_deadlines,
            selected_animations=selected_animations,
            scene_revisions=scene_revisions,
            structure_event_sequences=structure_event_sequences,
            cycling_active=cycling_active,
            dynamic_gate_states=dynamic_gate_states,
            beat_clocks=tuple(beat_clocks),
            sequence_playback=tuple(sequence_playback),
            effect_schedulers=tuple(effect_schedulers),
            scene_control_revision=scene_control_revision,
            audio_update_sequence=audio_update_sequence,
            last_error=last_error_value,
            output_rate=output_rate,
            frame_latency_ms=frame_latency_ms,
            queue_latency_ms=queue_latency_ms,
            transmission_ms=transmission_ms,
            frame_latency_p50_ms=frame_latency_p50_ms,
            frame_latency_p95_ms=frame_latency_p95_ms,
            frame_latency_p99_ms=frame_latency_p99_ms,
            audio_feature_to_led_latency_ms=audio_feature_to_led_latency_ms,
            audio_feature_to_led_p50_ms=audio_feature_to_led_p50_ms,
            audio_feature_to_led_p95_ms=audio_feature_to_led_p95_ms,
            audio_feature_to_led_p99_ms=audio_feature_to_led_p99_ms,
            sequential_skew_ms=sequential_skew_ms,
            last_successful_update_at=updated_at,
            render_time_ms=render_time_ms,
            estimated_power_watts=estimated_power_watts,
            applied_power_scale=applied_power_scale,
            output_power_watts=output_power_watts,
            limiting_output_index=limiting_output_index_value,
            preview_outputs=preview_outputs,
        )

    def render_capabilities(self) -> tuple[str, ...]:
        result = self._request("render_capabilities")
        animations = result.get("animations")
        if result.get("type") != "render_capabilities" or not isinstance(animations, list):
            raise RustEngineError("Rust engine returned invalid render capabilities")
        if any(not isinstance(animation, str) for animation in animations):
            raise RustEngineError("Rust engine render capabilities contain an invalid name")
        return tuple(animations)

    def configure(self, outputs: Sequence[RustOutputSpec]) -> None:
        wire_outputs = [output.to_wire() for output in outputs]
        pixel_counts = tuple(output.pixel_count for output in outputs)
        result = self._request(
            "configure",
            {"config": {"outputs": wire_outputs}},
            timeout=_CONFIGURE_TIMEOUT_SECONDS,
        )
        if result.get("type") != "configured":
            raise RustEngineError("Rust engine rejected output configuration")
        if result.get("output_count") != len(outputs) or result.get("total_pixels") != sum(
            pixel_counts
        ):
            raise RustEngineError("Rust engine returned an invalid output configuration")
        self._pixel_counts = pixel_counts

    def start_audio(self, selector: str | None, config: AudioConfig) -> bool:
        if selector is not None and not isinstance(selector, str):
            raise TypeError("Rust audio selector must be a string or None")
        result = self._request(
            "audio_start",
            {"selector": selector, "config": audio_config_to_wire(config)},
            timeout=_CONFIGURE_TIMEOUT_SECONDS,
        )
        if result.get("type") != "audio_configured":
            raise RustEngineError("Rust engine rejected audio capture start")
        return _bool_field(result, "active")

    def configure_audio(self, config: AudioConfig) -> bool:
        result = self._request(
            "audio_configure",
            {"config": audio_config_to_wire(config)},
            timeout=_CONFIGURE_TIMEOUT_SECONDS,
        )
        if result.get("type") != "audio_configured":
            raise RustEngineError("Rust engine rejected audio configuration")
        return _bool_field(result, "active")

    def stop_audio(self) -> None:
        result = self._request("audio_stop")
        if result.get("type") != "audio_stopped":
            raise RustEngineError("Rust engine did not stop audio capture")

    def audio_devices(self) -> tuple[RustAudioDevice, ...]:
        result = self._request("audio_devices")
        raw_devices = result.get("devices")
        if result.get("type") != "audio_devices" or not isinstance(raw_devices, list):
            raise RustEngineError("Rust engine returned invalid audio devices")
        return self._parse_audio_devices(raw_devices)

    def audio_status(self) -> EngineAudioStatus:
        result = self._request("audio_status")
        raw_status = result.get("status")
        if result.get("type") != "audio_status" or not isinstance(raw_status, dict):
            raise RustEngineError("Rust engine returned invalid audio status")
        raw_device = raw_status.get("device")
        device = None
        if raw_device is not None:
            device_list = self._parse_audio_devices([raw_device])
            device = device_list[0]
        return EngineAudioStatus(
            active=_bool_field(raw_status, "active"),
            device=device,
            frame=_mapping_field(raw_status, "frame"),
            features=_mapping_field(raw_status, "features"),
            structure=_mapping_field(raw_status, "structure"),
            processor=_mapping_field(raw_status, "processor"),
            capture=_mapping_field(raw_status, "capture"),
            last_frame_ns=_optional_nonnegative_int_field(raw_status, "last_frame_ns"),
            last_error=_optional_string_field(raw_status, "last_error"),
            audio_update_sequence=_nonnegative_int_field(raw_status, "audio_update_sequence"),
            dropped_audio_updates=_nonnegative_int_field(raw_status, "dropped_audio_updates"),
            dropped_structure_events=_nonnegative_int_field(raw_status, "dropped_structure_events"),
        )

    @staticmethod
    def _parse_audio_devices(raw_devices: list[object]) -> tuple[RustAudioDevice, ...]:
        devices: list[RustAudioDevice] = []
        for raw in raw_devices:
            if not isinstance(raw, dict):
                raise RustEngineError("Rust engine returned an invalid audio device")
            selector = raw.get("selector")
            host = raw.get("host")
            name = raw.get("name")
            sample_rate = raw.get("sample_rate")
            channels = raw.get("channels")
            if (
                not isinstance(selector, str)
                or not isinstance(host, str)
                or not isinstance(name, str)
                or type(sample_rate) is not int
                or sample_rate <= 0
                or type(channels) is not int
                or channels <= 0
                or type(raw.get("is_default")) is not bool
            ):
                raise RustEngineError("Rust engine returned an invalid audio device")
            devices.append(
                RustAudioDevice(
                    selector=selector,
                    host=host,
                    name=name,
                    is_default=raw["is_default"],
                    sample_rate=sample_rate,
                    channels=channels,
                )
            )
        return tuple(devices)

    def render_frame(
        self,
        *,
        sequence: int,
        timestamp_ns: int,
        delta_ns: int,
        brightness: float,
        outputs: Sequence[RustRenderOutput],
        audio: RustRenderAudioInput | None = None,
        power_limiting_enabled: bool = False,
        power_budget_watts: float | None = None,
        scheduler_fps: int | None = None,
    ) -> RustRenderAck:
        if sequence < 0 or timestamp_ns < 0 or delta_ns < 0:
            raise ValueError("Rust render sequence and timestamps must be non-negative")
        if not isfinite(brightness) or not 0.0 <= brightness <= 1.0:
            raise ValueError("Rust render brightness must be between zero and one")
        if len(outputs) != len(self._pixel_counts):
            raise ValueError("render output count does not match Rust output configuration")
        if not isinstance(power_limiting_enabled, bool):
            raise TypeError("Rust render power limiting state must be a boolean")
        if power_budget_watts is not None and (
            not isfinite(power_budget_watts) or power_budget_watts < 0.0
        ):
            raise ValueError("Rust render power budget must be finite and non-negative")
        if power_limiting_enabled and power_budget_watts is None:
            raise ValueError("enabled Rust render power limiting needs a watt budget")
        if scheduler_fps is not None and (
            type(scheduler_fps) is not int or not 1 <= scheduler_fps <= _MAX_RENDER_FPS
        ):
            raise ValueError(
                f"Rust render scheduler FPS must be between 1 and {_MAX_RENDER_FPS}"
            )
        frame: dict[str, object] = {
            "sequence": sequence,
            "timestamp_ns": timestamp_ns,
            "delta_ns": delta_ns,
            "brightness": brightness,
            "audio": None if audio is None else audio.to_wire(),
            "outputs": [output.to_wire() for output in outputs],
        }
        if power_limiting_enabled or power_budget_watts is not None:
            frame["power"] = {
                "enabled": power_limiting_enabled,
                "budget_watts": power_budget_watts,
            }
        data: dict[str, object] = {"frame": frame}
        if scheduler_fps is not None:
            data["scheduler"] = {"max_fps": scheduler_fps}
        result = self._request("render", data)
        if result.get("type") != "render_queued":
            raise RustEngineError("Rust engine returned an invalid render acknowledgement")
        returned_sequence = result.get("sequence")
        replaced = result.get("replaced")
        if type(returned_sequence) is not int or returned_sequence != sequence:
            raise RustEngineError("Rust engine render sequence does not match the request")
        if type(replaced) is not bool:
            raise RustEngineError("Rust engine render acknowledgement has an invalid replacement flag")
        return RustRenderAck(sequence=returned_sequence, replaced=replaced)

    def update_scene(
        self,
        *,
        revision: int,
        timestamp_ns: int,
        brightness: float,
        outputs: Sequence[RustRenderOutput],
        power_limiting_enabled: bool,
        power_budget_watts: float | None,
        scheduler_fps: int,
    ) -> bool:
        if type(revision) is not int or not 0 <= revision <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Rust scene revision must be an unsigned 64-bit integer")
        if type(timestamp_ns) is not int or timestamp_ns < 0:
            raise ValueError("Rust scene timestamp must be non-negative")
        if not isfinite(brightness) or not 0.0 <= brightness <= 1.0:
            raise ValueError("Rust scene brightness must be between zero and one")
        if len(outputs) != len(self._pixel_counts):
            raise ValueError("scene output count does not match Rust output configuration")
        if not isinstance(power_limiting_enabled, bool):
            raise TypeError("Rust scene power limiting state must be a boolean")
        if power_budget_watts is not None and (
            not isfinite(power_budget_watts) or power_budget_watts < 0.0
        ):
            raise ValueError("Rust scene power budget must be finite and non-negative")
        if power_limiting_enabled and power_budget_watts is None:
            raise ValueError("enabled Rust scene power limiting needs a watt budget")
        if type(scheduler_fps) is not int or not 1 <= scheduler_fps <= _MAX_RENDER_FPS:
            raise ValueError(
                f"Rust scene scheduler FPS must be between 1 and {_MAX_RENDER_FPS}"
            )
        update: dict[str, object] = {
            "revision": revision,
            "timestamp_ns": timestamp_ns,
            "brightness": brightness,
            "outputs": [output.to_wire() for output in outputs],
            "scheduler": {"max_fps": scheduler_fps},
        }
        if power_limiting_enabled or power_budget_watts is not None:
            update["power"] = {
                "enabled": power_limiting_enabled,
                "budget_watts": power_budget_watts,
            }
        result = self._request("scene_update", {"update": update})
        if result.get("type") != "scene_updated":
            raise RustEngineError("Rust engine returned an invalid scene update acknowledgement")
        returned_revision = result.get("revision")
        replaced = result.get("replaced")
        if type(returned_revision) is not int or returned_revision != revision:
            raise RustEngineError("Rust scene revision does not match the acknowledgement")
        if type(replaced) is not bool:
            raise RustEngineError("Rust scene update acknowledgement has an invalid replacement flag")
        return replaced

    def update_audio(
        self,
        *,
        sequence: int,
        timestamp_ns: int,
        audio: RustRenderAudioInput | None,
    ) -> bool:
        if type(sequence) is not int or not 0 <= sequence <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Rust audio sequence must be an unsigned 64-bit integer")
        if type(timestamp_ns) is not int or timestamp_ns < 0:
            raise ValueError("Rust audio timestamp must be non-negative")
        update = {
            "sequence": sequence,
            "timestamp_ns": timestamp_ns,
            "audio": None if audio is None else audio.to_wire(),
        }
        result = self._request("audio_update", {"update": update})
        if result.get("type") != "audio_updated":
            raise RustEngineError("Rust engine returned an invalid audio update acknowledgement")
        returned_sequence = result.get("sequence")
        accepted = result.get("accepted")
        if type(returned_sequence) is not int or returned_sequence != sequence:
            raise RustEngineError("Rust audio sequence does not match the acknowledgement")
        if type(accepted) is not bool:
            raise RustEngineError("Rust audio update acknowledgement has an invalid status")
        return accepted

    def open_frame_stream(self) -> socket.socket:
        if self._closed:
            raise RustEngineError("Rust engine process is closed")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(_REQUEST_TIMEOUT_SECONDS)
        try:
            connection.connect(str(self._socket_path))
        except OSError as exc:
            connection.close()
            raise RustEngineError(f"Rust engine frame socket failed: {exc}") from exc
        return connection

    def send_frame_set(
        self,
        connection: socket.socket,
        sequence: int,
        frames: Sequence[PixelFrame],
    ) -> int:
        if len(frames) != len(self._pixel_counts):
            raise RustEngineError("frame count does not match Rust output configuration")
        payload_bytes = 0
        views: list[memoryview] = []
        for index, (frame, pixel_count) in enumerate(
            zip(frames, self._pixel_counts, strict=True)
        ):
            if frame.dtype != np.uint8 or frame.shape != (pixel_count, 4):
                raise RustEngineError(
                    f"frame {index} must be uint8 with shape {(pixel_count, 4)}"
                )
            if not frame.flags.c_contiguous:
                raise RustEngineError(f"frame {index} must be C-contiguous")
            view = memoryview(frame).cast("B")
            payload_bytes += view.nbytes
            views.append(view)
        header = FRAME_HEADER.pack(
            FRAME_MAGIC, FRAME_VERSION, len(frames), sequence, payload_bytes
        )
        try:
            _send_vectors(connection, [memoryview(header), *views])
            ack_bytes = _read_exact(connection, FRAME_ACK.size)
        except (TimeoutError, OSError) as exc:
            raise RustEngineError(f"Rust engine frame IPC failed: {exc}") from exc
        magic, version, status, acknowledged_sequence, dropped = FRAME_ACK.unpack(
            ack_bytes
        )
        if magic != FRAME_ACK_MAGIC or version != FRAME_VERSION:
            raise RustEngineError("Rust engine returned an invalid frame acknowledgement")
        if acknowledged_sequence != sequence:
            raise RustEngineError("Rust engine frame sequence does not match the request")
        if status != 0:
            messages = {
                1: "Rust engine has no configured outputs",
                2: "Rust engine is shutting down",
                3: "Rust engine rejected an invalid frame set",
            }
            raise RustEngineError(messages.get(status, f"unknown frame status {status}"))
        return dropped

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def close(self) -> None:
        if self._closed:
            return
        process = self._process
        if process is not None and process.poll() is None:
            try:
                self._request("shutdown", timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            except OSError:
                pass
            try:
                process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self._closed = True
        self._temporary_directory.cleanup()

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            process = self._process
            if process is None:
                raise RustEngineError("Rust engine process was not created")
            if process.poll() is not None:
                raise RustEngineError(
                    f"Rust engine exited during startup with code {process.returncode}"
                )
            try:
                self.health()
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.01)
        detail = f": {last_error}" if last_error is not None else ""
        raise RustEngineError(f"Rust engine did not become ready in time{detail}")

    def _request(
        self,
        command: str,
        data: JSONCommandData = None,
        *,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, object]:
        with self._request_lock:
            return self._request_locked(command, data, timeout=timeout)

    def _request_locked(
        self,
        command: str,
        data: JSONCommandData,
        *,
        timeout: float,
    ) -> dict[str, object]:
        if self._closed:
            raise RustEngineError("Rust engine process is closed")
        request_id = str(uuid.uuid4())
        payload = _encode_request(request_id, command, data)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout)
                connection.connect(str(self._socket_path))
                connection.sendall(payload)
                response = bytearray()
                while not response.endswith(b"\n"):
                    chunk = connection.recv(
                        min(65_536, MAX_CONTROL_MESSAGE_BYTES + 1 - len(response))
                    )
                    if not chunk:
                        raise RustEngineError("Rust engine closed IPC before responding")
                    response.extend(chunk)
                    if len(response) > MAX_CONTROL_MESSAGE_BYTES:
                        raise RustEngineError("Rust engine control response exceeded the IPC limit")
        except (TimeoutError, OSError) as exc:
            if isinstance(exc, RustEngineError):
                raise
            raise RustEngineError(f"Rust engine IPC {command} failed: {exc}") from exc
        response.pop()
        return _decode_response(bytes(response), request_id)


@dataclass(slots=True)
class _ConfigureRequest:
    exchange: LatestFrameExchange | None
    output_lengths: tuple[int, ...]
    outputs: tuple[RustOutputSpec, ...]
    completed: Future[LatestFrameExchange | None]


@dataclass(frozen=True, slots=True)
class _RenderRequest:
    sequence: int
    timestamp_ns: int
    delta_ns: int
    brightness: float
    outputs: tuple[RustRenderOutput, ...]
    audio: RustRenderAudioInput | None
    power_limiting_enabled: bool
    power_budget_watts: float | None
    scheduler_fps: int | None


@dataclass(frozen=True, slots=True)
class _SceneUpdateRequest:
    revision: int
    timestamp_ns: int
    brightness: float
    outputs: tuple[RustRenderOutput, ...]
    power_limiting_enabled: bool
    power_budget_watts: float | None
    scheduler_fps: int


@dataclass(frozen=True, slots=True)
class _AudioUpdateRequest:
    sequence: int
    timestamp_ns: int
    audio: RustRenderAudioInput | None


class RustEngineOutputThread:
    """Bridge bounded frame and scene mailboxes to one Rust output process."""

    def __init__(
        self,
        output_lengths: Sequence[int],
        outputs: Sequence[RustOutputSpec],
        *,
        binary_path: Path | None = None,
    ) -> None:
        self._work_ready = threading.Event()
        self._render_lock = threading.Lock()
        self._process_lock = threading.RLock()
        self._pending_render_request: _RenderRequest | None = None
        self._control_lock = threading.Lock()
        self._pending_scene_updates: deque[_SceneUpdateRequest] = deque()
        self._pending_audio_update: _AudioUpdateRequest | None = None
        self._last_submitted_scene_revision: int | None = None
        self._last_submitted_audio_sequence: int | None = None
        self._last_submitted_audio_timestamp_ns: int | None = None
        self._exchange = _make_exchange(output_lengths, ready_event=self._work_ready)
        self._output_lengths = tuple(output_lengths)
        self._outputs = tuple(outputs)
        self._binary_path = binary_path
        self._process: RustEngineProcess | None = None
        self._requests: Queue[_ConfigureRequest] = Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._started = threading.Event()
        self._startup_exception: Exception | None = None
        self._thread = threading.Thread(
            target=self._run, name="lumistripe-rust-output-bridge", daemon=True
        )
        self._state_lock = threading.Lock()
        self._heartbeat_at: float | None = None
        self._last_frame_at: float | None = None
        self._transmitted_frames = 0
        self._failed_flushes = 0
        self._last_error: str | None = None
        self._last_successful_update_at: datetime | None = None
        self._last_successful_update_monotonic: float | None = None
        self._frame_latency_ms: float | None = None
        self._queue_latency_ms: float | None = None
        self._transmission_ms: float | None = None
        self._frame_latency_p50_ms: float | None = None
        self._frame_latency_p95_ms: float | None = None
        self._frame_latency_p99_ms: float | None = None
        self._audio_feature_to_led_latency_ms: float | None = None
        self._audio_feature_to_led_p50_ms: float | None = None
        self._audio_feature_to_led_p95_ms: float | None = None
        self._audio_feature_to_led_p99_ms: float | None = None
        self._ipc_latency_ms: float | None = None
        self._sequential_skew_ms: float | None = None
        self._render_time_ms: float | None = None
        self._estimated_power_watts: float | None = None
        self._applied_power_scale: float | None = None
        self._output_power_watts: tuple[float, ...] | None = None
        self._limiting_output_index: int | None = None
        self._preview_outputs: tuple[bytes, ...] | None = None
        self._frame_latency_samples: deque[float] = deque(
            maxlen=OUTPUT_LATENCY_SAMPLE_COUNT
        )
        self._output_rate = 0.0
        self._last_health_frames = 0
        self._health_dropped_frames = 0
        self._health_failed_frames = 0
        self._scheduler_missed_deadlines = 0
        self._selected_animations: tuple[str, ...] = ()
        self._scene_revisions: tuple[int, ...] = ()
        self._structure_event_sequences: tuple[int, ...] = ()
        self._cycling_active: tuple[bool, ...] = ()
        self._dynamic_gate_states: tuple[str, ...] = ()
        self._beat_clocks: tuple[RustBeatClockStats, ...] = ()
        self._sequence_playback: tuple[RustSequencePlaybackStats, ...] = ()
        self._effect_schedulers: tuple[RustEffectSchedulerStats, ...] = ()
        self._scene_control_revision: int | None = None
        self._audio_update_sequence: int | None = None
        self._scene_updates_replaced = 0
        self._audio_updates_replaced = 0
        self._render_request_dropped_frames = 0
        self._last_render_error: str | None = None
        self._health_next_poll_at = 0.0
        self._transport_failures = 0
        self._created_at_monotonic = time.monotonic()

    @property
    def exchange(self) -> LatestFrameExchange | None:
        return self._exchange

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        if not self._started.wait(timeout):
            raise TimeoutError("Rust output bridge did not initialize in time")
        if self._startup_exception is not None:
            raise self._startup_exception

    def reconfigure(
        self,
        output_lengths: Sequence[int],
        outputs: Sequence[RustOutputSpec],
        timeout: float = 10.0,
    ) -> LatestFrameExchange | None:
        return self.reconfigure_async(output_lengths, outputs).result(timeout)

    def reconfigure_async(
        self,
        output_lengths: Sequence[int],
        outputs: Sequence[RustOutputSpec],
    ) -> Future[LatestFrameExchange | None]:
        lengths = tuple(output_lengths)
        specs = tuple(outputs)
        if lengths != tuple(output.pixel_count for output in specs):
            raise ValueError("Rust output lengths must match their configuration")
        if self._stop_event.is_set() or not self._thread.is_alive():
            raise RuntimeError("Rust output bridge is not running")
        exchange = _make_exchange(lengths, ready_event=self._work_ready)
        completed: Future[LatestFrameExchange | None] = Future()
        try:
            self._requests.put_nowait(_ConfigureRequest(exchange, lengths, specs, completed))
        except Full:
            if exchange is not None:
                exchange.close(discard_pending=True)
            completed.set_exception(
                RuntimeError("a Rust output configuration is already pending")
            )
        self._work_ready.set()
        return completed

    def stats(self) -> OutputThreadStats:
        with self._state_lock:
            heartbeat = self._heartbeat_at
            last_frame = self._last_frame_at
            transmitted = self._transmitted_frames
            failed = self._failed_flushes + self._health_failed_frames
            rate = self._output_rate
            error = self._last_error
            successful_at = self._last_successful_update_at
            successful_monotonic = self._last_successful_update_monotonic
            frame_latency = self._frame_latency_ms
            queue_latency = self._queue_latency_ms
            transmission = self._transmission_ms
            frame_latency_p50 = self._frame_latency_p50_ms
            frame_latency_p95 = self._frame_latency_p95_ms
            frame_latency_p99 = self._frame_latency_p99_ms
            audio_feature_to_led_latency = self._audio_feature_to_led_latency_ms
            audio_feature_to_led_p50 = self._audio_feature_to_led_p50_ms
            audio_feature_to_led_p95 = self._audio_feature_to_led_p95_ms
            audio_feature_to_led_p99 = self._audio_feature_to_led_p99_ms
            ipc_latency = self._ipc_latency_ms
            sequential_skew = self._sequential_skew_ms
            render_time = self._render_time_ms
            estimated_power = self._estimated_power_watts
            applied_power_scale = self._applied_power_scale
            output_power = self._output_power_watts
            limiting_output_index = self._limiting_output_index
            preview_outputs = self._preview_outputs
            latency_samples = tuple(self._frame_latency_samples)
            health_dropped = self._health_dropped_frames
            render_dropped = self._render_request_dropped_frames
            scheduler_missed = self._scheduler_missed_deadlines
            selected_animations = self._selected_animations
            scene_revisions = self._scene_revisions
            structure_event_sequences = self._structure_event_sequences
            cycling_active = self._cycling_active
            dynamic_gate_states = self._dynamic_gate_states
            beat_clocks = self._beat_clocks
            sequence_playback = self._sequence_playback
            effect_schedulers = self._effect_schedulers
            scene_control_revision = self._scene_control_revision
            audio_update_sequence = self._audio_update_sequence
            scene_updates_replaced = self._scene_updates_replaced
            audio_updates_replaced = self._audio_updates_replaced
        with self._control_lock:
            pending_scene_updates = len(self._pending_scene_updates)
            pending_audio_update = self._pending_audio_update is not None
        exchange = self._exchange
        exchange_dropped = exchange.stats().dropped_count if exchange else 0
        successful_age = (
            max(0.0, time.monotonic() - successful_monotonic)
            if successful_monotonic is not None
            else None
        )
        return OutputThreadStats(
            heartbeat_at_monotonic=heartbeat,
            last_frame_at_monotonic=last_frame,
            transmitted_frames=transmitted,
            failed_flushes=failed,
            output_rate=rate,
            last_error=error,
            dropped_frames=exchange_dropped + health_dropped + render_dropped,
            last_successful_update_at=successful_at,
            last_successful_update_age_seconds=successful_age,
            frame_latency_ms=frame_latency,
            queue_latency_ms=queue_latency,
            transmission_ms=transmission,
            frame_latency_p50_ms=frame_latency_p50
            if frame_latency_p50 is not None
            else _nearest_rank_percentile(latency_samples, 0.50),
            frame_latency_p95_ms=frame_latency_p95
            if frame_latency_p95 is not None
            else _nearest_rank_percentile(latency_samples, 0.95),
            frame_latency_p99_ms=frame_latency_p99
            if frame_latency_p99 is not None
            else _nearest_rank_percentile(latency_samples, 0.99),
            audio_feature_to_led_latency_ms=audio_feature_to_led_latency,
            audio_feature_to_led_p50_ms=audio_feature_to_led_p50,
            audio_feature_to_led_p95_ms=audio_feature_to_led_p95,
            audio_feature_to_led_p99_ms=audio_feature_to_led_p99,
            ipc_latency_ms=ipc_latency,
            sequential_skew_ms=sequential_skew,
            frame_latency_sample_count=len(latency_samples),
            render_time_ms=render_time,
            estimated_power_watts=estimated_power,
            applied_power_scale=applied_power_scale,
            output_power_watts=output_power,
            limiting_output_index=limiting_output_index,
            preview_outputs=preview_outputs,
            scheduler_missed_deadlines=scheduler_missed,
            selected_animations=selected_animations,
            scene_revisions=scene_revisions,
            structure_event_sequences=structure_event_sequences,
            cycling_active=cycling_active,
            dynamic_gate_states=dynamic_gate_states,
            beat_clocks=beat_clocks,
            sequence_playback=sequence_playback,
            effect_schedulers=effect_schedulers,
            scene_control_revision=scene_control_revision,
            audio_update_sequence=audio_update_sequence,
            pending_scene_updates=pending_scene_updates,
            pending_audio_update=pending_audio_update,
            scene_updates_replaced=scene_updates_replaced,
            audio_updates_replaced=audio_updates_replaced,
        )

    def submit_scene_update(
        self,
        *,
        revision: int,
        timestamp_ns: int,
        brightness: float,
        outputs: Sequence[RustRenderOutput],
        power_limiting_enabled: bool,
        power_budget_watts: float | None,
        scheduler_fps: int,
    ) -> RustSceneUpdateAck:
        """Queue a reliable ordered scene control update without waiting for IPC."""
        if self._stop_event.is_set() or not self._thread.is_alive():
            raise RustEngineError("Rust output bridge is not running")
        if type(revision) is not int or not 0 <= revision <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Rust scene revision must be an unsigned 64-bit integer")
        if type(timestamp_ns) is not int or timestamp_ns < 0:
            raise ValueError("Rust scene timestamp must be non-negative")
        if not isfinite(brightness) or not 0.0 <= brightness <= 1.0:
            raise ValueError("Rust scene brightness must be between zero and one")
        if len(outputs) != len(self._output_lengths):
            raise ValueError("scene output count does not match Rust output configuration")
        if not isinstance(power_limiting_enabled, bool):
            raise TypeError("Rust scene power limiting state must be a boolean")
        if power_budget_watts is not None and (
            not isfinite(power_budget_watts) or power_budget_watts < 0.0
        ):
            raise ValueError("Rust scene power budget must be finite and non-negative")
        if power_limiting_enabled and power_budget_watts is None:
            raise ValueError("enabled Rust scene power limiting needs a watt budget")
        if type(scheduler_fps) is not int or not 1 <= scheduler_fps <= _MAX_RENDER_FPS:
            raise ValueError(
                f"Rust scene scheduler FPS must be between 1 and {_MAX_RENDER_FPS}"
            )
        request = _SceneUpdateRequest(
            revision=revision,
            timestamp_ns=timestamp_ns,
            brightness=brightness,
            outputs=tuple(outputs),
            power_limiting_enabled=power_limiting_enabled,
            power_budget_watts=power_budget_watts,
            scheduler_fps=scheduler_fps,
        )
        with self._control_lock:
            if (
                self._last_submitted_scene_revision is not None
                and revision <= self._last_submitted_scene_revision
            ):
                raise ValueError("Rust scene revisions must increase monotonically")
            if len(self._pending_scene_updates) >= _MAX_PENDING_SCENE_UPDATES:
                raise RustEngineError("Rust scene control queue is full")
            self._pending_scene_updates.append(request)
            self._last_submitted_scene_revision = revision
        self._work_ready.set()
        return RustSceneUpdateAck(revision=revision)

    def submit_audio_update(
        self,
        *,
        sequence: int,
        timestamp_ns: int,
        audio: RustRenderAudioInput | None,
    ) -> bool:
        """Replace the pending audio snapshot while retaining ordered sequence IDs."""
        if self._stop_event.is_set() or not self._thread.is_alive():
            raise RustEngineError("Rust output bridge is not running")
        if type(sequence) is not int or not 0 <= sequence <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("Rust audio sequence must be an unsigned 64-bit integer")
        if type(timestamp_ns) is not int or timestamp_ns < 0:
            raise ValueError("Rust audio timestamp must be non-negative")
        if audio is not None:
            audio.to_wire()
        request = _AudioUpdateRequest(
            sequence=sequence,
            timestamp_ns=timestamp_ns,
            audio=audio,
        )
        with self._control_lock:
            if (
                self._last_submitted_audio_sequence is not None
                and sequence <= self._last_submitted_audio_sequence
            ):
                raise ValueError("Rust audio sequences must increase monotonically")
            if (
                self._last_submitted_audio_timestamp_ns is not None
                and timestamp_ns < self._last_submitted_audio_timestamp_ns
            ):
                raise ValueError("Rust audio timestamps must not move backwards")
            replaced = self._pending_audio_update is not None
            self._pending_audio_update = request
            self._last_submitted_audio_sequence = sequence
            self._last_submitted_audio_timestamp_ns = timestamp_ns
        if replaced:
            with self._state_lock:
                self._audio_updates_replaced += 1
        self._work_ready.set()
        return replaced

    def submit_render_frame(
        self,
        *,
        sequence: int,
        timestamp_ns: int,
        delta_ns: int,
        brightness: float,
        outputs: Sequence[RustRenderOutput],
        audio: RustRenderAudioInput | None = None,
        power_limiting_enabled: bool = False,
        power_budget_watts: float | None = None,
        scheduler_fps: int | None = None,
    ) -> RustRenderAck:
        """Queue a latest-wins scene update without waiting for Rust IPC."""
        if self._stop_event.is_set() or not self._thread.is_alive():
            raise RustEngineError("Rust output bridge is not running")
        if sequence < 0 or timestamp_ns < 0 or delta_ns < 0:
            raise ValueError("Rust render sequence and timestamps must be non-negative")
        if not isfinite(brightness) or not 0.0 <= brightness <= 1.0:
            raise ValueError("Rust render brightness must be between zero and one")
        if len(outputs) != len(self._output_lengths):
            raise ValueError("render output count does not match Rust output configuration")
        if not isinstance(power_limiting_enabled, bool):
            raise TypeError("Rust render power limiting state must be a boolean")
        if power_budget_watts is not None and (
            not isfinite(power_budget_watts) or power_budget_watts < 0.0
        ):
            raise ValueError("Rust render power budget must be finite and non-negative")
        if power_limiting_enabled and power_budget_watts is None:
            raise ValueError("enabled Rust render power limiting needs a watt budget")
        if scheduler_fps is not None and (
            type(scheduler_fps) is not int or not 1 <= scheduler_fps <= _MAX_RENDER_FPS
        ):
            raise ValueError(
                f"Rust render scheduler FPS must be between 1 and {_MAX_RENDER_FPS}"
            )

        request = _RenderRequest(
            sequence=sequence,
            timestamp_ns=timestamp_ns,
            delta_ns=delta_ns,
            brightness=brightness,
            outputs=tuple(outputs),
            audio=audio,
            power_limiting_enabled=power_limiting_enabled,
            power_budget_watts=power_budget_watts,
            scheduler_fps=scheduler_fps,
        )
        with self._render_lock:
            replaced = self._pending_render_request is not None
            self._pending_render_request = request
            if replaced:
                with self._state_lock:
                    self._render_request_dropped_frames += 1
        with self._state_lock:
            if not self._selected_animations:
                self._selected_animations = tuple(
                    output.animation for output in request.outputs
                )
                self._scene_revisions = tuple(
                    output.scene_revision for output in request.outputs
                )
                self._cycling_active = tuple(
                    output.cycling is not None for output in request.outputs
                )
        self._work_ready.set()
        return RustRenderAck(sequence=sequence, replaced=replaced)

    def take_render_error(self) -> str | None:
        """Return and clear the last asynchronously rejected scene request."""
        with self._state_lock:
            error = self._last_render_error
            self._last_render_error = None
            return error

    def is_healthy(self, *, max_heartbeat_age_seconds: float) -> bool:
        if not self._thread.is_alive():
            return False
        with self._state_lock:
            heartbeat = self._heartbeat_at
            last_frame = self._last_frame_at
            transmitted = self._transmitted_frames
        now = time.monotonic()
        if heartbeat is None or now - heartbeat > max_heartbeat_age_seconds:
            return False
        exchange = self._exchange
        exchange_stats = exchange.stats() if exchange is not None else None
        submitted = exchange_stats.submitted_count if exchange_stats is not None else 0
        if submitted == 0:
            return True
        if transmitted == 0:
            return now - self._created_at_monotonic <= max_heartbeat_age_seconds
        return last_frame is not None and now - last_frame <= max_heartbeat_age_seconds

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        with self._render_lock:
            self._pending_render_request = None
        with self._control_lock:
            self._pending_scene_updates.clear()
            self._pending_audio_update = None
        self._work_ready.set()
        exchange = self._exchange
        if exchange is not None:
            exchange.close(discard_pending=True)
        self._thread.join(timeout + _SHUTDOWN_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            raise TimeoutError("Rust output bridge did not stop in time")

    def start_audio(self, selector: str | None, config: AudioConfig) -> bool:
        with self._process_lock:
            process = self._require_process()
            return process.start_audio(selector, config)

    def configure_audio(self, config: AudioConfig) -> bool:
        with self._process_lock:
            process = self._require_process()
            return process.configure_audio(config)

    def stop_audio(self) -> None:
        with self._process_lock:
            process = self._require_process()
            process.stop_audio()

    def audio_devices(self) -> tuple[RustAudioDevice, ...]:
        with self._process_lock:
            process = self._require_process()
            return process.audio_devices()

    def audio_status(self) -> EngineAudioStatus:
        with self._process_lock:
            process = self._require_process()
            return process.audio_status()

    def _require_process(self) -> RustEngineProcess:
        if self._stop_event.is_set() or not self._thread.is_alive():
            raise RustEngineError("Rust output bridge is not running")
        process = self._process
        if process is None:
            raise RustEngineError("Rust engine process is not ready")
        return process

    @property
    def process_is_running(self) -> bool:
        process = getattr(self, "_process", None)
        return process.is_running if process is not None else False

    def _run(self) -> None:
        process: RustEngineProcess | None = None
        frame_connection: socket.socket | None = None
        try:
            process = RustEngineProcess(binary_path=self._binary_path)
            process.configure(self._outputs)
            frame_connection = process.open_frame_stream()
            with self._process_lock:
                self._process = process
            self._record_heartbeat()
            self._started.set()
            while not self._stop_event.is_set():
                request = self._next_request()
                if request is not None:
                    self._apply_configuration(request, process)
                    continue
                self._work_ready.clear()
                request = self._next_request()
                if request is not None:
                    self._apply_configuration(request, process)
                    continue
                render_request = self._take_render_request()
                scene_update = self._take_scene_update()
                exchange = self._exchange
                if exchange is not None:
                    slot = exchange.take(timeout=0.0)
                else:
                    slot = None
                if render_request is not None and slot is not None:
                    if render_request.timestamp_ns >= slot.submitted_at_ns:
                        assert exchange is not None
                        exchange.release(slot)
                        self._increment_render_drop_count()
                        slot = None
                    else:
                        with self._render_lock:
                            if self._pending_render_request is None:
                                self._pending_render_request = render_request
                                self._work_ready.set()
                            else:
                                self._increment_render_drop_count()
                        render_request = None
                self._record_heartbeat()
                did_work = False
                scene_update_failed = False
                if scene_update is not None:
                    did_work = True
                    try:
                        ipc_started_at = time.monotonic()
                        replaced = process.update_scene(
                            revision=scene_update.revision,
                            timestamp_ns=scene_update.timestamp_ns,
                            brightness=scene_update.brightness,
                            outputs=scene_update.outputs,
                            power_limiting_enabled=(
                                scene_update.power_limiting_enabled
                            ),
                            power_budget_watts=scene_update.power_budget_watts,
                            scheduler_fps=scene_update.scheduler_fps,
                        )
                        with self._state_lock:
                            if replaced:
                                self._scene_updates_replaced += 1
                            self._ipc_latency_ms = (
                                time.monotonic() - ipc_started_at
                            ) * 1000.0
                            self._last_error = None
                    except Exception as exc:
                        self._record_update_error(exc)
                        if not process.is_running:
                            raise
                        scene_update_failed = True
                audio_update = (
                    None
                    if scene_update_failed
                    else self._take_audio_update_if_scene_queue_empty()
                )
                if audio_update is not None:
                    did_work = True
                    try:
                        ipc_started_at = time.monotonic()
                        accepted = process.update_audio(
                            sequence=audio_update.sequence,
                            timestamp_ns=audio_update.timestamp_ns,
                            audio=audio_update.audio,
                        )
                        if not accepted:
                            raise RustEngineError(
                                "Rust engine rejected a stale audio update"
                            )
                        with self._state_lock:
                            self._ipc_latency_ms = (
                                time.monotonic() - ipc_started_at
                            ) * 1000.0
                            self._last_error = None
                    except Exception as exc:
                        self._record_update_error(exc)
                        if not process.is_running:
                            raise
                if render_request is not None:
                    did_work = True
                    try:
                        ipc_started_at = time.monotonic()
                        process.render_frame(
                            sequence=render_request.sequence,
                            timestamp_ns=render_request.timestamp_ns,
                            delta_ns=render_request.delta_ns,
                            brightness=render_request.brightness,
                            outputs=render_request.outputs,
                            audio=render_request.audio,
                            power_limiting_enabled=render_request.power_limiting_enabled,
                            power_budget_watts=render_request.power_budget_watts,
                            scheduler_fps=render_request.scheduler_fps,
                        )
                        with self._state_lock:
                            self._ipc_latency_ms = (
                                time.monotonic() - ipc_started_at
                            ) * 1000.0
                            self._last_error = None
                    except Exception as exc:
                        self._record_error(exc)
                        with self._state_lock:
                            self._last_render_error = (
                                f"{type(exc).__name__}: {str(exc).strip()}"
                            )[:256]
                        if not process.is_running:
                            raise
                if slot is not None and exchange is not None:
                    did_work = True
                    try:
                        ipc_started_at = time.monotonic()
                        process.send_frame_set(frame_connection, slot.sequence, slot.frames)
                        with self._state_lock:
                            self._ipc_latency_ms = (
                                time.monotonic() - ipc_started_at
                            ) * 1000.0
                            self._last_error = None
                    except Exception as exc:
                        self._record_error(exc)
                        if not process.is_running:
                            raise
                    finally:
                        exchange.release(slot)
                self._record_heartbeat()
                if time.monotonic() >= self._health_next_poll_at:
                    try:
                        self._record_engine_health(process.health())
                    except OSError as exc:
                        self._record_error(exc)
                        if not process.is_running:
                            raise
                    self._health_next_poll_at = time.monotonic() + _HEALTH_POLL_SECONDS
                if not did_work:
                    timeout = max(
                        0.0,
                        min(
                            _HEALTH_POLL_SECONDS,
                            self._health_next_poll_at - time.monotonic(),
                        ),
                    )
                    self._work_ready.wait(timeout)
        except Exception as exc:  # noqa: BLE001 - the runtime watchdog observes a dead bridge
            if not self._started.is_set():
                self._startup_exception = exc
                self._started.set()
            else:
                self._record_error(exc)
        finally:
            exchange = self._exchange
            if exchange is not None:
                exchange.close(discard_pending=True)
            if frame_connection is not None:
                frame_connection.close()
            with self._process_lock:
                if process is not None:
                    process.close()
                self._process = None
            with self._render_lock:
                self._pending_render_request = None
            with self._control_lock:
                self._pending_scene_updates.clear()
                self._pending_audio_update = None
            self._fail_pending_requests()

    def _next_request(self) -> _ConfigureRequest | None:
        try:
            return self._requests.get_nowait()
        except Empty:
            return None

    def _take_render_request(self) -> _RenderRequest | None:
        with self._render_lock:
            request = self._pending_render_request
            self._pending_render_request = None
            return request

    def _take_scene_update(self) -> _SceneUpdateRequest | None:
        with self._control_lock:
            if not self._pending_scene_updates:
                return None
            return self._pending_scene_updates.popleft()

    def _take_audio_update_if_scene_queue_empty(self) -> _AudioUpdateRequest | None:
        with self._control_lock:
            if self._pending_scene_updates:
                return None
            request = self._pending_audio_update
            self._pending_audio_update = None
            return request

    def _record_update_error(self, exc: Exception) -> None:
        self._record_error(exc)
        with self._state_lock:
            self._last_render_error = (
                f"{type(exc).__name__}: {str(exc).strip()}"
            )[:256]

    def _increment_render_drop_count(self) -> None:
        with self._state_lock:
            self._render_request_dropped_frames += 1

    def _apply_configuration(
        self, request: _ConfigureRequest, process: RustEngineProcess
    ) -> None:
        with self._render_lock:
            if self._pending_render_request is not None:
                self._pending_render_request = None
                self._increment_render_drop_count()
        with self._control_lock:
            self._pending_scene_updates.clear()
            self._pending_audio_update = None
        current = self._exchange
        if current is not None:
            current.close(discard_pending=True)
        try:
            process.configure(request.outputs)
            baseline = process.health()
            self._exchange = request.exchange
            self._output_lengths = request.output_lengths
            self._outputs = request.outputs
            configured_at = time.monotonic()
            self._created_at_monotonic = configured_at
            self._health_next_poll_at = 0.0
            with self._state_lock:
                self._last_health_frames = baseline.frames_rendered
                self._transmitted_frames = baseline.frames_rendered
                self._last_frame_at = configured_at
                self._output_rate = baseline.output_rate
                self._frame_latency_ms = baseline.frame_latency_ms
                self._queue_latency_ms = baseline.queue_latency_ms
                self._transmission_ms = baseline.transmission_ms
                self._frame_latency_p50_ms = baseline.frame_latency_p50_ms
                self._frame_latency_p95_ms = baseline.frame_latency_p95_ms
                self._frame_latency_p99_ms = baseline.frame_latency_p99_ms
                self._audio_feature_to_led_latency_ms = (
                    baseline.audio_feature_to_led_latency_ms
                )
                self._audio_feature_to_led_p50_ms = baseline.audio_feature_to_led_p50_ms
                self._audio_feature_to_led_p95_ms = baseline.audio_feature_to_led_p95_ms
                self._audio_feature_to_led_p99_ms = baseline.audio_feature_to_led_p99_ms
                self._sequential_skew_ms = baseline.sequential_skew_ms
                self._render_time_ms = baseline.render_time_ms
                self._estimated_power_watts = baseline.estimated_power_watts
                self._applied_power_scale = baseline.applied_power_scale
                self._output_power_watts = baseline.output_power_watts
                self._limiting_output_index = baseline.limiting_output_index
                self._preview_outputs = baseline.preview_outputs
                self._scheduler_missed_deadlines = baseline.scheduler_missed_deadlines
                self._selected_animations = baseline.selected_animations
                self._scene_revisions = baseline.scene_revisions
                self._structure_event_sequences = baseline.structure_event_sequences
                self._cycling_active = baseline.cycling_active
                self._dynamic_gate_states = baseline.dynamic_gate_states
                self._beat_clocks = baseline.beat_clocks
                self._sequence_playback = baseline.sequence_playback
                self._effect_schedulers = baseline.effect_schedulers
                self._last_successful_update_at = baseline.last_successful_update_at
                self._last_successful_update_monotonic = None
                self._frame_latency_samples.clear()
                self._last_error = None
                self._last_render_error = None
                self._render_request_dropped_frames = 0
                self._scene_control_revision = baseline.scene_control_revision
                self._audio_update_sequence = baseline.audio_update_sequence
            request.completed.set_result(request.exchange)
        except Exception as exc:  # noqa: BLE001 - worker may roll back configuration
            self._exchange = None
            self._record_error(exc)
            request.completed.set_exception(exc)
        self._record_heartbeat()

    def _record_engine_health(self, health: EngineHealth) -> None:
        now = time.monotonic()
        with self._state_lock:
            has_new_frame = health.frames_rendered > self._last_health_frames
            if has_new_frame:
                self._last_frame_at = now
                self._last_health_frames = health.frames_rendered
            self._transmitted_frames = health.frames_rendered
            self._health_dropped_frames = health.dropped_frames
            self._health_failed_frames = health.failed_frames
            self._scheduler_missed_deadlines = health.scheduler_missed_deadlines
            self._scene_control_revision = health.scene_control_revision
            self._audio_update_sequence = health.audio_update_sequence
            self._selected_animations = health.selected_animations
            self._scene_revisions = health.scene_revisions
            self._structure_event_sequences = health.structure_event_sequences
            self._cycling_active = health.cycling_active
            self._dynamic_gate_states = health.dynamic_gate_states
            self._beat_clocks = health.beat_clocks
            self._sequence_playback = health.sequence_playback
            self._effect_schedulers = health.effect_schedulers
            self._output_rate = health.output_rate
            self._frame_latency_ms = health.frame_latency_ms
            self._queue_latency_ms = health.queue_latency_ms
            self._transmission_ms = health.transmission_ms
            self._frame_latency_p50_ms = health.frame_latency_p50_ms
            self._frame_latency_p95_ms = health.frame_latency_p95_ms
            self._frame_latency_p99_ms = health.frame_latency_p99_ms
            self._audio_feature_to_led_latency_ms = (
                health.audio_feature_to_led_latency_ms
            )
            self._audio_feature_to_led_p50_ms = health.audio_feature_to_led_p50_ms
            self._audio_feature_to_led_p95_ms = health.audio_feature_to_led_p95_ms
            self._audio_feature_to_led_p99_ms = health.audio_feature_to_led_p99_ms
            self._sequential_skew_ms = health.sequential_skew_ms
            self._render_time_ms = health.render_time_ms
            self._estimated_power_watts = health.estimated_power_watts
            self._applied_power_scale = health.applied_power_scale
            self._output_power_watts = health.output_power_watts
            self._limiting_output_index = health.limiting_output_index
            self._preview_outputs = health.preview_outputs
            if has_new_frame and health.frame_latency_ms is not None:
                self._frame_latency_samples.append(health.frame_latency_ms)
            if health.last_successful_update_at is not None:
                self._last_successful_update_at = health.last_successful_update_at
                age_seconds = max(
                    0.0,
                    datetime.now().astimezone().timestamp()
                    - health.last_successful_update_at.timestamp(),
                )
                self._last_successful_update_monotonic = now - age_seconds
            if health.last_error is not None:
                self._last_error = health.last_error
            elif self._transport_failures == 0:
                self._last_error = None
            self._heartbeat_at = now

    def _record_heartbeat(self) -> None:
        with self._state_lock:
            self._heartbeat_at = time.monotonic()

    def _record_error(self, exc: Exception) -> None:
        detail = str(exc).strip().replace("\n", " ")
        with self._state_lock:
            self._transport_failures += 1
            self._failed_flushes += 1
            self._last_error = f"{type(exc).__name__}: {detail}"[:256]
            self._heartbeat_at = time.monotonic()

    def _fail_pending_requests(self) -> None:
        while True:
            try:
                request = self._requests.get_nowait()
            except Empty:
                return
            try:
                request.completed.set_exception(
                    RuntimeError("Rust output bridge stopped")
                )
            except InvalidStateError:
                continue


def _make_exchange(
    output_lengths: Sequence[int], *, ready_event: threading.Event | None = None
) -> LatestFrameExchange | None:
    lengths = tuple(output_lengths)
    return LatestFrameExchange(lengths, ready_event=ready_event) if lengths else None


def _send_vectors(connection: socket.socket, vectors: list[memoryview]) -> None:
    pending = vectors
    while pending:
        sent = connection.sendmsg(pending)
        if sent <= 0:
            raise RustEngineError("Rust engine frame socket closed during write")
        while pending and sent >= pending[0].nbytes:
            sent -= pending[0].nbytes
            pending.pop(0)
        if pending and sent:
            pending[0] = pending[0][sent:]


def _read_exact(connection: socket.socket, length: int) -> bytes:
    result = bytearray(length)
    view = memoryview(result)
    offset = 0
    while offset < length:
        read = connection.recv_into(view[offset:])
        if read == 0:
            raise RustEngineError("Rust engine closed the frame socket before acknowledging")
        offset += read
    return bytes(result)


def _bool_field(value: dict[str, object], key: str) -> bool:
    result = value.get(key)
    if type(result) is not bool:
        raise RustEngineError(f"Rust engine health {key} is invalid")
    return result


def _mapping_field(value: dict[str, object], key: str) -> Mapping[str, object]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise RustEngineError(f"Rust engine audio status {key} is invalid")
    return result


def _optional_string_field(value: dict[str, object], key: str) -> str | None:
    result = value.get(key)
    if result is not None and not isinstance(result, str):
        raise RustEngineError(f"Rust engine audio status {key} is invalid")
    return result


def _nonnegative_int_field(value: dict[str, object], key: str) -> int:
    result = value.get(key)
    if type(result) is not int or result < 0:
        raise RustEngineError(f"Rust engine health {key} is invalid")
    return result


def _optional_nonnegative_int_field(value: dict[str, object], key: str) -> int | None:
    result = value.get(key)
    if result is None:
        return None
    if type(result) is not int or not 0 <= result <= 0xFFFFFFFFFFFFFFFF:
        raise RustEngineError(f"Rust engine health {key} is invalid")
    return result


def _number_field(value: dict[str, object], key: str) -> float:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise RustEngineError(f"Rust engine health {key} is invalid")
    number = float(result)
    if not np.isfinite(number) or number < 0.0:
        raise RustEngineError(f"Rust engine health {key} is invalid")
    return number


def _optional_number_field(value: dict[str, object], key: str) -> float | None:
    result = value.get(key)
    if result is None:
        return None
    return _number_field(value, key)


def _nearest_rank_percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(len(ordered) * quantile) - 1))
    return ordered[index]


__all__ = [
    "EngineAudioStatus",
    "EngineHealth",
    "RustAudioDevice",
    "RustEngineError",
    "RustEngineOutputThread",
    "RustEngineProcess",
    "RustOutputSpec",
    "RustRenderCycling",
    "RustRenderStructureEvent",
    "RustSceneUpdateAck",
    "audio_config_to_wire",
]
