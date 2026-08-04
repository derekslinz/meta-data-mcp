import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_nasa import (
    TOOLS,
    _mars_photos_to_shape_payload,
    handle_get_ace_data,
    handle_get_apod,
    handle_get_asteroids,
    handle_get_mars_photos,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_nasa_get_apod_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "title": "Starry Night",
            "url": "http://example.com/nasa.jpg",
            "explanation": "Testing context",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_apod()
        assert len(result) == 1
        assert "Starry Night" in result[0].text


@pytest.mark.anyio
async def test_nasa_get_apod_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_get_apod()


@pytest.mark.anyio
async def test_nasa_get_asteroids_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"element_count": 42}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_asteroids(
            {"start_date": "2024-01-01", "end_date": "2024-01-07"},
        )
        assert "element_count" in result[0].text
        assert "42" in result[0].text


@pytest.mark.anyio
async def test_nasa_get_mars_photos_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "photos": [{"img_src": "mars_pic.jpg"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_mars_photos(
            {"rover": "curiosity", "earth_date": "2024-01-01"},
        )
        assert "mars_pic.jpg" in result[0].text


@pytest.mark.anyio
async def test_nasa_get_ace_data_success():
    # Fixed ACE tool hitting SWPC API
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            ["time_tag", "density"],
            ["2024-04-01 12:00:00", "5.0"],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_ace_data(
            {"start_date": "2024-04-01", "end_date": "2024-04-01"},
        )
        assert "density" in result[0].text
        assert "5.0" in result[0].text


@pytest.mark.anyio
async def test_nasa_get_ace_data_no_results():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [["time_tag", "density"]]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_ace_data(
            {"start_date": "2099-01-01", "end_date": "2099-01-01"},
        )
        assert "No data found" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for nasa-get-mars-photos.
# ---------------------------------------------------------------------------


def test_mars_photos_adapter_flattens_photos_to_rows():
    raw = {
        "photos": [
            {
                "id": 102693,
                "sol": 1000,
                "earth_date": "2015-05-30",
                "camera": {"name": "FHAZ", "full_name": "Front Hazard Avoidance"},
                "rover": {"name": "Curiosity", "status": "active"},
                "img_src": "https://mars.example/photo.jpg",
            },
        ],
    }
    payload = _mars_photos_to_shape_payload(raw)
    row = payload["rows"][0]
    assert row["camera_name"] == "FHAZ"
    assert row["rover_name"] == "Curiosity"
    assert payload["default_facets"] == ["rover_name", "camera_name", "rover_status"]


def test_mars_photos_adapter_handles_empty_photos():
    assert _mars_photos_to_shape_payload({"photos": []})["rows"] == []
    assert _mars_photos_to_shape_payload({})["rows"] == []


def test_mars_photos_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "nasa-get-mars-photos")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_nasa_get_mars_photos_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "photos": [{"id": 1, "img_src": "mars_pic.jpg"}],
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_get_mars_photos(
            {"rover": "curiosity", "earth_date": "2024-01-01"},
        )
        body = json.loads(result[0].text)
        assert body["rows"][0]["img_src"] == "mars_pic.jpg"
        assert "schema" in body
