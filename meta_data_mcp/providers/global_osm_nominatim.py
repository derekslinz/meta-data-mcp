"""OpenStreetMap Nominatim Provider

This module exposes the OpenStreetMap Nominatim geocoding API (forward and
reverse geocoding) as MCP tools.

Source: https://nominatim.org/release-docs/develop/api/Overview/
License: Data is from OpenStreetMap (ODbL). Attribution required.

Fair-use notes (CRITICAL):
    Nominatim's public endpoint enforces a strict usage policy:
    https://operations.osmfoundation.org/policies/nominatim/

    - Max 1 request per second.
    - Must use an identifiable User-Agent (already set by ``http_get``).
    - No heavy / bulk usage; consider self-hosting Nominatim for that.

Features:
- Forward geocoding (free-text and structured)
- Reverse geocoding (lat/lon -> address)
- OSM ID lookup
- Service status

Usage:
    The module can be run directly to start a server handling API requests,
    or its components can be imported and used individually.
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
PROVIDER_ID = "global-osm-nominatim"
BASE_URL = "https://nominatim.openstreetmap.org"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Free-text Search
###################


class NominatimSearchParams(BaseModel):
    """Parameters for free-text Nominatim search (forward geocoding)."""

    q: str = Field(..., description="Free-text query (e.g. 'Eiffel Tower, Paris')")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum results")


def fetch_search(params: NominatimSearchParams) -> Any:
    """Run a forward geocoding query against /search."""
    query_params: dict[str, Any] = {
        "q": params.q,
        "format": "json",
        "limit": params.limit,
        "addressdetails": 1,
        "extratags": 1,
    }
    response = http_get(
        f"{BASE_URL}/search",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


def _nominatim_search_results_to_shape_payload(data: Any) -> dict:
    """Adapt Nominatim's ``[{lat, lon, display_name, ...}, ...]`` result
    list to the geofeatures payload contract.

    Nominatim returns ``lat``/``lon`` as strings, so they are coerced to
    floats here. Records with missing or unparseable coordinates are
    skipped silently; out-of-range values are dropped.
    """
    features: list[dict] = []
    if not isinstance(data, list):
        return {"features": features}
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            continue
        attrs = {k: v for k, v in item.items() if k not in ("lat", "lon")}
        features.append({"lat": lat, "lon": lon, "attrs": attrs})
    return {"features": features}


async def handle_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the nominatim-search tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format so the MCP Apps host can render the result inline
    via the bound shape primitive.
    """
    try:
        if not arguments or "q" not in arguments:
            raise ValueError("q is required")
        params = NominatimSearchParams(**arguments)
        data = fetch_search(params)
        payload = _nominatim_search_results_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_geofeatures_text(payload))]
    except Exception as e:
        log.error(f"Error in Nominatim search: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="nominatim-search",
        description=(
            "Forward-geocode a free-text query against OpenStreetMap Nominatim. "
            "Respect Nominatim's 1 req/s usage policy."
        ),
        input_schema=NominatimSearchParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["nominatim-search"] = handle_search


###################
# Reverse Geocoding
###################


class NominatimReverseParams(BaseModel):
    """Parameters for reverse geocoding."""

    lat: float = Field(
        ...,
        ge=-90.0,
        le=90.0,
        description="Latitude in decimal degrees",
    )
    lon: float = Field(
        ...,
        ge=-180.0,
        le=180.0,
        description="Longitude in decimal degrees",
    )


def fetch_reverse(params: NominatimReverseParams) -> Any:
    """Reverse-geocode a coordinate to an address via /reverse."""
    query_params: dict[str, Any] = {
        "lat": params.lat,
        "lon": params.lon,
        "format": "json",
        "addressdetails": 1,
    }
    response = http_get(
        f"{BASE_URL}/reverse",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_reverse(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the nominatim-reverse tool call."""
    try:
        if not arguments or "lat" not in arguments or "lon" not in arguments:
            raise ValueError("lat and lon are required")
        params = NominatimReverseParams(**arguments)
        data = fetch_reverse(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error in Nominatim reverse: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="nominatim-reverse",
        description=(
            "Reverse-geocode a latitude/longitude pair into an OpenStreetMap address. "
            "Respect Nominatim's 1 req/s usage policy."
        ),
        input_schema=NominatimReverseParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["nominatim-reverse"] = handle_reverse


###################
# Lookup by OSM IDs
###################


class NominatimLookupParams(BaseModel):
    """Parameters for OSM ID lookup."""

    osm_ids: str = Field(
        ...,
        description="Comma-separated OSM IDs prefixed by N/W/R (e.g. 'N123,W456,R789')",
    )


def fetch_lookup(params: NominatimLookupParams) -> Any:
    """Look up one or more OSM IDs via /lookup."""
    query_params: dict[str, Any] = {
        "osm_ids": params.osm_ids,
        "format": "json",
        "addressdetails": 1,
    }
    response = http_get(
        f"{BASE_URL}/lookup",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_lookup(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the nominatim-lookup tool call."""
    try:
        if not arguments or "osm_ids" not in arguments:
            raise ValueError("osm_ids is required")
        params = NominatimLookupParams(**arguments)
        data = fetch_lookup(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error in Nominatim lookup: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="nominatim-lookup",
        description=(
            "Look up one or more OSM objects by ID (e.g. 'N123,W456,R789'). "
            "Respect Nominatim's 1 req/s usage policy."
        ),
        input_schema=NominatimLookupParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["nominatim-lookup"] = handle_lookup


###################
# Structured Search
###################


class NominatimSearchStructuredParams(BaseModel):
    """Parameters for structured forward geocoding."""

    country: str | None = Field(None, description="Country name or code")
    state: str | None = Field(None, description="State / region")
    city: str | None = Field(None, description="City")
    street: str | None = Field(
        None,
        description="Street (with optional housenumber)",
    )
    postalcode: str | None = Field(None, description="Postal code")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum results")


def fetch_search_structured(params: NominatimSearchStructuredParams) -> Any:
    """Run a structured forward geocoding query against /search."""
    query_params: dict[str, Any] = {"format": "json", "limit": params.limit}
    if params.country:
        query_params["country"] = params.country
    if params.state:
        query_params["state"] = params.state
    if params.city:
        query_params["city"] = params.city
    if params.street:
        query_params["street"] = params.street
    if params.postalcode:
        query_params["postalcode"] = params.postalcode

    response = http_get(
        f"{BASE_URL}/search",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_search_structured(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the nominatim-search-structured tool call."""
    try:
        params = NominatimSearchStructuredParams(**(arguments or {}))
        data = fetch_search_structured(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error in Nominatim structured search: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="nominatim-search-structured",
        description=(
            "Structured forward geocoding: combine country, state, city, street, "
            "postalcode fields. Respect Nominatim's 1 req/s usage policy."
        ),
        input_schema=NominatimSearchStructuredParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["nominatim-search-structured"] = handle_search_structured


###################
# Status
###################


class NominatimStatusParams(BaseModel):
    """Parameters for the Nominatim status endpoint."""


def fetch_status(_params: NominatimStatusParams) -> Any:
    """Fetch Nominatim service status via /status."""
    response = http_get(
        f"{BASE_URL}/status",
        params={"format": "json"},
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_status(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the nominatim-status tool call."""
    try:
        params = NominatimStatusParams(**(arguments or {}))
        data = fetch_status(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Nominatim status: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="nominatim-status",
        description="Get the OSM Nominatim service status (JSON).",
        input_schema=NominatimStatusParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["nominatim-status"] = handle_status


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-osm-nominatim",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
