from __future__ import annotations

from dataclasses import dataclass, field

from ..animation.club_utils import center_distance, ring_profile
from ..animation.reactive import AudioReactive, Decay
from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla
from ..controller import Controller
from ..render import native_shockwave_available, render_shockwave_into
from .base import Effect


@dataclass(slots=True)
class Shockwave(Effect):
    native_render_enabled = True

    radius: float = 0.0
    accent: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "shockwave"

    def tick(self, frame: int, controller: Controller) -> None:
        center = max(controller.length - 1, 0) * 0.5
        speed = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.14)
        self.radius = (frame * 0.32 * speed) % (center + 6.0)
        for i in range(controller.length):
            dist = center_distance(i, controller.length)
            ring = ring_profile(dist, self.radius, width)
            ambient = max(1.0 - dist / max(center, 1.0), 0.0) * 0.12
            controller.set_pixel(i, Hsla(188, 100, 58, min(ambient + ring * 0.88, 1.0)))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        center = max(controller.length - 1, 0) * 0.5
        reactive = AudioReactive.from_frame(audio)
        speed = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.14)
        self.accent.step(reactive.accent, 0.04 + reactive.high * 0.04)
        if audio.beat:
            self.radius = 0.0
        else:
            self.radius += reactive.speed(0.48, 1.7) * speed
            if self.radius > center + 8.0:
                self.radius = 0.0
        for i in range(controller.length):
            dist = center_distance(i, controller.length)
            band = reactive.band_window(audio, i, controller.length, span=1)
            ring = ring_profile(dist, self.radius, width + reactive.high * 2.0)
            ambient = max(1.0 - dist / max(center, 1.0), 0.0) * 0.12
            alpha = min(ambient + ring * 0.88 + self.accent.value * 0.05, 1.0)
            hue = (170 + int(reactive.low * 18.0) - int(reactive.high * 10.0)) % 256
            light = min(
                int(52.0 + reactive.mid * 10.0 + band * 8.0 + self.accent.value * 4.0),
                68,
            )
            controller.set_pixel(i, reactive.accent_color(hue, 95, light, alpha))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_shockwave_available():
            return False
        length = destination.shape[0]
        center = max(length - 1, 0) * 0.5
        speed = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, length * self.setting("width", 30) / 100.0 * 0.14)
        bands = None
        accent = mid = 0.0
        if audio is None:
            self.radius = (frame * 0.32 * speed) % (center + 6.0)
            hue = 188
        else:
            reactive = AudioReactive.from_frame(audio)
            self.accent.step(reactive.accent, 0.04 + reactive.high * 0.04)
            if audio.beat:
                self.radius = 0.0
            else:
                self.radius += reactive.speed(0.48, 1.7) * speed
                if self.radius > center + 8.0:
                    self.radius = 0.0
            width += reactive.high * 2.0
            accent, mid = self.accent.value, reactive.mid
            hue = (170 + int(reactive.low * 18.0) - int(reactive.high * 10.0)) % 256
            bands = audio.bands
        return render_shockwave_into(
            destination,
            self.radius,
            width,
            float(accent),
            hue,
            float(mid),
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            bands,
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
