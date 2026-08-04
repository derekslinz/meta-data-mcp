import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_usgs_earthquake import (
    TOOLS,
    _usgs_geojson_to_shape_payload,
    handle_usgs_eq_application_version,
    handle_usgs_eq_count,
    handle_usgs_eq_feed_all_day,
    handle_usgs_eq_feed_all_week,
    handle_usgs_eq_feed_m45_week,
    handle_usgs_eq_feed_significant_day,
    handle_usgs_eq_feed_significant_week,
    handle_usgs_eq_query,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_usgs_eq_query_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "type": "FeatureCollection",
            "metadata": {"count": 2},
            "features": [
                {"id": "us1", "properties": {"mag": 5.1, "place": "Off the coast"}},
                {"id": "us2", "properties": {"mag": 4.7, "place": "Alaska"}},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_query(
            {"starttime": "2024-01-01", "endtime": "2024-01-02", "minmagnitude": 4.0},
        )
        assert len(result) == 1
        assert "FeatureCollection" in result[0].text
        assert "Alaska" in result[0].text


@pytest.mark.anyio
async def test_usgs_eq_query_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("USGS unavailable")

        with pytest.raises(httpx.HTTPError):
            await handle_usgs_eq_query({"starttime": "2024-01-01"})


@pytest.mark.anyio
async def test_usgs_eq_count_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"count": 1234}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_count(
            {"starttime": "2024-01-01", "endtime": "2024-01-02"},
        )
        assert "1234" in result[0].text


@pytest.mark.anyio
async def test_usgs_eq_feed_significant_day_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "type": "FeatureCollection",
            "metadata": {"title": "Significant Earthquakes, Past Day"},
            "features": [],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_feed_significant_day()
        assert "Significant Earthquakes" in result[0].text


@pytest.mark.anyio
async def test_usgs_eq_feed_significant_week_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "type": "FeatureCollection",
            "metadata": {"title": "Significant Earthquakes, Past Week"},
            "features": [],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_feed_significant_week()
        assert "Past Week" in result[0].text


@pytest.mark.anyio
async def test_usgs_eq_feed_all_day_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "type": "FeatureCollection",
            "metadata": {"title": "All Earthquakes, Past Day"},
            "features": [{"id": "eq-1"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_feed_all_day()
        assert "eq-1" in result[0].text


@pytest.mark.anyio
async def test_usgs_eq_feed_all_week_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "type": "FeatureCollection",
            "metadata": {"title": "All Earthquakes, Past Week"},
            "features": [],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_feed_all_week()
        assert "All Earthquakes" in result[0].text


@pytest.mark.anyio
async def test_usgs_eq_feed_m45_week_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "type": "FeatureCollection",
            "metadata": {"title": "M4.5+ Earthquakes, Past Week"},
            "features": [],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_feed_m45_week()
        assert "M4.5+" in result[0].text


@pytest.mark.anyio
async def test_usgs_eq_application_version_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = "1.14.1"
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_application_version()
        assert "1.14.1" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for usgs-eq-query.
# ---------------------------------------------------------------------------


def test_adapter_passes_geojson_through_natively():
    """USGS returns native GeoJSON — option A: wrap the FeatureCollection
    under ``features`` so the bundle can consume it directly.
    """
    raw = {
        "type": "FeatureCollection",
        "metadata": {"count": 1},
        "features": [
            {
                "id": "us1",
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-122.0, 37.5, 10.0]},
                "properties": {"mag": 5.1, "place": "Off the coast"},
            },
        ],
    }
    payload = _usgs_geojson_to_shape_payload(raw)
    assert payload["features"] == raw
    assert payload["features"]["type"] == "FeatureCollection"
    assert payload["features"]["features"][0]["id"] == "us1"


def test_adapter_handles_non_geojson_response():
    """Error or unexpected response shapes degrade to an empty
    FeatureCollection.
    """
    payload = _usgs_geojson_to_shape_payload({"error": "rate limited"})
    assert payload == {"features": {"type": "FeatureCollection", "features": []}}
    payload = _usgs_geojson_to_shape_payload("plain text")
    assert payload == {"features": {"type": "FeatureCollection", "features": []}}


def test_adapter_handles_empty_geojson():
    raw = {"type": "FeatureCollection", "features": []}
    assert _usgs_geojson_to_shape_payload(raw) == {"features": raw}


def test_eq_query_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "usgs-eq-query")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_usgs_eq_query_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "type": "FeatureCollection",
            "metadata": {"count": 1},
            "features": [
                {
                    "id": "us1",
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [-122.0, 37.5, 10.0],
                    },
                    "properties": {"mag": 5.1, "place": "Off the coast"},
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_query(
            {"starttime": "2024-01-01", "minmagnitude": 4.0},
        )
        body = json.loads(result[0].text)
        assert body["features"]["type"] == "FeatureCollection"
        assert body["features"]["features"][0]["id"] == "us1"


@pytest.mark.anyio
async def test_usgs_eq_query_caps_geojson_features_to_valid_json():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "type": "FeatureCollection",
            "metadata": {"count": 2000},
            "features": [
                {
                    "id": f"us{i}",
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-122.0, 37.5, 10.0]},
                    "properties": {
                        "mag": 5.1,
                        "place": "Off the coast",
                        "blob": "z" * 400,
                    },
                }
                for i in range(2000)
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_usgs_eq_query({"starttime": "2024-01-01"})

        body = json.loads(result[0].text)
        assert body["features"]["type"] == "FeatureCollection"
        assert 0 < len(body["features"]["features"]) < 2000
