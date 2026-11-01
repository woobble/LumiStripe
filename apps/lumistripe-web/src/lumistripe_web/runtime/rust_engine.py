"""Process-backed simulated output adapter for the optional Rust engine."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TypeAlias

import numpy.typing as npt
from lumistripe import Color, Controller, PixelBuffer, Rgba
from lumistripe.buffers import as_pixel_buffer, new_pixel_buffer

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1_048_576
_STARTUP_TIMEOUT_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 1.0
_JSONCommandData: TypeAlias = dict[str, object] | None


class RustEngineError(OSError):
    """The optional Rust process or its local IPC protocol failed."""


@dataclass(frozen=True, slots=True)
class EngineHealth:
    status: str
    pixel_count: int | None
    frames_rendered: int


def _encode_request(
    request_id: str, command: str, data: _JSONCommandData = None
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
    if len(payload) > MAX_MESSAGE_BYTES:
        raise RustEngineError(
            f"engine IPC request is {len(payload)} bytes; maximum is {MAX_MESSAGE_BYTES}"
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

    def __init__(self, pixel_count: int, *, binary_path: Path | None = None) -> None:
        self._binary_path = _resolve_binary(binary_path)
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="ls-")
        self._socket_path = Path(self._temporary_directory.name) / "engine.sock"
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        try:
            self._process = subprocess.Popen(
                [str(self._binary_path), "--socket", str(self._socket_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self._wait_until_ready()
            self.configure(pixel_count)
        except Exception:
            self.close()
            raise

    def health(self) -> EngineHealth:
        result = self._request("health")
        if result.get("type") != "health" or result.get("status") != "ready":
            raise RustEngineError("Rust engine did not report a ready health state")
        pixel_count_value = result.get("pixel_count")
        pixel_count = (
            pixel_count_value
            if type(pixel_count_value) is int
            else None
            if pixel_count_value is None
            else _invalid_health_value("pixel_count")
        )
        frames_rendered = result.get("frames_rendered")
        if type(frames_rendered) is not int or frames_rendered < 0:
            raise RustEngineError("Rust engine health frames_rendered is invalid")
        return EngineHealth("ready", pixel_count, frames_rendered)

    def configure(self, pixel_count: int) -> None:
        result = self._request("configure", {"config": {"pixel_count": pixel_count}})
        if result.get("type") != "configured" or result.get("pixel_count") != pixel_count:
            raise RustEngineError("Rust engine rejected output configuration")

    def render(self, pixels: list[list[int]]) -> int:
        result = self._request("render", {"pixels": pixels})
        if result.get("type") != "rendered":
            raise RustEngineError("Rust engine did not acknowledge the frame")
        frame_index = result.get("frame_index")
        if type(frame_index) is not int or frame_index <= 0:
            raise RustEngineError("Rust engine returned an invalid frame index")
        return frame_index

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
                self._request("shutdown", timeout=0.25)
            except OSError:
                pass
            try:
                process.wait(timeout=0.75)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=0.75)
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
        data: _JSONCommandData = None,
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
                    chunk = connection.recv(min(65_536, MAX_MESSAGE_BYTES + 1 - len(response)))
                    if not chunk:
                        raise RustEngineError("Rust engine closed IPC before responding")
                    response.extend(chunk)
                    if len(response) > MAX_MESSAGE_BYTES:
                        raise RustEngineError("Rust engine response exceeded the IPC limit")
        except (TimeoutError, OSError) as exc:
            if isinstance(exc, RustEngineError):
                raise
            raise RustEngineError(f"Rust engine IPC {command} failed: {exc}") from exc
        response.pop()
        return _decode_response(bytes(response), request_id)


class RustEngineController(Controller):
    """Controller implementation that forwards complete RGB frames to Rust."""

    def __init__(self, length: int, *, binary_path: Path | None = None) -> None:
        if length <= 0:
            raise ValueError("length must be greater than zero")
        self._pixels = new_pixel_buffer(length)
        self._engine = RustEngineProcess(length, binary_path=binary_path)

    @property
    def length(self) -> int:
        return int(self._pixels.shape[0])

    def pixels(self) -> PixelBuffer:
        return self._pixels

    def pixel(self, index: int) -> Color:
        self._check_index(index)
        red, green, blue, alpha = self._pixels[index]
        return Rgba(int(red), int(green), int(blue), float(alpha) / 255.0)

    def set_pixel(self, index: int, color: Color) -> None:
        self._check_index(index)
        self._pixels[index] = color.as_rgba_array()

    def set_pixels(self, colors: Sequence[Color] | npt.ArrayLike) -> None:
        normalized = as_pixel_buffer(colors)
        if normalized.shape[0] > self.length:
            raise ValueError(
                f"too many pixels: got {normalized.shape[0]}, controller length is {self.length}"
            )
        self._pixels[: normalized.shape[0]] = normalized

    def fill(self, color: Color) -> None:
        self._pixels[:] = color.as_rgba_array()

    def clear(self) -> None:
        self._pixels[:, :3] = 0
        self._pixels[:, 3] = 255

    def flush(self) -> None:
        rgb = self._pixels[:, :3].tolist()
        self._engine.render(rgb)

    def force_flush(self) -> None:
        self.flush()

    def close(self) -> None:
        self._engine.close()

    def health(self) -> EngineHealth:
        return self._engine.health()

    @property
    def process_is_running(self) -> bool:
        return self._engine.is_running

    def _check_index(self, index: int) -> None:
        if not 0 <= index < self.length:
            raise IndexError(f"pixel index {index} out of bounds for length {self.length}")


def _invalid_health_value(field_name: str) -> NoReturn:
    raise RustEngineError(f"Rust engine health {field_name} is invalid")


__all__ = [
    "EngineHealth",
    "RustEngineController",
    "RustEngineError",
    "RustEngineProcess",
]
