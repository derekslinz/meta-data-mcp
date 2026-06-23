"""Tests for meta_data_mcp.sdk.remote — the remote HTTP discovery client.

SDK-2 (Level 1): connects to a running SSE server over the network.

Tests here mock ``_call_tool`` so no live server is required in CI.
Transport-level integration (the actual SSE handshake) is covered by the
smoke test suite against the hosted server.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def client():
    from meta_data_mcp.sdk.remote import RemoteClient

    return RemoteClient("https://mcp.example.com", token="test-token")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_remote_client_stores_base_url_and_token():
    from meta_data_mcp.sdk.remote import RemoteClient

    c = RemoteClient("https://mcp.example.com/", token="tok")
    assert c._base_url == "https://mcp.example.com"  # trailing slash stripped
    assert c._token == "tok"


def test_remote_client_falls_back_to_env_token(monkeypatch):
    from meta_data_mcp.sdk.remote import RemoteClient

    monkeypatch.setenv("META_DATA_MCP_AUTH_TOKEN", "env-tok")
    c = RemoteClient("https://mcp.example.com")
    assert c._token == "env-tok"


def test_remote_client_bearer_header():
    from meta_data_mcp.sdk.remote import RemoteClient

    c = RemoteClient("https://mcp.example.com", token="secret")
    assert c._headers() == {"Authorization": "Bearer secret"}


def test_remote_client_no_token_no_header():
    from meta_data_mcp.sdk.remote import RemoteClient

    c = RemoteClient("https://mcp.example.com", token="")
    assert c._headers() == {}


# ---------------------------------------------------------------------------
# find_providers
# ---------------------------------------------------------------------------

_FIND_PROVIDERS_PAYLOAD = {
    "count": 2,
    "providers": [
        {
            "id": "us_usgs_earthquake",
            "server_name": "us-usgs-earthquake",
            "title": "USGS Earthquake Hazards",
            "description": "Real-time earthquake data.",
            "domains": ("earth-science",),
            "regions": ("us", "global"),
            "keywords": ("earthquake",),
            "homepage": "https://earthquake.usgs.gov/",
            "license_note": "",
            "requires_env": (),
        },
        {
            "id": "global_gdelt",
            "server_name": "global-gdelt",
            "title": "GDELT Project",
            "description": "Global news events.",
            "domains": ("news",),
            "regions": ("global",),
            "keywords": ("news",),
            "homepage": "https://www.gdeltproject.org/",
            "license_note": "",
            "requires_env": (),
        },
    ],
}


@pytest.mark.anyio
async def test_find_providers_parses_response(client):
    with patch.object(
        client, "_call_tool", new=AsyncMock(return_value=_FIND_PROVIDERS_PAYLOAD)
    ):
        results = await client.find_providers("earthquake", limit=2)

    assert len(results) == 2
    assert results[0]["entry"]["id"] == "us_usgs_earthquake"
    assert results[0]["breakdown"] is None  # explain=False default


@pytest.mark.anyio
async def test_find_providers_explain_populates_breakdown(client):
    payload = dict(
        _FIND_PROVIDERS_PAYLOAD,
        breakdowns={"us_usgs_earthquake": {"token": 0.9, "fuzzy": 0.7}},
    )
    with patch.object(client, "_call_tool", new=AsyncMock(return_value=payload)):
        results = await client.find_providers("earthquake", limit=2, explain=True)

    assert results[0]["breakdown"] == {"token": 0.9, "fuzzy": 0.7}
    assert results[1]["breakdown"] is None  # global_gdelt not in breakdowns


@pytest.mark.anyio
async def test_find_providers_empty_result(client):
    with patch.object(
        client, "_call_tool", new=AsyncMock(return_value={"count": 0, "providers": []})
    ):
        results = await client.find_providers("zzzz_no_match")

    assert results == []


@pytest.mark.anyio
async def test_find_providers_passes_correct_tool_name_and_args(client):
    mock = AsyncMock(return_value={"count": 0, "providers": []})
    with patch.object(client, "_call_tool", new=mock):
        await client.find_providers(
            "floods", domain="earth-science", region="us", limit=5
        )

    mock.assert_called_once_with(
        "opendata_providers_find",
        {
            "query": "floods",
            "domain": "earth-science",
            "region": "us",
            "limit": 5,
            "activate_top": 0,
        },
    )


@pytest.mark.anyio
async def test_find_providers_omits_none_args(client):
    """None values for query/domain/region must not appear in the args dict."""
    mock = AsyncMock(return_value={"count": 0, "providers": []})
    with patch.object(client, "_call_tool", new=mock):
        await client.find_providers(limit=10)

    args = mock.call_args[0][1]
    assert "query" not in args
    assert "domain" not in args
    assert "region" not in args
    assert args["limit"] == 10


# ---------------------------------------------------------------------------
# list_domains / list_regions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_domains(client):
    payload = {"domains": ["earth-science", "finance", "health"]}
    with patch.object(client, "_call_tool", new=AsyncMock(return_value=payload)):
        domains = await client.list_domains()

    assert domains == ["earth-science", "finance", "health"]


@pytest.mark.anyio
async def test_list_regions(client):
    payload = {"regions": ["eu", "global", "us"]}
    with patch.object(client, "_call_tool", new=AsyncMock(return_value=payload)):
        regions = await client.list_regions()

    assert regions == ["eu", "global", "us"]


# ---------------------------------------------------------------------------
# describe_provider
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_describe_provider_returns_entry(client):
    payload = {"provider": {"id": "us_usgs_earthquake", "title": "USGS Earthquake"}}
    with patch.object(client, "_call_tool", new=AsyncMock(return_value=payload)):
        entry = await client.describe_provider("us_usgs_earthquake")

    assert entry["id"] == "us_usgs_earthquake"


@pytest.mark.anyio
async def test_describe_provider_unknown_returns_none(client):
    with patch.object(client, "_call_tool", new=AsyncMock(return_value={})):
        entry = await client.describe_provider("not_real")

    assert entry is None


# ---------------------------------------------------------------------------
# activate_provider
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_activate_provider(client):
    payload = {
        "status": "activated",
        "provider_id": "us_usgs_earthquake",
        "tools_added": 3,
        "new_tool_names": ["usgs-list-events", "usgs-get-event", "usgs-get-count"],
    }
    with patch.object(client, "_call_tool", new=AsyncMock(return_value=payload)):
        result = await client.activate_provider("us_usgs_earthquake")

    assert result["status"] == "activated"
    assert result["tools_added"] == 3


# ---------------------------------------------------------------------------
# health_snapshot
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_snapshot(client):
    payload = {"scores": {"us_usgs_earthquake": 1.0, "global_arxiv": 0.95}}
    with patch.object(client, "_call_tool", new=AsyncMock(return_value=payload)):
        snap = await client.health_snapshot()

    assert snap["us_usgs_earthquake"] == 1.0
    assert snap["global_arxiv"] == 0.95


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_find_providers_propagates_call_tool_exception(client):
    with patch.object(client, "_call_tool", side_effect=RuntimeError("server error")):
        with pytest.raises(RuntimeError, match="server error"):
            await client.find_providers("anything")


# ---------------------------------------------------------------------------
# Context-manager API (mock the MCP transport layer)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_context_manager_uses_persistent_session():
    """Inside async with, find_providers should use the cached _session."""
    from unittest.mock import MagicMock
    from meta_data_mcp.sdk.remote import RemoteClient

    client = RemoteClient("https://mcp.example.com", token="tok")

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()

    from mcp.types import CallToolResult, TextContent

    mock_result = MagicMock(spec=CallToolResult)
    mock_result.isError = False
    mock_result.content = [TextContent(type="text", text='{"count":0,"providers":[]}')]
    mock_session.call_tool = AsyncMock(return_value=mock_result)

    # Simulate entering the context manager by injecting the mock session.
    client._session = mock_session
    results = await client.find_providers("test")
    assert results == []
    mock_session.call_tool.assert_called_once()
