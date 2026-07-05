from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .club_utils import neon_color
from .reactive import AudioReactive


@dataclass(slots=True)
class NeonConfetti(Animation):
    @property
    def name(self) -> str:
        return "neon_confetti"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(AudioFrame(rms=0.16, bands=(0.1, 0.12, 0.12, 0.14, 0.12, 0.16, 0.18, 0.2)))
        self._render(frame, controller, reactive)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._render(frame, controller, reactive)

    def _render(self, frame: int, controller: Controller, reactive: AudioReactive) -> None:
        density = 0.12 + reactive.rms * 0.38 + reactive.high * 0.28 + reactive.accent * 0.12
        sparkle_threshold = int((0.2 + reactive.high * 0.6) * 100.0)
        controller.clear()
        for index in range(controller.length):
            seed = frame * 97 + index * 53 + int(reactive.rms * 1000.0)
            lit = (seed % 1000) < int(density * 1000.0) or (seed % 100) < sparkle_threshold
            if lit:
                alpha = min(1.0, 0.42 + reactive.rms * 0.38 + reactive.high * 0.2)
                controller.set_pixel(index, neon_color(seed + index, alpha=alpha, lightness=64))
                if (seed // 7) % 3 == 0 and index + 1 < controller.length:
                    controller.set_pixel(index + 1, neon_color(seed + 19, alpha=min(1.0, alpha * 0.75), lightness=66))
