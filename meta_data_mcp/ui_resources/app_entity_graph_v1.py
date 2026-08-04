"""``ui://meta-data-mcp/app/entity-graph/v1`` — Phase 5 entity-graph app.

Renders a force-directed graph of entities and their relationships
from four independent providers:

- **OpenAlex** (``global_openalex``) — work↔author↔concept edges for
  scholarly research; doubles as the co-author overlay surface called
  out in ``Plans/linear-swimming-pond.md`` §5.
- **Wikidata** (``global_wikidata``) — item entities returned from a
  free-text search; each result is a labelled node hanging off a
  central "anchor" node that represents the query itself.
- **OpenSanctions** (``global_opensanctions``) — persons / companies /
  vessels and their related-entity cross-references.
- **Crossref** (``global_crossref``) — co-author overlay rooted at the
  named author (work↔author edges). ``crossref-works-search`` is
  intentionally NOT rebound here — it remains on the records primitive
  per Phase 4; ``crossref-works-by-author`` is the entity-graph hook.

Payload contract (provider → app via the host's ``tool_result``):

    {
      "nodes": [
        {"id": "...", "label": "...", "type": "author|work|entity|...",
         "attrs": {...optional metadata...}}
      ],
      "edges": [
        {"source": "<node-id>", "target": "<node-id>",
         "label": "...", "weight": <optional number>}
      ],
      "facets": [...]   // optional, currently unused
    }

Each provider ships a small ``_to_entity_graph_payload(...)`` adapter
that flattens its native response into ``nodes`` + ``edges``. The
bundle never sees raw upstream JSON — it sees a uniform shape
regardless of which provider populated it.

Bundle stack: D3.js v7 force-directed graph, loaded from
``cdn.jsdelivr.net`` (the canonical D3 CDN). The bundle degrades to a
flat node list if the CDN is unreachable so a CSP block or offline
environment never produces a blank screen.

postMessage envelope is inherited from the Phase 3 discovery app and
the Phase 5 vulnerability app — see those modules for the bidirectional
description.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files

from mcp import types

from meta_data_mcp.utils import register_ui_resource

URI: str = "ui://meta-data-mcp/app/entity-graph/v1"

# Surfaced in the host's resource catalog. Documents the payload contract
# inline so a host integrator can wire app↔host without reading the
# bundle source.
_DESCRIPTION = (
    "Entity-graph app: force-directed graph of entities and their "
    "relationships across four providers — OpenAlex (works/authors), "
    "Wikidata (search results), OpenSanctions (persons/companies), "
    "Crossref (co-author overlay via crossref-works-by-author). "
    "Payload contract: {nodes:[{id,label,type,attrs}], "
    "edges:[{source,target,label,weight?}]}. "
    "D3.js v7 force layout via CDN; degrades to flat list if D3 blocked. "
    "postMessage protocol — "
    "host→app: {type: 'tool_result'|'render', id?, tool?, payload}; "
    "app→host: {type: 'tool_call', id, name, arguments}."
)

_HTML: str = (
    files("meta_data_mcp.ui_resources") / "app_entity_graph_v1.html"
).read_text(encoding="utf-8")


def register(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> str:
    """Append the entity-graph app resource to the server's catalog.

    Returns the canonical URI so callers (and tests) can assert against it
    without duplicating the constant.
    """
    return register_ui_resource(
        name="app/entity-graph/v1",
        html=_HTML,
        description=_DESCRIPTION,
        resources=resources,
        resources_handlers=resources_handlers,
    )
