from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

PixelBuffer = npt.NDArray[np.uint8]


def new_pixel_buffer(length: int) -> PixelBuffer:
    if length <= 0:
        raise ValueError("length must be positive")

    pixels = np.zeros((length, 4), dtype=np.uint8)
    pixels[:, 3] = 255
    return pixels


def clear(pixels: PixelBuffer) -> None:
    pixels[:, :3] = 0
    pixels[:, 3] = 255


def fill_rgb(pixels: PixelBuffer, r: int, g: int, b: int, a: int = 255) -> None:
    pixels[:, 0] = np.clip(r, 0, 255)
    pixels[:, 1] = np.clip(g, 0, 255)
    pixels[:, 2] = np.clip(b, 0, 255)
    pixels[:, 3] = np.clip(a, 0, 255)


def fade(pixels: PixelBuffer, factor: int) -> None:
    factor = int(np.clip(factor, 0, 255))
    pixels[:, :3] = (pixels[:, :3].astype(np.uint16) * factor // 255).astype(np.uint8)


def apply_brightness_rgb(pixels: PixelBuffer, brightness: int) -> npt.NDArray[np.uint8]:
    brightness = int(np.clip(brightness, 0, 255))
    return (pixels[:, :3].astype(np.uint16) * brightness // 255).astype(np.uint8)


def normalize_pixel_array(colors: npt.ArrayLike) -> PixelBuffer:
    array = np.asarray(colors)
    if array.ndim != 2:
        raise ValueError("pixel array must be 2-dimensional")
    if array.shape[1] not in (3, 4):
        raise ValueError("pixel array must have shape (n, 3) or (n, 4)")

    array = np.clip(array, 0, 255).astype(np.uint8, copy=False)
    if array.shape[1] == 4:
        return array

    result = np.empty((array.shape[0], 4), dtype=np.uint8)
    result[:, :3] = array
    result[:, 3] = 255
    return result


def as_pixel_buffer(colors: Sequence[object] | npt.ArrayLike) -> PixelBuffer:
    if isinstance(colors, np.ndarray):
        return normalize_pixel_array(colors)
    if not colors:
        return np.empty((0, 4), dtype=np.uint8)

    from .color import Color

    first = colors[0]  # type: ignore[index]
    if isinstance(first, Color):
        result = np.empty((len(colors), 4), dtype=np.uint8)  # type: ignore[arg-type]
        for index, color in enumerate(colors):  # type: ignore[arg-type]
            if not isinstance(color, Color):
                raise TypeError("mixed color inputs are not supported")
            result[index] = color.as_rgba_array()
        return result

    return normalize_pixel_array(colors)  # type: ignore[arg-type]
