"""Europe PMC Provider

This module exposes the Europe PMC REST API, a discovery service for life
sciences and biomedical literature. Europe PMC aggregates PubMed,
PubMed Central, Agricola, Patents, and other sources, and offers open-
access full text for many articles.

License note:
    Article metadata is generally redistributable. Full text availability
    and licence depend on the underlying publisher; respect each record's
    licence field before redistributing payload data.

Features:
- Search the bibliographic index with cursor-based pagination
- Single-article retrieval with full record (core resultType)
- References and citations for an article
- Full-text XML download (where open access)
- Supplementary-files listings

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
PROVIDER_ID = "global-europepmc"
BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"

# Records-shape adapter constants
_MAX_ABSTRACT_CHARS = 500

# Europe PMC fullTextXML endpoint returns XML.
_XML_HEADERS = {"Accept": "application/xml"}

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Search
###################


class EuropePmcSearchParams(BaseModel):
    """Parameters for searching Europe PMC."""

    query: str = Field(
        ...,
        description="Europe PMC query string (e.g. 'CRISPR cas9', 'AUTH:\"Doudna J\"').",
    )
    pageSize: int = Field(
        default=25,
        description="Number of records per page (max 1000).",
    )
    cursorMark: str = Field(
        default="*",
        description="Cursor for pagination ('*' starts at the beginning).",
    )
    resultType: str = Field(
        default="lite",
        description="Detail level ('idlist', 'lite', or 'core').",
    )


def fetch_europepmc_search(params: EuropePmcSearchParams) -> dict:
    """Call Europe PMC /search."""
    query_params: dict[str, Any] = {
        "query": params.query,
        "format": "json",
        "pageSize": params.pageSize,
        "cursorMark": params.cursorMark,
        "resultType": params.resultType,
    }
    response = http_get(f"{BASE_URL}/search", params=query_params, provider=PROVIDER_ID)
    return response.json()


def _europepmc_search_to_shape_payload(data: dict) -> dict:
    """Adapt Europe PMC's ``/search`` response to the records shape primitive.

    Europe PMC wraps results in ``{hitCount, nextCursorMark, resultList: {result: [...]}}``;
    each result carries id, source, pmid, doi, title, authorString,
    journalTitle, pubYear, pubType, and isOpenAccess.
    """
    result_list = data.get("resultList") if isinstance(data, dict) else None
    raw_rows = result_list.get("result", []) if isinstance(result_list, dict) else []
    rows: list[dict[str, Any]] = []
    for art in raw_rows:
        if not isinstance(art, dict):
            continue
        abstract = art.get("abstractText") or ""
        if isinstance(abstract, str) and len(abstract) > _MAX_ABSTRACT_CHARS:
            abstract = abstract[:_MAX_ABSTRACT_CHARS].rstrip() + "…"
        rows.append(
            {
                "id": art.get("id"),
                "source": art.get("source"),
                "pmid": art.get("pmid"),
                "doi": art.get("doi"),
                "title": art.get("title"),
                "authors": art.get("authorString"),
                "journal": art.get("journalTitle"),
                "pubYear": art.get("pubYear"),
                "pubType": art.get("pubType"),
                "isOpenAccess": art.get("isOpenAccess"),
                "citedByCount": art.get("citedByCount"),
                "abstract": abstract,
            },
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "id", "type": "string", "description": "Europe PMC id"},
                {
                    "name": "source",
                    "type": "string",
                    "description": "Source database (MED, PMC, etc.)",
                },
                {"name": "pmid", "type": "string", "description": "PubMed id"},
                {"name": "doi", "type": "string", "description": "DOI"},
                {"name": "title", "type": "string", "description": "Article title"},
                {"name": "authors", "type": "string", "description": "Author string"},
                {"name": "journal", "type": "string", "description": "Journal title"},
                {
                    "name": "pubYear",
                    "type": "number",
                    "description": "Publication year",
                },
                {
                    "name": "pubType",
                    "type": "string",
                    "description": "Publication type",
                },
                {
                    "name": "isOpenAccess",
                    "type": "string",
                    "description": "Open-access flag",
                },
                {
                    "name": "citedByCount",
                    "type": "number",
                    "description": "Citation count",
                },
                {
                    "name": "abstract",
                    "type": "string",
                    "description": "Abstract (truncated)",
                },
            ],
        },
        "default_facets": ["source", "pubType", "journal"],
    }
    if isinstance(data, dict):
        if "hitCount" in data:
            payload["hitCount"] = data["hitCount"]
        if "nextCursorMark" in data:
            payload["nextCursorMark"] = data["nextCursorMark"]
    return payload


async def handle_europepmc_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the europepmc-search tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        if not arguments or "query" not in arguments:
            raise ValueError("query is required")
        params = EuropePmcSearchParams(**arguments)
        data = fetch_europepmc_search(params)
        payload = _europepmc_search_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching Europe PMC: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="europepmc-search",
        description="Search Europe PMC biomedical literature with cursor-based pagination.",
        input_schema=EuropePmcSearchParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["europepmc-search"] = handle_europepmc_search


###################
# Get Article
###################


class EuropePmcGetArticleParams(BaseModel):
    """Parameters for fetching a single Europe PMC article."""

    source: str = Field(
        ...,
        description="Source database (e.g. 'MED', 'PMC', 'AGR', 'CBA').",
    )
    id: str = Field(..., description="Article identifier (PMID or PMC id).")


def fetch_europepmc_get_article(params: EuropePmcGetArticleParams) -> dict:
    """Call Europe PMC /article/{source}/{id}."""
    query_params: dict[str, Any] = {
        "resultType": "core",
        "format": "json",
    }
    response = http_get(
        f"{BASE_URL}/article/{params.source}/{params.id}",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_europepmc_get_article(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the europepmc-get-article tool call."""
    try:
        if not arguments or "source" not in arguments or "id" not in arguments:
            raise ValueError("source and id are required")
        params = EuropePmcGetArticleParams(**arguments)
        data = fetch_europepmc_get_article(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Europe PMC article: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="europepmc-get-article",
        description="Fetch a single Europe PMC article (core record) by source and identifier.",
        input_schema=EuropePmcGetArticleParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["europepmc-get-article"] = handle_europepmc_get_article


###################
# References
###################


class EuropePmcReferencesParams(BaseModel):
    """Parameters for retrieving an article's references."""

    source: str = Field(..., description="Source database (e.g. 'MED', 'PMC').")
    id: str = Field(..., description="Article identifier (PMID or PMC id).")
    pageSize: int = Field(default=25, description="Number of references per page.")


def fetch_europepmc_references(params: EuropePmcReferencesParams) -> dict:
    """Call Europe PMC /{source}/{id}/references."""
    query_params: dict[str, Any] = {
        "format": "json",
        "pageSize": params.pageSize,
    }
    response = http_get(
        f"{BASE_URL}/{params.source}/{params.id}/references",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_europepmc_references(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the europepmc-references tool call."""
    try:
        if not arguments or "source" not in arguments or "id" not in arguments:
            raise ValueError("source and id are required")
        params = EuropePmcReferencesParams(**arguments)
        data = fetch_europepmc_references(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Europe PMC references: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="europepmc-references",
        description="Fetch the reference list for a Europe PMC article.",
        input_schema=EuropePmcReferencesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["europepmc-references"] = handle_europepmc_references


###################
# Citations
###################


class EuropePmcCitationsParams(BaseModel):
    """Parameters for retrieving an article's citations."""

    source: str = Field(..., description="Source database (e.g. 'MED', 'PMC').")
    id: str = Field(..., description="Article identifier (PMID or PMC id).")
    pageSize: int = Field(default=25, description="Number of citations per page.")


def fetch_europepmc_citations(params: EuropePmcCitationsParams) -> dict:
    """Call Europe PMC /{source}/{id}/citations."""
    query_params: dict[str, Any] = {
        "format": "json",
        "pageSize": params.pageSize,
    }
    response = http_get(
        f"{BASE_URL}/{params.source}/{params.id}/citations",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_europepmc_citations(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the europepmc-citations tool call."""
    try:
        if not arguments or "source" not in arguments or "id" not in arguments:
            raise ValueError("source and id are required")
        params = EuropePmcCitationsParams(**arguments)
        data = fetch_europepmc_citations(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Europe PMC citations: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="europepmc-citations",
        description="Fetch the list of articles citing a Europe PMC article.",
        input_schema=EuropePmcCitationsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["europepmc-citations"] = handle_europepmc_citations


###################
# Full Text XML
###################


class EuropePmcFullTextXmlParams(BaseModel):
    """Parameters for retrieving full-text XML."""

    source: str = Field(
        ...,
        description="Source database (usually 'PMC' for open-access full text).",
    )
    id: str = Field(..., description="Article identifier (e.g. PMC id).")


def fetch_europepmc_fulltext_xml(params: EuropePmcFullTextXmlParams) -> str:
    """Call Europe PMC /{source}/{id}/fullTextXML and return raw XML."""
    response = http_get(
        f"{BASE_URL}/{params.source}/{params.id}/fullTextXML",
        headers=_XML_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_europepmc_fulltext_xml(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the europepmc-fulltext-xml tool call."""
    try:
        if not arguments or "source" not in arguments or "id" not in arguments:
            raise ValueError("source and id are required")
        params = EuropePmcFullTextXmlParams(**arguments)
        data = fetch_europepmc_fulltext_xml(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Europe PMC full text XML: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="europepmc-fulltext-xml",
        description="Fetch the JATS XML full text for an open-access Europe PMC article.",
        input_schema=EuropePmcFullTextXmlParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["europepmc-fulltext-xml"] = handle_europepmc_fulltext_xml


###################
# Supplementary Files
###################


class EuropePmcSupplementaryFilesParams(BaseModel):
    """Parameters for listing supplementary files."""

    source: str = Field(..., description="Source database (e.g. 'PMC').")
    id: str = Field(..., description="Article identifier (e.g. PMC id).")
    includeInlineImage: bool | None = Field(
        default=True,
        description="Whether to include inline images in the listing.",
    )


def fetch_europepmc_supplementaryfiles(
    params: EuropePmcSupplementaryFilesParams,
) -> dict:
    """Call Europe PMC /{source}/{id}/supplementaryFiles."""
    query_params: dict[str, Any] = {
        "includeInlineImage": "true" if params.includeInlineImage else "false",
    }
    response = http_get(
        f"{BASE_URL}/{params.source}/{params.id}/supplementaryFiles",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_europepmc_supplementaryfiles(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the europepmc-supplementaryfiles tool call."""
    try:
        if not arguments or "source" not in arguments or "id" not in arguments:
            raise ValueError("source and id are required")
        params = EuropePmcSupplementaryFilesParams(**arguments)
        data = fetch_europepmc_supplementaryfiles(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Europe PMC supplementary files: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="europepmc-supplementaryfiles",
        description="List supplementary files attached to a Europe PMC article.",
        input_schema=EuropePmcSupplementaryFilesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["europepmc-supplementaryfiles"] = handle_europepmc_supplementaryfiles


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-europepmc",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
