"""Copernicus Earth Observation Data API Client

This module provides interfaces to access the Copernicus Data Space Ecosystem (CDSE).
It allows discovery of satellite data (Sentinel, Landsat, etc.) through STAC and OData APIs.

Features:
- Collection discovery through STAC Collections API
- Product search via STAC Search API (bbox, datetime, collection)
- Detailed product metadata retrieval via OData API

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
from meta_data_mcp.utils import http_get, http_post, to_json_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "eu-copernicus"
STAC_BASE_URL = "https://stac.dataspace.copernicus.eu/v1"
ODATA_BASE_URL = "https://catalogue.dataspace.copernicus.eu/odata/v1"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# Copernicus Collections
###################


class ListCollectionsParams(BaseModel):
    """Parameters for listing Copernicus satellite collections."""

    limit: int = Field(
        default=20,
        description="Maximum number of collections to return",
    )


class CollectionInfo(BaseModel):
    """Basic information about a Copernicus collection."""

    id: str = Field(..., description="The unique ID of the collection")
    title: str | None = Field(None, description="The title of the collection")
    description: str | None = Field(
        None,
        description="Description of the collection",
    )


def list_copernicus_collections(params: ListCollectionsParams) -> list[CollectionInfo]:
    """Fetch available satellite data collections from STAC API."""
    endpoint = f"{STAC_BASE_URL}/collections"
    response = http_get(endpoint, timeout=10.0, provider=PROVIDER_ID)
    data = response.json()
    collections = []
    for col in data.get("collections", []):
        collections.append(
            CollectionInfo(
                id=col.get("id"),
                title=col.get("title"),
                description=col.get("description"),
            ),
        )
        if len(collections) >= params.limit:
            break
    return collections


async def handle_list_collections(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the copernicus-list-collections tool call."""
    try:
        params = ListCollectionsParams(**(arguments or {}))
        collections = list_copernicus_collections(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(
                    [c.model_dump() for c in collections],
                    max_chars=10000,
                ),
            ),
        ]
    except Exception as e:
        log.error(f"Error listing Copernicus collections: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="copernicus-list-collections",
        description="List available Copernicus satellite data collections (e.g., sentinel-2a-l2a).",
        input_schema=ListCollectionsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["copernicus-list-collections"] = handle_list_collections

###################
# Copernicus Product Search (STAC)
###################


class SearchProductsParams(BaseModel):
    """Parameters for searching products via STAC."""

    collection: str = Field(
        ...,
        description="The collection ID to search (e.g., 'sentinel-2a-l2a')",
    )
    bbox: list[float] | None = Field(
        None,
        description="Bounding box [min_lon, min_lat, max_lon, max_lat]",
    )
    datetime: str | None = Field(
        None,
        description="Time range (e.g., '2023-01-01T00:00:00Z/2023-01-02T23:59:59Z')",
    )
    limit: int = Field(default=10, description="Maximum number of results (max 100)")


def search_copernicus_products(params: SearchProductsParams) -> dict:
    """Search for products using STAC Search endpoint."""
    endpoint = f"{STAC_BASE_URL}/search"
    query_params = {"collections": [params.collection], "limit": min(params.limit, 100)}
    if params.bbox:
        query_params["bbox"] = params.bbox
    if params.datetime:
        query_params["datetime"] = params.datetime

    response = http_post(
        endpoint,
        json=query_params,
        timeout=15.0,
        provider=PROVIDER_ID,
    )
    return response.json()


def _copernicus_search_results_to_shape_payload(data: Any) -> dict:
    """Adapt the Copernicus STAC search response to the geofeatures
    payload contract.

    STAC features carry a ``bbox`` ``[min_lon, min_lat, max_lon, max_lat]``
    that we collapse to a centroid point. The bundle is built for
    marker clustering, so a single point per product is more useful
    than the full footprint polygon.

    Features without usable bbox values are dropped silently.
    """
    features: list[dict] = []
    if not isinstance(data, dict):
        return {"features": features}
    stac_features = data.get("features")
    if not isinstance(stac_features, list):
        return {"features": features}
    for feat in stac_features:
        if not isinstance(feat, dict):
            continue
        bbox = feat.get("bbox")
        if not isinstance(bbox, list) or len(bbox) < 4:
            continue
        try:
            min_lon = float(bbox[0])
            min_lat = float(bbox[1])
            max_lon = float(bbox[2])
            max_lat = float(bbox[3])
        except (TypeError, ValueError):
            continue
        lat = (min_lat + max_lat) / 2.0
        lon = (min_lon + max_lon) / 2.0
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            continue
        props = (
            feat.get("properties", {})
            if isinstance(feat.get("properties"), dict)
            else {}
        )
        attrs: dict[str, Any] = {
            "id": feat.get("id"),
            "bbox": bbox,
            "datetime": props.get("datetime"),
            "cloud_cover": props.get("eo:cloud_cover"),
        }
        features.append({"lat": lat, "lon": lon, "attrs": attrs})
    return {"features": features}


async def handle_search_products(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the copernicus-search-products tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format. STAC features are collapsed to bbox centroids so
    each satellite product appears as a single map marker.
    """
    try:
        params = SearchProductsParams(**(arguments or {}))
        data = search_copernicus_products(params)
        payload = _copernicus_search_results_to_shape_payload(data)
        return [
            types.TextContent(type="text", text=to_json_text(payload, max_chars=10000)),
        ]
    except Exception as e:
        log.error(f"Error searching Copernicus products: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="copernicus-search-products",
        description="Search for satellite imagery products based on spatial and temporal criteria.",
        input_schema=SearchProductsParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["copernicus-search-products"] = handle_search_products

###################
# Copernicus Product Metadata (OData)
###################


class ProductMetadataParams(BaseModel):
    """Parameters for getting detailed product metadata."""

    product_id: str = Field(..., description="The unique Product ID (UUID)")


def fetch_product_metadata(params: ProductMetadataParams) -> dict:
    """Fetch detailed metadata for a specific product ID via OData."""
    endpoint = f"{ODATA_BASE_URL}/Products({params.product_id})"
    response = http_get(endpoint, timeout=10.0, provider=PROVIDER_ID)
    return response.json()


async def handle_get_product_metadata(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the copernicus-get-product-metadata tool call."""
    try:
        params = ProductMetadataParams(**(arguments or {}))
        data = fetch_product_metadata(params)
        return [
            types.TextContent(type="text", text=to_json_text(data, max_chars=15000)),
        ]
    except Exception as e:
        log.error(f"Error fetching Copernicus product metadata: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="copernicus-get-product-metadata",
        description="Get detailed technical metadata for a specific Copernicus product ID via OData.",
        input_schema=ProductMetadataParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["copernicus-get-product-metadata"] = handle_get_product_metadata


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "eu-copernicus",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
