from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive


@dataclass(slots=True)
class _Burst:
    phase: float = 0.0
    speed: float = 1.0
    strength: float = 1.0
    hue: int = 0


@dataclass(slots=True)
class FireworkBurst(Animation):
    bursts: list[_Burst] = field(default_factory=list)
    _hue: int = 0

    @property
    def name(self) -> str:
        return "firework_burst"

    def tick(self, frame: int, controller: Controller) -> None:
        center = max(controller.length - 1, 0) * 0.5
        phase = (frame * 0.25) % (center + 4.0)
        burst_id = int(frame / 36)
        seed = burst_id * 1103515245 + 12345
        hue = ((seed // 100) % 256 + frame * 2) % 256
        for i in range(controller.length):
            dist = abs(i - center)
            if dist <= phase:
                sparkle_dist = (phase - dist) / max(phase, 1.0)
                alpha = max(1.0 - sparkle_dist * 1.5, 0.0)
                offset = int(i * 7 + burst_id * 13) % 5
                variance = (offset - 2) * 0.04
                alpha = max(alpha + variance, 0.0)
                controller.set_pixel(i, Hsla(hue, 100, 60, alpha * 0.85))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        center = max(controller.length - 1, 0) * 0.5

        if audio.beat:
            self._hue = (self._hue + 41) % 256
            self.bursts.append(_Burst(
                phase=0.5,
                speed=0.5 + reactive.speed(0.15, 2.5) * (0.8 + reactive.accent * 0.4),
                strength=0.7 + reactive.accent * 0.3,
                hue=self._hue,
            ))

        for b in self.bursts:
            b.phase += b.speed
            b.strength *= 0.97
        self.bursts = [b for b in self.bursts if b.strength > 0.02 and b.phase < center + 6.0]

        bg_glow = reactive.rms * 0.08
        for i in range(controller.length):
            dist = abs(i - center)
            pixel_alpha = bg_glow
            pixel_hue = reactive.hue_shift(frame, 0.25)
            for b in self.bursts:
                if dist <= b.phase:
                    sparkle_dist = (b.phase - dist) / max(b.phase, 1.0)
                    wave_alpha = max(1.0 - sparkle_dist * 1.8, 0.0) * b.strength
                    particle = int(i * 7 + int(b.phase * 10) * 13) % 7
                    variance = (particle - 3) * 0.06
                    wave_alpha = max(wave_alpha + variance, 0.0)
                    band_sparkle = reactive.band_at(audio, i, controller.length) * 0.2
                    pixel_alpha = min(pixel_alpha + wave_alpha + band_sparkle, 1.0)
                    if wave_alpha > 0:
                        pixel_hue = b.hue
            if pixel_alpha > 0.0:
                lightness = int(45 + reactive.mid * 20 + reactive.high * 10)
                controller.set_pixel(i, Hsla(pixel_hue, 100, lightness, pixel_alpha))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
