"""NOAA National Centers for Environmental Information (NCEI) Provider

This module exposes the keyless NCEI Access Data Service and Search Service.
The endpoints cover daily summaries, global summary of the day, and metadata
discovery for tens of thousands of weather stations worldwide.

License note:
    NCEI products are produced by the US Government and are generally in the
    public domain. Some integrated datasets may carry contributor-specific
    use terms; consult the dataset documentation linked from each record
    before redistribution.

Features:
- Daily summaries (GHCN-Daily) by station and date range
- Global Summary of the Day (GSOD)
- Station search by bounding box
- Dataset catalog listing
- Station metadata lookup
- Convenience wrappers for precipitation and temperature

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
from meta_data_mcp.utils import http_get, serialize_for_llm, to_json_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-noaa-ncei"
BASE_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
SEARCH_DATA_URL = "https://www.ncei.noaa.gov/access/services/search/v1/data"
SEARCH_DATASETS_URL = "https://www.ncei.noaa.gov/access/services/search/v1/datasets"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Daily Summaries
###################


class NCEIDailySummariesParams(BaseModel):
    """Parameters for fetching GHCN daily summaries."""

    stations: str = Field(
        ...,
        description="Comma-separated station IDs (e.g. USW00014739).",
    )
    startDate: str = Field(..., description="Start date (YYYY-MM-DD).")
    endDate: str = Field(..., description="End date (YYYY-MM-DD).")
    dataTypes: str | None = Field(
        None,
        description="Comma-separated data type codes (e.g. TMAX,TMIN,PRCP). Omit for all.",
    )


def fetch_ncei_daily_summaries(params: NCEIDailySummariesParams) -> Any:
    """Call the NCEI access data service for daily summaries."""
    query_params: dict[str, Any] = {
        "dataset": "daily-summaries",
        "stations": params.stations,
        "startDate": params.startDate,
        "endDate": params.endDate,
        "format": "json",
    }
    if params.dataTypes:
        query_params["dataTypes"] = params.dataTypes
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


_NCEI_NON_NUMERIC_KEYS = {"STATION", "DATE", "NAME"}


def _ncei_daily_summaries_to_shape_payload(data: Any) -> dict:
    """Adapt the NCEI Access Data Service's row-list response (each row is
    ``{STATION, DATE, TMAX, TMIN, PRCP, ...}``) to the
    ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    All numeric columns become separate series (one line per data type per
    station). NCEI returns numbers as strings — we coerce defensively.
    """
    if not isinstance(data, list):
        return {"points": [], "axes": {"x": "Date", "y": "Value"}}

    points: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        date = row.get("DATE")
        if not isinstance(date, str):
            continue
        station = row.get("STATION") or ""
        for key, raw_value in row.items():
            if key in _NCEI_NON_NUMERIC_KEYS:
                continue
            if raw_value is None or isinstance(raw_value, bool):
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            series_label = f"{station}:{key}" if station else key
            points.append({"date": date, "value": value, "series": series_label})

    points.sort(key=lambda p: (p["date"], p["series"]))
    return {
        "points": points,
        "axes": {"x": "Date", "y": "Value"},
    }


async def handle_ncei_get_daily_summaries(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-ncei-get-daily-summaries tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        if not arguments or "stations" not in arguments:
            raise ValueError("stations is required")
        params = NCEIDailySummariesParams(**arguments)
        data = fetch_ncei_daily_summaries(params)
        payload = _ncei_daily_summaries_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error fetching NCEI daily summaries: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-ncei-get-daily-summaries",
        description="Fetch GHCN daily summaries (TMAX/TMIN/PRCP/etc.) from NCEI for one or more stations.",
        input_schema=NCEIDailySummariesParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["noaa-ncei-get-daily-summaries"] = handle_ncei_get_daily_summaries


###################
# Global Summary of the Day
###################


class NCEIGlobalSummaryParams(BaseModel):
    """Parameters for fetching Global Summary of the Day records."""

    stations: str = Field(..., description="Comma-separated station IDs.")
    startDate: str = Field(..., description="Start date (YYYY-MM-DD).")
    endDate: str = Field(..., description="End date (YYYY-MM-DD).")


def fetch_ncei_global_summary(params: NCEIGlobalSummaryParams) -> Any:
    """Call the NCEI access data service for global-summary-of-the-day."""
    query_params: dict[str, Any] = {
        "dataset": "global-summary-of-the-day",
        "stations": params.stations,
        "startDate": params.startDate,
        "endDate": params.endDate,
        "format": "json",
    }
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_ncei_get_global_summary(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-ncei-get-global-summary tool call."""
    try:
        if not arguments or "stations" not in arguments:
            raise ValueError("stations is required")
        params = NCEIGlobalSummaryParams(**arguments)
        data = fetch_ncei_global_summary(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NCEI global summary: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-ncei-get-global-summary",
        description="Fetch Global Summary of the Day (GSOD) records from NCEI for one or more stations.",
        input_schema=NCEIGlobalSummaryParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-ncei-get-global-summary"] = handle_ncei_get_global_summary


###################
# Station Search
###################


class NCEISearchStationsParams(BaseModel):
    """Parameters for the NCEI search-service station search."""

    boundingBox: str | None = Field(
        None,
        description="Bounding box as 'north,west,south,east' decimal degrees.",
    )
    startDate: str | None = Field(
        None,
        description="Restrict to stations active from this date (YYYY-MM-DD).",
    )
    endDate: str | None = Field(
        None,
        description="Restrict to stations active through this date (YYYY-MM-DD).",
    )
    limit: int = Field(
        default=25,
        ge=1,
        le=1000,
        description="Maximum number of stations to return.",
    )


def fetch_ncei_search_stations(params: NCEISearchStationsParams) -> Any:
    """Call the NCEI search service for stations under the daily-summaries dataset."""
    query_params: dict[str, Any] = {
        "dataset": "daily-summaries",
        "limit": params.limit,
    }
    if params.boundingBox:
        query_params["boundingBox"] = params.boundingBox
    if params.startDate:
        query_params["startDate"] = params.startDate
    if params.endDate:
        query_params["endDate"] = params.endDate
    response = http_get(SEARCH_DATA_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_ncei_search_stations(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-ncei-search-stations tool call."""
    try:
        params = NCEISearchStationsParams(**(arguments or {}))
        data = fetch_ncei_search_stations(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching NCEI stations: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-ncei-search-stations",
        description="Search NCEI daily-summaries stations by bounding box and active date range.",
        input_schema=NCEISearchStationsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-ncei-search-stations"] = handle_ncei_search_stations


###################
# List Datasets
###################


class NCEIListDatasetsParams(BaseModel):
    """Parameters for listing NCEI datasets."""

    limit: int = Field(default=25, ge=1, le=1000, description="Page size.")
    offset: int = Field(default=0, ge=0, description="Offset into the result set.")


def fetch_ncei_list_datasets(params: NCEIListDatasetsParams) -> Any:
    """Call the NCEI search service to list dataset records."""
    query_params: dict[str, Any] = {"limit": params.limit, "offset": params.offset}
    response = http_get(SEARCH_DATASETS_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_ncei_list_datasets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-ncei-list-datasets tool call."""
    try:
        params = NCEIListDatasetsParams(**(arguments or {}))
        data = fetch_ncei_list_datasets(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing NCEI datasets: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-ncei-list-datasets",
        description="List NCEI datasets discoverable via the search service.",
        input_schema=NCEIListDatasetsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-ncei-list-datasets"] = handle_ncei_list_datasets


###################
# Station Metadata
###################


class NCEIStationMetaParams(BaseModel):
    """Parameters for retrieving a single station's metadata."""

    stations: str = Field(..., description="Comma-separated station IDs.")


def fetch_ncei_station_meta(params: NCEIStationMetaParams) -> Any:
    """Call the search service to retrieve station metadata (limit=1)."""
    query_params: dict[str, Any] = {
        "dataset": "daily-summaries",
        "stations": params.stations,
        "format": "json",
        "limit": 1,
    }
    response = http_get(SEARCH_DATA_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_ncei_get_station_meta(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-ncei-get-station-meta tool call."""
    try:
        if not arguments or "stations" not in arguments:
            raise ValueError("stations is required")
        params = NCEIStationMetaParams(**arguments)
        data = fetch_ncei_station_meta(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NCEI station metadata: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-ncei-get-station-meta",
        description="Retrieve metadata for a single NCEI daily-summaries station.",
        input_schema=NCEIStationMetaParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-ncei-get-station-meta"] = handle_ncei_get_station_meta


###################
# Precipitation Convenience Wrapper
###################


class NCEIPrecipitationParams(BaseModel):
    """Parameters for the precipitation convenience wrapper."""

    stations: str = Field(..., description="Comma-separated station IDs.")
    startDate: str = Field(..., description="Start date (YYYY-MM-DD).")
    endDate: str = Field(..., description="End date (YYYY-MM-DD).")


def fetch_ncei_precipitation(params: NCEIPrecipitationParams) -> Any:
    """Daily summaries restricted to PRCP."""
    query_params: dict[str, Any] = {
        "dataset": "daily-summaries",
        "dataTypes": "PRCP",
        "stations": params.stations,
        "startDate": params.startDate,
        "endDate": params.endDate,
        "format": "json",
    }
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_ncei_get_precipitation(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-ncei-get-precipitation tool call."""
    try:
        if not arguments or "stations" not in arguments:
            raise ValueError("stations is required")
        params = NCEIPrecipitationParams(**arguments)
        data = fetch_ncei_precipitation(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NCEI precipitation: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-ncei-get-precipitation",
        description="Get daily precipitation (PRCP) totals from NCEI GHCN-Daily for one or more stations.",
        input_schema=NCEIPrecipitationParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-ncei-get-precipitation"] = handle_ncei_get_precipitation


###################
# Temperature Convenience Wrapper
###################


class NCEITemperatureParams(BaseModel):
    """Parameters for the temperature convenience wrapper."""

    stations: str = Field(..., description="Comma-separated station IDs.")
    startDate: str = Field(..., description="Start date (YYYY-MM-DD).")
    endDate: str = Field(..., description="End date (YYYY-MM-DD).")


def fetch_ncei_temperature(params: NCEITemperatureParams) -> Any:
    """Daily summaries restricted to TMAX,TMIN."""
    query_params: dict[str, Any] = {
        "dataset": "daily-summaries",
        "dataTypes": "TMAX,TMIN",
        "stations": params.stations,
        "startDate": params.startDate,
        "endDate": params.endDate,
        "format": "json",
    }
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_ncei_get_temperature(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the noaa-ncei-get-temperature tool call."""
    try:
        if not arguments or "stations" not in arguments:
            raise ValueError("stations is required")
        params = NCEITemperatureParams(**arguments)
        data = fetch_ncei_temperature(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching NCEI temperature: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="noaa-ncei-get-temperature",
        description="Get daily maximum/minimum temperature (TMAX/TMIN) from NCEI GHCN-Daily.",
        input_schema=NCEITemperatureParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["noaa-ncei-get-temperature"] = handle_ncei_get_temperature


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-noaa-ncei",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
