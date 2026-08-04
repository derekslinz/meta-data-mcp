import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_osm_nominatim import (
    TOOLS,
    NominatimLookupParams,
    NominatimReverseParams,
    NominatimSearchParams,
    NominatimSearchStructuredParams,
    NominatimStatusParams,
    _nominatim_search_results_to_shape_payload,
    fetch_lookup,
    fetch_reverse,
    fetch_search,
    fetch_search_structured,
    fetch_status,
    handle_lookup,
    handle_reverse,
    handle_search,
    handle_search_structured,
    handle_status,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_search_response():
    return [
        {
            "place_id": 1,
            "display_name": "Berlin, Germany",
            "lat": "52.52",
            "lon": "13.41",
        },
    ]


@pytest.fixture
def mock_reverse_response():
    return {
        "place_id": 2,
        "display_name": "Brandenburg Gate, Berlin, Germany",
        "address": {"city": "Berlin"},
    }


def test_fetch_search(mock_search_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_search_response
        mock_get.return_value.raise_for_status = Mock()

        params = NominatimSearchParams(q="Berlin", limit=5)
        data = fetch_search(params)
        assert data[0]["display_name"] == "Berlin, Germany"


@pytest.mark.anyio
async def test_handle_search(mock_search_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_search_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search({"q": "Berlin"})
        assert "Berlin" in result[0].text


@pytest.mark.anyio
async def test_handle_search_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("network down")
        with pytest.raises(httpx.HTTPError):
            await handle_search({"q": "Berlin"})


@pytest.mark.anyio
async def test_handle_search_missing_arg():
    with pytest.raises(ValueError, match="q is required"):
        await handle_search({})


def test_fetch_reverse(mock_reverse_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_reverse_response
        mock_get.return_value.raise_for_status = Mock()

        params = NominatimReverseParams(lat=52.52, lon=13.41)
        data = fetch_reverse(params)
        assert "Brandenburg Gate" in data["display_name"]


@pytest.mark.anyio
async def test_handle_reverse(mock_reverse_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_reverse_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_reverse({"lat": 52.52, "lon": 13.41})
        assert "Brandenburg Gate" in result[0].text


@pytest.mark.anyio
async def test_handle_reverse_missing_arg():
    with pytest.raises(ValueError, match="lat and lon are required"):
        await handle_reverse({"lat": 52.52})


def test_fetch_lookup():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"osm_id": 123, "display_name": "Place A"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        params = NominatimLookupParams(osm_ids="N123,W456")
        data = fetch_lookup(params)
        assert data[0]["osm_id"] == 123


@pytest.mark.anyio
async def test_handle_lookup():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"osm_id": 123, "display_name": "Place A"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_lookup({"osm_ids": "N123,W456"})
        assert "Place A" in result[0].text


@pytest.mark.anyio
async def test_handle_lookup_missing_arg():
    with pytest.raises(ValueError, match="osm_ids is required"):
        await handle_lookup({})


def test_fetch_status():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": 0,
            "message": "OK",
        }
        mock_get.return_value.raise_for_status = Mock()

        data = fetch_status(NominatimStatusParams())
        assert data["status"] == 0
        assert data["message"] == "OK"


@pytest.mark.anyio
async def test_handle_status():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"status": 0, "message": "OK"}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_status({})
        assert "OK" in result[0].text


def test_fetch_search_structured():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {
                "place_id": 42,
                "display_name": "1600 Pennsylvania Ave NW, Washington, DC",
            },
        ]
        mock_get.return_value.raise_for_status = Mock()

        params = NominatimSearchStructuredParams(
            country="USA",
            city="Washington",
            street="1600 Pennsylvania Ave NW",
        )
        data = fetch_search_structured(params)
        assert data[0]["place_id"] == 42


@pytest.mark.anyio
async def test_handle_search_structured():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {
                "place_id": 42,
                "display_name": "1600 Pennsylvania Ave NW, Washington, DC",
            },
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_structured(
            {"country": "USA", "city": "Washington"},
        )
        assert "Washington" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for nominatim-search.
# ---------------------------------------------------------------------------


def test_adapter_maps_search_results_to_features():
    raw = [
        {
            "place_id": 1,
            "display_name": "Berlin, Germany",
            "lat": "52.52",
            "lon": "13.41",
            "class": "place",
        },
        {
            "place_id": 2,
            "display_name": "Paris, France",
            "lat": "48.86",
            "lon": "2.35",
        },
    ]
    payload = _nominatim_search_results_to_shape_payload(raw)
    assert len(payload["features"]) == 2
    assert payload["features"][0]["lat"] == 52.52
    assert payload["features"][0]["lon"] == 13.41
    # Non-coordinate fields land in attrs
    assert payload["features"][0]["attrs"]["display_name"] == "Berlin, Germany"
    assert payload["features"][0]["attrs"]["place_id"] == 1
    assert "lat" not in payload["features"][0]["attrs"]
    assert "lon" not in payload["features"][0]["attrs"]


def test_adapter_handles_empty_list():
    assert _nominatim_search_results_to_shape_payload([]) == {"features": []}


def test_adapter_handles_non_list_response():
    """Nominatim returns a list on success; an error response (dict) must
    not crash the adapter.
    """
    assert _nominatim_search_results_to_shape_payload({"error": "bad"}) == {
        "features": [],
    }


def test_adapter_skips_invalid_coords_defensively():
    raw = [
        {"lat": "bad", "lon": "13.4"},
        {"lat": "100.0", "lon": "13.4"},  # out of range
        {"lat": "0.0", "lon": "-181.0"},  # out of range
        {"display_name": "no coords"},  # missing
        {"lat": "0.0", "lon": "0.0", "place_id": 99},
    ]
    payload = _nominatim_search_results_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["place_id"] == 99


def test_search_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "nominatim-search")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_handle_search_returns_shape_payload(mock_search_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_search_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search({"q": "Berlin"})
        body = json.loads(result[0].text)
        assert "features" in body
        assert body["features"][0]["lat"] == 52.52
        assert body["features"][0]["lon"] == 13.41
        assert body["features"][0]["attrs"]["display_name"] == "Berlin, Germany"
