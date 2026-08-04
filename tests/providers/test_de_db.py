import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.de_db import (
    TOOLS,
    _db_stations_to_shape_payload,
    handle_get_timetable,
    handle_list_stations,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_db_list_stations_success():
    """Now returns the geofeatures shape payload — only stations with
    usable coordinates make it through.
    """
    with patch("httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {
                "id": "8011160",
                "name": "Berlin Hbf",
                "latitude": 52.525592,
                "longitude": 13.369545,
            },
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_stations({"search": "Berlin"})
        assert len(result) == 1
        assert "Berlin Hbf" in result[0].text
        mock_get.assert_called_once()


@pytest.mark.anyio
async def test_db_list_stations_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Connection failed")

        with pytest.raises(httpx.HTTPError):
            await handle_list_stations({"search": "Berlin"})


@pytest.mark.anyio
async def test_db_get_timetable_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {
                "tripId": "123",
                "direction": "Munich",
                "plannedWhen": "2024-01-01T12:00:00Z",
                "line": {"name": "ICE 1"},
            },
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_timetable({"station_id": "8011160"})
        assert len(result) == 1
        assert "ICE 1" in result[0].text
        assert "Munich" in result[0].text


@pytest.mark.anyio
async def test_db_get_timetable_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Timeout")

        with pytest.raises(httpx.HTTPError):
            await handle_get_timetable({"station_id": "8011160"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for db-list-stations.
# ---------------------------------------------------------------------------


def test_adapter_maps_stations_with_top_level_coords():
    raw = [
        {
            "id": "8011160",
            "name": "Berlin Hbf",
            "latitude": 52.525592,
            "longitude": 13.369545,
        },
        {
            "id": "8000105",
            "name": "Frankfurt(Main)Hbf",
            "latitude": 50.107145,
            "longitude": 8.663785,
        },
    ]
    payload = _db_stations_to_shape_payload(raw)
    assert len(payload["features"]) == 2
    assert payload["features"][0]["lat"] == 52.525592
    assert payload["features"][0]["lon"] == 13.369545
    assert payload["features"][0]["attrs"]["name"] == "Berlin Hbf"
    # Coordinates stripped from attrs (already promoted)
    assert "latitude" not in payload["features"][0]["attrs"]
    assert "longitude" not in payload["features"][0]["attrs"]


def test_adapter_falls_back_to_nested_location():
    """Some DB endpoints nest coords under a ``location`` block."""
    raw = [
        {
            "id": "8011160",
            "name": "Berlin Hbf",
            "location": {"latitude": 52.525592, "longitude": 13.369545},
        },
    ]
    payload = _db_stations_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["lat"] == 52.525592
    assert payload["features"][0]["lon"] == 13.369545
    # location is stripped from attrs (already promoted)
    assert "location" not in payload["features"][0]["attrs"]


def test_adapter_handles_empty_list():
    assert _db_stations_to_shape_payload([]) == {"features": []}


def test_adapter_handles_non_list_response():
    """An error dict from the API must not crash the adapter."""
    assert _db_stations_to_shape_payload({"error": "bad"}) == {"features": []}


def test_adapter_skips_stations_without_coords():
    raw = [
        {"id": "1", "name": "no coords"},
        {"id": "2", "name": "bad coords", "latitude": "x", "longitude": 1.0},
        {"id": "3", "name": "out of range", "latitude": 100.0, "longitude": 1.0},
        {"id": "4", "name": "ok", "latitude": 52.5, "longitude": 13.4},
    ]
    payload = _db_stations_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["id"] == "4"


def test_list_stations_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "db-list-stations")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_db_list_stations_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {
                "id": "8011160",
                "name": "Berlin Hbf",
                "latitude": 52.525592,
                "longitude": 13.369545,
            },
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_stations({"search": "Berlin"})
        body = json.loads(result[0].text)
        assert body["features"][0]["lat"] == 52.525592
        assert body["features"][0]["lon"] == 13.369545
        assert body["features"][0]["attrs"]["name"] == "Berlin Hbf"
