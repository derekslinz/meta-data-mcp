"""Tests for the global-openaq provider."""

import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_openaq import (
    TOOLS,
    OpenAqListLocationsParams,
    OpenAqListParametersParams,
    OpenAqLocationLatestParams,
    _openaq_locations_to_shape_payload,
    fetch_openaq_list_locations,
    fetch_openaq_list_parameters,
    fetch_openaq_location_latest,
    handle_openaq_list_locations,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _ok_response(payload: dict) -> Mock:
    r = Mock()
    r.json.return_value = payload
    r.raise_for_status = Mock()
    r.status_code = 200
    r.headers = {}
    return r


def test_list_locations_sends_coordinates_and_radius():
    payload = {"meta": {"found": 1}, "results": [{"id": 42, "name": "X"}]}
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok_response(payload)
        params = OpenAqListLocationsParams(coordinates="37.77,-122.41", radius=5000)
        result = fetch_openaq_list_locations(params)
        assert result["results"][0]["id"] == 42
        sent = mock_get.call_args[1]["params"]
        assert sent["coordinates"] == "37.77,-122.41"
        assert sent["radius"] == 5000


def test_list_locations_validates_radius_bounds():
    with pytest.raises(Exception):
        OpenAqListLocationsParams(radius=0)
    with pytest.raises(Exception):
        OpenAqListLocationsParams(radius=25001)


def test_location_latest_path_param():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok_response({"results": [{"value": 12.3}]})
        result = fetch_openaq_location_latest(
            OpenAqLocationLatestParams(location_id=42),
        )
        assert result["results"][0]["value"] == 12.3
        assert "/locations/42/latest" in mock_get.call_args[0][0]


def test_location_latest_rejects_invalid_id():
    with pytest.raises(Exception):
        OpenAqLocationLatestParams(location_id=0)


def test_list_parameters():
    payload = {"results": [{"id": 2, "name": "pm25", "units": "µg/m³"}]}
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok_response(payload)
        result = fetch_openaq_list_parameters(OpenAqListParametersParams())
        assert result["results"][0]["name"] == "pm25"


def test_auth_header_sent_when_env_set(monkeypatch):
    monkeypatch.setenv("OPENAQ_API_KEY", "secret-token")
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok_response({"results": []})
        fetch_openaq_list_locations(OpenAqListLocationsParams())
        sent_headers = mock_get.call_args[1]["headers"]
        assert sent_headers["X-API-Key"] == "secret-token"


@pytest.mark.anyio
async def test_handle_list_locations():
    """Now returns the geofeatures shape payload — only locations with
    usable coordinates make it through.
    """
    payload = {
        "results": [
            {
                "id": 1,
                "name": "Test Station",
                "coordinates": {"latitude": 52.37, "longitude": 4.9},
            },
        ],
    }
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok_response(payload)
        result = await handle_openaq_list_locations({"iso": "NL"})
        assert "Test Station" in result[0].text


@pytest.mark.anyio
async def test_handle_translates_404_via_provider_kwarg():
    from meta_data_mcp.errors import NotFoundError

    req = httpx.Request("GET", "https://api.openaq.org/v3/locations")
    resp = httpx.Response(status_code=404, request=req)
    status_err = httpx.HTTPStatusError("not found", request=req, response=resp)

    with patch("httpx.get") as mock_get:
        mock_get.return_value.raise_for_status = Mock(side_effect=status_err)
        mock_get.return_value.status_code = 404
        mock_get.return_value.headers = {}

        with pytest.raises(NotFoundError) as exc_info:
            await handle_openaq_list_locations({"iso": "ZZ"})
        assert exc_info.value.provider == "global-openaq"


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for openaq-list-locations.
# ---------------------------------------------------------------------------


def test_adapter_maps_locations_to_features():
    raw = {
        "results": [
            {
                "id": 1,
                "name": "Berlin Station",
                "coordinates": {"latitude": 52.52, "longitude": 13.41},
                "country": {"id": 50, "code": "DE", "name": "Germany"},
            },
            {
                "id": 2,
                "name": "Paris Station",
                "coordinates": {"latitude": 48.86, "longitude": 2.35},
            },
        ],
    }
    payload = _openaq_locations_to_shape_payload(raw)
    assert len(payload["features"]) == 2
    assert payload["features"][0]["lat"] == 52.52
    assert payload["features"][0]["lon"] == 13.41
    assert payload["features"][0]["attrs"]["name"] == "Berlin Station"
    # coordinates is stripped (already promoted)
    assert "coordinates" not in payload["features"][0]["attrs"]


def test_adapter_handles_empty_results():
    assert _openaq_locations_to_shape_payload({"results": []}) == {"features": []}


def test_adapter_handles_non_dict_response():
    assert _openaq_locations_to_shape_payload([]) == {"features": []}
    assert _openaq_locations_to_shape_payload("err") == {"features": []}


def test_adapter_skips_locations_without_coords():
    raw = {
        "results": [
            {"id": 1, "name": "no coords"},
            {"id": 2, "name": "bad shape", "coordinates": "not a dict"},
            {
                "id": 3,
                "name": "bad value",
                "coordinates": {"latitude": "x", "longitude": 1.0},
            },
            {
                "id": 4,
                "name": "out of range",
                "coordinates": {"latitude": 200.0, "longitude": 1.0},
            },
            {
                "id": 5,
                "name": "ok",
                "coordinates": {"latitude": 52.5, "longitude": 13.4},
            },
        ],
    }
    payload = _openaq_locations_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["id"] == 5


def test_list_locations_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "openaq-list-locations")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_handle_list_locations_returns_shape_payload():
    payload = {
        "results": [
            {
                "id": 1,
                "name": "Berlin Station",
                "coordinates": {"latitude": 52.52, "longitude": 13.41},
            },
        ],
    }
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok_response(payload)
        result = await handle_openaq_list_locations({"iso": "DE"})
        body = json.loads(result[0].text)
        assert body["features"][0]["lat"] == 52.52
        assert body["features"][0]["lon"] == 13.41
        assert body["features"][0]["attrs"]["name"] == "Berlin Station"
