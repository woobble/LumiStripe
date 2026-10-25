"""Allocation-conscious pixel kernels with an optional native implementation."""

from __future__ import annotations

from types import ModuleType

import numpy as np
import numpy.typing as npt

PixelArray = npt.NDArray[np.uint8]
CorrectionArray = npt.NDArray[np.uint16]

_native_module: ModuleType | None
try:
    from . import _native as _native_module
except (ImportError, OSError):  # The pure-Python profile is the default.
    _native_module = None

NATIVE_AVAILABLE = _native_module is not None


def correct_rgba_into(
    source: PixelArray,
    destination: PixelArray,
    correction: CorrectionArray,
    rgb_scratch: npt.NDArray[np.uint16],
) -> None:
    """Copy an RGBA frame while applying integer RGB correction in place."""

    _validate_rgba_pair(source, destination)
    if correction.dtype != np.uint16 or correction.shape != (3,) or not correction.flags.c_contiguous:
        raise ValueError("correction must be a contiguous uint16 array of shape (3,)")
    if any(int(channel) > 255 for channel in correction):
        raise ValueError("correction channels must be between 0 and 255")
    if rgb_scratch.dtype != np.uint16 or rgb_scratch.shape != (source.shape[0], 3):
        raise ValueError("RGB scratch must be a uint16 array of shape (n, 3)")
    if not rgb_scratch.flags.c_contiguous or not rgb_scratch.flags.writeable:
        raise ValueError("RGB scratch must be writable and C-contiguous")

    if bool(np.all(correction == 255)):
        np.copyto(destination, source)
        return
    if _native_module is not None:
        _native_module.correct_rgba_into(source, destination, correction)
        return
    _correct_rgba_numpy(source, destination, correction, rgb_scratch)


def apply_alpha_scale_into(
    source: PixelArray,
    corrected: PixelArray,
    destination: PixelArray,
    scale: float,
    alpha_scratch: npt.NDArray[np.float64],
) -> None:
    """Copy a frame with floored alpha scaling and update corrected alpha."""

    _validate_rgba_pair(source, destination)
    _validate_rgba_pair(source, corrected)
    if not 0.0 <= scale <= 1.0:
        raise ValueError("alpha scale must be between zero and one")
    if alpha_scratch.dtype != np.float64 or alpha_scratch.shape != (source.shape[0],):
        raise ValueError("alpha scratch must be a float64 array of shape (n,)")
    if not alpha_scratch.flags.c_contiguous or not alpha_scratch.flags.writeable:
        raise ValueError("alpha scratch must be writable and C-contiguous")

    if scale == 1.0:
        np.copyto(destination, source)
        np.copyto(corrected[:, 3], source[:, 3])
        return
    if _native_module is not None:
        _native_module.apply_alpha_scale_into(source, corrected, destination, scale)
        return
    _apply_alpha_scale_numpy(source, corrected, destination, scale, alpha_scratch)


def composite_screen_rgba_into(
    base: PixelArray,
    destination: PixelArray,
    base_intensity: float,
    overlay_1: PixelArray | None = None,
    strength_1: float = 0.0,
    overlay_2: PixelArray | None = None,
    strength_2: float = 0.0,
) -> None:
    """Composite up to two RGBA layers using ordered SCREEN blending.

    RGB is weighted by each pixel's alpha before its layer strength is applied.
    The result is opaque RGBA and is written into a reusable destination.
    """

    _validate_rgba_pair(base, destination)
    if not np.isfinite(base_intensity):
        raise ValueError("base intensity must be finite")
    for name, overlay, strength in (
        ("overlay_1", overlay_1, strength_1),
        ("overlay_2", overlay_2, strength_2),
    ):
        if overlay is not None:
            _validate_rgba_source(overlay, name)
            if overlay.shape != base.shape:
                raise ValueError(f"{name} must have the same shape as base")
        if not np.isfinite(strength) or strength < 0.0:
            raise ValueError(f"{name} strength must be finite and non-negative")

    if _native_module is not None:
        _native_module.composite_screen_rgba_into(
            base,
            destination,
            base_intensity,
            overlay_1,
            strength_1,
            overlay_2,
            strength_2,
        )
        return
    _composite_screen_rgba_numpy(
        base,
        destination,
        base_intensity,
        overlay_1,
        strength_1,
        overlay_2,
        strength_2,
    )


def _composite_screen_rgba_numpy(
    base: PixelArray,
    destination: PixelArray,
    base_intensity: float,
    overlay_1: PixelArray | None,
    strength_1: float,
    overlay_2: PixelArray | None,
    strength_2: float,
) -> None:
    """Reference implementation matching the former layered render path."""

    composed = _visible_rgb_numpy(base) * base_intensity
    for overlay, strength in (
        (overlay_1, strength_1),
        (overlay_2, strength_2),
    ):
        if overlay is None or strength <= 0.0:
            continue
        visible = _visible_rgb_numpy(overlay) * strength
        composed = 1.0 - (1.0 - composed) * (1.0 - visible)
    destination[:, :3] = np.clip(composed * 255.0, 0.0, 255.0).astype(np.uint8)
    destination[:, 3] = 255


def _visible_rgb_numpy(pixels: PixelArray) -> npt.NDArray[np.float32]:
    normalized = pixels.astype(np.float32) / 255.0
    return normalized[:, :3] * normalized[:, 3:4]


def _correct_rgba_numpy(
    source: PixelArray,
    destination: PixelArray,
    correction: CorrectionArray,
    rgb_scratch: npt.NDArray[np.uint16],
) -> None:
    np.copyto(destination, source)
    if bool(np.all(correction == 255)):
        return
    np.multiply(source[:, :3], correction, out=rgb_scratch, dtype=np.uint16)
    np.floor_divide(rgb_scratch, 255, out=rgb_scratch)
    np.copyto(destination[:, :3], rgb_scratch, casting="unsafe")


def _apply_alpha_scale_numpy(
    source: PixelArray,
    corrected: PixelArray,
    destination: PixelArray,
    scale: float,
    alpha_scratch: npt.NDArray[np.float64],
) -> None:
    np.copyto(destination, source)
    if scale == 1.0:
        np.copyto(corrected[:, 3], source[:, 3])
        return
    np.multiply(source[:, 3], scale, out=alpha_scratch, dtype=np.float64)
    np.floor(alpha_scratch, out=alpha_scratch)
    np.copyto(destination[:, 3], alpha_scratch, casting="unsafe")
    np.copyto(corrected[:, 3], destination[:, 3])


def _validate_rgba_pair(source: PixelArray, destination: PixelArray) -> None:
    _validate_rgba_source(source, "source")
    _validate_rgba_source(destination, "destination")
    if source.shape != destination.shape:
        raise ValueError("source and destination must have the same shape")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")


def _validate_rgba_source(pixels: PixelArray, name: str) -> None:
    if not isinstance(pixels, np.ndarray):
        raise TypeError(f"{name} must be a NumPy array")
    if pixels.dtype != np.uint8 or pixels.ndim != 2 or pixels.shape[1] != 4:
        raise ValueError(f"{name} must have dtype uint8 and shape (n, 4)")
    if not pixels.flags.c_contiguous or not pixels.flags.aligned:
        raise ValueError(f"{name} must be C-contiguous and aligned")


__all__ = [
    "NATIVE_AVAILABLE",
    "apply_alpha_scale_into",
    "composite_screen_rgba_into",
    "correct_rgba_into",
]
