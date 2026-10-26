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

# Paired catalog benchmarks show that renderer-call overhead can exceed the C
# savings on very short strips. Keep native animation dispatch off below this
# size; update this threshold only with comparable Python/native measurements.
NATIVE_RENDER_MIN_PIXELS = 150


def native_render_dispatch_enabled(
    pixel_count: int, minimum_pixels: int = NATIVE_RENDER_MIN_PIXELS
) -> bool:
    """Return whether the measured native renderer path is worthwhile."""

    return pixel_count >= minimum_pixels


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


def native_tunnel_rings_available() -> bool:
    """Return whether the loaded render extension exposes the tunnel kernel."""

    return _native_module is not None and hasattr(
        _native_module, "render_tunnel_rings_into"
    )


def render_tunnel_rings_into(
    destination: PixelArray,
    geometry: npt.NDArray[np.float64],
    colors: PixelArray,
    clear_color: npt.NDArray[np.uint8],
    ring_count: int,
    brightness: float,
) -> bool:
    """Render one or two BeatTunnel rings into a reusable RGBA buffer.

    Returns ``False`` without touching the destination when the optional native
    kernel is unavailable, allowing the effect's Python renderer to take over.
    """

    if not native_tunnel_rings_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if geometry.dtype != np.float64 or geometry.shape != (2, 5):
        raise ValueError("geometry must be a float64 array of shape (2, 5)")
    if not geometry.flags.c_contiguous or not geometry.flags.aligned:
        raise ValueError("geometry must be aligned and C-contiguous")
    if colors.dtype != np.uint8 or colors.shape != (2, 3):
        raise ValueError("colors must be a uint8 array of shape (2, 3)")
    if not colors.flags.c_contiguous or not colors.flags.aligned:
        raise ValueError("colors must be aligned and C-contiguous")
    if clear_color.dtype != np.uint8 or clear_color.shape != (3,):
        raise ValueError("clear_color must be a uint8 array of shape (3,)")
    if not clear_color.flags.c_contiguous or not clear_color.flags.aligned:
        raise ValueError("clear_color must be aligned and C-contiguous")
    if ring_count not in (1, 2):
        raise ValueError("ring_count must be one or two")
    if not np.isfinite(brightness) or not 0.0 <= brightness <= 1.0:
        raise ValueError("brightness must be finite and between zero and one")
    assert _native_module is not None
    _native_module.render_tunnel_rings_into(
        destination, geometry, colors, clear_color, ring_count, brightness
    )
    return True


def native_spectrum_flash_available() -> bool:
    """Return whether the loaded extension exposes the SpectrumFlash kernel."""

    return _native_module is not None and hasattr(
        _native_module, "render_spectrum_flash_into"
    )


def render_spectrum_flash_into(
    destination: PixelArray,
    bands: tuple[float, ...],
    width: float,
    hue_phase: float,
    flash: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> bool:
    """Render SpectrumFlash pixels into a reusable RGBA buffer."""

    if not native_spectrum_flash_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if len(bands) != 8:
        raise ValueError("bands must contain exactly eight values")
    if not np.isfinite((width, hue_phase, flash, brightness)).all():
        raise ValueError("render parameters must be finite")
    if width < 0.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("width must be non-negative and brightness between zero and one")
    if palette not in (0, 1, 2, 3) or not 0 <= custom_rgb <= 0xFFFFFF:
        raise ValueError("palette or custom color is invalid")
    assert _native_module is not None
    _native_module.render_spectrum_flash_into(
        destination, bands, width, hue_phase, flash, brightness, palette, custom_rgb
    )
    return True


def native_solid_render_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_solid_rgba_into")


def native_neon_storm_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_neon_storm_into")


def native_electric_storm_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_electric_storm_into")


def render_electric_storm_into(
    destination: PixelArray,
    geometry: npt.NDArray[np.float64],
    flash_alpha: float,
    brightness: float,
    palette: int = 0,
    custom_rgb: int = 0x7C3AED,
) -> bool:
    if not native_electric_storm_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if geometry.dtype != np.float64 or geometry.ndim != 2 or geometry.shape[1] != 4 or not geometry.flags.c_contiguous:
        raise ValueError("geometry must be a contiguous float64 array with shape (n, 4)")
    if not np.isfinite((flash_alpha, brightness)).all() or not 0.0 <= flash_alpha <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("flash alpha and brightness must be finite and in [0, 1]")
    if palette not in (0, 1, 2, 3) or not 0 <= custom_rgb <= 0xFFFFFF:
        raise ValueError("palette or custom color is invalid")
    assert _native_module is not None
    _native_module.render_electric_storm_into(destination, geometry, flash_alpha, brightness, palette, custom_rgb)
    return True


def render_neon_storm_into(
    destination: PixelArray,
    levels: npt.NDArray[np.float32],
    hues: npt.NDArray[np.int16],
    positions: npt.NDArray[np.float64],
    drive: float,
    brightness: float,
    palette: int = 0,
    custom_rgb: int = 0x7C3AED,
) -> bool:
    if not native_neon_storm_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if levels.dtype != np.float32 or levels.shape != (len(destination),) or not levels.flags.c_contiguous:
        raise ValueError("levels must be contiguous float32 with one value per pixel")
    if hues.dtype != np.int16 or hues.shape != levels.shape or not hues.flags.c_contiguous:
        raise ValueError("hues must be contiguous int16 with one value per pixel")
    if positions.dtype != np.float64 or positions.ndim != 1 or not positions.flags.c_contiguous:
        raise ValueError("positions must be a contiguous float64 vector")
    if not np.isfinite((drive, brightness)).all() or not 0.0 <= brightness <= 1.0:
        raise ValueError("drive and brightness must be finite and brightness in [0, 1]")
    if palette not in (0, 1, 2, 3) or not 0 <= custom_rgb <= 0xFFFFFF:
        raise ValueError("palette or custom color is invalid")
    assert _native_module is not None
    _native_module.render_neon_storm_into(destination, levels, hues, positions, drive, brightness, palette, custom_rgb)
    return True


def native_halves_render_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_halves_rgba_into")


def render_solid_rgba_into(destination: PixelArray, rgba: tuple[int, int, int, int]) -> bool:
    if not native_solid_render_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if len(rgba) != 4 or any(not 0 <= int(channel) <= 255 for channel in rgba):
        raise ValueError("rgba must contain four channels between zero and 255")
    assert _native_module is not None
    _native_module.render_solid_rgba_into(destination, *(int(channel) for channel in rgba))
    return True


def native_hsl_solid_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_hsl_solid_into")


def render_hsl_solid_into(
    destination: PixelArray,
    hue: float,
    saturation: float,
    lightness: float,
    alpha: float,
    brightness: float,
) -> bool:
    if not native_hsl_solid_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if not np.isfinite((hue, saturation, lightness, alpha, brightness)).all():
        raise ValueError("HSL render values must be finite")
    if not 0.0 <= alpha <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("alpha and brightness must be between zero and one")
    assert _native_module is not None
    _native_module.render_hsl_solid_into(
        destination, hue, saturation, lightness, alpha, brightness
    )
    return True


def render_halves_rgba_into(
    destination: PixelArray,
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> bool:
    if _native_module is None or not hasattr(_native_module, "render_halves_rgba_into"):
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if any(
        len(color) != 4 or any(not 0 <= int(channel) <= 255 for channel in color)
        for color in (left, right)
    ):
        raise ValueError("colors must contain four channels between zero and 255")
    assert _native_module is not None
    _native_module.render_halves_rgba_into(
        destination, *(int(channel) for channel in (*left, *right))
    )
    return True


def native_red_sweep_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_red_sweep_into")


def render_red_sweep_into(
    destination: PixelArray, start: int, width: int, on: bool, alpha: int
) -> bool:
    if not native_red_sweep_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if start < 0 or width < 0 or not 0 <= alpha <= 255:
        raise ValueError("invalid red sweep geometry or alpha")
    assert _native_module is not None
    _native_module.render_red_sweep_into(destination, start, width, on, alpha)
    return True


def native_red_chase_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_red_chase_into")


def render_red_chase_into(
    destination: PixelArray, period: int, block: int, phase: float, alpha: int
) -> bool:
    if not native_red_chase_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if (
        period < 1 or not 0 <= block <= period or not np.isfinite(phase)
        or not 0 <= alpha <= 255
    ):
        raise ValueError("invalid red chase geometry")
    assert _native_module is not None
    _native_module.render_red_chase_into(destination, period, block, phase, alpha)
    return True


def native_drop_explosion_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_drop_explosion_into"
    )


def render_drop_explosion_into(
    destination: PixelArray,
    frame: int,
    flash_alpha: float,
    tension: float,
    preview: float,
    *,
    audio_mode: bool,
    low: float,
    high: float,
    brightness: float,
) -> bool:
    if not native_drop_explosion_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if not np.isfinite((flash_alpha, tension, preview, low, high, brightness)).all():
        raise ValueError("drop explosion render values must be finite")
    if not 0.0 <= brightness <= 1.0:
        raise ValueError("brightness must be between zero and one")
    assert _native_module is not None
    _native_module.render_drop_explosion_into(
        destination,
        frame,
        flash_alpha,
        tension,
        preview,
        audio_mode,
        low,
        high,
        brightness,
    )
    return True


def native_fire_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_fire_into")


def render_fire_into(
    destination: PixelArray,
    heat: npt.NDArray[np.float32],
    next_heat: npt.NDArray[np.float32],
    frame: int,
    cooling: float,
    sparking: float,
    drive: float,
    bass: float,
    shimmer: float,
    accent: float,
    brightness: float,
) -> bool:
    if not native_fire_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    for name, buffer in (("heat", heat), ("next_heat", next_heat)):
        if (
            buffer.dtype != np.float32
            or buffer.shape != (destination.shape[0],)
            or not buffer.flags.c_contiguous
            or not buffer.flags.aligned
            or not buffer.flags.writeable
        ):
            raise ValueError(f"{name} must be a writable float32 vector matching destination")
    values = (cooling, sparking, drive, bass, shimmer, accent, brightness)
    if not np.isfinite(values).all() or not 0.0 <= brightness <= 1.0:
        raise ValueError("fire render values must be finite and brightness between zero and one")
    assert _native_module is not None
    _native_module.render_fire_into(
        destination,
        heat,
        next_heat,
        frame,
        cooling,
        sparking,
        drive,
        bass,
        shimmer,
        accent,
        brightness,
    )
    return True


def native_color_wipe_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_color_wipe_into"
    )


def native_rainbow_pattern_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_rainbow_pattern_into"
    )


def render_rainbow_pattern_into(
    destination: PixelArray,
    mode: int,
    frame: int,
    bands: tuple[float, ...] | None,
    spread: float,
    base_hue: float,
    saturation: float,
    alpha: float,
    low: float,
    mid: float,
    beat_pulse: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_rainbow_pattern_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if mode not in (0, 1, 2) or palette_id is None:
        raise ValueError("rainbow mode or palette is invalid")
    if bands is not None and len(bands) != 8:
        raise ValueError("bands must contain exactly eight values")
    if len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("custom color must use #RRGGBB format")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    values = (spread, base_hue, saturation, alpha, low, mid, beat_pulse, brightness)
    if not np.isfinite(values).all() or spread <= 0.0:
        raise ValueError("rainbow render values must be finite and spread positive")
    if not 0.0 <= alpha <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("rainbow alpha or brightness is invalid")
    assert _native_module is not None
    _native_module.render_rainbow_pattern_into(
        destination,
        mode,
        frame,
        bands,
        spread,
        base_hue,
        saturation,
        alpha,
        low,
        mid,
        beat_pulse,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_theater_chase_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_theater_chase_into"
    )


def render_theater_chase_into(
    destination: PixelArray,
    stride: int,
    phase: float,
    hue: float,
    drive: float,
    brightness: float,
    audio_mode: bool,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_theater_chase_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if stride < 1 or palette_id is None:
        raise ValueError("stride or palette is invalid")
    if len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("custom color must use #RRGGBB format")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    if not np.isfinite((phase, hue, drive, brightness)).all():
        raise ValueError("TheaterChase render parameters must be finite")
    if not 0.0 <= drive <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("drive or brightness is outside its valid range")
    assert _native_module is not None
    _native_module.render_theater_chase_into(
        destination,
        stride,
        phase,
        hue,
        drive,
        brightness,
        audio_mode,
        palette_id,
        custom_rgb,
    )
    return True


def native_laser_sweep_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_laser_sweep_into"
    )


def render_laser_sweep_into(
    destination: PixelArray,
    position: float,
    width: float,
    hue: int,
    alpha: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_laser_sweep_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None:
        raise ValueError("palette is invalid")
    if len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("custom color must use #RRGGBB format")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    if not np.isfinite((position, width, hue, alpha, brightness)).all():
        raise ValueError("LaserSweep render parameters must be finite")
    if width <= 0 or not 0.0 <= alpha <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("width, alpha, or brightness is invalid")
    assert _native_module is not None
    _native_module.render_laser_sweep_into(
        destination, position, width, hue, alpha, brightness, palette_id, custom_rgb
    )
    return True


def native_sinelon_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_sinelon_into")


def render_sinelon_into(
    destination: PixelArray,
    center: float,
    width: float,
    hue: int,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_sinelon_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None:
        raise ValueError("palette is invalid")
    if len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("custom color must use #RRGGBB format")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    if not np.isfinite((center, width, hue, brightness)).all():
        raise ValueError("Sinelon render parameters must be finite")
    if width <= 0 or not 0 <= hue <= 255 or not 0.0 <= brightness <= 1.0:
        raise ValueError("width, hue, or brightness is invalid")
    assert _native_module is not None
    _native_module.render_sinelon_into(
        destination, center, width, hue, brightness, palette_id, custom_rgb
    )
    return True


def native_peak_mirror_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_peak_mirror_into"
    )


def render_peak_mirror_into(
    destination: PixelArray,
    levels: tuple[float, ...],
    center_flash: float,
    hue_shift: int,
    width: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_peak_mirror_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None or len(levels) != 8:
        raise ValueError("palette or levels are invalid")
    if len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("custom color must use #RRGGBB format")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    if not np.isfinite((*levels, center_flash, width, brightness)).all():
        raise ValueError("PeakMirror render parameters must be finite")
    if (
        not 0 <= center_flash <= 1
        or not 0 <= hue_shift < 360
        or width <= 0
        or not 0 <= brightness <= 1
    ):
        raise ValueError("PeakMirror render parameters are outside their valid ranges")
    assert _native_module is not None
    _native_module.render_peak_mirror_into(
        destination,
        levels,
        center_flash,
        hue_shift,
        width,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_bouncing_ball_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_bouncing_ball_into"
    )


def render_bouncing_ball_into(
    destination: PixelArray,
    position: float,
    radius: float,
    glow_width: float,
    glow: float,
    hue: int,
    brightness: float,
    audio_mode: bool,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_bouncing_ball_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None:
        raise ValueError("palette is invalid")
    if len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("custom color must use #RRGGBB format")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    if not np.isfinite((position, radius, glow_width, glow, hue, brightness)).all():
        raise ValueError("BouncingBall render parameters must be finite")
    if (
        radius <= 0
        or glow_width <= 0
        or not 0.0 <= glow <= 1.0
        or not 0 <= hue <= 359
        or not 0.0 <= brightness <= 1.0
    ):
        raise ValueError("BouncingBall render parameters are outside their valid ranges")
    assert _native_module is not None
    _native_module.render_bouncing_ball_into(
        destination,
        position,
        radius,
        glow_width,
        glow,
        hue,
        brightness,
        audio_mode,
        palette_id,
        custom_rgb,
    )
    return True


def native_wave_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_wave_into")


def render_wave_into(
    destination: PixelArray,
    frame: int,
    bands: tuple[float, ...] | None,
    frequency: float,
    speed: float,
    low: float,
    mid: float,
    high: float,
    accent: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_wave_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None:
        raise ValueError("palette is invalid")
    if bands is not None and len(bands) != 8:
        raise ValueError("bands must contain exactly eight values")
    if len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("custom color must use #RRGGBB format")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    values = (frequency, speed, low, mid, high, accent, brightness)
    if not np.isfinite(values).all():
        raise ValueError("Wave render parameters must be finite")
    if frequency <= 0.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("frequency or brightness is invalid")
    assert _native_module is not None
    _native_module.render_wave_into(
        destination,
        frame,
        bands is not None,
        bands,
        frequency,
        speed,
        low,
        mid,
        high,
        accent,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_juggle_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_juggle_into")


def render_juggle_into(
    destination: PixelArray,
    positions: tuple[float, ...],
    radius: float,
    frame: int,
    hue_rate: float,
    brightness: float,
    audio_mode: bool,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_juggle_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None or not 1 <= len(positions) <= 32:
        raise ValueError("palette or positions are invalid")
    if len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("custom color must use #RRGGBB format")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    if not np.isfinite((*positions, radius, hue_rate, brightness)).all():
        raise ValueError("Juggle render parameters must be finite")
    if radius <= 0 or hue_rate < 0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("Juggle render parameters are outside their valid ranges")
    assert _native_module is not None
    _native_module.render_juggle_into(
        destination,
        positions,
        radius,
        frame,
        hue_rate,
        brightness,
        audio_mode,
        palette_id,
        custom_rgb,
    )
    return True


def native_confetti_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_confetti_into"
    )


def render_confetti_into(
    destination: PixelArray,
    intensities: npt.NDArray[np.float32],
    frame: int,
    bands: tuple[float, ...] | None,
    fade: float,
    spread: float,
    trigger_rate: float,
    density: int,
    addition: float,
    accent: float,
    high: float,
    bloom: float,
    hue_shift: int,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_confetti_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if (
        intensities.dtype != np.float32
        or intensities.shape != (destination.shape[0],)
        or not intensities.flags.c_contiguous
        or not intensities.flags.writeable
    ):
        raise ValueError("intensities must be writable float32 with one value per pixel")
    if bands is not None and len(bands) != 8:
        raise ValueError("bands must contain exactly eight values")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None or len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("palette or custom color is invalid")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    values = (fade, spread, trigger_rate, addition, accent, high, bloom, brightness)
    if not np.isfinite(values).all():
        raise ValueError("Confetti render parameters must be finite")
    if not 0.0 <= fade <= 1.0 or not 0.0 <= spread <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("Confetti fade, spread, or brightness is outside its valid range")
    assert _native_module is not None
    _native_module.render_confetti_into(
        destination,
        intensities,
        bands,
        frame,
        bands is not None,
        fade,
        spread,
        trigger_rate,
        density,
        addition,
        accent,
        high,
        bloom,
        hue_shift,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_glow_rush_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_glow_rush_into"
    )


def render_glow_rush_into(
    destination: PixelArray,
    position: float,
    width: float,
    hue: int,
    pulse: float,
    glow_width: float,
    brightness: float,
    audio_mode: bool,
    main_light: int,
    glow_alpha: float,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_glow_rush_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None:
        raise ValueError("palette is invalid")
    if len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("custom color must use #RRGGBB format")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    values = (position, width, pulse, glow_width, brightness, glow_alpha)
    if not np.isfinite(values).all():
        raise ValueError("GlowRush render parameters must be finite")
    if (
        width <= 0
        or not 0 <= hue <= 255
        or not 0 <= pulse <= 1
        or glow_width <= 0
        or not 0 <= brightness <= 1
        or not 0 <= main_light <= 100
        or glow_alpha < 0
    ):
        raise ValueError("GlowRush render parameters are outside their valid ranges")
    assert _native_module is not None
    _native_module.render_glow_rush_into(
        destination,
        position,
        width,
        hue,
        pulse,
        glow_width,
        brightness,
        audio_mode,
        main_light,
        glow_alpha,
        palette_id,
        custom_rgb,
    )
    return True


def native_dual_comet_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_dual_comet_into"
    )


def render_dual_comet_into(
    destination: PixelArray,
    phase: float,
    width: float,
    alpha: float,
    separation: float,
    hue_a: int,
    hue_b: int,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_dual_comet_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None or len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("palette or custom color is invalid")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    if not np.isfinite((phase, width, alpha, separation, brightness)).all():
        raise ValueError("DualComet render parameters must be finite")
    if width <= 0 or not 0 <= alpha <= 1 or not 0 <= brightness <= 1:
        raise ValueError("DualComet width, alpha, or brightness is invalid")
    if not 0 <= hue_a <= 359 or not 0 <= hue_b <= 359:
        raise ValueError("DualComet hue is outside its valid range")
    assert _native_module is not None
    _native_module.render_dual_comet_into(
        destination,
        phase,
        width,
        alpha,
        separation,
        hue_a,
        hue_b,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_shockwave_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_shockwave_into"
    )


def render_shockwave_into(
    destination: PixelArray,
    radius: float,
    width: float,
    accent: float,
    hue: int,
    mid: float,
    brightness: float,
    bands: tuple[float, ...] | None,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_shockwave_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if bands is not None and len(bands) != 8:
        raise ValueError("bands must contain exactly eight values")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None or len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("palette or custom color is invalid")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    if not np.isfinite((radius, width, accent, hue, mid, brightness)).all():
        raise ValueError("Shockwave render parameters must be finite")
    if width <= 0 or not 0 <= accent <= 1 or not 0 <= hue <= 255 or not 0 <= brightness <= 1:
        raise ValueError("Shockwave render parameters are outside their valid ranges")
    assert _native_module is not None
    _native_module.render_shockwave_into(
        destination,
        radius,
        width,
        accent,
        hue,
        mid,
        brightness,
        bands is not None,
        bands,
        palette_id,
        custom_rgb,
    )
    return True


def native_aurora_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_aurora_into")


def native_pulse_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_pulse_into")


def native_comet_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_comet_into")


def _palette_parameters(palette: str, custom_color: str) -> tuple[int, int]:
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None or len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("palette or custom color is invalid")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    return palette_id, custom_rgb


def native_dance_floor_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_dance_floor_into")


def render_dance_floor_into(
    destination: PixelArray,
    frame: int,
    segment_width: int,
    frequency: float,
    audio_mode: bool,
    hue_shift: float,
    mid: float,
    high: float,
    drive: float,
    beat_boost: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render segmented DanceFloor pixels with the optional native kernel."""
    if not native_dance_floor_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (frequency, hue_shift, mid, high, drive, beat_boost, brightness)
    if not np.isfinite(values).all() or segment_width < 1:
        raise ValueError("DanceFloor render parameters must be finite and valid")
    if not 0.0 <= brightness <= 1.0:
        raise ValueError("DanceFloor brightness must be between zero and one")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_dance_floor_into(
        destination,
        frame,
        segment_width,
        frequency,
        int(audio_mode),
        hue_shift,
        mid,
        high,
        drive,
        beat_boost,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_disco_sparkle_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_disco_sparkle_into")


def render_disco_sparkle_into(
    destination: PixelArray,
    intensities: npt.NDArray[np.float32],
    frame: int,
    audio_mode: bool,
    density: int,
    accent_trigger: bool,
    decay: float,
    variation: float,
    hue_rate: float,
    pulse: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Advance sparkle state and render it natively when available."""
    if not native_disco_sparkle_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if (
        intensities.dtype != np.float32
        or intensities.shape != (destination.shape[0],)
        or not intensities.flags.c_contiguous
        or not intensities.flags.writeable
    ):
        raise ValueError("intensities must be writable contiguous float32 matching destination")
    values = (decay, variation, hue_rate, pulse, brightness)
    if not np.isfinite(values).all() or not 0.0 <= brightness <= 1.0:
        raise ValueError("DiscoSparkle render parameters must be finite and valid")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_disco_sparkle_into(
        destination,
        intensities,
        frame,
        int(audio_mode),
        density,
        int(accent_trigger),
        decay,
        variation,
        hue_rate,
        pulse,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_club_flash_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_club_flash_into")


def render_club_flash_into(
    destination: PixelArray,
    hue_seed: int,
    segment_start: int,
    segment_width: int,
    whole_strip: bool,
    flash: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render ClubFlash pixels through the optional native extension."""
    if not native_club_flash_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if segment_width < 0 or not np.isfinite((flash, brightness)).all():
        raise ValueError("ClubFlash render parameters must be finite and valid")
    if not 0.0 <= flash <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("ClubFlash flash and brightness must be between zero and one")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_club_flash_into(
        destination,
        hue_seed,
        segment_start,
        segment_width,
        int(whole_strip),
        flash,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_beat_wave_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_beat_wave_into")


def render_beat_wave_into(
    destination: PixelArray,
    waves: npt.NDArray[np.float64],
    wave_count: int,
    hue: int,
    width: float,
    glow: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render the current BeatWave field without allocating wave scratch."""
    if not native_beat_wave_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if (
        waves.dtype != np.float64
        or waves.shape != (8, 2)
        or not waves.flags.c_contiguous
    ):
        raise ValueError("waves must be contiguous float64 with shape (8, 2)")
    if not 0 <= wave_count <= 8 or not 0 <= hue <= 255:
        raise ValueError("BeatWave count or hue is outside its valid range")
    if not np.isfinite((width, glow, brightness)).all() or width < 1.0:
        raise ValueError("BeatWave render parameters must be finite and valid")
    if not 0.0 <= glow <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("BeatWave glow and brightness must be between zero and one")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_beat_wave_into(
        destination,
        waves,
        wave_count,
        hue,
        width,
        glow,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_beat_explosion_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_beat_explosion_into")


def render_beat_explosion_into(
    destination: PixelArray,
    frame: int,
    audio_mode: bool,
    center: float,
    radius: float,
    width: float,
    hit: float,
    low: float,
    mid: float,
    high: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render BeatExplosion pixels with the optional native kernel."""
    if not native_beat_explosion_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (center, radius, width, hit, low, mid, high, brightness)
    if not np.isfinite(values).all() or center < 0.0 or width <= 0.0:
        raise ValueError("BeatExplosion render parameters must be finite and valid")
    if not 0.0 <= hit <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("BeatExplosion hit and brightness must be between zero and one")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_beat_explosion_into(
        destination,
        frame,
        int(audio_mode),
        center,
        radius,
        width,
        hit,
        low,
        mid,
        high,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_firework_burst_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_firework_burst_into")


def render_firework_burst_into(
    destination: PixelArray,
    phases: npt.NDArray[np.float64],
    phase_count: int,
    bursts: npt.NDArray[np.float64],
    burst_count: int,
    frame: int,
    audio_mode: bool,
    center: float,
    fade_alpha: float,
    glow: float,
    mid: float,
    high: float,
    brightness: float,
    bands: tuple[float, ...] | None,
    hue: int,
    palette: str,
    custom_color: str,
) -> bool:
    """Render FireworkBurst phases or its current audio burst state."""
    if not native_firework_burst_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if (
        phases.dtype != np.float64
        or phases.shape != (12,)
        or not phases.flags.c_contiguous
        or bursts.dtype != np.float64
        or bursts.shape != (12, 3)
        or not bursts.flags.c_contiguous
    ):
        raise ValueError("FireworkBurst scratch arrays have invalid dtype or shape")
    if not 0 <= phase_count <= 12 or not 0 <= burst_count <= 12 or not 0 <= hue <= 255:
        raise ValueError("FireworkBurst count or hue is outside its valid range")
    values = (center, fade_alpha, glow, mid, high, brightness)
    if not np.isfinite(values).all() or center < 0.0:
        raise ValueError("FireworkBurst parameters must be finite and valid")
    if not 0.0 <= fade_alpha <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("FireworkBurst fade and brightness must be between zero and one")
    if bands is not None and (len(bands) > 32 or not np.isfinite(bands).all()):
        raise ValueError("FireworkBurst audio bands are invalid")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_firework_burst_into(
        destination,
        phases,
        bursts,
        phase_count,
        burst_count,
        frame,
        int(audio_mode),
        center,
        fade_alpha,
        glow,
        mid,
        high,
        brightness,
        bands,
        hue,
        palette_id,
        custom_rgb,
    )
    return True


def native_color_burst_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_color_burst_into")


def render_color_burst_into(
    destination: PixelArray,
    bursts: npt.NDArray[np.float64],
    burst_count: int,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render active ColorBurst rings into a reusable RGBA frame."""
    if not native_color_burst_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if (
        bursts.dtype != np.float64
        or bursts.shape != (6, 5)
        or not bursts.flags.c_contiguous
    ):
        raise ValueError("ColorBurst scratch must be contiguous float64 with shape (6, 5)")
    if not 0 <= burst_count <= 6 or not np.isfinite(brightness):
        raise ValueError("ColorBurst parameters must be finite and valid")
    if not 0.0 <= brightness <= 1.0:
        raise ValueError("ColorBurst brightness must be between zero and one")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_color_burst_into(
        destination,
        bursts,
        burst_count,
        palette_id,
        brightness,
        custom_rgb,
    )
    return True


def native_plasma_rave_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_plasma_rave_into")


def render_plasma_rave_into(
    destination: PixelArray,
    frequency: float,
    time_one: float,
    time_two: float,
    time_three: float,
    hue_base: int,
    hue_scale: float,
    alpha_extra: float,
    brightness: float,
    audio_mode: bool,
    palette: str,
    custom_color: str,
) -> bool:
    """Render PlasmaRave's full per-pixel plasma field in native C."""
    if not native_plasma_rave_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (frequency, time_one, time_two, time_three, hue_scale, alpha_extra, brightness)
    if not np.isfinite(values).all() or frequency <= 0.0:
        raise ValueError("PlasmaRave render parameters must be finite and valid")
    if not 0.0 <= alpha_extra <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("PlasmaRave alpha and brightness must be between zero and one")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_plasma_rave_into(
        destination,
        frequency,
        time_one,
        time_two,
        time_three,
        hue_base,
        hue_scale,
        alpha_extra,
        brightness,
        palette_id,
        int(audio_mode),
        int(not audio_mode),
        custom_rgb,
    )
    return True


def native_beat_ripple_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_beat_ripple_into")


def render_beat_ripple_into(
    destination: PixelArray,
    audio_mode: bool,
    center: float,
    radius_or_offset: float,
    width: float,
    alpha_scale: float,
    hue: int,
    lightness: int,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render BeatRipple rings into a transparent reusable RGBA frame."""
    if not native_beat_ripple_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (center, radius_or_offset, width, alpha_scale, brightness)
    if not np.isfinite(values).all() or width <= 0.0 or alpha_scale < 0.0:
        raise ValueError("BeatRipple parameters must be finite and valid")
    if not 0.0 <= brightness <= 1.0 or not 0 <= hue <= 255 or not 0 <= lightness <= 100:
        raise ValueError("BeatRipple color and brightness values are out of range")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_beat_ripple_into(
        destination,
        int(audio_mode),
        center,
        radius_or_offset,
        width,
        alpha_scale,
        brightness,
        hue,
        lightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_neon_confetti_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_neon_confetti_into")


def render_neon_confetti_into(
    destination: PixelArray,
    levels: npt.NDArray[np.float32],
    hues: npt.NDArray[np.int16],
    frame: int,
    fade_multiplier: float,
    spawn_threshold: float,
    high: float,
    spread: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Advance NeonConfetti state and render its deterministic particle field."""
    if not native_neon_confetti_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if (
        levels.dtype != np.float32
        or hues.dtype != np.int16
        or levels.shape != (destination.shape[0],)
        or hues.shape != levels.shape
        or not levels.flags.c_contiguous
        or not hues.flags.c_contiguous
        or not levels.flags.writeable
        or not hues.flags.writeable
    ):
        raise ValueError("NeonConfetti state arrays must match the destination")
    values = (fade_multiplier, spawn_threshold, high, spread, brightness)
    if not np.isfinite(values).all():
        raise ValueError("NeonConfetti parameters must be finite")
    if (
        fade_multiplier < 0.0
        or spawn_threshold < 0.0
        or not 0.0 <= high <= 1.0
        or not 0.0 <= spread <= 1.0
        or not 0.0 <= brightness <= 1.0
    ):
        raise ValueError("NeonConfetti parameters are out of range")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_neon_confetti_into(
        destination,
        levels,
        hues,
        frame,
        fade_multiplier,
        spawn_threshold,
        high,
        spread,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_strobe_chase_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_strobe_chase_into")


def render_strobe_chase_into(
    destination: PixelArray,
    stride: int,
    phase: float,
    hue: int,
    frame: int,
    drive: float,
    flash: float,
    brightness: float,
    flash_active: bool,
    palette: str,
    custom_color: str,
) -> bool:
    """Render StrobeChase pulses and beat-triggered full-strip flashes."""
    if not native_strobe_chase_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (phase, drive, flash, brightness)
    if not np.isfinite(values).all():
        raise ValueError("StrobeChase parameters must be finite")
    if (
        stride < 2
        or not 0.0 <= drive <= 1.0
        or not 0.0 <= flash <= 1.0
        or not 0.0 <= brightness <= 1.0
        or not 0 <= hue < 360
    ):
        raise ValueError("StrobeChase parameters are out of range")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_strobe_chase_into(
        destination,
        stride,
        phase,
        hue,
        frame,
        drive,
        flash,
        brightness,
        int(flash_active),
        palette_id,
        custom_rgb,
    )
    return True


def native_rave_scanner_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_rave_scanner_into")


def render_rave_scanner_into(
    destination: PixelArray,
    phase: float,
    width: float,
    beam_brightness: float,
    brightness: float,
    hue: int,
    palette: str,
    custom_color: str,
) -> bool:
    """Render RaveScanner's linear beam into a reusable RGBA output buffer."""
    if not native_rave_scanner_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (phase, width, beam_brightness, brightness)
    if not np.isfinite(values).all() or width <= 0.0:
        raise ValueError("RaveScanner parameters must be finite and valid")
    if (
        not 0.0 <= beam_brightness <= 1.0
        or not 0.0 <= brightness <= 1.0
        or not 0 <= hue <= 255
    ):
        raise ValueError("RaveScanner parameters are out of range")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_rave_scanner_into(
        destination,
        phase,
        width,
        beam_brightness,
        brightness,
        hue,
        palette_id,
        custom_rgb,
    )
    return True


def native_center_burst_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_center_burst_into")


def render_center_burst_into(
    destination: PixelArray,
    hue_seed: int,
    center: float,
    radius: float,
    width: float,
    flash: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render CenterBurst's single expanding ring into a reusable frame."""
    if not native_center_burst_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (center, radius, width, flash, brightness)
    if not np.isfinite(values).all() or width <= 0.0:
        raise ValueError("CenterBurst parameters must be finite and valid")
    if not 0.0 <= flash <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("CenterBurst flash and brightness must be between zero and one")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_center_burst_into(
        destination,
        hue_seed,
        center,
        radius,
        width,
        flash,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_dual_laser_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_dual_laser_into")


def render_dual_laser_into(
    destination: PixelArray,
    position_a: float,
    position_b: float,
    width: float,
    alpha: float,
    brightness: float,
    hue_a: int,
    hue_b: int,
    palette: str,
    custom_color: str,
) -> bool:
    """Render DualLaser's two independently colored beams."""
    if not native_dual_laser_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (position_a, position_b, width, alpha, brightness)
    if not np.isfinite(values).all() or width <= 0.0:
        raise ValueError("DualLaser parameters must be finite and valid")
    if (
        not 0.0 <= alpha <= 1.0
        or not 0.0 <= brightness <= 1.0
        or not 0 <= hue_a <= 255
        or not 0 <= hue_b <= 255
    ):
        raise ValueError("DualLaser color and brightness values are out of range")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_dual_laser_into(
        destination,
        position_a,
        position_b,
        width,
        alpha,
        brightness,
        hue_a,
        hue_b,
        palette_id,
        custom_rgb,
    )
    return True


def native_mirror_flash_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_mirror_flash_into")


def render_mirror_flash_into(
    destination: PixelArray,
    hue_seed: int,
    alternate: int,
    center: float,
    span: float,
    flash: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render MirrorFlash's symmetric center-out pulse."""
    if not native_mirror_flash_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (center, span, flash, brightness)
    if not np.isfinite(values).all() or span <= 0.0:
        raise ValueError("MirrorFlash parameters must be finite and valid")
    if (
        alternate not in (0, 1)
        or not 0.0 <= flash <= 1.0
        or not 0.0 <= brightness <= 1.0
    ):
        raise ValueError("MirrorFlash parameters are out of range")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_mirror_flash_into(
        destination,
        hue_seed,
        center,
        span,
        flash,
        brightness,
        alternate,
        palette_id,
        custom_rgb,
    )
    return True


def native_drop_wave_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_drop_wave_into")


def render_drop_wave_into(
    destination: PixelArray,
    hue_seed: int,
    center: float,
    radius: float,
    width: float,
    charge: float,
    flash: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render DropWave's charge field or expanding drop ring."""
    if not native_drop_wave_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (center, radius, width, charge, flash, brightness)
    if not np.isfinite(values).all() or width <= 0.0:
        raise ValueError("DropWave parameters must be finite and valid")
    if (
        not 0.0 <= charge <= 1.0
        or not 0.0 <= flash <= 1.0
        or not 0.0 <= brightness <= 1.0
    ):
        raise ValueError("DropWave parameters are out of range")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_drop_wave_into(
        destination,
        hue_seed,
        center,
        radius,
        width,
        charge,
        flash,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_disco_comet_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_disco_comet_into")


def render_disco_comet_into(
    destination: PixelArray,
    phase: float,
    width: float,
    trail: float,
    hue: int,
    high: float,
    sparkle_threshold: int,
    frame: int,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render DiscoComet's trail and deterministic per-pixel sparkles."""
    if not native_disco_comet_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (phase, width, trail, high, brightness)
    if not np.isfinite(values).all() or width <= 0.0:
        raise ValueError("DiscoComet parameters must be finite and valid")
    if (
        not 0.0 <= trail <= 1.0
        or not 0.0 <= high <= 1.0
        or not 0.0 <= brightness <= 1.0
        or not 0 <= hue <= 255
        or not 0 <= sparkle_threshold <= 100
    ):
        raise ValueError("DiscoComet parameters are out of range")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_disco_comet_into(
        destination,
        phase,
        width,
        trail,
        hue,
        high,
        sparkle_threshold,
        frame,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_pixel_explosion_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_pixel_explosion_into")


def render_pixel_explosion_into(
    destination: PixelArray,
    bursts: npt.NDArray[np.float64],
    burst_count: int,
    width: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render active PixelExplosion rings from reusable burst-state storage."""
    if not native_pixel_explosion_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if (
        bursts.dtype != np.float64
        or bursts.ndim != 2
        or bursts.shape[1] != 4
        or burst_count < 0
        or burst_count > bursts.shape[0]
        or not bursts.flags.c_contiguous
    ):
        raise ValueError("PixelExplosion bursts must be contiguous float64 rows")
    if not np.isfinite((width, brightness)).all() or width <= 0.0:
        raise ValueError("PixelExplosion parameters must be finite and valid")
    if not 0.0 <= brightness <= 1.0:
        raise ValueError("PixelExplosion brightness must be between zero and one")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_pixel_explosion_into(
        destination,
        bursts,
        burst_count,
        width,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def native_lightning_strike_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_lightning_strike_into")


def render_lightning_strike_into(
    destination: PixelArray,
    strike_position: int,
    strike_length: int,
    hue: int,
    flash_alpha: float,
    all_flash: float,
    brightness: float,
    audio_mode: bool,
    palette: str,
    custom_color: str,
) -> bool:
    """Render LightningStrike's branch-specific flash frame."""
    if not native_lightning_strike_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (flash_alpha, all_flash, brightness)
    if not np.isfinite(values).all():
        raise ValueError("LightningStrike values must be finite")
    if (
        strike_length < 0
        or not 0 <= hue <= 255
        or not 0.0 <= flash_alpha <= 1.0
        or all_flash < 0.0
        or not 0.0 <= brightness <= 1.0
    ):
        raise ValueError("LightningStrike values are out of range")
    palette_id, custom_rgb = _palette_parameters(palette, custom_color)
    assert _native_module is not None
    _native_module.render_lightning_strike_into(
        destination,
        strike_position,
        strike_length,
        hue,
        flash_alpha,
        all_flash,
        brightness,
        int(audio_mode),
        palette_id,
        custom_rgb,
    )
    return True


def render_comet_into(
    destination: PixelArray,
    position: float,
    tail: float,
    shimmer: float,
    hue: float,
    intensity: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render a Comet trail natively when the optional extension is present."""
    if not native_comet_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    values = (position, tail, shimmer, hue, intensity, brightness)
    if not np.isfinite(values).all() or tail <= 0.0:
        raise ValueError("Comet render parameters must be finite and valid")
    if not 0.0 <= intensity <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("Comet intensity and brightness must be between zero and one")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None or len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("palette or custom color is invalid")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    assert _native_module is not None
    _native_module.render_comet_into(
        destination,
        position,
        tail,
        shimmer,
        hue,
        intensity,
        brightness,
        palette_id,
        custom_rgb,
    )
    return True


def render_pulse_into(
    destination: PixelArray,
    phase: float,
    hue: int,
    alpha: float,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    """Render Pulse pixels natively when the optional extension is present."""
    if not native_pulse_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if not np.isfinite((phase, alpha, brightness)).all():
        raise ValueError("Pulse render parameters must be finite")
    if not 0.0 <= alpha <= 1.0 or not 0.0 <= brightness <= 1.0:
        raise ValueError("Pulse alpha and brightness must be between zero and one")
    if not 0 <= hue <= 255:
        raise ValueError("Pulse hue must be between zero and 255")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None or len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("palette or custom color is invalid")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    assert _native_module is not None
    _native_module.render_pulse_into(
        destination, phase, hue, alpha, brightness, palette_id, custom_rgb
    )
    return True


def render_aurora_into(
    destination: PixelArray,
    frame: int,
    bands: tuple[float, ...] | None,
    frequency: float,
    speed: float,
    low: float,
    mid: float,
    high: float,
    beat_glow: float,
    hue_offset: int,
    brightness: float,
    palette: str,
    custom_color: str,
) -> bool:
    if not native_aurora_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if bands is not None and len(bands) != 8:
        raise ValueError("bands must contain exactly eight values")
    palette_id = {"rainbow": 0, "neon": 1, "fire": 2, "custom": 3}.get(palette)
    if palette_id is None or len(custom_color) != 7 or not custom_color.startswith("#"):
        raise ValueError("palette or custom color is invalid")
    try:
        custom_rgb = int(custom_color[1:], 16)
    except ValueError as exc:
        raise ValueError("custom color must use #RRGGBB format") from exc
    values = (frequency, speed, low, mid, high, beat_glow, brightness)
    if not np.isfinite(values).all():
        raise ValueError("Aurora render parameters must be finite")
    if frequency <= 0 or not 0 <= beat_glow <= 1 or not 0 <= brightness <= 1:
        raise ValueError("Aurora frequency, beat glow, or brightness is invalid")
    if not 0 <= hue_offset <= 255:
        raise ValueError("Aurora hue offset is invalid")
    assert _native_module is not None
    _native_module.render_aurora_into(
        destination,
        frame,
        bands is not None,
        bands,
        frequency,
        speed,
        low,
        mid,
        high,
        beat_glow,
        brightness,
        hue_offset,
        palette_id,
        custom_rgb,
    )
    return True


def render_color_wipe_into(
    destination: PixelArray,
    head: float,
    tail: float,
    hue: float,
    saturation: float,
    direction: int,
    local: float,
    boost: float,
    brightness: float,
) -> bool:
    if not native_color_wipe_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if direction not in (-1, 1) or tail <= 0.0:
        raise ValueError("color wipe direction or tail is invalid")
    values = (head, tail, hue, saturation, local, boost, brightness)
    if not np.isfinite(values).all() or not 0.0 <= brightness <= 1.0:
        raise ValueError("color wipe values must be finite and brightness in range")
    assert _native_module is not None
    _native_module.render_color_wipe_into(
        destination,
        destination.shape[0],
        head,
        tail,
        hue,
        saturation,
        direction,
        local,
        boost,
        brightness,
    )
    return True


def native_comet_storm_available() -> bool:
    return _native_module is not None and hasattr(
        _native_module, "render_comet_storm_into"
    )


def render_comet_storm_into(
    destination: PixelArray,
    geometry: npt.NDArray[np.float64],
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> bool:
    if not native_comet_storm_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if geometry.dtype != np.float64 or geometry.ndim != 2 or geometry.shape[1] != 4:
        raise ValueError("geometry must be a float64 array of shape (n, 4)")
    if not geometry.flags.c_contiguous or not geometry.flags.aligned:
        raise ValueError("geometry must be aligned and C-contiguous")
    if not np.isfinite(brightness) or not 0.0 <= brightness <= 1.0:
        raise ValueError("brightness must be finite and between zero and one")
    if palette not in (0, 1, 2, 3) or not 0 <= custom_rgb <= 0xFFFFFF:
        raise ValueError("palette or custom color is invalid")
    assert _native_module is not None
    _native_module.render_comet_storm_into(
        destination, geometry, brightness, palette, custom_rgb
    )
    return True


def native_twinkle_available() -> bool:
    return _native_module is not None and hasattr(_native_module, "render_twinkle_into")


def render_twinkle_into(
    destination: PixelArray,
    levels: npt.NDArray[np.float32],
    hues: npt.NDArray[np.int16],
    frame: int,
    radius: int,
    bloom: float,
    brightness: float,
    palette: int,
    custom_rgb: int,
) -> bool:
    if not native_twinkle_available():
        return False
    _validate_rgba_source(destination, "destination")
    if not destination.flags.writeable:
        raise ValueError("destination must be writable")
    if levels.dtype != np.float32 or levels.shape != (destination.shape[0],):
        raise ValueError("levels must be a float32 array matching destination length")
    if hues.dtype != np.int16 or hues.shape != (destination.shape[0],):
        raise ValueError("hues must be an int16 array matching destination length")
    if not levels.flags.c_contiguous or not hues.flags.c_contiguous:
        raise ValueError("levels and hues must be contiguous")
    if radius < 0 or not np.isfinite((bloom, brightness)).all():
        raise ValueError("radius must be non-negative and parameters finite")
    if not 0.0 <= brightness <= 1.0 or palette not in (0, 1, 2, 3):
        raise ValueError("brightness or palette is invalid")
    if not 0 <= custom_rgb <= 0xFFFFFF:
        raise ValueError("custom color is invalid")
    assert _native_module is not None
    _native_module.render_twinkle_into(
        destination, levels, hues, frame, radius, bloom, brightness, palette, custom_rgb
    )
    return True


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
    "native_aurora_available",
    "native_bouncing_ball_available",
    "native_color_wipe_available",
    "native_comet_storm_available",
    "native_confetti_available",
    "native_drop_explosion_available",
    "native_dual_comet_available",
    "native_fire_available",
    "native_glow_rush_available",
    "native_halves_render_available",
    "native_hsl_solid_available",
    "native_juggle_available",
    "native_laser_sweep_available",
    "native_peak_mirror_available",
    "native_rainbow_pattern_available",
    "native_red_chase_available",
    "native_red_sweep_available",
    "native_shockwave_available",
    "native_sinelon_available",
    "native_solid_render_available",
    "native_spectrum_flash_available",
    "native_theater_chase_available",
    "native_tunnel_rings_available",
    "native_twinkle_available",
    "native_wave_available",
    "render_aurora_into",
    "render_bouncing_ball_into",
    "render_color_wipe_into",
    "render_comet_storm_into",
    "render_confetti_into",
    "render_drop_explosion_into",
    "render_dual_comet_into",
    "render_fire_into",
    "render_glow_rush_into",
    "render_halves_rgba_into",
    "render_hsl_solid_into",
    "render_juggle_into",
    "render_laser_sweep_into",
    "render_peak_mirror_into",
    "render_rainbow_pattern_into",
    "render_red_chase_into",
    "render_red_sweep_into",
    "render_shockwave_into",
    "render_sinelon_into",
    "render_solid_rgba_into",
    "render_spectrum_flash_into",
    "render_theater_chase_into",
    "render_tunnel_rings_into",
    "render_twinkle_into",
    "render_wave_into",
]
