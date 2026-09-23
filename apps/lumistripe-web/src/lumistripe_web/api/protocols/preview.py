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


def encode_preview_frame(frame: _PreviewFrame) -> bytes:
    """Encode an immutable runtime frame for the browser preview stream."""

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
        packet.extend(PREVIEW_OUTPUT_HEADER.pack(len(output) // 4))
        packet.extend(output)
    return bytes(packet)


__all__ = [
    "PREVIEW_HEADER",
    "PREVIEW_MAGIC",
    "PREVIEW_OUTPUT_HEADER",
    "PREVIEW_VERSION",
    "encode_preview_frame",
]
