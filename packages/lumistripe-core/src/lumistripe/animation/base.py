from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

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

    def reset(self) -> None:
        """Reset runtime state before this animation is activated again."""

    @property
    def metadata(self):
        from ..selector import animation_metadata

        return animation_metadata(self.name)


@dataclass(slots=True)
class _AnimationEntry:
    animation: Animation
    frame_ms: int
    frames_per_cycle: int
    automatic: bool

    def fresh_animation(self) -> Animation:
        animation_type: type[Animation] = type(self.animation)
        try:
            return animation_type()
        except TypeError:
            self.animation.reset()
            return self.animation

    def restart(self) -> None:
        self.animation = self.fresh_animation()


@dataclass(slots=True)
class AnimationPlayer:
    animations: list[_AnimationEntry] = field(default_factory=list)
    index: int = 0
    frame: int = 0
    brightness: float = 1.0
    audio_enabled: bool = True
    transition_ms: int = 0
    _audio_snapshot: Callable[[], AudioFrame] | None = None
    _transition_duration_ms: int = 0
    _transition_elapsed_ms: int = 0
    _transition_source: Any = None
    _transition_buffer: Any = None

    def add(self, animation: Animation, frame_ms: int, frames_per_cycle: int) -> None:
        self.animations.append(
            _AnimationEntry(animation, frame_ms, frames_per_cycle, True)
        )

    def add_utility(
        self, animation: Animation, frame_ms: int, frames_per_cycle: int
    ) -> None:
        self.animations.append(
            _AnimationEntry(animation, frame_ms, frames_per_cycle, False)
        )

    @classmethod
    def party(cls) -> AnimationPlayer:
        from ..effects import (
            BassDrop,
            BeatExplosion,
            BeatRipple,
            BeatTunnel,
            BeatWave,
            CenterBurst,
            ClubFlash,
            ColorBurst,
            Confetti,
            DropExplosion,
            DropWave,
            ElectricStorm,
            FireworkBurst,
            HardBeat,
            LightningStrike,
            MirrorFlash,
            PixelExplosion,
            Shockwave,
            SpectrumFlash,
        )
        from .aurora import Aurora
        from .bouncing_ball import BouncingBall
        from .bpm import Bpm
        from .color_wipe import ColorWipe
        from .comet import Comet
        from .comet_storm import CometStorm
        from .dance_floor import DanceFloor
        from .disco_comet import DiscoComet
        from .disco_sparkle import DiscoSparkle
        from .dual_comet import DualComet
        from .dual_laser import DualLaser
        from .fire import Fire
        from .glow_rush import GlowRush
        from .juggle import Juggle
        from .laser_sweep import LaserSweep
        from .neon_confetti import NeonConfetti
        from .neon_storm import NeonStorm
        from .peak_mirror import PeakMirror
        from .plasma_rave import PlasmaRave
        from .police import Police
        from .pulse import Pulse
        from .rainbow import Rainbow
        from .rainbow_cycle import RainbowCycle
        from .rainbow_strobe import RainbowStrobe
        from .rave_pulse import RavePulse
        from .rave_scanner import RaveScanner
        from .red_rave import RedBlackoutStrobe, RedRaveChase, RedRaveSweep
        from .sinelon import Sinelon
        from .strobe import Strobe
        from .strobe_chase import StrobeChase
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
        player.add(RedRaveSweep(), 16, 180)
        player.add(RedRaveChase(), 16, 160)
        player.add(RedBlackoutStrobe(), 24, 120)
        player.add(NeonConfetti(), 12, 150)
        player.add(StrobeChase(), 12, 120)
        player.add(CenterBurst(), 16, 160)
        player.add(MirrorFlash(), 14, 140)
        player.add(SpectrumFlash(), 16, 180)
        player.add(DropWave(), 18, 180)
        return player

    def set_brightness(self, brightness: float) -> None:
        self.brightness = max(0.0, min(1.0, brightness))

    def set_audio_snapshot(self, snapshot: Callable[[], AudioFrame]) -> None:
        self._audio_snapshot = snapshot

    def clear_audio_snapshot(self) -> None:
        self._audio_snapshot = None

    def step(
        self, controller: Controller, *, audio_frame: AudioFrame | None = None
    ) -> float:
        if not self.animations:
            return 0.05

        if (
            audio_frame is None
            and self._audio_snapshot is not None
            and self.audio_enabled
        ):
            audio_frame = self._audio_snapshot()
        if not self.audio_enabled:
            audio_frame = None
        entry = self.animations[self.index]
        target = controller
        if self.transition_active:
            from ..stripe import Stripe

            if (
                self._transition_source is None
                or self._transition_source.shape != controller.pixels().shape
            ):
                self._transition_source = controller.pixels().copy()
            if (
                self._transition_buffer is None
                or self._transition_buffer.length != controller.length
            ):
                self._transition_buffer = Stripe(controller.length)
            target = self._transition_buffer
        bright = BrightnessController(target, self.brightness)
        if audio_frame is None:
            entry.animation.tick(self.frame, bright)
        else:
            entry.animation.tick_audio(self.frame, bright, audio_frame)
        if self.transition_active:
            self._write_transition(controller, entry.frame_ms)
        else:
            controller.flush()
        self.frame += 1
        return entry.frame_ms / 1000.0

    @property
    def transition_active(self) -> bool:
        return self._transition_duration_ms > 0

    @property
    def transition_progress(self) -> float:
        if not self.transition_active:
            return 1.0
        return min(
            1.0,
            self._transition_elapsed_ms / max(self._transition_duration_ms, 1),
        )

    def set_index(
        self,
        index: int,
        *,
        transition_ms: int | None = None,
        restart: bool = True,
    ) -> None:
        next_index = min(max(index, 0), max(len(self.animations) - 1, 0))
        changed = next_index != self.index
        self.index = next_index
        self.frame = 0
        if self.animations and restart:
            self.animations[self.index].restart()
        duration = self.transition_ms if transition_ms is None else transition_ms
        if changed and duration > 0:
            self.begin_transition(duration_ms=duration)
        else:
            self.cancel_transition()

    def next(self) -> None:
        if not self.animations:
            return
        self.set_index((self.index + 1) % len(self.animations))

    def prev(self) -> None:
        if not self.animations:
            return
        self.set_index(len(self.animations) - 1 if self.index == 0 else self.index - 1)

    def begin_transition(self, *, duration_ms: int, source: Any = None) -> None:
        duration_ms = max(int(duration_ms), 0)
        if duration_ms == 0:
            self.cancel_transition()
            return
        self._transition_duration_ms = duration_ms
        self._transition_elapsed_ms = 0
        self._transition_source = (
            None if source is None else np.asarray(source, dtype=np.uint8).copy()
        )
        self._transition_buffer = None

    def cancel_transition(self) -> None:
        self._transition_duration_ms = 0
        self._transition_elapsed_ms = 0
        self._transition_source = None
        self._transition_buffer = None

    def fresh_animation(self, name: str) -> Animation | None:
        index = self.index_of(name)
        if index is None:
            return None
        return self.animations[index].fresh_animation()

    def _write_transition(self, controller: Controller, frame_ms: int) -> None:
        assert self._transition_source is not None
        assert self._transition_buffer is not None
        self._transition_elapsed_ms = min(
            self._transition_elapsed_ms + frame_ms,
            self._transition_duration_ms,
        )
        amount = self._transition_elapsed_ms / max(self._transition_duration_ms, 1)
        source = self._transition_source.astype(np.float32)
        target = self._transition_buffer.pixels().astype(np.float32)
        blended = np.clip(source * (1.0 - amount) + target * amount, 0.0, 255.0).astype(
            np.uint8
        )
        controller.set_pixels(blended)
        controller.flush()
        if amount >= 1.0:
            self.cancel_transition()

    def current_index(self) -> int:
        return self.index

    def automatic_indices(self) -> list[int]:
        return [index for index, entry in enumerate(self.animations) if entry.automatic]

    def index_of(self, name: str) -> int | None:
        for index, entry in enumerate(self.animations):
            if entry.animation.name == name:
                return index
        return None

    def name_at(self, index: int) -> str | None:
        if 0 <= index < len(self.animations):
            return self.animations[index].animation.name
        return None
