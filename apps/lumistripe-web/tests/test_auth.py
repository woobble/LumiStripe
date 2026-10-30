from __future__ import annotations

import pytest
from lumistripe_web import auth
from lumistripe_web.auth import (
    InvalidPairingCodeError,
    PairingAuth,
)


def test_auth_cleans_abandoned_records() -> None:
    access = PairingAuth("1234")
    token = access.pair("1234", "paired", now_s=0)
    access.pair("1234", "new", now_s=auth.SESSION_MAX_AGE_SECONDS + 1)
    assert token not in access._sessions


def test_auth_session_storage_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "MAX_SESSIONS", 2)
    access = PairingAuth("1234")
    tokens = [access.pair("1234", "phone", now_s=0) for _ in range(3)]
    assert len(access._sessions) == 2
    assert not access.authenticated(tokens[0], now_s=0)


def test_disabled_auth_does_not_store_sessions() -> None:
    access = PairingAuth(None)
    assert access.pair("1234", "phone")
    assert not access._sessions


def test_pairing_auth_creates_and_revokes_opaque_session() -> None:
    access = PairingAuth("1234")

    with pytest.raises(InvalidPairingCodeError):
        access.pair("0000", "phone", now_s=0.0)

    session = access.pair("1234", "phone", now_s=1.0)
    assert session != "1234"
    assert access.authenticated(session, now_s=1.0) is True

    access.revoke(session)
    assert access.authenticated(session, now_s=1.0) is False


def test_pairing_auth_allows_repeated_attempts() -> None:
    access = PairingAuth("1234")

    for attempt in range(10):
        with pytest.raises(InvalidPairingCodeError):
            access.pair("0000", "phone", now_s=float(attempt))

    session = access.pair("1234", "phone", now_s=10.0)
    assert access.authenticated(session, now_s=10.0) is True


@pytest.mark.parametrize("code", ["123", "12345", "12a4", "１２３４"])
def test_pairing_auth_rejects_non_four_ascii_digit_configuration(code: str) -> None:
    with pytest.raises(ValueError, match="four digits"):
        PairingAuth(code)
