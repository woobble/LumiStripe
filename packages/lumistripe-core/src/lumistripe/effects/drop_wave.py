from __future__ import annotations

from dataclasses import dataclass, field

from ..animation.club_utils import burst_profile, club_color, warm_flash
from ..animation.reactive import AudioReactive, Decay
from ..audio import AudioFrame
from ..buffers import PixelBuffer
from ..controller import Controller
from ..render import native_drop_wave_available, render_drop_wave_into
from .base import Effect


@dataclass(slots=True)
class DropWave(Effect):
    native_render_enabled = True

    charge: float = 0.0
    radius: float = 0.0
    speed: float = 0.0
    hue_seed: int = 0
    wave: Decay = field(default_factory=Decay)

    @property
    def name(self) -> str:
        return "drop_wave"

    def tick(self, frame: int, controller: Controller) -> None:
        reactive = AudioReactive.from_frame(
            AudioFrame(rms=0.16, bands=(0.24, 0.22, 0.18, 0.16, 0.14, 0.12, 0.1, 0.08))
        )
        self._step(frame, controller, reactive, beat=frame % 40 == 0)

    def tick_audio(self, frame: int, controller: Controller, audio: AudioFrame) -> None:
        reactive = AudioReactive.from_frame(audio)
        self._step(frame, controller, reactive, beat=audio.beat)

    def _step(
        self, frame: int, controller: Controller, reactive: AudioReactive, *, beat: bool
    ) -> None:
        length = max(controller.length, 1)
        self.charge = min(
            1.0, self.charge + 0.006 + reactive.low * 0.018 + reactive.mid * 0.01
        )
        speed_scale = 0.25 + self.setting("speed", 50) / 100.0 * 1.5
        width = max(1.0, length * self.setting("width", 30) / 100.0 * 0.12)
        decay = self.setting("decay", 50) / 100.0

        drop_hit = reactive.drop_hit(beat=beat)
        if drop_hit:
            self.wave.value = 1.0
            self.radius = 0.0
            self.speed = (0.75 + reactive.accent * 2.4) * speed_scale
            self.hue_seed = frame * 19 + int(reactive.low * 120.0)
            self.charge = 0.0
        elif self.charge > 0.78 and frame % 7 == 0:
            self.wave.value = max(self.wave.value, 0.45)
            self.speed = max(self.speed, (0.55 + reactive.drive() * 1.2) * speed_scale)

        self.radius += self.speed
        flash = self.wave.step(0.0, 0.02 + decay * 0.1)
        center = (length - 1) / 2.0
        controller.clear()

        for index in range(length):
            distance = abs(index - center)
            if flash > 0.0:
                intensity = burst_profile(
                    distance, self.radius, width + self.charge * width
                )
                if intensity > 0.0:
                    alpha = min(1.0, intensity * (0.22 + flash * 0.78))
                    controller.set_pixel(
                        index, warm_flash(self.hue_seed + index, alpha=alpha)
                    )
            elif self.charge > 0.35:
                pre = max(0.0, 1.0 - distance / max(center, 1.0))
                alpha = min(1.0, pre * (0.08 + self.charge * 0.2))
                if alpha > 0.0:
                    controller.set_pixel(
                        index,
                        club_color(self.hue_seed + index, alpha=alpha, lightness=48),
                    )

    def _render_native(
        self,
        frame: int,
        destination: PixelBuffer,
        audio: AudioFrame | None,
        brightness: float,
    ) -> bool:
        if not native_drop_wave_available():
            return False
        reactive = AudioReactive.from_frame(
            audio
            if audio is not None
            else AudioFrame(
                rms=0.16,
                bands=(0.24, 0.22, 0.18, 0.16, 0.14, 0.12, 0.1, 0.08),
            )
        )
        beat = frame % 40 == 0 if audio is None else audio.beat
        length = max(int(destination.shape[0]), 1)
        self.charge = min(
            1.0,
            self.charge + 0.006 + reactive.low * 0.018 + reactive.mid * 0.01,
        )
        speed_scale = 0.25 + float(self.setting("speed", 50)) / 100.0 * 1.5
        width = max(
            1.0,
            length * float(self.setting("width", 30)) / 100.0 * 0.12,
        )
        decay = float(self.setting("decay", 50)) / 100.0
        if reactive.drop_hit(beat=beat):
            self.wave.value = 1.0
            self.radius = 0.0
            self.speed = (0.75 + reactive.accent * 2.4) * speed_scale
            self.hue_seed = frame * 19 + int(reactive.low * 120.0)
            self.charge = 0.0
        elif self.charge > 0.78 and frame % 7 == 0:
            self.wave.value = max(self.wave.value, 0.45)
            self.speed = max(
                self.speed,
                (0.55 + reactive.drive() * 1.2) * speed_scale,
            )
        self.radius += self.speed
        flash = self.wave.step(0.0, 0.02 + decay * 0.1)
        return render_drop_wave_into(
            destination,
            self.hue_seed,
            (length - 1) / 2.0,
            self.radius,
            width,
            self.charge,
            flash,
            max(0.0, min(1.0, float(self._output_brightness(brightness)))),
            str(self.setting("palette", "rainbow")),
            str(self.setting("custom_color", "#7C3AED")),
        )
