"""
In-memory provider health registry.

Tracks per-provider failure / success events and exposes a health score in
``[0.0, 1.0]`` for use by routing scorers. Recent failures lower the score;
the score decays back toward 1.0 over time (exponential decay with a ~5 minute
characteristic) so transient outages don't permanently penalize a provider.

This module is intentionally process-local and thread-safe. There is no
persistence across restarts — see the related plan for follow-up work.

The monotonic clock is read via ``_clock`` so tests can inject a fake clock to
drive decay deterministically.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from math import exp
from typing import Callable

# Module-level injectable clock. Defaults to ``time.monotonic`` so callers get
# a steady, never-decreasing reference. Tests may replace this with a callable
# that returns a controlled value.
_clock: Callable[[], float] = time.monotonic


@dataclass
class _ProviderHealthState:
    """Mutable health state for a single provider.

    ``failure_mass`` is a continuously-decayed float accumulator. On every
    ``record_failure`` or ``record_success`` call the existing mass is first
    scaled by ``exp(-dt / _DECAY_TAU_SECONDS)`` and then 1.0 is added or
    subtracted. This means old failures are diluted before new ones arrive, so a
    long-idle provider is not permanently re-penalized by a single new failure.
    ``last_update_ts`` records when the mass was last written so the same decay
    can be applied in ``health_score`` without mutating state.
    """

    failure_mass: float = 0.0
    last_update_ts: float | None = None


_lock = threading.Lock()
_state: dict[str, _ProviderHealthState] = {}

# Characteristic time (seconds) for failure decay. After this many seconds
# since the last failure, the impact of accumulated failures is reduced by a
# factor of 1/e (~0.37). After several multiples of this constant, even a
# burst of failures is effectively forgotten.
_DECAY_TAU_SECONDS: float = 300.0


def record_failure(provider_id: str, status: int | None = None) -> None:
    """Record a provider failure.

    Args:
        provider_id: The provider's stable id.
        status: Optional HTTP status (currently unused; kept for caller
            compatibility with future translate_http_error wiring).
    """
    del status  # Reserved for future use; not stored.
    now = _clock()
    with _lock:
        entry = _state.get(provider_id)
        if entry is None:
            entry = _ProviderHealthState()
            _state[provider_id] = entry
        # Decay existing mass before adding the new failure so that old
        # failures don't jump back to their full weight on the next event.
        if entry.last_update_ts is not None:
            dt = max(0.0, now - entry.last_update_ts)
            entry.failure_mass *= exp(-dt / _DECAY_TAU_SECONDS)
        entry.failure_mass += 1.0
        entry.last_update_ts = now


def record_success(provider_id: str) -> None:
    """Record a provider success.

    Decays the failure mass and then subtracts 1.0 (floor 0.0). A stream of
    successes will drive the mass to zero, recovering a healthy score.
    """
    now = _clock()
    with _lock:
        entry = _state.get(provider_id)
        if entry is None:
            entry = _ProviderHealthState()
            _state[provider_id] = entry
        # Decay before adjusting so the subtraction is proportionate to the
        # current actual mass.
        if entry.last_update_ts is not None:
            dt = max(0.0, now - entry.last_update_ts)
            entry.failure_mass *= exp(-dt / _DECAY_TAU_SECONDS)
        entry.failure_mass = max(0.0, entry.failure_mass - 1.0)
        entry.last_update_ts = now


def health_score(provider_id: str, *, now: float | None = None) -> float:
    """Return a health score in ``[0.0, 1.0]`` for the provider.

    A score of 1.0 means "fully healthy / no recent failures recorded".
    Lower scores reflect recent accumulated failures, with exponential decay
    over time. Providers with no recorded state are assumed healthy.

    The lock is held for the entire computation so the returned value always
    corresponds to a consistent snapshot of the registry state.

    Args:
        provider_id: The provider's stable id.
        now: Optional clock value (seconds, monotonic). When ``None``, reads
            from the module-level ``_clock``. Used by tests to drive decay.
    """
    with _lock:
        entry = _state.get(provider_id)
        if entry is None:
            return 1.0
        if entry.failure_mass <= 0.0 or entry.last_update_ts is None:
            return 1.0

        current = now if now is not None else _clock()
        dt = max(0.0, current - entry.last_update_ts)

        # Exponential decay: more recent failures hurt more; older ones fade.
        decayed_mass = entry.failure_mass * exp(-dt / _DECAY_TAU_SECONDS)
        penalty = min(1.0, decayed_mass)
        score = 1.0 - penalty
        # Clamp defensively.
        if score < 0.0:
            return 0.0
        if score > 1.0:
            return 1.0
        return score


def reset(provider_id: str | None = None) -> None:
    """Clear health state.

    Args:
        provider_id: When given, clear only that provider's entry. When
            ``None``, clear all state. Intended for tests.
    """
    with _lock:
        if provider_id is None:
            _state.clear()
        else:
            _state.pop(provider_id, None)


def snapshot(
    provider_ids: list[str] | tuple[str, ...] | None = None,
    *,
    now: float | None = None,
) -> dict[str, dict[str, float | None]]:
    """Return a snapshot of health state for instrumentation / UI.

    The Phase 3 ``opendata.health.snapshot`` meta tool calls this to feed
    the discovery app's per-provider health badges. The return value is a
    fresh dict — callers can mutate it freely without affecting the
    in-memory registry.

    Args:
        provider_ids: Optional list of provider ids to query. When given,
            providers with no recorded state default to a fully-healthy
            entry (score 1.0, ``failure_mass`` 0.0, ``last_update_ts`` ``None``).
            When ``None``, returns every provider currently in the registry.
        now: Optional clock value for decay computation. ``None`` reads the
            module-level ``_clock``. Used by tests.

    Returns:
        ``{provider_id: {"score": float, "failure_mass": float,
        "last_update_ts": float | None}}``.
    """
    current = now if now is not None else _clock()
    with _lock:
        if provider_ids is None:
            ids = list(_state.keys())
        else:
            ids = list(provider_ids)

        result: dict[str, dict[str, float | None]] = {}
        for pid in ids:
            entry = _state.get(pid)
            if entry is None:
                result[pid] = {
                    "score": 1.0,
                    "failure_mass": 0.0,
                    "last_update_ts": None,
                }
                continue
            # Mirror health_score()'s decay calculation so the snapshot is
            # consistent with whatever the scorer would have returned at
            # the same instant.
            if entry.failure_mass <= 0.0 or entry.last_update_ts is None:
                score = 1.0
                decayed_mass = entry.failure_mass
            else:
                dt = max(0.0, current - entry.last_update_ts)
                decayed_mass = entry.failure_mass * exp(-dt / _DECAY_TAU_SECONDS)
                penalty = min(1.0, decayed_mass)
                score = max(0.0, min(1.0, 1.0 - penalty))
            result[pid] = {
                "score": score,
                "failure_mass": decayed_mass,
                "last_update_ts": entry.last_update_ts,
            }
    return result
