import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_unesco_heritage import (
    TOOLS,
    _unesco_sites_to_shape_payload,
    handle_unesco_heritage_get_site,
    handle_unesco_heritage_list_sites,
    handle_unesco_heritage_search,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_unesco_heritage_list_sites_success():
    """Now returns the geofeatures shape payload — only sites with
    usable coordinates make it through.
    """
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {
                "id_number": 1,
                "site": "Galápagos Islands",
                "states": "EC",
                "region": "LAC",
                "category": "Natural",
                "date_inscribed": 1978,
                "latitude": "-0.7",
                "longitude": "-90.5",
            },
            {
                "id_number": 456,
                "site": "Taj Mahal",
                "states": "IN",
                "region": "APA",
                "category": "Cultural",
                "date_inscribed": 1983,
                "latitude": "27.175015",
                "longitude": "78.042111",
            },
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_unesco_heritage_list_sites({})
        assert "Galápagos Islands" in result[0].text
        assert "Taj Mahal" in result[0].text


@pytest.mark.anyio
async def test_unesco_heritage_list_sites_with_filters():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = []
        mock_get.return_value.raise_for_status = Mock()

        await handle_unesco_heritage_list_sites(
            {"iso": "FR", "category": "Cultural", "danger": 0},
        )
        call_params = mock_get.call_args.kwargs.get("params", {})
        assert call_params.get("iso") == "FR"
        assert call_params.get("category") == "Cultural"


@pytest.mark.anyio
async def test_unesco_heritage_list_sites_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Server error")
        with pytest.raises(httpx.HTTPError):
            await handle_unesco_heritage_list_sites({})


@pytest.mark.anyio
async def test_unesco_heritage_get_site_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "id_number": 456,
            "site": "Taj Mahal",
            "states": "IN",
            "region": "APA",
            "category": "Cultural",
            "date_inscribed": 1983,
            "latitude": "27.175015",
            "longitude": "78.042111",
            "area_hectares": 42.0,
            "short_description": "An immense mausoleum of white marble...",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_unesco_heritage_get_site({"site_id": 456})
        assert "Taj Mahal" in result[0].text
        assert "456" in result[0].text


@pytest.mark.anyio
async def test_unesco_heritage_get_site_missing_param():
    with pytest.raises(ValueError):
        await handle_unesco_heritage_get_site({})


@pytest.mark.anyio
async def test_unesco_heritage_search_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {
                "id_number": 154,
                "site": "Venice and its Lagoon",
                "states": "IT",
                "category": "Cultural",
            },
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_unesco_heritage_search({"name": "Venice"})
        assert "Venice" in result[0].text


@pytest.mark.anyio
async def test_unesco_heritage_search_missing_param():
    with pytest.raises(ValueError):
        await handle_unesco_heritage_search({})


@pytest.mark.anyio
async def test_unesco_heritage_search_passes_name_param():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = []
        mock_get.return_value.raise_for_status = Mock()

        await handle_unesco_heritage_search({"name": "Great Barrier Reef"})
        call_params = mock_get.call_args.kwargs.get("params", {})
        assert call_params.get("name") == "Great Barrier Reef"


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for unesco-heritage-list-sites.
# ---------------------------------------------------------------------------


def test_adapter_maps_sites_to_features():
    raw = [
        {
            "id_number": 456,
            "site": "Taj Mahal",
            "latitude": "27.175015",
            "longitude": "78.042111",
            "category": "Cultural",
        },
        {
            "id_number": 1,
            "site": "Galápagos Islands",
            "latitude": "-0.7",
            "longitude": "-90.5",
        },
    ]
    payload = _unesco_sites_to_shape_payload(raw)
    assert len(payload["features"]) == 2
    assert payload["features"][0]["lat"] == 27.175015
    assert payload["features"][0]["lon"] == 78.042111
    assert payload["features"][0]["attrs"]["site"] == "Taj Mahal"
    # Coordinate keys stripped from attrs (already promoted)
    assert "latitude" not in payload["features"][0]["attrs"]
    assert "longitude" not in payload["features"][0]["attrs"]


def test_adapter_handles_empty_list():
    assert _unesco_sites_to_shape_payload([]) == {"features": []}


def test_adapter_handles_non_list_response():
    """An error dict from the API must not crash the adapter."""
    assert _unesco_sites_to_shape_payload({"error": "bad"}) == {"features": []}


def test_adapter_skips_sites_without_coords():
    raw = [
        {"id_number": 1, "site": "no coords"},
        {"id_number": 2, "site": "empty string", "latitude": "", "longitude": ""},
        {"id_number": 3, "site": "bad", "latitude": "x", "longitude": "1.0"},
        {"id_number": 4, "site": "range", "latitude": "200", "longitude": "1"},
        {"id_number": 5, "site": "ok", "latitude": "27.0", "longitude": "78.0"},
    ]
    payload = _unesco_sites_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["id_number"] == 5


def test_list_sites_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "unesco-heritage-list-sites")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_unesco_heritage_list_sites_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {
                "id_number": 456,
                "site": "Taj Mahal",
                "latitude": "27.175015",
                "longitude": "78.042111",
            },
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_unesco_heritage_list_sites({})
        body = json.loads(result[0].text)
        assert body["features"][0]["lat"] == 27.175015
        assert body["features"][0]["lon"] == 78.042111
        assert body["features"][0]["attrs"]["site"] == "Taj Mahal"
