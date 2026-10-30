from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from math import ceil

SESSION_COOKIE = "lumistripe_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30
CLEANUP_INTERVAL_SECONDS = 60
MAX_SESSIONS = 1024
PAIRING_MAX_FAILURES = 5
PAIRING_WINDOW_SECONDS = 60


class InvalidPairingCodeError(ValueError):
    pass


class PairingRateLimitError(ValueError):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("Too many pairing attempts. Please try again later.")


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
        self._pairing_failures: deque[float] = deque()
        self._lock = threading.Lock()
        self._next_cleanup_s = 0.0

    def _cleanup(self, now: float) -> None:
        """Prune abandoned records periodically while holding the auth lock."""
        if now < self._next_cleanup_s:
            return
        self._next_cleanup_s = now + CLEANUP_INTERVAL_SECONDS
        self._sessions = {
            token: expiry for token, expiry in self._sessions.items() if expiry > now
        }

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
            self._cleanup(now)
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
            return secrets.token_urlsafe(32)
        with self._lock:
            now = time.monotonic() if now_s is None else now_s
            self._cleanup(now)
            while (
                self._pairing_failures
                and self._pairing_failures[0] <= now - PAIRING_WINDOW_SECONDS
            ):
                self._pairing_failures.popleft()
            if len(self._pairing_failures) >= PAIRING_MAX_FAILURES:
                raise PairingRateLimitError(
                    max(1, ceil(self._pairing_failures[0] + PAIRING_WINDOW_SECONDS - now))
                )
            assert self._pairing_code is not None
            if secrets.compare_digest(code, self._pairing_code):
                token = secrets.token_urlsafe(32)
                if len(self._sessions) >= MAX_SESSIONS:
                    del self._sessions[next(iter(self._sessions))]
                self._sessions[token] = now + SESSION_MAX_AGE_SECONDS
                return token

            self._pairing_failures.append(now)
            raise InvalidPairingCodeError("The pairing code is incorrect.")

    def revoke(self, session: str | None) -> None:
        if session is None:
            return
        with self._lock:
            self._sessions.pop(session, None)

