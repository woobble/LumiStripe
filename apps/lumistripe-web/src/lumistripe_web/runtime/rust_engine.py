"""Single-process Rust output manager and compact binary frame transport."""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from collections.abc import Sequence
from concurrent.futures import Future, InvalidStateError
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from queue import Empty, Full, Queue
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

from .frame_exchange import LatestFrameExchange
from .output_thread import OUTPUT_LATENCY_SAMPLE_COUNT, OutputThreadStats

PixelFrame: TypeAlias = npt.NDArray[np.uint8]
JSONCommandData: TypeAlias = dict[str, object] | None

PROTOCOL_VERSION = 2
MAX_CONTROL_MESSAGE_BYTES = 1_048_576
FRAME_MAGIC = b"LSF2"
FRAME_ACK_MAGIC = b"LSA2"
FRAME_HEADER = struct.Struct("<4sHHQI")
FRAME_ACK = struct.Struct("<4sHHQQ")
_STARTUP_TIMEOUT_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 1.0
_CONFIGURE_TIMEOUT_SECONDS = 12.0
_SHUTDOWN_TIMEOUT_SECONDS = 8.0
_HEALTH_POLL_SECONDS = 0.5


class RustEngineError(OSError):
    """The optional Rust process or its local IPC protocol failed."""


@dataclass(frozen=True, slots=True)
class RustOutputSpec:
    """One configured Rust output, with its backend selected by the runtime."""

    output_id: str
    pixel_count: int
    reversed: bool
    backend: str
    device: str | None = None
    speed_hz: int | None = None
    chip: str | None = None
    data_pin: int | None = None
    clock_pin: int | None = None

    def to_wire(self) -> dict[str, object]:
        if self.backend == "mock":
            backend: dict[str, object] = {"type": "mock"}
        elif self.backend == "mock_spi":
            backend = {"type": "mock_spi"}
        elif self.backend == "spi":
            backend = {
                "type": "spi",
                "device": self.device,
                "speed_hz": self.speed_hz,
            }
        elif self.backend == "gpio":
            backend = {
                "type": "gpio",
                "chip": self.chip,
                "data_pin": self.data_pin,
                "clock_pin": self.clock_pin,
            }
        else:
            raise ValueError(f"unsupported Rust output backend: {self.backend}")
        return {
            "id": self.output_id,
            "pixel_count": self.pixel_count,
            "reversed": self.reversed,
            "backend": backend,
        }


@dataclass(frozen=True, slots=True)
class EngineHealth:
    status: str
    configured: bool
    output_count: int
    pixel_counts: tuple[int, ...]
    frames_rendered: int
    last_sequence: int | None
    dropped_frames: int
    failed_frames: int
    last_error: str | None
    output_rate: float
    frame_latency_ms: float | None
    queue_latency_ms: float | None
    transmission_ms: float | None
    frame_latency_p50_ms: float | None
    frame_latency_p95_ms: float | None
    frame_latency_p99_ms: float | None
    sequential_skew_ms: float | None
    last_successful_update_at: datetime | None


def _encode_request(
    request_id: str, command: str, data: JSONCommandData = None
) -> bytes:
    command_value: dict[str, object] = {"type": command}
    if data is not None:
        command_value["data"] = data
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "command": command_value,
    }
    payload = json.dumps(request, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    ) + b"\n"
    if len(payload) > MAX_CONTROL_MESSAGE_BYTES:
        raise RustEngineError(
            f"engine IPC request is {len(payload)} bytes; maximum is {MAX_CONTROL_MESSAGE_BYTES}"
        )
    return payload


def _decode_response(payload: bytes, expected_request_id: str) -> dict[str, object]:
    try:
        response = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RustEngineError(f"engine returned invalid JSON: {exc}") from exc
    if not isinstance(response, dict):
        raise RustEngineError("engine response must be a JSON object")
    version = response.get("protocol_version")
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise RustEngineError(f"engine response has unsupported protocol version: {version}")
    request_id = response.get("request_id")
    if request_id != expected_request_id:
        raise RustEngineError("engine response request_id does not match the request")
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("type"), str):
        raise RustEngineError("engine response result is malformed")
    if result["type"] == "error":
        code = result.get("code", "unknown")
        message = result.get("message", "engine request failed")
        raise RustEngineError(f"Rust engine {code}: {message}")
    return result


def _resolve_binary(binary_path: Path | None) -> Path:
    candidate = binary_path
    if candidate is None:
        configured = os.environ.get("LUMISTRIPE_ENGINE_BINARY")
        if configured:
            candidate = Path(configured)
    if candidate is None:
        located = shutil.which("lumistripe-engine")
        if located is not None:
            candidate = Path(located)
    if candidate is None:
        repository_binary = (
            Path(__file__).resolve().parents[5]
            / "rust"
            / "target"
            / "debug"
            / "lumistripe-engine"
        )
        if repository_binary.is_file():
            candidate = repository_binary
    if candidate is None:
        raise RustEngineError(
            "Rust backend selected but lumistripe-engine was not found; set "
            "LUMISTRIPE_ENGINE_BINARY or pass --engine-binary"
        )
    resolved = candidate.expanduser().resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RustEngineError(f"Rust engine binary is not executable: {resolved}")
    return resolved


class RustEngineProcess:
    """Own one engine subprocess and its private Unix-domain socket."""

    def __init__(self, *, binary_path: Path | None = None) -> None:
        self._binary_path = _resolve_binary(binary_path)
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="ls-")
        self._socket_path = Path(self._temporary_directory.name) / "engine.sock"
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._pixel_counts: tuple[int, ...] = ()
        try:
            self._process = subprocess.Popen(
                [
                    str(self._binary_path),
                    "--socket",
                    str(self._socket_path),
                    "--owner-pid",
                    str(os.getpid()),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self._wait_until_ready()
        except Exception:
            self.close()
            raise

    def health(self) -> EngineHealth:
        result = self._request("health")
        if result.get("type") != "health" or result.get("status") != "ready":
            raise RustEngineError("Rust engine did not report a ready health state")
        configured = _bool_field(result, "configured")
        output_count = _nonnegative_int_field(result, "output_count")
        pixel_counts_value = result.get("pixel_counts")
        if not isinstance(pixel_counts_value, list) or any(
            type(value) is not int or value <= 0 for value in pixel_counts_value
        ):
            raise RustEngineError("Rust engine health pixel_counts is invalid")
        pixel_counts = tuple(pixel_counts_value)
        if output_count != len(pixel_counts):
            raise RustEngineError("Rust engine health output_count is inconsistent")
        frames_rendered = _nonnegative_int_field(result, "frames_rendered")
        last_sequence_value = result.get("last_sequence")
        if last_sequence_value is not None and (
            type(last_sequence_value) is not int or last_sequence_value < 0
        ):
            raise RustEngineError("Rust engine health last_sequence is invalid")
        dropped_frames = _nonnegative_int_field(result, "dropped_frames")
        failed_frames = _nonnegative_int_field(result, "failed_frames")
        last_error_value = result.get("last_error")
        if last_error_value is not None and not isinstance(last_error_value, str):
            raise RustEngineError("Rust engine health last_error is invalid")
        output_rate = _number_field(result, "output_rate")
        frame_latency_ms = _optional_number_field(result, "frame_latency_ms")
        queue_latency_ms = _optional_number_field(result, "queue_latency_ms")
        transmission_ms = _optional_number_field(result, "transmission_ms")
        frame_latency_p50_ms = _optional_number_field(
            result, "frame_latency_p50_ms"
        )
        frame_latency_p95_ms = _optional_number_field(result, "frame_latency_p95_ms")
        frame_latency_p99_ms = _optional_number_field(
            result, "frame_latency_p99_ms"
        )
        sequential_skew_ms = _optional_number_field(result, "sequential_skew_ms")
        updated_ms = result.get("last_successful_update_epoch_ms")
        if updated_ms is not None and (type(updated_ms) is not int or updated_ms < 0):
            raise RustEngineError(
                "Rust engine health last_successful_update_epoch_ms is invalid"
            )
        updated_at = (
            datetime.fromtimestamp(updated_ms / 1000, tz=UTC).astimezone()
            if updated_ms is not None
            else None
        )
        return EngineHealth(
            status="ready",
            configured=configured,
            output_count=output_count,
            pixel_counts=pixel_counts,
            frames_rendered=frames_rendered,
            last_sequence=last_sequence_value,
            dropped_frames=dropped_frames,
            failed_frames=failed_frames,
            last_error=last_error_value,
            output_rate=output_rate,
            frame_latency_ms=frame_latency_ms,
            queue_latency_ms=queue_latency_ms,
            transmission_ms=transmission_ms,
            frame_latency_p50_ms=frame_latency_p50_ms,
            frame_latency_p95_ms=frame_latency_p95_ms,
            frame_latency_p99_ms=frame_latency_p99_ms,
            sequential_skew_ms=sequential_skew_ms,
            last_successful_update_at=updated_at,
        )

    def configure(self, outputs: Sequence[RustOutputSpec]) -> None:
        wire_outputs = [output.to_wire() for output in outputs]
        pixel_counts = tuple(output.pixel_count for output in outputs)
        result = self._request(
            "configure",
            {"config": {"outputs": wire_outputs}},
            timeout=_CONFIGURE_TIMEOUT_SECONDS,
        )
        if result.get("type") != "configured":
            raise RustEngineError("Rust engine rejected output configuration")
        if result.get("output_count") != len(outputs) or result.get("total_pixels") != sum(
            pixel_counts
        ):
            raise RustEngineError("Rust engine returned an invalid output configuration")
        self._pixel_counts = pixel_counts

    def open_frame_stream(self) -> socket.socket:
        if self._closed:
            raise RustEngineError("Rust engine process is closed")
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(_REQUEST_TIMEOUT_SECONDS)
        try:
            connection.connect(str(self._socket_path))
        except OSError as exc:
            connection.close()
            raise RustEngineError(f"Rust engine frame socket failed: {exc}") from exc
        return connection

    def send_frame_set(
        self,
        connection: socket.socket,
        sequence: int,
        frames: Sequence[PixelFrame],
    ) -> int:
        if len(frames) != len(self._pixel_counts):
            raise RustEngineError("frame count does not match Rust output configuration")
        payload_bytes = 0
        views: list[memoryview] = []
        for index, (frame, pixel_count) in enumerate(
            zip(frames, self._pixel_counts, strict=True)
        ):
            if frame.dtype != np.uint8 or frame.shape != (pixel_count, 4):
                raise RustEngineError(
                    f"frame {index} must be uint8 with shape {(pixel_count, 4)}"
                )
            if not frame.flags.c_contiguous:
                raise RustEngineError(f"frame {index} must be C-contiguous")
            view = memoryview(frame).cast("B")
            payload_bytes += view.nbytes
            views.append(view)
        header = FRAME_HEADER.pack(
            FRAME_MAGIC, PROTOCOL_VERSION, len(frames), sequence, payload_bytes
        )
        try:
            _send_vectors(connection, [memoryview(header), *views])
            ack_bytes = _read_exact(connection, FRAME_ACK.size)
        except (TimeoutError, OSError) as exc:
            raise RustEngineError(f"Rust engine frame IPC failed: {exc}") from exc
        magic, version, status, acknowledged_sequence, dropped = FRAME_ACK.unpack(
            ack_bytes
        )
        if magic != FRAME_ACK_MAGIC or version != PROTOCOL_VERSION:
            raise RustEngineError("Rust engine returned an invalid frame acknowledgement")
        if acknowledged_sequence != sequence:
            raise RustEngineError("Rust engine frame sequence does not match the request")
        if status != 0:
            messages = {
                1: "Rust engine has no configured outputs",
                2: "Rust engine is shutting down",
                3: "Rust engine rejected an invalid frame set",
            }
            raise RustEngineError(messages.get(status, f"unknown frame status {status}"))
        return dropped

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def close(self) -> None:
        if self._closed:
            return
        process = self._process
        if process is not None and process.poll() is None:
            try:
                self._request("shutdown", timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            except OSError:
                pass
            try:
                process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self._closed = True
        self._temporary_directory.cleanup()

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            process = self._process
            if process is None:
                raise RustEngineError("Rust engine process was not created")
            if process.poll() is not None:
                raise RustEngineError(
                    f"Rust engine exited during startup with code {process.returncode}"
                )
            try:
                self.health()
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.01)
        detail = f": {last_error}" if last_error is not None else ""
        raise RustEngineError(f"Rust engine did not become ready in time{detail}")

    def _request(
        self,
        command: str,
        data: JSONCommandData = None,
        *,
        timeout: float = _REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, object]:
        if self._closed:
            raise RustEngineError("Rust engine process is closed")
        request_id = str(uuid.uuid4())
        payload = _encode_request(request_id, command, data)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout)
                connection.connect(str(self._socket_path))
                connection.sendall(payload)
                response = bytearray()
                while not response.endswith(b"\n"):
                    chunk = connection.recv(
                        min(65_536, MAX_CONTROL_MESSAGE_BYTES + 1 - len(response))
                    )
                    if not chunk:
                        raise RustEngineError("Rust engine closed IPC before responding")
                    response.extend(chunk)
                    if len(response) > MAX_CONTROL_MESSAGE_BYTES:
                        raise RustEngineError("Rust engine control response exceeded the IPC limit")
        except (TimeoutError, OSError) as exc:
            if isinstance(exc, RustEngineError):
                raise
            raise RustEngineError(f"Rust engine IPC {command} failed: {exc}") from exc
        response.pop()
        return _decode_response(bytes(response), request_id)


@dataclass(slots=True)
class _ConfigureRequest:
    exchange: LatestFrameExchange | None
    output_lengths: tuple[int, ...]
    outputs: tuple[RustOutputSpec, ...]
    completed: Future[LatestFrameExchange | None]


class RustEngineOutputThread:
    """Bridge the Python latest-frame exchange to one Rust output process."""

    def __init__(
        self,
        output_lengths: Sequence[int],
        outputs: Sequence[RustOutputSpec],
        *,
        binary_path: Path | None = None,
    ) -> None:
        self._exchange = _make_exchange(output_lengths)
        self._output_lengths = tuple(output_lengths)
        self._outputs = tuple(outputs)
        self._binary_path = binary_path
        self._process: RustEngineProcess | None = None
        self._requests: Queue[_ConfigureRequest] = Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._started = threading.Event()
        self._startup_exception: Exception | None = None
        self._thread = threading.Thread(
            target=self._run, name="lumistripe-rust-output-bridge", daemon=True
        )
        self._state_lock = threading.Lock()
        self._heartbeat_at: float | None = None
        self._last_frame_at: float | None = None
        self._transmitted_frames = 0
        self._failed_flushes = 0
        self._last_error: str | None = None
        self._last_successful_update_at: datetime | None = None
        self._last_successful_update_monotonic: float | None = None
        self._frame_latency_ms: float | None = None
        self._queue_latency_ms: float | None = None
        self._transmission_ms: float | None = None
        self._frame_latency_p50_ms: float | None = None
        self._frame_latency_p95_ms: float | None = None
        self._frame_latency_p99_ms: float | None = None
        self._ipc_latency_ms: float | None = None
        self._sequential_skew_ms: float | None = None
        self._frame_latency_samples: deque[float] = deque(
            maxlen=OUTPUT_LATENCY_SAMPLE_COUNT
        )
        self._output_rate = 0.0
        self._last_health_frames = 0
        self._health_dropped_frames = 0
        self._health_failed_frames = 0
        self._health_next_poll_at = 0.0
        self._transport_failures = 0
        self._created_at_monotonic = time.monotonic()

    @property
    def exchange(self) -> LatestFrameExchange | None:
        return self._exchange

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        if not self._started.wait(timeout):
            raise TimeoutError("Rust output bridge did not initialize in time")
        if self._startup_exception is not None:
            raise self._startup_exception

    def reconfigure(
        self,
        output_lengths: Sequence[int],
        outputs: Sequence[RustOutputSpec],
        timeout: float = 10.0,
    ) -> LatestFrameExchange | None:
        return self.reconfigure_async(output_lengths, outputs).result(timeout)

    def reconfigure_async(
        self,
        output_lengths: Sequence[int],
        outputs: Sequence[RustOutputSpec],
    ) -> Future[LatestFrameExchange | None]:
        lengths = tuple(output_lengths)
        specs = tuple(outputs)
        if lengths != tuple(output.pixel_count for output in specs):
            raise ValueError("Rust output lengths must match their configuration")
        if self._stop_event.is_set() or not self._thread.is_alive():
            raise RuntimeError("Rust output bridge is not running")
        exchange = _make_exchange(lengths)
        completed: Future[LatestFrameExchange | None] = Future()
        try:
            self._requests.put_nowait(_ConfigureRequest(exchange, lengths, specs, completed))
        except Full:
            if exchange is not None:
                exchange.close(discard_pending=True)
            completed.set_exception(
                RuntimeError("a Rust output configuration is already pending")
            )
        return completed

    def stats(self) -> OutputThreadStats:
        with self._state_lock:
            heartbeat = self._heartbeat_at
            last_frame = self._last_frame_at
            transmitted = self._transmitted_frames
            failed = self._failed_flushes + self._health_failed_frames
            rate = self._output_rate
            error = self._last_error
            successful_at = self._last_successful_update_at
            successful_monotonic = self._last_successful_update_monotonic
            frame_latency = self._frame_latency_ms
            queue_latency = self._queue_latency_ms
            transmission = self._transmission_ms
            frame_latency_p50 = self._frame_latency_p50_ms
            frame_latency_p95 = self._frame_latency_p95_ms
            frame_latency_p99 = self._frame_latency_p99_ms
            ipc_latency = self._ipc_latency_ms
            sequential_skew = self._sequential_skew_ms
            latency_samples = tuple(self._frame_latency_samples)
            health_dropped = self._health_dropped_frames
        exchange = self._exchange
        exchange_dropped = exchange.stats().dropped_count if exchange else 0
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
            dropped_frames=exchange_dropped + health_dropped,
            last_successful_update_at=successful_at,
            last_successful_update_age_seconds=successful_age,
            frame_latency_ms=frame_latency,
            queue_latency_ms=queue_latency,
            transmission_ms=transmission,
            frame_latency_p50_ms=frame_latency_p50
            if frame_latency_p50 is not None
            else _nearest_rank_percentile(latency_samples, 0.50),
            frame_latency_p95_ms=frame_latency_p95
            if frame_latency_p95 is not None
            else _nearest_rank_percentile(latency_samples, 0.95),
            frame_latency_p99_ms=frame_latency_p99
            if frame_latency_p99 is not None
            else _nearest_rank_percentile(latency_samples, 0.99),
            ipc_latency_ms=ipc_latency,
            sequential_skew_ms=sequential_skew,
            frame_latency_sample_count=len(latency_samples),
        )

    def is_healthy(self, *, max_heartbeat_age_seconds: float) -> bool:
        if not self._thread.is_alive():
            return False
        with self._state_lock:
            heartbeat = self._heartbeat_at
            last_frame = self._last_frame_at
            transmitted = self._transmitted_frames
        now = time.monotonic()
        if heartbeat is None or now - heartbeat > max_heartbeat_age_seconds:
            return False
        exchange = self._exchange
        exchange_stats = exchange.stats() if exchange is not None else None
        submitted = exchange_stats.submitted_count if exchange_stats is not None else 0
        if submitted == 0:
            return True
        if transmitted == 0:
            return now - self._created_at_monotonic <= max_heartbeat_age_seconds
        return last_frame is not None and now - last_frame <= max_heartbeat_age_seconds

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        exchange = self._exchange
        if exchange is not None:
            exchange.close(discard_pending=True)
        self._thread.join(timeout + _SHUTDOWN_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            raise TimeoutError("Rust output bridge did not stop in time")

    @property
    def process_is_running(self) -> bool:
        process = getattr(self, "_process", None)
        return process.is_running if process is not None else False

    def _run(self) -> None:
        process: RustEngineProcess | None = None
        frame_connection: socket.socket | None = None
        try:
            process = RustEngineProcess(binary_path=self._binary_path)
            process.configure(self._outputs)
            frame_connection = process.open_frame_stream()
            self._process = process
            self._record_heartbeat()
            self._started.set()
            while not self._stop_event.is_set():
                request = self._next_request()
                if request is not None:
                    self._apply_configuration(request, process)
                    continue
                exchange = self._exchange
                if exchange is not None:
                    slot = exchange.take(timeout=0.02)
                else:
                    slot = None
                    self._stop_event.wait(0.02)
                self._record_heartbeat()
                if slot is not None and exchange is not None:
                    try:
                        ipc_started_at = time.monotonic()
                        process.send_frame_set(frame_connection, slot.sequence, slot.frames)
                        with self._state_lock:
                            self._ipc_latency_ms = (
                                time.monotonic() - ipc_started_at
                            ) * 1000.0
                            self._last_error = None
                    except Exception as exc:
                        self._record_error(exc)
                        if not process.is_running:
                            raise
                    finally:
                        exchange.release(slot)
                if time.monotonic() >= self._health_next_poll_at:
                    try:
                        self._record_engine_health(process.health())
                    except OSError as exc:
                        self._record_error(exc)
                        if not process.is_running:
                            raise
                    self._health_next_poll_at = time.monotonic() + _HEALTH_POLL_SECONDS
        except Exception as exc:  # noqa: BLE001 - the runtime watchdog observes a dead bridge
            if not self._started.is_set():
                self._startup_exception = exc
                self._started.set()
            else:
                self._record_error(exc)
        finally:
            exchange = self._exchange
            if exchange is not None:
                exchange.close(discard_pending=True)
            if frame_connection is not None:
                frame_connection.close()
            if process is not None:
                process.close()
            self._process = None
            self._fail_pending_requests()

    def _next_request(self) -> _ConfigureRequest | None:
        try:
            return self._requests.get_nowait()
        except Empty:
            return None

    def _apply_configuration(
        self, request: _ConfigureRequest, process: RustEngineProcess
    ) -> None:
        current = self._exchange
        if current is not None:
            current.close(discard_pending=True)
        try:
            process.configure(request.outputs)
            baseline = process.health()
            self._exchange = request.exchange
            self._output_lengths = request.output_lengths
            self._outputs = request.outputs
            configured_at = time.monotonic()
            self._created_at_monotonic = configured_at
            self._health_next_poll_at = 0.0
            with self._state_lock:
                self._last_health_frames = baseline.frames_rendered
                self._transmitted_frames = baseline.frames_rendered
                self._last_frame_at = configured_at
                self._output_rate = baseline.output_rate
                self._frame_latency_ms = baseline.frame_latency_ms
                self._queue_latency_ms = baseline.queue_latency_ms
                self._transmission_ms = baseline.transmission_ms
                self._frame_latency_p50_ms = baseline.frame_latency_p50_ms
                self._frame_latency_p95_ms = baseline.frame_latency_p95_ms
                self._frame_latency_p99_ms = baseline.frame_latency_p99_ms
                self._sequential_skew_ms = baseline.sequential_skew_ms
                self._last_successful_update_at = baseline.last_successful_update_at
                self._last_successful_update_monotonic = None
                self._frame_latency_samples.clear()
                self._last_error = None
            request.completed.set_result(request.exchange)
        except Exception as exc:  # noqa: BLE001 - worker may roll back configuration
            self._exchange = None
            self._record_error(exc)
            request.completed.set_exception(exc)
        self._record_heartbeat()

    def _record_engine_health(self, health: EngineHealth) -> None:
        now = time.monotonic()
        with self._state_lock:
            has_new_frame = health.frames_rendered > self._last_health_frames
            if has_new_frame:
                self._last_frame_at = now
                self._last_health_frames = health.frames_rendered
            self._transmitted_frames = health.frames_rendered
            self._health_dropped_frames = health.dropped_frames
            self._health_failed_frames = health.failed_frames
            self._output_rate = health.output_rate
            self._frame_latency_ms = health.frame_latency_ms
            self._queue_latency_ms = health.queue_latency_ms
            self._transmission_ms = health.transmission_ms
            self._frame_latency_p50_ms = health.frame_latency_p50_ms
            self._frame_latency_p95_ms = health.frame_latency_p95_ms
            self._frame_latency_p99_ms = health.frame_latency_p99_ms
            self._sequential_skew_ms = health.sequential_skew_ms
            if has_new_frame and health.frame_latency_ms is not None:
                self._frame_latency_samples.append(health.frame_latency_ms)
            if health.last_successful_update_at is not None:
                self._last_successful_update_at = health.last_successful_update_at
                age_seconds = max(
                    0.0,
                    datetime.now().astimezone().timestamp()
                    - health.last_successful_update_at.timestamp(),
                )
                self._last_successful_update_monotonic = now - age_seconds
            if health.last_error is not None:
                self._last_error = health.last_error
            elif self._transport_failures == 0:
                self._last_error = None
            self._heartbeat_at = now

    def _record_heartbeat(self) -> None:
        with self._state_lock:
            self._heartbeat_at = time.monotonic()

    def _record_error(self, exc: Exception) -> None:
        detail = str(exc).strip().replace("\n", " ")
        with self._state_lock:
            self._transport_failures += 1
            self._failed_flushes += 1
            self._last_error = f"{type(exc).__name__}: {detail}"[:256]
            self._heartbeat_at = time.monotonic()

    def _fail_pending_requests(self) -> None:
        while True:
            try:
                request = self._requests.get_nowait()
            except Empty:
                return
            try:
                request.completed.set_exception(
                    RuntimeError("Rust output bridge stopped")
                )
            except InvalidStateError:
                continue


def _make_exchange(output_lengths: Sequence[int]) -> LatestFrameExchange | None:
    lengths = tuple(output_lengths)
    return LatestFrameExchange(lengths) if lengths else None


def _send_vectors(connection: socket.socket, vectors: list[memoryview]) -> None:
    pending = vectors
    while pending:
        sent = connection.sendmsg(pending)
        if sent <= 0:
            raise RustEngineError("Rust engine frame socket closed during write")
        while pending and sent >= pending[0].nbytes:
            sent -= pending[0].nbytes
            pending.pop(0)
        if pending and sent:
            pending[0] = pending[0][sent:]


def _read_exact(connection: socket.socket, length: int) -> bytes:
    result = bytearray(length)
    view = memoryview(result)
    offset = 0
    while offset < length:
        read = connection.recv_into(view[offset:])
        if read == 0:
            raise RustEngineError("Rust engine closed the frame socket before acknowledging")
        offset += read
    return bytes(result)


def _bool_field(value: dict[str, object], key: str) -> bool:
    result = value.get(key)
    if type(result) is not bool:
        raise RustEngineError(f"Rust engine health {key} is invalid")
    return result


def _nonnegative_int_field(value: dict[str, object], key: str) -> int:
    result = value.get(key)
    if type(result) is not int or result < 0:
        raise RustEngineError(f"Rust engine health {key} is invalid")
    return result


def _number_field(value: dict[str, object], key: str) -> float:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise RustEngineError(f"Rust engine health {key} is invalid")
    number = float(result)
    if not np.isfinite(number) or number < 0.0:
        raise RustEngineError(f"Rust engine health {key} is invalid")
    return number


def _optional_number_field(value: dict[str, object], key: str) -> float | None:
    result = value.get(key)
    if result is None:
        return None
    return _number_field(value, key)


def _nearest_rank_percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(len(ordered) * quantile) - 1))
    return ordered[index]


__all__ = [
    "EngineHealth",
    "RustEngineError",
    "RustEngineOutputThread",
    "RustEngineProcess",
    "RustOutputSpec",
]
