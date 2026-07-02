from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class _Wave:
    pos: float = 0.0
    speed: float = 1.0
    strength: float = 1.0
    hue: int = 0


@dataclass(slots=True)
class BeatWave(Animation):
    waves: list[_Wave] = field(default_factory=list)
    _hue: int = 0

    @property
    def name(self) -> str:
        return "beat_wave"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._hue = (self._hue + 17) % 256
            wave = _Wave(
                pos=0.0,
                speed=0.5 + reactive.speed(0.1, 0.5) * (1.0 + reactive.accent * 0.5),
                strength=0.6 + reactive.accent * 0.4,
                hue=self._hue,
            )
            self.waves.append(wave)

        for w in self.waves:
            w.pos += w.speed
            w.strength *= 0.98
        self.waves = [w for w in self.waves if w.strength > 0.01 and w.pos < controller.length]

        glow = 0.04 + reactive.rms * 0.12
        hue = reactive.hue_shift(frame, 0.25)
        for i in range(controller.length):
            combined = glow
            for w in self.waves:
                dist = abs(i - w.pos)
                if dist < 5.0:
                    wave_alpha = w.strength * (1.0 - dist / 5.0)
                    combined = min(combined + wave_alpha, 1.0)
            if combined > 0.0:
                controller.set_pixel(i, reactive.vivid(hue, combined))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
