"""France Open Data Portal (data.gouv.fr) Provider

This module exposes the public uData REST API hosted at www.data.gouv.fr, the
French Government's open data portal. The catalog aggregates datasets, reuses,
organisations, and thematic topics published by central and local government,
public agencies, and partner institutions.

License note:
    Catalog metadata is typically published under the Licence Ouverte / Open
    Licence v2.0 (compatible with CC-BY 2.0). Individual datasets carry their
    own licences; consult the license fields before redistribution.

Features:
- Dataset search and listing (uData /datasets/)
- Dataset metadata retrieval (/datasets/{id_or_slug}/)
- Organisation discovery and detail (/organizations/)
- Reuse discovery (/reuses/) — community-built apps using the data
- Topic catalog (/topics/)
- Tag suggestions (/tags/suggest/)

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.

Note:
    Unlike CKAN portals, data.gouv.fr uses uData REST conventions: pagination
    uses `page` + `page_size`, and single-resource lookups are RESTful path
    segments (`/datasets/{slug}/`) rather than CKAN's `package_show?id=`.

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
PROVIDER_ID = "fr-data-gouv"
BASE_URL = "https://www.data.gouv.fr/api/1"

# Records-shape adapter constants
_MAX_DESC_CHARS = 500

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Dataset Search
###################


class FRDataGouvSearchDatasetsParams(BaseModel):
    """Parameters for searching datasets on data.gouv.fr."""

    q: str | None = Field(
        None,
        description="Free-text search query. Leave blank to list all datasets.",
    )
    page: int = Field(
        default=1,
        description="1-indexed page number for pagination.",
    )
    page_size: int = Field(
        default=20,
        description="Number of datasets to return per page.",
    )


def fetch_fr_datagouv_search_datasets(params: FRDataGouvSearchDatasetsParams) -> dict:
    """Call /datasets/ on data.gouv.fr."""
    query_params: dict[str, Any] = {
        "page": params.page,
        "page_size": params.page_size,
    }
    if params.q:
        query_params["q"] = params.q
    response = http_get(
        f"{BASE_URL}/datasets/",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _udata_search_to_shape_payload(data: dict) -> dict:
    """Adapt a uData ``/datasets/`` response (data.gouv.fr) to the
    ``ui://meta-data-mcp/shape/records/v1`` payload.

    uData uses ``{data: [...], total, page, page_size, next_page}``
    rather than CKAN's ``{result: {results: [...]}}`` envelope.
    """
    raw_rows = data.get("data", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for ds in raw_rows:
        if not isinstance(ds, dict):
            continue
        org = ds.get("organization") or {}
        org_name = (
            (org.get("name") or org.get("acronym")) if isinstance(org, dict) else None
        )
        tags = ds.get("tags") or []
        if isinstance(tags, list):
            tag_csv = ", ".join(t for t in tags if isinstance(t, str))
        else:
            tag_csv = ""
        license_ = ds.get("license") or ""
        resources = ds.get("resources") or []
        if isinstance(resources, list):
            formats = sorted(
                {
                    (r.get("format") or "").upper()
                    for r in resources
                    if isinstance(r, dict) and r.get("format")
                },
            )
        else:
            formats = []
        desc = ds.get("description") or ""
        if isinstance(desc, str) and len(desc) > _MAX_DESC_CHARS:
            desc = desc[:_MAX_DESC_CHARS].rstrip() + "…"
        rows.append(
            {
                "slug": ds.get("slug"),
                "title": ds.get("title"),
                "organization": org_name,
                "license": license_,
                "tags": tag_csv,
                "num_resources": len(resources) if isinstance(resources, list) else 0,
                "formats": ", ".join(formats),
                "created_at": ds.get("created_at"),
                "last_modified": ds.get("last_modified"),
                "description": desc,
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "slug", "type": "string", "description": "Dataset slug"},
                {"name": "title", "type": "string", "description": "Dataset title"},
                {
                    "name": "organization",
                    "type": "string",
                    "description": "Publishing organisation",
                },
                {"name": "license", "type": "string", "description": "Licence id"},
                {"name": "tags", "type": "string", "description": "Tags (csv)"},
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
                    "name": "created_at",
                    "type": "date",
                    "description": "Creation timestamp",
                },
                {
                    "name": "last_modified",
                    "type": "date",
                    "description": "Last-modified timestamp",
                },
                {
                    "name": "description",
                    "type": "string",
                    "description": "Description (truncated)",
                },
            ],
        },
        "default_facets": ["organization", "license", "formats"],
    }
    if isinstance(data, dict) and "total" in data:
        payload["total"] = data["total"]
    return payload


async def handle_fr_datagouv_search_datasets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fr-data-gouv-search-datasets tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = FRDataGouvSearchDatasetsParams(**(arguments or {}))
        data = fetch_fr_datagouv_search_datasets(params)
        payload = _udata_search_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching data.gouv.fr datasets: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fr-data-gouv-search-datasets",
        description="Search the France data.gouv.fr catalog (uData /datasets/).",
        input_schema=FRDataGouvSearchDatasetsParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["fr-data-gouv-search-datasets"] = handle_fr_datagouv_search_datasets


###################
# Dataset Details
###################


class FRDataGouvGetDatasetParams(BaseModel):
    """Parameters for fetching a single dataset's full metadata."""

    id: str = Field(
        ...,
        description="The dataset slug or id (used as a RESTful path segment).",
    )


def fetch_fr_datagouv_get_dataset(params: FRDataGouvGetDatasetParams) -> dict:
    """Call /datasets/{id}/ on data.gouv.fr."""
    response = http_get(f"{BASE_URL}/datasets/{params.id}/", provider=PROVIDER_ID)
    return response.json()


async def handle_fr_datagouv_get_dataset(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fr-data-gouv-get-dataset tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = FRDataGouvGetDatasetParams(**arguments)
        data = fetch_fr_datagouv_get_dataset(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching data.gouv.fr dataset: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fr-data-gouv-get-dataset",
        description="Fetch full metadata for a data.gouv.fr dataset by slug or id.",
        input_schema=FRDataGouvGetDatasetParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fr-data-gouv-get-dataset"] = handle_fr_datagouv_get_dataset


###################
# Organizations List
###################


class FRDataGouvListOrganizationsParams(BaseModel):
    """Parameters for listing publishing organisations."""

    page: int = Field(default=1, description="1-indexed page number.")
    page_size: int = Field(
        default=20,
        description="Number of organisations to return per page.",
    )


def fetch_fr_datagouv_list_organizations(
    params: FRDataGouvListOrganizationsParams,
) -> dict:
    """Call /organizations/ on data.gouv.fr."""
    query_params: dict[str, Any] = {
        "page": params.page,
        "page_size": params.page_size,
    }
    response = http_get(
        f"{BASE_URL}/organizations/",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_fr_datagouv_list_organizations(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fr-data-gouv-list-organizations tool call."""
    try:
        params = FRDataGouvListOrganizationsParams(**(arguments or {}))
        data = fetch_fr_datagouv_list_organizations(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing data.gouv.fr organisations: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fr-data-gouv-list-organizations",
        description="List publishing organisations on data.gouv.fr.",
        input_schema=FRDataGouvListOrganizationsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fr-data-gouv-list-organizations"] = (
    handle_fr_datagouv_list_organizations
)


###################
# Organization Details
###################


class FRDataGouvGetOrganizationParams(BaseModel):
    """Parameters for fetching a single organisation's full record."""

    id: str = Field(
        ...,
        description="The organisation slug or id (used as a RESTful path segment).",
    )


def fetch_fr_datagouv_get_organization(
    params: FRDataGouvGetOrganizationParams,
) -> dict:
    """Call /organizations/{slug}/ on data.gouv.fr."""
    response = http_get(f"{BASE_URL}/organizations/{params.id}/", provider=PROVIDER_ID)
    return response.json()


async def handle_fr_datagouv_get_organization(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fr-data-gouv-get-organization tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = FRDataGouvGetOrganizationParams(**arguments)
        data = fetch_fr_datagouv_get_organization(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching data.gouv.fr organisation: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fr-data-gouv-get-organization",
        description="Fetch full details for a data.gouv.fr publishing organisation.",
        input_schema=FRDataGouvGetOrganizationParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fr-data-gouv-get-organization"] = handle_fr_datagouv_get_organization


###################
# Reuses Search
###################


class FRDataGouvSearchReusesParams(BaseModel):
    """Parameters for searching reuses (apps/sites built on the data)."""

    q: str | None = Field(
        None,
        description="Free-text search query for reuse titles/descriptions.",
    )
    page_size: int = Field(
        default=20,
        description="Number of reuses to return per page.",
    )


def fetch_fr_datagouv_search_reuses(params: FRDataGouvSearchReusesParams) -> dict:
    """Call /reuses/ on data.gouv.fr."""
    query_params: dict[str, Any] = {"page_size": params.page_size}
    if params.q:
        query_params["q"] = params.q
    response = http_get(
        f"{BASE_URL}/reuses/",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_fr_datagouv_search_reuses(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fr-data-gouv-search-reuses tool call."""
    try:
        params = FRDataGouvSearchReusesParams(**(arguments or {}))
        data = fetch_fr_datagouv_search_reuses(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching data.gouv.fr reuses: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fr-data-gouv-search-reuses",
        description="Search community-built reuses on data.gouv.fr.",
        input_schema=FRDataGouvSearchReusesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fr-data-gouv-search-reuses"] = handle_fr_datagouv_search_reuses


###################
# Topics
###################


class FRDataGouvListTopicsParams(BaseModel):
    """Parameters for listing thematic topics."""

    page_size: int = Field(
        default=20,
        description="Number of topics to return per page.",
    )


def fetch_fr_datagouv_list_topics(params: FRDataGouvListTopicsParams) -> dict:
    """Call /topics/ on data.gouv.fr."""
    query_params: dict[str, Any] = {"page_size": params.page_size}
    response = http_get(
        f"{BASE_URL}/topics/",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_fr_datagouv_list_topics(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fr-data-gouv-list-topics tool call."""
    try:
        params = FRDataGouvListTopicsParams(**(arguments or {}))
        data = fetch_fr_datagouv_list_topics(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing data.gouv.fr topics: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fr-data-gouv-list-topics",
        description="List thematic topics defined on data.gouv.fr.",
        input_schema=FRDataGouvListTopicsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fr-data-gouv-list-topics"] = handle_fr_datagouv_list_topics


###################
# Tag Suggestions
###################


class FRDataGouvListTagsParams(BaseModel):
    """Parameters for tag-name autosuggestion."""

    q: str | None = Field(
        None,
        description="Substring to suggest tags for (uData /tags/suggest/?q=).",
    )


def fetch_fr_datagouv_list_tags(params: FRDataGouvListTagsParams) -> dict | list:
    """Call /tags/suggest/ on data.gouv.fr."""
    query_params: dict[str, Any] = {}
    if params.q:
        query_params["q"] = params.q
    response = http_get(
        f"{BASE_URL}/tags/suggest/",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_fr_datagouv_list_tags(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fr-data-gouv-list-tags tool call."""
    try:
        params = FRDataGouvListTagsParams(**(arguments or {}))
        data = fetch_fr_datagouv_list_tags(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error suggesting data.gouv.fr tags: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fr-data-gouv-list-tags",
        description="Autosuggest tag names on data.gouv.fr.",
        input_schema=FRDataGouvListTagsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fr-data-gouv-list-tags"] = handle_fr_datagouv_list_tags


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "fr-data-gouv",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
