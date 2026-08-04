import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_opensky import (
    TOOLS,
    _opensky_states_to_shape_payload,
    handle_get_flights_aircraft,
    handle_get_flights_arrival,
    handle_get_flights_departure,
    handle_get_states_all,
    handle_get_states_by_aircraft,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_opensky_states_all_success():
    """Now returns the geofeatures shape payload — coords lift to top-level
    lat/lon and named fields go into attrs.
    """
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "time": 1700000000,
            "states": [
                # Indexed: icao24, callsign, country, time_pos, last_contact,
                #         lon, lat, baro_alt, on_ground, velocity, ...
                [
                    "abc123",
                    "AAL123  ",
                    "United States",
                    1700000000,
                    1700000000,
                    -74.0,
                    40.5,
                    11000.0,
                    False,
                    230.5,
                    270.0,
                    None,
                    None,
                    11000.0,
                    "1200",
                    False,
                    0,
                ],
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_states_all(
            {"lamin": 40.0, "lomin": -75.0, "lamax": 41.0, "lomax": -74.0},
        )
        assert "abc123" in result[0].text


@pytest.mark.anyio
async def test_opensky_states_all_no_bbox():
    """Empty states still produces a valid geofeatures envelope."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"time": 1, "states": []}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_states_all({})
        assert "features" in result[0].text


@pytest.mark.anyio
async def test_opensky_states_by_aircraft_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "time": 1,
            "states": [["deadbe", "TEST1"]],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_states_by_aircraft({"icao24": "deadbe"})
        assert "TEST1" in result[0].text


@pytest.mark.anyio
async def test_opensky_states_by_aircraft_missing_icao():
    with pytest.raises(ValueError, match="icao24 is required"):
        await handle_get_states_by_aircraft({})


@pytest.mark.anyio
async def test_opensky_flights_aircraft_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"icao24": "deadbe", "firstSeen": 1, "lastSeen": 2},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_flights_aircraft(
            {"icao24": "deadbe", "begin": 1, "end": 2},
        )
        assert "deadbe" in result[0].text


@pytest.mark.anyio
async def test_opensky_flights_arrival_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"estArrivalAirport": "EDDF", "icao24": "abc"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_flights_arrival(
            {"airport": "EDDF", "begin": 100, "end": 200},
        )
        assert "EDDF" in result[0].text


@pytest.mark.anyio
async def test_opensky_flights_departure_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"estDepartureAirport": "KJFK", "icao24": "xyz"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_flights_departure(
            {"airport": "KJFK", "begin": 100, "end": 200},
        )
        assert "KJFK" in result[0].text


@pytest.mark.anyio
async def test_opensky_flights_arrival_missing_args():
    with pytest.raises(ValueError, match="airport, begin, and end are required"):
        await handle_get_flights_arrival({"airport": "EDDF"})


@pytest.mark.anyio
async def test_opensky_http_error_propagates():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_get_states_all({})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for opensky-get-states-all.
# ---------------------------------------------------------------------------


def test_adapter_maps_state_vectors_to_features():
    raw = {
        "time": 1700000000,
        "states": [
            [
                "abc123",
                "AAL123  ",
                "United States",
                1700000000,
                1700000000,
                -74.0,
                40.5,
                11000.0,
                False,
                230.5,
                270.0,
            ],
        ],
    }
    payload = _opensky_states_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    feature = payload["features"][0]
    assert feature["lat"] == 40.5
    assert feature["lon"] == -74.0
    # Callsign is trimmed (OpenSky right-pads to 8 chars)
    assert feature["attrs"]["callsign"] == "AAL123"
    assert feature["attrs"]["icao24"] == "abc123"
    assert feature["attrs"]["origin_country"] == "United States"
    assert feature["attrs"]["baro_altitude"] == 11000.0
    assert feature["attrs"]["on_ground"] is False
    assert feature["attrs"]["velocity"] == 230.5
    assert feature["attrs"]["true_track"] == 270.0


def test_adapter_handles_empty_states():
    payload = _opensky_states_to_shape_payload({"time": 1, "states": []})
    assert payload == {"features": []}


def test_adapter_handles_non_dict_response():
    assert _opensky_states_to_shape_payload("error") == {"features": []}
    assert _opensky_states_to_shape_payload({"states": "bad"}) == {"features": []}


def test_adapter_skips_invalid_state_vectors():
    raw = {
        "states": [
            ["short"],  # too few elements
            None,  # not a list
            ["a", "b", "c", 1, 2, None, 40.0, 100.0],  # missing lon
            ["a", "b", "c", 1, 2, "bad", 40.0, 100.0],  # bad lon
            ["a", "b", "c", 1, 2, -74.0, 200.0, 100.0],  # lat out of range
            ["ok123", "X       ", "X", 1, 2, -74.0, 40.5, 100.0],  # valid
        ],
    }
    payload = _opensky_states_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["icao24"] == "ok123"


def test_states_all_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "opensky-get-states-all")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_opensky_states_all_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "time": 1700000000,
            "states": [
                [
                    "abc123",
                    "AAL123  ",
                    "United States",
                    1700000000,
                    1700000000,
                    -74.0,
                    40.5,
                    11000.0,
                    False,
                    230.5,
                    270.0,
                ],
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_states_all({})
        body = json.loads(result[0].text)
        assert body["features"][0]["lat"] == 40.5
        assert body["features"][0]["lon"] == -74.0
        assert body["features"][0]["attrs"]["icao24"] == "abc123"


@pytest.mark.anyio
async def test_opensky_states_all_caps_features_to_valid_json():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "time": 1700000000,
            "states": [
                [
                    f"icao{i:04d}",
                    f"CALL{i:04d}",
                    "United States",
                    1700000000,
                    1700000000,
                    -74.0,
                    40.5,
                    11000.0,
                    False,
                    230.5,
                    270.0,
                ]
                for i in range(2000)
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_states_all({})

        body = json.loads(result[0].text)
        assert isinstance(body["features"], list)
        assert 0 < len(body["features"]) < 2000
