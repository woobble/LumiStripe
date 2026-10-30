"""Binary preview frame protocol shared by the backend and browser."""

from __future__ import annotations

import struct
from typing import Protocol


class _PreviewFrame(Protocol):
    @property
    def sequence(self) -> int: ...

    @property
    def outputs(self) -> tuple[bytes, ...]: ...


PREVIEW_HEADER = struct.Struct("<4sBBIH")
PREVIEW_OUTPUT_HEADER = struct.Struct("<I")
PREVIEW_MAGIC = b"LSFP"
PREVIEW_VERSION = 1
PREVIEW_MAX_OUTPUTS = 2
PREVIEW_MAX_PIXELS = 4096


def encode_preview_frame(frame: _PreviewFrame) -> bytes:
    """Encode an immutable runtime frame for the browser preview stream."""

    if len(frame.outputs) > PREVIEW_MAX_OUTPUTS:
        raise ValueError("preview frame cannot contain more than two outputs")
    packet = bytearray(
        PREVIEW_HEADER.pack(
            PREVIEW_MAGIC,
            PREVIEW_VERSION,
            0,
            frame.sequence & 0xFFFFFFFF,
            len(frame.outputs),
        )
    )
    for output in frame.outputs:
        if len(output) % 4:
            raise ValueError("preview output must contain complete RGBA pixels")
        pixel_count = len(output) // 4
        if pixel_count > PREVIEW_MAX_PIXELS:
            raise ValueError("preview output cannot contain more than 4096 pixels")
        packet.extend(PREVIEW_OUTPUT_HEADER.pack(pixel_count))
        packet.extend(output)
    return bytes(packet)


__all__ = [
    "PREVIEW_HEADER",
    "PREVIEW_MAGIC",
    "PREVIEW_OUTPUT_HEADER",
    "PREVIEW_VERSION",
    "encode_preview_frame",
]
