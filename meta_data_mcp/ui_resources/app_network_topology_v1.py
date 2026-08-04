"""``ui://meta-data-mcp/app/network-topology/v1`` — Phase 5 ASN graph app.

Force-directed visualisation of BGP/ASN-level network topology.
Synthesises data from two providers:

- **RIPE NCC RIPEstat** (``global_ripe_stat``) — authoritative source
  of BGP neighbour relationships. ``ripestat-asn-neighbours`` returns
  the current peer/upstream/downstream ASNs for any focus ASN;
  ``ripestat-asn-neighbours-history`` returns the same data over a
  time window. The unrelated ``ripestat-geoloc`` tool is bound to the
  Phase 4 geofeatures shape primitive and is NOT touched by this app.
- **BGPView.io** (``global_bgpview``) — historically a complementary
  source for peers/upstreams/downstreams. The upstream API is offline,
  but the binding remains for protocol completeness so a future
  drop-in replacement provider lights up the same panel automatically.

Payload contract — host pushes a JSON object of this shape into the
panel via the standard MCP Apps postMessage envelope::

    {
      "asns":  [{"asn": <int>, "name": "...", "country": "...",
                 "rank": <number>?}],
      "edges": [{"source_asn": <int>, "target_asn": <int>,
                 "relationship": "peer"|"upstream"|"downstream",
                 "weight": <number>?}],
      "focus_asn": <int>?
    }

``focus_asn`` (when present) is rendered with a thicker stroke + larger
radius and is pinned at the centre of the viewport so the layout stays
stable across click-to-expand merges. Edges fall back to ``peer`` if
their declared ``relationship`` isn't one of the three canonical values.

Interactions:

- Click an ASN node → fires an ``app→host`` ``tool_call`` for
  ``ripestat-asn-neighbours`` with that ASN; new nodes/edges merge into
  the existing graph (no full re-render of unaffected layout).
- Hover an ASN node → tooltip with AS number, holder, country, rank,
  and current degree (number of incident edges).
- ``Load`` button → typed ASN focus reset + initial neighbour fetch.
- ``Reset`` button → clears the graph back to idle.

D3.js v7 (loaded via jsdelivr CDN) is the only dependency: the force
simulation, drag, and zoom primitives are not worth reimplementing
in-bundle for this app. The rest of the apps in this repo are
dependency-free; D3 is justified here because rolling our own physics
sim would inflate the bundle far past the 100KB Phase 6b budget for no
UX gain.

postMessage protocol matches the Phase 3 discovery app envelope — see
``app_discovery_v1.py`` for the full description.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files

from mcp import types

from meta_data_mcp.utils import register_ui_resource

URI: str = "ui://meta-data-mcp/app/network-topology/v1"

# Surfaced in the host's resource catalog. Documents the bidirectional
# postMessage protocol AND the payload contract so a host integrator can
# wire app↔host without reading the bundle source. The provider list
# (ripe_stat, bgpview) is included so a host knows which tool families
# light up the panel.
_DESCRIPTION = (
    "Network topology app: force-directed ASN graph with peer / upstream / "
    "downstream edges. Wraps ripestat-asn-neighbours, "
    "ripestat-asn-neighbours-history (RIPE NCC RIPEstat) and "
    "bgpview-asn-peers, bgpview-asn-upstreams, bgpview-asn-downstreams "
    "(BGPView, currently offline — binding kept for protocol completeness). "
    "Payload contract: {asns: [{asn, name?, country?, rank?}], "
    "edges: [{source_asn, target_asn, relationship: peer|upstream|downstream, "
    "weight?}], focus_asn?}. D3.js v7 via jsdelivr (only the force layout). "
    "postMessage protocol — "
    "host→app: {type: 'tool_result'|'render', id?, tool?, payload}; "
    "app→host: {type: 'tool_call', id, name, arguments} (Phase 3 invention)."
)

_HTML: str = (
    files("meta_data_mcp.ui_resources") / "app_network_topology_v1.html"
).read_text(encoding="utf-8")


def register(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> str:
    """Append the network-topology app resource to the server's catalog.

    Returns the canonical URI so callers (and tests) can assert against it
    without duplicating the constant.
    """
    return register_ui_resource(
        name="app/network-topology/v1",
        html=_HTML,
        description=_DESCRIPTION,
        resources=resources,
        resources_handlers=resources_handlers,
    )
