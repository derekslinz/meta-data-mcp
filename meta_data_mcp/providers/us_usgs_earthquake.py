"""US Geological Survey (USGS) Earthquake Hazards Program Provider

This module exposes the USGS Earthquake Catalog FDSN web service and the
public GeoJSON real-time feeds. The catalog covers global seismic events
recorded by the USGS Advanced National Seismic System and partner networks.

License note:
    USGS data products are generally released as US Government works and
    are in the public domain in the United States. Consult the USGS data
    policy (https://www.usgs.gov/information-policies-and-instructions) for
    attribution and redistribution requirements outside the US.

Features:
- FDSN event query with magnitude/time/bounding-box filters
- FDSN event count (totals matching a filter)
- Real-time GeoJSON feeds (significant, all, M4.5+) for day/week windows
- Application version metadata

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_geofeatures_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-usgs-earthquake"
BASE_URL = "https://earthquake.usgs.gov/fdsnws/event/1"
FEED_BASE_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Event Query
###################


class USGSEqQueryParams(BaseModel):
    """Parameters for querying the USGS FDSN event catalog."""

    starttime: str | None = Field(
        None,
        description="Start time (ISO 8601, e.g. 2024-01-01 or 2024-01-01T00:00:00).",
    )
    endtime: str | None = Field(
        None,
        description="End time (ISO 8601, e.g. 2024-01-02 or 2024-01-02T00:00:00).",
    )
    minmagnitude: float | None = Field(
        None,
        description="Minimum event magnitude (inclusive).",
    )
    maxmagnitude: float | None = Field(
        None,
        description="Maximum event magnitude (inclusive).",
    )
    minlatitude: float | None = Field(
        None,
        ge=-90.0,
        le=90.0,
        description="Southern bounding-box latitude.",
    )
    maxlatitude: float | None = Field(
        None,
        ge=-90.0,
        le=90.0,
        description="Northern bounding-box latitude.",
    )
    minlongitude: float | None = Field(
        None,
        ge=-360.0,
        le=360.0,
        description="Western bounding-box longitude.",
    )
    maxlongitude: float | None = Field(
        None,
        ge=-360.0,
        le=360.0,
        description="Eastern bounding-box longitude.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=20000,
        description="Maximum number of events to return.",
    )


def fetch_usgs_eq_query(params: USGSEqQueryParams) -> dict:
    """Call the FDSN /query endpoint and return the GeoJSON FeatureCollection."""
    query_params: dict[str, Any] = {"format": "geojson", "limit": params.limit}
    for key in (
        "starttime",
        "endtime",
        "minmagnitude",
        "maxmagnitude",
        "minlatitude",
        "maxlatitude",
        "minlongitude",
        "maxlongitude",
    ):
        value = getattr(params, key)
        if value is not None:
            query_params[key] = value
    response = http_get(f"{BASE_URL}/query", params=query_params, provider=PROVIDER_ID)
    return response.json()


def _usgs_geojson_to_shape_payload(data: Any) -> dict:
    """Adapt the USGS FDSN GeoJSON FeatureCollection to the geofeatures
    payload contract by wrapping it in ``{features: <FeatureCollection>}``.

    USGS already returns proper GeoJSON, so option A (native pass-through)
    is the cleanest route — Leaflet inside the bundle consumes
    FeatureCollections directly.

    Non-dict or non-GeoJSON responses degrade to an empty
    FeatureCollection so the bundle never has to handle invalid shapes.
    """
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        return {"features": data}
    return {"features": {"type": "FeatureCollection", "features": []}}


async def handle_usgs_eq_query(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the usgs-eq-query tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format. USGS already returns a GeoJSON FeatureCollection,
    so it is wrapped natively under ``features`` (option A).
    """
    try:
        params = USGSEqQueryParams(**(arguments or {}))
        data = fetch_usgs_eq_query(params)
        payload = _usgs_geojson_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_geofeatures_text(payload))]
    except Exception as e:
        log.error(f"Error querying USGS earthquake catalog: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="usgs-eq-query",
        description="Query the USGS FDSN earthquake catalog (GeoJSON). Supports time, magnitude, and bounding-box filters.",
        input_schema=USGSEqQueryParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["usgs-eq-query"] = handle_usgs_eq_query


###################
# Event Count
###################


class USGSEqCountParams(BaseModel):
    """Parameters for counting events that match a filter."""

    starttime: str | None = Field(None, description="Start time (ISO 8601).")
    endtime: str | None = Field(None, description="End time (ISO 8601).")
    minmagnitude: float | None = Field(
        None,
        description="Minimum event magnitude (inclusive).",
    )


def fetch_usgs_eq_count(params: USGSEqCountParams) -> dict:
    """Call the FDSN /count endpoint."""
    query_params: dict[str, Any] = {"format": "geojson"}
    for key in ("starttime", "endtime", "minmagnitude"):
        value = getattr(params, key)
        if value is not None:
            query_params[key] = value
    response = http_get(f"{BASE_URL}/count", params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_usgs_eq_count(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the usgs-eq-count tool call."""
    try:
        params = USGSEqCountParams(**(arguments or {}))
        data = fetch_usgs_eq_count(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error counting USGS earthquake events: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="usgs-eq-count",
        description="Return the number of USGS earthquake events matching a filter (no payload).",
        input_schema=USGSEqCountParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["usgs-eq-count"] = handle_usgs_eq_count


###################
# Fixed Real-Time Feeds
###################


class USGSEqFeedParams(BaseModel):
    """No parameters; feeds are fixed URLs maintained by USGS."""


def _fetch_feed(url: str) -> dict:
    """Helper: fetch one of the static GeoJSON summary feeds."""
    response = http_get(url, provider=PROVIDER_ID)
    return response.json()


def fetch_usgs_eq_feed_significant_day(_params: USGSEqFeedParams) -> dict:
    return _fetch_feed(f"{FEED_BASE_URL}/significant_day.geojson")


def fetch_usgs_eq_feed_significant_week(_params: USGSEqFeedParams) -> dict:
    return _fetch_feed(f"{FEED_BASE_URL}/significant_week.geojson")


def fetch_usgs_eq_feed_all_day(_params: USGSEqFeedParams) -> dict:
    return _fetch_feed(f"{FEED_BASE_URL}/all_day.geojson")


def fetch_usgs_eq_feed_all_week(_params: USGSEqFeedParams) -> dict:
    return _fetch_feed(f"{FEED_BASE_URL}/all_week.geojson")


def fetch_usgs_eq_feed_m45_week(_params: USGSEqFeedParams) -> dict:
    return _fetch_feed(f"{FEED_BASE_URL}/4.5_week.geojson")


async def handle_usgs_eq_feed_significant_day(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the usgs-eq-feed-significant-day tool call."""
    try:
        params = USGSEqFeedParams(**(arguments or {}))
        data = fetch_usgs_eq_feed_significant_day(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching USGS significant_day feed: {e}")
        raise


async def handle_usgs_eq_feed_significant_week(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the usgs-eq-feed-significant-week tool call."""
    try:
        params = USGSEqFeedParams(**(arguments or {}))
        data = fetch_usgs_eq_feed_significant_week(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching USGS significant_week feed: {e}")
        raise


async def handle_usgs_eq_feed_all_day(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the usgs-eq-feed-all-day tool call."""
    try:
        params = USGSEqFeedParams(**(arguments or {}))
        data = fetch_usgs_eq_feed_all_day(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching USGS all_day feed: {e}")
        raise


async def handle_usgs_eq_feed_all_week(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the usgs-eq-feed-all-week tool call."""
    try:
        params = USGSEqFeedParams(**(arguments or {}))
        data = fetch_usgs_eq_feed_all_week(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching USGS all_week feed: {e}")
        raise


async def handle_usgs_eq_feed_m45_week(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the usgs-eq-feed-m45-week tool call."""
    try:
        params = USGSEqFeedParams(**(arguments or {}))
        data = fetch_usgs_eq_feed_m45_week(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching USGS 4.5_week feed: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="usgs-eq-feed-significant-day",
        description="GeoJSON feed of significant earthquakes in the past day.",
        input_schema=USGSEqFeedParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["usgs-eq-feed-significant-day"] = handle_usgs_eq_feed_significant_day

TOOLS.append(
    types.Tool(
        name="usgs-eq-feed-significant-week",
        description="GeoJSON feed of significant earthquakes in the past week.",
        input_schema=USGSEqFeedParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["usgs-eq-feed-significant-week"] = handle_usgs_eq_feed_significant_week

TOOLS.append(
    types.Tool(
        name="usgs-eq-feed-all-day",
        description="GeoJSON feed of all earthquakes in the past day.",
        input_schema=USGSEqFeedParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["usgs-eq-feed-all-day"] = handle_usgs_eq_feed_all_day

TOOLS.append(
    types.Tool(
        name="usgs-eq-feed-all-week",
        description="GeoJSON feed of all earthquakes in the past week.",
        input_schema=USGSEqFeedParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["usgs-eq-feed-all-week"] = handle_usgs_eq_feed_all_week

TOOLS.append(
    types.Tool(
        name="usgs-eq-feed-m45-week",
        description="GeoJSON feed of earthquakes with magnitude >= 4.5 in the past week.",
        input_schema=USGSEqFeedParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["usgs-eq-feed-m45-week"] = handle_usgs_eq_feed_m45_week


###################
# Application Version
###################


class USGSEqVersionParams(BaseModel):
    """No parameters required for the /version endpoint."""


def fetch_usgs_eq_application_version(_params: USGSEqVersionParams) -> str:
    """Call the FDSN /version endpoint. Response is plain text, not JSON."""
    response = http_get(
        f"{BASE_URL}/version",
        headers={"Accept": "text/plain"},
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_usgs_eq_application_version(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the usgs-eq-application-version tool call."""
    try:
        params = USGSEqVersionParams(**(arguments or {}))
        data = fetch_usgs_eq_application_version(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching USGS earthquake catalog version: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="usgs-eq-application-version",
        description="Return the USGS FDSN event web-service application version string.",
        input_schema=USGSEqVersionParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["usgs-eq-application-version"] = handle_usgs_eq_application_version


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-usgs-earthquake",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
