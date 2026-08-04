"""Tests for the in-memory provider health registry and HealthScorer."""

from __future__ import annotations

import threading

import pytest

from meta_data_mcp import health
from meta_data_mcp.registry import ProviderEntry
from meta_data_mcp.routing import HealthScorer, RoutingEngine


@pytest.fixture(autouse=True)
def _clean_health_state():
    """Ensure each test starts with an empty health registry and a real clock."""
    original_clock = health._clock
    health.reset()
    yield
    health.reset()
    health._clock = original_clock


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------- Registry behavior ----------


def test_fresh_provider_is_fully_healthy():
    assert health.health_score("never-seen") == 1.0


def test_single_failure_drops_score_below_one():
    # Pin the clock so dt is small but non-zero — failure penalty applies.
    health._clock = lambda: 1000.0
    health.record_failure("p1")
    # Observe shortly after with the explicit override.
    score = health.health_score("p1", now=1000.5)
    assert 0.0 <= score < 1.0


def test_many_failures_decay_back_toward_healthy():
    # Stack a burst of failures at t=0, then check far in the future.
    health._clock = lambda: 0.0
    for _ in range(5):
        health.record_failure("p2")

    immediate = health.health_score("p2", now=0.0)
    assert immediate < 1.0

    # 3600 s == 12 * tau, exp(-12) ≈ 6e-6, so score returns ~1.0.
    far_future = health.health_score("p2", now=3600.0)
    assert far_future > 0.99


def test_record_success_decays_failure_counter():
    health._clock = lambda: 10.0
    health.record_failure("p3")
    health.record_failure("p3")
    # Two successes wipe the two recorded failures.
    health.record_success("p3")
    health.record_success("p3")
    # Score must be 1.0 since failures hit 0 (last_failure_ts is irrelevant
    # when failures == 0).
    assert health.health_score("p3", now=11.0) == 1.0


def test_reset_clears_all_state():
    health._clock = lambda: 0.0
    health.record_failure("p4")
    health.record_failure("p5")
    # Scores should be below 1.0 immediately after failures with no decay.
    assert health.health_score("p4", now=0.0) < 1.0
    assert health.health_score("p5", now=0.0) < 1.0
    health.reset()
    assert health.health_score("p4") == 1.0
    assert health.health_score("p5") == 1.0


def test_reset_single_provider_only():
    health._clock = lambda: 0.0
    health.record_failure("a")
    health.record_failure("b")
    health.reset("a")
    assert health.health_score("a") == 1.0
    # b still has a recorded failure -> score below 1.0
    assert health.health_score("b", now=0.5) < 1.0


def test_thread_safety_failure_count_is_exact():
    threads_count = 32
    per_thread = 100

    def worker():
        for _ in range(per_thread):
            health.record_failure("contended")

    threads = [threading.Thread(target=worker) for _ in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All 3200 failure events must have been applied. Because each call decays
    # the existing mass first, the final mass will be slightly less than the
    # raw count (tiny real-time decay over the short test duration). We check
    # that it's within 1 % of the expected count.
    expected = threads_count * per_thread
    with health._lock:
        entry = health._state["contended"]
        assert entry.failure_mass == pytest.approx(expected, rel=0.01)


# ---------- Routing integration ----------


def _make_provider(pid: str) -> ProviderEntry:
    return ProviderEntry(
        id=pid,
        server_name=pid,
        title=f"Test {pid}",
        description="A test provider for health-aware routing.",
        domains=("government",),
        regions=("global",),
        keywords=("test", "weather", "data"),
        homepage="https://example.com/",
    )


@pytest.mark.anyio
async def test_health_scorer_returns_one_for_healthy_provider():
    scorer = HealthScorer()
    provider = _make_provider("healthy-1")
    assert await scorer.score("anything", provider) == 1.0


@pytest.mark.anyio
async def test_health_scorer_drops_for_failing_provider():
    scorer = HealthScorer()
    provider = _make_provider("flaky-1")
    health._clock = lambda: 100.0
    for _ in range(3):
        health.record_failure("flaky-1")
    # The scorer reads health_score with the live clock — keep the clock
    # pinned so dt stays small and the penalty is visible.
    score = await scorer.score("anything", provider)
    assert score < 1.0


@pytest.mark.anyio
async def test_routing_engine_combined_score_penalizes_unhealthy_provider():
    """With non-zero health weight, an unhealthy provider scores below a
    healthy peer for the same query and base signals.
    """
    healthy = _make_provider("healthy-1")
    flaky = _make_provider("flaky-1")

    # Weights: only token + health contribute. The two providers have
    # identical metadata, so token score is equal; health is the
    # tiebreaker.
    engine = RoutingEngine(
        weights={
            "token": 0.5,
            "fuzzy": 0.0,
            "metadata": 0.0,
            "semantic": 0.0,
            "health": 0.5,
        },
    )

    health._clock = lambda: 100.0
    for _ in range(3):
        health.record_failure("flaky-1")

    healthy_score, _, _ = await engine._combined_score("weather data", healthy, False)
    flaky_score, _, _ = await engine._combined_score("weather data", flaky, False)

    assert healthy_score > flaky_score


@pytest.mark.anyio
async def test_default_engine_health_weight_is_nonzero():
    """Phase 3 activation: the routing engine's health weight is no longer 0.

    Pre-Phase-3 the weight was 0.0 because the kernel ``http_get`` health
    feed hadn't been wired through every provider yet. After Phase 4
    shipped the binding sweep the baseline is reliable, so Phase 3 bumps
    the weight to a low but non-zero value (``0.05`` raw, normalized
    proportionally). This test pins that decision so a future revert is
    intentional rather than accidental.
    """
    engine = RoutingEngine()
    # After normalization (weights sum to 1), health weight is the raw
    # weight (0.05) divided by the raw total (1.05) ≈ 0.0476.
    assert engine.weights["health"] > 0.0
    assert engine.weights["health"] < 0.1


# ---------- snapshot() helper (Phase 3) ----------


def test_snapshot_unknown_provider_defaults_to_healthy():
    """When a caller asks for a provider that's never been recorded, the
    snapshot returns the fully-healthy baseline rather than omitting the
    entry. This keeps the discovery app's UI free of "missing badge"
    edge cases.
    """
    snap = health.snapshot(["never-seen"])
    assert snap == {
        "never-seen": {
            "score": 1.0,
            "failure_mass": 0.0,
            "last_update_ts": None,
        },
    }


def test_snapshot_reflects_recorded_failures():
    health._clock = lambda: 1000.0
    health.record_failure("p1")
    snap = health.snapshot(["p1"], now=1000.0)
    assert snap["p1"]["score"] < 1.0
    assert snap["p1"]["failure_mass"] > 0.0
    assert snap["p1"]["last_update_ts"] == 1000.0


def test_snapshot_consistent_with_health_score():
    """The snapshot's score field MUST equal what health_score() would
    return at the same instant — otherwise the UI badge and the scorer
    disagree about which providers are 'healthy enough to route to'.
    """
    health._clock = lambda: 0.0
    health.record_failure("p2")
    health.record_failure("p2")
    health._clock = lambda: 60.0
    snap = health.snapshot(["p2"], now=60.0)
    assert snap["p2"]["score"] == pytest.approx(health.health_score("p2", now=60.0))


def test_snapshot_without_ids_returns_all_recorded_providers():
    health._clock = lambda: 0.0
    health.record_failure("alpha")
    health.record_success("beta")
    snap = health.snapshot()
    assert set(snap.keys()) == {"alpha", "beta"}


def test_snapshot_returns_fresh_dict_caller_mutation_safe():
    """The caller must be able to mutate the returned dict without
    corrupting the in-memory registry.
    """
    health._clock = lambda: 0.0
    health.record_failure("p")
    snap = health.snapshot(["p"])
    snap["p"]["score"] = -999.0  # caller stomps the value
    # Internal state is unaffected.
    again = health.snapshot(["p"])
    assert again["p"]["score"] >= 0.0


# ---------- V12 fix: 401/403 must not degrade health (Phase 3) ----------


def test_record_failure_skipped_for_401_403(monkeypatch):
    """``utils.http_get`` and ``http_post`` should NOT call
    ``health.record_failure`` for 401/403 responses — those indicate
    caller-side credential misconfig, not upstream fault. Penalizing the
    provider would bias routing away from a provider that's actually
    healthy. This test pins the wiring at the call-site, where the
    decision is made.
    """
    import httpx

    from meta_data_mcp import utils as utils_module
    from meta_data_mcp.errors import AuthError

    request = httpx.Request("GET", "https://example.test/auth")
    response = httpx.Response(status_code=401, request=request)

    # Build a fake httpx.get that returns a 401 response. ``raise_for_status``
    # on the real Response object will raise HTTPStatusError, which is the
    # path that exercises the V12 branch.
    def fake_get(url, **kwargs):
        return response

    monkeypatch.setattr(utils_module.httpx, "get", fake_get)

    # Make sure health starts clean for this provider.
    health.reset("auth-provider")

    with pytest.raises(AuthError):
        utils_module.http_get("https://example.test/auth", provider="auth-provider")

    # No failure should have been recorded — score remains a baseline 1.0.
    assert health.health_score("auth-provider") == 1.0


def test_record_failure_still_fires_for_500(monkeypatch):
    """Sanity counterpart: non-auth status codes (e.g. 500) DO degrade health.
    Without this the V12 branch could silently swallow all failures.
    """
    import httpx

    from meta_data_mcp import utils as utils_module
    from meta_data_mcp.errors import UpstreamError

    request = httpx.Request("GET", "https://example.test/svc")
    response = httpx.Response(status_code=500, request=request)

    def fake_get(url, **kwargs):
        return response

    monkeypatch.setattr(utils_module.httpx, "get", fake_get)

    # Disable retries for this test so we get one shot at the failure.
    monkeypatch.setenv("OPENDATA_MCP_HTTP_RETRIES", "0")

    health.reset("svc-provider")
    with pytest.raises(UpstreamError):
        utils_module.http_get("https://example.test/svc", provider="svc-provider")
    assert health.health_score("svc-provider") < 1.0
