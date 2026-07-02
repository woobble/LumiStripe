from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from .buffers import PixelBuffer, as_pixel_buffer, clear as clear_pixels, new_pixel_buffer
from .color import Color, Rgba
from .controller import Controller


@dataclass(frozen=True, slots=True)
class Config:
    chip: str = "/dev/gpiochip0"
    gpio_data: int = 14
    gpio_clock: int = 15
    consumer: str = "lumistripe"
    default_color: Color | None = None


class Stripe(Controller):
    def __init__(self, length: int, *, default_color: Color | None = None) -> None:
        self._pixels = new_pixel_buffer(length)
        self._dirty = np.ones(length, dtype=bool)
        self._default_color = default_color or Rgba(0, 0, 0, 0.0)
        if default_color is not None:
            self.fill(default_color)
            self._dirty[:] = True

    @property
    def length(self) -> int:
        return int(self._pixels.shape[0])

    def pixels(self) -> PixelBuffer:
        return self._pixels

    def pixel(self, index: int) -> Color:
        self._check_index(index)
        r, g, b, a = self._pixels[index]
        return Rgba(int(r), int(g), int(b), float(a) / 255.0)

    def set_pixel(self, index: int, color: Color) -> None:
        self._check_index(index)
        self._pixels[index] = color.as_rgba_array()
        self._dirty[index] = True

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        normalized = as_pixel_buffer(colors)
        if normalized.shape[0] > self.length:
            raise ValueError(
                f"too many pixels: got {normalized.shape[0]}, stripe length is {self.length}"
            )
        self._pixels[: normalized.shape[0]] = normalized
        self._dirty[: normalized.shape[0]] = True

    def fill(self, color: Color) -> None:
        self._pixels[:] = color.as_rgba_array()
        self._dirty[:] = True

    def clear(self) -> None:
        clear_pixels(self._pixels)
        self._dirty[:] = True

    def flush(self) -> None:
        self._dirty[:] = False

    def force_flush(self) -> None:
        self._dirty[:] = False

    def sub_stripe(self, start: int, end: int) -> SubStripe:
        return SubStripe(self, start, end)

    def _check_index(self, index: int) -> None:
        if not 0 <= index < self.length:
            raise IndexError(f"pixel index {index} out of bounds for length {self.length}")


class SubStripe(Controller):
    def __init__(self, stripe: Controller, start: int, end: int) -> None:
        if start < 0 or end < start or end > stripe.length:
            raise IndexError(f"invalid sub-stripe range [{start}, {end}) for length {stripe.length}")
        self._stripe = stripe
        self._start = start
        self._end = end

    @property
    def length(self) -> int:
        return self._end - self._start

    def pixels(self) -> PixelBuffer:
        return self._stripe.pixels()[self._start : self._end]

    def pixel(self, index: int) -> Color:
        return self._stripe.pixel(self._map(index))

    def set_pixel(self, index: int, color: Color) -> None:
        self._stripe.set_pixel(self._map(index), color)

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        normalized = as_pixel_buffer(colors)
        if normalized.shape[0] > self.length:
            raise ValueError(
                f"too many pixels: got {normalized.shape[0]}, sub-stripe length is {self.length}"
            )
        for index, rgba in enumerate(normalized):
            self._stripe.set_pixel(
                self._start + index,
                Rgba(int(rgba[0]), int(rgba[1]), int(rgba[2]), float(rgba[3]) / 255.0),
            )

    def fill(self, color: Color) -> None:
        for index in range(self._start, self._end):
            self._stripe.set_pixel(index, color)

    def clear(self) -> None:
        for index in range(self._start, self._end):
            self._stripe.set_pixel(index, Rgba(0, 0, 0, 0.0))

    def flush(self) -> None:
        self._stripe.flush()

    def force_flush(self) -> None:
        self._stripe.force_flush()

    def _map(self, index: int) -> int:
        if not 0 <= index < self.length:
            raise IndexError(f"pixel index {index} out of bounds for length {self.length}")
        return self._start + index
