from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from random import Random

from ..audio import MusicFeatures
from .base import AnimationPlayer


class AnimationClass(str, Enum):
    AMBIENT = "ambient"
    CALM = "calm"
    GROOVY = "groovy"
    HARD_DROP = "hard_drop"
    FAST_PARTY = "fast_party"
    BASS_HEAVY = "bass_heavy"
    VOCAL_POP = "vocal_pop"
    CHAOTIC = "chaotic"


@dataclass(frozen=True, slots=True)
class MusicSelectorConfig:
    class_dwell_frames: int = 120
    animation_dwell_frames: int = 150
    confidence_threshold: float = 0.06
    feature_attack: float = 0.28
    feature_release: float = 0.08
    idle_enter_frames: int = 60
    idle_energy_threshold: float = 0.03
    idle_onset_threshold: float = 0.025
    idle_beat_density_threshold: float = 0.05
    idle_brightness_threshold: float = 0.08


@dataclass(slots=True)
class MusicDrivenSelector:
    config: MusicSelectorConfig = field(default_factory=MusicSelectorConfig)
    current_class: AnimationClass = AnimationClass.AMBIENT
    current_animation_name: str | None = None
    last_class_switch_frame: int = 0
    last_animation_switch_frame: int = 0
    frame_count: int = 0
    energy_short: float = 0.0
    energy_long: float = 0.0
    bass_short: float = 0.0
    mid_short: float = 0.0
    high_short: float = 0.0
    accent_short: float = 0.0
    beat_density: float = 0.0
    brightness_smooth: float = 0.0
    onset_smooth: float = 0.0
    bpm_smooth: float = 120.0
    auto_select: bool = True
    idle_active: bool = False
    _idle_frames: int = 0
    _class_animation_queue: list[str] = field(default_factory=list)
    _played_recently: list[str] = field(default_factory=list)
    _rng: Random = field(default_factory=Random)

    def set_auto_select(self, enabled: bool) -> None:
        self.auto_select = enabled

    def set_manual_class(self, player: AnimationPlayer, class_: AnimationClass) -> None:
        self.current_class = class_
        self.last_class_switch_frame = self.frame_count
        self.current_animation_name = None
        self.last_animation_switch_frame = 0
        class_names = self._available_class_names(player, self.current_class)
        self._reset_class_rotation(class_names)
        if class_names:
            chosen = self._choose_class_animation(class_names)
            if chosen is not None:
                self.current_animation_name = chosen
                self.last_animation_switch_frame = self.frame_count
                index = player.index_of(self.current_animation_name)
                if index is not None:
                    player.set_index(index)

    def set_manual_animation(self, player: AnimationPlayer, name: str) -> None:
        self.current_animation_name = name
        index = player.index_of(name)
        if index is not None:
            player.set_index(index)
        classes = CLASS_MAP.get(name, ())
        if classes:
            self.current_class = classes[0]
        class_names = self._available_class_names(player, self.current_class)
        self._reset_class_rotation(class_names, current_name=name)
        self.last_animation_switch_frame = self.frame_count

    def update(self, player: AnimationPlayer, features: MusicFeatures) -> AnimationClass:
        self.frame_count += 1
        self._update_features(features)
        if not self.auto_select:
            return self.current_class

        self._update_idle_state()
        if self.idle_active:
            idle_names = self._idle_animation_names(player)
            if idle_names and self._should_rotate_animation():
                chosen = self._choose_class_animation(idle_names)
                if chosen is not None:
                    self.current_animation_name = chosen
                    self.last_animation_switch_frame = self.frame_count
                    index = player.index_of(self.current_animation_name)
                    if index is not None and index != player.current_index():
                        player.set_index(index)
            return self.current_class

        scores = self._class_scores()
        target_class = max(scores, key=scores.__getitem__)
        if self._should_switch_class(target_class, scores):
            self.current_class = target_class
            self.last_class_switch_frame = self.frame_count
            self.current_animation_name = None
            self.last_animation_switch_frame = 0
            self._reset_class_rotation(self._available_class_names(player, self.current_class))

        class_names = self._available_class_names(player, self.current_class)
        if class_names and self._should_rotate_animation():
            chosen = self._choose_class_animation(class_names)
            if chosen is not None:
                self.current_animation_name = chosen
                self.last_animation_switch_frame = self.frame_count
                index = player.index_of(self.current_animation_name)
                if index is not None and index != player.current_index():
                    player.set_index(index)

        return self.current_class

    def _update_features(self, features: MusicFeatures) -> None:
        mid = (features.bands[2] + features.bands[3] + features.bands[4]) / 3.0
        high = (features.bands[5] + features.bands[6] + features.bands[7]) / 3.0
        short_attack = self.config.feature_attack
        short_release = self.config.feature_release
        self.energy_short = _smooth(self.energy_short, features.energy, short_attack, short_release)
        self.bass_short = _smooth(self.bass_short, features.bass, short_attack, short_release)
        self.mid_short = _smooth(self.mid_short, mid, short_attack, short_release)
        self.high_short = _smooth(self.high_short, high, short_attack, short_release)
        self.accent_short = _smooth(self.accent_short, features.beat_strength, 0.45, 0.12)
        beat_target = 1.0 if features.beat else 0.0
        self.beat_density = _smooth(self.beat_density, beat_target, 0.3, 0.06)
        self.energy_long = _smooth(self.energy_long, self.energy_short, 0.05, 0.02)
        self.brightness_smooth = _smooth(self.brightness_smooth, features.brightness, 0.2, 0.1)
        self.onset_smooth = _smooth(self.onset_smooth, features.onset_strength, 0.25, 0.1)
        self.bpm_smooth = _smooth(self.bpm_smooth, features.bpm, 0.1, 0.02)

    def _class_scores(self) -> dict[AnimationClass, float]:
        trend = self.energy_short - self.energy_long
        rise = max(trend, 0.0)
        bass_ratio = self.bass_short / max(self.energy_short, 0.08)
        bass_ratio = min(bass_ratio, 2.0)
        mid_ratio = self.mid_short / max(self.energy_short, 0.01)
        mid_ratio = min(mid_ratio, 2.0)
        bass_dominance = max(0.0, self.bass_short - (self.mid_short * 0.65 + self.high_short * 0.45))
        bass_dominance = min(bass_dominance * 1.8, 1.0)
        low_energy_presence = max(0.0, 1.0 - self.energy_short * 6.0)
        active_energy = max(0.0, self.energy_short - 0.24)
        stable_energy = max(0.0, 1.0 - abs(trend) * 4.0)
        quiet_bonus = max(0.0, 0.2 - self.energy_short) / 0.2
        soft_bass_bonus = max(0.0, 0.12 - self.bass_short) / 0.12
        groove_support = self.beat_density * 0.45 + self.mid_short * 0.35 + self.brightness_smooth * 0.2
        sustained_bass_groove = self.bass_short * 0.45 + self.mid_short * 0.25 + bass_ratio * 0.15 + stable_energy * 0.15

        return {
            AnimationClass.AMBIENT: (
                (1.0 - self.energy_short) * 0.85
                + (1.0 - self.beat_density) * 0.45
                + (1.0 - self.onset_smooth) * 0.3
                + (1.0 - self.bass_short) * 0.2
                - groove_support * 0.35
            ),
            AnimationClass.CALM: (
                (1.0 - self.energy_short) * 0.95
                + (1.0 - self.beat_density) * 0.22
                + (1.0 - self.onset_smooth) * 0.18
                + low_energy_presence * 0.35
                + quiet_bonus * 0.2
                + soft_bass_bonus * 0.05
                - self.bass_short * 0.35
                - bass_ratio * 0.08
                - active_energy * 1.1
            ),
            AnimationClass.GROOVY: (
                self.energy_short * 0.4
                + self.beat_density * 0.45
                + self.bass_short * 0.45
                + self.mid_short * 0.4
                + sustained_bass_groove * 0.6
                + active_energy * 0.3
                + low_energy_presence * 0.15
                - bass_dominance * 0.2
            ),
            AnimationClass.HARD_DROP: rise * 3.2 + self.bass_short * 0.7 + self.accent_short * 0.5 + self.onset_smooth * 0.6 + bass_dominance * 0.3 + (1.0 - self.high_short) * 0.3,
            AnimationClass.FAST_PARTY: (
                self.energy_short * 1.05
                + self.beat_density * 0.55
                + self.high_short * 0.45
                + self.brightness_smooth * 0.35
                + self.bpm_smooth / 250.0 * 0.2
                + self.bass_short * 0.2
                + self.onset_smooth * 0.3
                + active_energy * 0.45
                - low_energy_presence * 0.35
                - bass_dominance * 0.32
            ),
            AnimationClass.BASS_HEAVY: (
                self.bass_short * 0.6
                + bass_ratio * 0.42
                + bass_dominance * 0.6
                + self.energy_short * 0.42
                + stable_energy * 0.12
                + self.beat_density * 0.1
                - low_energy_presence * 0.18
                - self.high_short * 0.15
            ),
            AnimationClass.VOCAL_POP: self.mid_short * 0.6 + mid_ratio * 0.6 + self.energy_short * 0.3 + self.beat_density * 0.3 + self.brightness_smooth * 0.35 + (1.0 - bass_dominance) * 0.3,
            AnimationClass.CHAOTIC: self.energy_short * 0.6 + self.accent_short * 1.0 + self.beat_density * 0.6 + self.high_short * 0.5 + self.onset_smooth * 1.0 + abs(trend) * 0.6,
        }

    def _should_switch_class(self, target_class: AnimationClass, scores: dict[AnimationClass, float]) -> bool:
        if target_class == self.current_class:
            return False
        if (self.frame_count - self.last_class_switch_frame) < self.config.class_dwell_frames:
            return False
        sorted_scores = sorted(scores.values(), reverse=True)
        confidence = sorted_scores[0] - sorted_scores[1] if len(sorted_scores) > 1 else sorted_scores[0]
        return confidence >= self.config.confidence_threshold

    def _should_rotate_animation(self) -> bool:
        if self.current_animation_name is None:
            return True
        return (self.frame_count - self.last_animation_switch_frame) >= self.config.animation_dwell_frames

    def _choose_class_animation(self, class_names: list[str]) -> str | None:
        if not class_names:
            return None

        allowed = set(class_names)
        self._played_recently = [name for name in self._played_recently if name in allowed]
        available = [n for n in class_names if n not in self._played_recently]
        if not available:
            available = class_names
            self._played_recently.clear()

        self._class_animation_queue = [name for name in self._class_animation_queue if name in available]

        if not self._class_animation_queue:
            queue = list(available)
            self._rng.shuffle(queue)
            self._class_animation_queue = queue

        if self._class_animation_queue:
            chosen = self._class_animation_queue.pop(0)
        else:
            chosen = available[0]

        self._played_recently.append(chosen)
        if len(self._played_recently) > 4:
            self._played_recently.pop(0)

        return chosen

    def _update_idle_state(self) -> None:
        if self._is_music_active():
            if self.idle_active:
                self.idle_active = False
                self._idle_frames = 0
                self.current_animation_name = None
                self.last_animation_switch_frame = 0
                self._class_animation_queue.clear()
            return

        self._idle_frames += 1
        if not self.idle_active and self._idle_frames >= self.config.idle_enter_frames:
            self.idle_active = True
            self.current_animation_name = None
            self.last_animation_switch_frame = 0
            self._class_animation_queue.clear()

    def _is_music_active(self) -> bool:
        return any(
            (
                self.energy_short >= self.config.idle_energy_threshold,
                self.onset_smooth >= self.config.idle_onset_threshold,
                self.beat_density >= self.config.idle_beat_density_threshold,
                self.brightness_smooth >= self.config.idle_brightness_threshold,
            )
        )

    def _idle_animation_names(self, player: AnimationPlayer) -> list[str]:
        names: list[str] = []
        for entry in player.animations:
            classes = CLASS_MAP.get(entry.animation.name, ())
            if AnimationClass.AMBIENT in classes or AnimationClass.CALM in classes:
                names.append(entry.animation.name)
        return names

    def _reset_class_rotation(self, class_names: list[str], *, current_name: str | None = None) -> None:
        self._class_animation_queue.clear()
        allowed = set(class_names)
        self._played_recently = [name for name in self._played_recently if name in allowed and name != current_name]
        if current_name is not None and current_name in allowed:
            self._played_recently.append(current_name)
            if len(self._played_recently) > 4:
                self._played_recently = self._played_recently[-4:]

    def _available_class_names(self, player: AnimationPlayer, class_: AnimationClass) -> list[str]:
        names: list[str] = []
        for entry in player.animations:
            if class_ in CLASS_MAP.get(entry.animation.name, ()):
                names.append(entry.animation.name)
        return names


CLASS_MAP: dict[str, tuple[AnimationClass, ...]] = {
    "aurora": (AnimationClass.AMBIENT, AnimationClass.CALM),
    "wave": (AnimationClass.AMBIENT, AnimationClass.CALM, AnimationClass.GROOVY),
    "rainbow": (AnimationClass.AMBIENT, AnimationClass.CALM),
    "twinkle": (AnimationClass.AMBIENT, AnimationClass.CALM),
    "plasma_rave": (AnimationClass.AMBIENT, AnimationClass.GROOVY, AnimationClass.FAST_PARTY),
    "pulse": (AnimationClass.GROOVY, AnimationClass.BASS_HEAVY),
    "confetti": (AnimationClass.GROOVY, AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC),
    "rainbow_cycle": (AnimationClass.GROOVY, AnimationClass.FAST_PARTY),
    "theater_chase": (AnimationClass.GROOVY, AnimationClass.FAST_PARTY),
    "bouncing_ball": (AnimationClass.GROOVY, AnimationClass.CALM),
    "disco_sparkle": (AnimationClass.GROOVY, AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC),
    "beat_tunnel": (AnimationClass.GROOVY, AnimationClass.BASS_HEAVY),
    "laser_sweep": (AnimationClass.FAST_PARTY, AnimationClass.GROOVY),
    "comet_storm": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC, AnimationClass.GROOVY),
    "comet": (AnimationClass.FAST_PARTY, AnimationClass.GROOVY),
    "beat_explosion": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC, AnimationClass.BASS_HEAVY),
    "firework_burst": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC),
    "bpm": (AnimationClass.BASS_HEAVY, AnimationClass.FAST_PARTY),
    "beat_wave": (AnimationClass.BASS_HEAVY, AnimationClass.FAST_PARTY),
    "shockwave": (AnimationClass.HARD_DROP, AnimationClass.BASS_HEAVY, AnimationClass.FAST_PARTY),
    "bass_drop": (AnimationClass.HARD_DROP, AnimationClass.BASS_HEAVY),
    "drop_explosion": (AnimationClass.HARD_DROP, AnimationClass.CHAOTIC),
    "fire": (AnimationClass.CHAOTIC, AnimationClass.BASS_HEAVY, AnimationClass.HARD_DROP),
    "lightning_strike": (AnimationClass.CHAOTIC, AnimationClass.FAST_PARTY),
    "police": (AnimationClass.CHAOTIC, AnimationClass.FAST_PARTY),
    "strobe": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC),
    "peak_mirror": (AnimationClass.VOCAL_POP, AnimationClass.GROOVY, AnimationClass.FAST_PARTY),
    "sinelon": (AnimationClass.VOCAL_POP, AnimationClass.GROOVY, AnimationClass.CALM),
    "juggle": (AnimationClass.VOCAL_POP, AnimationClass.GROOVY, AnimationClass.FAST_PARTY),
    "color_wipe": (AnimationClass.VOCAL_POP, AnimationClass.GROOVY),
    "dual_comet": (AnimationClass.VOCAL_POP, AnimationClass.FAST_PARTY, AnimationClass.GROOVY),
    "rave_pulse": (AnimationClass.FAST_PARTY, AnimationClass.BASS_HEAVY, AnimationClass.CHAOTIC),
    "neon_storm": (AnimationClass.CHAOTIC, AnimationClass.FAST_PARTY),
    "pixel_explosion": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC),
    "dual_laser": (AnimationClass.GROOVY, AnimationClass.FAST_PARTY),
    "rainbow_strobe": (AnimationClass.FAST_PARTY, AnimationClass.CHAOTIC),
    "beat_ripple": (AnimationClass.BASS_HEAVY, AnimationClass.GROOVY),
    "dance_floor": (AnimationClass.GROOVY, AnimationClass.FAST_PARTY),
    "electric_storm": (AnimationClass.CHAOTIC, AnimationClass.BASS_HEAVY),
    "glow_rush": (AnimationClass.GROOVY, AnimationClass.AMBIENT),
    "hard_beat": (AnimationClass.BASS_HEAVY, AnimationClass.HARD_DROP, AnimationClass.FAST_PARTY),
}


def _smooth(current: float, target: float, attack: float, release: float) -> float:
    factor = attack if target >= current else release
    factor = max(0.0, min(1.0, factor))
    return current + (target - current) * factor
