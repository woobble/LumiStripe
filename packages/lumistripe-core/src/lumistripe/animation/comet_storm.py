from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Hsla, Rgba
from ..controller import Controller
from ..render import native_comet_storm_available, render_comet_storm_into
from .base import Animation
from .reactive import AudioReactive


def _render_comet(controller: Controller, head: float, hue: float, length: float, alpha: float) -> None:
    for i in range(controller.length):
        direct = abs(head - i)
        dist = min(direct, max(controller.length - direct, 0.0))
        intensity = max(1.0 - dist / max(length, 0.001), 0.0)
        if intensity > 0.0:
            existing = controller.pixel(i)
            ea = existing.to_rgba()[3]
            strength = min(1.0, intensity * alpha)
            if strength >= ea:
                controller.set_pixel(i, Hsla(hue, 100, 55, strength))


@dataclass(slots=True)
class CometStorm(Animation):
    native_render_enabled = True
    phase: float = 0.0
    hue_phase: float = 0.0
    _native_geometry: npt.NDArray[np.float64] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def name(self) -> str:
        return "comet_storm"

    def tick(self, frame: int, controller: Controller) -> None:
        controller.fill(Rgba(0, 0, 0, 0.0))
        for head, tail, hue, alpha in self._static_comets(frame, controller.length):
            _render_comet(controller, head, hue, tail, alpha)

    def _static_comets(self, frame: int, length: int) -> tuple[tuple[float, float, float, float], ...]:
        n_comets = self.setting("count", 4)
        tail = max(1.0, length * self.setting("tail_length", 8) / 100.0)
        speed = 2.0 * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        return tuple(
            (
                float((frame * speed + c * max(length, 1) // max(n_comets, 1)) % max(length, 1)),
                tail,
                float((c * 51 + frame * 3) % 256),
                0.7,
            )
            for c in range(n_comets)
        )

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        controller.fill(Rgba(0, 0, 0, 0.0))
        for head, tail, hue, alpha in self._audio_comets(frame, controller.length, audio):
            _render_comet(controller, head, hue, tail, alpha)

    def _audio_comets(
        self, frame: int, length: int, audio: AudioFrame
    ) -> tuple[tuple[float, float, float, float], ...]:
        reactive = AudioReactive.from_frame(audio)
        base_count = self.setting("count", 4)
        n_comets = max(1, min(12, base_count + int(reactive.shimmer() * 2.0)))
        speed = reactive.speed(0.8, 3.5) * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        self.phase = (self.phase + speed) % max(length, 1)
        self.hue_phase = (self.hue_phase + 0.5 + reactive.high * 1.4) % 360.0
        comets = []
        for c in range(n_comets):
            seed = c * 1103515245 + (frame // 30) * 12345
            offset = (seed % 200) + seed // 100 * 7
            head = (self.phase + offset) % max(length, 1)
            hue = int(self.hue_phase + c * 40) % 360
            tail = max(1.0, length * self.setting("tail_length", 8) / 100.0) * (0.75 + reactive.high * 0.5)
            alph = 0.4 + reactive.pulse(0.0, 0.4)
            comets.append((head, tail, float(hue), alph))
        return tuple(comets)

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_comet_storm_available():
            return False
        length = destination.shape[0]
        comets = self._static_comets(frame, length) if audio is None else self._audio_comets(frame, length, audio)
        if self._native_geometry is None or self._native_geometry.shape != (len(comets), 4):
            self._native_geometry = np.empty((len(comets), 4), dtype=np.float64)
        for index, comet in enumerate(comets):
            self._native_geometry[index] = comet
        configuration = getattr(self, "_animation_configuration", {})
        palette_name = str(configuration.get("palette", "rainbow"))
        palette = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette_name)
        if palette is None:
            return False
        custom = str(configuration.get("custom_color", "#7C3AED"))
        try:
            custom_rgb = int(custom[1:], 16)
        except (ValueError, IndexError):
            custom_rgb = 0x7C3AED
        output_brightness = max(0.0, min(1.0, float(self._output_brightness(brightness))))
        assert self._native_geometry is not None
        return render_comet_storm_into(
            destination, self._native_geometry, output_brightness, palette, custom_rgb
        )
