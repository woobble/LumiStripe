from __future__ import annotations

import threading
import time
from concurrent.futures import Future

import lumistripe_web.spotify as spotify_module
import pytest
from lumistripe_web.spotify import (
    SpotifyClient,
    SpotifyCommandError,
    control_payloads,
)


def test_control_payloads_match_soloist_websocket_commands() -> None:
    assert control_payloads("play") == ({"type": "command", "command": "play"},)
    assert control_payloads("seek", 12_345) == (
        {"type": "command", "command": "seek", "position_ms": 12_345},
    )
    assert control_payloads("set_volume", 72.8) == (
        {"type": "command", "command": "set_volume", "volume": 72},
    )
    assert control_payloads("set_shuffle", True) == (
        {"type": "command", "command": "set_shuffle", "enabled": True},
    )
    assert control_payloads("set_repeat", "track") == (
        {"type": "command", "command": "set_repeat_track", "enabled": True},
        {"type": "command", "command": "set_repeat_context", "enabled": False},
    )


@pytest.mark.parametrize(
    ("action", "value"),
    (("seek", -1), ("seek", float("nan")), ("seek", float("inf")),
     ("set_volume", 101), ("set_volume", float("nan")),
     ("set_shuffle", "yes"), ("set_repeat", "all"), ("set_repeat", [])),
)
def test_control_payloads_reject_invalid_values(action: str, value: object) -> None:
    with pytest.raises(SpotifyCommandError):
        control_payloads(action, value)


def test_client_projects_soloist_events_into_a_stable_status() -> None:
    client = SpotifyClient(enabled=True)

    client._apply_event(
        {
            "type": "auth_state",
            "logged_in": True,
            "is_active": True,
            "device_name": "LumiStripe",
        }
    )
    client._apply_event(
        {
            "type": "playback_state",
            "status": "playing",
            "position": {"position_ms": 4_200},
            "volume": 68,
            "options": {"shuffle": True, "repeat": "context"},
            "is_active": True,
            "item": {
                "uri": "spotify:track:example",
                "decorations": {
                    "identity": {"name": "Example track"},
                    "playback": {"duration_ms": 180_000},
                    "parent": {
                        "entity": {
                            "decorations": {"identity": {"name": "Example album"}}
                        }
                    },
                    "creators": [
                        {
                            "entity": {
                                "decorations": {"identity": {"name": "Example artist"}}
                            }
                        }
                    ],
                },
            },
        }
    )

    status = client.status()
    assert status.connected is False
    assert status.logged_in is True
    assert status.is_active is True
    assert status.device_name == "LumiStripe"
    assert status.status == "playing"
    assert status.position_ms == 4_200
    assert status.volume == 68
    assert status.shuffle is True
    assert status.repeat == "context"
    assert status.track is not None
    assert status.track.name == "Example track"
    assert status.track.artists == ("Example artist",)
    assert status.track.album == "Example album"
    assert status.track.duration_ms == 180_000


def test_client_interpolates_position_between_soloist_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(spotify_module.time, "monotonic", lambda: now[0])
    client = SpotifyClient(enabled=True)

    client._apply_event(
        {
            "type": "playback_state",
            "status": "playing",
            "position": {
                "position_ms": 4_200,
                "timestamp_ms": 1_000,
                "speed": 1.0,
            },
            "item": {"decorations": {"playback": {"duration_ms": 180_000}}},
        }
    )

    now[0] += 2.5
    assert client.status().position_ms == 6_700
    client._apply_event({"type": "playback_changed", "status": "paused"})
    now[0] += 10
    assert client.status().position_ms == 6_700


def test_malformed_soloist_status_does_not_break_the_client() -> None:
    client = SpotifyClient()
    client._apply_event({
        "type": "playback_state",
        "status": {},
        "volume": float("nan"),
        "options": {"repeat": []},
        "position": {"position_ms": float("inf"), "speed": float("nan")},
    })
    status = client.status()
    assert status.status == "idle"
    assert status.volume == 0
    assert status.repeat == "off"
    assert status.position_ms == 0


def test_extreme_soloist_position_remains_finite_and_json_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(spotify_module.time, "monotonic", lambda: now[0])
    client = SpotifyClient()
    client._apply_event({
        "type": "playback_state",
        "status": "playing",
        "position": {"position_ms": 1e308, "speed": 1e308},
    })
    now[0] += 10.0
    assert client.status().position_ms == spotify_module.MAX_POSITION_MS


def test_disconnect_clears_stale_playback_and_authentication_state() -> None:
    client = SpotifyClient()
    client._set_state(connected=True, logged_in=True, is_active=True)
    client._apply_event({
        "type": "playback_state",
        "status": "playing",
        "is_active": True,
        "position": {"position_ms": 1000, "speed": 1},
        "item": {"uri": "spotify:track:test"},
    })
    client.stop()
    status = client.status()
    assert not status.connected
    assert not status.logged_in
    assert not status.is_active
    assert status.status == "idle"
    assert status.track is None
    assert status.position_ms == 0


def test_reader_continues_after_malformed_event(monkeypatch: pytest.MonkeyPatch) -> None:
    class Socket:
        def __init__(self) -> None:
            self.closed = threading.Event()
            self.messages = iter((
                '{"type":"playback_state","status":{},"volume":NaN}',
                b"\xff",
                '{"type":"playback_changed","status":"paused"}',
            ))

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            self.close()

        def recv(self, timeout: float) -> str | bytes | None:
            message = next(self.messages, None)
            if message is not None:
                return message
            if self.closed.wait(min(timeout, 0.01)):
                return None
            raise TimeoutError

        def close(self) -> None:
            self.closed.set()

    socket = Socket()
    calls: list[str] = []
    monkeypatch.setattr(
        spotify_module, "connect", lambda uri, **kwargs: calls.append(uri) or socket
    )
    client = SpotifyClient(reconnect_delay=0.25)
    client.start()
    try:
        deadline = time.monotonic() + 1.0
        while client.status().status != "paused" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.status().status == "paused"
        assert client.status().connected
        assert "event encoding" in (client.status().error or "")
        assert len(calls) == 1
    finally:
        client.stop()


def test_timed_out_queued_command_is_not_sent_later(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify_module, "SPOTIFY_COMMAND_TIMEOUT_SECONDS", 0.01)
    client = SpotifyClient()
    sent: list[str] = []

    class Socket:
        def send(self, payload: str) -> None:
            sent.append(payload)

    socket = Socket()
    client._socket = socket
    with pytest.raises(SpotifyCommandError, match="before delivery"):
        client.control("play")
    client._send_queued(socket)
    assert sent == []


def test_inflight_timeout_reports_delivery_uncertainty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify_module, "SPOTIFY_COMMAND_TIMEOUT_SECONDS", 0.5)
    client = SpotifyClient()
    entered = threading.Event()
    release = threading.Event()
    errors: list[str] = []

    class Socket:
        def send(self, payload: str) -> None:
            entered.set()
            release.wait()

    socket = Socket()
    client._socket = socket

    def control() -> None:
        try:
            client.control("play")
        except SpotifyCommandError as exc:
            errors.append(str(exc))

    caller = threading.Thread(target=control)
    caller.start()
    deadline = time.monotonic() + 1.0
    while client._commands.empty() and time.monotonic() < deadline:
        time.sleep(0.001)
    sender = threading.Thread(target=client._send_queued, args=(socket,))
    sender.start()
    try:
        assert entered.wait(1)
        caller.join(1)
        assert not caller.is_alive()
        assert errors == ["Spotify command timed out; delivery may still complete"]
    finally:
        release.set()
        sender.join(1)
        caller.join(1)


def test_spotify_command_queue_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spotify_module, "MAX_PENDING_SPOTIFY_COMMANDS", 1)
    client = SpotifyClient()
    client._socket = object()
    client._commands.put_nowait(spotify_module._PendingCommand({}, Future()))
    with pytest.raises(SpotifyCommandError, match="queue is full"):
        client.control("play")


def test_timed_out_stop_does_not_start_a_second_spotify_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SpotifyClient()
    entered = threading.Event()
    release = threading.Event()

    def blocked_run() -> None:
        entered.set()
        release.wait()

    monkeypatch.setattr(client, "_run", blocked_run)
    client.start()
    assert entered.wait(1)
    thread = client._thread
    assert thread is not None
    join = thread.join
    monkeypatch.setattr(thread, "join", lambda timeout=None: None)
    try:
        client.stop()
        client.start()
        assert client._thread is thread
        assert client._stop_event.is_set()
    finally:
        release.set()
        join(1)
        client.stop()
