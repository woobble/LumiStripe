from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from math import isfinite

import numpy as np
import numpy.typing as npt

RgbArray = npt.NDArray[np.uint8]
RgbaFloatArray = npt.NDArray[np.float32]


def _clamp_u8(value: int | float) -> int:
    return max(0, min(255, int(value)))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _clamp_percent(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


class Color(ABC):
    @abstractmethod
    def to_rgba(self) -> tuple[int, int, int, float]:
        raise NotImplementedError

    def scaled(self) -> tuple[int, int, int]:
        r, g, b, a = self.to_rgba()
        scale = _clamp01(a) if isfinite(a) else 0.0
        if scale <= 0.0:
            return 0, 0, 0
        if scale >= 1.0:
            return _clamp_u8(r), _clamp_u8(g), _clamp_u8(b)
        return (
            int(_clamp_u8(r) * scale),
            int(_clamp_u8(g) * scale),
            int(_clamp_u8(b) * scale),
        )

    def as_rgba_array(self) -> RgbArray:
        r, g, b, a = self.to_rgba()
        alpha = _clamp01(a if isfinite(a) else 0.0)
        return np.array(
            [_clamp_u8(r), _clamp_u8(g), _clamp_u8(b), int(alpha * 255)],
            dtype=np.uint8,
        )

    def as_scaled_rgb_array(self) -> RgbArray:
        return np.array(self.scaled(), dtype=np.uint8)

    @staticmethod
    def hue_to_rgb(p: float, q: float, t: float) -> float:
        if t < 0.0:
            t += 1.0
        elif t > 1.0:
            t -= 1.0

        if t < 1.0 / 6.0:
            return p + (q - p) * 6.0 * t
        if t < 1.0 / 2.0:
            return q
        if t < 2.0 / 3.0:
            return p + (q - p) * 6.0 * (2.0 / 3.0 - t)
        return p


@dataclass(frozen=True, slots=True)
class Rgb(Color):
    r: int
    g: int
    b: int

    def to_rgba(self) -> tuple[int, int, int, float]:
        return self.r, self.g, self.b, 1.0


@dataclass(frozen=True, slots=True)
class Rgba(Color):
    r: int
    g: int
    b: int
    a: float

    def to_rgba(self) -> tuple[int, int, int, float]:
        return self.r, self.g, self.b, self.a


@dataclass(frozen=True, slots=True)
class Hsl(Color):
    h: float
    s: float
    lightness: float

    def to_rgba(self) -> tuple[int, int, int, float]:
        h = (float(self.h) % 360.0) / 360.0
        s = _clamp_percent(self.s) / 100.0
        lightness = _clamp_percent(self.lightness) / 100.0

        if s == 0.0:
            gray = int(lightness * 255.0)
            return gray, gray, gray, 1.0

        q = lightness * (1.0 + s) if lightness < 0.5 else lightness + s - lightness * s
        p = 2.0 * lightness - q
        r = int(Color.hue_to_rgb(p, q, h + 1.0 / 3.0) * 255.0)
        g = int(Color.hue_to_rgb(p, q, h) * 255.0)
        b = int(Color.hue_to_rgb(p, q, h - 1.0 / 3.0) * 255.0)
        return _clamp_u8(r), _clamp_u8(g), _clamp_u8(b), 1.0


@dataclass(frozen=True, slots=True)
class Hsla(Color):
    h: float
    s: float
    lightness: float
    a: float

    def to_rgba(self) -> tuple[int, int, int, float]:
        r, g, b, _ = Hsl(self.h, self.s, self.lightness).to_rgba()
        return r, g, b, self.a


@dataclass(frozen=True, slots=True)
class Hex(Color):
    value: int

    def to_rgba(self) -> tuple[int, int, int, float]:
        r = (self.value >> 16) & 0xFF
        g = (self.value >> 8) & 0xFF
        b = self.value & 0xFF
        return r, g, b, 1.0


class ColorBatch:
    def __init__(self, colors: npt.ArrayLike):
        array = np.asarray(colors, dtype=np.float32)
        if array.ndim != 2 or array.shape[1] != 4:
            raise ValueError("colors must have shape (n, 4)")
        self.colors: RgbaFloatArray = array

    def scaled(self) -> RgbArray:
        rgb = np.clip(self.colors[:, :3], 0.0, 255.0)
        alpha = self.colors[:, 3]
        scale = np.clip(alpha, 0.0, 1.0)
        scale = np.where(np.isfinite(alpha), scale, 0.0)
        return (rgb * scale[:, None]).astype(np.uint8)
