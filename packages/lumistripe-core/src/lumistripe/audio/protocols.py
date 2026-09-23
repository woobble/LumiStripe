"""Protocols at the audio capture and DSP boundaries."""

from __future__ import annotations

from typing import Protocol

import numpy as np
import numpy.typing as npt

from .config import AudioConfig
from .types import AudioFrame, AudioInputHealth, AudioSnapshot, MusicFeatures


class AudioProcessor(Protocol):
    """Minimal DSP contract independent of the native implementation."""

    def feed_samples(self, samples: npt.ArrayLike) -> None: ...

    def frame(self) -> AudioFrame: ...

    def music_features(self) -> MusicFeatures: ...

    def reconfigure(self, config: AudioConfig) -> None: ...

    def stats(self) -> object: ...


class AudioBatchBuffer(Protocol):
    """Non-blocking producer / blocking worker contract for sample batches."""

    def push(self, samples: npt.NDArray[np.float32]) -> bool: ...

    def pop(self, timeout: float | None = None) -> npt.NDArray[np.float32] | None: ...

    def task_done(self) -> None: ...

    def wait_until_idle(self, timeout: float | None = None) -> bool: ...

    def close(self) -> None: ...

    @property
    def dropped_count(self) -> int: ...


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


__all__ = ["AudioBatchBuffer", "AudioProcessor", "AudioSource", "snapshot_from_source"]
