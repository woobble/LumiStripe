from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame
from ..color import Hsla


@dataclass(slots=True)
class AudioReactive:
    rms: float
    accent: float
    low: float
    mid: float
    high: float

    @classmethod
    def from_frame(cls, audio: AudioFrame) -> AudioReactive:
        low = (audio.bands[0] + audio.bands[1]) * 0.5
        mid = (audio.bands[2] + audio.bands[3] + audio.bands[4]) / 3.0
        high = (audio.bands[5] + audio.bands[6] + audio.bands[7]) / 3.0
        accent = max(audio.beat_strength, audio.rms * 0.5) if audio.beat else audio.rms * 0.2
        return cls(
            rms=audio.rms,
            accent=max(0.0, min(1.0, accent)),
            low=max(0.0, min(1.0, low)),
            mid=max(0.0, min(1.0, mid)),
            high=max(0.0, min(1.0, high)),
        )

    def drive(self) -> float:
        return max(0.0, min(1.0, self.rms * 0.5 + self.low * 0.3 + self.mid * 0.2))

    def shimmer(self) -> float:
        return max(0.0, min(1.0, self.high * 0.6 + self.mid * 0.2 + self.accent * 0.2))

    def speed(self, base: float, range_: float) -> float:
        return base + self.drive() * range_ + self.accent * range_ * 0.35

    def pulse(self, base: float, amount: float) -> float:
        return max(0.0, min(1.0, base + self.rms * amount * 0.5 + self.accent * amount))

    def hue_shift(self, frame: int, base_rate: float) -> int:
        return int(frame * (base_rate + self.high * 3.0 + self.accent * 6.0)) % 256

    def band_at(self, audio: AudioFrame, index: int, length: int) -> float:
        bands = len(audio.bands)
        if bands == 0:
            return 0.0
        pos = 0 if length <= 1 else index * bands // length
        return audio.bands[min(pos, bands - 1)]

    def vivid(self, hue: int, alpha: float) -> Hsla:
        return Hsla(hue, 100, 50, max(0.0, min(1.0, alpha)))

    def bright(self, hue: int, lightness: int, alpha: float) -> Hsla:
        return Hsla(hue, 100, lightness, max(0.0, min(1.0, alpha)))

    def accent_color(self, hue: int, sat: int, light: int, alpha: float) -> Hsla:
        return Hsla(hue, sat, light, max(0.0, min(1.0, alpha)))


@dataclass(slots=True)
class Decay:
    value: float = 0.0

    def step(self, target: float, release: float) -> float:
        release = max(0.0, min(1.0, release))
        if target >= self.value:
            self.value = min(target, 1.0)
        else:
            self.value = max(self.value - release, target, 0.0)
        return self.value
