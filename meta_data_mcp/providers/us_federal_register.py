"""US Federal Register Provider

This module exposes the federalregister.gov public API (v1), which
publishes the daily Federal Register: rules, proposed rules, notices,
and presidential documents (executive orders, proclamations, etc.).

License note:
    Federal Register documents are works of the US federal government
    and are therefore in the public domain in the United States.

Features:
- Full-text search across documents
- Document lookup by document number (e.g. '2024-12345')
- Agency directory and per-agency lookup
- Public Inspection documents (pre-publication)
- Executive Orders listing
- Suggested searches (curated topic feeds)

Usage:
    The module can be run directly to start an MCP server, or its
    components can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.fields import NonEmptyStr, PageInt
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_records_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-federal-register"
BASE_URL = "https://www.federalregister.gov/api/v1"

# Records-shape adapter constants
_MAX_ABSTRACT_CHARS = 500

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Search Documents
###################


class FedRegSearchDocumentsParams(BaseModel):
    """Parameters for searching Federal Register documents."""

    term: str | None = Field(
        None,
        description="Free-text search term (matched against documents).",
    )
    per_page: int = Field(default=20, description="Results per page (max 1000).")
    page: PageInt = Field(description="Results page (1-indexed).")
    order: str | None = Field(
        None,
        description=(
            "Sort order, e.g. 'relevance', 'newest', 'oldest', "
            "'executive_order_number'."
        ),
    )


def fetch_fedreg_search_documents(params: FedRegSearchDocumentsParams) -> dict:
    """Call /documents.json for a general search."""
    query_params: dict[str, Any] = {
        "per_page": params.per_page,
        "page": params.page,
    }
    if params.term:
        query_params["conditions[term]"] = params.term
    if params.order:
        query_params["order"] = params.order
    response = http_get(
        f"{BASE_URL}/documents.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _fedreg_search_to_shape_payload(data: dict) -> dict:
    """Adapt Federal Register ``/documents.json`` response to the records
    shape primitive's payload.

    Each result carries document_number, title, publication_date, type
    (Rule, Proposed Rule, Notice, Presidential Document), abstract,
    agencies (list of {name, raw_name, ...}), and html_url.
    """
    raw_rows = data.get("results", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for doc in raw_rows:
        if not isinstance(doc, dict):
            continue
        agencies = doc.get("agencies") or []
        if isinstance(agencies, list):
            agency_names = [
                a.get("name") or a.get("raw_name")
                for a in agencies
                if isinstance(a, dict)
            ]
        else:
            agency_names = []
        abstract = doc.get("abstract") or ""
        if isinstance(abstract, str) and len(abstract) > _MAX_ABSTRACT_CHARS:
            abstract = abstract[:_MAX_ABSTRACT_CHARS].rstrip() + "…"
        rows.append(
            {
                "document_number": doc.get("document_number"),
                "title": doc.get("title"),
                "type": doc.get("type"),
                "publication_date": doc.get("publication_date"),
                "agencies": ", ".join(a for a in agency_names if a),
                "html_url": doc.get("html_url"),
                "abstract": abstract,
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {
                    "name": "document_number",
                    "type": "string",
                    "description": "Federal Register document number",
                },
                {"name": "title", "type": "string", "description": "Document title"},
                {
                    "name": "type",
                    "type": "string",
                    "description": "Document type (Rule, Notice, etc.)",
                },
                {
                    "name": "publication_date",
                    "type": "date",
                    "description": "Publication date",
                },
                {
                    "name": "agencies",
                    "type": "string",
                    "description": "Publishing agencies (csv)",
                },
                {"name": "html_url", "type": "string", "description": "Document URL"},
                {
                    "name": "abstract",
                    "type": "string",
                    "description": "Abstract (truncated)",
                },
            ],
        },
        "default_facets": ["type", "agencies"],
    }
    if isinstance(data, dict):
        if "count" in data:
            payload["count"] = data["count"]
        if "next_page_url" in data:
            payload["next_page_url"] = data["next_page_url"]
    return payload


async def handle_fedreg_search_documents(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fedreg-search-documents tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = FedRegSearchDocumentsParams(**(arguments or {}))
        data = fetch_fedreg_search_documents(params)
        payload = _fedreg_search_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching Federal Register documents: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fedreg-search-documents",
        description="Search Federal Register documents (rules, notices, proposed rules, presidential documents).",
        input_schema=FedRegSearchDocumentsParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["fedreg-search-documents"] = handle_fedreg_search_documents


###################
# Get Document
###################


class FedRegGetDocumentParams(BaseModel):
    """Parameters for fetching a single Federal Register document."""

    document_number: NonEmptyStr = Field(
        ...,
        description="Federal Register document number (e.g. '2024-12345').",
    )


def fetch_fedreg_get_document(params: FedRegGetDocumentParams) -> dict:
    """Call /documents/{document_number}.json."""
    response = http_get(
        f"{BASE_URL}/documents/{params.document_number}.json",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_fedreg_get_document(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fedreg-get-document tool call."""
    try:
        if not arguments or "document_number" not in arguments:
            raise ValueError("document_number is required")
        params = FedRegGetDocumentParams(**arguments)
        data = fetch_fedreg_get_document(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Federal Register document: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fedreg-get-document",
        description="Fetch a single Federal Register document by its document number.",
        input_schema=FedRegGetDocumentParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fedreg-get-document"] = handle_fedreg_get_document


###################
# List Agencies
###################


class FedRegListAgenciesParams(BaseModel):
    """Parameters for listing Federal Register agencies (no filters)."""


def fetch_fedreg_list_agencies(params: FedRegListAgenciesParams) -> Any:
    """Call /agencies.json. Returns a list of agency objects."""
    response = http_get(f"{BASE_URL}/agencies.json", provider=PROVIDER_ID)
    return response.json()


async def handle_fedreg_list_agencies(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fedreg-list-agencies tool call."""
    try:
        params = FedRegListAgenciesParams(**(arguments or {}))
        data = fetch_fedreg_list_agencies(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing Federal Register agencies: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fedreg-list-agencies",
        description="List all agencies tracked by the Federal Register.",
        input_schema=FedRegListAgenciesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fedreg-list-agencies"] = handle_fedreg_list_agencies


###################
# Get Agency
###################


class FedRegGetAgencyParams(BaseModel):
    """Parameters for fetching a single Federal Register agency."""

    slug: NonEmptyStr = Field(
        ...,
        description="Agency slug (e.g. 'environmental-protection-agency').",
    )


def fetch_fedreg_get_agency(params: FedRegGetAgencyParams) -> dict:
    """Call /agencies/{slug}.json."""
    response = http_get(f"{BASE_URL}/agencies/{params.slug}.json", provider=PROVIDER_ID)
    return response.json()


async def handle_fedreg_get_agency(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fedreg-get-agency tool call."""
    try:
        if not arguments or "slug" not in arguments:
            raise ValueError("slug is required")
        params = FedRegGetAgencyParams(**arguments)
        data = fetch_fedreg_get_agency(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Federal Register agency: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fedreg-get-agency",
        description="Fetch metadata for a single Federal Register agency by slug.",
        input_schema=FedRegGetAgencyParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fedreg-get-agency"] = handle_fedreg_get_agency


###################
# Public Inspection Documents
###################


class FedRegPublicInspectionParams(BaseModel):
    """Parameters for the Public Inspection desk feed."""

    available_on: str | None = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="ISO date (YYYY-MM-DD) on which the document is available.",
    )
    per_page: int = Field(default=20, description="Results per page (max 1000).")


def fetch_fedreg_public_inspection(params: FedRegPublicInspectionParams) -> dict:
    """Call /public-inspection-documents.json."""
    query_params: dict[str, Any] = {"per_page": params.per_page}
    if params.available_on:
        query_params["conditions[available_on]"] = params.available_on
    response = http_get(
        f"{BASE_URL}/public-inspection-documents.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_fedreg_public_inspection(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fedreg-public-inspection-documents tool call."""
    try:
        params = FedRegPublicInspectionParams(**(arguments or {}))
        data = fetch_fedreg_public_inspection(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Public Inspection documents: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fedreg-public-inspection-documents",
        description=(
            "List documents on the Public Inspection desk (pre-publication "
            "documents), optionally filtered by availability date."
        ),
        input_schema=FedRegPublicInspectionParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fedreg-public-inspection-documents"] = handle_fedreg_public_inspection


###################
# List Executive Orders
###################


class FedRegListExecutiveOrdersParams(BaseModel):
    """Parameters for listing Executive Orders via the documents endpoint."""

    president: str | None = Field(
        None,
        description=(
            "President slug filter (e.g. 'donald-trump', 'joe-biden', 'barack-obama')."
        ),
    )
    per_page: int = Field(default=20, description="Results per page (max 1000).")
    page: PageInt = Field(description="Results page (1-indexed).")


def fetch_fedreg_list_executive_orders(
    params: FedRegListExecutiveOrdersParams,
) -> dict:
    """Call /documents.json with presidential_document_type=executive_order."""
    query_params: dict[str, Any] = {
        "conditions[presidential_document_type]": "executive_order",
        "per_page": params.per_page,
        "page": params.page,
    }
    if params.president:
        query_params["conditions[president]"] = params.president
    response = http_get(
        f"{BASE_URL}/documents.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_fedreg_list_executive_orders(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fedreg-list-executive-orders tool call."""
    try:
        params = FedRegListExecutiveOrdersParams(**(arguments or {}))
        data = fetch_fedreg_list_executive_orders(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing Executive Orders: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fedreg-list-executive-orders",
        description="List Executive Orders from the Federal Register, optionally filtered by president.",
        input_schema=FedRegListExecutiveOrdersParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fedreg-list-executive-orders"] = handle_fedreg_list_executive_orders


###################
# Suggested Searches
###################


class FedRegSuggestedSearchesParams(BaseModel):
    """Parameters for fetching curated suggested searches."""

    sections: str | None = Field(
        None,
        description="Comma-separated section slugs (e.g. 'health,environment').",
    )
    term: str | None = Field(
        None,
        description="Optional free-text query to filter suggested searches.",
    )


def fetch_fedreg_suggested_searches(params: FedRegSuggestedSearchesParams) -> Any:
    """Call /suggested_searches.json."""
    query_params: dict[str, Any] = {}
    if params.sections:
        query_params["sections"] = params.sections
    if params.term:
        query_params["conditions[term]"] = params.term
    response = http_get(
        f"{BASE_URL}/suggested_searches.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_fedreg_suggested_searches(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the fedreg-suggested-searches tool call."""
    try:
        params = FedRegSuggestedSearchesParams(**(arguments or {}))
        data = fetch_fedreg_suggested_searches(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching suggested searches: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="fedreg-suggested-searches",
        description="Fetch Federal Register editor-curated suggested searches by section or term.",
        input_schema=FedRegSuggestedSearchesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["fedreg-suggested-searches"] = handle_fedreg_suggested_searches


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-federal-register",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
