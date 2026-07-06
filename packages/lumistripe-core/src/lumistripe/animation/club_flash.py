from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .club_utils import club_color, warm_flash
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class ClubFlash(Animation):
    flash: Decay = field(default_factory=Decay)
    segment_start: int = 0
    segment_width: int = 0
    whole_strip: bool = False
    hue_seed: int = 0

    @property
    def name(self) -> str:
        return "club_flash"

    def tick(self, frame: int, controller: Controller) -> None:
        drive = 0.18 + ((frame % 13) / 13.0) * 0.28
        low = 0.08 + ((frame % 9) / 9.0) * 0.18
        self._step(frame, controller, drive=drive, low=low, high=0.12, accent=0.0, beat=frame % 11 == 0)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(
            frame,
            controller,
            drive=reactive.drive(),
            low=reactive.low,
            high=reactive.high,
            accent=reactive.accent,
            beat=audio.beat,
        )

    def _step(
        self,
        frame: int,
        controller: Controller,
        *,
        drive: float,
        low: float,
        high: float,
        accent: float,
        beat: bool,
        ) -> None:
        length = max(controller.length, 1)
        seed = frame * 131 + length * 17 + int(drive * 100.0) + int(low * 80.0)
        strong_hit = (beat and low > 0.24) or accent > 0.74 or low > 0.78 or drive > 0.82
        trigger_prob = 0.03 + drive * 0.08 + accent * 0.07 + low * 0.05

        if strong_hit or (seed % 1000) < int(trigger_prob * 1000.0):
            strength = min(1.0, 0.18 + low * 0.34 + accent * 0.52 + drive * 0.24)
            self.flash.step(strength, 0.08)
            self.hue_seed = seed
            self.whole_strip = strong_hit and ((seed // 7) % 3 != 0 or strength > 0.72)
            max_width = max(3, length // 2)
            self.segment_width = max(2, min(max_width, 2 + int(strength * max_width)))
            self.segment_start = seed % length
        else:
            self.flash.step(0.0, 0.08)

        brightness = self.flash.step(self.flash.value, 0.0)
        if brightness <= 0.01:
            controller.clear()
            return

        if self.whole_strip:
            controller.fill(warm_flash(self.hue_seed, alpha=min(1.0, 0.2 + brightness * 0.8)))
            return

        controller.clear()
        color = club_color(self.hue_seed, alpha=min(1.0, 0.12 + brightness * 0.88))
        for offset in range(self.segment_width):
            index = (self.segment_start + offset) % length
            controller.set_pixel(index, color)
            if brightness > 0.6 and length > 6:
                mirror = max(length - 1 - index, 0)
                controller.set_pixel(mirror, warm_flash(self.hue_seed + offset, alpha=min(1.0, 0.1 + brightness * 0.7)))
