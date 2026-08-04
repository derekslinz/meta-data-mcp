"""global-openaq provider.

OpenAQ — open global air-quality data aggregated from government reference
monitors and low-cost sensors. Covers PM2.5, PM10, NO2, SO2, CO, O3, BC,
relative humidity, and temperature at thousands of stations worldwide.

Homepage: https://openaq.org
API docs: https://docs.openaq.org/
License: CC BY 4.0 (most sources); per-location attribution available
via the `provider` and `attribution` fields on each location.
Auth: anonymous works; set ``OPENAQ_API_KEY`` env var for higher rate
limits (free signup at https://explore.openaq.org/register).
"""

import logging
import os
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI
from meta_data_mcp.utils import (
    create_mcp_server,
    http_get,
    run_server,
    serialize_for_llm,
    to_geofeatures_text,
)

log = logging.getLogger(__name__)

PROVIDER_ID = "global-openaq"
BASE_URL = "https://api.openaq.org/v3"

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


def _auth_headers() -> dict[str, str]:
    """Return X-API-Key header when OPENAQ_API_KEY is set, else empty."""
    token = os.getenv("OPENAQ_API_KEY")
    if token:
        return {"X-API-Key": token}
    return {}


###################
# openaq-list-locations
###################


class OpenAqListLocationsParams(BaseModel):
    """Parameters for openaq-list-locations."""

    coordinates: str | None = Field(
        None,
        description=(
            "Search near a point. Format: 'lat,lon' "
            "(e.g. '37.7749,-122.4194' for San Francisco)."
        ),
    )
    radius: int | None = Field(
        None,
        ge=1,
        le=25000,
        description="Search radius in meters when `coordinates` is set (1–25000).",
    )
    iso: str | None = Field(
        None,
        description="ISO 3166-1 alpha-2 country code (e.g. 'US', 'NL', 'JP').",
    )
    parameters_id: int | None = Field(
        None,
        description=(
            "Numeric parameter id to filter locations measuring a specific pollutant. "
            "Use openaq-list-parameters to discover ids."
        ),
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum locations to return.",
    )
    page: int = Field(default=1, ge=1, description="Pagination page (1-indexed).")


def fetch_openaq_list_locations(params: OpenAqListLocationsParams) -> Any:
    """Search OpenAQ monitoring locations."""
    query: dict[str, Any] = {"limit": params.limit, "page": params.page}
    if params.coordinates is not None:
        query["coordinates"] = params.coordinates
    if params.radius is not None:
        query["radius"] = params.radius
    if params.iso is not None:
        query["iso"] = params.iso
    if params.parameters_id is not None:
        query["parameters_id"] = params.parameters_id
    response = http_get(
        f"{BASE_URL}/locations",
        params=query,
        headers=_auth_headers() or None,
        provider=PROVIDER_ID,
    )
    return response.json()


def _openaq_locations_to_shape_payload(data: Any) -> dict:
    """Adapt OpenAQ v3 ``/locations`` to the geofeatures payload contract.

    Each location carries a ``coordinates`` block with ``latitude`` and
    ``longitude``. Records without usable coordinates (e.g. mobile
    sensors with deferred geocoding) are dropped silently.
    """
    features: list[dict] = []
    if not isinstance(data, dict):
        return {"features": features}
    results = data.get("results")
    if not isinstance(results, list):
        return {"features": features}
    for loc in results:
        if not isinstance(loc, dict):
            continue
        coords = loc.get("coordinates")
        if not isinstance(coords, dict):
            continue
        try:
            lat = float(coords["latitude"])
            lon = float(coords["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            continue
        attrs = {k: v for k, v in loc.items() if k != "coordinates"}
        features.append({"lat": lat, "lon": lon, "attrs": attrs})
    return {"features": features}


async def handle_openaq_list_locations(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openaq-list-locations tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format so the MCP Apps host can render the result inline
    via the bound shape primitive.
    """
    params = OpenAqListLocationsParams(**(arguments or {}))
    data = fetch_openaq_list_locations(params)
    payload = _openaq_locations_to_shape_payload(data)
    return [types.TextContent(type="text", text=to_geofeatures_text(payload))]


TOOLS.append(
    types.Tool(
        name="openaq-list-locations",
        description=(
            "List OpenAQ air-quality monitoring locations. Filter by coordinates "
            "(lat,lon + radius), ISO country code, or pollutant parameter. Returns "
            "station metadata including which pollutants are measured."
        ),
        input_schema=OpenAqListLocationsParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["openaq-list-locations"] = handle_openaq_list_locations


###################
# openaq-location-latest
###################


class OpenAqLocationLatestParams(BaseModel):
    """Parameters for openaq-location-latest."""

    location_id: int = Field(
        ...,
        ge=1,
        description="Numeric OpenAQ location id (from openaq-list-locations).",
    )


def fetch_openaq_location_latest(params: OpenAqLocationLatestParams) -> Any:
    """Fetch the latest measurement values for a location."""
    response = http_get(
        f"{BASE_URL}/locations/{params.location_id}/latest",
        headers=_auth_headers() or None,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openaq_location_latest(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openaq-location-latest tool call."""
    params = OpenAqLocationLatestParams(**(arguments or {}))
    data = fetch_openaq_location_latest(params)
    return [types.TextContent(type="text", text=serialize_for_llm(data))]


TOOLS.append(
    types.Tool(
        name="openaq-location-latest",
        description=(
            "Fetch the most-recent measurement values for each parameter at a "
            "given OpenAQ location id."
        ),
        input_schema=OpenAqLocationLatestParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openaq-location-latest"] = handle_openaq_location_latest


###################
# openaq-list-parameters
###################


class OpenAqListParametersParams(BaseModel):
    """Parameters for openaq-list-parameters."""

    limit: int = Field(default=100, ge=1, le=1000, description="Maximum to return.")


def fetch_openaq_list_parameters(params: OpenAqListParametersParams) -> Any:
    """List all parameters (pollutants and meteorological measures)."""
    response = http_get(
        f"{BASE_URL}/parameters",
        params={"limit": params.limit},
        headers=_auth_headers() or None,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openaq_list_parameters(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openaq-list-parameters tool call."""
    params = OpenAqListParametersParams(**(arguments or {}))
    data = fetch_openaq_list_parameters(params)
    return [types.TextContent(type="text", text=serialize_for_llm(data))]


TOOLS.append(
    types.Tool(
        name="openaq-list-parameters",
        description=(
            "List the parameters (pollutants and meteorological variables) "
            "tracked by OpenAQ, with unit and display name. Use the returned "
            "ids to filter openaq-list-locations."
        ),
        input_schema=OpenAqListParametersParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openaq-list-parameters"] = handle_openaq_list_parameters


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    server = create_mcp_server(
        "global-openaq",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )
    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
