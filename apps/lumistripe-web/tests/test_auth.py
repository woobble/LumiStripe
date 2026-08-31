from __future__ import annotations

import pytest
from lumistripe_web.auth import (
    InvalidPairingCodeError,
    PairingAuth,
    PairingRateLimitedError,
)


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
