from __future__ import annotations

from dataclasses import dataclass, field
from math import sin

from ..audio import AudioFrame
from ..color import Hsl, Rgba
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
        half = controller.length // 2
        for i in range(controller.length):
            mirrored = min(i, max(controller.length - 1 - i, 0))
            band = mirrored * 8 // max(half, 1)
            phase = sin(frame * 0.08 + band * 0.7) * 0.5 + 0.5
            limit = int(phase * half / 8.0)
            dist = mirrored - band * max(half, 1) // 8
            if dist <= limit:
                controller.set_pixel(i, Rgba((band * 28) % 256, 255, 220, 0.8))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        half = controller.length // 2
        reactive = AudioReactive.from_frame(audio)
        decay_rate = 0.15 + reactive.high * 0.10
        raw = [peak.step(audio.bands[band], decay_rate) for band, peak in enumerate(self.peaks)]
        if audio.beat:
            self._center_flash = reactive.accent
            raw = [min(v + reactive.accent * 0.4, 1.0) for v in raw]
        self._center_flash *= 0.92

        hue_shift = reactive.hue_shift(frame, 0.4)
        for i in range(controller.length):
            mirrored = min(i, max(controller.length - 1 - i, 0))
            band = mirrored * 8 // max(half, 1)
            band_start = band * max(half, 1) // 8
            band_width = max(max(half, 1) // 8, 1)
            within = mirrored - band_start
            fill = int(raw[band] * band_width)
            base = audio.bands[band] * 0.45 + reactive.accent * 0.25
            if within < fill:
                hue = (hue_shift + band * 24) % 256
                controller.set_pixel(i, Hsl(hue, 100, 55))
            elif within == min(fill, band_width - 1):
                controller.set_pixel(i, Rgba(255, 255, 255, min(0.3 + base, 1.0)))
            elif mirrored <= 2 and self._center_flash > 0.0:
                controller.set_pixel(i, Rgba(255, 255, 255, self._center_flash))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))
