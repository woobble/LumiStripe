"""Audio capture with a bounded callback-to-DSP worker boundary."""

from __future__ import annotations

import threading
import time
from collections import deque
from types import TracebackType
from typing import Any, Self

import numpy as np
import numpy.typing as npt

from .config import AUDIO_FRESH_SECONDS, FFT_HOP_SIZE, AudioConfig
from .devices import (
    _device_name,
    _load_sounddevice,
    device_id,
    device_sample_rate,
    find_input_device,
)
from .dsp import AudioState
from .protocols import AudioBatchBuffer
from .types import AudioFrame, AudioInputHealth, MusicFeatures


class BoundedAudioBatchBuffer:
    """Drop-oldest sample buffer designed for real-time producers."""

    def __init__(self, max_batches: int = 8) -> None:
        if max_batches <= 0:
            raise ValueError("max_batches must be greater than zero")
        self._max_batches = max_batches
        self._items: deque[npt.NDArray[np.float32]] = deque()
        self._condition = threading.Condition()
        self._unfinished = 0
        self._dropped = 0
        self._closed = False

    def push(self, samples: npt.NDArray[np.float32]) -> bool:
        with self._condition:
            if self._closed:
                return False
            if len(self._items) >= self._max_batches:
                self._items.popleft()
                self._unfinished -= 1
                self._dropped += 1
            self._items.append(samples)
            self._unfinished += 1
            self._condition.notify_all()
            return True

    def pop(self, timeout: float | None = None) -> npt.NDArray[np.float32] | None:
        with self._condition:
            deadline = None if timeout is None else time.monotonic() + timeout
            while not self._items:
                if self._closed:
                    return None
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return None
                self._condition.wait(remaining)
            return self._items.popleft()

    def task_done(self) -> None:
        with self._condition:
            if self._unfinished > 0:
                self._unfinished -= 1
            self._condition.notify_all()

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        with self._condition:
            deadline = None if timeout is None else time.monotonic() + timeout
            while self._unfinished:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def dropped_count(self) -> int:
        with self._condition:
            return self._dropped


class AudioInput:
    def __init__(
        self,
        device_pattern: str | None = None,
        config: AudioConfig | None = None,
        *,
        buffer: AudioBatchBuffer | None = None,
        buffer_size: int = 8,
    ) -> None:
        sounddevice = _load_sounddevice()
        self._sounddevice = sounddevice
        self._config = config or AudioConfig()
        self._state_lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._buffer = buffer or BoundedAudioBatchBuffer(buffer_size)
        self._closed = False
        self._worker_error: str | None = None
        self._callback_count = 0
        self._status_count = 0
        self._last_status: str | None = None
        self._last_callback_at: float | None = None

        device = find_input_device(sounddevice, device_pattern)
        self._device_name = _device_name(device)
        device_id_value = device_id(device)
        sample_rate = device_sample_rate(device)
        self._state = AudioState(self._config, sample_rate=sample_rate)
        self._worker = threading.Thread(
            target=self._process_batches,
            name="lumistripe-audio-dsp",
            daemon=True,
        )
        self._worker.start()

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info
            try:
                samples = np.asarray(indata, dtype=np.float32)
                if samples.ndim == 2:
                    if samples.shape[1] > 1:
                        mono = samples.mean(axis=1, dtype=np.float32)
                    else:
                        mono = samples[:, 0]
                else:
                    mono = samples.reshape(-1)
                # PortAudio may reuse its input memory as soon as the callback
                # returns, so the worker receives an owned contiguous batch.
                batch = np.ascontiguousarray(mono, dtype=np.float32).copy()
            except Exception as exc:  # noqa: BLE001 - backend conversion errors vary
                with self._health_lock:
                    self._worker_error = f"audio callback conversion failed: {exc}"
                return

            with self._health_lock:
                self._callback_count += 1
                self._last_callback_at = time.monotonic()
                if status:
                    self._status_count += 1
                    self._last_status = str(status)
            self._buffer.push(batch)

        try:
            self._stream = sounddevice.InputStream(
                device=device_id_value,
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=FFT_HOP_SIZE,
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            self._buffer.close()
            self._worker.join(timeout=1.0)
            raise RuntimeError(
                f'failed to open audio input "{self._device_name}": {exc}'
            ) from exc

    @classmethod
    def new(cls) -> AudioInput:
        return cls()

    @classmethod
    def with_device(cls, pattern: str) -> AudioInput:
        return cls(device_pattern=pattern)

    @classmethod
    def with_config(cls, config: AudioConfig) -> AudioInput:
        return cls(config=config)

    @classmethod
    def with_device_config(cls, pattern: str, config: AudioConfig) -> AudioInput:
        return cls(device_pattern=pattern, config=config)

    def _process_batches(self) -> None:
        while True:
            batch = self._buffer.pop()
            if batch is None:
                return
            try:
                with self._state_lock:
                    self._state.feed_samples(batch)
            except Exception as exc:  # noqa: BLE001 - DSP backend errors vary
                with self._health_lock:
                    self._worker_error = f"audio DSP worker failed: {exc}"
            finally:
                self._buffer.task_done()

    def _wait_for_worker(self) -> None:
        self._buffer.wait_until_idle(timeout=1.0)

    def state(self) -> AudioState:
        self._wait_for_worker()
        with self._state_lock:
            return self._state.copy()

    def read(self) -> AudioFrame:
        self._wait_for_worker()
        with self._state_lock:
            frame = self._state.frame()
            age = self._state.frame_age()
            fresh = age is not None and age <= AUDIO_FRESH_SECONDS
            return AudioFrame(
                rms=frame.rms,
                bands=frame.bands,
                beat=frame.beat,
                beat_strength=frame.beat_strength,
                sequence=frame.sequence,
                timestamp=frame.timestamp,
                fresh=fresh,
            )

    def read_features(self) -> MusicFeatures:
        self._wait_for_worker()
        with self._state_lock:
            return self._state.music_features()

    def health(self) -> AudioInputHealth:
        self._wait_for_worker()
        with self._health_lock:
            callback_count = self._callback_count
            status_count = self._status_count
            last_status = self._last_status
            last_callback_at = self._last_callback_at
            worker_error = self._worker_error
        with self._state_lock:
            last_callback_age = (
                None
                if last_callback_at is None
                else max(0.0, time.monotonic() - last_callback_at)
            )
            return AudioInputHealth(
                callback_count=callback_count,
                status_count=status_count,
                last_status=last_status,
                last_callback_age=last_callback_age,
                last_frame_age=self._state.frame_age(),
                dropped_batches=self._buffer.dropped_count,
                worker_error=worker_error,
                processor=self._state.stats(),
            )

    def reconfigure(self, config: AudioConfig) -> None:
        self._wait_for_worker()
        with self._state_lock:
            self._config = config
            self._state.reconfigure(config)

    def device_name(self) -> str:
        return self._device_name

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stream.stop()
        finally:
            self._stream.close()
            self._buffer.close()
            self._worker.join(timeout=1.0)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        self.close()


__all__ = ["AudioInput", "BoundedAudioBatchBuffer"]
