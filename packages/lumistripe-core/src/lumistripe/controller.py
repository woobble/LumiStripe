from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy as np
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


class ReversedController(Controller):
    def __init__(self, inner: Controller) -> None:
        self._inner = inner

    @property
    def length(self) -> int:
        return self._inner.length

    def pixels(self) -> PixelBuffer:
        return self._inner.pixels()[::-1]

    def pixel(self, index: int) -> Color:
        return self._inner.pixel(self._map(index))

    def set_pixel(self, index: int, color: Color) -> None:
        self._inner.set_pixel(self._map(index), color)

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        normalized = as_pixel_buffer(colors)
        if normalized.shape[0] > self.length:
            raise ValueError(
                f"too many pixels: got {normalized.shape[0]}, reversed controller length is {self.length}"
            )
        for index, rgba in enumerate(normalized):
            self._inner.set_pixel(
                self._map(index),
                Rgba(int(rgba[0]), int(rgba[1]), int(rgba[2]), float(rgba[3]) / 255.0),
            )

    def fill(self, color: Color) -> None:
        self._inner.fill(color)

    def clear(self) -> None:
        self._inner.clear()

    def flush(self) -> None:
        self._inner.flush()

    def force_flush(self) -> None:
        self._inner.force_flush()

    def _map(self, index: int) -> int:
        if not 0 <= index < self.length:
            raise IndexError(f"pixel index {index} out of bounds for length {self.length}")
        return self.length - 1 - index


class CompositeController(Controller):
    def __init__(self, controllers: Sequence[Controller]) -> None:
        if not controllers:
            raise ValueError("at least one controller is required")
        self._controllers = list(controllers)
        self._offsets: list[int] = []
        offset = 0
        for controller in self._controllers:
            self._offsets.append(offset)
            offset += controller.length
        self._length = offset

    @property
    def length(self) -> int:
        return self._length

    def pixels(self) -> PixelBuffer:
        if len(self._controllers) == 1:
            return self._controllers[0].pixels()
        return np.concatenate([controller.pixels() for controller in self._controllers], axis=0)

    def pixel(self, index: int) -> Color:
        controller, local_index = self._locate(index)
        return controller.pixel(local_index)

    def set_pixel(self, index: int, color: Color) -> None:
        controller, local_index = self._locate(index)
        controller.set_pixel(local_index, color)

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        normalized = as_pixel_buffer(colors)
        if normalized.shape[0] > self.length:
            raise ValueError(
                f"too many pixels: got {normalized.shape[0]}, composite controller length is {self.length}"
            )

        offset = 0
        for controller in self._controllers:
            if offset >= normalized.shape[0]:
                break
            end = min(offset + controller.length, normalized.shape[0])
            controller.set_pixels(normalized[offset:end])
            offset = end

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

    def _locate(self, index: int) -> tuple[Controller, int]:
        if not 0 <= index < self.length:
            raise IndexError(f"pixel index {index} out of bounds for length {self.length}")
        for controller, offset in zip(self._controllers, self._offsets, strict=True):
            if index < offset + controller.length:
                return controller, index - offset
        raise IndexError(f"pixel index {index} out of bounds for length {self.length}")


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
