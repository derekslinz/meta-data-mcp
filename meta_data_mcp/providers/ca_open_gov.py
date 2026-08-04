"""Canada Open Government Data Catalog (open.canada.ca) Provider

This module exposes the public CKAN API hosted at open.canada.ca/data, the
Government of Canada's open data portal. The catalog aggregates datasets from
federal departments and agencies, with bilingual (English/French) metadata.

License note:
    Catalog metadata is made available under the Open Government Licence -
    Canada. Individual datasets may carry their own licences; consult the
    license fields returned by the API before redistributing payload data.

Features:
- Dataset search via CKAN package_search
- Dataset metadata retrieval via package_show
- Organisation discovery and detail (organization_list / organization_show)
- Group discovery (group_list)
- Tag listing (tag_list)
- License catalog (license_list)

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
PROVIDER_ID = "ca-open-gov"
BASE_URL = "https://open.canada.ca/data/api/action"

# Records-shape adapter constants
_MAX_NOTES_CHARS = 500

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Dataset Search
###################


class CAOpenGovSearchDatasetsParams(BaseModel):
    """Parameters for searching datasets in the open.canada.ca CKAN catalog."""

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


def fetch_ca_opengov_search_datasets(params: CAOpenGovSearchDatasetsParams) -> dict:
    """Call CKAN package_search on open.canada.ca."""
    query_params: dict[str, Any] = {
        "q": params.q or "",
        "rows": params.rows,
        "start": params.start,
    }
    response = http_get(
        f"{BASE_URL}/package_search",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _ckan_package_search_to_shape_payload(data: dict) -> dict:
    """Adapt a CKAN ``package_search`` response to the
    ``ui://meta-data-mcp/shape/records/v1`` payload.

    Flattens the most-useful dataset fields (title, organisation, tags,
    license, resource counts, dates) to top-level columns. Mirror of the
    UK-gov adapter; the response shape is identical across CKAN portals.
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


async def handle_ca_opengov_search_datasets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ca-open-gov-search-datasets tool call.

    Returns the response in the ``ui://meta-data-mcp/shape/records/v1``
    payload so MCP Apps hosts render the dataset list as a faceted table.
    """
    try:
        params = CAOpenGovSearchDatasetsParams(**(arguments or {}))
        data = fetch_ca_opengov_search_datasets(params)
        payload = _ckan_package_search_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching open.canada.ca datasets: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ca-open-gov-search-datasets",
        description="Search the Canada open.canada.ca catalog (CKAN package_search).",
        input_schema=CAOpenGovSearchDatasetsParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["ca-open-gov-search-datasets"] = handle_ca_opengov_search_datasets


###################
# Dataset Details
###################


class CAOpenGovGetDatasetParams(BaseModel):
    """Parameters for fetching a single dataset's full metadata."""

    id: str = Field(..., description="The CKAN dataset id or slug (name) to retrieve.")


def fetch_ca_opengov_get_dataset(params: CAOpenGovGetDatasetParams) -> dict:
    """Call CKAN package_show on open.canada.ca."""
    response = http_get(
        f"{BASE_URL}/package_show",
        params={"id": params.id},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ca_opengov_get_dataset(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ca-open-gov-get-dataset tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = CAOpenGovGetDatasetParams(**arguments)
        data = fetch_ca_opengov_get_dataset(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching open.canada.ca dataset: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ca-open-gov-get-dataset",
        description="Fetch full metadata for an open.canada.ca dataset by id or slug.",
        input_schema=CAOpenGovGetDatasetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ca-open-gov-get-dataset"] = handle_ca_opengov_get_dataset


###################
# Organizations List
###################


class CAOpenGovListOrganizationsParams(BaseModel):
    """Parameters for listing publishing organisations."""

    limit: int = Field(
        default=50,
        description="Maximum number of organisations to return.",
    )


def fetch_ca_opengov_list_organizations(
    params: CAOpenGovListOrganizationsParams,
) -> dict:
    """Call CKAN organization_list on open.canada.ca."""
    query_params: dict[str, Any] = {
        "all_fields": "true",
        "limit": params.limit,
    }
    response = http_get(
        f"{BASE_URL}/organization_list",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ca_opengov_list_organizations(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ca-open-gov-list-organizations tool call."""
    try:
        params = CAOpenGovListOrganizationsParams(**(arguments or {}))
        data = fetch_ca_opengov_list_organizations(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing open.canada.ca organisations: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ca-open-gov-list-organizations",
        description="List publishing organisations on open.canada.ca with full fields.",
        input_schema=CAOpenGovListOrganizationsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ca-open-gov-list-organizations"] = handle_ca_opengov_list_organizations


###################
# Organization Details
###################


class CAOpenGovGetOrganizationParams(BaseModel):
    """Parameters for retrieving a single organisation."""

    id: str = Field(..., description="The CKAN organisation id or slug (name).")


def fetch_ca_opengov_get_organization(params: CAOpenGovGetOrganizationParams) -> dict:
    """Call CKAN organization_show on open.canada.ca."""
    response = http_get(
        f"{BASE_URL}/organization_show",
        params={"id": params.id},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ca_opengov_get_organization(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ca-open-gov-get-organization tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = CAOpenGovGetOrganizationParams(**arguments)
        data = fetch_ca_opengov_get_organization(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching open.canada.ca organisation: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ca-open-gov-get-organization",
        description="Fetch full details for an open.canada.ca publishing organisation.",
        input_schema=CAOpenGovGetOrganizationParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ca-open-gov-get-organization"] = handle_ca_opengov_get_organization


###################
# Groups List
###################


class CAOpenGovListGroupsParams(BaseModel):
    """Parameters for listing groups (thematic collections)."""

    all_fields: bool = Field(
        default=True,
        description="Whether to return full group records (true) or just names (false).",
    )


def fetch_ca_opengov_list_groups(params: CAOpenGovListGroupsParams) -> dict:
    """Call CKAN group_list on open.canada.ca."""
    query_params: dict[str, Any] = {
        "all_fields": "true" if params.all_fields else "false",
    }
    response = http_get(
        f"{BASE_URL}/group_list",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ca_opengov_list_groups(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ca-open-gov-list-groups tool call."""
    try:
        params = CAOpenGovListGroupsParams(**(arguments or {}))
        data = fetch_ca_opengov_list_groups(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing open.canada.ca groups: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ca-open-gov-list-groups",
        description="List groups defined on open.canada.ca.",
        input_schema=CAOpenGovListGroupsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ca-open-gov-list-groups"] = handle_ca_opengov_list_groups


###################
# Tag Suggestions
###################


class CAOpenGovListTagsParams(BaseModel):
    """Parameters for listing or searching tags."""

    query: str | None = Field(
        None,
        description="Optional substring to filter tag names (CKAN 'query').",
    )


def fetch_ca_opengov_list_tags(params: CAOpenGovListTagsParams) -> dict:
    """Call CKAN tag_list on open.canada.ca."""
    query_params: dict[str, Any] = {}
    if params.query:
        query_params["query"] = params.query
    response = http_get(
        f"{BASE_URL}/tag_list",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ca_opengov_list_tags(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ca-open-gov-list-tags tool call."""
    try:
        params = CAOpenGovListTagsParams(**(arguments or {}))
        data = fetch_ca_opengov_list_tags(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing open.canada.ca tags: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ca-open-gov-list-tags",
        description="List or search tag names on open.canada.ca.",
        input_schema=CAOpenGovListTagsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ca-open-gov-list-tags"] = handle_ca_opengov_list_tags


###################
# License Catalog
###################


class CAOpenGovListLicensesParams(BaseModel):
    """Parameters for listing the licence catalog (no fields required)."""


def fetch_ca_opengov_list_licenses(params: CAOpenGovListLicensesParams) -> dict:
    """Call CKAN license_list on open.canada.ca."""
    response = http_get(f"{BASE_URL}/license_list", provider=PROVIDER_ID)
    return response.json()


async def handle_ca_opengov_list_licenses(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ca-open-gov-list-licenses tool call."""
    try:
        params = CAOpenGovListLicensesParams(**(arguments or {}))
        data = fetch_ca_opengov_list_licenses(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing open.canada.ca licences: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ca-open-gov-list-licenses",
        description="List the licence catalog used by open.canada.ca datasets.",
        input_schema=CAOpenGovListLicensesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ca-open-gov-list-licenses"] = handle_ca_opengov_list_licenses


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "ca-open-gov",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
