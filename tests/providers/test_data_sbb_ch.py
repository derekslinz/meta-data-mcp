import json
from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.ch_sbb import (
    TOOLS,
    RailwayLineParams,
    RollingStockParams,
    TrafficInfoParams,
    _railway_lines_to_shape_payload,
    fetch_rail_traffic_info,
    fetch_railway_lines,
    fetch_rolling_stock,
    handle_rail_traffic_info,
    handle_railway_lines,
    handle_rolling_stock,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


###################
# Rail Traffic Information
###################


@pytest.fixture
def mock_traffic_info_response():
    return {
        "total_count": 2,
        "results": [
            {
                "title": "Track maintenance in Zürich",
                "link": "https://data.sbb.ch/info/1",
                "description": "Maintenance work between Zürich HB and Oerlikon",
                "published": "2024-01-01T10:00:00Z",
                "author": "SBB",
                "validitybegin": "2024-01-02T00:00:00Z",
                "validityend": "2024-01-03T00:00:00Z",
                "description_html": "<p>Maintenance work between Zürich HB and Oerlikon</p>",
            },
            {
                "title": "Delays in Bern",
                "link": "https://data.sbb.ch/info/2",
                "description": "Signal failure causing delays",
                "published": "2024-01-01T11:00:00Z",
                "author": "SBB",
                "validitybegin": "2024-01-01T11:00:00Z",
                "validityend": "2024-01-01T15:00:00Z",
                "description_html": "<p>Signal failure causing delays</p>",
            },
        ],
    }


def test_fetch_rail_traffic_info(mock_traffic_info_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_traffic_info_response
        mock_get.return_value.raise_for_status = Mock()

        params = TrafficInfoParams(limit=2, timezone="Europe/Zurich")
        response = fetch_rail_traffic_info(params)

        assert response.total_count == 2
        assert len(response.results) == 2
        assert response.results[0].title == "Track maintenance in Zürich"
        assert response.results[1].title == "Delays in Bern"


@pytest.mark.anyio
async def test_handle_rail_traffic_info(mock_traffic_info_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_traffic_info_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_rail_traffic_info({"limit": 2})

        assert len(result) == 1
        assert result[0].type == "text"
        assert "Track maintenance in Zürich" in result[0].text
        assert "Delays in Bern" in result[0].text


###################
# Railway Line Information
###################


@pytest.fixture
def mock_railway_line_response():
    return {
        "total_count": 2,
        "results": [
            {
                "linie": 100,
                "linienname": "Zürich HB - Bern",
                "bpk_anfang": "Zürich HB",
                "bpk_ende": "Bern",
                "km_anfang": 0.0,
                "km_ende": 120.5,
                "stationierung_anfang": 0,
                "stationierung_ende": 120500,
                "tst": {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[8.540192, 47.378177], [7.439122, 46.949083]],
                    },
                    "properties": {},
                },
                "geo_point_2d": {"lon": 7.989657, "lat": 47.163630},
            },
            {
                "linie": 200,
                "linienname": "Basel - Luzern",
                "bpk_anfang": "Basel SBB",
                "bpk_ende": "Luzern",
                "km_anfang": 0.0,
                "km_ende": 105.8,
                "stationierung_anfang": 0,
                "stationierung_ende": 105800,
                "tst": {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[7.589576, 47.547184], [8.310485, 47.050168]],
                    },
                    "properties": {},
                },
                "geo_point_2d": {"lon": 7.950031, "lat": 47.298676},
            },
        ],
    }


def test_fetch_railway_lines(mock_railway_line_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_railway_line_response
        mock_get.return_value.raise_for_status = Mock()

        params = RailwayLineParams(limit=2)
        response = fetch_railway_lines(params)

        assert response.total_count == 2
        assert len(response.results) == 2
        assert response.results[0].linie == 100
        assert response.results[0].linienname == "Zürich HB - Bern"
        assert response.results[1].linie == 200
        assert response.results[1].linienname == "Basel - Luzern"


@pytest.mark.anyio
async def test_handle_railway_lines(mock_railway_line_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_railway_line_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_railway_lines({"limit": 2})

        assert len(result) == 1
        assert result[0].type == "text"
        assert "Zürich HB - Bern" in result[0].text
        assert "Basel - Luzern" in result[0].text


###################
# Rolling Stock Information
###################


@pytest.fixture
def mock_rolling_stock_response():
    return {
        "total_count": 2,
        "results": [
            {
                "fahrzeug_art_struktur": "Locomotive",
                "fahrzeug_typ": "Re 460",
                "objekt": "460 001-1",
                "baudatum_fahrzeug": "1991-01-15",
                "eigengewicht_tara": 84.0,
                "lange_uber_puffer_lup": 18640,
                "vmax_betrieblich_zugelassen": 200,
            },
            {
                "fahrzeug_art_struktur": "Passenger Car",
                "fahrzeug_typ": "IC 2000",
                "objekt": "IC2000-1234",
                "baudatum_fahrzeug": "1997-06-20",
                "eigengewicht_tara": 42.5,
                "lange_uber_puffer_lup": 26100,
                "vmax_betrieblich_zugelassen": 200,
            },
        ],
    }


def test_fetch_rolling_stock(mock_rolling_stock_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_rolling_stock_response
        mock_get.return_value.raise_for_status = Mock()

        params = RollingStockParams(limit=2)
        response = fetch_rolling_stock(params)

        assert response.total_count == 2
        assert len(response.results) == 2
        assert response.results[0].fahrzeug_typ == "Re 460"
        assert response.results[1].fahrzeug_typ == "IC 2000"


@pytest.mark.anyio
async def test_handle_rolling_stock(mock_rolling_stock_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_rolling_stock_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_rolling_stock({"limit": 2})

        assert len(result) == 1
        assert result[0].type == "text"
        assert "Re 460" in result[0].text
        assert "IC 2000" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for railway-lines.
# ---------------------------------------------------------------------------


def test_adapter_maps_railway_lines_to_features():
    raw = {
        "results": [
            {
                "linie": 100,
                "linienname": "Zürich HB - Bern",
                "geo_point_2d": {"lon": 7.989657, "lat": 47.163630},
            },
            {
                "linie": 200,
                "linienname": "Basel - Luzern",
                "geo_point_2d": {"lon": 7.950031, "lat": 47.298676},
            },
        ],
    }
    payload = _railway_lines_to_shape_payload(raw)
    assert len(payload["features"]) == 2
    assert payload["features"][0]["lat"] == 47.163630
    assert payload["features"][0]["lon"] == 7.989657
    assert payload["features"][0]["attrs"]["linienname"] == "Zürich HB - Bern"
    # geo_point_2d is stripped from attrs (already promoted)
    assert "geo_point_2d" not in payload["features"][0]["attrs"]


def test_adapter_handles_empty_results():
    payload = _railway_lines_to_shape_payload({"results": []})
    assert payload == {"features": []}


def test_adapter_handles_missing_structure():
    assert _railway_lines_to_shape_payload({}) == {"features": []}
    assert _railway_lines_to_shape_payload("error") == {"features": []}


def test_adapter_skips_lines_without_geo_point():
    """Lines without a geo_point_2d block (or with invalid coords) are
    dropped silently so the bundle never has to render bad pins.
    """
    raw = {
        "results": [
            {"linie": 1, "linienname": "no point"},
            {"linie": 2, "geo_point_2d": "not a dict"},
            {"linie": 3, "geo_point_2d": {"lon": "bad", "lat": 1.0}},
            {"linie": 4, "geo_point_2d": {"lon": 200.0, "lat": 1.0}},  # range
            {"linie": 5, "geo_point_2d": {"lon": 7.0, "lat": 47.0}},
        ],
    }
    payload = _railway_lines_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["linie"] == 5


def test_railway_lines_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "railway-lines")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_handle_railway_lines_returns_shape_payload(
    mock_railway_line_response,
):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_railway_line_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_railway_lines({"limit": 2})
        body = json.loads(result[0].text)
        assert "features" in body
        assert len(body["features"]) == 2
        assert body["features"][0]["lat"] == 47.16363
        assert body["features"][0]["lon"] == 7.989657
        assert body["features"][0]["attrs"]["linienname"] == "Zürich HB - Bern"
