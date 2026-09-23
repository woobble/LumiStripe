"""Immutable preview frames exchanged between the worker and WebSockets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PreviewFrame:
    """An immutable snapshot of the latest rendered output frame."""

    sequence: int
    outputs: tuple[bytes, ...]


__all__ = ["PreviewFrame"]
