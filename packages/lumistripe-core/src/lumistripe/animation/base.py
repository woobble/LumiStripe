from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from random import Random

from ..audio import AudioFrame
from ..controller import BrightnessController, Controller


class Animation(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def tick(self, frame: int, controller: Controller) -> None:
        raise NotImplementedError

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        self.tick(frame, controller)

    @property
    def metadata(self):
        from ..selector import animation_metadata

        return animation_metadata(self.name)


@dataclass(slots=True)
class _AnimationEntry:
    animation: Animation
    frame_ms: int
    frames_per_cycle: int
    reactive: bool


@dataclass(slots=True)
class AnimationPlayer:
    animations: list[_AnimationEntry] = field(default_factory=list)
    index: int = 0
    frame: int = 0
    brightness: float = 1.0
    random_mode: bool = False
    audio_enabled: bool = True
    _rng: Random = field(default_factory=Random)
    _audio_snapshot: Callable[[], AudioFrame] | None = None

    def add(self, animation: Animation, frame_ms: int, frames_per_cycle: int) -> None:
        self.animations.append(_AnimationEntry(animation, frame_ms, frames_per_cycle, True))

    def add_utility(self, animation: Animation, frame_ms: int, frames_per_cycle: int) -> None:
        self.animations.append(_AnimationEntry(animation, frame_ms, frames_per_cycle, False))

    @classmethod
    def party(cls) -> AnimationPlayer:
        from .aurora import Aurora
        from .bass_drop import BassDrop
        from .beat_explosion import BeatExplosion
        from .beat_ripple import BeatRipple
        from .beat_tunnel import BeatTunnel
        from .beat_wave import BeatWave
        from .bouncing_ball import BouncingBall
        from .bpm import Bpm
        from .center_burst import CenterBurst
        from .club_flash import ClubFlash
        from .color_burst import ColorBurst
        from .color_wipe import ColorWipe
        from .comet import Comet
        from .comet_storm import CometStorm
        from .confetti import Confetti
        from .dance_floor import DanceFloor
        from .disco_comet import DiscoComet
        from .disco_sparkle import DiscoSparkle
        from .drop_explosion import DropExplosion
        from .drop_wave import DropWave
        from .dual_comet import DualComet
        from .dual_laser import DualLaser
        from .electric_storm import ElectricStorm
        from .fire import Fire
        from .firework_burst import FireworkBurst
        from .glow_rush import GlowRush
        from .hard_beat import HardBeat
        from .juggle import Juggle
        from .laser_sweep import LaserSweep
        from .lightning_strike import LightningStrike
        from .mirror_flash import MirrorFlash
        from .neon_confetti import NeonConfetti
        from .neon_storm import NeonStorm
        from .peak_mirror import PeakMirror
        from .pixel_explosion import PixelExplosion
        from .plasma_rave import PlasmaRave
        from .police import Police
        from .pulse import Pulse
        from .rave_scanner import RaveScanner
        from .rainbow import Rainbow
        from .rainbow_cycle import RainbowCycle
        from .rainbow_strobe import RainbowStrobe
        from .rave_pulse import RavePulse
        from .shockwave import Shockwave
        from .spectrum_flash import SpectrumFlash
        from .sinelon import Sinelon
        from .strobe_chase import StrobeChase
        from .strobe import Strobe
        from .theater_chase import TheaterChase
        from .twinkle import Twinkle
        from .wave import Wave

        player = cls()
        player.add(RainbowCycle(), 15, 300)
        player.add(Pulse(), 20, 180)
        player.add(Confetti(), 15, 220)
        player.add(Comet(), 16, 240)
        player.add(Shockwave(), 16, 180)
        player.add(TheaterChase(), 30, 220)
        player.add(Aurora(), 18, 260)
        player.add(ColorWipe(), 18, 220)
        player.add(Fire(), 20, 300)
        player.add(PeakMirror(), 18, 220)
        player.add(Wave(), 18, 240)
        player.add(Twinkle(), 15, 200)
        player.add(BouncingBall(), 20, 200)
        player.add(DualComet(), 16, 220)
        player.add(Rainbow(), 20, 200)
        player.add(Police(), 25, 160)
        player.add(Juggle(), 18, 240)
        player.add(Sinelon(), 18, 200)
        player.add(Strobe(), 12, 160)
        player.add(Bpm(), 20, 120)
        player.add(BeatWave(), 20, 200)
        player.add(DiscoSparkle(), 12, 160)
        player.add(BeatExplosion(), 16, 140)
        player.add(CometStorm(), 14, 180)
        player.add(LaserSweep(), 12, 140)
        player.add(PlasmaRave(), 18, 220)
        player.add(FireworkBurst(), 15, 160)
        player.add(LightningStrike(), 10, 100)
        player.add(BeatTunnel(), 18, 200)
        player.add(DropExplosion(), 20, 180)
        player.add(BassDrop(), 20, 200)
        player.add(RavePulse(), 14, 160)
        player.add(NeonStorm(), 12, 140)
        player.add(PixelExplosion(), 14, 140)
        player.add(DualLaser(), 14, 160)
        player.add(RainbowStrobe(), 10, 120)
        player.add(BeatRipple(), 18, 160)
        player.add(DanceFloor(), 18, 200)
        player.add(ElectricStorm(), 12, 120)
        player.add(GlowRush(), 16, 200)
        player.add(HardBeat(), 12, 100)
        player.add(ClubFlash(), 12, 120)
        player.add(ColorBurst(), 16, 180)
        player.add(DiscoComet(), 14, 180)
        player.add(RaveScanner(), 12, 140)
        player.add(NeonConfetti(), 12, 150)
        player.add(StrobeChase(), 12, 120)
        player.add(CenterBurst(), 16, 160)
        player.add(MirrorFlash(), 14, 140)
        player.add(SpectrumFlash(), 16, 180)
        player.add(DropWave(), 18, 180)
        return player

    def set_brightness(self, brightness: float) -> None:
        self.brightness = max(0.0, min(1.0, brightness))

    def set_random_mode(self, enabled: bool) -> None:
        self.random_mode = enabled

    def set_audio_snapshot(self, snapshot: Callable[[], AudioFrame]) -> None:
        self._audio_snapshot = snapshot

    def clear_audio_snapshot(self) -> None:
        self._audio_snapshot = None

    def step(self, controller: Controller) -> float:
        if not self.animations:
            return 0.05

        audio_frame = self._audio_snapshot() if (self._audio_snapshot is not None and self.audio_enabled) else None
        entry = self.animations[self.index]
        bright = BrightnessController(controller, self.brightness)
        if audio_frame is None:
            entry.animation.tick(self.frame, bright)
        else:
            entry.animation.tick_audio(self.frame, bright, audio_frame)
        controller.flush()
        self.frame += 1
        return entry.frame_ms / 1000.0

    def set_index(self, index: int) -> None:
        self.index = min(index, max(len(self.animations) - 1, 0))
        self.frame = 0

    def next(self) -> None:
        if not self.animations:
            return
        self.index = (self.index + 1) % len(self.animations)
        self.frame = 0

    def prev(self) -> None:
        if not self.animations:
            return
        self.index = len(self.animations) - 1 if self.index == 0 else self.index - 1
        self.frame = 0

    def current_index(self) -> int:
        return self.index

    def reactive_indices(self) -> list[int]:
        return [index for index, entry in enumerate(self.animations) if entry.reactive]

    def cycle_due(self) -> bool:
        if not self.animations:
            return False
        return self.frame >= self.animations[self.index].frames_per_cycle

    def cycle_next(self) -> None:
        if not self.animations:
            return
        self.frame = 0
        if self.random_mode and len(self.animations) > 1:
            current = self.index
            while self.index == current:
                self.index = self._rng.randrange(len(self.animations))
        else:
            self.index = (self.index + 1) % len(self.animations)

    def index_of(self, name: str) -> int | None:
        for index, entry in enumerate(self.animations):
            if entry.animation.name == name:
                return index
        return None

    def name_at(self, index: int) -> str | None:
        if 0 <= index < len(self.animations):
            return self.animations[index].animation.name
        return None
