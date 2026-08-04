"""Deutsche Bahn (DB) Open Data Provider

This module provides interfaces to access Deutsche Bahn's public APIs.
It focuses on station data (StaDa) and timetable information.

Features:
- Station discovery (search by name/ID)
- Timetable: Arrivals and Departures for a specific station

Usage:
    The module can be run directly to start a server handling API requests.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI
from meta_data_mcp.utils import http_get, to_json_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
# We use the public-transport-rest standard for DB data (key-less).
PROVIDER_ID = "de-db"
BASE_URL = "https://v6.db.transport.rest"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# Station Discovery
###################


class DBStationParams(BaseModel):
    """Parameters for searching Deutsche Bahn stations."""

    search: str = Field(
        ...,
        description="Search term for the station name (e.g., 'Berlin')",
    )


def fetch_db_stations(params: DBStationParams) -> list:
    """Fetch live station data from the public DB transport API wrapper."""
    url = f"{BASE_URL}/locations"
    query_params = {"query": params.search, "results": 5}
    response = http_get(url, params=query_params, timeout=10.0, provider=PROVIDER_ID)
    return response.json()


def _db_stations_to_shape_payload(data: Any) -> dict:
    """Adapt the public-transport-rest DB station search response to the
    geofeatures payload.

    Station records carry coordinates either at the top level
    (``{latitude, longitude, ...}``) or under a nested ``location``
    block (``{location: {latitude, longitude}}``). Both shapes are
    handled. Records lacking usable coordinates are dropped.
    """
    features: list[dict] = []
    if not isinstance(data, list):
        return {"features": features}
    for station in data:
        if not isinstance(station, dict):
            continue
        lat = station.get("latitude")
        lon = station.get("longitude")
        if (lat is None or lon is None) and isinstance(station.get("location"), dict):
            lat = station["location"].get("latitude")
            lon = station["location"].get("longitude")
        try:
            lat_f = float(lat) if lat is not None else None
            lon_f = float(lon) if lon is not None else None
        except (TypeError, ValueError):
            continue
        if lat_f is None or lon_f is None:
            continue
        if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0):
            continue
        attrs = {
            k: v
            for k, v in station.items()
            if k not in ("latitude", "longitude", "location")
        }
        features.append({"lat": lat_f, "lon": lon_f, "attrs": attrs})
    return {"features": features}


async def handle_list_stations(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the db-list-stations tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format so the MCP Apps host can render the result inline
    via the bound shape primitive.
    """
    try:
        if not arguments or "search" not in arguments:
            raise ValueError("search term is required")
        params = DBStationParams(**arguments)
        data = fetch_db_stations(params)
        payload = _db_stations_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error listing DB stations: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="db-list-stations",
        description="Search for stations in the Deutsche Bahn rail network (live data).",
        input_schema=DBStationParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["db-list-stations"] = handle_list_stations

###################
# Timetables
###################


class DBTimetableParams(BaseModel):
    """Parameters for getting station timetables."""

    station_id: str = Field(
        ...,
        description="The EVA ID of the station (e.g., '8011160' for Berlin Hbf)",
    )
    mode: str = Field(
        "departures",
        description="Whether to fetch 'departures' or 'arrivals'",
    )
    duration: int = Field(15, description="Duration in minutes to fetch (default 15)")


def fetch_db_timetable(params: DBTimetableParams) -> list:
    """Fetch live departure/arrival data from the public DB transport API wrapper."""
    endpoint = f"{BASE_URL}/stops/{params.station_id}/{params.mode}"
    query_params = {"duration": params.duration}
    response = http_get(
        endpoint,
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    # If the response is a dict containing the list, extract it. The v6 API usually returns a list or a list inside an object.
    data = response.json()
    if isinstance(data, dict):
        # Handle cases where it might be wrapped (e.g. {"departures": [...]})
        return data.get(params.mode, [])
    return data


async def handle_get_timetable(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the db-get-timetable tool call."""
    try:
        if not arguments or "station_id" not in arguments:
            raise ValueError("station_id is required")
        params = DBTimetableParams(**arguments)
        data = fetch_db_timetable(params)

        # Summarize to keep it concise for LLM
        summary = []
        for entry in data[:10]:  # Limit to 10 entries
            trip = entry.get("tripId", "N/A")
            direction = entry.get("direction", "N/A")
            planned_time = entry.get("plannedWhen")
            actual_time = entry.get("when")
            line = entry.get("line", {}).get("name", "N/A")
            summary.append(
                {
                    "line": line,
                    "direction": direction,
                    "planned": planned_time,
                    "actual": actual_time,
                    "tripId": trip,
                },
            )

        return [types.TextContent(type="text", text=to_json_text(summary))]
    except Exception as e:
        log.error(f"Error fetching DB timetable: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="db-get-timetable",
        description="Get live departures or arrivals for a specific station EVA ID.",
        input_schema=DBTimetableParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["db-get-timetable"] = handle_get_timetable


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "de-db",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
