"""iNaturalist Provider

This module exposes the public iNaturalist v1 REST API, a citizen-science
platform that records biodiversity observations submitted (and identified)
by a global community.

License note:
    iNaturalist data licences vary per record. Observations are commonly
    CC-BY-NC or all-rights-reserved; consult the per-record licence and the
    iNaturalist Terms of Use before redistribution. The API enforces polite
    rate limits and asks consumers to send an identifying User-Agent (the
    shared `http_get` helper supplies one automatically).

Features:
- Observation search and detail
- Taxon search and detail
- Place autocomplete and detail
- Project listing
- User profile lookup

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_records_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-inaturalist"
BASE_URL = "https://api.inaturalist.org/v1"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Search Observations
###################


class INaturalistSearchObservationsParams(BaseModel):
    """Parameters for searching iNaturalist observations."""

    q: str | None = Field(None, description="Free-text query.")
    taxon_name: str | None = Field(
        None,
        description="Filter by scientific/common taxon name.",
    )
    place_id: int | None = Field(None, description="Filter by iNaturalist place id.")
    user_id: str | None = Field(
        None,
        description="Filter by iNaturalist user login or numeric id.",
    )
    d1: str | None = Field(
        None,
        description="Observed on or after this date (YYYY-MM-DD).",
    )
    d2: str | None = Field(
        None,
        description="Observed on or before this date (YYYY-MM-DD).",
    )
    per_page: int = Field(default=30, ge=1, le=200, description="Page size.")
    page: int = Field(default=1, ge=1, description="Page number (1-indexed).")


def fetch_inaturalist_search_observations(
    params: INaturalistSearchObservationsParams,
) -> dict:
    """Call /observations."""
    query_params: dict[str, Any] = {
        "per_page": params.per_page,
        "page": params.page,
    }
    for key in ("q", "taxon_name", "place_id", "user_id", "d1", "d2"):
        value = getattr(params, key)
        if value is not None:
            query_params[key] = value
    response = http_get(
        f"{BASE_URL}/observations",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _inaturalist_observations_to_shape_payload(data: dict) -> dict:
    """Adapt iNaturalist ``/observations`` response to the records shape
    primitive's payload.

    iNaturalist returns ``{total_results, page, per_page, results: [...]}``;
    each observation nests taxon and user info. We hoist scientific name,
    common name, taxon rank, place_guess, user login, observed_on, and
    quality_grade.
    """
    raw_rows = data.get("results", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for obs in raw_rows:
        if not isinstance(obs, dict):
            continue
        taxon = obs.get("taxon") or {}
        if not isinstance(taxon, dict):
            taxon = {}
        user = obs.get("user") or {}
        rows.append(
            {
                "id": obs.get("id"),
                "scientific_name": taxon.get("name") or obs.get("species_guess"),
                "common_name": taxon.get("preferred_common_name")
                or obs.get("species_guess"),
                "rank": taxon.get("rank"),
                "iconic_taxon": taxon.get("iconic_taxon_name"),
                "place_guess": obs.get("place_guess"),
                "observed_on": obs.get("observed_on"),
                "user": user.get("login") if isinstance(user, dict) else None,
                "quality_grade": obs.get("quality_grade"),
                "license_code": obs.get("license_code"),
                "uri": obs.get("uri"),
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "id", "type": "number", "description": "Observation id"},
                {
                    "name": "scientific_name",
                    "type": "string",
                    "description": "Scientific name",
                },
                {
                    "name": "common_name",
                    "type": "string",
                    "description": "Preferred common name",
                },
                {"name": "rank", "type": "string", "description": "Taxonomic rank"},
                {
                    "name": "iconic_taxon",
                    "type": "string",
                    "description": "Iconic taxon (high-level group)",
                },
                {
                    "name": "place_guess",
                    "type": "string",
                    "description": "Place name",
                },
                {
                    "name": "observed_on",
                    "type": "date",
                    "description": "Observation date",
                },
                {"name": "user", "type": "string", "description": "Observer login"},
                {
                    "name": "quality_grade",
                    "type": "string",
                    "description": "Quality grade",
                },
                {
                    "name": "license_code",
                    "type": "string",
                    "description": "Observation licence",
                },
                {"name": "uri", "type": "string", "description": "Observation URL"},
            ],
        },
        "default_facets": ["iconic_taxon", "quality_grade", "rank"],
    }
    if isinstance(data, dict):
        for key in ("total_results", "page", "per_page"):
            if key in data:
                payload[key] = data[key]
    return payload


async def handle_inaturalist_search_observations(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the inaturalist-search-observations tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = INaturalistSearchObservationsParams(**(arguments or {}))
        data = fetch_inaturalist_search_observations(params)
        payload = _inaturalist_observations_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching iNaturalist observations: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="inaturalist-search-observations",
        description="Search iNaturalist observations by query, taxon, place, user, and date range.",
        input_schema=INaturalistSearchObservationsParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["inaturalist-search-observations"] = (
    handle_inaturalist_search_observations
)


###################
# Get Observation
###################


class INaturalistGetObservationParams(BaseModel):
    """Parameters for retrieving a single observation."""

    id: int = Field(..., description="iNaturalist observation id.")


def fetch_inaturalist_get_observation(
    params: INaturalistGetObservationParams,
) -> dict:
    """Call /observations/{id}."""
    response = http_get(f"{BASE_URL}/observations/{params.id}", provider=PROVIDER_ID)
    return response.json()


async def handle_inaturalist_get_observation(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the inaturalist-get-observation tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = INaturalistGetObservationParams(**arguments)
        data = fetch_inaturalist_get_observation(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching iNaturalist observation: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="inaturalist-get-observation",
        description="Fetch a single iNaturalist observation by id.",
        input_schema=INaturalistGetObservationParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["inaturalist-get-observation"] = handle_inaturalist_get_observation


###################
# Search Taxa
###################


class INaturalistSearchTaxaParams(BaseModel):
    """Parameters for searching iNaturalist taxa."""

    q: str = Field(..., description="Free-text query against taxon names.")
    rank: str | None = Field(
        None,
        description="Restrict to taxon rank (e.g. species, genus, family).",
    )
    per_page: int = Field(default=30, ge=1, le=200, description="Page size.")


def fetch_inaturalist_search_taxa(params: INaturalistSearchTaxaParams) -> dict:
    """Call /taxa."""
    query_params: dict[str, Any] = {"q": params.q, "per_page": params.per_page}
    if params.rank:
        query_params["rank"] = params.rank
    response = http_get(f"{BASE_URL}/taxa", params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_inaturalist_search_taxa(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the inaturalist-search-taxa tool call."""
    try:
        if not arguments or "q" not in arguments:
            raise ValueError("q is required")
        params = INaturalistSearchTaxaParams(**arguments)
        data = fetch_inaturalist_search_taxa(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching iNaturalist taxa: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="inaturalist-search-taxa",
        description="Search the iNaturalist taxonomic database by query and optional rank.",
        input_schema=INaturalistSearchTaxaParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["inaturalist-search-taxa"] = handle_inaturalist_search_taxa


###################
# Get Taxon
###################


class INaturalistGetTaxonParams(BaseModel):
    """Parameters for retrieving a single taxon."""

    id: int = Field(..., description="iNaturalist taxon id.")


def fetch_inaturalist_get_taxon(params: INaturalistGetTaxonParams) -> dict:
    """Call /taxa/{id}."""
    response = http_get(f"{BASE_URL}/taxa/{params.id}", provider=PROVIDER_ID)
    return response.json()


async def handle_inaturalist_get_taxon(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the inaturalist-get-taxon tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = INaturalistGetTaxonParams(**arguments)
        data = fetch_inaturalist_get_taxon(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching iNaturalist taxon: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="inaturalist-get-taxon",
        description="Fetch a single iNaturalist taxon by id.",
        input_schema=INaturalistGetTaxonParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["inaturalist-get-taxon"] = handle_inaturalist_get_taxon


###################
# Places Autocomplete
###################


class INaturalistListPlacesParams(BaseModel):
    """Parameters for the place autocomplete endpoint."""

    q: str = Field(..., description="Partial place name to autocomplete.")
    per_page: int = Field(default=20, ge=1, le=100, description="Page size.")


def fetch_inaturalist_list_places(params: INaturalistListPlacesParams) -> dict:
    """Call /places/autocomplete."""
    query_params: dict[str, Any] = {"q": params.q, "per_page": params.per_page}
    response = http_get(
        f"{BASE_URL}/places/autocomplete",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_inaturalist_list_places(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the inaturalist-list-places tool call."""
    try:
        if not arguments or "q" not in arguments:
            raise ValueError("q is required")
        params = INaturalistListPlacesParams(**arguments)
        data = fetch_inaturalist_list_places(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing iNaturalist places: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="inaturalist-list-places",
        description="Autocomplete iNaturalist places by partial name.",
        input_schema=INaturalistListPlacesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["inaturalist-list-places"] = handle_inaturalist_list_places


###################
# Get Place
###################


class INaturalistGetPlaceParams(BaseModel):
    """Parameters for retrieving a single place."""

    id: int = Field(..., description="iNaturalist place id.")


def fetch_inaturalist_get_place(params: INaturalistGetPlaceParams) -> dict:
    """Call /places/{id}."""
    response = http_get(f"{BASE_URL}/places/{params.id}", provider=PROVIDER_ID)
    return response.json()


async def handle_inaturalist_get_place(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the inaturalist-get-place tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = INaturalistGetPlaceParams(**arguments)
        data = fetch_inaturalist_get_place(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching iNaturalist place: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="inaturalist-get-place",
        description="Fetch a single iNaturalist place by id.",
        input_schema=INaturalistGetPlaceParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["inaturalist-get-place"] = handle_inaturalist_get_place


###################
# List Projects
###################


class INaturalistListProjectsParams(BaseModel):
    """Parameters for listing iNaturalist projects."""

    q: str | None = Field(None, description="Free-text query against project name.")
    per_page: int = Field(default=20, ge=1, le=200, description="Page size.")


def fetch_inaturalist_list_projects(params: INaturalistListProjectsParams) -> dict:
    """Call /projects."""
    query_params: dict[str, Any] = {"per_page": params.per_page}
    if params.q:
        query_params["q"] = params.q
    response = http_get(
        f"{BASE_URL}/projects",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_inaturalist_list_projects(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the inaturalist-list-projects tool call."""
    try:
        params = INaturalistListProjectsParams(**(arguments or {}))
        data = fetch_inaturalist_list_projects(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing iNaturalist projects: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="inaturalist-list-projects",
        description="List or search iNaturalist projects.",
        input_schema=INaturalistListProjectsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["inaturalist-list-projects"] = handle_inaturalist_list_projects


###################
# Get User
###################


class INaturalistGetUserParams(BaseModel):
    """Parameters for retrieving an iNaturalist user."""

    id: str = Field(..., description="iNaturalist user login or numeric id.")


def fetch_inaturalist_get_user(params: INaturalistGetUserParams) -> dict:
    """Call /users/{id}."""
    response = http_get(f"{BASE_URL}/users/{params.id}", provider=PROVIDER_ID)
    return response.json()


async def handle_inaturalist_get_user(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the inaturalist-get-user tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = INaturalistGetUserParams(**arguments)
        data = fetch_inaturalist_get_user(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching iNaturalist user: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="inaturalist-get-user",
        description="Fetch an iNaturalist user profile by login or numeric id.",
        input_schema=INaturalistGetUserParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["inaturalist-get-user"] = handle_inaturalist_get_user


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-inaturalist",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
