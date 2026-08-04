"""NASA Open Data Provider

This module provides interfaces to access NASA's public APIs.
It uses the public DEMO_KEY by default (30 req/hr limit). Set the
NASA_API_KEY environment variable to use a personal API key with
higher rate limits.

Features:
- APOD: Astronomy Picture of the Day
- NeoWs: Asteroid tracking and Near Earth Objects
- Mars Rover Photos: Curiosity, Opportunity, Spirit
- Space Weather: ACE Solar Wind data (via CDAWeb)

Usage:
    The module can be run directly to start a server handling API requests.
"""

import logging
import os
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, to_json_text, to_records_text

# Initialize logging
log = logging.getLogger(__name__)

PROVIDER_ID = "us-nasa"

# Constants
BASE_URL = "https://api.nasa.gov"
DEMO_KEY = "DEMO_KEY"
# Use NASA_API_KEY env var if set, otherwise fall back to the public DEMO_KEY
_API_KEY = os.getenv("NASA_API_KEY", DEMO_KEY)

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# APOD
###################


class APODParams(BaseModel):
    """Parameters for getting the Astronomy Picture of the Day."""

    date: str | None = Field(
        None,
        description="The date of the APOD image (YYYY-MM-DD)",
    )


def fetch_apod(params: APODParams) -> dict:
    """Fetch APOD data from NASA API."""
    query_params = {"api_key": _API_KEY}
    if params.date:
        query_params["date"] = params.date

    response = http_get(
        f"{BASE_URL}/planetary/apod",
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_apod(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the nasa-get-apod tool call."""
    try:
        params = APODParams(**(arguments or {}))
        data = fetch_apod(params)
        return [types.TextContent(type="text", text=to_json_text(data))]
    except Exception as e:
        log.error(f"Error fetching NASA APOD: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="nasa-get-apod",
        description="Get NASA's Astronomy Picture of the Day with its explanation. Uses DEMO_KEY (30 req/hr limit) unless NASA_API_KEY env var is set.",
        input_schema=APODParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["nasa-get-apod"] = handle_get_apod

###################
# NeoWs (Asteroids)
###################


class NeoWsParams(BaseModel):
    """Parameters for getting Near Earth Objects."""

    start_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Start date for asteroid search (YYYY-MM-DD)",
    )
    end_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="End date for asteroid search (YYYY-MM-DD)",
    )


def fetch_neows(params: NeoWsParams) -> dict:
    """Fetch NEO data from NASA API."""
    query_params = {
        "api_key": _API_KEY,
        "start_date": params.start_date,
        "end_date": params.end_date,
    }
    response = http_get(
        f"{BASE_URL}/neo/rest/v1/feed",
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_asteroids(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the nasa-get-asteroids tool call."""
    try:
        if not arguments or "start_date" not in arguments:
            raise ValueError("start_date is required")
        params = NeoWsParams(**arguments)
        data = fetch_neows(params)
        return [types.TextContent(type="text", text=to_json_text(data))]
    except Exception as e:
        log.error(f"Error fetching NASA Asteroids: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="nasa-get-asteroids",
        description="Search for Near Earth Objects (asteroids) within a date range.",
        input_schema=NeoWsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["nasa-get-asteroids"] = handle_get_asteroids

###################
# Mars Rover Photos
###################


class MarsRoverParams(BaseModel):
    """Parameters for fetching Mars rover photos."""

    rover: str = Field(
        ...,
        description="The rover name (curiosity, opportunity, spirit)",
    )
    earth_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="The Earth date the photo was taken (YYYY-MM-DD)",
    )
    camera: str | None = Field(
        None,
        description="The camera abbreviation (e.g., FHAZ, RHAZ, MAST)",
    )


def fetch_mars_photos(params: MarsRoverParams) -> dict:
    """Fetch Mars rover photos from NASA API."""
    query_params = {"api_key": _API_KEY, "earth_date": params.earth_date}
    if params.camera:
        query_params["camera"] = params.camera

    response = http_get(
        f"{BASE_URL}/mars-photos/api/v1/rovers/{params.rover}/photos",
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


def _mars_photos_to_shape_payload(data: dict) -> dict:
    """Adapt NASA Mars rover ``/photos`` response to the records shape
    primitive's payload.

    NASA returns ``{photos: [{id, sol, camera: {name, full_name},
    img_src, earth_date, rover: {name, landing_date, status}}]}``;
    we hoist camera + rover to top-level columns.

    The sibling ``nasa-get-asteroids`` endpoint nests data under
    ``near_earth_objects.<date>[]`` which would need row-explosion to
    fit the records contract; we bind Mars Photos because its response
    is naturally a flat list.
    """
    raw_rows = data.get("photos", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for photo in raw_rows:
        if not isinstance(photo, dict):
            continue
        camera = photo.get("camera") or {}
        rover = photo.get("rover") or {}
        rows.append(
            {
                "id": photo.get("id"),
                "sol": photo.get("sol"),
                "earth_date": photo.get("earth_date"),
                "camera_name": camera.get("name") if isinstance(camera, dict) else None,
                "camera_full_name": camera.get("full_name")
                if isinstance(camera, dict)
                else None,
                "rover_name": rover.get("name") if isinstance(rover, dict) else None,
                "rover_status": rover.get("status")
                if isinstance(rover, dict)
                else None,
                "img_src": photo.get("img_src"),
            },
        )
    return {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "id", "type": "number", "description": "Photo id"},
                {"name": "sol", "type": "number", "description": "Sol (Martian day)"},
                {
                    "name": "earth_date",
                    "type": "date",
                    "description": "Earth date taken",
                },
                {
                    "name": "camera_name",
                    "type": "string",
                    "description": "Camera abbreviation",
                },
                {
                    "name": "camera_full_name",
                    "type": "string",
                    "description": "Camera full name",
                },
                {"name": "rover_name", "type": "string", "description": "Rover name"},
                {
                    "name": "rover_status",
                    "type": "string",
                    "description": "Rover status",
                },
                {"name": "img_src", "type": "string", "description": "Image URL"},
            ],
        },
        "default_facets": ["rover_name", "camera_name", "rover_status"],
    }


async def handle_get_mars_photos(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the nasa-get-mars-photos tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        if not arguments or "rover" not in arguments:
            raise ValueError("rover is required")
        params = MarsRoverParams(**arguments)
        data = fetch_mars_photos(params)
        payload = _mars_photos_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error fetching Mars photos: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="nasa-get-mars-photos",
        description="Get photos taken by NASA's Mars rovers (Curiosity, Opportunity, Spirit).",
        input_schema=MarsRoverParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["nasa-get-mars-photos"] = handle_get_mars_photos

###################
# ACE Solar Wind
###################


class ACESolarWindParams(BaseModel):
    """Parameters for fetching ACE Solar Wind data."""

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


def fetch_ace_data(params: ACESolarWindParams) -> list:
    """Fetch ACE/DSCOVR Solar Wind data from NOAA SWPC.
    Note: Real-time API only provides the last 7 days of data.
    """
    url = "https://services.swpc.noaa.gov/products/solar-wind/plasma-7-day.json"
    response = http_get(url, timeout=10.0, provider=PROVIDER_ID)
    data = response.json()

    if not data or len(data) < 2:
        return []

    headers = data[0]
    results = []

    start_str = params.start_date
    end_str = params.end_date

    for row in data[1:]:
        time_tag = row[0]
        # time_tag format is "YYYY-MM-DD HH:MM:SS.SSS"
        if time_tag:
            date_part = time_tag.split(" ")[0]
            if start_str <= date_part <= end_str:
                # strict=False: NASA CSV-like rows are inherently
                # best-effort; a ragged row produces a partial dict
                # rather than dropping the whole row.
                results.append(dict(zip(headers, row, strict=False)))

    return results


async def handle_get_ace_data(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the nasa-get-ace-data tool call."""
    try:
        if (
            not arguments
            or "start_date" not in arguments
            or "end_date" not in arguments
        ):
            raise ValueError("start_date and end_date are required")
        params = ACESolarWindParams(**arguments)
        data = fetch_ace_data(params)

        if not data:
            return [
                types.TextContent(
                    type="text",
                    text="No data found for the requested date range. Note: Only the last 7 days of data are available from the real-time NOAA Space Weather API.",
                ),
            ]

        return [types.TextContent(type="text", text=to_json_text(data))]
    except Exception as e:
        log.error(f"Error fetching ACE data: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="nasa-get-ace-data",
        description="Get Solar Wind plasma data from NOAA Space Weather (last 7 days only). Dates outside that window return no results.",
        input_schema=ACESolarWindParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["nasa-get-ace-data"] = handle_get_ace_data


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-nasa",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
