import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_census_geocoder import (
    TOOLS,
    TOOLS_HANDLERS,
    _census_address_matches_to_shape_payload,
    handle_census_benchmarks,
    handle_census_geocode_address,
    handle_census_geocode_address_geographies,
    handle_census_geocode_coordinates,
    handle_census_geocode_oneline,
    handle_census_geocode_oneline_geographies,
    handle_census_vintages,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_tools_registered():
    """All registered tools must have a matching handler entry."""
    names = [t.name for t in TOOLS]
    for name in names:
        assert name in TOOLS_HANDLERS


@pytest.mark.anyio
async def test_census_geocode_oneline_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": {
                "addressMatches": [{"matchedAddress": "1600 PENNSYLVANIA AVE NW"}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_census_geocode_oneline(
            {"address": "1600 Pennsylvania Ave NW, Washington DC 20500"},
        )
        assert len(result) == 1
        assert "PENNSYLVANIA" in result[0].text


@pytest.mark.anyio
async def test_census_geocode_oneline_missing_address():
    with pytest.raises(ValueError):
        await handle_census_geocode_oneline({})


@pytest.mark.anyio
async def test_census_geocode_address_success():
    """Returns the geofeatures shape payload — coords lift to top-level
    lat/lon and the rest of the match goes into attrs.
    """
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": {
                "addressMatches": [
                    {
                        "coordinates": {"x": -77.0, "y": 38.9},
                        "matchedAddress": "1600 PENNSYLVANIA AVE NW",
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_census_geocode_address(
            {
                "street": "1600 Pennsylvania Ave NW",
                "city": "Washington",
                "state": "DC",
                "zip": "20500",
            },
        )
        body = json.loads(result[0].text)
        assert body["features"][0]["lat"] == 38.9
        assert body["features"][0]["lon"] == -77.0


@pytest.mark.anyio
async def test_census_geocode_coordinates_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": {"geographies": {"Counties": [{"NAME": "District of Columbia"}]}},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_census_geocode_coordinates({"x": -77.0365, "y": 38.8977})
        assert "District of Columbia" in result[0].text


@pytest.mark.anyio
async def test_census_geocode_coordinates_missing_args():
    with pytest.raises(ValueError):
        await handle_census_geocode_coordinates({"x": -77.0})


@pytest.mark.anyio
async def test_census_geocode_oneline_geographies_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": {
                "addressMatches": [
                    {"geographies": {"Census Blocks": [{"GEOID": "110010001"}]}},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_census_geocode_oneline_geographies(
            {"address": "1600 Pennsylvania Ave NW, Washington DC"},
        )
        assert "GEOID" in result[0].text


@pytest.mark.anyio
async def test_census_geocode_address_geographies_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": {"addressMatches": [{"matchedAddress": "EXAMPLE"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_census_geocode_address_geographies(
            {"street": "1 Infinite Loop", "city": "Cupertino", "state": "CA"},
        )
        assert "EXAMPLE" in result[0].text


@pytest.mark.anyio
async def test_census_benchmarks_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "benchmarks": [{"benchmarkName": "Public_AR_Current"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_census_benchmarks()
        assert "Public_AR_Current" in result[0].text


@pytest.mark.anyio
async def test_census_vintages_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "vintages": [{"vintageName": "Current_Current"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_census_vintages({"benchmark": "Public_AR_Current"})
        assert "Current_Current" in result[0].text


@pytest.mark.anyio
async def test_census_benchmarks_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_census_benchmarks()


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for census-geocode-address.
# ---------------------------------------------------------------------------


def test_adapter_maps_address_matches_to_features():
    raw = {
        "result": {
            "addressMatches": [
                {
                    "coordinates": {"x": -77.0, "y": 38.9},
                    "matchedAddress": "1600 PENNSYLVANIA AVE NW",
                    "tigerLine": {"tigerLineId": "12345"},
                },
                {
                    "coordinates": {"x": -122.03, "y": 37.33},
                    "matchedAddress": "1 INFINITE LOOP",
                },
            ],
        },
    }
    payload = _census_address_matches_to_shape_payload(raw)
    assert len(payload["features"]) == 2
    assert payload["features"][0]["lat"] == 38.9
    assert payload["features"][0]["lon"] == -77.0
    assert (
        payload["features"][0]["attrs"]["matchedAddress"] == "1600 PENNSYLVANIA AVE NW"
    )
    # coordinates is stripped from attrs (already promoted)
    assert "coordinates" not in payload["features"][0]["attrs"]


def test_adapter_handles_empty_matches():
    payload = _census_address_matches_to_shape_payload(
        {"result": {"addressMatches": []}},
    )
    assert payload == {"features": []}


def test_adapter_handles_missing_structure():
    """Some Census error responses omit the addressMatches key entirely."""
    assert _census_address_matches_to_shape_payload({}) == {"features": []}
    assert _census_address_matches_to_shape_payload({"result": {}}) == {"features": []}
    assert _census_address_matches_to_shape_payload("error") == {"features": []}


def test_adapter_skips_invalid_coords_defensively():
    raw = {
        "result": {
            "addressMatches": [
                {"coordinates": {"x": "bad", "y": 38.9}},
                {"coordinates": {"x": -77.0, "y": 200.0}},  # out of range
                {"matchedAddress": "no coords"},  # missing
                {"coordinates": {"x": 0.0, "y": 0.0}, "matchedAddress": "OK"},
            ],
        },
    }
    payload = _census_address_matches_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["matchedAddress"] == "OK"


def test_geocode_address_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "census-geocode-address")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_handle_census_geocode_address_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": {
                "addressMatches": [
                    {
                        "coordinates": {"x": -77.0365, "y": 38.8977},
                        "matchedAddress": "1600 PENNSYLVANIA AVE NW",
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_census_geocode_address(
            {"street": "1600 Pennsylvania Ave NW"},
        )
        body = json.loads(result[0].text)
        assert body["features"][0]["lat"] == 38.8977
        assert body["features"][0]["lon"] == -77.0365
        assert (
            body["features"][0]["attrs"]["matchedAddress"] == "1600 PENNSYLVANIA AVE NW"
        )
