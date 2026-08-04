"""CERN Open Data Portal Provider

This module provides interfaces to the CERN Open Data Portal API, which
publishes particle physics datasets, software, and documentation from LHC
experiments (CMS, ATLAS, LHCb, ALICE, OPERA).

License: Datasets are published under CC0 1.0 and software under various
open-source licenses. See https://opendata.cern.ch/about/terms-of-use for
full terms.

Environment variables:
- None required. All endpoints are public.

Features:
- Record search with full-text query, sort, and pagination
- Full record metadata retrieval by record id
- Dataset collection listing
- Experiment-scoped search (CMS, ATLAS, LHCb, ALICE)
- Software record search

Usage:
    The module can be run directly to start a server handling API requests.
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
PROVIDER_ID = "cern-opendata"
BASE_URL = "https://opendata.cern.ch/api"

# Records-shape adapter constants
_MAX_ABSTRACT_CHARS = 500

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Search Records
###################


class CERNSearchRecordsParams(BaseModel):
    """Parameters for searching CERN Open Data records."""

    q: str | None = Field(None, description="Full-text search query string")
    size: int = Field(default=20, description="Number of results per page")
    page: int = Field(default=1, description="Page number (1-indexed)")


def fetch_search_records(params: CERNSearchRecordsParams) -> dict:
    """Search the CERN Open Data record index."""
    query_params: dict[str, Any] = {
        "size": params.size,
        "page": params.page,
        "sort": "mostrecent",
    }
    if params.q:
        query_params["q"] = params.q

    response = http_get(
        f"{BASE_URL}/records/",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _cern_search_to_shape_payload(data: dict) -> dict:
    """Adapt the CERN Invenio search response to the
    ``ui://meta-data-mcp/shape/records/v1`` payload.

    Invenio wraps results in ``{hits: {hits: [{id, metadata: {...}}]}}``;
    we flatten the most-useful metadata fields (title, experiment, type,
    collections, accelerator, collision energy, year, publication date)
    to top-level columns.
    """
    hits_outer = data.get("hits") if isinstance(data, dict) else None
    raw_rows = hits_outer.get("hits", []) if isinstance(hits_outer, dict) else []
    rows: list[dict[str, Any]] = []
    for hit in raw_rows:
        if not isinstance(hit, dict):
            continue
        meta = hit.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        collections = meta.get("collections") or []
        if isinstance(collections, list):
            collection_csv = ", ".join(c for c in collections if isinstance(c, str))
        else:
            collection_csv = ""
        experiment = meta.get("experiment") or ""
        if isinstance(experiment, list):
            experiment = ", ".join(e for e in experiment if isinstance(e, str))
        type_obj = meta.get("type") or {}
        record_type = (
            type_obj.get("primary") if isinstance(type_obj, dict) else type_obj
        )
        abstract = meta.get("abstract")
        if isinstance(abstract, dict):
            abstract_text = abstract.get("description") or ""
        else:
            abstract_text = abstract or ""
        if isinstance(abstract_text, str) and len(abstract_text) > _MAX_ABSTRACT_CHARS:
            abstract_text = abstract_text[:_MAX_ABSTRACT_CHARS].rstrip() + "…"
        rows.append(
            {
                "recid": hit.get("id") or meta.get("recid"),
                "title": meta.get("title"),
                "experiment": experiment,
                "type": record_type,
                "collections": collection_csv,
                "accelerator": meta.get("accelerator"),
                "collision_energy": meta.get("collision_energy"),
                "year": meta.get("date_created")
                if not meta.get("year")
                else meta.get("year"),
                "publication_date": meta.get("publication_date"),
                "abstract": abstract_text,
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "recid", "type": "string", "description": "Record id"},
                {"name": "title", "type": "string", "description": "Record title"},
                {
                    "name": "experiment",
                    "type": "string",
                    "description": "Experiment(s)",
                },
                {"name": "type", "type": "string", "description": "Record type"},
                {
                    "name": "collections",
                    "type": "string",
                    "description": "Collection memberships (csv)",
                },
                {
                    "name": "accelerator",
                    "type": "string",
                    "description": "Accelerator",
                },
                {
                    "name": "collision_energy",
                    "type": "string",
                    "description": "Collision energy",
                },
                {"name": "year", "type": "string", "description": "Data year"},
                {
                    "name": "publication_date",
                    "type": "date",
                    "description": "Publication date",
                },
                {
                    "name": "abstract",
                    "type": "string",
                    "description": "Abstract (truncated)",
                },
            ],
        },
        "default_facets": ["experiment", "type", "accelerator"],
    }
    if isinstance(hits_outer, dict) and "total" in hits_outer:
        payload["total"] = hits_outer["total"]
    return payload


async def handle_search_records(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cern-search-records tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = CERNSearchRecordsParams(**(arguments or {}))
        data = fetch_search_records(params)
        payload = _cern_search_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching CERN Open Data records: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cern-search-records",
        description="Search the CERN Open Data Portal for datasets, software, and documentation.",
        input_schema=CERNSearchRecordsParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["cern-search-records"] = handle_search_records


###################
# Get Record
###################


class CERNGetRecordParams(BaseModel):
    """Parameters for fetching a single CERN Open Data record."""

    record_id: int = Field(..., description="The CERN Open Data numeric record id")


def fetch_get_record(params: CERNGetRecordParams) -> dict:
    """Fetch full metadata for a CERN Open Data record."""
    response = http_get(f"{BASE_URL}/records/{params.record_id}", provider=PROVIDER_ID)
    return response.json()


async def handle_get_record(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cern-get-record tool call."""
    try:
        if not arguments or "record_id" not in arguments:
            raise ValueError("record_id is required")
        params = CERNGetRecordParams(**arguments)
        data = fetch_get_record(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CERN Open Data record: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cern-get-record",
        description="Fetch full metadata for a specific CERN Open Data record by record_id.",
        input_schema=CERNGetRecordParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cern-get-record"] = handle_get_record


###################
# List Collections (Datasets)
###################


class CERNListCollectionsParams(BaseModel):
    """Parameters for listing CERN Open Data dataset collections."""

    size: int = Field(default=50, description="Number of dataset records to return")


def fetch_list_collections(params: CERNListCollectionsParams) -> dict:
    """List CERN Open Data Dataset records."""
    query_params = {"type": "Dataset", "size": params.size}
    response = http_get(
        f"{BASE_URL}/records/",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_list_collections(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cern-list-collections tool call."""
    try:
        params = CERNListCollectionsParams(**(arguments or {}))
        data = fetch_list_collections(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing CERN Open Data collections: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cern-list-collections",
        description="List CERN Open Data dataset collections.",
        input_schema=CERNListCollectionsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cern-list-collections"] = handle_list_collections


###################
# Search By Experiment
###################


class CERNSearchByExperimentParams(BaseModel):
    """Parameters for searching CERN Open Data within a single experiment."""

    experiment: str = Field(
        ...,
        description="Experiment name (e.g. 'CMS', 'ATLAS', 'LHCb', 'ALICE')",
    )
    q: str | None = Field(None, description="Optional full-text search query")
    size: int = Field(default=20, description="Number of results to return")


def fetch_search_by_experiment(params: CERNSearchByExperimentParams) -> dict:
    """Search CERN Open Data records filtered by experiment."""
    query_params: dict[str, Any] = {
        "experiment": params.experiment,
        "size": params.size,
    }
    if params.q:
        query_params["q"] = params.q

    response = http_get(
        f"{BASE_URL}/records/",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_search_by_experiment(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cern-search-by-experiment tool call."""
    try:
        if not arguments or "experiment" not in arguments:
            raise ValueError("experiment is required")
        params = CERNSearchByExperimentParams(**arguments)
        data = fetch_search_by_experiment(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching CERN Open Data by experiment: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cern-search-by-experiment",
        description="Search CERN Open Data records scoped to a specific experiment (CMS, ATLAS, LHCb, ALICE).",
        input_schema=CERNSearchByExperimentParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cern-search-by-experiment"] = handle_search_by_experiment


###################
# Search Software
###################


class CERNSearchSoftwareParams(BaseModel):
    """Parameters for searching CERN Open Data software releases."""

    q: str | None = Field(None, description="Optional full-text search query")
    size: int = Field(default=20, description="Number of results to return")


def fetch_search_software(params: CERNSearchSoftwareParams) -> dict:
    """Search CERN Open Data Software-typed records."""
    query_params: dict[str, Any] = {"type": "Software", "size": params.size}
    if params.q:
        query_params["q"] = params.q

    response = http_get(
        f"{BASE_URL}/records/",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_search_software(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cern-search-software tool call."""
    try:
        params = CERNSearchSoftwareParams(**(arguments or {}))
        data = fetch_search_software(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching CERN Open Data software: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cern-search-software",
        description="Search CERN Open Data software releases (analysis frameworks, VMs).",
        input_schema=CERNSearchSoftwareParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cern-search-software"] = handle_search_software


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "cern-opendata",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
