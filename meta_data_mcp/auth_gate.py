"""Magic-link auth gate: email verification + per-user rate limiting.

This module holds the in-memory state machines that sit on top of the OAuth
flow to (a) require a verified email address before access is granted and
(b) throttle each verified user independently.

Two stores, both deliberately in-memory and process-local (matching
:class:`~meta_data_mcp.oauth_provider.InMemoryOAuthProvider`):

- :class:`MagicLinkStore` — issues single-use, short-TTL tokens that bind a
  pending consent session to an email address. A token is consumed on the
  first successful :meth:`MagicLinkStore.verify`.
- :class:`RateLimiter` — fixed-window per-identity request counter. Identity is
  the verified email (falling back to the access token when no email is bound).

Restarting the server clears both. A durable backend (e.g. SQLite) can replace
these without touching call sites — the method surfaces are intentionally
small. See ``docs/hosting.md`` for the env-var contract.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

# Single-use sign-in tokens are short-lived: long enough to receive and click
# the email, short enough to limit replay if the inbox is later compromised.
DEFAULT_MAGIC_LINK_TTL_SECONDS = 600  # 10 minutes
DEFAULT_RATE_LIMIT_RPM = 30  # matches the Sophymarine free tier


@dataclass(frozen=True)
class MagicLinkRecord:
    """The state bound to one outstanding magic-link token."""

    session_token: str
    email: str


class MagicLinkStore:
    """Single-use, TTL-bounded magic-link tokens.

    Each token maps to the consent ``session_token`` it will complete and the
    ``email`` the user entered. :meth:`verify` is one-shot — a token cannot be
    replayed, so a leaked link is useless once clicked.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_MAGIC_LINK_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._tokens: dict[str, tuple[MagicLinkRecord, float]] = {}

    def issue(self, session_token: str, email: str) -> str:
        """Create a magic-link token for (session_token, email) and return it."""
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (
            MagicLinkRecord(session_token=session_token, email=email),
            time.time() + self._ttl,
        )
        return token

    def verify(self, token: str) -> MagicLinkRecord | None:
        """Consume ``token`` and return its record, or None if invalid/expired.

        Removes the token unconditionally on lookup so an expired token can't be
        retried and a valid token can't be reused.
        """
        entry = self._tokens.pop(token, None)
        if entry is None:
            return None
        record, expires_at = entry
        if time.time() > expires_at:
            return None
        return record

    def purge_expired(self) -> int:
        """Drop expired tokens; return how many were removed. For housekeeping."""
        now = time.time()
        stale = [t for t, (_, exp) in self._tokens.items() if now > exp]
        for t in stale:
            self._tokens.pop(t, None)
        return len(stale)


class RateLimiter:
    """Fixed-window per-identity rate limiter.

    A window is ``window_seconds`` wide (default 60s → "per minute"). Each
    identity gets up to ``rpm`` requests per window; the window resets on the
    first request after it elapses. Fixed-window is chosen over a sliding
    window for the same reason the rest of this server stays simple: it is
    O(1) per request, has no background sweeper, and the burst-at-boundary
    imprecision is irrelevant for a courtesy free-tier limit.
    """

    def __init__(self, rpm: int = DEFAULT_RATE_LIMIT_RPM, window_seconds: int = 60):
        self.rpm = rpm
        self.window_seconds = window_seconds
        # identity -> (window_start, count)
        self._buckets: dict[str, tuple[float, int]] = {}

    def allow(self, identity: str) -> bool:
        """Return True if ``identity`` may make a request now, else False.

        A non-positive ``rpm`` disables limiting (always allows) — lets an
        operator turn the gate's throttle off via config without code changes.
        """
        if self.rpm <= 0:
            return True
        now = time.time()
        window_start, count = self._buckets.get(identity, (now, 0))
        if now - window_start >= self.window_seconds:
            # Window elapsed — start a fresh one.
            self._buckets[identity] = (now, 1)
            return True
        if count >= self.rpm:
            return False
        self._buckets[identity] = (window_start, count + 1)
        return True

    def retry_after(self, identity: str) -> int:
        """Seconds until ``identity``'s current window resets (for 429 hints)."""
        bucket = self._buckets.get(identity)
        if bucket is None:
            return 0
        window_start, _ = bucket
        remaining = self.window_seconds - (time.time() - window_start)
        return max(0, int(remaining) + 1)
