"""Dedicated owner for physical LED controllers and frame transmission."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime
from math import ceil

import numpy as np
import numpy.typing as npt
from lumistripe import Controller

from .frame_exchange import LatestFrameExchange
from .outputs import OutputGateController

PixelFrame = npt.NDArray[np.uint8]
OutputBuilder = Callable[[], Sequence[tuple[Controller, bool]]]
OUTPUT_LATENCY_SAMPLE_COUNT = 128


@dataclass(frozen=True, slots=True)
class OutputThreadStats:
    """Low-rate output diagnostics independent of the render heartbeat."""

    heartbeat_at_monotonic: float | None
    last_frame_at_monotonic: float | None
    transmitted_frames: int
    failed_flushes: int
    output_rate: float
    last_error: str | None
    dropped_frames: int
    last_successful_update_at: datetime | None
    last_successful_update_age_seconds: float | None
    frame_latency_ms: float | None
    frame_latency_p95_ms: float | None
    frame_latency_sample_count: int


@dataclass(slots=True)
class _Output:
    gate: OutputGateController
    reversed: bool
    last_error: str | None = None


@dataclass(slots=True)
class _ConfigureRequest:
    exchange: LatestFrameExchange | None
    builder: OutputBuilder
    completed: Future[LatestFrameExchange | None]


class LedOutputThread:
    """Create and access hardware controllers only on a dedicated thread.

    The renderer publishes a complete, already corrected set of frames through
    a bounded latest-wins exchange. One output consumer owns each slot until
    all configured controllers have accepted that set. No render-owned arrays
    or controller objects cross this boundary.
    """

    def __init__(self, output_lengths: Sequence[int], builder: OutputBuilder) -> None:
        self._exchange = _make_exchange(output_lengths)
        self._builder = builder
        self._requests: queue.Queue[_ConfigureRequest] = queue.Queue()
        self._stop_event = threading.Event()
        self._started = threading.Event()
        self._startup_exception: Exception | None = None
        self._thread = threading.Thread(
            target=self._run, name="lumistripe-led-output", daemon=True
        )
        self._outputs: tuple[_Output, ...] = ()
        self._state_lock = threading.Lock()
        self._heartbeat_at: float | None = None
        self._last_frame_at: float | None = None
        self._transmitted_frames = 0
        self._failed_flushes = 0
        self._last_error: str | None = None
        self._last_successful_update_at: datetime | None = None
        self._last_successful_update_monotonic: float | None = None
        self._frame_latency_ms: float | None = None
        self._frame_latency_samples: deque[float] = deque(
            maxlen=OUTPUT_LATENCY_SAMPLE_COUNT
        )
        self._rate_started_at = time.monotonic()
        self._rate_frames = 0
        self._output_rate = 0.0

    @property
    def exchange(self) -> LatestFrameExchange | None:
        return self._exchange

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        if not self._started.wait(timeout):
            raise TimeoutError("LED output thread did not initialize in time")
        if self._startup_exception is not None:
            raise self._startup_exception

    def reconfigure(
        self,
        output_lengths: Sequence[int],
        builder: OutputBuilder,
        timeout: float = 10.0,
    ) -> LatestFrameExchange | None:
        """Replace physical devices on their owner thread at a frame boundary."""
        return self.reconfigure_async(output_lengths, builder).result(timeout)

    def reconfigure_async(
        self,
        output_lengths: Sequence[int],
        builder: OutputBuilder,
    ) -> Future[LatestFrameExchange | None]:
        """Queue device replacement without waiting for current output work."""
        exchange = _make_exchange(output_lengths)
        request: _ConfigureRequest = _ConfigureRequest(
            exchange, builder, Future()
        )
        if self._stop_event.is_set() or not self._thread.is_alive():
            raise RuntimeError("LED output thread is not running")
        self._requests.put(request)
        return request.completed

    def stats(self) -> OutputThreadStats:
        with self._state_lock:
            heartbeat = self._heartbeat_at
            last_frame = self._last_frame_at
            transmitted = self._transmitted_frames
            failed = self._failed_flushes
            rate = self._output_rate
            error = self._last_error
            successful_at = self._last_successful_update_at
            successful_monotonic = self._last_successful_update_monotonic
            frame_latency = self._frame_latency_ms
            frame_latency_samples = tuple(self._frame_latency_samples)
        exchange = self._exchange
        dropped = exchange.stats().dropped_count if exchange is not None else 0
        successful_age = (
            max(0.0, time.monotonic() - successful_monotonic)
            if successful_monotonic is not None
            else None
        )
        return OutputThreadStats(
            heartbeat_at_monotonic=heartbeat,
            last_frame_at_monotonic=last_frame,
            transmitted_frames=transmitted,
            failed_flushes=failed,
            output_rate=rate,
            last_error=error,
            dropped_frames=dropped,
            last_successful_update_at=successful_at,
            last_successful_update_age_seconds=successful_age,
            frame_latency_ms=frame_latency,
            frame_latency_p95_ms=_nearest_rank_percentile(
                frame_latency_samples, 0.95
            ),
            frame_latency_sample_count=len(frame_latency_samples),
        )

    def is_healthy(self, *, max_heartbeat_age_seconds: float) -> bool:
        if not self._thread.is_alive():
            return False
        with self._state_lock:
            heartbeat = self._heartbeat_at
        return heartbeat is not None and (
            time.monotonic() - heartbeat <= max_heartbeat_age_seconds
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        exchange = self._exchange
        if exchange is not None:
            exchange.close(discard_pending=True)
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise TimeoutError("LED output thread did not stop in time")

    def _run(self) -> None:
        try:
            self._outputs = self._create_outputs(self._builder, self._exchange)
            self._record_heartbeat()
        except Exception as exc:  # noqa: BLE001 - report startup failures to caller
            self._startup_exception = exc
            self._set_error(exc)
            self._started.set()
            return
        self._started.set()

        try:
            while not self._stop_event.is_set():
                request = self._next_request()
                if request is not None:
                    self._apply_configuration(request)
                    continue

                exchange = self._exchange
                if exchange is None:
                    self._record_heartbeat()
                    try:
                        request = self._requests.get(timeout=0.02)
                    except queue.Empty:
                        continue
                    self._apply_configuration(request)
                    continue
                slot = exchange.take(timeout=0.02)
                self._record_heartbeat()
                if slot is None:
                    continue
                try:
                    self._transmit(slot.frames)
                finally:
                    latency_ms = (
                        time.monotonic_ns() - slot.submitted_at_ns
                    ) / 1_000_000.0
                    self._record_frame_latency(latency_ms)
                    exchange.release(slot)
        finally:
            exchange = self._exchange
            if exchange is not None:
                exchange.close(discard_pending=True)
            self._close_outputs()
            self._fail_pending_requests()

    def _next_request(self) -> _ConfigureRequest | None:
        try:
            return self._requests.get_nowait()
        except queue.Empty:
            return None

    def _apply_configuration(self, request: _ConfigureRequest) -> None:
        current = self._exchange
        if current is not None:
            current.close(discard_pending=True)
        self._clear_and_close_outputs()
        try:
            self._outputs = self._create_outputs(request.builder, request.exchange)
            self._exchange = request.exchange
            self._builder = request.builder
            with self._state_lock:
                self._last_successful_update_at = None
                self._last_successful_update_monotonic = None
                self._last_error = None
            self._record_heartbeat()
        except Exception as exc:  # noqa: BLE001 - allow caller to restore old topology
            self._outputs = ()
            self._exchange = None
            self._set_error(exc)
            request.completed.set_exception(exc)
            return
        request.completed.set_result(request.exchange)

    @staticmethod
    def _create_outputs(
        builder: OutputBuilder,
        exchange: LatestFrameExchange | None,
    ) -> tuple[_Output, ...]:
        outputs: list[_Output] = []
        try:
            for controller, reversed_output in builder():
                outputs.append(
                    _Output(OutputGateController(controller), reversed_output)
                )
            if exchange is None:
                if outputs:
                    raise ValueError("physical outputs require a frame exchange")
            else:
                if len(outputs) != exchange.output_count:
                    raise ValueError(
                        "physical output count does not match the frame exchange"
                    )
                for index, (output, length) in enumerate(
                    zip(outputs, exchange.output_lengths, strict=True)
                ):
                    if output.gate.length != length:
                        raise ValueError(
                            f"physical output {index} has length {output.gate.length}; "
                            f"expected {length}"
                        )
        except Exception as build_error:
            for output in outputs:
                try:
                    output.gate.close()
                except Exception as close_error:  # noqa: BLE001 - preserve build error
                    build_error.add_note(f"controller close also failed: {close_error}")
            raise
        return tuple(outputs)

    def _transmit(self, frames: tuple[PixelFrame, ...]) -> None:
        for output, frame in zip(self._outputs, frames, strict=True):
            output.gate.set_pixels(frame[::-1] if output.reversed else frame)
        for output in self._outputs:
            before = output.gate.last_successful_update_at
            output.gate.flush()
            after = output.gate.last_successful_update_at
            error = output.gate.last_error
            if error is not None and error != output.last_error:
                with self._state_lock:
                    self._failed_flushes += 1
                    self._last_error = error
                output.last_error = error
            elif after is not None and after != before:
                output.last_error = None

        now = time.monotonic()
        successful_at: datetime | None = None
        successful_age: float | None = None
        for output in self._outputs:
            output_at = output.gate.last_successful_update_at
            output_age = output.gate.last_successful_update_age_seconds
            if output_at is not None and (
                successful_at is None or output_at > successful_at
            ):
                successful_at = output_at
            if output_age is not None and (
                successful_age is None or output_age < successful_age
            ):
                successful_age = output_age
        with self._state_lock:
            self._transmitted_frames += 1
            self._rate_frames += 1
            self._last_frame_at = now
            elapsed = now - self._rate_started_at
            if elapsed >= 1.0:
                self._output_rate = self._rate_frames / elapsed
                self._rate_frames = 0
                self._rate_started_at = now
            if all(output.gate.last_error is None for output in self._outputs):
                self._last_error = None
            if successful_at is not None and successful_age is not None:
                self._last_successful_update_at = successful_at
                self._last_successful_update_monotonic = now - successful_age
        self._record_heartbeat(now)

    def _clear_and_close_outputs(self) -> None:
        outputs = self._outputs
        self._outputs = ()
        for output in outputs:
            try:
                output.gate.clear()
                output.gate.force_flush()
            except Exception as exc:  # noqa: BLE001 - close every output regardless
                self._set_error(exc)
            finally:
                try:
                    output.gate.close()
                except Exception as exc:  # noqa: BLE001 - close sibling devices too
                    self._set_error(exc)

    def _close_outputs(self) -> None:
        self._clear_and_close_outputs()

    def _record_heartbeat(self, at: float | None = None) -> None:
        with self._state_lock:
            self._heartbeat_at = time.monotonic() if at is None else at

    def _record_frame_latency(self, latency_ms: float) -> None:
        with self._state_lock:
            self._frame_latency_ms = latency_ms
            self._frame_latency_samples.append(latency_ms)

    def _set_error(self, exc: Exception) -> None:
        detail = str(exc).strip().replace("\n", " ")
        with self._state_lock:
            self._last_error = f"{type(exc).__name__}: {detail}"[:256]
            self._failed_flushes += 1

    def _fail_pending_requests(self) -> None:
        while True:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                return
            request.completed.set_exception(RuntimeError("LED output thread stopped"))


def _make_exchange(output_lengths: Sequence[int]) -> LatestFrameExchange | None:
    lengths = tuple(output_lengths)
    return LatestFrameExchange(lengths) if lengths else None


def _nearest_rank_percentile(
    values: Sequence[float], quantile: float
) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(len(ordered) * quantile) - 1))
    return ordered[index]


__all__ = ["LedOutputThread", "OutputThreadStats"]
