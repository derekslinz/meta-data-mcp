"""NOAA CO-OPS Tides and Currents Provider

This module exposes the NOAA Center for Operational Oceanographic Products
and Services (CO-OPS) public datagetter API along with the station metadata
web service. It covers real-time and historical observations from tide and
current stations across US coastal waters and the Great Lakes.

License note:
    CO-OPS data products are produced by the US Government and are released
    in the public domain. NOAA requests attribution when products are
    redistributed (see https://tidesandcurrents.noaa.gov).

Features:
- Water levels (observed)
- Tide predictions
- Meteorological observations (air temperature, water temperature, wind)
- Currents observations
- Hourly height records
- Station metadata directory

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI
from meta_data_mcp.utils import http_get, serialize_for_llm

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-noaa-tides"
BASE_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATIONS_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Water Level
###################


class NOAATidesWaterLevelParams(BaseModel):
    """Parameters for retrieving observed water levels."""

    station: str = Field(..., description="CO-OPS station ID (e.g. 9447130).")
    date: str = Field(
        default="today",
        description="Date selector. Accepts 'today', 'latest', 'recent', or YYYYMMDD.",
    )
    datum: str = Field(default="MLLW", description="Vertical datum (default MLLW).")
    units: str = Field(default="metric", description="'metric' or 'english'.")
    time_zone: str = Field(
        default="lst_ldt",
        description="Time zone code (default lst_ldt).",
    )


def _build_common_params(
    station: str,
    datum: str,
    units: str,
    time_zone: str,
    product: str,
) -> dict[str, Any]:
    return {
        "station": station,
        "datum": datum,
        "units": units,
        "time_zone": time_zone,
        "product": product,
        "format": "json",
    }


def fetch_noaa_tides_water_level(params: NOAATidesWaterLevelParams) -> Any:
    """Call the datagetter for observed water levels."""
    query_params = _build_common_params(
        params.station,
        params.datum,
        params.units,
        params.time_zone,
        "water_level",
    )
    query_params["date"] = params.date
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


def _noaa_tides_water_level_to_shape_payload(data: dict) -> dict:
    """Adapt CO-OPS datagetter's ``{metadata: {...}, data: [{t, v, ...}]}``
    response to the ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    The water level value ``v`` is a string; coerced defensively. Station
    name from metadata flows into the series label when present.
    """
    metadata = data.get("metadata") or {}
    station_name = ""
    if isinstance(metadata, dict):
        station_name = metadata.get("name") or metadata.get("id") or ""
    rows = data.get("data") or []
    if not isinstance(rows, list):
        return {"points": [], "axes": {"x": "Time", "y": "Water level"}}

    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = row.get("t")
        raw_value = row.get("v")
        if not isinstance(date, str) or raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if isinstance(raw_value, bool):
            continue
        point: dict[str, Any] = {"date": date, "value": value}
        if station_name:
            point["series"] = str(station_name)
        points.append(point)

    points.sort(key=lambda p: (p["date"], p.get("series", "")))
    return {
        "points": points,
        "axes": {"x": "Time", "y": "Water level"},
    }


async def handle_noaa_tides_water_level(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-tides-water-level tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        if not arguments or "station" not in arguments:
            raise ValueError("station is required")
        params = NOAATidesWaterLevelParams(**arguments)
        data = fetch_noaa_tides_water_level(params)
        payload = _noaa_tides_water_level_to_shape_payload(data)
        return [types.TextContent(type="text", text=serialize_for_llm(payload))]
    except Exception as e:
        log.error(f"Error fetching NOAA tides water level: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-tides-water-level",
        description="Get observed water levels from a NOAA CO-OPS station.",
        input_schema=NOAATidesWaterLevelParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["noaa-tides-water-level"] = handle_noaa_tides_water_level


###################
# Predictions
###################


class NOAATidesPredictionsParams(BaseModel):
    """Parameters for tide predictions."""

    station: str = Field(..., description="CO-OPS station ID.")
    begin_date: str = Field(..., description="Begin date (YYYYMMDD or YYYYMMDD HH:MM).")
    end_date: str = Field(..., description="End date (YYYYMMDD or YYYYMMDD HH:MM).")
    datum: str = Field(default="MLLW", description="Vertical datum.")
    interval: str | None = Field(
        None,
        description="'hilo' for high/low only or 'h' / minute interval.",
    )
    units: str = Field(default="metric", description="'metric' or 'english'.")
    time_zone: str = Field(default="lst_ldt", description="Time zone code.")


def fetch_noaa_tides_predictions(params: NOAATidesPredictionsParams) -> Any:
    """Call the datagetter for tide predictions."""
    query_params = _build_common_params(
        params.station,
        params.datum,
        params.units,
        params.time_zone,
        "predictions",
    )
    query_params["begin_date"] = params.begin_date
    query_params["end_date"] = params.end_date
    if params.interval:
        query_params["interval"] = params.interval
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_noaa_tides_predictions(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-tides-predictions tool call."""
    try:
        if not arguments or "station" not in arguments:
            raise ValueError("station is required")
        params = NOAATidesPredictionsParams(**arguments)
        data = fetch_noaa_tides_predictions(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NOAA tides predictions: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-tides-predictions",
        description="Get tide predictions for a NOAA CO-OPS station between two dates.",
        input_schema=NOAATidesPredictionsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-tides-predictions"] = handle_noaa_tides_predictions


###################
# Meteorological Observations
###################


class NOAATidesMetParams(BaseModel):
    """Common parameters for meteorological products."""

    station: str = Field(..., description="CO-OPS station ID.")
    date: str = Field(
        default="today",
        description="Date selector. Accepts 'today', 'latest', 'recent', or YYYYMMDD.",
    )
    datum: str = Field(default="MLLW", description="Vertical datum.")
    units: str = Field(default="metric", description="'metric' or 'english'.")
    time_zone: str = Field(default="lst_ldt", description="Time zone code.")


def _fetch_met_product(params: NOAATidesMetParams, product: str) -> Any:
    """Generic helper for met/water-temp/wind products."""
    query_params = _build_common_params(
        params.station,
        params.datum,
        params.units,
        params.time_zone,
        product,
    )
    query_params["date"] = params.date
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


def fetch_noaa_tides_air_temperature(params: NOAATidesMetParams) -> Any:
    return _fetch_met_product(params, "air_temperature")


def fetch_noaa_tides_water_temperature(params: NOAATidesMetParams) -> Any:
    return _fetch_met_product(params, "water_temperature")


def fetch_noaa_tides_wind(params: NOAATidesMetParams) -> Any:
    return _fetch_met_product(params, "wind")


def fetch_noaa_tides_currents(params: NOAATidesMetParams) -> Any:
    return _fetch_met_product(params, "currents")


def fetch_noaa_tides_hourly_height(params: NOAATidesMetParams) -> Any:
    return _fetch_met_product(params, "hourly_height")


async def handle_noaa_tides_air_temperature(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-tides-air-temperature tool call."""
    try:
        if not arguments or "station" not in arguments:
            raise ValueError("station is required")
        params = NOAATidesMetParams(**arguments)
        data = fetch_noaa_tides_air_temperature(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NOAA air temperature: {e}")
        raise


async def handle_noaa_tides_water_temperature(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-tides-water-temperature tool call."""
    try:
        if not arguments or "station" not in arguments:
            raise ValueError("station is required")
        params = NOAATidesMetParams(**arguments)
        data = fetch_noaa_tides_water_temperature(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NOAA water temperature: {e}")
        raise


async def handle_noaa_tides_wind(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-tides-wind tool call."""
    try:
        if not arguments or "station" not in arguments:
            raise ValueError("station is required")
        params = NOAATidesMetParams(**arguments)
        data = fetch_noaa_tides_wind(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NOAA wind: {e}")
        raise


async def handle_noaa_tides_currents(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-tides-currents tool call."""
    try:
        if not arguments or "station" not in arguments:
            raise ValueError("station is required")
        params = NOAATidesMetParams(**arguments)
        data = fetch_noaa_tides_currents(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NOAA currents: {e}")
        raise


async def handle_noaa_tides_hourly_height(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-tides-hourly-height tool call."""
    try:
        if not arguments or "station" not in arguments:
            raise ValueError("station is required")
        params = NOAATidesMetParams(**arguments)
        data = fetch_noaa_tides_hourly_height(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NOAA hourly height: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-tides-air-temperature",
        description="Get air temperature observations from a NOAA CO-OPS station.",
        input_schema=NOAATidesMetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-tides-air-temperature"] = handle_noaa_tides_air_temperature

TOOLS.append(
    types.Tool(
        name="noaa-tides-water-temperature",
        description="Get water temperature observations from a NOAA CO-OPS station.",
        input_schema=NOAATidesMetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-tides-water-temperature"] = handle_noaa_tides_water_temperature

TOOLS.append(
    types.Tool(
        name="noaa-tides-wind",
        description="Get wind observations from a NOAA CO-OPS station.",
        input_schema=NOAATidesMetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-tides-wind"] = handle_noaa_tides_wind

TOOLS.append(
    types.Tool(
        name="noaa-tides-currents",
        description="Get current speed/direction observations from a NOAA CO-OPS station.",
        input_schema=NOAATidesMetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-tides-currents"] = handle_noaa_tides_currents

TOOLS.append(
    types.Tool(
        name="noaa-tides-hourly-height",
        description="Get hourly water height records from a NOAA CO-OPS station.",
        input_schema=NOAATidesMetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-tides-hourly-height"] = handle_noaa_tides_hourly_height


###################
# Station Metadata
###################


class NOAATidesStationMetadataParams(BaseModel):
    """Parameters for listing CO-OPS stations by type."""

    type: str = Field(
        default="tidepredictions",
        description="Station type filter, e.g. tidepredictions, waterlevels, currents.",
    )


def fetch_noaa_tides_station_metadata(
    params: NOAATidesStationMetadataParams,
) -> Any:
    """Call the CO-OPS Metadata API stations endpoint."""
    query_params: dict[str, Any] = {"type": params.type}
    response = http_get(STATIONS_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_noaa_tides_station_metadata(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-tides-station-metadata tool call."""
    try:
        params = NOAATidesStationMetadataParams(**(arguments or {}))
        data = fetch_noaa_tides_station_metadata(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NOAA tides station metadata: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-tides-station-metadata",
        description="List NOAA CO-OPS stations by type (default: tide-prediction stations).",
        input_schema=NOAATidesStationMetadataParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-tides-station-metadata"] = handle_noaa_tides_station_metadata


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-noaa-tides",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
