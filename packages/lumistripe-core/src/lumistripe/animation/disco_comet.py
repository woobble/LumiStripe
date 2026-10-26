from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..controller import Controller
from ..render import native_disco_comet_available, render_disco_comet_into
from .base import Animation
from .club_utils import club_color, neon_color
from .reactive import AudioReactive


@dataclass(slots=True)
class DiscoComet(Animation):
    native_render_enabled = True

    phase: float = 0.0
    direction: float = 1.0

    @property
    def name(self) -> str:
        return "disco_comet"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(AudioFrame(rms=0.18, bands=(0.08, 0.12, 0.16, 0.18, 0.14, 0.16, 0.18, 0.2)))
        self._step(frame, controller, reactive, beat=frame % 21 == 0)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(frame, controller, reactive, beat=audio.beat)

    def _step(self, frame: int, controller: Controller, reactive: AudioReactive, *, beat: bool) -> None:
        length = max(controller.length, 1)
        if beat:
            self.direction *= -1.0
        travel = self.setting("direction", "bounce")
        speed = reactive.speed(0.45, 1.65) * (0.25 + self.setting("speed", 50) / 100.0 * 1.5)
        speed += reactive.rms * 0.35 + reactive.mid * 0.15
        self.phase += self.direction * max(0.3, speed)
        max_pos = float(length - 1)
        if travel == "forward":
            self.phase %= max(length, 1)
            self.direction = 1.0
        elif travel == "reverse":
            self.phase %= max(length, 1)
            self.direction = -1.0
        elif self.phase <= 0.0 or self.phase >= max_pos:
            self.direction *= -1.0
            self.phase = min(max(self.phase, 0.0), max_pos)

        width = max(1.0, length * self.setting("tail_length", 8) / 100.0)
        trail = 0.15 + reactive.drive() * 0.25
        sparkle_prob = 0.08 + reactive.high * 0.32 + reactive.accent * 0.18
        hue = reactive.hue_shift(frame, 0.55)

        controller.clear()
        for index in range(length):
            distance = abs(index - self.phase)
            if distance <= width:
                intensity = max(0.0, 1.0 - distance / width)
                alpha = min(1.0, trail + intensity * 0.85)
                controller.set_pixel(index, club_color(hue + index * 5, alpha=alpha))
                sparkle_seed = frame * 97 + index * 31 + int(reactive.high * 100.0)
                if distance < width * 0.8 and (sparkle_seed % 100) < int(sparkle_prob * 100.0):
                    controller.set_pixel(index, neon_color(hue + sparkle_seed, alpha=min(1.0, 0.65 + reactive.high * 0.35)))
                    if index + 1 < length and (sparkle_seed // 7) % 2 == 0:
                        controller.set_pixel(index + 1, neon_color(hue + sparkle_seed + 13, alpha=min(1.0, 0.42 + reactive.high * 0.24)))

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_disco_comet_available():
            return False
        reactive = AudioReactive.from_frame(
            audio
            if audio is not None
            else AudioFrame(
                rms=0.18,
                bands=(0.08, 0.12, 0.16, 0.18, 0.14, 0.16, 0.18, 0.2),
            )
        )
        beat = frame % 21 == 0 if audio is None else audio.beat
        length = max(int(destination.shape[0]), 1)
        if beat:
            self.direction *= -1.0
        travel = str(self.setting("direction", "bounce"))
        speed = reactive.speed(0.45, 1.65) * (
            0.25 + float(self.setting("speed", 50)) / 100.0 * 1.5
        )
        speed += reactive.rms * 0.35 + reactive.mid * 0.15
        self.phase += self.direction * max(0.3, speed)
        max_pos = float(length - 1)
        if travel == "forward":
            self.phase %= max(length, 1)
            self.direction = 1.0
        elif travel == "reverse":
            self.phase %= max(length, 1)
            self.direction = -1.0
        elif self.phase <= 0.0 or self.phase >= max_pos:
            self.direction *= -1.0
            self.phase = min(max(self.phase, 0.0), max_pos)
        width = max(1.0, length * float(self.setting("tail_length", 8)) / 100.0)
        trail = 0.15 + reactive.drive() * 0.25
        sparkle_probability = (
            0.08 + reactive.high * 0.32 + reactive.accent * 0.18
        )
        sparkle_threshold = int(sparkle_probability * 100.0)
        hue = reactive.hue_shift(frame, 0.55)
        return render_disco_comet_into(
            destination,
            self.phase,
            width,
            trail,
            hue,
            reactive.high,
            sparkle_threshold,
            frame,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
