"""US CDC Open Data Provider (Socrata)

This module provides interfaces to the US Centers for Disease Control and
Prevention open data catalog hosted on the Socrata Open Data API (SODA) at
data.cdc.gov.

License: Most CDC datasets are in the public domain. Individual dataset
licenses may apply; consult the dataset metadata for details.

Environment variables:
- None required for basic access. Authenticated/elevated rate limits may
  use a Socrata app token; this provider currently uses anonymous access.

Features:
- Catalog search across CDC datasets
- Dataset metadata retrieval by 4x4 dataset id
- Row-level SoQL queries against individual datasets
- Row-count helper using SoQL aggregate
- Bulk metadata view (`/api/views/metadata/v1`)

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
PROVIDER_ID = "us-cdc-socrata"
BASE_URL = "https://data.cdc.gov"

# Records-shape adapter constants
_MAX_DESC_CHARS = 500

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Search Datasets
###################


class CDCSearchDatasetsParams(BaseModel):
    """Parameters for searching the CDC Socrata catalog."""

    q: str | None = Field(None, description="Free-text search query")
    limit: int = Field(default=20, description="Number of results per page")
    page: int = Field(default=1, description="Page number (1-indexed)")


def fetch_search_datasets(params: CDCSearchDatasetsParams) -> Any:
    """Search the CDC Socrata catalog."""
    query_params: dict[str, Any] = {"limit": params.limit, "page": params.page}
    if params.q:
        query_params["q"] = params.q

    response = http_get(
        f"{BASE_URL}/api/views",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _socrata_views_to_shape_payload(data: Any) -> dict:
    """Adapt a Socrata ``/api/views`` response to the records shape
    primitive's payload (identical to the city Socrata adapters).
    """
    raw_rows = data if isinstance(data, list) else []
    rows: list[dict[str, Any]] = []
    for view in raw_rows:
        if not isinstance(view, dict):
            continue
        owner = view.get("owner") or {}
        owner_name = (
            (owner.get("displayName") or owner.get("screenName"))
            if isinstance(owner, dict)
            else None
        )
        tags = view.get("tags") or []
        tag_csv = (
            ", ".join(t for t in tags if isinstance(t, str))
            if isinstance(tags, list)
            else ""
        )
        desc = view.get("description") or ""
        if isinstance(desc, str) and len(desc) > _MAX_DESC_CHARS:
            desc = desc[:_MAX_DESC_CHARS].rstrip() + "…"
        rows.append(
            {
                "id": view.get("id"),
                "name": view.get("name"),
                "category": view.get("category"),
                "attribution": view.get("attribution"),
                "owner": owner_name,
                "viewType": view.get("viewType"),
                "tags": tag_csv,
                "rowsUpdatedAt": view.get("rowsUpdatedAt"),
                "createdAt": view.get("createdAt"),
                "description": desc,
            },
        )
    return {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "id", "type": "string", "description": "Socrata dataset id"},
                {"name": "name", "type": "string", "description": "Dataset name"},
                {
                    "name": "category",
                    "type": "string",
                    "description": "Catalog category",
                },
                {
                    "name": "attribution",
                    "type": "string",
                    "description": "Attribution",
                },
                {"name": "owner", "type": "string", "description": "Owner name"},
                {
                    "name": "viewType",
                    "type": "string",
                    "description": "Socrata view type",
                },
                {"name": "tags", "type": "string", "description": "Tags (csv)"},
                {
                    "name": "rowsUpdatedAt",
                    "type": "number",
                    "description": "Last data update (epoch)",
                },
                {
                    "name": "createdAt",
                    "type": "number",
                    "description": "Created at (epoch)",
                },
                {
                    "name": "description",
                    "type": "string",
                    "description": "Description (truncated)",
                },
            ],
        },
        "default_facets": ["category", "viewType", "attribution"],
    }


async def handle_search_datasets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cdc-search-datasets tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = CDCSearchDatasetsParams(**(arguments or {}))
        data = fetch_search_datasets(params)
        payload = _socrata_views_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching CDC datasets: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cdc-search-datasets",
        description="Search the CDC Socrata data catalog for datasets.",
        input_schema=CDCSearchDatasetsParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["cdc-search-datasets"] = handle_search_datasets


###################
# Get Dataset Metadata
###################


class CDCGetDatasetMetadataParams(BaseModel):
    """Parameters for fetching a CDC dataset's metadata."""

    dataset_id: str = Field(
        ...,
        description="The 4x4 Socrata dataset id (e.g. '9bhg-hcku')",
    )


def fetch_get_dataset_metadata(params: CDCGetDatasetMetadataParams) -> Any:
    """Fetch metadata for a CDC dataset."""
    response = http_get(
        f"{BASE_URL}/api/views/{params.dataset_id}",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_dataset_metadata(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cdc-get-dataset-metadata tool call."""
    try:
        if not arguments or "dataset_id" not in arguments:
            raise ValueError("dataset_id is required")
        params = CDCGetDatasetMetadataParams(**arguments)
        data = fetch_get_dataset_metadata(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CDC dataset metadata: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cdc-get-dataset-metadata",
        description="Fetch metadata for a specific CDC Socrata dataset by 4x4 id.",
        input_schema=CDCGetDatasetMetadataParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cdc-get-dataset-metadata"] = handle_get_dataset_metadata


###################
# Query Dataset
###################


class CDCQueryDatasetParams(BaseModel):
    """Parameters for SoQL queries against a CDC dataset."""

    dataset_id: str = Field(
        ...,
        description="The 4x4 Socrata dataset id (e.g. '9bhg-hcku')",
    )
    limit: int = Field(default=100, description="Maximum rows to return ($limit)")
    offset: int = Field(default=0, description="Row offset for pagination ($offset)")
    where: str | None = Field(None, description="SoQL $where filter expression")


def fetch_query_dataset(params: CDCQueryDatasetParams) -> Any:
    """Run a SoQL query against a CDC dataset."""
    query_params: dict[str, Any] = {
        "$limit": params.limit,
        "$offset": params.offset,
    }
    if params.where:
        query_params["$where"] = params.where

    response = http_get(
        f"{BASE_URL}/resource/{params.dataset_id}.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_query_dataset(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cdc-query-dataset tool call."""
    try:
        if not arguments or "dataset_id" not in arguments:
            raise ValueError("dataset_id is required")
        params = CDCQueryDatasetParams(**arguments)
        data = fetch_query_dataset(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error querying CDC dataset: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cdc-query-dataset",
        description="Query a CDC dataset using Socrata SoQL ($where/$limit/$offset).",
        input_schema=CDCQueryDatasetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cdc-query-dataset"] = handle_query_dataset


###################
# Count Dataset Rows
###################


class CDCCountDatasetRowsParams(BaseModel):
    """Parameters for counting rows in a CDC dataset."""

    dataset_id: str = Field(
        ...,
        description="The 4x4 Socrata dataset id (e.g. '9bhg-hcku')",
    )


def fetch_count_dataset_rows(params: CDCCountDatasetRowsParams) -> Any:
    """Count the rows in a CDC dataset via SoQL aggregate."""
    query_params = {"$select": "count(*)"}
    response = http_get(
        f"{BASE_URL}/resource/{params.dataset_id}.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_count_dataset_rows(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cdc-count-dataset-rows tool call."""
    try:
        if not arguments or "dataset_id" not in arguments:
            raise ValueError("dataset_id is required")
        params = CDCCountDatasetRowsParams(**arguments)
        data = fetch_count_dataset_rows(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error counting CDC dataset rows: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cdc-count-dataset-rows",
        description="Count the rows in a CDC dataset via SoQL count(*) aggregate.",
        input_schema=CDCCountDatasetRowsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cdc-count-dataset-rows"] = handle_count_dataset_rows


###################
# Get Metadata V1
###################


class CDCGetMetadataV1Params(BaseModel):
    """Parameters for the v1 metadata catalog view."""

    limit: int = Field(default=20, description="Number of metadata records to return")


def fetch_get_metadata_v1(params: CDCGetMetadataV1Params) -> Any:
    """Fetch CDC catalog metadata via the v1 metadata API."""
    query_params = {"limit": params.limit}
    response = http_get(
        f"{BASE_URL}/api/views/metadata/v1",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_metadata_v1(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cdc-get-metadata-v1 tool call."""
    try:
        params = CDCGetMetadataV1Params(**(arguments or {}))
        data = fetch_get_metadata_v1(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CDC v1 metadata: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cdc-get-metadata-v1",
        description="Fetch CDC catalog metadata via the Socrata v1 metadata view.",
        input_schema=CDCGetMetadataV1Params.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cdc-get-metadata-v1"] = handle_get_metadata_v1


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-cdc-socrata",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
