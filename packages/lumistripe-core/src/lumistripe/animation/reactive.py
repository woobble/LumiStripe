from __future__ import annotations

from dataclasses import dataclass

from ..audio import AudioFrame, AudioSnapshot
from ..color import Hsla


@dataclass(slots=True)
class AudioReactive:
    rms: float
    accent: float
    low: float
    mid: float
    high: float
    onset: float = 0.0
    brightness: float = 0.0
    bpm: float = 120.0
    activity_level: float = 0.0

    @classmethod
    def from_frame(cls, audio: AudioFrame) -> AudioReactive:
        bands = list(audio.bands)
        if len(bands) < 8:
            bands.extend(0.0 for _ in range(8 - len(bands)))
        low = (bands[0] + bands[1]) * 0.5
        mid = (bands[2] + bands[3] + bands[4]) / 3.0
        high = (bands[5] + bands[6] + bands[7]) / 3.0
        accent = max(audio.beat_strength, audio.rms * 0.55) if audio.beat else audio.rms * 0.2
        drive = max(0.0, min(1.0, audio.rms * 0.5 + low * 0.3 + mid * 0.2))
        return cls(
            rms=audio.rms,
            accent=max(0.0, min(1.0, accent)),
            low=max(0.0, min(1.0, low)),
            mid=max(0.0, min(1.0, mid)),
            high=max(0.0, min(1.0, high)),
            onset=max(0.0, min(1.0, audio.beat_strength if audio.beat else audio.rms * 0.18)),
            brightness=max(0.0, min(1.0, high * 0.55 + audio.rms * 0.25)),
            activity_level=max(0.0, min(1.0, drive * 0.65 + accent * 0.25 + high * 0.1)),
        )

    @classmethod
    def from_snapshot(cls, snapshot: AudioSnapshot) -> AudioReactive:
        return cls(
            rms=snapshot.features.energy,
            accent=snapshot.accent,
            low=snapshot.low,
            mid=snapshot.mid,
            high=snapshot.high,
            onset=snapshot.onset_strength,
            brightness=snapshot.brightness,
            bpm=snapshot.bpm,
            activity_level=snapshot.activity,
        )

    def drive(self) -> float:
        return max(0.0, min(1.0, self.rms * 0.5 + self.low * 0.3 + self.mid * 0.2))

    def shimmer(self) -> float:
        return max(0.0, min(1.0, self.high * 0.6 + self.mid * 0.2 + self.accent * 0.2))

    def activity(self) -> float:
        if self.activity_level > 0.0:
            return max(0.0, min(1.0, self.activity_level))
        return max(0.0, min(1.0, self.drive() * 0.6 + self.accent * 0.25 + self.shimmer() * 0.15))

    def bass_hit(self, threshold: float = 0.55) -> bool:
        return self.low >= threshold or (self.accent >= 0.62 and self.low >= threshold * 0.55)

    def high_hit(self, threshold: float = 0.5) -> bool:
        return self.high >= threshold or self.shimmer() >= threshold

    def drop_hit(self, *, beat: bool, low_threshold: float = 0.34, accent_threshold: float = 0.72) -> bool:
        return beat and (self.low > low_threshold or self.accent > accent_threshold or self.onset > 0.58 or self.rms > 0.8)

    def speed(self, base: float, range_: float) -> float:
        return base + self.drive() * range_ + self.accent * range_ * 0.35

    def pulse(self, base: float, amount: float) -> float:
        return max(0.0, min(1.0, base + self.rms * amount * 0.5 + self.accent * amount))

    def beat_pulse(self, floor: float = 0.0, amount: float = 1.0) -> float:
        return max(0.0, min(1.0, max(floor, self.accent * amount + self.rms * amount * 0.25)))

    def hue_shift(self, frame: int, base_rate: float) -> int:
        return int(frame * (base_rate + self.high * 3.0 + self.accent * 6.0)) % 256

    def band_at(self, audio: AudioFrame, index: int, length: int) -> float:
        bands = len(audio.bands)
        if bands == 0:
            return 0.0
        pos = 0 if length <= 1 else index * bands // length
        return audio.bands[min(pos, bands - 1)]

    def band_window(self, audio: AudioFrame, index: int, length: int, span: int = 1) -> float:
        bands = len(audio.bands)
        if bands == 0:
            return 0.0
        if length <= 1:
            return audio.bands[0]
        pos = index * bands / max(length - 1, 1)
        center = int(pos)
        start = max(center - span, 0)
        end = min(center + span + 1, bands)
        if start >= end:
            return audio.bands[min(center, bands - 1)]
        total = 0.0
        count = 0
        for band in audio.bands[start:end]:
            total += band
            count += 1
        return total / max(count, 1)

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
