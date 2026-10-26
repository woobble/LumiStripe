from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsl, Hsla
from ..controller import Controller
from ..render import native_rainbow_pattern_available, render_rainbow_pattern_into
from .base import Animation
from .club_utils import strip_ratio
from .reactive import AudioReactive


@dataclass(slots=True)
class Rainbow(Animation):
    native_render_enabled = True

    _beat_flash: float = 0.0

    @property
    def name(self) -> str:
        return "rainbow"

    def tick(self, frame: int, controller: Controller) -> None:
        spread = 0.35 + self.setting("color_spread", 50) / 100.0 * 1.3
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            hue = int(pos * 256.0 * spread + frame * 1.5) % 256
            controller.set_pixel(i, Hsl(hue, 100, 50))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        if audio.beat:
            self._beat_flash = 1.0
        self._beat_flash *= 0.88

        brightness = 0.42 + reactive.rms * 0.58
        sat = int(62.0 + reactive.low * 34.0)
        offset = reactive.hue_shift(frame, 0.15)
        spread = 0.35 + self.setting("color_spread", 50) / 100.0 * 1.3
        for i in range(controller.length):
            pos = strip_ratio(i, controller.length)
            local = reactive.band_window(audio, i, controller.length, span=1)
            hue = (int(pos * 256.0 * spread) + offset + int(local * 44.0)) % 256
            alpha = min(brightness + self._beat_flash * 0.4, 1.0)
            light = 48 + int(reactive.mid * 6.0 + local * 6.0)
            controller.set_pixel(i, Hsla(hue, sat, light, alpha))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_rainbow_pattern_available():
            return False
        spread = 0.35 + float(self.setting("color_spread", 50)) / 100.0 * 1.3
        if audio is None:
            alpha, saturation, base_hue, low, mid = 1.0, 100.0, 0.0, 0.0, 0.0
            bands = None
        else:
            reactive = AudioReactive.from_frame(audio)
            if audio.beat:
                self._beat_flash = 1.0
            self._beat_flash *= 0.88
            alpha = min(0.42 + reactive.rms * 0.58 + self._beat_flash * 0.4, 1.0)
            saturation = float(int(62.0 + reactive.low * 34.0))
            base_hue = float(reactive.hue_shift(frame, 0.15))
            low, mid = reactive.low, reactive.mid
            bands = audio.bands
        return render_rainbow_pattern_into(
            destination,
            0,
            frame,
            bands,
            spread,
            base_hue,
            saturation,
            alpha,
            low,
            mid,
            0.0,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
