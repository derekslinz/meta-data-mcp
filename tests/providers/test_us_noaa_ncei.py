import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_noaa_ncei import (
    TOOLS,
    _ncei_daily_summaries_to_shape_payload,
    handle_ncei_get_daily_summaries,
    handle_ncei_get_global_summary,
    handle_ncei_get_precipitation,
    handle_ncei_get_station_meta,
    handle_ncei_get_temperature,
    handle_ncei_list_datasets,
    handle_ncei_search_stations,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_ncei_daily_summaries_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"STATION": "USW00014739", "DATE": "2024-01-01", "TMAX": 12, "TMIN": 3},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ncei_get_daily_summaries(
            {
                "stations": "USW00014739",
                "startDate": "2024-01-01",
                "endDate": "2024-01-01",
                "dataTypes": "TMAX,TMIN",
            },
        )
        assert "USW00014739" in result[0].text
        assert "TMAX" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for noaa-ncei-get-daily-summaries.
# ---------------------------------------------------------------------------


def test_ncei_adapter_emits_one_point_per_numeric_field():
    raw = [
        {"STATION": "USW00014739", "DATE": "2024-01-01", "TMAX": "12", "TMIN": "3"},
        {"STATION": "USW00014739", "DATE": "2024-01-02", "TMAX": "10", "TMIN": "2"},
    ]
    payload = _ncei_daily_summaries_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Date", "y": "Value"}
    # 2 dates × 2 vars = 4 points
    assert len(payload["points"]) == 4
    series = {p["series"] for p in payload["points"]}
    assert series == {"USW00014739:TMAX", "USW00014739:TMIN"}


def test_ncei_adapter_handles_empty():
    payload = _ncei_daily_summaries_to_shape_payload([])
    assert payload["points"] == []


def test_ncei_adapter_handles_non_list():
    payload = _ncei_daily_summaries_to_shape_payload({})
    assert payload["points"] == []


def test_ncei_adapter_skips_non_numeric_fields():
    raw = [
        {
            "STATION": "X",
            "DATE": "2024-01-01",
            "TMAX": "bad",
            "TMIN": "5",
            "NAME": "Some Place",
        },
    ]
    payload = _ncei_daily_summaries_to_shape_payload(raw)
    assert len(payload["points"]) == 1
    assert payload["points"][0]["series"] == "X:TMIN"


def test_ncei_daily_summaries_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "noaa-ncei-get-daily-summaries")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_ncei_daily_summaries_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"STATION": "X", "DATE": "2024-01-01", "TMAX": "12"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ncei_get_daily_summaries(
            {
                "stations": "X",
                "startDate": "2024-01-01",
                "endDate": "2024-01-01",
            },
        )
        body = json.loads(result[0].text)
        assert body["points"][0] == {
            "date": "2024-01-01",
            "value": 12.0,
            "series": "X:TMAX",
        }


@pytest.mark.anyio
async def test_ncei_daily_summaries_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("NCEI down")

        with pytest.raises(httpx.HTTPError):
            await handle_ncei_get_daily_summaries(
                {
                    "stations": "USW00014739",
                    "startDate": "2024-01-01",
                    "endDate": "2024-01-01",
                },
            )


@pytest.mark.anyio
async def test_ncei_global_summary_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"STATION": "72509014739", "DATE": "2024-01-01", "TEMP": "30.4"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ncei_get_global_summary(
            {
                "stations": "72509014739",
                "startDate": "2024-01-01",
                "endDate": "2024-01-01",
            },
        )
        assert "72509014739" in result[0].text


@pytest.mark.anyio
async def test_ncei_search_stations_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "results": [{"id": "USW00014739", "name": "Boston Logan"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ncei_search_stations(
            {"boundingBox": "42.5,-71.5,42.0,-70.5", "limit": 5},
        )
        assert "Boston Logan" in result[0].text


@pytest.mark.anyio
async def test_ncei_list_datasets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 2,
            "results": [
                {"id": "daily-summaries"},
                {"id": "global-summary-of-the-day"},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ncei_list_datasets({"limit": 10})
        assert "daily-summaries" in result[0].text


@pytest.mark.anyio
async def test_ncei_get_station_meta_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"id": "USW00014739", "name": "Boston Logan Intl"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ncei_get_station_meta({"stations": "USW00014739"})
        assert "Boston Logan Intl" in result[0].text


@pytest.mark.anyio
async def test_ncei_get_precipitation_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"STATION": "USW00014739", "DATE": "2024-01-01", "PRCP": 0.42},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ncei_get_precipitation(
            {
                "stations": "USW00014739",
                "startDate": "2024-01-01",
                "endDate": "2024-01-01",
            },
        )
        assert "PRCP" in result[0].text
        assert "0.42" in result[0].text


@pytest.mark.anyio
async def test_ncei_get_temperature_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"STATION": "USW00014739", "DATE": "2024-01-01", "TMAX": 12, "TMIN": 3},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ncei_get_temperature(
            {
                "stations": "USW00014739",
                "startDate": "2024-01-01",
                "endDate": "2024-01-01",
            },
        )
        assert "TMAX" in result[0].text
        assert "TMIN" in result[0].text
