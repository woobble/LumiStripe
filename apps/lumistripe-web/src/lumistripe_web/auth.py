from __future__ import annotations

import math
import secrets
import threading
import time
from collections import defaultdict, deque

SESSION_COOKIE = "lumistripe_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30
FAILED_ATTEMPT_WINDOW_SECONDS = 5 * 60
FAILED_ATTEMPT_LIMIT = 5
LOCKOUT_SECONDS = 60


class InvalidPairingCodeError(ValueError):
    pass


class PairingRateLimitedError(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"Too many attempts. Try again in {retry_after_seconds} seconds."
        )


class PairingAuth:
    def __init__(self, pairing_code: str | None) -> None:
        if pairing_code is not None and (
            len(pairing_code) != 4
            or not pairing_code.isascii()
            or not pairing_code.isdigit()
        ):
            raise ValueError("pairing code must contain exactly four digits")
        self._pairing_code = pairing_code
        self._sessions: dict[str, float] = {}
        self._failed_attempts: dict[str, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def required(self) -> bool:
        return self._pairing_code is not None

    def authenticated(self, session: str | None, *, now_s: float | None = None) -> bool:
        if not self.required:
            return True
        if session is None:
            return False
        now = time.monotonic() if now_s is None else now_s
        with self._lock:
            expires_at = self._sessions.get(session)
            if expires_at is None:
                return False
            if expires_at <= now:
                self._sessions.pop(session, None)
                return False
            return True

    def pair(
        self,
        code: str,
        client_id: str,
        *,
        now_s: float | None = None,
    ) -> str:
        if not self.required:
            return self._new_session()
        now = time.monotonic() if now_s is None else now_s
        with self._lock:
            blocked_until = self._blocked_until.get(client_id, 0.0)
            if blocked_until > now:
                raise PairingRateLimitedError(math.ceil(blocked_until - now))

            attempts = self._failed_attempts[client_id]
            while attempts and now - attempts[0] > FAILED_ATTEMPT_WINDOW_SECONDS:
                attempts.popleft()

            assert self._pairing_code is not None
            if secrets.compare_digest(code, self._pairing_code):
                attempts.clear()
                self._blocked_until.pop(client_id, None)
                token = secrets.token_urlsafe(32)
                self._sessions[token] = now + SESSION_MAX_AGE_SECONDS
                return token

            attempts.append(now)
            if len(attempts) >= FAILED_ATTEMPT_LIMIT:
                self._blocked_until[client_id] = now + LOCKOUT_SECONDS
                attempts.clear()
                raise PairingRateLimitedError(LOCKOUT_SECONDS)
            raise InvalidPairingCodeError("The pairing code is incorrect.")

    def revoke(self, session: str | None) -> None:
        if session is None:
            return
        with self._lock:
            self._sessions.pop(session, None)

    def _new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = time.monotonic() + SESSION_MAX_AGE_SECONDS
        return token
