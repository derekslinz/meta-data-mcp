"""Metropolitan Museum of Art Open Access Provider

This module exposes the Metropolitan Museum of Art Collection API, which
provides programmatic access to more than 470,000 artworks released under
Creative Commons Zero (CC0).

License note:
    The Met Open Access program makes images and metadata for public-domain
    works available under CC0. Some records remain in copyright; consult
    the per-record `isPublicDomain` field before reuse.

Features:
- List object IDs (optionally filtered by department or update date)
- Retrieve full metadata for an individual object
- Free-text and faceted search across the collection
- List curatorial departments
- Artist / culture-targeted search

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.app_museum_v1 import URI as MUSEUM_APP_URI
from meta_data_mcp.utils import MAX_RESPONSE_CHARS, http_get, to_json_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-met-museum"
BASE_URL = "https://collectionapi.metmuseum.org/public/collection/v1"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# List Objects
###################


class MetListObjectsParams(BaseModel):
    """Parameters for listing object IDs in the Met collection."""

    departmentIds: str | None = Field(
        None,
        description="Pipe-separated list of department IDs to filter by (e.g. '1|3').",
    )
    metadataDate: str | None = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Return objects updated on or after this date (YYYY-MM-DD).",
    )


def fetch_met_list_objects(params: MetListObjectsParams) -> dict:
    """Call the Met /objects endpoint."""
    query_params: dict[str, Any] = {}
    if params.departmentIds:
        query_params["departmentIds"] = params.departmentIds
    if params.metadataDate:
        query_params["metadataDate"] = params.metadataDate
    response = http_get(
        f"{BASE_URL}/objects",
        params=query_params or None,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_met_list_objects(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the met-list-objects tool call."""
    try:
        params = MetListObjectsParams(**(arguments or {}))
        data = fetch_met_list_objects(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error listing Met objects: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="met-list-objects",
        description="List Met Museum object IDs, optionally filtered by department or metadata update date.",
        input_schema=MetListObjectsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["met-list-objects"] = handle_met_list_objects


###################
# Get Object
###################


class MetGetObjectParams(BaseModel):
    """Parameters for retrieving a single Met object."""

    objectID: int = Field(..., description="The Met Museum object ID to retrieve.")


def fetch_met_get_object(params: MetGetObjectParams) -> dict:
    """Call the Met /objects/{objectID} endpoint."""
    response = http_get(f"{BASE_URL}/objects/{params.objectID}", provider=PROVIDER_ID)
    return response.json()


async def handle_met_get_object(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the met-get-object tool call."""
    try:
        if not arguments or "objectID" not in arguments:
            raise ValueError("objectID is required")
        params = MetGetObjectParams(**arguments)
        data = fetch_met_get_object(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching Met object: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="met-get-object",
        description="Fetch the full metadata record for a Met Museum object by object ID.",
        input_schema=MetGetObjectParams.model_json_schema(),
        # MCP Apps binding: render via the museum app's detail modal.
        _meta={"ui": {"resourceUri": MUSEUM_APP_URI}},
    ),
)
TOOLS_HANDLERS["met-get-object"] = handle_met_get_object


###################
# Search
###################


class MetSearchParams(BaseModel):
    """Parameters for the Met /search endpoint."""

    q: str = Field(..., description="Free-text search query.")
    hasImages: bool | None = Field(
        None,
        description="If true, restrict to records with images.",
    )
    departmentId: int | None = Field(
        None,
        description="Restrict to a single department ID.",
    )
    medium: str | None = Field(
        None,
        description="Pipe-separated list of medium values (e.g. 'Paintings|Sculpture').",
    )
    geoLocation: str | None = Field(
        None,
        description="Pipe-separated geographic location filter (e.g. 'France|Paris').",
    )
    dateBegin: int | None = Field(
        None,
        description="Earliest year (use negative numbers for BCE).",
    )
    dateEnd: int | None = Field(
        None,
        description="Latest year (use negative numbers for BCE).",
    )


def fetch_met_search(params: MetSearchParams) -> dict:
    """Call the Met /search endpoint."""
    query_params: dict[str, Any] = {"q": params.q}
    if params.hasImages is not None:
        query_params["hasImages"] = "true" if params.hasImages else "false"
    if params.departmentId is not None:
        query_params["departmentId"] = params.departmentId
    if params.medium:
        query_params["medium"] = params.medium
    if params.geoLocation:
        query_params["geoLocation"] = params.geoLocation
    if params.dateBegin is not None:
        query_params["dateBegin"] = params.dateBegin
    if params.dateEnd is not None:
        query_params["dateEnd"] = params.dateEnd
    response = http_get(f"{BASE_URL}/search", params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_met_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the met-search tool call."""
    try:
        if not arguments or "q" not in arguments:
            raise ValueError("q is required")
        params = MetSearchParams(**arguments)
        data = fetch_met_search(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error searching Met collection: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="met-search",
        description="Search the Met Museum collection by free text with optional faceted filters.",
        input_schema=MetSearchParams.model_json_schema(),
        # MCP Apps binding: render via the museum app's image grid.
        _meta={"ui": {"resourceUri": MUSEUM_APP_URI}},
    ),
)
TOOLS_HANDLERS["met-search"] = handle_met_search


###################
# List Departments
###################


class MetListDepartmentsParams(BaseModel):
    """Parameters for listing Met departments (no inputs)."""


def fetch_met_list_departments(params: MetListDepartmentsParams) -> dict:
    """Call the Met /departments endpoint."""
    response = http_get(f"{BASE_URL}/departments", provider=PROVIDER_ID)
    return response.json()


async def handle_met_list_departments(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the met-list-departments tool call."""
    try:
        params = MetListDepartmentsParams(**(arguments or {}))
        data = fetch_met_list_departments(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error listing Met departments: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="met-list-departments",
        description="List the curatorial departments at the Met Museum.",
        input_schema=MetListDepartmentsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["met-list-departments"] = handle_met_list_departments


###################
# Search By Artist
###################


class MetSearchByArtistParams(BaseModel):
    """Parameters for searching by artist or culture."""

    q: str = Field(
        ...,
        description="Artist name or culture to search for (e.g. 'Van Gogh').",
    )


def fetch_met_search_by_artist(params: MetSearchByArtistParams) -> dict:
    """Call the Met /search endpoint with artistOrCulture=true."""
    query_params: dict[str, Any] = {
        "artistOrCulture": "true",
        "q": params.q,
    }
    response = http_get(f"{BASE_URL}/search", params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_met_search_by_artist(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the met-search-by-artist tool call."""
    try:
        if not arguments or "q" not in arguments:
            raise ValueError("q is required")
        params = MetSearchByArtistParams(**arguments)
        data = fetch_met_search_by_artist(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error searching Met collection by artist: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="met-search-by-artist",
        description="Search the Met Museum collection restricted to artist or culture fields.",
        input_schema=MetSearchByArtistParams.model_json_schema(),
        # MCP Apps binding: same response shape as met-search ({total, objectIDs}),
        # so it renders through the museum app's image grid the same way.
        _meta={"ui": {"resourceUri": MUSEUM_APP_URI}},
    ),
)
TOOLS_HANDLERS["met-search-by-artist"] = handle_met_search_by_artist


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-met-museum",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
