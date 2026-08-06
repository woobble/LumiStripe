from __future__ import annotations

from dataclasses import dataclass, field
from math import sin

from ..audio import AudioFrame
from ..color import Hsla, Rgba
from ..controller import Controller
from .base import Animation
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class PeakMirror(Animation):
    peaks: list[Decay] = field(default_factory=lambda: [Decay() for _ in range(8)])
    _center_flash: float = 0.0

    @property
    def name(self) -> str:
        return "peak_mirror"

    def tick(self, frame: int, controller: Controller) -> None:
        levels = [
            0.14 + (sin(frame * 0.08 + band * 0.7) * 0.5 + 0.5) * 0.38
            for band in range(8)
        ]
        self._render(controller, levels, hue_shift=int(frame * 0.4) % 360)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        decay_rate = 0.15 + reactive.high * 0.10
        raw = [
            peak.step(audio.bands[band], decay_rate)
            for band, peak in enumerate(self.peaks)
        ]
        if audio.beat:
            self._center_flash = max(self._center_flash, reactive.beat_pulse(0.0, 0.95))
            raw = [min(v + reactive.accent * 0.45, 1.0) for v in raw]

        self._render(controller, raw, hue_shift=reactive.hue_shift(frame, 0.4) % 360)
        self._center_flash *= 0.92

    def _render(
        self, controller: Controller, levels: list[float], *, hue_shift: int
    ) -> None:
        length = controller.length
        if length == 0:
            return

        mirror_span = max((length - 1) // 2, 1)
        center = (length - 1) / 2.0
        for i in range(length):
            mirrored = min(i, length - 1 - i)
            position = 1.0 if length == 1 else min(mirrored / mirror_span, 1.0)
            band_position = position * (len(levels) - 1)
            lower_band = int(band_position)
            upper_band = min(lower_band + 1, len(levels) - 1)
            blend = band_position - lower_band
            level = levels[lower_band] * (1.0 - blend) + levels[upper_band] * blend

            center_glow = max(0.0, 1.0 - abs(i - center) / 2.5)
            beat_glow = self._center_flash * center_glow
            level = max(level, beat_glow)
            if level <= 0.015:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
                continue

            hue = (hue_shift + int(band_position * 28.0)) % 360
            lightness = min(48.0 + level * 12.0 + beat_glow * 6.0, 66.0)
            alpha = min(0.08 + level * 0.9, 1.0)
            controller.set_pixel(i, Hsla(hue, 100, lightness, alpha))
