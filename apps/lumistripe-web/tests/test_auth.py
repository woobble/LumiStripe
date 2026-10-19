from __future__ import annotations

import pytest
from lumistripe_web import auth
from lumistripe_web.auth import (
    InvalidPairingCodeError,
    PairingAuth,
    PairingRateLimitedError,
)


def test_auth_cleans_abandoned_records() -> None:
    access = PairingAuth("1234")
    token = access.pair("1234", "paired", now_s=0)
    for attempt in range(5):
        with pytest.raises((InvalidPairingCodeError, PairingRateLimitedError)):
            access.pair("0000", "abandoned", now_s=float(attempt))
    access.pair("1234", "new", now_s=auth.SESSION_MAX_AGE_SECONDS + 1)
    assert token not in access._sessions
    assert not access._failed_attempts
    assert not access._blocked_until


def test_auth_storage_limits_preserve_existing_lockouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "MAX_CLIENTS", 1)
    monkeypatch.setattr(auth, "MAX_SESSIONS", 2)
    access = PairingAuth("1234")
    tokens = [access.pair("1234", "phone", now_s=0) for _ in range(3)]
    assert len(access._sessions) == 2
    assert not access.authenticated(tokens[0], now_s=0)
    for _ in range(5):
        with pytest.raises((InvalidPairingCodeError, PairingRateLimitedError)):
            access.pair("0000", "blocked", now_s=0)
    with pytest.raises(PairingRateLimitedError):
        access.pair("0000", "new", now_s=0)
    with pytest.raises(PairingRateLimitedError):
        access.pair("1234", "blocked", now_s=0)
    assert len(access._failed_attempts) == 1


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


def test_pairing_auth_rate_limits_repeated_failures() -> None:
    access = PairingAuth("1234")

    for attempt in range(4):
        with pytest.raises(InvalidPairingCodeError):
            access.pair("0000", "phone", now_s=float(attempt))

    with pytest.raises(PairingRateLimitedError) as exc_info:
        access.pair("0000", "phone", now_s=4.0)
    assert exc_info.value.retry_after_seconds == 60

    with pytest.raises(PairingRateLimitedError):
        access.pair("1234", "phone", now_s=10.0)

    session = access.pair("1234", "phone", now_s=65.0)
    assert access.authenticated(session, now_s=65.0) is True


@pytest.mark.parametrize("code", ["123", "12345", "12a4", "１２３４"])
def test_pairing_auth_rejects_non_four_ascii_digit_configuration(code: str) -> None:
    with pytest.raises(ValueError, match="four digits"):
        PairingAuth(code)
