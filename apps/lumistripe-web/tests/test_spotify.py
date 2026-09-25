from __future__ import annotations

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
    (("seek", -1), ("set_volume", 101), ("set_shuffle", "yes"), ("set_repeat", "all")),
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
