"""US Data.gov Catalog API Client

This module provides interfaces to access the US Federal Government's open data catalog
via the current catalog.data.gov API.

Features:
- Dataset discovery through the catalog search endpoint
- Detailed dataset metadata retrieval from exact search matches

Usage:
    The module can be run directly to start a server handling API requests,
    or its components can be imported and used individually.
"""

from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field, ValidationError

from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, to_json_text, to_records_text

PROVIDER_ID = "us-data-gov"

# Records-shape adapter constants
_MAX_DESC_CHARS = 500

# Constants
BASE_URL = "https://catalog.data.gov"
SEARCH_URL = f"{BASE_URL}/search"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# Data.gov Dataset Discovery
###################


class DataGovListDatasetsParams(BaseModel):
    """Parameters for searching or listing US Data.gov datasets."""

    search: str | None = Field(
        None,
        description="Search term for dataset titles or descriptions",
    )
    rows: int = Field(default=20, description="Number of results to return")
    after: str | None = Field(
        None,
        description="Pagination cursor returned by a previous Data.gov search",
    )
    org_slug: str | None = Field(
        None,
        description="Optional organization slug filter, e.g. 'nasa'",
    )


def list_datagov_datasets(params: DataGovListDatasetsParams) -> dict:
    """Search for available US Data.gov datasets using the catalog API."""
    query_params = {
        "q": params.search or "",
        "per_page": params.rows,
    }
    if params.after:
        query_params["after"] = params.after
    if params.org_slug:
        query_params["org_slug"] = params.org_slug

    response = http_get(
        SEARCH_URL,
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


def _datagov_search_to_shape_payload(result: dict) -> dict:
    """Adapt a catalog.data.gov ``/search`` response to the
    ``ui://meta-data-mcp/shape/records/v1`` payload.

    The catalog returns ``{results: [...], after: <cursor>}``; we flatten
    each package to identifier, slug, title, publisher, organization, and a
    truncated description, then expose the cursor as ``after`` so paginated
    callers can chain pages.
    """
    raw_rows = result.get("results", []) if isinstance(result, dict) else []
    rows: list[dict[str, Any]] = []
    for pkg in raw_rows:
        if not isinstance(pkg, dict):
            continue
        org = pkg.get("organization") or {}
        org_name = (
            (org.get("name") or org.get("title")) if isinstance(org, dict) else None
        )
        desc = pkg.get("description") or ""
        if isinstance(desc, str) and len(desc) > _MAX_DESC_CHARS:
            desc = desc[:_MAX_DESC_CHARS].rstrip() + "…"
        rows.append(
            {
                "identifier": pkg.get("identifier"),
                "slug": pkg.get("slug"),
                "title": pkg.get("title"),
                "publisher": pkg.get("publisher"),
                "organization": org_name,
                "description": desc,
                "harvest_record": pkg.get("harvest_record"),
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {
                    "name": "identifier",
                    "type": "string",
                    "description": "Dataset identifier",
                },
                {"name": "slug", "type": "string", "description": "URL slug"},
                {"name": "title", "type": "string", "description": "Dataset title"},
                {
                    "name": "publisher",
                    "type": "string",
                    "description": "Publisher name",
                },
                {
                    "name": "organization",
                    "type": "string",
                    "description": "Publishing organisation",
                },
                {
                    "name": "description",
                    "type": "string",
                    "description": "Description (truncated)",
                },
                {
                    "name": "harvest_record",
                    "type": "string",
                    "description": "Harvest record id",
                },
            ],
        },
        "default_facets": ["organization", "publisher"],
    }
    if isinstance(result, dict) and "after" in result:
        payload["after"] = result["after"]
    payload["count"] = len(rows)
    return payload


async def handle_datagov_list_datasets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the us-datagov-list-datasets tool call.

    Returns the response in the records shape primitive payload so the
    MCP Apps host renders the catalog list as a faceted table.
    """
    try:
        params = DataGovListDatasetsParams(**(arguments or {}))
    except ValidationError:
        # Pre-translation: caller-supplied schema errors should keep their
        # native ValidationError type so callers can branch on it.
        raise

    result = list_datagov_datasets(params)
    payload = _datagov_search_to_shape_payload(result)
    return [types.TextContent(type="text", text=to_records_text(payload))]


TOOLS.append(
    types.Tool(
        name="us-datagov-list-datasets",
        description="Search for datasets in the US Data.gov catalog.",
        input_schema=DataGovListDatasetsParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["us-datagov-list-datasets"] = handle_datagov_list_datasets

###################
# Data.gov Dataset Details
###################


class DataGovGetDatasetParams(BaseModel):
    """Parameters for fetching full metadata for a US Data.gov dataset."""

    dataset_id: str = Field(
        ...,
        description="The slug, identifier, or title of the dataset",
    )


def _normalize_dataset_key(value: str | None) -> str:
    return (value or "").strip().casefold()


def fetch_datagov_dataset(params: DataGovGetDatasetParams) -> dict:
    """Fetch full metadata for a specific US Data.gov dataset using catalog search."""
    dataset_id = params.dataset_id.strip()
    query_params = {"q": dataset_id, "per_page": 25}

    response = http_get(
        SEARCH_URL,
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    data = response.json()

    target = _normalize_dataset_key(dataset_id)
    results = data.get("results", [])
    for result in results:
        candidates = [
            result.get("slug"),
            result.get("identifier"),
            result.get("title"),
            result.get("dcat", {}).get("identifier"),
            result.get("dcat", {}).get("title"),
        ]
        if any(_normalize_dataset_key(candidate) == target for candidate in candidates):
            return result

    if results:
        return results[0]

    raise ValueError(f"API Error: dataset not found: {dataset_id}")


async def handle_datagov_get_dataset(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the us-datagov-get-dataset tool call."""
    if not arguments or "dataset_id" not in arguments:
        raise ValueError("dataset_id is required")

    try:
        params = DataGovGetDatasetParams(**arguments)
    except ValidationError:
        # Pre-translation: keep schema errors as-is for the caller.
        raise

    result = fetch_datagov_dataset(params)
    return [types.TextContent(type="text", text=to_json_text(result))]


TOOLS.append(
    types.Tool(
        name="us-datagov-get-dataset",
        description="Fetch detailed metadata for a specific US Data.gov dataset.",
        input_schema=DataGovGetDatasetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["us-datagov-get-dataset"] = handle_datagov_get_dataset


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-data-gov",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


# Server initialization
if __name__ == "__main__":
    import anyio

    anyio.run(main)
