"""Typed client for the local Spotify Soloist WebSocket API."""

from __future__ import annotations

import json
import logging
import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from websockets.sync.client import connect

logger = logging.getLogger(__name__)

SpotifyPlaybackStatus = Literal["idle", "playing", "paused", "buffering"]
SpotifyRepeat = Literal["off", "context", "track"]


@dataclass(frozen=True, slots=True)
class SpotifyTrack:
    uri: str = ""
    name: str = ""
    artists: tuple[str, ...] = ()
    album: str | None = None
    cover_url: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class SpotifyStatus:
    configured: bool = False
    connected: bool = False
    logged_in: bool = False
    is_active: bool = False
    device_name: str | None = None
    status: SpotifyPlaybackStatus = "idle"
    track: SpotifyTrack | None = None
    position_ms: int = 0
    duration_ms: int | None = None
    volume: int = 0
    shuffle: bool = False
    repeat: SpotifyRepeat = "off"
    error: str | None = None


class SpotifyCommandError(RuntimeError):
    """A Spotify Soloist command could not be delivered."""


class SpotifyBackend(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def status(self) -> SpotifyStatus: ...

    def control(self, action: str, value: object | None = None) -> SpotifyStatus: ...


@dataclass(slots=True)
class _PendingCommand:
    payload: dict[str, Any]
    future: Future[None]


class SpotifyClient:
    """Reconnect to Soloist and keep its latest state as an immutable snapshot.

    The WebSocket reader owns the socket. Runtime commands are queued so API
    threads and the LumiStripe worker never write to a socket concurrently.
    """

    def __init__(
        self,
        uri: str = "ws://127.0.0.1:9090",
        *,
        enabled: bool = True,
        reconnect_delay: float = 2.0,
    ) -> None:
        self._uri = uri
        self._enabled = enabled
        self._reconnect_delay = max(0.25, reconnect_delay)
        self._state_lock = threading.RLock()
        self._state = SpotifyStatus(configured=enabled)
        self._commands: queue.Queue[_PendingCommand] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: Any = None
        self._socket_lock = threading.Lock()

    def start(self) -> None:
        if not self._enabled or (
            self._thread is not None and self._thread.is_alive()
        ):
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="lumistripe-spotify",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._socket_lock:
            socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception as exc:  # noqa: BLE001 - shutdown must continue
                logger.debug("could not close Spotify Soloist socket: %s", exc)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        self._fail_queued(SpotifyCommandError("Spotify client stopped"))
        with self._state_lock:
            self._state = replace(
                self._state,
                connected=False,
                error=None if not self._enabled else self._state.error,
            )

    def status(self) -> SpotifyStatus:
        with self._state_lock:
            return self._state

    def control(self, action: str, value: object | None = None) -> SpotifyStatus:
        if not self._enabled:
            raise SpotifyCommandError("Spotify is unavailable in this runtime")
        if self._thread is None or not self._thread.is_alive():
            raise SpotifyCommandError("Spotify Soloist is not connected")
        payloads = control_payloads(action, value)
        for payload in payloads:
            future: Future[None] = Future()
            self._commands.put(_PendingCommand(payload, future))
            try:
                future.result(timeout=3.0)
            except Exception as exc:
                if isinstance(exc, SpotifyCommandError):
                    raise
                raise SpotifyCommandError(str(exc)) from exc
        return self.status()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                with connect(self._uri, open_timeout=1.5, close_timeout=1.0) as socket:
                    with self._socket_lock:
                        self._socket = socket
                    self._set_state(connected=True, error=None)
                    self._receive_loop(socket)
            except Exception as exc:  # noqa: BLE001 - third-party socket errors vary
                if not self._stop_event.is_set():
                    logger.debug("Spotify Soloist connection unavailable: %s", exc)
                    self._set_state(connected=False, error=str(exc))
            finally:
                with self._socket_lock:
                    self._socket = None
                self._fail_queued(SpotifyCommandError("Spotify Soloist is disconnected"))
                self._set_state(connected=False)
            self._stop_event.wait(self._reconnect_delay)

    def _receive_loop(self, socket: Any) -> None:
        while not self._stop_event.is_set():
            self._send_queued(socket)
            try:
                message = socket.recv(timeout=0.25)
            except TimeoutError:
                continue
            if message is None:
                return
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            try:
                event = json.loads(message)
            except (TypeError, json.JSONDecodeError) as exc:
                self._set_state(error=f"invalid Soloist event: {exc}")
                continue
            if isinstance(event, dict):
                self._apply_event(event)

    def _send_queued(self, socket: Any) -> None:
        while True:
            try:
                pending = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                socket.send(json.dumps(pending.payload))
            except Exception as exc:
                pending.future.set_exception(SpotifyCommandError(str(exc)))
                raise
            pending.future.set_result(None)

    def _apply_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "auth_state":
            self._set_state(
                logged_in=bool(event.get("logged_in", False)),
                is_active=bool(event.get("is_active", False)),
                device_name=_optional_string(event.get("device_name")),
            )
        elif event_type == "playback_state":
            self._apply_playback_state(event)
        elif event_type == "track_changed":
            track = _parse_track(event.get("item"))
            self._set_state(track=track, duration_ms=track.duration_ms if track else None)
        elif event_type == "playback_changed":
            self._set_state(status=_playback_status(event.get("status")))
        elif event_type == "volume_changed":
            self._set_state(volume=_volume(event.get("volume")))
        elif event_type == "device_changed":
            self._set_state(
                is_active=bool(event.get("is_active", False)),
                device_name=_optional_string(event.get("device_name")),
            )
        elif event_type == "options_changed":
            options = event.get("options")
            if isinstance(options, dict):
                self._set_state(
                    shuffle=bool(options.get("shuffle", self.status().shuffle)),
                    repeat=_repeat(options.get("repeat", self.status().repeat)),
                )
        elif event_type == "position_sync":
            position = event.get("position")
            if isinstance(position, dict):
                self._set_state(position_ms=_nonnegative_int(position.get("position_ms")))
        elif event_type == "error":
            self._set_state(error=_optional_string(event.get("message")) or "Spotify command failed")

    def _apply_playback_state(self, event: dict[str, Any]) -> None:
        track = _parse_track(event.get("item"))
        position = event.get("position")
        options = event.get("options")
        position_ms = (
            _nonnegative_int(position.get("position_ms"))
            if isinstance(position, dict)
            else 0
        )
        self._set_state(
            status=_playback_status(event.get("status")),
            track=track,
            position_ms=position_ms,
            duration_ms=track.duration_ms if track else None,
            volume=_volume(event.get("volume")),
            is_active=bool(event.get("is_active", False)),
            shuffle=bool(options.get("shuffle", False)) if isinstance(options, dict) else False,
            repeat=_repeat(options.get("repeat", "off")) if isinstance(options, dict) else "off",
            error=None,
        )

    def _set_state(self, **changes: Any) -> None:
        with self._state_lock:
            self._state = replace(self._state, **changes)

    def _fail_queued(self, error: Exception) -> None:
        while True:
            try:
                pending = self._commands.get_nowait()
            except queue.Empty:
                return
            if not pending.future.done():
                pending.future.set_exception(error)


def control_payloads(action: str, value: object | None = None) -> tuple[dict[str, Any], ...]:
    """Validate a dashboard action and translate it to Soloist commands."""

    base = {"type": "command"}
    if action in {"play", "pause", "skip_next", "skip_prev"}:
        if value is not None:
            raise SpotifyCommandError(f"{action} does not accept a value")
        return (base | {"command": action},)
    if action == "seek":
        position = _required_number(value, "seek position")
        if position < 0:
            raise SpotifyCommandError("seek position must not be negative")
        return (base | {"command": "seek", "position_ms": int(position)},)
    if action == "set_volume":
        volume = _required_number(value, "volume")
        if not 0 <= volume <= 100:
            raise SpotifyCommandError("volume must be between 0 and 100")
        return (base | {"command": "set_volume", "volume": int(volume)},)
    if action == "set_shuffle":
        if not isinstance(value, bool):
            raise SpotifyCommandError("shuffle requires a boolean value")
        return (base | {"command": "set_shuffle", "enabled": value},)
    if action == "set_repeat":
        if value not in {"off", "context", "track"}:
            raise SpotifyCommandError("repeat must be off, context, or track")
        return (
            base | {"command": "set_repeat_track", "enabled": value == "track"},
            base | {"command": "set_repeat_context", "enabled": value == "context"},
        )
    raise SpotifyCommandError(f"unsupported Spotify action: {action}")


def _parse_track(value: object) -> SpotifyTrack | None:
    if not isinstance(value, dict):
        return None
    decorations = value.get("decorations")
    decorations = decorations if isinstance(decorations, dict) else {}
    identity = decorations.get("identity")
    identity = identity if isinstance(identity, dict) else {}
    visual_identity = decorations.get("visual_identity")
    visual_identity = visual_identity if isinstance(visual_identity, dict) else {}
    covers = visual_identity.get("cover")
    cover_url = None
    if isinstance(covers, list):
        for cover in covers:
            if isinstance(cover, dict) and isinstance(cover.get("url"), str):
                cover_url = cover["url"]
                break
    parent = decorations.get("parent")
    parent = parent if isinstance(parent, dict) else {}
    parent_entity = parent.get("entity")
    parent_entity = parent_entity if isinstance(parent_entity, dict) else {}
    parent_decorations = parent_entity.get("decorations")
    parent_decorations = parent_decorations if isinstance(parent_decorations, dict) else {}
    parent_identity = parent_decorations.get("identity")
    parent_identity = parent_identity if isinstance(parent_identity, dict) else {}
    artists: list[str] = []
    creators = decorations.get("creators")
    if isinstance(creators, list):
        for creator in creators:
            if not isinstance(creator, dict):
                continue
            creator_entity = creator.get("entity")
            creator_entity = creator_entity if isinstance(creator_entity, dict) else {}
            creator_decorations = creator_entity.get("decorations")
            creator_decorations = creator_decorations if isinstance(creator_decorations, dict) else {}
            creator_identity = creator_decorations.get("identity")
            creator_identity = creator_identity if isinstance(creator_identity, dict) else {}
            name = creator_identity.get("name")
            if isinstance(name, str) and name:
                artists.append(name)
    playback = decorations.get("playback")
    playback = playback if isinstance(playback, dict) else {}
    duration = playback.get("duration_ms")
    return SpotifyTrack(
        uri=_optional_string(value.get("uri")) or "",
        name=_optional_string(identity.get("name")) or "",
        artists=tuple(artists),
        album=_optional_string(parent_identity.get("name")),
        cover_url=cover_url,
        duration_ms=_nonnegative_int(duration) if duration is not None else None,
    )


def _playback_status(value: object) -> SpotifyPlaybackStatus:
    return value if value in {"idle", "playing", "paused", "buffering"} else "idle"  # type: ignore[return-value]


def _repeat(value: object) -> SpotifyRepeat:
    return value if value in {"off", "context", "track"} else "off"  # type: ignore[return-value]


def _volume(value: object) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return min(100, max(0, int(value)))
    return 0


def _nonnegative_int(value: object) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(value))
    return 0


def _required_number(value: object | None, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SpotifyCommandError(f"{label} requires a number")
    return float(value)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "SpotifyBackend",
    "SpotifyClient",
    "SpotifyCommandError",
    "SpotifyPlaybackStatus",
    "SpotifyRepeat",
    "SpotifyStatus",
    "SpotifyTrack",
    "control_payloads",
]
