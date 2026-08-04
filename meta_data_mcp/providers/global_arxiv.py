"""arXiv Preprint Provider

This module exposes the arXiv export API, which returns Atom XML feeds of
preprint metadata across physics, mathematics, computer science, biology,
quantitative finance, statistics, and more.

License note:
    arXiv metadata is provided for personal and scholarly use. The full text
    of each paper carries the license selected by its authors (often arXiv's
    non-exclusive license to distribute, sometimes CC-BY / CC0). Consult
    each paper's listing before redistribution.

Output format:
    Endpoints return Atom XML, not JSON. Each handler returns the raw text
    truncated to 20,000 characters so consumers can parse the feed
    themselves (e.g. with feedparser or xml.etree.ElementTree).

Features:
- Free-text and field-targeted query (title, author, abstract, category)
- Title and author shortcuts
- Category-scoped feed (e.g. cs.AI, math.NT)
- Direct paper lookup by arXiv id

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_records_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-arxiv"
BASE_URL = "https://export.arxiv.org/api"

# Records-shape adapter constants
_MAX_SUMMARY_CHARS = 500
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

# arXiv responds with Atom XML; ask for it explicitly.
_ATOM_HEADERS = {"Accept": "application/atom+xml"}

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Query
###################


class ArxivQueryParams(BaseModel):
    """Parameters for a generic arXiv search query."""

    search_query: str = Field(
        ...,
        description=(
            "arXiv search expression, e.g. 'all:transformer', 'ti:attention', "
            "'au:hinton AND cat:cs.LG'."
        ),
    )
    start: int = Field(default=0, description="Offset into the result set.")
    max_results: int = Field(
        default=10,
        description="Maximum results to return (max 2000).",
    )
    sortBy: str = Field(
        default="relevance",
        description="Sort field ('relevance', 'lastUpdatedDate', 'submittedDate').",
    )
    sortOrder: str = Field(
        default="descending",
        description="Sort order ('ascending' or 'descending').",
    )


def fetch_arxiv_query(params: ArxivQueryParams) -> str:
    """Call arXiv /query and return the raw Atom XML text."""
    query_params: dict[str, Any] = {
        "search_query": params.search_query,
        "start": params.start,
        "max_results": params.max_results,
        "sortBy": params.sortBy,
        "sortOrder": params.sortOrder,
    }
    response = http_get(
        f"{BASE_URL}/query",
        params=query_params,
        headers=_ATOM_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


def _arxiv_atom_to_shape_payload(xml_text: str) -> dict:
    """Adapt arXiv's Atom XML feed to the
    ``ui://meta-data-mcp/shape/records/v1`` payload.

    Each ``<entry>`` becomes a row with id (arXiv identifier), title,
    authors (csv), primary category, all categories (csv), published,
    updated, and a truncated summary. If parsing fails (malformed XML or
    upstream gave HTML), returns an empty payload defensively rather
    than crashing the handler.
    """
    rows: list[dict[str, Any]] = []
    if not isinstance(xml_text, str) or not xml_text.strip():
        return {
            "rows": rows,
            "schema": _arxiv_schema(),
            "default_facets": _arxiv_facets(),
        }
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        log.warning("arxiv adapter: failed to parse Atom XML; returning empty rows")
        return {
            "rows": rows,
            "schema": _arxiv_schema(),
            "default_facets": _arxiv_facets(),
        }
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title_el = entry.find(f"{_ATOM_NS}title")
        title = (
            " ".join(title_el.text.split())
            if title_el is not None and title_el.text
            else ""
        )
        id_el = entry.find(f"{_ATOM_NS}id")
        arxiv_id = id_el.text if id_el is not None else None
        # Extract just the arxiv id suffix from the URL.
        if isinstance(arxiv_id, str) and "/abs/" in arxiv_id:
            arxiv_id = arxiv_id.rsplit("/abs/", 1)[-1]
        summary_el = entry.find(f"{_ATOM_NS}summary")
        summary = (
            " ".join(summary_el.text.split())
            if summary_el is not None and summary_el.text
            else ""
        )
        if len(summary) > _MAX_SUMMARY_CHARS:
            summary = summary[:_MAX_SUMMARY_CHARS].rstrip() + "…"
        authors = []
        for author in entry.findall(f"{_ATOM_NS}author"):
            name_el = author.find(f"{_ATOM_NS}name")
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())
        primary_cat_el = entry.find(f"{_ARXIV_NS}primary_category")
        primary_category = (
            primary_cat_el.get("term") if primary_cat_el is not None else None
        )
        categories = [
            c.get("term") for c in entry.findall(f"{_ATOM_NS}category") if c.get("term")
        ]
        published_el = entry.find(f"{_ATOM_NS}published")
        updated_el = entry.find(f"{_ATOM_NS}updated")
        rows.append(
            {
                "id": arxiv_id,
                "title": title,
                "authors": ", ".join(authors),
                "primary_category": primary_category,
                "categories": ", ".join(c for c in categories if c),
                "published": published_el.text if published_el is not None else None,
                "updated": updated_el.text if updated_el is not None else None,
                "summary": summary,
            },
        )
    return {
        "rows": rows,
        "schema": _arxiv_schema(),
        "default_facets": _arxiv_facets(),
    }


def _arxiv_schema() -> dict[str, Any]:
    return {
        "columns": [
            {"name": "id", "type": "string", "description": "arXiv identifier"},
            {"name": "title", "type": "string", "description": "Paper title"},
            {"name": "authors", "type": "string", "description": "Authors (csv)"},
            {
                "name": "primary_category",
                "type": "string",
                "description": "arXiv primary category",
            },
            {
                "name": "categories",
                "type": "string",
                "description": "All categories (csv)",
            },
            {
                "name": "published",
                "type": "date",
                "description": "Initial publication timestamp",
            },
            {
                "name": "updated",
                "type": "date",
                "description": "Last-updated timestamp",
            },
            {
                "name": "summary",
                "type": "string",
                "description": "Abstract (truncated)",
            },
        ],
    }


def _arxiv_facets() -> list[str]:
    return ["primary_category"]


async def handle_arxiv_query(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the arxiv-query tool call.

    Returns the response in the records shape primitive payload (parsed
    from the upstream Atom XML feed).
    """
    try:
        if not arguments or "search_query" not in arguments:
            raise ValueError("search_query is required")
        params = ArxivQueryParams(**arguments)
        data = fetch_arxiv_query(params)
        payload = _arxiv_atom_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error querying arXiv: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="arxiv-query",
        description="Run an arXiv search query. Use field prefixes like 'ti:', 'au:', 'cat:'.",
        input_schema=ArxivQueryParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["arxiv-query"] = handle_arxiv_query


###################
# Search by Title
###################


class ArxivSearchByTitleParams(BaseModel):
    """Parameters for an arXiv title-only search."""

    title: str = Field(..., description="Title (or fragment) to search for.")
    max_results: int = Field(default=10, description="Maximum results to return.")


def fetch_arxiv_search_by_title(params: ArxivSearchByTitleParams) -> str:
    """Search arXiv by exact title phrase."""
    query_params: dict[str, Any] = {
        "search_query": f'ti:"{params.title}"',
        "max_results": params.max_results,
    }
    response = http_get(
        f"{BASE_URL}/query",
        params=query_params,
        headers=_ATOM_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_arxiv_search_by_title(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the arxiv-search-by-title tool call."""
    try:
        if not arguments or "title" not in arguments:
            raise ValueError("title is required")
        params = ArxivSearchByTitleParams(**arguments)
        data = fetch_arxiv_search_by_title(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching arXiv by title: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="arxiv-search-by-title",
        description="Search arXiv preprints by title (Atom XML response).",
        input_schema=ArxivSearchByTitleParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["arxiv-search-by-title"] = handle_arxiv_search_by_title


###################
# Search by Author
###################


class ArxivSearchByAuthorParams(BaseModel):
    """Parameters for an arXiv author search."""

    author: str = Field(..., description="Author name to search for.")
    max_results: int = Field(default=10, description="Maximum results to return.")


def fetch_arxiv_search_by_author(params: ArxivSearchByAuthorParams) -> str:
    """Search arXiv by author, sorted by submission date."""
    query_params: dict[str, Any] = {
        "search_query": f'au:"{params.author}"',
        "max_results": params.max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    response = http_get(
        f"{BASE_URL}/query",
        params=query_params,
        headers=_ATOM_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_arxiv_search_by_author(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the arxiv-search-by-author tool call."""
    try:
        if not arguments or "author" not in arguments:
            raise ValueError("author is required")
        params = ArxivSearchByAuthorParams(**arguments)
        data = fetch_arxiv_search_by_author(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching arXiv by author: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="arxiv-search-by-author",
        description="Search arXiv preprints by author, newest first (Atom XML response).",
        input_schema=ArxivSearchByAuthorParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["arxiv-search-by-author"] = handle_arxiv_search_by_author


###################
# Search by Category
###################


class ArxivSearchByCategoryParams(BaseModel):
    """Parameters for an arXiv category feed."""

    category: str = Field(
        ...,
        description="arXiv category slug (e.g. 'cs.AI', 'math.NT', 'q-bio.PE').",
    )
    max_results: int = Field(default=10, description="Maximum results to return.")


def fetch_arxiv_search_by_category(params: ArxivSearchByCategoryParams) -> str:
    """Fetch the most recent papers in an arXiv category."""
    query_params: dict[str, Any] = {
        "search_query": f"cat:{params.category}",
        "max_results": params.max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    response = http_get(
        f"{BASE_URL}/query",
        params=query_params,
        headers=_ATOM_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_arxiv_search_by_category(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the arxiv-search-by-category tool call."""
    try:
        if not arguments or "category" not in arguments:
            raise ValueError("category is required")
        params = ArxivSearchByCategoryParams(**arguments)
        data = fetch_arxiv_search_by_category(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching arXiv by category: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="arxiv-search-by-category",
        description="Fetch the latest arXiv preprints for a category like 'cs.AI' or 'math.NT'.",
        input_schema=ArxivSearchByCategoryParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["arxiv-search-by-category"] = handle_arxiv_search_by_category


###################
# Get Paper
###################


class ArxivGetPaperParams(BaseModel):
    """Parameters for fetching a single arXiv paper by id."""

    arxiv_id: str = Field(
        ...,
        description="arXiv identifier (e.g. '2104.08653' or 'cs.AI/0606009').",
    )


def fetch_arxiv_get_paper(params: ArxivGetPaperParams) -> str:
    """Look up a single arXiv paper by id_list."""
    query_params: dict[str, Any] = {"id_list": params.arxiv_id}
    response = http_get(
        f"{BASE_URL}/query",
        params=query_params,
        headers=_ATOM_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_arxiv_get_paper(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the arxiv-get-paper tool call."""
    try:
        if not arguments or "arxiv_id" not in arguments:
            raise ValueError("arxiv_id is required")
        params = ArxivGetPaperParams(**arguments)
        data = fetch_arxiv_get_paper(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching arXiv paper: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="arxiv-get-paper",
        description="Fetch metadata for a single arXiv paper by its identifier (Atom XML response).",
        input_schema=ArxivGetPaperParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["arxiv-get-paper"] = handle_arxiv_get_paper


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-arxiv",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
