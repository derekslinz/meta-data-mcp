"""Open Library Provider

This module exposes the public Open Library API (hosted by the Internet
Archive). It covers book search, work / edition / author records, ISBN
lookup, and subject feeds.

License / source:
    Open Library data is available under Creative Commons CC0 (public
    domain dedication). Some downstream sources (e.g. cover images) may
    carry their own terms; consult openlibrary.org for details.

Features:
- Book search (`/search.json`)
- Author search (`/search/authors.json`)
- Work record (`/works/{work_id}.json`)
- Edition record (`/books/{edition_id}.json`)
- Author record (`/authors/{author_id}.json`)
- ISBN lookup (`/isbn/{isbn}.json`)
- Subject feed (`/subjects/{subject}.json`)

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
PROVIDER_ID = "global-open-library"
BASE_URL = "https://openlibrary.org"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Search books
###################


class OpenLibrarySearchBooksParams(BaseModel):
    """Parameters for searching Open Library books."""

    title: str | None = Field(
        default=None,
        description="Title fragment to search for.",
    )
    author: str | None = Field(default=None, description="Author name fragment.")
    q: str | None = Field(
        default=None,
        description="Free-text search across all fields.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Results per page (1-100).",
    )
    page: int = Field(default=1, ge=1, description="Page number (1-indexed).")


def fetch_open_library_search_books(params: OpenLibrarySearchBooksParams) -> dict:
    """Search Open Library books."""
    query_params: dict[str, Any] = {"limit": params.limit, "page": params.page}
    if params.title:
        query_params["title"] = params.title
    if params.author:
        query_params["author"] = params.author
    if params.q:
        query_params["q"] = params.q
    response = http_get(
        f"{BASE_URL}/search.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _openlibrary_search_to_shape_payload(data: dict) -> dict:
    """Adapt Open Library ``/search.json`` to the records shape primitive.

    Open Library returns ``{numFound, docs: [...]}`` with each doc carrying
    key (work id), title, author_name (list), first_publish_year, language
    (list), publisher (list), and cover_i.
    """
    raw_rows = data.get("docs", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for doc in raw_rows:
        if not isinstance(doc, dict):
            continue
        authors = doc.get("author_name") or []
        languages = doc.get("language") or []
        publishers = doc.get("publisher") or []
        rows.append(
            {
                "key": doc.get("key"),
                "title": doc.get("title"),
                "authors": ", ".join(a for a in authors if isinstance(a, str))
                if isinstance(authors, list)
                else "",
                "first_publish_year": doc.get("first_publish_year"),
                "languages": ", ".join(la for la in languages if isinstance(la, str))
                if isinstance(languages, list)
                else "",
                "publishers": ", ".join(p for p in publishers[:5] if isinstance(p, str))
                if isinstance(publishers, list)
                else "",
                "edition_count": doc.get("edition_count"),
                "cover_i": doc.get("cover_i"),
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "key", "type": "string", "description": "Open Library key"},
                {"name": "title", "type": "string", "description": "Book title"},
                {"name": "authors", "type": "string", "description": "Authors (csv)"},
                {
                    "name": "first_publish_year",
                    "type": "number",
                    "description": "Earliest publication year",
                },
                {
                    "name": "languages",
                    "type": "string",
                    "description": "Languages (csv)",
                },
                {
                    "name": "publishers",
                    "type": "string",
                    "description": "Publishers (first 5, csv)",
                },
                {
                    "name": "edition_count",
                    "type": "number",
                    "description": "Edition count",
                },
                {"name": "cover_i", "type": "number", "description": "Cover image id"},
            ],
        },
        "default_facets": ["first_publish_year", "languages"],
    }
    if isinstance(data, dict) and "numFound" in data:
        payload["numFound"] = data["numFound"]
    return payload


async def handle_openlibrary_search_books(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openlibrary-search-books tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        params = OpenLibrarySearchBooksParams(**(arguments or {}))
        data = fetch_open_library_search_books(params)
        payload = _openlibrary_search_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching Open Library books: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openlibrary-search-books",
        description="Search Open Library books by title, author, or free-text query.",
        input_schema=OpenLibrarySearchBooksParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["openlibrary-search-books"] = handle_openlibrary_search_books


###################
# Search authors
###################


class OpenLibrarySearchAuthorsParams(BaseModel):
    """Parameters for searching Open Library authors."""

    q: str = Field(..., description="Free-text author search.")
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Results per page (1-100).",
    )


def fetch_open_library_search_authors(
    params: OpenLibrarySearchAuthorsParams,
) -> dict:
    """Search Open Library authors."""
    query_params: dict[str, Any] = {"q": params.q, "limit": params.limit}
    response = http_get(
        f"{BASE_URL}/search/authors.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openlibrary_search_authors(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openlibrary-search-authors tool call."""
    try:
        if not arguments or "q" not in arguments:
            raise ValueError("q is required")
        params = OpenLibrarySearchAuthorsParams(**arguments)
        data = fetch_open_library_search_authors(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching Open Library authors: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openlibrary-search-authors",
        description="Search Open Library authors by free-text query.",
        input_schema=OpenLibrarySearchAuthorsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openlibrary-search-authors"] = handle_openlibrary_search_authors


###################
# Get work
###################


class OpenLibraryGetWorkParams(BaseModel):
    """Parameters for fetching a work record."""

    work_id: str = Field(..., description="Open Library work ID (e.g. 'OL45804W').")


def fetch_open_library_get_work(params: OpenLibraryGetWorkParams) -> dict:
    """Fetch an Open Library work record."""
    response = http_get(f"{BASE_URL}/works/{params.work_id}.json", provider=PROVIDER_ID)
    return response.json()


async def handle_openlibrary_get_work(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openlibrary-get-work tool call."""
    try:
        if not arguments or "work_id" not in arguments:
            raise ValueError("work_id is required")
        params = OpenLibraryGetWorkParams(**arguments)
        data = fetch_open_library_get_work(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Open Library work: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openlibrary-get-work",
        description="Get an Open Library work record by ID (e.g. 'OL45804W').",
        input_schema=OpenLibraryGetWorkParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openlibrary-get-work"] = handle_openlibrary_get_work


###################
# Get edition
###################


class OpenLibraryGetEditionParams(BaseModel):
    """Parameters for fetching an edition record."""

    edition_id: str = Field(
        ...,
        description="Open Library edition ID (e.g. 'OL7353617M').",
    )


def fetch_open_library_get_edition(params: OpenLibraryGetEditionParams) -> dict:
    """Fetch an Open Library edition record."""
    response = http_get(
        f"{BASE_URL}/books/{params.edition_id}.json",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openlibrary_get_edition(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openlibrary-get-edition tool call."""
    try:
        if not arguments or "edition_id" not in arguments:
            raise ValueError("edition_id is required")
        params = OpenLibraryGetEditionParams(**arguments)
        data = fetch_open_library_get_edition(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Open Library edition: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openlibrary-get-edition",
        description="Get an Open Library edition (book) record by ID (e.g. 'OL7353617M').",
        input_schema=OpenLibraryGetEditionParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openlibrary-get-edition"] = handle_openlibrary_get_edition


###################
# Get author
###################


class OpenLibraryGetAuthorParams(BaseModel):
    """Parameters for fetching an author record."""

    author_id: str = Field(..., description="Open Library author ID (e.g. 'OL23919A').")


def fetch_open_library_get_author(params: OpenLibraryGetAuthorParams) -> dict:
    """Fetch an Open Library author record."""
    response = http_get(
        f"{BASE_URL}/authors/{params.author_id}.json",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openlibrary_get_author(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openlibrary-get-author tool call."""
    try:
        if not arguments or "author_id" not in arguments:
            raise ValueError("author_id is required")
        params = OpenLibraryGetAuthorParams(**arguments)
        data = fetch_open_library_get_author(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Open Library author: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openlibrary-get-author",
        description="Get an Open Library author record by ID (e.g. 'OL23919A').",
        input_schema=OpenLibraryGetAuthorParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openlibrary-get-author"] = handle_openlibrary_get_author


###################
# ISBN lookup
###################


class OpenLibraryISBNLookupParams(BaseModel):
    """Parameters for ISBN lookup."""

    isbn: str = Field(
        ...,
        description="ISBN-10 or ISBN-13 (without hyphens or with them).",
    )


def fetch_open_library_isbn_lookup(params: OpenLibraryISBNLookupParams) -> dict:
    """Look up a book by ISBN."""
    response = http_get(f"{BASE_URL}/isbn/{params.isbn}.json", provider=PROVIDER_ID)
    return response.json()


async def handle_openlibrary_isbn_lookup(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openlibrary-isbn-lookup tool call."""
    try:
        if not arguments or "isbn" not in arguments:
            raise ValueError("isbn is required")
        params = OpenLibraryISBNLookupParams(**arguments)
        data = fetch_open_library_isbn_lookup(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error performing ISBN lookup: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openlibrary-isbn-lookup",
        description="Look up an Open Library edition by ISBN.",
        input_schema=OpenLibraryISBNLookupParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openlibrary-isbn-lookup"] = handle_openlibrary_isbn_lookup


###################
# Subject
###################


class OpenLibrarySubjectParams(BaseModel):
    """Parameters for fetching a subject feed."""

    subject: str = Field(
        ...,
        description="Subject slug (e.g. 'love', 'science_fiction').",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Maximum works to return.",
    )


def fetch_open_library_subject(params: OpenLibrarySubjectParams) -> dict:
    """Fetch the subject feed for a subject slug."""
    query_params: dict[str, Any] = {"limit": params.limit}
    response = http_get(
        f"{BASE_URL}/subjects/{params.subject}.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_openlibrary_subject(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the openlibrary-subject tool call."""
    try:
        if not arguments or "subject" not in arguments:
            raise ValueError("subject is required")
        params = OpenLibrarySubjectParams(**arguments)
        data = fetch_open_library_subject(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Open Library subject: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="openlibrary-subject",
        description="Get the Open Library subject feed for a slug (e.g. 'science_fiction').",
        input_schema=OpenLibrarySubjectParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["openlibrary-subject"] = handle_openlibrary_subject


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-open-library",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
