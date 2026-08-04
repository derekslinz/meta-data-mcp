"""OpenAlex Provider

This module exposes the OpenAlex API, a fully open index of the global
scholarly research graph: works, authors, institutions, sources
(journals/repositories), concepts, and publishers.

License note:
    OpenAlex metadata is released under the CC0 public-domain
    dedication (see https://docs.openalex.org/ for details).

Polite pool:
    OpenAlex routes traffic from clients that identify themselves via
    the ``mailto`` query parameter onto a faster, more reliable pool.
    This module injects ``mailto`` automatically from the
    ``META_DATA_MCP_CONTACT`` environment variable (falling back to a
    safe default).

Features:
- Works search (by query, with filters, sort, paging)
- Single-work lookup by id (OpenAlex W-id, DOI, or full URL)
- Authors / institutions / sources / concepts / publishers search
- Single-author lookup

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

from meta_data_mcp.ui_resources.app_entity_graph_v1 import URI as ENTITY_GRAPH_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_entity_graph_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-openalex"
BASE_URL = "https://api.openalex.org"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


def _polite_params() -> dict[str, str]:
    """Return polite-pool query params (``mailto``) for every OpenAlex call."""
    return {"mailto": os.getenv("META_DATA_MCP_CONTACT", "meta-data-mcp@example.org")}


def _merge_params(extra: dict[str, Any]) -> dict[str, Any]:
    """Merge polite-pool params with caller-supplied query params."""
    merged: dict[str, Any] = {}
    merged.update(_polite_params())
    merged.update(extra)
    return merged


###################
# Search Works
###################


class OpenAlexSearchWorksParams(BaseModel):
    """Parameters for searching OpenAlex works."""

    search: str | None = Field(
        None,
        description="Free-text search query across work fields.",
    )
    per_page: int = Field(default=25, description="Results per page (max 200).")
    page: int = Field(default=1, description="Results page (1-indexed).")
    filter: str | None = Field(
        None,
        description=(
            "Comma-separated OpenAlex filter expression "
            "(e.g. 'from_publication_date:2024-01-01,type:journal-article')."
        ),
    )
    sort: str | None = Field(
        None,
        description=(
            "Sort expression, e.g. 'cited_by_count:desc', 'publication_date:desc'."
        ),
    )


def fetch_openalex_search_works(params: OpenAlexSearchWorksParams) -> dict:
    """Call /works."""
    query_params: dict[str, Any] = {
        "per_page": params.per_page,
        "page": params.page,
    }
    if params.search:
        query_params["search"] = params.search
    if params.filter:
        query_params["filter"] = params.filter
    if params.sort:
        query_params["sort"] = params.sort
    response = http_get(
        f"{BASE_URL}/works",
        params=_merge_params(query_params),
        provider=PROVIDER_ID,
    )
    return response.json()


def _openalex_works_to_entity_graph_payload(data: dict) -> dict:
    """Adapt OpenAlex ``/works`` response to the entity-graph payload.

    Each work becomes a ``work`` node; each authorship becomes an
    ``author`` node connected to its work by an "authored" edge; each
    concept becomes a ``concept`` node connected to the work by an
    "about" edge. Authors and concepts dedupe across works — that's
    what surfaces the co-authorship overlay (two works sharing an
    author show up as a triangle in the force layout).

    Author nodes are weighted by how many works in the response they
    appear on; the force layout uses ``weight`` to shorten edges so
    frequent co-authors cluster visibly.
    """
    results = data.get("results", []) if isinstance(data, dict) else []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add_node(
        node_id: str,
        label: str,
        ntype: str,
        attrs: dict | None = None,
    ) -> None:
        if not node_id or node_id in seen_ids:
            return
        seen_ids.add(node_id)
        nodes.append(
            {
                "id": node_id,
                "label": label or node_id,
                "type": ntype,
                "attrs": attrs or {},
            },
        )

    # Track co-author frequency so edge weights communicate clustering.
    author_work_counts: dict[str, int] = {}

    for work in results:
        if not isinstance(work, dict):
            continue
        work_id = work.get("id") or work.get("doi")
        if not work_id:
            continue
        work_id = str(work_id)
        title = work.get("title") or work.get("display_name") or work_id
        _add_node(
            work_id,
            str(title),
            "work",
            {
                "doi": work.get("doi"),
                "publication_year": work.get("publication_year"),
                "cited_by_count": work.get("cited_by_count"),
                "type": work.get("type"),
            },
        )

        authorships = work.get("authorships") or []
        if isinstance(authorships, list):
            for ship in authorships:
                if not isinstance(ship, dict):
                    continue
                author = ship.get("author") or {}
                if not isinstance(author, dict):
                    continue
                author_id = author.get("id") or author.get("orcid")
                if not author_id:
                    continue
                author_id = str(author_id)
                author_work_counts[author_id] = author_work_counts.get(author_id, 0) + 1
                _add_node(
                    author_id,
                    str(author.get("display_name") or author_id),
                    "author",
                    {
                        "orcid": author.get("orcid"),
                        "institutions": [
                            inst.get("display_name")
                            for inst in (ship.get("institutions") or [])
                            if isinstance(inst, dict) and inst.get("display_name")
                        ]
                        or None,
                    },
                )
                edges.append(
                    {
                        "source": work_id,
                        "target": author_id,
                        "label": "authored",
                        "weight": 1,
                    },
                )

        concepts = work.get("concepts") or []
        if isinstance(concepts, list):
            # Restrict to top-3 concepts per work — beyond that the
            # graph gets dominated by generic taxonomy terms.
            for concept in concepts[:3]:
                if not isinstance(concept, dict):
                    continue
                concept_id = concept.get("id")
                if not concept_id:
                    continue
                concept_id = str(concept_id)
                _add_node(
                    concept_id,
                    str(concept.get("display_name") or concept_id),
                    "concept",
                    {"level": concept.get("level"), "score": concept.get("score")},
                )
                edges.append(
                    {
                        "source": work_id,
                        "target": concept_id,
                        "label": "about",
                        "weight": max(1.0, float(concept.get("score") or 0.0) * 2.0),
                    },
                )

    # Promote author edge weights by works-shared count so the force
    # layout pulls prolific co-authors closer to their works cluster.
    for e in edges:
        if e["label"] == "authored":
            e["weight"] = author_work_counts.get(e["target"], 1)

    return {"nodes": nodes, "edges": edges}


async def handle_openalex_search_works(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openalex-search-works tool call.

    Returns the response shaped for the entity-graph app primitive so
    the bound bundle can render works↔authors↔concepts directly.
    """
    try:
        params = OpenAlexSearchWorksParams(**(arguments or {}))
        data = fetch_openalex_search_works(params)
        payload = _openalex_works_to_entity_graph_payload(data)
        return [types.TextContent(type="text", text=to_entity_graph_text(payload))]
    except Exception as e:
        log.error(f"Error searching OpenAlex works: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openalex-search-works",
        description="Search OpenAlex works with free text, filters, sort, and paging.",
        input_schema=OpenAlexSearchWorksParams.model_json_schema(),
        # MCP Apps binding: works↔authors↔concepts surface for the
        # entity-graph app. Use the alias keyword (``_meta=``) — ``meta=``
        # silently drops into extras; see tests/test_ui_resource.py.
        _meta={"ui": {"resourceUri": ENTITY_GRAPH_URI}},
    ),
)
TOOLS_HANDLERS["openalex-search-works"] = handle_openalex_search_works


###################
# Get Work
###################


class OpenAlexGetWorkParams(BaseModel):
    """Parameters for fetching a single OpenAlex work."""

    id: str = Field(
        ...,
        description=(
            "OpenAlex work id ('W12345'), a DOI ('doi:10.1038/...'), or full "
            "OpenAlex URL."
        ),
    )


def fetch_openalex_get_work(params: OpenAlexGetWorkParams) -> dict:
    """Call /works/{id}."""
    response = http_get(
        f"{BASE_URL}/works/{params.id}",
        params=_merge_params({}),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openalex_get_work(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openalex-get-work tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = OpenAlexGetWorkParams(**arguments)
        data = fetch_openalex_get_work(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OpenAlex work: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openalex-get-work",
        description=(
            "Fetch a single OpenAlex work by id (OpenAlex W-id, DOI "
            "prefix, or full URL)."
        ),
        input_schema=OpenAlexGetWorkParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openalex-get-work"] = handle_openalex_get_work


###################
# Search Authors
###################


class OpenAlexSearchAuthorsParams(BaseModel):
    """Parameters for searching OpenAlex authors."""

    search: str | None = Field(None, description="Free-text search query.")
    per_page: int = Field(default=25, description="Results per page (max 200).")
    page: int = Field(default=1, description="Results page (1-indexed).")
    filter: str | None = Field(
        None,
        description="Comma-separated OpenAlex filter expression.",
    )


def fetch_openalex_search_authors(params: OpenAlexSearchAuthorsParams) -> dict:
    """Call /authors."""
    query_params: dict[str, Any] = {
        "per_page": params.per_page,
        "page": params.page,
    }
    if params.search:
        query_params["search"] = params.search
    if params.filter:
        query_params["filter"] = params.filter
    response = http_get(
        f"{BASE_URL}/authors",
        params=_merge_params(query_params),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openalex_search_authors(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openalex-search-authors tool call."""
    try:
        params = OpenAlexSearchAuthorsParams(**(arguments or {}))
        data = fetch_openalex_search_authors(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching OpenAlex authors: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openalex-search-authors",
        description="Search OpenAlex authors by free-text query and optional filters.",
        input_schema=OpenAlexSearchAuthorsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openalex-search-authors"] = handle_openalex_search_authors


###################
# Get Author
###################


class OpenAlexGetAuthorParams(BaseModel):
    """Parameters for fetching a single OpenAlex author."""

    id: str = Field(
        ...,
        description=(
            "OpenAlex author id ('A12345'), an ORCID ('orcid:0000-...'), or "
            "full OpenAlex URL."
        ),
    )


def fetch_openalex_get_author(params: OpenAlexGetAuthorParams) -> dict:
    """Call /authors/{id}."""
    response = http_get(
        f"{BASE_URL}/authors/{params.id}",
        params=_merge_params({}),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openalex_get_author(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openalex-get-author tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = OpenAlexGetAuthorParams(**arguments)
        data = fetch_openalex_get_author(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OpenAlex author: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openalex-get-author",
        description="Fetch a single OpenAlex author by id (A-id, ORCID, or full URL).",
        input_schema=OpenAlexGetAuthorParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openalex-get-author"] = handle_openalex_get_author


###################
# Search Institutions
###################


class OpenAlexSearchInstitutionsParams(BaseModel):
    """Parameters for searching OpenAlex institutions."""

    search: str | None = Field(None, description="Free-text search query.")
    per_page: int = Field(default=25, description="Results per page (max 200).")
    filter: str | None = Field(
        None,
        description="Comma-separated OpenAlex filter expression.",
    )


def fetch_openalex_search_institutions(
    params: OpenAlexSearchInstitutionsParams,
) -> dict:
    """Call /institutions."""
    query_params: dict[str, Any] = {"per_page": params.per_page}
    if params.search:
        query_params["search"] = params.search
    if params.filter:
        query_params["filter"] = params.filter
    response = http_get(
        f"{BASE_URL}/institutions",
        params=_merge_params(query_params),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openalex_search_institutions(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openalex-search-institutions tool call."""
    try:
        params = OpenAlexSearchInstitutionsParams(**(arguments or {}))
        data = fetch_openalex_search_institutions(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching OpenAlex institutions: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openalex-search-institutions",
        description="Search OpenAlex institutions (universities, labs, etc.) by free-text query.",
        input_schema=OpenAlexSearchInstitutionsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openalex-search-institutions"] = handle_openalex_search_institutions


###################
# Search Sources
###################


class OpenAlexSearchSourcesParams(BaseModel):
    """Parameters for searching OpenAlex sources (journals/repositories)."""

    search: str | None = Field(None, description="Free-text search query.")
    per_page: int = Field(default=25, description="Results per page (max 200).")
    filter: str | None = Field(
        None,
        description="Comma-separated OpenAlex filter expression.",
    )


def fetch_openalex_search_sources(params: OpenAlexSearchSourcesParams) -> dict:
    """Call /sources."""
    query_params: dict[str, Any] = {"per_page": params.per_page}
    if params.search:
        query_params["search"] = params.search
    if params.filter:
        query_params["filter"] = params.filter
    response = http_get(
        f"{BASE_URL}/sources",
        params=_merge_params(query_params),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openalex_search_sources(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openalex-search-sources tool call."""
    try:
        params = OpenAlexSearchSourcesParams(**(arguments or {}))
        data = fetch_openalex_search_sources(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching OpenAlex sources: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openalex-search-sources",
        description="Search OpenAlex sources (journals, repositories, conferences) by free-text query.",
        input_schema=OpenAlexSearchSourcesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openalex-search-sources"] = handle_openalex_search_sources


###################
# Search Concepts
###################


class OpenAlexSearchConceptsParams(BaseModel):
    """Parameters for searching OpenAlex concepts."""

    search: str | None = Field(None, description="Free-text search query.")
    per_page: int = Field(default=25, description="Results per page (max 200).")


def fetch_openalex_search_concepts(params: OpenAlexSearchConceptsParams) -> dict:
    """Call /concepts."""
    query_params: dict[str, Any] = {"per_page": params.per_page}
    if params.search:
        query_params["search"] = params.search
    response = http_get(
        f"{BASE_URL}/concepts",
        params=_merge_params(query_params),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openalex_search_concepts(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openalex-search-concepts tool call."""
    try:
        params = OpenAlexSearchConceptsParams(**(arguments or {}))
        data = fetch_openalex_search_concepts(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching OpenAlex concepts: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openalex-search-concepts",
        description="Search OpenAlex concepts (topical taxonomy) by free-text query.",
        input_schema=OpenAlexSearchConceptsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openalex-search-concepts"] = handle_openalex_search_concepts


###################
# Search Publishers
###################


class OpenAlexSearchPublishersParams(BaseModel):
    """Parameters for searching OpenAlex publishers."""

    search: str | None = Field(None, description="Free-text search query.")
    per_page: int = Field(default=25, description="Results per page (max 200).")


def fetch_openalex_search_publishers(params: OpenAlexSearchPublishersParams) -> dict:
    """Call /publishers."""
    query_params: dict[str, Any] = {"per_page": params.per_page}
    if params.search:
        query_params["search"] = params.search
    response = http_get(
        f"{BASE_URL}/publishers",
        params=_merge_params(query_params),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openalex_search_publishers(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openalex-search-publishers tool call."""
    try:
        params = OpenAlexSearchPublishersParams(**(arguments or {}))
        data = fetch_openalex_search_publishers(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching OpenAlex publishers: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openalex-search-publishers",
        description="Search OpenAlex publishers by free-text query.",
        input_schema=OpenAlexSearchPublishersParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openalex-search-publishers"] = handle_openalex_search_publishers


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-openalex",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
