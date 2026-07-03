from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy.typing as npt

from .buffers import PixelBuffer, as_pixel_buffer
from .color import Color, Rgba


class Controller(ABC):
    @property
    @abstractmethod
    def length(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def pixels(self) -> PixelBuffer:
        raise NotImplementedError

    @abstractmethod
    def pixel(self, index: int) -> Color:
        raise NotImplementedError

    @abstractmethod
    def set_pixel(self, index: int, color: Color) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        raise NotImplementedError

    @abstractmethod
    def fill(self, color: Color) -> None:
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def flush(self) -> None:
        raise NotImplementedError

    def force_flush(self) -> None:
        self.flush()


class BrightnessController(Controller):
    def __init__(self, inner: Controller, brightness: float) -> None:
        self._inner = inner
        self._brightness = max(0.0, min(1.0, float(brightness)))

    @property
    def length(self) -> int:
        return self._inner.length

    def pixels(self) -> PixelBuffer:
        return self._inner.pixels()

    def pixel(self, index: int) -> Color:
        return self._inner.pixel(index)

    def set_pixel(self, index: int, color: Color) -> None:
        r, g, b, a = color.to_rgba()
        self._inner.set_pixel(index, Rgba(r, g, b, a * self._brightness))

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        if isinstance(colors, Sequence) and len(colors) > 0 and isinstance(colors[0], Color):
            scaled = []
            for color in colors:
                assert isinstance(color, Color)
                r, g, b, a = color.to_rgba()
                scaled.append(Rgba(r, g, b, a * self._brightness))
            self._inner.set_pixels(scaled)
            return

        pixels = as_pixel_buffer(colors).copy()
        pixels[:, 3] = (pixels[:, 3].astype("uint16") * int(self._brightness * 255) // 255).astype(
            "uint8"
        )
        self._inner.set_pixels(pixels)

    def fill(self, color: Color) -> None:
        r, g, b, a = color.to_rgba()
        self._inner.fill(Rgba(r, g, b, a * self._brightness))

    def clear(self) -> None:
        self._inner.clear()

    def flush(self) -> None:
        self._inner.flush()

    def force_flush(self) -> None:
        self._inner.force_flush()


class MultiController(Controller):
    def __init__(self, controllers: Sequence[Controller]) -> None:
        if not controllers:
            raise ValueError("at least one controller is required")
        self._controllers = list(controllers)
        length = self._controllers[0].length
        if any(controller.length != length for controller in self._controllers[1:]):
            raise ValueError("all controllers must have the same length")

    @property
    def length(self) -> int:
        return self._controllers[0].length

    def pixels(self) -> PixelBuffer:
        return self._controllers[0].pixels()

    def pixel(self, index: int) -> Color:
        return self._controllers[0].pixel(index)

    def set_pixel(self, index: int, color: Color) -> None:
        for controller in self._controllers:
            controller.set_pixel(index, color)

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        normalized = as_pixel_buffer(colors)
        for controller in self._controllers:
            controller.set_pixels(normalized)

    def fill(self, color: Color) -> None:
        for controller in self._controllers:
            controller.fill(color)

    def clear(self) -> None:
        for controller in self._controllers:
            controller.clear()

    def flush(self) -> None:
        for controller in self._controllers:
            controller.flush()

    def force_flush(self) -> None:
        for controller in self._controllers:
            controller.force_flush()


class DualController(MultiController):
    def __init__(self, primary: Controller, secondary: Controller) -> None:
        super().__init__([primary, secondary])
