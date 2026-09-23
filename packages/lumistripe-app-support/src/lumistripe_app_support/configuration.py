"""Shared, UI-agnostic configuration for LumiStripe applications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lumistripe.audio.config import (
    AudioAnalysis,
    AudioConfig,
    AudioNormalization,
    AudioSmoothing,
)
from lumistripe.playback import (
    AudioSource,
    CycleOrder,
    CycleTiming,
    CyclingConfig,
    MusicActivityConfig,
)
from lumistripe.selector import DynamicSelectorConfig


class _CyclingOptions(Protocol):
    cycle_order: CycleOrder
    cycle_timing: CycleTiming
    cycle_interval: float
    dynamic_seed: int | None


class _SelectorOptions(Protocol):
    dynamic_min_duration: float
    dynamic_max_duration: float
    dynamic_switch_cooldown: float
    dynamic_drop_cooldown: float
    dynamic_randomness: float
    dynamic_history_size: int
    dynamic_seed: int | None


@dataclass(frozen=True, slots=True)
class ActivityPolicyOptions:
    """CLI-independent values used to construct music activity gating."""

    idle_enter_frames: int
    idle_threshold_scale: float
    activation_delay_s: float


def build_audio_config(
    *,
    target_level: float,
    noise_floor: float,
    analysis: AudioAnalysis | None = None,
) -> AudioConfig:
    """Build the common normalized audio configuration used by applications."""

    return AudioConfig(
        smoothing=AudioSmoothing(noise_floor=noise_floor),
        normalization=AudioNormalization(target_level=target_level),
        analysis=analysis if analysis is not None else AudioAnalysis(),
    )


def build_cycling_config(options: _CyclingOptions) -> CyclingConfig:
    return CyclingConfig(
        order=options.cycle_order,
        timing=options.cycle_timing,
        interval_s=options.cycle_interval,
        seed=options.dynamic_seed,
    )


def build_dynamic_selector_config(options: _SelectorOptions) -> DynamicSelectorConfig:
    return DynamicSelectorConfig(
        min_duration_s=options.dynamic_min_duration,
        max_duration_s=options.dynamic_max_duration,
        switch_cooldown_s=options.dynamic_switch_cooldown,
        drop_cooldown_s=options.dynamic_drop_cooldown,
        randomness=options.dynamic_randomness,
        history_size=options.dynamic_history_size,
        seed=options.dynamic_seed,
    )


def build_activity_config(options: ActivityPolicyOptions) -> MusicActivityConfig:
    defaults = MusicActivityConfig()
    scale = options.idle_threshold_scale
    return MusicActivityConfig(
        feature_attack=defaults.feature_attack,
        feature_release=defaults.feature_release,
        idle_enter_frames=options.idle_enter_frames,
        activation_delay_s=options.activation_delay_s,
        energy_threshold=defaults.energy_threshold * scale,
        onset_threshold=defaults.onset_threshold * scale,
        beat_density_threshold=defaults.beat_density_threshold * scale,
        brightness_threshold=defaults.brightness_threshold * scale,
    )


def parse_audio_source(value: str, *, allow_bluetooth: bool = False) -> AudioSource:
    """Parse an application audio source with a consistent error message."""

    try:
        source = AudioSource(value.lower())
    except ValueError as exc:
        raise ValueError(f"invalid audio source: {value}") from exc
    if source is AudioSource.BLUETOOTH and not allow_bluetooth:
        raise ValueError("Bluetooth audio is only available through lumistripe-web")
    return source


__all__ = [
    "ActivityPolicyOptions",
    "build_activity_config",
    "build_audio_config",
    "build_cycling_config",
    "build_dynamic_selector_config",
    "parse_audio_source",
]
