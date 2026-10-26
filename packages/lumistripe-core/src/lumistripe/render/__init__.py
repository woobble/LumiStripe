"""Optional native kernels and NumPy fallbacks for pixel processing."""

from .kernels import (
    NATIVE_AVAILABLE,
    apply_alpha_scale_into,
    composite_screen_rgba_into,
    correct_rgba_into,
    native_halves_render_available,
    native_comet_storm_available,
    native_solid_render_available,
    native_twinkle_available,
    native_spectrum_flash_available,
    native_tunnel_rings_available,
    render_halves_rgba_into,
    render_comet_storm_into,
    render_twinkle_into,
    render_solid_rgba_into,
    render_spectrum_flash_into,
    render_tunnel_rings_into,
)

__all__ = [
    "NATIVE_AVAILABLE",
    "apply_alpha_scale_into",
    "composite_screen_rgba_into",
    "correct_rgba_into",
    "native_halves_render_available",
    "native_comet_storm_available",
    "native_solid_render_available",
    "native_twinkle_available",
    "native_spectrum_flash_available",
    "native_tunnel_rings_available",
    "render_halves_rgba_into",
    "render_comet_storm_into",
    "render_twinkle_into",
    "render_solid_rgba_into",
    "render_spectrum_flash_into",
    "render_tunnel_rings_into",
]
