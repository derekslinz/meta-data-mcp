"""Tweedekamer OData v4 Data Provider

This module provides an MCP interface for the Dutch House of Representatives'
OData v4 Gegevensmagazijn API.

Features:
- Discovery of entity sets (Persoon, Fractie, etc.)
- Flexible OData queries using $filter, $select, $top, $skip, $expand

Usage:
    The module can be run directly to start a server handling API requests,
    or its components can be imported and used individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, to_json_text, to_records_text

# Initialize logging
log = logging.getLogger(__name__)

PROVIDER_ID = "nl-tweedekamer"

# Constants
BASE_URL = "https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# Tweedekamer Discovery
###################


def list_tk_entities() -> list[str]:
    """Return a hardcoded list of available entities from Tweedekamer Gegevensmagazijn."""
    # This list is distilled from the $metadata document
    return [
        "Persoon",
        "Commissie",
        "Fractie",
        "Activiteit",
        "Agendapunt",
        "Besluit",
        "Stemming",
        "Zaak",
        "Zaal",
        "Reservering",
        "Document",
        "DocumentActor",
        "DocumentVersie",
        "DocumentPublicatie",
        "Kamerstukdossier",
        "Vergadering",
        "Verslag",
        "Toezegging",
    ]


async def handle_tk_list_entities(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the tk-list-entities tool call."""
    try:
        entities = list_tk_entities()
        return [types.TextContent(type="text", text=to_json_text(entities))]
    except Exception as e:
        log.error(f"Error listing TK entities: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="tk-list-entities",
        description="List all available OData entity sets in the Tweedekamer Gegevensmagazijn.",
        input_schema={"type": "object", "properties": {}},
    ),
)
TOOLS_HANDLERS["tk-list-entities"] = handle_tk_list_entities

###################
# Tweedekamer Querying
###################


class TkQueryEntityParams(BaseModel):
    """Parameters for querying a Tweedekamer entity set using OData v4."""

    entity: str = Field(
        ...,
        description="The name of the entity set (e.g. 'Persoon', 'Fractie')",
    )
    filter: str | None = Field(
        None,
        description="OData $filter string (e.g., \"Achternaam eq 'Dijkhoff'\")",
    )
    select: str | None = Field(
        None,
        description="OData $select string (e.g., 'Id,Voornamen,Achternaam')",
    )
    expand: str | None = Field(
        None,
        description="OData $expand string for related entities (e.g., 'FractieZetelPersoon')",
    )
    top: int = Field(default=20, description="OData $top (limit results)")
    skip: int = Field(default=0, description="OData $skip (offset results)")


def query_tk_entity(params: TkQueryEntityParams) -> dict:
    """Execute an OData v4 query against the Tweedekamer API."""
    # Validate entity against the known whitelist to prevent URL path traversal
    known_entities = set(list_tk_entities())
    if params.entity not in known_entities:
        raise ValueError(
            f"Unknown entity '{params.entity}'. Valid entities: {sorted(known_entities)}",
        )
    url = f"{BASE_URL}/{params.entity}"
    query_params = {
        "$top": params.top,
        "$skip": params.skip,
    }
    if params.filter:
        query_params["$filter"] = params.filter
    if params.select:
        query_params["$select"] = params.select
    if params.expand:
        query_params["$expand"] = params.expand

    response = http_get(
        url,
        params=query_params,
        timeout=10.0,
        provider=PROVIDER_ID,
    )
    return response.json()


def _tk_odata_to_shape_payload(data: dict) -> dict:
    """Adapt an OData v4 entity-set response to the records shape primitive.

    OData v4 returns ``{value: [...], "@odata.count": n, "@odata.nextLink": ...}``.
    We pass the rows through as-is — OData entities are already flat enough
    for the records bundle to infer types and pick facets, and the entity
    set chosen by the caller varies (Persoon, Fractie, Document, etc.).
    """
    raw_rows = data.get("value", []) if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = [r for r in raw_rows if isinstance(r, dict)]
    payload: dict[str, Any] = {"rows": rows}
    if isinstance(data, dict):
        if "@odata.count" in data:
            payload["count"] = data["@odata.count"]
        if "@odata.nextLink" in data:
            payload["next_link"] = data["@odata.nextLink"]
    return payload


async def handle_tk_query(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the tk-query tool call.

    Returns the response in the records shape primitive payload. OData
    entities vary by entity set, so the adapter passes rows through
    untouched and lets the records bundle's auto-inference handle
    column types and facets.
    """
    try:
        if not arguments or "entity" not in arguments:
            raise ValueError("entity name is required")

        params = TkQueryEntityParams(**arguments)
        result = query_tk_entity(params)
        payload = _tk_odata_to_shape_payload(result)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(
            f"Error querying TK entity {arguments.get('entity') if arguments else ''}: {e}",
        )
        raise


TOOLS.append(
    types.Tool(
        name="tk-query",
        description="Query a Tweedekamer entity set using OData v4 parameters.",
        input_schema=TkQueryEntityParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["tk-query"] = handle_tk_query


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "nl-tweedekamer",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


# Server initialization
if __name__ == "__main__":
    import anyio

    anyio.run(main)
