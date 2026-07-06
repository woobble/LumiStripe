from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Any

from ..audio import AudioFeatures
from .metadata import AnimationMetadata, animation_metadata


@dataclass(frozen=True, slots=True)
class AutoSelectorConfig:
    enabled: bool = True
    mode: str = "dj"
    min_duration_s: float = 12.0
    max_duration_s: float = 45.0
    switch_cooldown_s: float = 8.0
    drop_cooldown_s: float = 15.0
    randomness: float = 0.15
    history_size: int = 5
    seed: int | None = None
    switch_margin: float = 0.12


@dataclass(frozen=True, slots=True)
class AnimationScore:
    name: str
    score: float
    metadata: AnimationMetadata
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectorDecision:
    selected_name: str | None
    selected_score: float
    current_name: str | None
    should_switch: bool
    reason: str
    scores: tuple[AnimationScore, ...] = ()


@dataclass(slots=True)
class AnimationScoringEngine:
    config: AutoSelectorConfig = field(default_factory=AutoSelectorConfig)
    _rng: Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = Random(self.config.seed)

    def rank(
        self,
        animations: list[Any],
        features: AudioFeatures,
        *,
        current_name: str | None = None,
        recent_names: tuple[str, ...] = (),
    ) -> tuple[AnimationScore, ...]:
        scores = [
            self.score_animation(entry.animation if hasattr(entry, "animation") else entry, features, current_name=current_name, recent_names=recent_names)
            for entry in animations
        ]
        return tuple(sorted(scores, key=lambda item: item.score, reverse=True))

    def score_animation(
        self,
        animation: Any,
        features: AudioFeatures,
        *,
        current_name: str | None = None,
        recent_names: tuple[str, ...] = (),
    ) -> AnimationScore:
        metadata = animation_metadata(animation)
        name = metadata.name or str(getattr(animation, "name", ""))
        energy = _feature_value(features, "energy_level", "energy", "volume")
        bass = _feature_value(features, "bass_energy", "bass")
        mid = _feature_value(features, "mid_energy")
        treble = _feature_value(features, "treble_energy")
        beat_confidence = _feature_value(features, "beat_confidence", "beat_strength")
        bpm = max(0.0, float(getattr(features, "bpm", 120.0)))

        score = metadata.weight
        reasons: list[str] = [f"weight={metadata.weight:0.2f}"]

        energy_fit = _range_fit(energy, metadata.energy_min, metadata.energy_max)
        score += energy_fit * 0.85
        reasons.append(f"energy={energy_fit:0.2f}")

        bpm_fit = _range_fit(bpm, metadata.bpm_min, metadata.bpm_max, scale=60.0)
        score += bpm_fit * 0.35
        reasons.append(f"bpm={bpm_fit:0.2f}")

        spectrum_fit = (
            (1.0 - abs(metadata.prefers_bass - bass)) * metadata.prefers_bass
            + (1.0 - abs(metadata.prefers_mid - mid)) * metadata.prefers_mid
            + (1.0 - abs(metadata.prefers_treble - treble)) * metadata.prefers_treble
        )
        score += spectrum_fit * 0.55
        if spectrum_fit > 0.0:
            reasons.append(f"spectrum={spectrum_fit:0.2f}")

        if metadata.supports_beats:
            score += beat_confidence * 0.28
        elif beat_confidence > 0.35:
            score -= beat_confidence * 0.25

        if bool(getattr(features, "drop_detected", False)):
            if metadata.supports_drops:
                score += 1.1
                reasons.append("drop")
            else:
                score -= 0.18

        if bool(getattr(features, "silence", False)):
            if metadata.supports_silence:
                score += 0.9
                reasons.append("silence")
            else:
                score -= 0.65

        intensity_fit = 1.0 - abs(metadata.intensity - energy)
        score += max(0.0, intensity_fit) * 0.28
        reasons.append(f"intensity={intensity_fit:0.2f}")

        if name == current_name:
            score += 0.18
            reasons.append("current")

        if name in recent_names:
            distance = len(recent_names) - recent_names.index(name)
            penalty = 0.18 + (0.12 * distance)
            score -= penalty
            reasons.append(f"fatigue=-{penalty:0.2f}")

        jitter = self._rng.uniform(-self.config.randomness, self.config.randomness) if self.config.randomness > 0.0 else 0.0
        score += jitter
        if jitter:
            reasons.append(f"random={jitter:0.2f}")

        return AnimationScore(name=name, score=score, metadata=metadata, reasons=tuple(reasons))


def _feature_value(features: AudioFeatures, *names: str) -> float:
    for name in names:
        value = getattr(features, name, 0.0)
        if value:
            return _clamp01(float(value))
    return 0.0


def _range_fit(value: float, low: float, high: float, *, scale: float = 0.25) -> float:
    if low <= value <= high:
        return 1.0
    distance = low - value if value < low else value - high
    return _clamp01(1.0 - distance / max(scale, 1e-6))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
