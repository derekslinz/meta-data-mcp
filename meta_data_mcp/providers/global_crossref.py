"""Crossref Scholarly Metadata Provider

This module exposes the Crossref REST API, the canonical source of DOI
metadata for journal articles, books, datasets, conference proceedings,
and more.

License note:
    Crossref metadata is generally available under CC0 (works metadata) or
    CC-BY (member metadata). Polite-pool usage requires identifying the
    client via User-Agent, which ``http_get`` already provides; the
    contact email can be customised via the ``META_DATA_MCP_CONTACT``
    environment variable.

Features:
- Works search (full-text, author, title) with filters and field selection
- Single-work lookup by DOI
- Journals search and single-journal lookup by ISSN
- Funders search

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.app_entity_graph_v1 import URI as ENTITY_GRAPH_URI
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import (
    http_get,
    serialize_for_llm,
    to_entity_graph_text,
    to_records_text,
)

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-crossref"
BASE_URL = "https://api.crossref.org"

# Records-shape adapter constants
_MAX_ABSTRACT_CHARS = 500

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Works Search
###################


class CrossrefWorksSearchParams(BaseModel):
    """Parameters for searching Crossref /works."""

    query: str | None = Field(
        None,
        description="Free-text search query across all fields.",
    )
    rows: int = Field(default=20, description="Number of results per page (max 1000).")
    offset: int = Field(default=0, description="Offset into the result set.")
    filter: str | None = Field(
        None,
        description="Comma-separated Crossref filter expression (e.g. 'from-pub-date:2020,type:journal-article').",
    )
    select: str | None = Field(
        None,
        description="Comma-separated list of fields to return (e.g. 'DOI,title,author').",
    )
    sort: str | None = Field(
        None,
        description="Sort field (e.g. 'score', 'published', 'is-referenced-by-count').",
    )
    order: str | None = Field(None, description="Sort order ('asc' or 'desc').")


def fetch_crossref_works_search(params: CrossrefWorksSearchParams) -> dict:
    """Call Crossref /works for a general search."""
    query_params: dict[str, Any] = {
        "rows": params.rows,
        "offset": params.offset,
    }
    if params.query:
        query_params["query"] = params.query
    if params.filter:
        query_params["filter"] = params.filter
    if params.select:
        query_params["select"] = params.select
    if params.sort:
        query_params["sort"] = params.sort
    if params.order:
        query_params["order"] = params.order
    response = http_get(f"{BASE_URL}/works", params=query_params, provider=PROVIDER_ID)
    return response.json()


def _crossref_works_to_shape_payload(data: dict) -> dict:
    """Adapt Crossref's ``/works`` response to the records shape primitive.

    Crossref wraps results in ``{status, message: {items: [...], total-results}}``;
    each item carries DOI, title (array), author (array of dicts),
    container-title (array), publisher, type, and a nested published date.
    """
    message = data.get("message") if isinstance(data, dict) else None
    raw_rows = message.get("items", []) if isinstance(message, dict) else []
    rows: list[dict[str, Any]] = []
    for work in raw_rows:
        if not isinstance(work, dict):
            continue
        title_list = work.get("title") or []
        title = title_list[0] if isinstance(title_list, list) and title_list else ""
        authors = work.get("author") or []
        author_names = []
        if isinstance(authors, list):
            for a in authors:
                if isinstance(a, dict):
                    given = a.get("given") or ""
                    family = a.get("family") or a.get("name") or ""
                    name = (f"{given} {family}").strip()
                    if name:
                        author_names.append(name)
        container = work.get("container-title") or []
        container_title = (
            container[0] if isinstance(container, list) and container else ""
        )
        published = (
            work.get("published-print")
            or work.get("published-online")
            or work.get("published")
        )
        published_date = None
        if isinstance(published, dict):
            parts = published.get("date-parts")
            if isinstance(parts, list) and parts and isinstance(parts[0], list):
                published_date = "-".join(
                    f"{int(p):02d}" if len(str(int(p))) == 1 and i > 0 else str(int(p))
                    for i, p in enumerate(parts[0])
                    if isinstance(p, (int, float))
                )
        abstract = work.get("abstract") or ""
        if isinstance(abstract, str) and len(abstract) > _MAX_ABSTRACT_CHARS:
            abstract = abstract[:_MAX_ABSTRACT_CHARS].rstrip() + "…"
        rows.append(
            {
                "DOI": work.get("DOI"),
                "title": title,
                "authors": ", ".join(author_names),
                "container_title": container_title,
                "publisher": work.get("publisher"),
                "type": work.get("type"),
                "published": published_date,
                "is_referenced_by_count": work.get("is-referenced-by-count"),
                "abstract": abstract,
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "DOI", "type": "string", "description": "DOI"},
                {"name": "title", "type": "string", "description": "Work title"},
                {"name": "authors", "type": "string", "description": "Authors (csv)"},
                {
                    "name": "container_title",
                    "type": "string",
                    "description": "Journal/book title",
                },
                {"name": "publisher", "type": "string", "description": "Publisher"},
                {"name": "type", "type": "string", "description": "Work type"},
                {
                    "name": "published",
                    "type": "string",
                    "description": "Publication date (Y-M-D where available)",
                },
                {
                    "name": "is_referenced_by_count",
                    "type": "number",
                    "description": "Citation count",
                },
                {
                    "name": "abstract",
                    "type": "string",
                    "description": "Abstract (truncated, HTML/JATS)",
                },
            ],
        },
        "default_facets": ["type", "publisher", "container_title"],
    }
    if isinstance(message, dict) and "total-results" in message:
        payload["total_results"] = message["total-results"]
    return payload


async def handle_crossref_works_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the crossref-works-search tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = CrossrefWorksSearchParams(**(arguments or {}))
        data = fetch_crossref_works_search(params)
        payload = _crossref_works_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching Crossref works: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="crossref-works-search",
        description="Search Crossref scholarly works with full-text query, filters, sort, and field selection.",
        input_schema=CrossrefWorksSearchParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["crossref-works-search"] = handle_crossref_works_search


###################
# Get Work
###################


class CrossrefGetWorkParams(BaseModel):
    """Parameters for fetching a single Crossref work by DOI."""

    doi: str = Field(..., description="DOI of the work (e.g. '10.1038/nature12373').")


def fetch_crossref_get_work(params: CrossrefGetWorkParams) -> dict:
    """Call Crossref /works/{doi}."""
    # safe="" forces full URL-encoding (including the '/').
    encoded = quote(params.doi, safe="")
    response = http_get(f"{BASE_URL}/works/{encoded}", provider=PROVIDER_ID)
    return response.json()


async def handle_crossref_get_work(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the crossref-get-work tool call."""
    try:
        if not arguments or "doi" not in arguments:
            raise ValueError("doi is required")
        params = CrossrefGetWorkParams(**arguments)
        data = fetch_crossref_get_work(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Crossref work: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="crossref-get-work",
        description="Fetch the full Crossref metadata record for a single DOI.",
        input_schema=CrossrefGetWorkParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["crossref-get-work"] = handle_crossref_get_work


###################
# Works by Author
###################


class CrossrefWorksByAuthorParams(BaseModel):
    """Parameters for searching Crossref works by author."""

    author: str = Field(..., description="Author name to search for.")
    rows: int = Field(default=20, description="Number of results per page.")
    select: str | None = Field(
        None,
        description="Comma-separated list of fields to return (e.g. 'DOI,title,author').",
    )


def fetch_crossref_works_by_author(params: CrossrefWorksByAuthorParams) -> dict:
    """Call Crossref /works with query.author."""
    query_params: dict[str, Any] = {
        "query.author": params.author,
        "rows": params.rows,
    }
    if params.select:
        query_params["select"] = params.select
    response = http_get(f"{BASE_URL}/works", params=query_params, provider=PROVIDER_ID)
    return response.json()


def _crossref_works_by_author_to_entity_graph_payload(
    data: dict,
    query_author: str = "",
) -> dict:
    """Adapt Crossref's ``/works?query.author=...`` response to entity-graph.

    Surfaces the co-authorship overlay called out in the plan: every
    work in the response is a ``work`` node; every author on each
    work is an ``author`` node; an "authored" edge connects each
    work to each of its authors. Authors dedupe across works, which
    is precisely what makes the co-author cluster visible — a prolific
    pair shares N work nodes, producing the visual triangle the layout
    pulls together.

    The provider's ``crossref-works-search`` tool stays on the records
    primitive (it's a flat tabular surface), so this binding doesn't
    override that one — different tool, different shape.
    """
    message = data.get("message") if isinstance(data, dict) else None
    items = message.get("items", []) if isinstance(message, dict) else []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add_node(
        node_id: str,
        label: str,
        ntype: str,
        attrs: dict | None = None,
    ) -> None:
        if not node_id or node_id in seen:
            return
        seen.add(node_id)
        nodes.append(
            {
                "id": node_id,
                "label": label or node_id,
                "type": ntype,
                "attrs": attrs or {},
            },
        )

    # Count how many works each author touched so the layout's link
    # weight can pull frequent authors closer to their works cluster.
    author_work_counts: dict[str, int] = {}

    for work in items:
        if not isinstance(work, dict):
            continue
        doi = work.get("DOI")
        if not doi:
            continue
        work_id = "doi:" + str(doi)
        title_list = work.get("title") or []
        title = (
            title_list[0] if isinstance(title_list, list) and title_list else str(doi)
        )
        container = work.get("container-title") or []
        container_title = (
            container[0] if isinstance(container, list) and container else None
        )
        _add_node(
            work_id,
            str(title),
            "work",
            {
                "DOI": doi,
                "publisher": work.get("publisher"),
                "container_title": container_title,
                "type": work.get("type"),
                "is_referenced_by_count": work.get("is-referenced-by-count"),
            },
        )

        authors = work.get("author") or []
        if not isinstance(authors, list):
            continue
        for author in authors:
            if not isinstance(author, dict):
                continue
            given = author.get("given") or ""
            family = author.get("family") or author.get("name") or ""
            display = (f"{given} {family}").strip() or family or given
            if not display:
                continue
            # Authors don't carry stable ids in /works; use ORCID when
            # present, otherwise a normalized display-name id. Two
            # authors with identical names will collide — that's the
            # expected tradeoff for a co-author overlay.
            author_id = (
                "orcid:" + author.get("ORCID")
                if author.get("ORCID")
                else "name:" + display.lower()
            )
            author_work_counts[author_id] = author_work_counts.get(author_id, 0) + 1
            _add_node(
                author_id,
                display,
                "author",
                {
                    "ORCID": author.get("ORCID"),
                    "affiliation": [
                        a.get("name")
                        for a in (author.get("affiliation") or [])
                        if isinstance(a, dict) and a.get("name")
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

    # Promote author edge weights by works-shared count so the force
    # layout pulls prolific co-authors closer.
    for e in edges:
        if e["label"] == "authored":
            e["weight"] = author_work_counts.get(e["target"], 1)

    return {"nodes": nodes, "edges": edges}


async def handle_crossref_works_by_author(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the crossref-works-by-author tool call.

    Returns the response shaped for the entity-graph app primitive
    (co-author overlay) — works on the query author's publication
    list become a force-directed map of co-authors.
    """
    try:
        if not arguments or "author" not in arguments:
            raise ValueError("author is required")
        params = CrossrefWorksByAuthorParams(**arguments)
        data = fetch_crossref_works_by_author(params)
        payload = _crossref_works_by_author_to_entity_graph_payload(data, params.author)
        return [types.TextContent(type="text", text=to_entity_graph_text(payload))]
    except Exception as e:
        log.error(f"Error searching Crossref works by author: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="crossref-works-by-author",
        description="Search Crossref works whose author field matches the given name.",
        input_schema=CrossrefWorksByAuthorParams.model_json_schema(),
        # MCP Apps binding: co-author overlay via entity-graph. Distinct
        # from ``crossref-works-search`` which renders as the records
        # primitive — different shape, different tool. Use the alias
        # keyword (``_meta=``) — ``meta=`` silently drops into extras.
        _meta={"ui": {"resourceUri": ENTITY_GRAPH_URI}},
    ),
)
TOOLS_HANDLERS["crossref-works-by-author"] = handle_crossref_works_by_author


###################
# Works by Title
###################


class CrossrefWorksByTitleParams(BaseModel):
    """Parameters for searching Crossref works by title."""

    title: str = Field(..., description="Title (or fragment) to search for.")
    rows: int = Field(default=20, description="Number of results per page.")


def fetch_crossref_works_by_title(params: CrossrefWorksByTitleParams) -> dict:
    """Call Crossref /works with query.title."""
    query_params: dict[str, Any] = {
        "query.title": params.title,
        "rows": params.rows,
    }
    response = http_get(f"{BASE_URL}/works", params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_crossref_works_by_title(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the crossref-works-by-title tool call."""
    try:
        if not arguments or "title" not in arguments:
            raise ValueError("title is required")
        params = CrossrefWorksByTitleParams(**arguments)
        data = fetch_crossref_works_by_title(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching Crossref works by title: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="crossref-works-by-title",
        description="Search Crossref works whose title field matches the given query.",
        input_schema=CrossrefWorksByTitleParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["crossref-works-by-title"] = handle_crossref_works_by_title


###################
# Journals Search
###################


class CrossrefJournalsSearchParams(BaseModel):
    """Parameters for searching Crossref /journals."""

    query: str = Field(..., description="Free-text query over journal metadata.")
    rows: int = Field(default=20, description="Number of results per page.")


def fetch_crossref_journals_search(params: CrossrefJournalsSearchParams) -> dict:
    """Call Crossref /journals."""
    query_params: dict[str, Any] = {
        "query": params.query,
        "rows": params.rows,
    }
    response = http_get(
        f"{BASE_URL}/journals",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_crossref_journals_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the crossref-journals-search tool call."""
    try:
        if not arguments or "query" not in arguments:
            raise ValueError("query is required")
        params = CrossrefJournalsSearchParams(**arguments)
        data = fetch_crossref_journals_search(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching Crossref journals: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="crossref-journals-search",
        description="Search Crossref journals by free-text query.",
        input_schema=CrossrefJournalsSearchParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["crossref-journals-search"] = handle_crossref_journals_search


###################
# Get Journal
###################


class CrossrefGetJournalParams(BaseModel):
    """Parameters for fetching a single Crossref journal by ISSN."""

    issn: str = Field(..., description="ISSN of the journal (e.g. '1476-4687').")


def fetch_crossref_get_journal(params: CrossrefGetJournalParams) -> dict:
    """Call Crossref /journals/{issn}."""
    response = http_get(f"{BASE_URL}/journals/{params.issn}", provider=PROVIDER_ID)
    return response.json()


async def handle_crossref_get_journal(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the crossref-get-journal tool call."""
    try:
        if not arguments or "issn" not in arguments:
            raise ValueError("issn is required")
        params = CrossrefGetJournalParams(**arguments)
        data = fetch_crossref_get_journal(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Crossref journal: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="crossref-get-journal",
        description="Fetch the full Crossref metadata record for a single journal by ISSN.",
        input_schema=CrossrefGetJournalParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["crossref-get-journal"] = handle_crossref_get_journal


###################
# Funders Search
###################


class CrossrefFundersSearchParams(BaseModel):
    """Parameters for searching Crossref /funders."""

    query: str = Field(..., description="Free-text query over funder metadata.")
    rows: int = Field(default=20, description="Number of results per page.")


def fetch_crossref_funders_search(params: CrossrefFundersSearchParams) -> dict:
    """Call Crossref /funders."""
    query_params: dict[str, Any] = {
        "query": params.query,
        "rows": params.rows,
    }
    response = http_get(
        f"{BASE_URL}/funders",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_crossref_funders_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the crossref-funders-search tool call."""
    try:
        if not arguments or "query" not in arguments:
            raise ValueError("query is required")
        params = CrossrefFundersSearchParams(**arguments)
        data = fetch_crossref_funders_search(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching Crossref funders: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="crossref-funders-search",
        description="Search Crossref funders by free-text query.",
        input_schema=CrossrefFundersSearchParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["crossref-funders-search"] = handle_crossref_funders_search


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-crossref",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
