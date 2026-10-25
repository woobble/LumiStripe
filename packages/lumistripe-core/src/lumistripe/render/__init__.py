"""Optional native kernels and NumPy fallbacks for pixel processing."""

from .kernels import (
    NATIVE_AVAILABLE,
    apply_alpha_scale_into,
    composite_screen_rgba_into,
    correct_rgba_into,
)

__all__ = [
    "NATIVE_AVAILABLE",
    "apply_alpha_scale_into",
    "composite_screen_rgba_into",
    "correct_rgba_into",
]
