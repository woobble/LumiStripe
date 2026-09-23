"""Protocols used by audio consumers and test doubles."""

from __future__ import annotations

from typing import Protocol

import numpy.typing as npt

from . import AudioConfig, AudioFrame, AudioInputHealth, AudioSnapshot, MusicFeatures


class AudioProcessor(Protocol):
    """Minimal DSP contract independent of the native implementation."""

    def feed_samples(self, samples: npt.ArrayLike) -> None: ...

    def frame(self) -> AudioFrame: ...

    def music_features(self) -> MusicFeatures: ...

    def reconfigure(self, config: AudioConfig) -> None: ...

    def stats(self) -> object: ...


class AudioSource(Protocol):
    """Capture contract consumed by playback runtimes."""

    def read(self) -> AudioFrame: ...

    def read_features(self) -> MusicFeatures: ...

    def health(self) -> AudioInputHealth: ...

    def close(self) -> None: ...


def snapshot_from_source(source: AudioSource) -> AudioSnapshot:
    """Build an immutable snapshot from a capture source."""

    return AudioSnapshot.from_parts(
        source.read(),
        source.read_features(),
        source.health(),
    )


__all__ = ["AudioProcessor", "AudioSource", "snapshot_from_source"]
