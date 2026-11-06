#!/usr/bin/env python3
"""Run a bounded, opt-in software stability profile against the simulator."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _http(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: Any = None,
    cookie: str | None = None,
    return_cookie: bool = False,
) -> Any:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(
        f"{base}{path}",
        data=payload,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        data = response.read()
        if response.status != 200:
            raise RuntimeError(f"HTTP {method} {path} returned {response.status}")
        set_cookie = response.headers.get("Set-Cookie", "")
    result = json.loads(data) if data else None
    return (result, set_cookie) if return_cookie else result


def _wait_health(base: str, process: subprocess.Popen[str], timeout: float = 25) -> None:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"simulator exited before health ({process.returncode})")
        try:
            if _http(base, "/api/health").get("status") == "ok":
                return
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = str(error)
        time.sleep(0.1)
    raise RuntimeError(f"simulator did not become healthy: {last_error}")


def _proc_metrics(pid: int) -> dict[str, int | float]:
    proc = Path("/proc") / str(pid)
    status: dict[str, int] = {}
    for line in (proc / "status").read_text(encoding="ascii").splitlines():
        if line.startswith("VmRSS:"):
            status["rss_bytes"] = int(line.split()[1]) * 1024
        elif line.startswith("Threads:"):
            status["threads"] = int(line.split()[1])
    stat_text = (proc / "stat").read_text(encoding="ascii")
    stat_fields = stat_text[stat_text.rfind(")") + 2 :].split()
    ticks = int(stat_fields[11]) + int(stat_fields[12])
    return {
        "cpu_seconds": ticks / os.sysconf("SC_CLK_TCK"),
        "rss_bytes": status.get("rss_bytes", 0),
        "threads": status.get("threads", 0),
        "file_descriptors": len(list((proc / "fd").iterdir())),
    }


class _MockSpotify:
    """Loopback-only Soloist WebSocket fixture with playback and disconnect cycles."""

    def __init__(self) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(0.25)
        self.uri = f"ws://127.0.0.1:{self.listener.getsockname()[1]}"
        self.stop_event = threading.Event()
        self.connections = 0
        self.transitions = 0
        self.interruptions = 0
        self.thread = threading.Thread(target=self._serve, name="mock-spotify", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.listener.close()
        self.thread.join(timeout=3)

    @staticmethod
    def _text_frame(payload: bytes) -> bytes:
        size = len(payload)
        if size < 126:
            header = bytes((0x81, size))
        elif size <= 0xFFFF:
            header = bytes((0x81, 126)) + struct.pack("!H", size)
        else:
            header = bytes((0x81, 127)) + struct.pack("!Q", size)
        return header + payload

    def _handshake(self, connection: socket.socket) -> bool:
        connection.settimeout(2)
        request = bytearray()
        while b"\r\n\r\n" not in request:
            chunk = connection.recv(4096)
            if not chunk:
                return False
            request.extend(chunk)
            if len(request) > 16_384:
                return False
        key = ""
        for line in bytes(request).decode("latin-1").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
                break
        if not key:
            return False
        accept = base64.b64encode(
            hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        connection.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii")
        )
        connection.settimeout(0.1)
        return True

    def _serve_client(self, connection: socket.socket) -> None:
        if not self._handshake(connection):
            return
        self.connections += 1
        connection_number = self.connections
        for cycle in range(8):
            if self.stop_event.is_set():
                return
            status = "playing" if cycle % 2 == 0 else "paused"
            events = [
                {
                    "type": "auth_state",
                    "logged_in": cycle % 5 != 4,
                    "is_active": True,
                    "device_name": "LumiStripe stability fixture",
                },
                {
                    "type": "playback_state",
                    "status": status,
                    "position": {"position_ms": cycle * 500, "speed": 1 if status == "playing" else 0},
                    "item": {
                        "uri": "spotify:track:software-stability-fixture",
                        "decorations": {"identity": {"name": "Software fixture"}},
                    },
                },
            ]
            try:
                for event in events:
                    connection.sendall(self._text_frame(json.dumps(event).encode("utf-8")))
                    self.transitions += 1
            except OSError:
                return
            if cycle == 3 and connection_number % 2 == 0:
                self.interruptions += 1
                return
            time.sleep(0.35)

    def _serve(self) -> None:
        while not self.stop_event.is_set():
            try:
                connection, _address = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                try:
                    self._serve_client(connection)
                except (OSError, TimeoutError):
                    continue


class _AudioSocket:
    def __init__(self, address: tuple[str, int], cookie: str) -> None:
        self.socket = socket.create_connection(address, timeout=4)
        self.socket.settimeout(4)
        self.buffer = bytearray()
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET /ws/audio HTTP/1.1\r\nHost: {address[0]}:{address[1]}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"Origin: http://{address[0]}:{address[1]}\r\n"
            f"Cookie: {cookie}\r\n\r\n"
        )
        self.socket.sendall(request.encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response:
            response.extend(self.socket.recv(4096))
        header, remainder = bytes(response).split(b"\r\n\r\n", 1)
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"audio telemetry WebSocket rejected upgrade: {header!r}")
        self.buffer.extend(remainder)

    def _fill(self, size: int, *, allow_timeout: bool = False) -> bool:
        while len(self.buffer) < size:
            try:
                chunk = self.socket.recv(max(4096, size - len(self.buffer)))
            except TimeoutError:
                if allow_timeout and not self.buffer:
                    return False
                raise
            if not chunk:
                return False
            self.buffer.extend(chunk)
        return True

    def receive(self, timeout: float = 0.5) -> dict[str, Any] | None:
        self.socket.settimeout(timeout)
        if not self._fill(2, allow_timeout=True):
            return None
        first, second = self.buffer[0], self.buffer[1]
        del self.buffer[:2]
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            self._fill(2)
            length = struct.unpack("!H", self.buffer[:2])[0]
            del self.buffer[:2]
        elif length == 127:
            self._fill(8)
            length = struct.unpack("!Q", self.buffer[:8])[0]
            del self.buffer[:8]
        masked = bool(second & 0x80)
        mask = b""
        if masked:
            self._fill(4)
            mask = bytes(self.buffer[:4])
            del self.buffer[:4]
        self._fill(length)
        payload = bytes(self.buffer[:length])
        del self.buffer[:length]
        if masked:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        if opcode == 9:
            self._send_pong(payload)
            return None
        if opcode == 8:
            return None
        if opcode != 1:
            return None
        decoded = json.loads(payload)
        return decoded if isinstance(decoded, dict) else None

    def _send_pong(self, payload: bytes) -> None:
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.socket.sendall(bytes((0x8A, 0x80 | len(payload))) + mask + masked)

    def close(self) -> None:
        self.socket.close()


def _run_process(
    binary: Path, *, spotify_uri: str | None = None
) -> tuple[
    subprocess.Popen[str],
    tempfile.TemporaryFile[str],
    int,
    tempfile.TemporaryDirectory[str],
]:
    temporary = tempfile.TemporaryDirectory(prefix="lumistripe-stability-")
    root = Path(temporary.name)
    port = _free_port()
    command = [
        str(binary),
        "--simulate",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--settings-file",
        str(root / "settings.json"),
        "--spotify-backend",
        "soloist" if spotify_uri else "disabled",
    ]
    if spotify_uri:
        command.extend(["--spotify-ws", spotify_uri])
    # The caller owns the log for the full child-process lifetime.
    log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")  # noqa: SIM115
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(root),
            "XDG_CONFIG_HOME": str(root),
            "LUMI_SPOTIFY_BACKEND": "soloist" if spotify_uri else "disabled",
            "LUMI_PAIRING_CODE": "2468",
            "RUST_LOG": "warn",
        }
    )
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
        start_new_session=True,
    )
    return process, log, port, temporary


def _stop_process(process: subprocess.Popen[str], log: tempfile.TemporaryFile[str]) -> str:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        result = process.wait(timeout=15)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait(timeout=5)
        log.seek(0)
        raise RuntimeError("simulator did not shut down within 15 seconds") from error
    log.seek(0)
    output = log.read()
    if result != 0:
        raise RuntimeError(f"simulator shutdown returned {result}:\n{output[-4000:]}")
    return output


def run_profile(binary: Path, duration_seconds: int) -> dict[str, Any]:
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"simulator binary is missing or not executable: {binary}")

    spotify = _MockSpotify()
    spotify.start()
    process: subprocess.Popen[str] | None = None
    log: tempfile.TemporaryFile[str] | None = None
    config_root: tempfile.TemporaryDirectory[str] | None = None
    audio: _AudioSocket | None = None
    started_at = time.monotonic()
    samples: list[dict[str, int | float]] = []
    operations = {"api_requests": 0, "animation_changes": 0, "parameter_changes": 0, "audio_source_changes": 0}
    api_errors = 0
    api_error_examples: list[str] = []
    max_audio_queue_drops = 0
    max_render_queue_drops = 0
    audio_messages = 0
    state_errors = 0
    spotify_states: set[tuple[bool, bool, bool, str]] = set()
    try:
        process, log, port, config_root = _run_process(binary, spotify_uri=spotify.uri)
        base = f"http://127.0.0.1:{port}"
        _wait_health(base, process)
        paired, set_cookie = _http(
            base,
            "/api/auth/pair",
            method="POST",
            body={"code": "2468"},
            return_cookie=True,
        )
        cookie = set_cookie.split(";", 1)[0]
        if not isinstance(paired, dict) or not paired.get("authenticated") or not cookie:
            raise RuntimeError("simulator pairing did not establish an authenticated session")
        audio = _AudioSocket(("127.0.0.1", port), cookie)

        animations = _http(base, "/api/animations", cookie=cookie)["items"]
        names = [item["name"] for item in animations if isinstance(item, dict) and item.get("name")]
        if not names:
            raise RuntimeError("animation catalog returned no selectable animations")
        animation_settings = _http(
            base,
            "/api/animations/rainbow_cycle/settings",
            cookie=cookie,
        )
        animation_values = animation_settings.get("values")
        if not isinstance(animation_values, dict) or "speed" not in animation_values:
            raise RuntimeError("rainbow_cycle settings did not expose a speed control")
        _http(base, "/api/mode", method="PUT", body={"mode": "dynamic"}, cookie=cookie)
        _http(base, "/api/audio/source", method="PUT", body={"source": "demo"}, cookie=cookie)
        operations["api_requests"] += 4

        deadline = time.monotonic() + duration_seconds
        iteration = 0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"simulator exited during stability profile ({process.returncode})")
            endpoint = ""
            try:
                name = names[iteration % len(names)]
                endpoint = "/api/animation"
                _http(base, endpoint, method="PUT", body={"name": name}, cookie=cookie)
                operations["animation_changes"] += 1
                operations["api_requests"] += 1

                endpoint = "/api/animations/rainbow_cycle/settings"
                values = animation_values.copy()
                values["speed"] = 35 + (iteration % 50)
                _http(
                    base,
                    endpoint,
                    method="PUT",
                    body={"values": values},
                    cookie=cookie,
                )
                operations["parameter_changes"] += 1
                operations["api_requests"] += 1

                source = "off" if iteration % 4 == 3 else "demo"
                endpoint = "/api/audio/source"
                _http(base, endpoint, method="PUT", body={"source": source}, cookie=cookie)
                operations["audio_source_changes"] += 1
                operations["api_requests"] += 1

                endpoint = "/api/state"
                state = _http(base, endpoint, cookie=cookie)
                operations["api_requests"] += 1
                if state.get("error"):
                    state_errors += 1
                max_render_queue_drops = max(
                    max_render_queue_drops,
                    int(state.get("output_dropped_frame_count", 0)),
                )
            except (OSError, urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as error:
                api_errors += 1
                if len(api_error_examples) < 5:
                    api_error_examples.append(f"{endpoint}: {error}")

            if audio is not None:
                try:
                    telemetry = audio.receive(timeout=0.25)
                    if telemetry is not None:
                        audio_messages += 1
                        ingress = telemetry.get("pcm_ingress") or {}
                        max_audio_queue_drops = max(
                            max_audio_queue_drops,
                            int(ingress.get("dropped_samples", 0)),
                        )
                except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
                    pass

            try:
                endpoint = "/api/audio/spotify"
                spotify_status = _http(base, endpoint, cookie=cookie)
                if isinstance(spotify_status, dict):
                    spotify_states.add(
                        (
                            bool(spotify_status.get("connected")),
                            bool(spotify_status.get("logged_in")),
                            bool(spotify_status.get("is_active")),
                            str(spotify_status.get("status", "unknown")),
                        )
                    )
                operations["api_requests"] += 1
            except (OSError, urllib.error.URLError, TimeoutError, ValueError) as error:
                api_errors += 1
                if len(api_error_examples) < 5:
                    api_error_examples.append(f"{endpoint}: {error}")

            if iteration % 2 == 0:
                samples.append(_proc_metrics(process.pid))
            iteration += 1
            time.sleep(0.75)

        if audio is not None:
            audio.close()
            audio = None
        _stop_process(process, log)
        process = None
        log.close()
        log = None
        assert config_root is not None
        config_root.cleanup()
        config_root = None

        rapid_start_stop = 0
        for _ in range(2):
            quick_process, quick_log, quick_port, quick_root = _run_process(binary)
            try:
                _wait_health(f"http://127.0.0.1:{quick_port}", quick_process, timeout=15)
                _stop_process(quick_process, quick_log)
                rapid_start_stop += 1
            finally:
                if quick_process.poll() is None:
                    quick_process.kill()
                    quick_process.wait(timeout=5)
                quick_log.close()
                quick_root.cleanup()

        if api_errors or state_errors:
            raise RuntimeError(
                f"stability profile observed {api_errors} API errors and "
                f"{state_errors} dashboard state errors; samples: "
                + "; ".join(api_error_examples)
            )
        if audio_messages == 0:
            raise RuntimeError("stability profile received no audio telemetry")
        if spotify.connections < 2 or spotify.interruptions == 0:
            raise RuntimeError(
                "mock Spotify did not complete a reconnect after a simulated network interruption"
            )
        observed_login_states = {state[1] for state in spotify_states}
        observed_playback_states = {state[3] for state in spotify_states}
        if observed_login_states != {False, True} or not {"playing", "paused"}.issubset(
            observed_playback_states
        ):
            raise RuntimeError(
                "mock Spotify transitions did not reach both login and playback states; "
                f"observed {len(spotify_states)} API states"
            )

        if not samples:
            raise RuntimeError("stability profile did not collect process resource samples")
        cpu = [float(sample["cpu_seconds"]) for sample in samples]
        return {
            "duration_seconds": round(time.monotonic() - started_at, 2),
            "operations": operations,
            "resource_samples": len(samples),
            "cpu_seconds_start_to_end": round(cpu[-1] - cpu[0], 3),
            "rss_bytes_peak": max(int(sample["rss_bytes"]) for sample in samples),
            "thread_count_peak": max(int(sample["threads"]) for sample in samples),
            "file_descriptors_peak": max(int(sample["file_descriptors"]) for sample in samples),
            "audio_telemetry_messages": audio_messages,
            "audio_queue_dropped_samples_max": max_audio_queue_drops,
            "render_queue_drops_max": max_render_queue_drops,
            "api_error_count": api_errors,
            "state_error_count": state_errors,
            "mock_spotify_connections": spotify.connections,
            "mock_spotify_state_transitions": spotify.transitions,
            "mock_spotify_api_states": len(spotify_states),
            "simulated_network_interruptions": spotify.interruptions,
            "rapid_start_stop_cycles": rapid_start_stop,
            "task_termination": "clean",
        }
    finally:
        if audio is not None:
            audio.close()
        try:
            if process is not None and log is not None:
                try:
                    _stop_process(process, log)
                finally:
                    log.close()
            elif log is not None:
                log.close()
        finally:
            if config_root is not None:
                config_root.cleanup()
            spotify.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 30 <= args.duration_seconds <= 1800:
        parser.error("--duration-seconds must be between 30 and 1800")
    try:
        report = run_profile(args.binary.resolve(), args.duration_seconds)
        serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        sys.stdout.write(serialized)
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
        print(f"stability profile failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
