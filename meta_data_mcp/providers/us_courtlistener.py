"""CourtListener Provider (Free Law Project)

This module exposes the CourtListener REST API v4, which offers free,
open access to US federal and state court opinions, dockets, judges,
oral arguments, and RECAP-archived PACER documents.

License note:
    CourtListener data is provided by the Free Law Project under their
    terms of use. Most opinion text and metadata is in the public domain
    or available under permissive terms. Consult
    https://www.courtlistener.com/terms/ for specifics.

Authentication:
    Anonymous access works at low volumes. For higher rate limits, set
    the ``COURTLISTENER_API_TOKEN`` environment variable; when present
    it is sent as an ``Authorization: Token <token>`` header.

Features:
- Multi-type search (opinions, RECAP, people, oral arguments)
- Court directory listing
- Opinion and opinion-cluster lookup by id
- Judge / "people" search and lookup
- Docket listing with court / docket-number filters

Usage:
    The module can be run directly to start an MCP server, or its
    components can be imported individually.
"""

import logging
import os
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.fields import PageInt
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_records_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-courtlistener"
BASE_URL = "https://www.courtlistener.com/api/rest/v4"

# Records-shape adapter constants
_MAX_SNIPPET_CHARS = 500

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


def _auth_headers() -> dict[str, str]:
    """Return optional Authorization header if COURTLISTENER_API_TOKEN is set."""
    token = os.getenv("COURTLISTENER_API_TOKEN")
    if token:
        return {"Authorization": f"Token {token}"}
    return {}


###################
# Search
###################


class CourtListenerSearchParams(BaseModel):
    """Parameters for searching CourtListener."""

    q: str | None = Field(
        None,
        description="Free-text query (Lucene-style supported).",
    )
    type: str = Field(
        default="o",
        description=(
            "Result type: 'o' (opinions), 'r' (RECAP), 'p' (people/judges), "
            "'oa' (oral arguments)."
        ),
    )
    order_by: str | None = Field(
        None,
        description="Sort expression, e.g. 'dateFiled desc', 'score desc'.",
    )
    page: PageInt = Field(description="Results page (1-indexed).")


def fetch_courtlistener_search(params: CourtListenerSearchParams) -> dict:
    """Call /search/."""
    query_params: dict[str, Any] = {
        "type": params.type,
        "page": params.page,
    }
    if params.q:
        query_params["q"] = params.q
    if params.order_by:
        query_params["order_by"] = params.order_by
    response = http_get(
        f"{BASE_URL}/search/",
        params=query_params,
        headers=_auth_headers(),
        provider=PROVIDER_ID,
    )
    return response.json()


def _courtlistener_search_to_shape_payload(data: dict) -> dict:
    """Adapt CourtListener's ``/search/`` response to the records shape
    primitive's payload.

    CourtListener returns ``{count, next, previous, results: [...]}``;
    each result varies by ``type`` (opinions, RECAP, people, oral args)
    so we capture a defensive set of the most-common fields and let the
    records bundle infer types for missing-sparse columns.
    """
    raw_rows = data.get("results", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for hit in raw_rows:
        if not isinstance(hit, dict):
            continue
        citation = hit.get("citation") or hit.get("citeCount")
        if isinstance(citation, list):
            citation = ", ".join(c for c in citation if isinstance(c, str))
        snippet = hit.get("snippet") or hit.get("plain_text") or ""
        if isinstance(snippet, str) and len(snippet) > _MAX_SNIPPET_CHARS:
            snippet = snippet[:_MAX_SNIPPET_CHARS].rstrip() + "…"
        rows.append(
            {
                "id": hit.get("id"),
                "caseName": hit.get("caseName") or hit.get("name"),
                "court": hit.get("court") or hit.get("court_id"),
                "dateFiled": hit.get("dateFiled"),
                "citation": citation,
                "docketNumber": hit.get("docketNumber"),
                "status": hit.get("status"),
                "snippet": snippet,
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "id", "type": "number", "description": "Result id"},
                {"name": "caseName", "type": "string", "description": "Case name"},
                {"name": "court", "type": "string", "description": "Court id/name"},
                {"name": "dateFiled", "type": "date", "description": "Date filed"},
                {
                    "name": "citation",
                    "type": "string",
                    "description": "Citation (csv if multiple)",
                },
                {
                    "name": "docketNumber",
                    "type": "string",
                    "description": "Docket number",
                },
                {
                    "name": "status",
                    "type": "string",
                    "description": "Publication status",
                },
                {
                    "name": "snippet",
                    "type": "string",
                    "description": "Snippet (truncated)",
                },
            ],
        },
        "default_facets": ["court", "status"],
    }
    if isinstance(data, dict):
        if "count" in data:
            payload["count"] = data["count"]
        if "next" in data:
            payload["next"] = data["next"]
    return payload


async def handle_courtlistener_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the courtlistener-search tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = CourtListenerSearchParams(**(arguments or {}))
        data = fetch_courtlistener_search(params)
        payload = _courtlistener_search_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching CourtListener: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="courtlistener-search",
        description=(
            "Search CourtListener across opinions ('o'), RECAP docs ('r'), "
            "people/judges ('p'), or oral arguments ('oa')."
        ),
        input_schema=CourtListenerSearchParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["courtlistener-search"] = handle_courtlistener_search


###################
# List Courts
###################


class CourtListenerListCourtsParams(BaseModel):
    """Parameters for listing CourtListener courts."""

    page: PageInt = Field(description="Results page (1-indexed).")


def fetch_courtlistener_list_courts(params: CourtListenerListCourtsParams) -> dict:
    """Call /courts/."""
    query_params: dict[str, Any] = {"page": params.page}
    response = http_get(
        f"{BASE_URL}/courts/",
        params=query_params,
        headers=_auth_headers(),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_courtlistener_list_courts(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the courtlistener-list-courts tool call."""
    try:
        params = CourtListenerListCourtsParams(**(arguments or {}))
        data = fetch_courtlistener_list_courts(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing CourtListener courts: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="courtlistener-list-courts",
        description="List the courts indexed by CourtListener (paginated).",
        input_schema=CourtListenerListCourtsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["courtlistener-list-courts"] = handle_courtlistener_list_courts


###################
# Get Opinion
###################


class CourtListenerGetOpinionParams(BaseModel):
    """Parameters for fetching a single opinion by id."""

    opinion_id: int = Field(..., description="Numeric CourtListener opinion id.")


def fetch_courtlistener_get_opinion(params: CourtListenerGetOpinionParams) -> dict:
    """Call /opinions/{id}/."""
    response = http_get(
        f"{BASE_URL}/opinions/{params.opinion_id}/",
        headers=_auth_headers(),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_courtlistener_get_opinion(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the courtlistener-get-opinion tool call."""
    try:
        if not arguments or "opinion_id" not in arguments:
            raise ValueError("opinion_id is required")
        params = CourtListenerGetOpinionParams(**arguments)
        data = fetch_courtlistener_get_opinion(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CourtListener opinion: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="courtlistener-get-opinion",
        description="Fetch a single CourtListener opinion (with full text) by numeric id.",
        input_schema=CourtListenerGetOpinionParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["courtlistener-get-opinion"] = handle_courtlistener_get_opinion


###################
# Get Opinion Cluster
###################


class CourtListenerGetClusterParams(BaseModel):
    """Parameters for fetching an opinion cluster by id."""

    cluster_id: int = Field(
        ...,
        description="Numeric CourtListener opinion-cluster id.",
    )


def fetch_courtlistener_get_cluster(params: CourtListenerGetClusterParams) -> dict:
    """Call /clusters/{id}/."""
    response = http_get(
        f"{BASE_URL}/clusters/{params.cluster_id}/",
        headers=_auth_headers(),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_courtlistener_get_cluster(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the courtlistener-get-opinion-cluster tool call."""
    try:
        if not arguments or "cluster_id" not in arguments:
            raise ValueError("cluster_id is required")
        params = CourtListenerGetClusterParams(**arguments)
        data = fetch_courtlistener_get_cluster(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CourtListener cluster: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="courtlistener-get-opinion-cluster",
        description=(
            "Fetch a CourtListener opinion cluster (case-level metadata grouping "
            "related opinions) by numeric id."
        ),
        input_schema=CourtListenerGetClusterParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["courtlistener-get-opinion-cluster"] = handle_courtlistener_get_cluster


###################
# List Judges (People)
###################


class CourtListenerListJudgesParams(BaseModel):
    """Parameters for searching CourtListener people (judges)."""

    name_first: str | None = Field(None, description="Judge's first name filter.")
    name_last: str | None = Field(None, description="Judge's last name filter.")
    page: PageInt = Field(description="Results page (1-indexed).")


def fetch_courtlistener_list_judges(params: CourtListenerListJudgesParams) -> dict:
    """Call /people/."""
    query_params: dict[str, Any] = {"page": params.page}
    if params.name_first:
        query_params["name_first"] = params.name_first
    if params.name_last:
        query_params["name_last"] = params.name_last
    response = http_get(
        f"{BASE_URL}/people/",
        params=query_params,
        headers=_auth_headers(),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_courtlistener_list_judges(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the courtlistener-list-judges tool call."""
    try:
        params = CourtListenerListJudgesParams(**(arguments or {}))
        data = fetch_courtlistener_list_judges(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing CourtListener judges: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="courtlistener-list-judges",
        description="Search CourtListener's people endpoint (judges) by first/last name.",
        input_schema=CourtListenerListJudgesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["courtlistener-list-judges"] = handle_courtlistener_list_judges


###################
# Get Judge
###################


class CourtListenerGetJudgeParams(BaseModel):
    """Parameters for fetching a single judge by id."""

    person_id: int = Field(..., description="Numeric CourtListener person id.")


def fetch_courtlistener_get_judge(params: CourtListenerGetJudgeParams) -> dict:
    """Call /people/{id}/."""
    response = http_get(
        f"{BASE_URL}/people/{params.person_id}/",
        headers=_auth_headers(),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_courtlistener_get_judge(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the courtlistener-get-judge tool call."""
    try:
        if not arguments or "person_id" not in arguments:
            raise ValueError("person_id is required")
        params = CourtListenerGetJudgeParams(**arguments)
        data = fetch_courtlistener_get_judge(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CourtListener judge: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="courtlistener-get-judge",
        description="Fetch a CourtListener person (judge) by numeric id.",
        input_schema=CourtListenerGetJudgeParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["courtlistener-get-judge"] = handle_courtlistener_get_judge


###################
# List Dockets
###################


class CourtListenerListDocketsParams(BaseModel):
    """Parameters for listing CourtListener dockets."""

    court: str | None = Field(
        None,
        description="Court id slug (e.g. 'scotus', 'ca9').",
    )
    docket_number: str | None = Field(
        None,
        description="Docket number filter (exact match).",
    )
    page: PageInt = Field(description="Results page (1-indexed).")


def fetch_courtlistener_list_dockets(params: CourtListenerListDocketsParams) -> dict:
    """Call /dockets/."""
    query_params: dict[str, Any] = {"page": params.page}
    if params.court:
        query_params["court"] = params.court
    if params.docket_number:
        query_params["docket_number"] = params.docket_number
    response = http_get(
        f"{BASE_URL}/dockets/",
        params=query_params,
        headers=_auth_headers(),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_courtlistener_list_dockets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the courtlistener-list-dockets tool call."""
    try:
        params = CourtListenerListDocketsParams(**(arguments or {}))
        data = fetch_courtlistener_list_dockets(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing CourtListener dockets: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="courtlistener-list-dockets",
        description="List CourtListener dockets, optionally filtered by court and/or docket number.",
        input_schema=CourtListenerListDocketsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["courtlistener-list-dockets"] = handle_courtlistener_list_dockets


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-courtlistener",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
