import json
from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.global_open_meteo import (
    TOOLS,
    AirQualityParams,
    HistoricalWeatherParams,
    WeatherForecastParams,
    _open_meteo_to_shape_payload,
    fetch_air_quality,
    fetch_historical_weather,
    fetch_weather_forecast,
    handle_get_air_quality,
    handle_get_forecast,
    handle_get_historical_weather,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_forecast_response():
    return {
        "latitude": 52.52,
        "longitude": 13.41,
        "hourly": {"time": ["2023-01-01T00:00"], "temperature_2m": [10.5]},
        "daily": {"time": ["2023-01-01"], "temperature_2m_max": [15.0]},
    }


@pytest.fixture
def mock_historical_response():
    return {
        "latitude": 52.52,
        "longitude": 13.41,
        "daily": {"time": ["2020-01-01"], "temperature_2m_max": [5.0]},
    }


@pytest.fixture
def mock_air_quality_response():
    return {
        "latitude": 52.52,
        "longitude": 13.41,
        "hourly": {"time": ["2023-01-01T00:00"], "pm2_5": [5.0]},
    }


def test_fetch_weather_forecast(mock_forecast_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_forecast_response
        mock_get.return_value.raise_for_status = Mock()

        params = WeatherForecastParams(latitude=52.52, longitude=13.41)
        response = fetch_weather_forecast(params)
        assert response["latitude"] == 52.52
        assert response["hourly"]["temperature_2m"][0] == 10.5


@pytest.mark.anyio
async def test_handle_get_forecast(mock_forecast_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_forecast_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_forecast({"latitude": 52.52, "longitude": 13.41})
        assert len(result) == 1
        assert "15.0" in result[0].text
        assert "points" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for weather-get-forecast.
# ---------------------------------------------------------------------------


def test_open_meteo_adapter_prefers_daily():
    raw = {
        "daily": {
            "time": ["2023-01-01", "2023-01-02"],
            "temperature_2m_max": [15.0, 16.0],
        },
        "daily_units": {"temperature_2m_max": "°C"},
        "hourly": {"time": ["2023-01-01T00:00"], "temperature_2m": [10.0]},
    }
    payload = _open_meteo_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Time", "y": "°C"}
    assert len(payload["points"]) == 2
    assert payload["points"][0]["series"] == "temperature_2m_max"


def test_open_meteo_adapter_falls_back_to_hourly():
    raw = {
        "hourly": {
            "time": ["2023-01-01T00:00", "2023-01-01T01:00"],
            "temperature_2m": [10.0, 11.0],
        },
        "hourly_units": {"temperature_2m": "°C"},
    }
    payload = _open_meteo_to_shape_payload(raw)
    assert len(payload["points"]) == 2
    assert all(p["series"] == "temperature_2m" for p in payload["points"])


def test_open_meteo_adapter_multi_variable_no_uniform_unit():
    raw = {
        "daily": {
            "time": ["2023-01-01"],
            "temperature_2m_max": [15.0],
            "precipitation_sum": [2.0],
        },
        "daily_units": {"temperature_2m_max": "°C", "precipitation_sum": "mm"},
    }
    payload = _open_meteo_to_shape_payload(raw)
    assert payload["axes"]["y"] == "Value"
    assert len(payload["points"]) == 2


def test_open_meteo_adapter_empty():
    payload = _open_meteo_to_shape_payload({})
    assert payload["points"] == []


def test_open_meteo_adapter_skips_null_values():
    raw = {
        "daily": {
            "time": ["2023-01-01", "2023-01-02"],
            "temperature_2m_max": [None, 10.0],
        },
    }
    payload = _open_meteo_to_shape_payload(raw)
    assert len(payload["points"]) == 1
    assert payload["points"][0]["value"] == 10.0


def test_weather_forecast_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "weather-get-forecast")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_weather_forecast_returns_shape_payload(mock_forecast_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_forecast_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_forecast({"latitude": 52.52, "longitude": 13.41})
        body = json.loads(result[0].text)
        assert "points" in body and "axes" in body


def test_fetch_historical_weather(mock_historical_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_historical_response
        mock_get.return_value.raise_for_status = Mock()

        params = HistoricalWeatherParams(
            latitude=52.52,
            longitude=13.41,
            start_date="2020-01-01",
            end_date="2020-01-01",
        )
        response = fetch_historical_weather(params)
        assert response["daily"]["temperature_2m_max"][0] == 5.0


@pytest.mark.anyio
async def test_handle_get_historical_weather(mock_historical_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_historical_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_historical_weather(
            {
                "latitude": 52.52,
                "longitude": 13.41,
                "start_date": "2020-01-01",
                "end_date": "2020-01-01",
            },
        )
        assert len(result) == 1
        assert "5.0" in result[0].text


def test_fetch_air_quality(mock_air_quality_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_air_quality_response
        mock_get.return_value.raise_for_status = Mock()

        params = AirQualityParams(latitude=52.52, longitude=13.41)
        response = fetch_air_quality(params)
        assert response["hourly"]["pm2_5"][0] == 5.0


@pytest.mark.anyio
async def test_handle_get_air_quality(mock_air_quality_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_air_quality_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_air_quality({"latitude": 52.52, "longitude": 13.41})
        assert len(result) == 1
        assert "5.0" in result[0].text
