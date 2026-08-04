"""NOAA Aviation Weather Center (AWC) Provider

This module provides interfaces to the NOAA AWC API for METAR, TAF, and station data.

License: NOAA data is in the public domain.
See https://aviationweather.gov/data/api/ for details.

API Documentation: https://aviationweather.gov/data/api/
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.fields import NonEmptyStr
from meta_data_mcp.utils import http_get, serialize_for_llm

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-noaa-awc"
BASE_URL = "https://aviationweather.gov/api/data"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# AWC METAR
###################


class AWCMetarParams(BaseModel):
    """Parameters for fetching METAR data."""

    ids: NonEmptyStr = Field(..., description="Station IDs (e.g. 'KJFK,EGLL')")


def fetch_awc_metar(params: AWCMetarParams) -> Any:
    """Fetch METAR data from NOAA AWC."""
    query_params = {"ids": params.ids, "format": "json"}
    response = http_get(f"{BASE_URL}/metar", params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_awc_metar(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the awc-metar tool call."""
    try:
        params = AWCMetarParams(**(arguments or {}))
        data = fetch_awc_metar(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching AWC METAR: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="awc-metar",
        description="Fetch real-time METAR (Meteorological Aerodrome Report) data from NOAA AWC.",
        input_schema=AWCMetarParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["awc-metar"] = handle_awc_metar

###################
# AWC TAF
###################


class AWCTafParams(BaseModel):
    """Parameters for fetching TAF data."""

    ids: NonEmptyStr = Field(..., description="Station IDs (e.g. 'KJFK')")


def fetch_awc_taf(params: AWCTafParams) -> Any:
    """Fetch TAF data from NOAA AWC."""
    query_params = {"ids": params.ids, "format": "json"}
    response = http_get(f"{BASE_URL}/taf", params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_awc_taf(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the awc-taf tool call."""
    try:
        params = AWCTafParams(**(arguments or {}))
        data = fetch_awc_taf(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching AWC TAF: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="awc-taf",
        description="Fetch real-time TAF (Terminal Aerodrome Forecast) data from NOAA AWC.",
        input_schema=AWCTafParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["awc-taf"] = handle_awc_taf

###################
# AWC Station
###################


class AWCStationParams(BaseModel):
    """Parameters for fetching station data."""

    ids: NonEmptyStr = Field(..., description="Station IDs (e.g. 'KJFK')")


def fetch_awc_station(params: AWCStationParams) -> Any:
    """Fetch station metadata from NOAA AWC."""
    query_params = {"ids": params.ids, "format": "json"}
    response = http_get(
        f"{BASE_URL}/station",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_awc_station(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the awc-station tool call."""
    try:
        params = AWCStationParams(**(arguments or {}))
        data = fetch_awc_station(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching AWC station: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="awc-station",
        description="Fetch aviation weather station metadata from NOAA AWC.",
        input_schema=AWCStationParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["awc-station"] = handle_awc_station


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-noaa-awc",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
