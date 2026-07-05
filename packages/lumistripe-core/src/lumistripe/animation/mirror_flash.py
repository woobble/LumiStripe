from __future__ import annotations

from dataclasses import dataclass, field

from ..audio import AudioFrame
from ..controller import Controller
from .base import Animation
from .club_utils import mirrored_index, club_color, warm_flash
from .reactive import AudioReactive, Decay


@dataclass(slots=True)
class MirrorFlash(Animation):
    flash: Decay = field(default_factory=Decay)
    alternate: int = 0
    hue_seed: int = 0

    @property
    def name(self) -> str:
        return "mirror_flash"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(AudioFrame(rms=0.16, bands=(0.16, 0.14, 0.14, 0.16, 0.14, 0.12, 0.1, 0.08)))
        self._step(frame, controller, reactive, beat=frame % 17 == 0)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(frame, controller, reactive, beat=audio.beat)

    def _step(self, frame: int, controller: Controller, reactive: AudioReactive, *, beat: bool) -> None:
        length = max(controller.length, 1)
        if beat and (reactive.low > 0.2 or reactive.accent > 0.42 or reactive.rms > 0.4):
            self.flash.value = min(1.0, 0.34 + reactive.accent * 0.66)
            self.hue_seed = frame * 31 + int(reactive.mid * 60.0)
            self.alternate = 1 - self.alternate

        flash = self.flash.step(0.0, 0.1)
        center = (length - 1) / 2.0
        span = max(2.0, center * (0.3 + reactive.drive() * 0.7))
        controller.clear()
        for index in range((length + 1) // 2):
            distance = abs(index - center)
            intensity = max(0.0, 1.0 - distance / span) * flash
            if intensity <= 0.0:
                continue
            hue = self.hue_seed + (index * 13 if self.alternate else -index * 9)
            color = club_color(hue, alpha=min(1.0, 0.14 + intensity * 0.86), lightness=62)
            controller.set_pixel(index, color)
            mirror = mirrored_index(index, length)
            if mirror != index:
                controller.set_pixel(mirror, color if self.alternate == 0 else warm_flash(hue + 11, alpha=min(1.0, 0.14 + intensity * 0.86)))
