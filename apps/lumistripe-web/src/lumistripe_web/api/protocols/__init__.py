"""Versioned wire protocols used by the dashboard API."""

from .preview import (
    PREVIEW_HEADER,
    PREVIEW_MAGIC,
    PREVIEW_OUTPUT_HEADER,
    PREVIEW_VERSION,
    encode_preview_frame,
)

__all__ = [
    "PREVIEW_HEADER",
    "PREVIEW_MAGIC",
    "PREVIEW_OUTPUT_HEADER",
    "PREVIEW_VERSION",
    "encode_preview_frame",
]
