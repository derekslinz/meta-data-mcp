"""Tests for meta_data_mcp.sdk — the embedded Python discovery client.

SDK-1 (Level 2): uses the routing engine + registry directly,
no HTTP or MCP protocol overhead.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# DiscoveryClient — class-level API
# ---------------------------------------------------------------------------


def test_discovery_client_instantiates():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    assert client._engine is not None


@pytest.mark.anyio
async def test_find_providers_async_returns_ranked_list():
    """find_providers returns a non-empty ranked list for a known query."""
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    results = await client.find_providers("earthquake", limit=5)
    assert isinstance(results, list)
    assert len(results) > 0
    top = results[0]
    assert "entry" in top
    assert "score" in top
    assert "breakdown" in top
    assert isinstance(top["score"], float)
    # The USGS earthquake provider should rank highly.
    ids = [r["entry"]["id"] for r in results]
    assert "us_usgs_earthquake" in ids


@pytest.mark.anyio
async def test_find_providers_explain_populates_breakdown():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    results = await client.find_providers("earthquake", limit=3, explain=True)
    assert results
    # With explain=True every result should carry a breakdown dict.
    for r in results:
        assert r["breakdown"] is not None
        assert isinstance(r["breakdown"], dict)


@pytest.mark.anyio
async def test_find_providers_no_match_returns_empty():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    results = await client.find_providers("zzz_no_such_topic_xyz")
    assert results == []


@pytest.mark.anyio
async def test_find_providers_domain_filter():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    results = await client.find_providers(domain="health", limit=10)
    assert len(results) > 0
    # Every returned provider must include the "health" domain tag.
    for r in results:
        assert "health" in r["entry"]["domains"]


def test_find_providers_sync():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    results = client.find_providers_sync("earthquake", limit=3)
    assert len(results) > 0
    assert results[0]["entry"]["id"] == "us_usgs_earthquake"


def test_list_domains_returns_known_domains():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    domains = client.list_domains()
    assert isinstance(domains, list)
    assert "health" in domains
    assert "finance" in domains
    assert "earth-science" in domains


def test_list_regions_returns_known_regions():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    regions = client.list_regions()
    assert isinstance(regions, list)
    assert "us" in regions
    assert "global" in regions
    assert "eu" in regions


def test_describe_provider_known_id():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    entry = client.describe_provider("us_usgs_earthquake")
    assert entry is not None
    assert entry["id"] == "us_usgs_earthquake"
    assert "domains" in entry
    assert "description" in entry


def test_describe_provider_unknown_id_returns_none():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    assert client.describe_provider("not_a_real_provider_xyz") is None


def test_activate_provider_known_id():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    result = client.activate_provider("us_usgs_earthquake")
    # Either newly activated or already active — both are valid.
    assert result["status"] in ("activated", "already_active")
    assert "provider_id" in result


def test_activate_provider_unknown_id_returns_error():
    from meta_data_mcp.sdk import DiscoveryClient

    client = DiscoveryClient()
    result = client.activate_provider("not_a_real_provider_xyz")
    assert result["status"] == "error"


def test_health_snapshot_covers_all_providers():
    from meta_data_mcp.sdk import DiscoveryClient
    from meta_data_mcp.registry import iter_registry

    client = DiscoveryClient()
    snapshot = client.health_snapshot()
    all_ids = {entry.id for entry in iter_registry()}
    assert all_ids.issubset(snapshot.keys())
    # All scores in [0.0, 1.0].
    for score in snapshot.values():
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Module-level convenience API — mirrors the DiscoveryClient methods
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_module_find_providers():
    import meta_data_mcp.sdk as sdk

    results = await sdk.find_providers("earthquake", limit=3)
    assert len(results) > 0
    assert results[0]["entry"]["id"] == "us_usgs_earthquake"


def test_module_find_providers_sync():
    import meta_data_mcp.sdk as sdk

    results = sdk.find_providers_sync("earthquake", limit=3)
    assert len(results) > 0


def test_module_list_domains():
    import meta_data_mcp.sdk as sdk

    assert "health" in sdk.list_domains()


def test_module_list_regions():
    import meta_data_mcp.sdk as sdk

    assert "global" in sdk.list_regions()


def test_module_describe_provider():
    import meta_data_mcp.sdk as sdk

    entry = sdk.describe_provider("global_arxiv")
    assert entry is not None
    assert entry["id"] == "global_arxiv"


def test_module_health_snapshot():
    import meta_data_mcp.sdk as sdk

    snap = sdk.health_snapshot()
    assert "us_usgs_earthquake" in snap
    assert isinstance(snap["us_usgs_earthquake"], float)


def test_module_activate_provider():
    import meta_data_mcp.sdk as sdk

    result = sdk.activate_provider("us_usgs_earthquake")
    assert result["status"] in ("activated", "already_active")


def test_multiple_clients_independent_engines():
    """Two DiscoveryClient instances should have independent caches."""
    from meta_data_mcp.sdk import DiscoveryClient

    a = DiscoveryClient()
    b = DiscoveryClient()
    assert a._engine is not b._engine
