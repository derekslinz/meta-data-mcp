"""global-opensanctions provider.

OpenSanctions — a database of persons and companies of political, criminal,
or economic interest, compiled from over 200 official sources: OFAC SDN,
UN consolidated sanctions, EU consolidated list, UK HMT, national PEP
lists, debarment registers, ICIJ disclosures, and more.

Homepage: https://www.opensanctions.org
API docs: https://www.opensanctions.org/docs/api/
License: Most data is CC-BY 4.0; the API is free for non-commercial
research use (cite "OpenSanctions"). Commercial use requires a license.
Auth: Anonymous public-API tier works; set ``OPENSANCTIONS_API_KEY`` env
var (sent as ``Authorization: ApiKey ...``) for higher rate limits and
the commercial-tier datasets.
"""

import logging
import os
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, ConfigDict, Field

from meta_data_mcp.ui_resources.app_entity_graph_v1 import URI as ENTITY_GRAPH_URI
from meta_data_mcp.utils import (
    create_mcp_server,
    http_get,
    run_server,
    serialize_for_llm,
    to_entity_graph_text,
)

log = logging.getLogger(__name__)

PROVIDER_ID = "global-opensanctions"
BASE_URL = "https://api.opensanctions.org"

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


def _auth_headers() -> dict[str, str]:
    """Send Authorization when OPENSANCTIONS_API_KEY is set."""
    token = os.getenv("OPENSANCTIONS_API_KEY")
    if token:
        return {"Authorization": f"ApiKey {token}"}
    return {}


###################
# opensanctions-search
###################


class OpenSanctionsSearchParams(BaseModel):
    """Parameters for opensanctions-search."""

    query: str = Field(
        ...,
        min_length=1,
        description=(
            "Free-text query — a name, alias, organization, or vessel. "
            "OpenSanctions performs fuzzy matching including transliteration."
        ),
    )
    schema_: str | None = Field(
        None,
        alias="schema",
        description=(
            "Entity schema filter — 'Person', 'Organization', 'Company', "
            "'Vessel', 'Aircraft', 'CryptoWallet', 'Address', 'Security'. "
            "Defaults to all schemas."
        ),
    )
    countries: str | None = Field(
        None,
        description=(
            "ISO 3166-1 alpha-2 country code(s), comma-separated (e.g. "
            "'ru,by'). Filters to entities tagged with these countries."
        ),
    )
    topics: str | None = Field(
        None,
        description=(
            "Topic filter — 'sanction', 'sanction.linked', 'role.pep', "
            "'crime.fin', 'corp.disqual', etc. Comma-separated to combine."
        ),
    )
    dataset: str = Field(
        default="default",
        description=(
            "Dataset slug — 'default' (recommended sanctions+PEP collection), "
            "'sanctions', 'peps', or a specific source slug like 'us_ofac_sdn'."
        ),
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum results to return (1-100).",
    )

    model_config = ConfigDict(populate_by_name=True)


def fetch_opensanctions_search(params: OpenSanctionsSearchParams) -> Any:
    """Search OpenSanctions for matching entities."""
    query: dict[str, Any] = {"q": params.query, "limit": params.limit}
    if params.schema_ is not None:
        query["schema"] = params.schema_
    if params.countries is not None:
        query["countries"] = params.countries
    if params.topics is not None:
        query["topics"] = params.topics
    response = http_get(
        f"{BASE_URL}/search/{params.dataset}",
        params=query,
        headers=_auth_headers() or None,
        provider=PROVIDER_ID,
    )
    return response.json()


def _opensanctions_search_to_entity_graph_payload(data: dict) -> dict:
    """Adapt OpenSanctions ``/search/<dataset>`` response to entity-graph.

    Each result is a top-level entity node typed by its schema (Person,
    Company, Vessel, …) lower-cased and mapped onto the bundle's color
    palette ("entity" is the catch-all). Where a result's ``properties``
    dict contains nested entity references — ``familyMembers``,
    ``associates``, ``directorOf``, etc. — those are emitted as
    secondary nodes with a relationship-typed edge back to the parent.

    Topics (sanction, role.pep, crime.fin, …) are emitted as edge
    labels on the parent's self-edge to surface compliance signal in
    the graph view without inventing a separate "topic" node type
    (which would blow up the node count for popular topic strings).
    """
    results = data.get("results", []) if isinstance(data, dict) else []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _node_type(schema: str | None) -> str:
        if not schema:
            return "entity"
        lower = schema.lower()
        # Persons map to the "author" palette slot for visual contrast
        # against the "entity" catch-all; the *label* still says Person.
        if lower == "person":
            return "author"
        return "entity"

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

    for result in results:
        if not isinstance(result, dict):
            continue
        entity_id = result.get("id")
        if not entity_id:
            continue
        entity_id = str(entity_id)
        schema = result.get("schema")
        caption = result.get("caption") or entity_id
        topics = result.get("topics") or []
        countries = result.get("countries") or []
        datasets = result.get("datasets") or []
        _add_node(
            entity_id,
            str(caption),
            _node_type(schema),
            {
                "schema": schema,
                "topics": topics or None,
                "countries": countries or None,
                "datasets": datasets or None,
                "score": result.get("score"),
                "first_seen": result.get("first_seen"),
                "last_seen": result.get("last_seen"),
            },
        )

        properties = result.get("properties") or {}
        if isinstance(properties, dict):
            for prop_name, values in properties.items():
                if not isinstance(values, list):
                    continue
                for value in values:
                    # Nested entity references show up as dicts with their
                    # own id + caption; scalar property values (strings,
                    # dates) are skipped — they don't carry node identity.
                    if not isinstance(value, dict):
                        continue
                    target_id = value.get("id")
                    if not target_id:
                        continue
                    target_id = str(target_id)
                    target_schema = value.get("schema")
                    target_caption = value.get("caption") or target_id
                    _add_node(
                        target_id,
                        str(target_caption),
                        _node_type(target_schema),
                        {"schema": target_schema},
                    )
                    edges.append(
                        {
                            "source": entity_id,
                            "target": target_id,
                            "label": str(prop_name),
                            "weight": 1,
                        },
                    )

    return {"nodes": nodes, "edges": edges}


async def handle_opensanctions_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opensanctions-search tool call.

    Returns the response shaped for the entity-graph app primitive so
    the bound bundle can render persons/companies and their
    cross-references directly.
    """
    # Use model_validate (not **kwargs) so the 'schema' alias works —
    # Python's keyword-arg unpack will not match the field name 'schema_'.
    params = OpenSanctionsSearchParams.model_validate(arguments or {})
    data = fetch_opensanctions_search(params)
    payload = _opensanctions_search_to_entity_graph_payload(data)
    return [types.TextContent(type="text", text=to_entity_graph_text(payload))]


TOOLS.append(
    types.Tool(
        name="opensanctions-search",
        description=(
            "Search OpenSanctions for persons, companies, vessels, or other "
            "entities that appear on official sanctions lists, PEP registers, "
            "or related risk databases. Supports fuzzy / transliterated name "
            "matching. Returns the entity's schema, topics, countries, and "
            "source references."
        ),
        input_schema=OpenSanctionsSearchParams.model_json_schema(),
        # MCP Apps binding: render persons/companies via entity-graph.
        # Use the alias keyword (``_meta=``) — ``meta=`` silently drops
        # into extras; see tests/test_ui_resource.py.
        _meta={"ui": {"resourceUri": ENTITY_GRAPH_URI}},
    ),
)
TOOLS_HANDLERS["opensanctions-search"] = handle_opensanctions_search


###################
# opensanctions-get-entity
###################


class OpenSanctionsGetEntityParams(BaseModel):
    """Parameters for opensanctions-get-entity."""

    entity_id: str = Field(
        ...,
        min_length=1,
        description=(
            "OpenSanctions entity id (e.g. 'NK-aBcD123' or a 'Q-' Wikidata "
            "id). Returned in the 'id' field of a search result."
        ),
    )


def fetch_opensanctions_get_entity(params: OpenSanctionsGetEntityParams) -> Any:
    """Fetch full structured data for a single OpenSanctions entity."""
    response = http_get(
        f"{BASE_URL}/entities/{params.entity_id}",
        headers=_auth_headers() or None,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_opensanctions_get_entity(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opensanctions-get-entity tool call."""
    params = OpenSanctionsGetEntityParams(**(arguments or {}))
    data = fetch_opensanctions_get_entity(params)
    return [types.TextContent(type="text", text=serialize_for_llm(data))]


TOOLS.append(
    types.Tool(
        name="opensanctions-get-entity",
        description=(
            "Fetch the full structured profile for a single OpenSanctions "
            "entity — properties, sources, related entities, and provenance."
        ),
        input_schema=OpenSanctionsGetEntityParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["opensanctions-get-entity"] = handle_opensanctions_get_entity


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    server = create_mcp_server(
        "global-opensanctions",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )
    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
