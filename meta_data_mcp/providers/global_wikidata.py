"""Wikidata Provider

This module exposes Wikidata's public MediaWiki Action API and the Wikidata
Query Service (SPARQL) endpoint. Wikidata is a free, collaboratively edited
multilingual knowledge graph hosted by the Wikimedia Foundation.

License note:
    Wikidata structured data is released under the Creative Commons CC0
    public domain dedication. Some text snippets in entity labels/descriptions
    may carry CC BY-SA. Wikimedia request that callers respect the
    User-Agent and rate-limit policies; this module relies on the project's
    default User-Agent set in meta_data_mcp.utils.http_get.

Features:
- Fetch entity records by Q/P id (wbgetentities)
- Free-text entity search (wbsearchentities)
- Fetch claims/statements on a single entity (wbgetclaims)
- Run arbitrary SPARQL against the Wikidata Query Service
- Search property entities (P-ids) by name
- Resolve an entity from its Wikipedia article title

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.app_entity_graph_v1 import URI as ENTITY_GRAPH_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_entity_graph_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-wikidata"
BASE_URL = "https://www.wikidata.org/w/api.php"
SPARQL_URL = "https://query.wikidata.org/sparql"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# wbgetentities (by ids)
###################


class WikidataGetEntitiesParams(BaseModel):
    """Parameters for fetching Wikidata entities by id."""

    ids: str = Field(
        ...,
        description="Comma-separated list of entity ids, e.g. 'Q42,Q5' or 'P31'.",
    )
    languages: str | None = Field(
        None,
        description="Optional comma-separated language codes to restrict labels/descriptions, e.g. 'en,fr'.",
    )


def fetch_wikidata_entities(params: WikidataGetEntitiesParams) -> dict:
    """Call action=wbgetentities on the Wikidata Action API."""
    query_params: dict[str, Any] = {
        "action": "wbgetentities",
        "ids": params.ids,
        "format": "json",
    }
    if params.languages:
        query_params["languages"] = params.languages
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_wikidata_get_entities(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikidata-get-entities tool call."""
    try:
        if not arguments or "ids" not in arguments:
            raise ValueError("ids is required")
        params = WikidataGetEntitiesParams(**arguments)
        data = fetch_wikidata_entities(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Wikidata entities: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikidata-get-entities",
        description="Fetch one or more Wikidata entities by Q/P id (action=wbgetentities).",
        input_schema=WikidataGetEntitiesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikidata-get-entities"] = handle_wikidata_get_entities


###################
# wbsearchentities (items)
###################


class WikidataSearchEntitiesParams(BaseModel):
    """Parameters for free-text entity search on Wikidata."""

    search: str = Field(..., description="Free-text query string.")
    language: str = Field(default="en", description="Search language code.")
    limit: int = Field(default=10, description="Maximum number of results.")


def fetch_wikidata_search_entities(params: WikidataSearchEntitiesParams) -> dict:
    """Call action=wbsearchentities (type=item) on the Wikidata Action API."""
    query_params: dict[str, Any] = {
        "action": "wbsearchentities",
        "search": params.search,
        "language": params.language,
        "type": "item",
        "limit": params.limit,
        "format": "json",
    }
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


def _wikidata_search_to_entity_graph_payload(data: dict, query: str = "") -> dict:
    """Adapt Wikidata's ``wbsearchentities`` response to entity-graph.

    ``wbsearchentities`` returns a flat list of matches. Without an
    extra round-trip for each result's claims there's no real edge
    structure to surface, so the adapter emits a "hub-and-spoke"
    graph: a synthetic ``anchor`` node representing the query string,
    with a "matches" edge to every result. This still gives the bundle
    something meaningful to lay out and makes the result set visually
    rank-orderable (concept-rich queries fan out further).
    """
    results = data.get("search", []) if isinstance(data, dict) else []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    anchor_id = "wd-query::" + (query or "search")
    nodes.append(
        {
            "id": anchor_id,
            "label": "Search: " + (query or ""),
            "type": "anchor",
            "attrs": {"query": query, "source": "wikidata-search-entities"},
        },
    )

    for idx, entry in enumerate(results):
        if not isinstance(entry, dict):
            continue
        entity_id = entry.get("id") or entry.get("concepturi")
        if not entity_id:
            continue
        entity_id = str(entity_id)
        if entity_id in seen:
            continue
        seen.add(entity_id)
        label = entry.get("label") or entry.get("title") or entity_id
        nodes.append(
            {
                "id": entity_id,
                "label": str(label),
                "type": "entity",
                "attrs": {
                    "description": entry.get("description"),
                    "concepturi": entry.get("concepturi"),
                    "url": entry.get("url"),
                    "match": entry.get("match"),
                },
            },
        )
        # Edge weight degrades with rank so the layout pulls the top
        # match toward the anchor — visually communicates relevance.
        edges.append(
            {
                "source": anchor_id,
                "target": entity_id,
                "label": "matches",
                "weight": max(0.25, 1.0 - (idx * 0.05)),
            },
        )

    return {"nodes": nodes, "edges": edges}


async def handle_wikidata_search_entities(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikidata-search-entities tool call.

    Returns the response shaped for the entity-graph app primitive so
    the bound bundle can render the result set as a fan-out from the
    query.
    """
    try:
        if not arguments or "search" not in arguments:
            raise ValueError("search is required")
        params = WikidataSearchEntitiesParams(**arguments)
        data = fetch_wikidata_search_entities(params)
        payload = _wikidata_search_to_entity_graph_payload(data, params.search)
        return [types.TextContent(type="text", text=to_entity_graph_text(payload))]
    except Exception as e:
        log.error(f"Error searching Wikidata entities: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikidata-search-entities",
        description="Free-text search for Wikidata item entities (Q-ids).",
        input_schema=WikidataSearchEntitiesParams.model_json_schema(),
        # MCP Apps binding: render via entity-graph app as a fan-out
        # from the query anchor. Use the alias keyword (``_meta=``).
        _meta={"ui": {"resourceUri": ENTITY_GRAPH_URI}},
    ),
)
TOOLS_HANDLERS["wikidata-search-entities"] = handle_wikidata_search_entities


###################
# wbgetclaims
###################


class WikidataGetClaimsParams(BaseModel):
    """Parameters for fetching claims on a Wikidata entity."""

    entity: str = Field(..., description="Entity id, e.g. 'Q42'.")
    property: str | None = Field(
        None,
        description="Optional property id to filter to a single property, e.g. 'P31'.",
    )


def fetch_wikidata_claims(params: WikidataGetClaimsParams) -> dict:
    """Call action=wbgetclaims on the Wikidata Action API."""
    query_params: dict[str, Any] = {
        "action": "wbgetclaims",
        "entity": params.entity,
        "format": "json",
    }
    if params.property:
        query_params["property"] = params.property
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_wikidata_get_claims(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikidata-get-claims tool call."""
    try:
        if not arguments or "entity" not in arguments:
            raise ValueError("entity is required")
        params = WikidataGetClaimsParams(**arguments)
        data = fetch_wikidata_claims(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Wikidata claims: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikidata-get-claims",
        description="Fetch claims (statements) for a Wikidata entity, optionally filtered by property.",
        input_schema=WikidataGetClaimsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikidata-get-claims"] = handle_wikidata_get_claims


###################
# SPARQL
###################


class WikidataSPARQLParams(BaseModel):
    """Parameters for running a SPARQL query against the Wikidata Query Service."""

    query: str = Field(..., description="SPARQL query to execute.")


def fetch_wikidata_sparql(params: WikidataSPARQLParams) -> dict:
    """Run a SPARQL query against https://query.wikidata.org/sparql."""
    response = http_get(
        SPARQL_URL,
        params={"query": params.query},
        headers={"Accept": "application/sparql-results+json"},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_wikidata_sparql(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikidata-sparql tool call."""
    try:
        if not arguments or "query" not in arguments:
            raise ValueError("query is required")
        params = WikidataSPARQLParams(**arguments)
        data = fetch_wikidata_sparql(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error running Wikidata SPARQL: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikidata-sparql",
        description="Run a SPARQL query against the Wikidata Query Service.",
        input_schema=WikidataSPARQLParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikidata-sparql"] = handle_wikidata_sparql


###################
# wbsearchentities (properties)
###################


class WikidataListPropertiesParams(BaseModel):
    """Parameters for searching Wikidata property entities (P-ids)."""

    search: str = Field(..., description="Free-text query for the property name.")
    language: str = Field(default="en", description="Search language code.")
    limit: int = Field(default=10, description="Maximum number of results.")


def fetch_wikidata_list_properties(params: WikidataListPropertiesParams) -> dict:
    """Call action=wbsearchentities (type=property) on the Wikidata Action API."""
    query_params: dict[str, Any] = {
        "action": "wbsearchentities",
        "search": params.search,
        "language": params.language,
        "type": "property",
        "limit": params.limit,
        "format": "json",
    }
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_wikidata_list_properties(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikidata-list-properties tool call."""
    try:
        if not arguments or "search" not in arguments:
            raise ValueError("search is required")
        params = WikidataListPropertiesParams(**arguments)
        data = fetch_wikidata_list_properties(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching Wikidata properties: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikidata-list-properties",
        description="Search Wikidata property entities (P-ids) by name.",
        input_schema=WikidataListPropertiesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikidata-list-properties"] = handle_wikidata_list_properties


###################
# wbgetentities by Wikipedia title
###################


class WikidataGetByTitleParams(BaseModel):
    """Parameters for resolving a Wikidata entity from a Wikipedia article title."""

    titles: str = Field(
        ...,
        description="Pipe-separated Wikipedia article titles, e.g. 'Albert Einstein|Marie Curie'.",
    )
    sites: str = Field(
        default="enwiki",
        description="Wikipedia site id, e.g. 'enwiki', 'frwiki'.",
    )


def fetch_wikidata_get_by_title(params: WikidataGetByTitleParams) -> dict:
    """Call action=wbgetentities with sites+titles on the Wikidata Action API."""
    query_params: dict[str, Any] = {
        "action": "wbgetentities",
        "sites": params.sites,
        "titles": params.titles,
        "format": "json",
    }
    response = http_get(BASE_URL, params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_wikidata_get_by_title(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikidata-get-entity-by-title tool call."""
    try:
        if not arguments or "titles" not in arguments:
            raise ValueError("titles is required")
        params = WikidataGetByTitleParams(**arguments)
        data = fetch_wikidata_get_by_title(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Wikidata entity by Wikipedia title: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikidata-get-entity-by-title",
        description="Resolve Wikidata entities from Wikipedia article titles (sites+titles).",
        input_schema=WikidataGetByTitleParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikidata-get-entity-by-title"] = handle_wikidata_get_by_title


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-wikidata",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
