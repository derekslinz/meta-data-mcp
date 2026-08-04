"""Global Biodiversity Information Facility (GBIF) Provider

This module exposes the public GBIF v1 REST API, which aggregates species
occurrence records, taxonomic backbone data, and dataset metadata from
contributors worldwide.

License note:
    GBIF metadata is released under the GBIF Terms of Use; underlying
    occurrence records carry licences supplied by the publishing dataset
    (commonly CC0, CC BY, or CC BY-NC). Always honour the licence reported
    on the individual record before redistribution.

Features:
- Occurrence search and detail
- Species search, detail, and name suggestion
- Dataset listing and detail
- Country-level occurrence counts

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
PROVIDER_ID = "global-gbif"
BASE_URL = "https://api.gbif.org/v1"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Search Occurrences
###################


class GBIFSearchOccurrencesParams(BaseModel):
    """Parameters for searching GBIF occurrence records."""

    scientificName: str | None = Field(
        None,
        description="Scientific name to match (e.g. 'Puma concolor').",
    )
    country: str | None = Field(
        None,
        description="ISO 3166-1 alpha-2 country code (e.g. 'US').",
    )
    year: int | None = Field(None, description="Filter by collection year.")
    hasCoordinate: bool | None = Field(
        None,
        description="If true, only records with lat/lon.",
    )
    limit: int = Field(default=20, ge=1, le=300, description="Page size.")
    offset: int = Field(default=0, ge=0, description="Offset into the result set.")


def fetch_gbif_search_occurrences(params: GBIFSearchOccurrencesParams) -> dict:
    """Call /occurrence/search."""
    query_params: dict[str, Any] = {"limit": params.limit, "offset": params.offset}
    if params.scientificName:
        query_params["scientificName"] = params.scientificName
    if params.country:
        query_params["country"] = params.country
    if params.year is not None:
        query_params["year"] = params.year
    if params.hasCoordinate is not None:
        query_params["hasCoordinate"] = str(params.hasCoordinate).lower()
    response = http_get(
        f"{BASE_URL}/occurrence/search",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _gbif_occurrences_to_shape_payload(data: Any) -> dict:
    """Adapt the GBIF ``/occurrence/search`` response to the geofeatures
    payload contract.

    Each occurrence carries ``decimalLatitude`` / ``decimalLongitude``
    when location data is available. Records without usable coordinates
    are dropped silently — Darwin Core records are often coordinate-free
    by design (e.g. herbarium specimens with locality strings only).
    """
    features: list[dict] = []
    if not isinstance(data, dict):
        return {"features": features}
    results = data.get("results")
    if not isinstance(results, list):
        return {"features": features}
    for occ in results:
        if not isinstance(occ, dict):
            continue
        lat_raw = occ.get("decimalLatitude")
        lon_raw = occ.get("decimalLongitude")
        try:
            lat = float(lat_raw) if lat_raw is not None else None
            lon = float(lon_raw) if lon_raw is not None else None
        except (TypeError, ValueError):
            continue
        if lat is None or lon is None:
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            continue
        attrs = {
            k: v
            for k, v in occ.items()
            if k not in ("decimalLatitude", "decimalLongitude")
        }
        features.append({"lat": lat, "lon": lon, "attrs": attrs})
    return {"features": features}


async def handle_gbif_search_occurrences(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gbif-search-occurrences tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format so the MCP Apps host can render the result inline
    via the bound shape primitive.
    """
    try:
        params = GBIFSearchOccurrencesParams(**(arguments or {}))
        data = fetch_gbif_search_occurrences(params)
        payload = _gbif_occurrences_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_geofeatures_text(payload))]
    except Exception as e:
        log.error(f"Error searching GBIF occurrences: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="gbif-search-occurrences",
        description="Search GBIF occurrence records by scientific name, country, year, and coordinate flag.",
        input_schema=GBIFSearchOccurrencesParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["gbif-search-occurrences"] = handle_gbif_search_occurrences


###################
# Get Occurrence
###################


class GBIFGetOccurrenceParams(BaseModel):
    """Parameters for retrieving a single occurrence record."""

    key: int = Field(..., description="GBIF occurrence integer key.")


def fetch_gbif_get_occurrence(params: GBIFGetOccurrenceParams) -> dict:
    """Call /occurrence/{key}."""
    response = http_get(f"{BASE_URL}/occurrence/{params.key}", provider=PROVIDER_ID)
    return response.json()


async def handle_gbif_get_occurrence(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gbif-get-occurrence tool call."""
    try:
        if not arguments or "key" not in arguments:
            raise ValueError("key is required")
        params = GBIFGetOccurrenceParams(**arguments)
        data = fetch_gbif_get_occurrence(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching GBIF occurrence: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="gbif-get-occurrence",
        description="Fetch a single GBIF occurrence record by integer key.",
        input_schema=GBIFGetOccurrenceParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["gbif-get-occurrence"] = handle_gbif_get_occurrence


###################
# Search Species
###################


class GBIFSearchSpeciesParams(BaseModel):
    """Parameters for searching the GBIF species backbone."""

    q: str = Field(..., description="Free-text query (e.g. 'Quercus').")
    rank: str | None = Field(
        None,
        description="Restrict to a taxonomic rank (kingdom, phylum, class, order, family, genus, species).",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Page size.")


def fetch_gbif_search_species(params: GBIFSearchSpeciesParams) -> dict:
    """Call /species/search."""
    query_params: dict[str, Any] = {"q": params.q, "limit": params.limit}
    if params.rank:
        query_params["rank"] = params.rank
    response = http_get(
        f"{BASE_URL}/species/search",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_gbif_search_species(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gbif-search-species tool call."""
    try:
        if not arguments or "q" not in arguments:
            raise ValueError("q is required")
        params = GBIFSearchSpeciesParams(**arguments)
        data = fetch_gbif_search_species(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching GBIF species: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="gbif-search-species",
        description="Search the GBIF species backbone by free-text query and optional rank.",
        input_schema=GBIFSearchSpeciesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["gbif-search-species"] = handle_gbif_search_species


###################
# Get Species
###################


class GBIFGetSpeciesParams(BaseModel):
    """Parameters for retrieving a single species record."""

    key: int = Field(..., description="GBIF taxon key.")


def fetch_gbif_get_species(params: GBIFGetSpeciesParams) -> dict:
    """Call /species/{key}."""
    response = http_get(f"{BASE_URL}/species/{params.key}", provider=PROVIDER_ID)
    return response.json()


async def handle_gbif_get_species(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gbif-get-species tool call."""
    try:
        if not arguments or "key" not in arguments:
            raise ValueError("key is required")
        params = GBIFGetSpeciesParams(**arguments)
        data = fetch_gbif_get_species(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching GBIF species: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="gbif-get-species",
        description="Fetch a single GBIF species/taxon record by taxon key.",
        input_schema=GBIFGetSpeciesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["gbif-get-species"] = handle_gbif_get_species


###################
# Species Name Suggest
###################


class GBIFSpeciesSuggestParams(BaseModel):
    """Parameters for the species name autocomplete endpoint."""

    q: str = Field(..., description="Partial scientific name to autocomplete.")
    limit: int = Field(default=10, ge=1, le=100, description="Maximum suggestions.")


def fetch_gbif_species_suggest(params: GBIFSpeciesSuggestParams) -> Any:
    """Call /species/suggest."""
    query_params: dict[str, Any] = {"q": params.q, "limit": params.limit}
    response = http_get(
        f"{BASE_URL}/species/suggest",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_gbif_get_species_name_suggest(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gbif-get-species-name-suggest tool call."""
    try:
        if not arguments or "q" not in arguments:
            raise ValueError("q is required")
        params = GBIFSpeciesSuggestParams(**arguments)
        data = fetch_gbif_species_suggest(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error suggesting GBIF species names: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="gbif-get-species-name-suggest",
        description="Autocomplete scientific names against the GBIF species backbone.",
        input_schema=GBIFSpeciesSuggestParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["gbif-get-species-name-suggest"] = handle_gbif_get_species_name_suggest


###################
# List Datasets
###################


class GBIFListDatasetsParams(BaseModel):
    """Parameters for listing GBIF datasets."""

    type: str | None = Field(
        None,
        description="Dataset type (OCCURRENCE, CHECKLIST, METADATA, SAMPLING_EVENT).",
    )
    country: str | None = Field(
        None,
        description="ISO 3166-1 alpha-2 country code filter.",
    )
    limit: int = Field(default=20, ge=1, le=1000, description="Page size.")
    offset: int = Field(default=0, ge=0, description="Offset into the result set.")


def fetch_gbif_list_datasets(params: GBIFListDatasetsParams) -> dict:
    """Call /dataset."""
    query_params: dict[str, Any] = {"limit": params.limit, "offset": params.offset}
    if params.type:
        query_params["type"] = params.type
    if params.country:
        query_params["country"] = params.country
    response = http_get(
        f"{BASE_URL}/dataset",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_gbif_list_datasets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gbif-list-datasets tool call."""
    try:
        params = GBIFListDatasetsParams(**(arguments or {}))
        data = fetch_gbif_list_datasets(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing GBIF datasets: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="gbif-list-datasets",
        description="List GBIF datasets with optional type and country filters.",
        input_schema=GBIFListDatasetsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["gbif-list-datasets"] = handle_gbif_list_datasets


###################
# Get Dataset
###################


class GBIFGetDatasetParams(BaseModel):
    """Parameters for fetching a single dataset record."""

    key: str = Field(..., description="GBIF dataset UUID key.")


def fetch_gbif_get_dataset(params: GBIFGetDatasetParams) -> dict:
    """Call /dataset/{key}."""
    response = http_get(f"{BASE_URL}/dataset/{params.key}", provider=PROVIDER_ID)
    return response.json()


async def handle_gbif_get_dataset(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gbif-get-dataset tool call."""
    try:
        if not arguments or "key" not in arguments:
            raise ValueError("key is required")
        params = GBIFGetDatasetParams(**arguments)
        data = fetch_gbif_get_dataset(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching GBIF dataset: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="gbif-get-dataset",
        description="Fetch a single GBIF dataset record by UUID key.",
        input_schema=GBIFGetDatasetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["gbif-get-dataset"] = handle_gbif_get_dataset


###################
# Occurrence Counts
###################


class GBIFOccurrenceCountsParams(BaseModel):
    """Parameters for the country occurrence-counts endpoint."""

    country: str | None = Field(
        None,
        description="ISO 3166-1 alpha-2 country code filter.",
    )


def fetch_gbif_occurrence_counts(params: GBIFOccurrenceCountsParams) -> Any:
    """Call /occurrence/counts."""
    query_params: dict[str, Any] = {}
    if params.country:
        query_params["country"] = params.country
    response = http_get(
        f"{BASE_URL}/occurrence/counts",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_gbif_get_occurrence_counts(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gbif-get-occurrence-counts tool call."""
    try:
        params = GBIFOccurrenceCountsParams(**(arguments or {}))
        data = fetch_gbif_occurrence_counts(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching GBIF occurrence counts: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="gbif-get-occurrence-counts",
        description="Get aggregate GBIF occurrence counts, optionally filtered by country.",
        input_schema=GBIFOccurrenceCountsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["gbif-get-occurrence-counts"] = handle_gbif_get_occurrence_counts


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-gbif",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
