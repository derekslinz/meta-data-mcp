"""Statistics Netherlands (CBS) Data API Client

This module provides interfaces to access the CBS OData API (opendata.cbs.nl).
It allows querying various Dutch statistical datasets, including economic,
geographic, and social data.

Features:
- OData-style querying against CBS legacy OData endpoints (v2/v3 semantics)
- Automatic JSON format handling
- Metadata retrieval for data property descriptions
- Support for paginated table catalogs

Usage:
    The module can be run directly to start a server handling API requests,
    or its components can be imported and used individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI
from meta_data_mcp.utils import http_get, to_json_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "nl-cbs"
BASE_URL = "https://opendata.cbs.nl/ODataFeed/odata"
CATALOG_URL = "https://opendata.cbs.nl/ODataCatalog/Tables"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# CBS Data Retrieval
###################


class CBSDataParams(BaseModel):
    """Parameters for fetching data from a CBS table."""

    table_id: str = Field(
        ...,
        description="The ID of the table (e.g., '80416ENG' for fuel prices)",
    )
    dataset_type: str = Field(
        default="TypedDataSet",
        description="Type of dataset: 'TypedDataSet' or 'UntypedDataSet'",
    )
    select: str | None = Field(
        None,
        description="OData $select parameter (e.g., 'Periods,Euro95_1')",
    )
    filter: str | None = Field(
        None,
        description="OData $filter parameter (e.g., \"Periods eq '2023MM01'\")",
    )
    top: int | None = Field(
        None,
        description="OData $top parameter for limiting results",
    )
    skip: int | None = Field(
        None,
        description="OData $skip parameter for pagination",
    )


class CBSDataResponse(BaseModel):
    """Response model for CBS table data."""

    total_count: int | None = Field(
        None,
        description="Total number of records (if available)",
    )
    results: list[dict] = Field(..., description="The actual data records")


def fetch_cbs_data(params: CBSDataParams) -> dict:
    """Fetch data from a specific CBS table."""
    endpoint = f"{BASE_URL}/{params.table_id}/{params.dataset_type}"

    # Prepare OData query parameters
    query_params = {"$format": "json"}
    if params.select:
        query_params["$select"] = params.select
    if params.filter:
        query_params["$filter"] = params.filter
    if params.top is not None:
        query_params["$top"] = params.top
    if params.skip is not None:
        query_params["$skip"] = params.skip

    response = http_get(
        endpoint,
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


_CBS_DATE_FIELDS = ("Perioden", "Periods", "Periode", "Period")
_CBS_NON_NUMERIC_KEYS = {"ID", *_CBS_DATE_FIELDS}


def _cbs_typed_dataset_to_shape_payload(data: dict) -> dict:
    """Adapt CBS's ``{value: [{ID, Perioden, <metric1>, <metric2>, ...}]}``
    response to the ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    The ``Perioden`` field (CBS code like ``2023MM01``) becomes the
    x-axis; every other numeric field becomes a separate series so
    tables with multiple measurement columns render as multiple lines.
    """
    rows = data.get("value")
    if not isinstance(rows, list):
        return {"points": [], "axes": {"x": "Period", "y": "Value"}}

    points: list[dict[str, Any]] = []
    date_field: str | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if date_field is None:
            for cand in _CBS_DATE_FIELDS:
                if cand in row and isinstance(row[cand], str):
                    date_field = cand
                    break
            if date_field is None:
                continue
        date = row.get(date_field)
        if not isinstance(date, str):
            continue
        for key, value in row.items():
            if key in _CBS_NON_NUMERIC_KEYS:
                continue
            if value is None or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)):
                # CBS sometimes returns numbers as strings — coerce.
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            points.append({"date": date, "value": value, "series": str(key)})

    points.sort(key=lambda p: (p["date"], p["series"]))
    return {
        "points": points,
        "axes": {"x": "Period", "y": "Value"},
    }


async def handle_cbs_data(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cbs-get-typed-dataset tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        if not arguments or "table_id" not in arguments:
            raise ValueError("table_id is required")

        params = CBSDataParams(**arguments)
        data = fetch_cbs_data(params)
        payload = _cbs_typed_dataset_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error fetching CBS data: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cbs-get-typed-dataset",
        description="Fetch data from a specific CBS table ID using the TypedDataSet endpoint. Use 'cbs-list-tables' to find relevant table IDs.",
        input_schema=CBSDataParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["cbs-get-typed-dataset"] = handle_cbs_data

###################
# CBS Metadata
###################


class CBSMetadataParams(BaseModel):
    """Parameters for fetching metadata for a CBS table."""

    table_id: str = Field(..., description="The ID of the table (e.g., '80416ENG')")


def fetch_cbs_metadata(params: CBSMetadataParams) -> dict:
    """Fetch metadata (DataProperties) for a specific CBS table."""
    endpoint = f"{BASE_URL}/{params.table_id}/DataProperties"
    response = http_get(
        endpoint,
        params={"$format": "json"},
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_cbs_metadata(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cbs-get-metadata tool call."""
    try:
        if not arguments or "table_id" not in arguments:
            raise ValueError("table_id is required")

        params = CBSMetadataParams(**arguments)
        metadata = fetch_cbs_metadata(params)
        return [types.TextContent(type="text", text=to_json_text(metadata))]
    except Exception as e:
        log.error(f"Error fetching CBS metadata: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cbs-get-metadata",
        description="Fetch metadata (column descriptions) for a specific CBS table ID.",
        input_schema=CBSMetadataParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cbs-get-metadata"] = handle_cbs_metadata

###################
# CBS Table Catalog
###################


class CBSListTablesParams(BaseModel):
    """Parameters for listing or searching CBS tables."""

    search: str | None = Field(
        None,
        description="Keyword to search in table titles or summaries",
    )
    top: int = Field(default=10, description="Number of results to return")
    skip: int = Field(default=0, description="Number of results to skip")


class CBSListTablesResponse(BaseModel):
    """Response model for CBS tables list."""

    total_count: int | None = Field(
        None,
        description="Total number of available tables",
    )
    results: list[dict] = Field(..., description="The list of tables")


def list_cbs_tables(params: CBSListTablesParams) -> dict:
    """Search or list available CBS tables."""
    query_params = {
        "$format": "json",
        "$top": params.top,
        "$skip": params.skip,
        "$inlinecount": "allpages",
    }
    if params.search:
        # Escape single quotes for OData string literals (OData spec: double them)
        safe_search = params.search.replace("'", "''")
        query_params["$filter"] = (
            f"substringof('{safe_search}', ShortTitle) or substringof('{safe_search}', Title)"
        )

    response = http_get(
        CATALOG_URL,
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_cbs_list_tables(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cbs-list-tables tool call."""
    try:
        params = CBSListTablesParams(**(arguments or {}))
        data = list_cbs_tables(params)
        results = data.get("value", [])
        total_count = data.get("odata.count")
        response = CBSListTablesResponse(results=results, total_count=total_count)
        return [
            types.TextContent(type="text", text=to_json_text(response.model_dump())),
        ]
    except Exception as e:
        log.error(f"Error listing CBS tables: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="cbs-list-tables",
        description="Search or list available CBS data tables.",
        input_schema=CBSListTablesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["cbs-list-tables"] = handle_cbs_list_tables


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "nl-cbs",
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
