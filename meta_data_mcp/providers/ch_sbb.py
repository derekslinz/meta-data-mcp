"""Swiss Federal Railways (SBB) Data API Client

This module provides interfaces to access various endpoints of the SBB Data API (data.sbb.ch).
It includes tools and handlers for retrieving rail traffic information and other railway-related
data through the SBB's public API endpoints.

Features:
- Rail traffic information retrieval
- Configurable query parameters
- Response parsing and type validation using Pydantic models

Usage:
    The module can be run directly to start a server handling API requests,
    or its components can be imported and used individually.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI
from meta_data_mcp.utils import http_get, to_json_text

log = logging.getLogger(__name__)

PROVIDER_ID = "ch-sbb"
BASE_URL = "https://data.sbb.ch/api/explore/v2.1"

# Registration Variables
RESOURCES: list[Any] = []  # resources that will be registered by each endpoints
RESOURCES_HANDLERS: dict[
    str,
    Any,
] = {}  # resources handlers that will be registered by each endpoints
TOOLS: list[types.Tool] = []  # tools that will be registered by each endpoints
TOOLS_HANDLERS: dict[
    str,
    Any,
] = {}  # tools handlers that will be registered by each endpoints


###################
# Rail Traffic Information
###################


# 1. define models for the input / output
class TrafficInfoParams(BaseModel):
    select: str | None = Field(
        None,
        description="Fields to select in the response. Examples: 'title,description' for basic info, 'title,validitybegin,validityend' for timing info",
    )
    where: str | None = Field(
        None,
        description="Filter conditions for traffic info. Examples: 'validitybegin >= NOW()', 'description LIKE \"*Zürich*\"'",
    )
    group_by: str | None = Field(
        None,
        description="Group traffic info by specific fields. Example: 'author' to group by the author of the traffic info",
    )
    order_by: str | None = Field(
        None,
        description="Sort traffic info. Example: 'validitybegin ASC' for chronological order, 'published DESC' for newest first",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of traffic info entries to return (1-100)",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of traffic info entries to skip for pagination",
    )
    refine: str | None = Field(
        None,
        description="Refine by specific facets. Example: 'author:SBB' to show only SBB notifications",
    )
    exclude: str | None = Field(
        None,
        description="Exclude specific fields from response. Example: 'description_html' to exclude HTML formatting",
    )
    lang: str | None = Field(
        None,
        description="Language code for responses (de, fr, it, en). Affects message content language",
    )
    timezone: str = Field(
        default="UTC",
        description="Timezone for validity and publication times. Example: 'Europe/Zurich' for local Swiss time",
    )
    include_links: bool = Field(
        default=False,
        description="Include related links in response",
    )
    include_app_metas: bool = Field(
        default=False,
        description="Include application metadata",
    )


class TrafficInfoResult(BaseModel):
    title: str | None = Field(default=None, description="Title of the traffic info")
    link: str | None = Field(default=None, description="URL to more details")
    description: str | None = Field(
        default=None,
        description="Plain text description",
    )
    published: datetime | None = Field(
        default=None,
        description="Publication timestamp",
    )
    author: str | None = Field(
        default=None,
        description="Author of the traffic info",
    )
    validitybegin: datetime | None = Field(
        default=None,
        description="Start time of the disruption",
    )
    validityend: datetime | None = Field(
        default=None,
        description="End time of the disruption",
    )
    description_html: str | None = Field(
        default=None,
        description="HTML formatted description",
    )


class TrafficInfoResponse(BaseModel):
    total_count: int = Field(description="Total number of results available")
    results: list[TrafficInfoResult] = Field(description="List of traffic info items")


# 2. define the function to fetch the data
def fetch_rail_traffic_info(params: TrafficInfoParams) -> TrafficInfoResponse:
    """Fetch rail traffic information based on the provided parameters.

    Args:
        params: TrafficInfoParams object containing all query parameters

    Returns:
        TrafficInfoResponse object containing the results

    """
    endpoint = f"{BASE_URL}/catalog/datasets/rail-traffic-information/records"
    response = http_get(
        endpoint,
        params=params.model_dump(exclude_none=True),
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return TrafficInfoResponse(**response.json())


# 3. register the function to run when the tool is called
async def handle_rail_traffic_info(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    try:
        traffic_info_response = fetch_rail_traffic_info(
            TrafficInfoParams(**(arguments or {})),
        )
        return [
            types.TextContent(
                type="text",
                text=to_json_text(traffic_info_response.model_dump()),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching rail traffic info: {e}")
        raise


# 4. register the tool
TOOLS.append(
    types.Tool(
        name="rail-traffic-info",
        description="Fetch rail traffic information",
        input_schema=TrafficInfoParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["rail-traffic-info"] = handle_rail_traffic_info

###################
# Railway Line Information
###################


# 1. define models for the input / output
class RailwayLineParams(BaseModel):
    select: str | None = Field(
        None,
        description="Fields to select in the response. Examples: 'linie,linienname' for basic info, 'bpk_anfang,bpk_ende' for station info",
    )
    where: str | None = Field(
        None,
        description="Filter conditions. Examples: 'linie = 100', 'bpk_anfang LIKE \"*Zürich*\"'",
    )
    group_by: str | None = Field(
        None,
        description="Group railway lines by specific fields. Example: 'bpk_anfang' to group by starting station",
    )
    order_by: str | None = Field(
        None,
        description="Sort railway lines. Example: 'linie ASC' for line number order, 'km_ende DESC' for longest routes first",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of railway line entries to return (1-100)",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of railway line entries to skip for pagination",
    )


class GeoPoint2D(BaseModel):
    lon: float = Field(description="Longitude coordinate")
    lat: float = Field(description="Latitude coordinate")


class LineGeometry(BaseModel):
    coordinates: list[list[float]] = Field(
        description="List of coordinate pairs [lon, lat]",
    )
    type: str = Field(description="Geometry type (usually 'LineString')")


class LineFeature(BaseModel):
    type: str = Field(description="Feature type")
    geometry: LineGeometry = Field(description="Line geometry information")
    properties: dict = Field(description="Additional properties")


class RailwayLineResult(BaseModel):
    linie: int | None = Field(default=None, description="Line number")
    linienname: str | None = Field(default=None, description="Line name/description")
    bpk_anfang: str | None = Field(default=None, description="Starting station")
    bpk_ende: str | None = Field(default=None, description="End station")
    km_anfang: float | None = Field(default=None, description="Starting kilometer")
    km_ende: float | None = Field(default=None, description="End kilometer")
    stationierung_anfang: int | None = Field(
        default=None,
        description="Starting stationing",
    )
    stationierung_ende: int | None = Field(
        default=None,
        description="End stationing",
    )
    tst: LineFeature | None = Field(
        default=None,
        description="Geographic line feature",
    )
    geo_point_2d: GeoPoint2D | None = Field(
        default=None,
        description="Center point of the line",
    )


class RailwayLineResponse(BaseModel):
    total_count: int = Field(description="Total number of results available")
    results: list[RailwayLineResult] = Field(description="List of railway line items")


# 2. define the function to fetch the data
def fetch_railway_lines(params: RailwayLineParams) -> RailwayLineResponse:
    """Fetch railway line information based on the provided parameters.

    Args:
        params: RailwayLineParams object containing all query parameters

    Returns:
        RailwayLineResponse object containing the results

    """
    endpoint = f"{BASE_URL}/catalog/datasets/linie/records"
    response = http_get(
        endpoint,
        params=params.model_dump(exclude_none=True),
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return RailwayLineResponse(**response.json())


def _railway_lines_to_shape_payload(data: dict) -> dict:
    """Adapt the SBB railway-lines response to the geofeatures payload.

    SBB returns each line with a ``geo_point_2d: {lon, lat}`` summary
    point (centroid of the line). That single point is the most
    useful representation for an inline map: the full LineString
    geometry would dwarf the marker cluster the bundle is built for.

    Records missing or with invalid ``geo_point_2d`` are dropped
    silently. Non-coordinate fields go into ``attrs``.
    """
    features: list[dict] = []
    if not isinstance(data, dict):
        return {"features": features}
    results = data.get("results")
    if not isinstance(results, list):
        return {"features": features}
    for line in results:
        if not isinstance(line, dict):
            continue
        point = line.get("geo_point_2d")
        if not isinstance(point, dict):
            continue
        try:
            lat = float(point["lat"])
            lon = float(point["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            continue
        attrs = {k: v for k, v in line.items() if k != "geo_point_2d"}
        features.append({"lat": lat, "lon": lon, "attrs": attrs})
    return {"features": features}


# 3. register the function to run when the tool is called
async def handle_railway_lines(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Handle the railway-lines tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format so the MCP Apps host can render the result inline
    via the bound shape primitive.
    """
    try:
        railway_lines_response = fetch_railway_lines(
            RailwayLineParams(**(arguments or {})),
        )
        payload = _railway_lines_to_shape_payload(railway_lines_response.model_dump())
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error fetching railway lines: {e}")
        raise


# 4. register the tool
TOOLS.append(
    types.Tool(
        name="railway-lines",
        description="Fetch railway line information",
        input_schema=RailwayLineParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["railway-lines"] = handle_railway_lines

###################
# Rolling Stock Information
###################


# 1. define models for the input / output
class RollingStockParams(BaseModel):
    select: str | None = Field(
        None,
        description="Fields to select in the response. Examples: 'fahrzeug_typ,objekt' for basic info, 'vmax_betrieblich_zugelassen,lange_uber_puffer_lup' for technical details",
    )
    where: str | None = Field(
        None,
        description="Filter conditions. Examples: 'fahrzeug_typ = \"X\"', 'vmax_betrieblich_zugelassen > 100'",
    )
    group_by: str | None = Field(
        None,
        description="Group rolling stock by specific fields. Example: 'fahrzeug_typ' to group by vehicle type",
    )
    order_by: str | None = Field(
        None,
        description="Sort rolling stock. Example: 'baudatum_fahrzeug ASC' for oldest first, 'vmax_betrieblich_zugelassen DESC' for fastest first",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of rolling stock entries to return (1-100)",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of rolling stock entries to skip for pagination",
    )


class RollingStockResult(BaseModel):
    fahrzeug_art_struktur: str | None = Field(
        default=None,
        description="Vehicle structure type",
    )
    fahrzeug_typ: str | None = Field(default=None, description="Vehicle type")
    objekt: str | None = Field(default=None, description="Vehicle identifier")
    baudatum_fahrzeug: str | None = Field(default=None, description="Build date")
    eigengewicht_tara: float | None = Field(default=None, description="Tare weight")
    lange_uber_puffer_lup: int | None = Field(
        default=None,
        description="Length over buffers (mm)",
    )
    vmax_betrieblich_zugelassen: int | None = Field(
        default=None,
        description="Maximum operational speed",
    )
    # Add other fields as needed, all as Optional since many can be null


class RollingStockResponse(BaseModel):
    total_count: int = Field(description="Total number of results available")
    results: list[RollingStockResult] = Field(description="List of rolling stock items")


# 2. define the function to fetch the data
def fetch_rolling_stock(params: RollingStockParams) -> RollingStockResponse:
    """Fetch rolling stock information based on the provided parameters.

    Args:
        params: RollingStockParams object containing all query parameters

    Returns:
        RollingStockResponse object containing the results

    """
    endpoint = f"{BASE_URL}/catalog/datasets/rollmaterial/records"
    response = http_get(
        endpoint,
        params=params.model_dump(exclude_none=True),
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return RollingStockResponse(**response.json())


# 3. register the function to run when the tool is called
async def handle_rolling_stock(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    try:
        rolling_stock_response = fetch_rolling_stock(
            RollingStockParams(**(arguments or {})),
        )
        return [
            types.TextContent(
                type="text",
                text=to_json_text(rolling_stock_response.model_dump()),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching rolling stock info: {e}")
        raise


# 4. register the tool
TOOLS.append(
    types.Tool(
        name="rolling-stock",
        description="Fetch rolling stock (vehicle) information",
        input_schema=RollingStockParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["rolling-stock"] = handle_rolling_stock


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "data.sbb.ch",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    # anyio.run(main)

    # test the endpoints
    print(
        "Rail Traffic Info:",
        fetch_rail_traffic_info(TrafficInfoParams(select="title,description", limit=1)),
    )
    print(
        "Railway Lines:",
        fetch_railway_lines(RailwayLineParams(select="linie,linienname", limit=1)),
    )
    print(
        "Rolling Stock:",
        fetch_rolling_stock(RollingStockParams(select="fahrzeug_typ,objekt", limit=1)),
    )
