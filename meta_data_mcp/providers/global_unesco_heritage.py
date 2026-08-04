"""UNESCO World Heritage Centre Provider

This module exposes the UNESCO World Heritage Centre (WHC) API, providing
free access to data about UNESCO World Heritage Sites, including natural,
cultural, and mixed properties.

License / source:
    Data is provided by UNESCO under the CC BY-SA 3.0 IGO license.
    Consult https://whc.unesco.org/en/syndication for details.
    Source API: https://whc.unesco.org/api/

Features:
- List World Heritage Sites with filtering by country, region, category, and danger status
- Retrieve details for a specific site by its WHC ID
- Search sites by name

Usage:
    The module can be run directly to start an MCP server, or its components
    can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_geofeatures_text

log = logging.getLogger(__name__)

PROVIDER_ID = "global-unesco-heritage"
BASE_URL = "https://whc.unesco.org/api/sites"

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# List Sites
###################


class UNESCOHeritageListSitesParams(BaseModel):
    """Parameters for listing UNESCO World Heritage Sites."""

    region: str | None = Field(
        None,
        description=(
            "Filter by UNESCO region code. One of: 'afr' (Africa), 'ara' (Arab States), "
            "'apa' (Asia-Pacific), 'eur' (Europe & North America), 'lac' (Latin America), "
            "'sas' (South Asia)."
        ),
    )
    iso: str | None = Field(
        None,
        description="Filter by ISO 3166-1 alpha-2 country code (e.g. 'FR', 'CN', 'IT').",
    )
    category: str | None = Field(
        None,
        description=(
            "Filter by inscription category: 'Cultural' (C), 'Natural' (N), "
            "or 'Mixed' (C/N)."
        ),
    )
    danger: int | None = Field(
        None,
        description="Set to 1 to return only sites on the List of World Heritage in Danger.",
    )
    order: str | None = Field(
        None,
        description="Sort order field (e.g. 'name', 'date_inscribed', 'id_number').",
    )
    page: int = Field(default=1, ge=1, description="Results page (1-indexed).")
    per_page: int = Field(
        default=30,
        ge=1,
        le=100,
        description="Results per page (1-100).",
    )


def fetch_unesco_heritage_list_sites(
    params: UNESCOHeritageListSitesParams,
) -> dict:
    """Fetch a list of UNESCO World Heritage Sites."""
    query_params: dict[str, Any] = {
        "format": "json",
        "page": params.page,
        "per_page": params.per_page,
    }
    if params.region:
        query_params["region"] = params.region
    if params.iso:
        query_params["iso"] = params.iso
    if params.category:
        query_params["category"] = params.category
    if params.danger is not None:
        query_params["danger"] = params.danger
    if params.order:
        query_params["order"] = params.order
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


def _unesco_sites_to_shape_payload(data: Any) -> dict:
    """Adapt the UNESCO WHC ``/sites`` response to the geofeatures
    payload contract.

    Each site carries ``latitude`` / ``longitude`` (as strings) in the
    JSON feed. Sites lacking usable coordinates (e.g. when the list
    endpoint is filtered to summary fields only) are dropped silently.
    """
    features: list[dict] = []
    if not isinstance(data, list):
        return {"features": features}
    for site in data:
        if not isinstance(site, dict):
            continue
        lat_raw = site.get("latitude")
        lon_raw = site.get("longitude")
        try:
            lat = float(lat_raw) if lat_raw not in (None, "") else None
            lon = float(lon_raw) if lon_raw not in (None, "") else None
        except (TypeError, ValueError):
            continue
        if lat is None or lon is None:
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            continue
        attrs = {k: v for k, v in site.items() if k not in ("latitude", "longitude")}
        features.append({"lat": lat, "lon": lon, "attrs": attrs})
    return {"features": features}


async def handle_unesco_heritage_list_sites(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the unesco-heritage-list-sites tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format so the MCP Apps host can render the result inline
    via the bound shape primitive.
    """
    try:
        params = UNESCOHeritageListSitesParams(**(arguments or {}))
        data = fetch_unesco_heritage_list_sites(params)
        payload = _unesco_sites_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_geofeatures_text(payload))]
    except Exception as e:
        log.error(f"Error listing UNESCO Heritage sites: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="unesco-heritage-list-sites",
        description=(
            "List UNESCO World Heritage Sites, optionally filtered by region, country, "
            "category (Cultural/Natural/Mixed), or danger status."
        ),
        input_schema=UNESCOHeritageListSitesParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["unesco-heritage-list-sites"] = handle_unesco_heritage_list_sites


###################
# Get Site
###################


class UNESCOHeritageGetSiteParams(BaseModel):
    """Parameters for fetching a specific UNESCO World Heritage Site."""

    site_id: int = Field(
        ...,
        description="WHC numeric site ID (e.g. 1 for Galápagos Islands, 456 for Taj Mahal).",
    )


def fetch_unesco_heritage_get_site(params: UNESCOHeritageGetSiteParams) -> dict:
    """Fetch a specific UNESCO World Heritage Site by ID."""
    response = http_get(
        f"{BASE_URL}/{params.site_id}",
        params={"format": "json"},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_unesco_heritage_get_site(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the unesco-heritage-get-site tool call."""
    try:
        if not arguments or "site_id" not in arguments:
            raise ValueError("site_id is required")
        params = UNESCOHeritageGetSiteParams(**arguments)
        data = fetch_unesco_heritage_get_site(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching UNESCO Heritage site: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="unesco-heritage-get-site",
        description=(
            "Get detailed information about a specific UNESCO World Heritage Site by its WHC ID, "
            "including name, country, category, inscription year, coordinates, and description."
        ),
        input_schema=UNESCOHeritageGetSiteParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["unesco-heritage-get-site"] = handle_unesco_heritage_get_site


###################
# Search Sites
###################


class UNESCOHeritageSearchParams(BaseModel):
    """Parameters for searching UNESCO World Heritage Sites by name."""

    name: str = Field(
        ...,
        description="Name or keyword to search for (e.g. 'Great Barrier Reef', 'Venice').",
    )
    page: int = Field(default=1, ge=1, description="Results page (1-indexed).")
    per_page: int = Field(
        default=30,
        ge=1,
        le=100,
        description="Results per page (1-100).",
    )


def fetch_unesco_heritage_search(params: UNESCOHeritageSearchParams) -> dict:
    """Search UNESCO World Heritage Sites by name."""
    query_params: dict[str, Any] = {
        "format": "json",
        "name": params.name,
        "page": params.page,
        "per_page": params.per_page,
    }
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_unesco_heritage_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the unesco-heritage-search tool call."""
    try:
        if not arguments or "name" not in arguments:
            raise ValueError("name is required")
        params = UNESCOHeritageSearchParams(**arguments)
        data = fetch_unesco_heritage_search(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching UNESCO Heritage sites: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="unesco-heritage-search",
        description="Search UNESCO World Heritage Sites by name or keyword.",
        input_schema=UNESCOHeritageSearchParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["unesco-heritage-search"] = handle_unesco_heritage_search


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-unesco-heritage",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
