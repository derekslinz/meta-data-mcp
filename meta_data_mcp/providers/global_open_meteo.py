"""Global Weather Data Provider (Open-Meteo)

This module provides interfaces to access the Open-Meteo weather API.
Open-Meteo is an open-source weather API that aggregates data from
national meteorological services (ECMWF, NOAA, DWD, MeteoFrance, etc.).

Features:
- Global weather forecasts (hourly/daily)
- Historical weather data (80+ years archive)
- Air quality data (PM2.5, NO2, O3, Dust, etc.)

Usage:
    The module can be run directly to start a server handling API requests,
    or its components can be imported and used individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI
from meta_data_mcp.utils import http_get, to_json_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-open-meteo"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# Weather Forecast
###################


class WeatherForecastParams(BaseModel):
    """Parameters for getting a weather forecast."""

    latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Latitude of the location",
    )
    longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Longitude of the location",
    )
    hourly: str = Field(
        default="temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        description="Comma-separated hourly variables to return",
    )
    daily: str = Field(
        default="weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
        description="Comma-separated daily variables to return",
    )
    forecast_days: int = Field(
        default=7,
        ge=1,
        le=16,
        description="Number of forecast days (1-16)",
    )


def fetch_weather_forecast(params: WeatherForecastParams) -> dict:
    """Fetch forecast data from Open-Meteo."""
    query_params = {
        "latitude": params.latitude,
        "longitude": params.longitude,
        "hourly": params.hourly,
        "daily": params.daily,
        "forecast_days": params.forecast_days,
        "timezone": "auto",
    }
    response = http_get(
        FORECAST_URL,
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


def _open_meteo_to_shape_payload(data: dict) -> dict:
    """Adapt Open-Meteo's parallel-vector ``{hourly|daily: {time: [...],
    var: [...], ...}, hourly_units: {...}}`` response to the
    ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    Each non-time variable becomes its own series, indexed positionally
    against the ``time`` vector. We prefer the daily block when present
    (more compact); otherwise we fall back to hourly.
    """
    block = data.get("daily") or data.get("hourly") or {}
    units = data.get("daily_units") if data.get("daily") else data.get("hourly_units")
    if not isinstance(block, dict):
        return {"points": [], "axes": {"x": "Time", "y": "Value"}}
    times = block.get("time") or []
    if not isinstance(times, list):
        return {"points": [], "axes": {"x": "Time", "y": "Value"}}

    points: list[dict[str, Any]] = []
    unit_set: set[str] = set()
    for var_name, values in block.items():
        if var_name == "time":
            continue
        if not isinstance(values, list):
            continue
        if isinstance(units, dict):
            unit = units.get(var_name)
            if isinstance(unit, str) and unit:
                unit_set.add(unit)
        # strict=True: ``times`` is the canonical grid for the entire
        # hourly/daily block; every variable's value series must align.
        # Mismatched length means the upstream response is broken.
        for date, value in zip(times, values, strict=True):
            if not isinstance(date, str):
                continue
            if value is None or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)):
                continue
            points.append({"date": date, "value": value, "series": var_name})

    points.sort(key=lambda p: (p["date"], p["series"]))
    y_label = unit_set.pop() if len(unit_set) == 1 else "Value"
    return {
        "points": points,
        "axes": {"x": "Time", "y": y_label},
    }


async def handle_get_forecast(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the weather-get-forecast tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        params = WeatherForecastParams(**(arguments or {}))
        data = fetch_weather_forecast(params)
        payload = _open_meteo_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error fetching weather forecast: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="weather-get-forecast",
        description="Get current and upcoming weather forecast for a specific location.",
        input_schema=WeatherForecastParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["weather-get-forecast"] = handle_get_forecast

###################
# Historical Weather
###################


class HistoricalWeatherParams(BaseModel):
    """Parameters for getting historical weather data."""

    latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Latitude of the location",
    )
    longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Longitude of the location",
    )
    start_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Start date (YYYY-MM-DD)",
    )
    end_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="End date (YYYY-MM-DD)",
    )
    daily: str = Field(
        default="temperature_2m_max,temperature_2m_min,precipitation_sum",
        description="Comma-separated daily variables to return",
    )


def fetch_historical_weather(params: HistoricalWeatherParams) -> dict:
    """Fetch historical data from Open-Meteo Archive API."""
    query_params = {
        "latitude": params.latitude,
        "longitude": params.longitude,
        "start_date": params.start_date,
        "end_date": params.end_date,
        "daily": params.daily,
        "timezone": "auto",
    }
    response = http_get(
        ARCHIVE_URL,
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_historical_weather(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the weather-get-historical tool call."""
    try:
        params = HistoricalWeatherParams(**(arguments or {}))
        data = fetch_historical_weather(params)
        return [types.TextContent(type="text", text=to_json_text(data))]
    except Exception as e:
        log.error(f"Error fetching historical weather: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="weather-get-historical",
        description="Get historical weather records for a specific location and date range.",
        input_schema=HistoricalWeatherParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["weather-get-historical"] = handle_get_historical_weather

###################
# Air Quality
###################


class AirQualityParams(BaseModel):
    """Parameters for getting air quality data."""

    latitude: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Latitude of the location",
    )
    longitude: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Longitude of the location",
    )
    hourly: str = Field(
        default="pm2_5,pm10,nitrogen_dioxide,ozone,dust",
        description="Comma-separated hourly variables to return",
    )


def fetch_air_quality(params: AirQualityParams) -> dict:
    """Fetch air quality data from Open-Meteo."""
    query_params = {
        "latitude": params.latitude,
        "longitude": params.longitude,
        "hourly": params.hourly,
        "timezone": "auto",
    }
    response = http_get(
        AIR_QUALITY_URL,
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_air_quality(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the weather-get-air-quality tool call."""
    try:
        params = AirQualityParams(**(arguments or {}))
        data = fetch_air_quality(params)
        return [types.TextContent(type="text", text=to_json_text(data))]
    except Exception as e:
        log.error(f"Error fetching air quality: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="weather-get-air-quality",
        description="Get air quality data (PM2.5, ozone, dust, etc.) for a specific location.",
        input_schema=AirQualityParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["weather-get-air-quality"] = handle_get_air_quality


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-open-meteo",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
