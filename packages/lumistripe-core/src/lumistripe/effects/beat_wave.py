from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..animation.reactive import AudioReactive
from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..color import Rgba
from ..controller import Controller
from ..render import native_beat_wave_available, render_beat_wave_into
from .base import Effect


@dataclass(slots=True)
class _Wave:
    pos: float = 0.0
    speed: float = 1.0
    strength: float = 1.0
    hue: int = 0


@dataclass(slots=True)
class BeatWave(Effect):
    native_render_enabled = True
    waves: list[_Wave] = field(default_factory=list)
    _hue: int = 0
    _native_waves: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._native_waves = np.zeros((8, 2), dtype=np.float64)

    @property
    def name(self) -> str:
        return "beat_wave"

    def tick(self, frame: int, controller: Controller) -> None:
        for i in range(controller.length):
            controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, controller.length * self.setting("width", 30) / 100.0 * 0.14)
        if audio.beat:
            self._hue = (self._hue + 17) % 256
            wave = _Wave(
                pos=0.0,
                speed=(0.5 + reactive.speed(0.1, 0.5) * (1.0 + reactive.accent * 0.5)) * speed_scale,
                strength=0.6 + reactive.accent * 0.4,
                hue=self._hue,
            )
            self.waves.append(wave)
            self.waves = self.waves[-8:]

        for w in self.waves:
            w.pos += w.speed
            w.strength *= 0.98
        self.waves = [
            w for w in self.waves if w.strength > 0.01 and w.pos < controller.length
        ]

        glow = 0.04 + reactive.rms * 0.12
        hue = reactive.hue_shift(frame, 0.25)
        for i in range(controller.length):
            combined = glow
            for w in self.waves:
                dist = abs(i - w.pos)
                if dist < width:
                    wave_alpha = w.strength * (1.0 - dist / width)
                    combined = min(combined + wave_alpha, 1.0)
            if combined > 0.0:
                controller.set_pixel(i, reactive.vivid(hue, combined))
            else:
                controller.set_pixel(i, Rgba(0, 0, 0, 0.0))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_beat_wave_available():
            return False
        length = int(destination.shape[0])
        width = max(1.0, length * self.setting("width", 30) / 100.0 * 0.14)
        wave_count = 0
        hue = 0
        glow = 0.0
        if audio is not None:
            reactive = AudioReactive.from_frame(audio)
            speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
            if audio.beat:
                self._hue = (self._hue + 17) % 256
                self.waves.append(
                    _Wave(
                        pos=0.0,
                        speed=(
                            0.5
                            + reactive.speed(0.1, 0.5)
                            * (1.0 + reactive.accent * 0.5)
                        )
                        * speed_scale,
                        strength=0.6 + reactive.accent * 0.4,
                        hue=self._hue,
                    )
                )
                self.waves = self.waves[-8:]
            for wave in self.waves:
                wave.pos += wave.speed
                wave.strength *= 0.98
            self.waves = [
                wave
                for wave in self.waves
                if wave.strength > 0.01 and wave.pos < length
            ]
            wave_count = len(self.waves)
            for index, wave in enumerate(self.waves):
                self._native_waves[index, 0] = wave.pos
                self._native_waves[index, 1] = wave.strength
            hue = reactive.hue_shift(frame, 0.25)
            glow = 0.04 + reactive.rms * 0.12
        return render_beat_wave_into(
            destination,
            self._native_waves,
            wave_count,
            hue,
            float(width),
            float(glow),
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
