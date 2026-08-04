"""Australia Open Data Catalog (data.gov.au) Provider

This module exposes the public CKAN API hosted at data.gov.au, the Australian
Government's open data portal. The catalog aggregates datasets published by
federal, state, territory, and local government agencies.

License note:
    Catalog metadata is made available under Creative Commons Attribution
    licences (typically CC-BY 4.0). Individual datasets carry their own
    licences; consult the license fields before redistributing payload data.

Features:
- Dataset search via CKAN package_search
- Dataset metadata retrieval via package_show
- Organisation discovery and detail (organization_list / organization_show)
- Group discovery (group_list)
- Tag listing (tag_list)

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.

Note:
    The data.gov.au CKAN endpoint is mounted under a `/data/` path prefix
    (https://data.gov.au/data/api/3/action/...), unlike most CKAN portals.

"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.provider_config import ProviderConfig
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_records_text

# Initialize logging
log = logging.getLogger(__name__)

PROVIDER_ID = "au-data-gov"

# Records-shape adapter constants
_MAX_NOTES_CHARS = 500

# Constants
CONFIG = ProviderConfig(
    base_url="https://data.gov.au/data/api/3/action",
    default_accept="application/json",
)

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Dataset Search
###################


class AUDataGovSearchDatasetsParams(BaseModel):
    """Parameters for searching datasets in the data.gov.au CKAN catalog."""

    q: str | None = Field(
        None,
        description="Free-text search query (CKAN 'q' parameter). Leave blank to list all.",
    )
    rows: int = Field(
        default=20,
        description="Maximum number of datasets to return per page (CKAN 'rows').",
    )
    start: int = Field(
        default=0,
        description="Offset into the result set for pagination (CKAN 'start').",
    )


def fetch_au_datagov_search_datasets(params: AUDataGovSearchDatasetsParams) -> dict:
    """Call CKAN package_search on data.gov.au."""
    query_params: dict[str, Any] = {
        "q": params.q or "",
        "rows": params.rows,
        "start": params.start,
    }
    response = http_get(
        f"{CONFIG.base_url}/package_search",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _ckan_package_search_to_shape_payload(data: dict) -> dict:
    """Adapt a CKAN ``package_search`` response to the records shape
    primitive's payload. CKAN portals share a stable response shape, so
    this mirrors the UK and Canada adapters.
    """
    result = data.get("result") if isinstance(data, dict) else None
    raw_rows = result.get("results", []) if isinstance(result, dict) else []
    rows: list[dict[str, Any]] = []
    for pkg in raw_rows:
        if not isinstance(pkg, dict):
            continue
        org = pkg.get("organization") or {}
        org_title = (
            org.get("title") or org.get("name") if isinstance(org, dict) else None
        )
        tags = pkg.get("tags") or []
        tag_names = [
            t.get("display_name") or t.get("name") for t in tags if isinstance(t, dict)
        ]
        groups = pkg.get("groups") or []
        group_titles = [
            g.get("title") or g.get("display_name") or g.get("name")
            for g in groups
            if isinstance(g, dict)
        ]
        resources = pkg.get("resources") or []
        formats = sorted(
            {
                (r.get("format") or "").upper()
                for r in resources
                if isinstance(r, dict) and r.get("format")
            },
        )
        notes = pkg.get("notes") or ""
        if isinstance(notes, str) and len(notes) > _MAX_NOTES_CHARS:
            notes = notes[:_MAX_NOTES_CHARS].rstrip() + "…"
        rows.append(
            {
                "name": pkg.get("name"),
                "title": pkg.get("title"),
                "organization": org_title,
                "license": pkg.get("license_title"),
                "tags": ", ".join(t for t in tag_names if t),
                "groups": ", ".join(g for g in group_titles if g),
                "num_resources": pkg.get("num_resources")
                or (len(resources) if resources else 0),
                "formats": ", ".join(formats),
                "metadata_created": pkg.get("metadata_created"),
                "metadata_modified": pkg.get("metadata_modified"),
                "notes": notes,
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "name", "type": "string", "description": "CKAN slug"},
                {"name": "title", "type": "string", "description": "Dataset title"},
                {
                    "name": "organization",
                    "type": "string",
                    "description": "Publishing organisation",
                },
                {"name": "license", "type": "string", "description": "Licence title"},
                {"name": "tags", "type": "string", "description": "Tag names (csv)"},
                {
                    "name": "groups",
                    "type": "string",
                    "description": "Group/theme titles (csv)",
                },
                {
                    "name": "num_resources",
                    "type": "number",
                    "description": "Resource count",
                },
                {
                    "name": "formats",
                    "type": "string",
                    "description": "Resource formats (csv)",
                },
                {
                    "name": "metadata_created",
                    "type": "date",
                    "description": "Catalog creation timestamp",
                },
                {
                    "name": "metadata_modified",
                    "type": "date",
                    "description": "Catalog last-modified timestamp",
                },
                {
                    "name": "notes",
                    "type": "string",
                    "description": "Description (truncated)",
                },
            ],
        },
        "default_facets": ["organization", "license", "formats"],
    }
    if isinstance(result, dict) and "count" in result:
        payload["count"] = result["count"]
    return payload


async def handle_au_datagov_search_datasets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the au-data-gov-search-datasets tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = AUDataGovSearchDatasetsParams(**(arguments or {}))
        data = fetch_au_datagov_search_datasets(params)
        payload = _ckan_package_search_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching data.gov.au datasets: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="au-data-gov-search-datasets",
        description="Search the Australia data.gov.au catalog (CKAN package_search).",
        input_schema=AUDataGovSearchDatasetsParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["au-data-gov-search-datasets"] = handle_au_datagov_search_datasets


###################
# Dataset Details
###################


class AUDataGovGetDatasetParams(BaseModel):
    """Parameters for fetching a single dataset's full metadata."""

    id: str = Field(..., description="The CKAN dataset id or slug (name) to retrieve.")


def fetch_au_datagov_get_dataset(params: AUDataGovGetDatasetParams) -> dict:
    """Call CKAN package_show on data.gov.au."""
    response = http_get(
        f"{CONFIG.base_url}/package_show",
        params={"id": params.id},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_au_datagov_get_dataset(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the au-data-gov-get-dataset tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = AUDataGovGetDatasetParams(**arguments)
        data = fetch_au_datagov_get_dataset(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching data.gov.au dataset: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="au-data-gov-get-dataset",
        description="Fetch full metadata for a data.gov.au dataset by id or slug.",
        input_schema=AUDataGovGetDatasetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["au-data-gov-get-dataset"] = handle_au_datagov_get_dataset


###################
# Organizations List
###################


class AUDataGovListOrganizationsParams(BaseModel):
    """Parameters for listing publishing organisations."""

    limit: int = Field(
        default=50,
        description="Maximum number of organisations to return.",
    )


def fetch_au_datagov_list_organizations(
    params: AUDataGovListOrganizationsParams,
) -> dict:
    """Call CKAN organization_list on data.gov.au."""
    query_params: dict[str, Any] = {
        "all_fields": "true",
        "limit": params.limit,
    }
    response = http_get(
        f"{CONFIG.base_url}/organization_list",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_au_datagov_list_organizations(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the au-data-gov-list-organizations tool call."""
    try:
        params = AUDataGovListOrganizationsParams(**(arguments or {}))
        data = fetch_au_datagov_list_organizations(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing data.gov.au organisations: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="au-data-gov-list-organizations",
        description="List publishing organisations on data.gov.au with full fields.",
        input_schema=AUDataGovListOrganizationsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["au-data-gov-list-organizations"] = handle_au_datagov_list_organizations


###################
# Organization Details
###################


class AUDataGovGetOrganizationParams(BaseModel):
    """Parameters for retrieving a single organisation."""

    id: str = Field(..., description="The CKAN organisation id or slug (name).")


def fetch_au_datagov_get_organization(params: AUDataGovGetOrganizationParams) -> dict:
    """Call CKAN organization_show on data.gov.au."""
    response = http_get(
        f"{CONFIG.base_url}/organization_show",
        params={"id": params.id},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_au_datagov_get_organization(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the au-data-gov-get-organization tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = AUDataGovGetOrganizationParams(**arguments)
        data = fetch_au_datagov_get_organization(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching data.gov.au organisation: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="au-data-gov-get-organization",
        description="Fetch full details for a data.gov.au publishing organisation.",
        input_schema=AUDataGovGetOrganizationParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["au-data-gov-get-organization"] = handle_au_datagov_get_organization


###################
# Groups List
###################


class AUDataGovListGroupsParams(BaseModel):
    """Parameters for listing groups (thematic collections)."""

    all_fields: bool = Field(
        default=True,
        description="Whether to return full group records (true) or just names (false).",
    )


def fetch_au_datagov_list_groups(params: AUDataGovListGroupsParams) -> dict:
    """Call CKAN group_list on data.gov.au."""
    query_params: dict[str, Any] = {
        "all_fields": "true" if params.all_fields else "false",
    }
    response = http_get(
        f"{CONFIG.base_url}/group_list",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_au_datagov_list_groups(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the au-data-gov-list-groups tool call."""
    try:
        params = AUDataGovListGroupsParams(**(arguments or {}))
        data = fetch_au_datagov_list_groups(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing data.gov.au groups: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="au-data-gov-list-groups",
        description="List groups defined on data.gov.au.",
        input_schema=AUDataGovListGroupsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["au-data-gov-list-groups"] = handle_au_datagov_list_groups


###################
# Tag Suggestions
###################


class AUDataGovListTagsParams(BaseModel):
    """Parameters for listing or searching tags."""

    query: str | None = Field(
        None,
        description="Optional substring to filter tag names (CKAN 'query').",
    )


def fetch_au_datagov_list_tags(params: AUDataGovListTagsParams) -> dict:
    """Call CKAN tag_list on data.gov.au."""
    query_params: dict[str, Any] = {}
    if params.query:
        query_params["query"] = params.query
    response = http_get(
        f"{CONFIG.base_url}/tag_list",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_au_datagov_list_tags(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the au-data-gov-list-tags tool call."""
    try:
        params = AUDataGovListTagsParams(**(arguments or {}))
        data = fetch_au_datagov_list_tags(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing data.gov.au tags: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="au-data-gov-list-tags",
        description="List or search tag names on data.gov.au.",
        input_schema=AUDataGovListTagsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["au-data-gov-list-tags"] = handle_au_datagov_list_tags


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "au-data-gov",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
