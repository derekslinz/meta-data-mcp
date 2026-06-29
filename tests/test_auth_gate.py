"""Tests for the magic-link store and per-user rate limiter."""

from __future__ import annotations

import meta_data_mcp.auth_gate as auth_gate
from meta_data_mcp.auth_gate import MagicLinkStore, RateLimiter


class FakeClock:
    """Monkeypatchable stand-in for time.time()."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# MagicLinkStore
# ---------------------------------------------------------------------------


def test_magic_link_issue_then_verify_returns_record(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_gate.time, "time", clock)
    store = MagicLinkStore(ttl_seconds=600)

    token = store.issue("sess-1", "u@example.com")
    record = store.verify(token)

    assert record is not None
    assert record.session_token == "sess-1"
    assert record.email == "u@example.com"


def test_magic_link_is_single_use(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_gate.time, "time", clock)
    store = MagicLinkStore()

    token = store.issue("sess-1", "u@example.com")
    assert store.verify(token) is not None
    # Second use is rejected — token was consumed.
    assert store.verify(token) is None


def test_magic_link_expires(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_gate.time, "time", clock)
    store = MagicLinkStore(ttl_seconds=600)

    token = store.issue("sess-1", "u@example.com")
    clock.advance(601)
    assert store.verify(token) is None


def test_magic_link_verify_unknown_token():
    assert MagicLinkStore().verify("nope") is None


def test_magic_link_purge_expired(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_gate.time, "time", clock)
    store = MagicLinkStore(ttl_seconds=100)

    store.issue("s1", "a@example.com")
    store.issue("s2", "b@example.com")
    clock.advance(101)
    store.issue("s3", "c@example.com")  # fresh

    assert store.purge_expired() == 2


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_up_to_rpm(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_gate.time, "time", clock)
    rl = RateLimiter(rpm=3, window_seconds=60)

    assert [rl.allow("u@example.com") for _ in range(4)] == [True, True, True, False]


def test_rate_limiter_is_per_identity(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_gate.time, "time", clock)
    rl = RateLimiter(rpm=1, window_seconds=60)

    assert rl.allow("a@example.com") is True
    assert rl.allow("b@example.com") is True  # different identity, own bucket
    assert rl.allow("a@example.com") is False


def test_rate_limiter_window_resets(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_gate.time, "time", clock)
    rl = RateLimiter(rpm=1, window_seconds=60)

    assert rl.allow("u@example.com") is True
    assert rl.allow("u@example.com") is False
    clock.advance(60)
    assert rl.allow("u@example.com") is True


def test_rate_limiter_disabled_when_rpm_non_positive():
    rl = RateLimiter(rpm=0)
    assert all(rl.allow("u@example.com") for _ in range(1000))


def test_rate_limiter_retry_after(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_gate.time, "time", clock)
    rl = RateLimiter(rpm=1, window_seconds=60)

    rl.allow("u@example.com")
    clock.advance(20)
    # 40s left in the window, +1 rounding => 41
    assert rl.retry_after("u@example.com") == 41
    assert rl.retry_after("unknown@example.com") == 0
