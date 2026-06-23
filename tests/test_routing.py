import pytest

from meta_data_mcp.registry import REGISTRY, ProviderEntry, register_plugin
from meta_data_mcp.routing import RoutingEngine


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_route_without_query_returns_filtered_results():
    engine = RoutingEngine()
    results = await engine.route(domain="health", limit=5)
    assert results
    assert all("health" in result.entry.domains for result in results)
    assert all(result.score == 1.0 for result in results)


@pytest.mark.anyio
async def test_route_cache_distinguishes_explain_and_limit():
    engine = RoutingEngine()

    first = await engine.route(query="earthquake", limit=1, explain=False)
    assert first

    second = await engine.route(query="earthquake", limit=5, explain=False)
    assert len(second) >= len(first)

    explained = await engine.route(query="earthquake", limit=1, explain=True)
    assert explained
    assert explained[0].breakdown is not None


@pytest.mark.anyio
async def test_route_filters_are_case_insensitive():
    engine = RoutingEngine()
    lower = await engine.route(region="uk", limit=10)
    upper = await engine.route(region="UK", limit=10)
    assert [item.entry.id for item in lower] == [item.entry.id for item in upper]


@pytest.mark.anyio
async def test_route_sees_dynamically_registered_plugins():
    """A plugin hot-loaded via register_plugin() must appear in route() results.

    Regression guard: routing.py used to iterate the static REGISTRY tuple
    directly, so plugins added to DYNAMIC_REGISTRY (the in-memory list that
    `opendata_plugins_create` writes to) were invisible to the routing engine.
    Now it iterates `iter_registry()` which yields both lists.
    """
    sentinel_id = "_test_dynamic_route_sentinel"
    REGISTRY.remove(sentinel_id)  # clean any prior run
    try:
        register_plugin(
            ProviderEntry(
                id=sentinel_id,
                server_name="_test-dynamic-route-sentinel",
                title="Dynamic Routing Sentinel",
                description="A made-up provider used to exercise dynamic-registry routing.",
                domains=("security",),
                regions=("global",),
                keywords=(
                    "dynamic-routing-sentinel-keyword",  # unique token, low collision risk
                ),
                homepage="https://example.invalid/",
            )
        )
        engine = RoutingEngine()
        results = await engine.route(query="dynamic-routing-sentinel-keyword", limit=5)
        assert any(r.entry.id == sentinel_id for r in results), (
            f"dynamic plugin not visible in route() output; got {[r.entry.id for r in results]}"
        )
    finally:
        REGISTRY.remove(sentinel_id)
