"""BGPView.io Provider — DEPRECATED

BGPView (api.bgpview.io) shut down and is no longer available.
All tools return an error directing users to the ripestat-* alternatives.

Use ripestat-network-info, ripestat-bgp-state, ripestat-announced-prefixes,
ripestat-asn-neighbours, and ripestat-asn-neighbours-history instead.

Phase 5 note: ``bgpview-asn-peers`` / ``bgpview-asn-upstreams`` /
``bgpview-asn-downstreams`` carry an MCP Apps ``_meta`` binding to the
network-topology app for forward-looking protocol completeness. The
binding declares intent: "this tool family lights up the topology
panel". A future drop-in replacement provider that re-uses these tool
names (or a revived BGPView API) will then automatically render
through the topology bundle without re-touching the provider, the
panel, or the wire format. The adapter exists for the same reason —
unit-tested against the historical BGPView response shape — so the
moment the upstream comes back the wiring is ready.
"""

import logging
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.app_network_topology_v1 import (
    URI as NETWORK_TOPOLOGY_URI,
)
from meta_data_mcp.utils import serialize_for_llm

log = logging.getLogger(__name__)

PROVIDER_ID = "global-bgpview"

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

_UNAVAILABLE_MSG = (
    "BGPView (api.bgpview.io) shut down and is no longer available. "
    "Use the RIPE NCC RIPEstat tools instead: "
    "ripestat-network-info, ripestat-bgp-state, ripestat-announced-prefixes, "
    "ripestat-asn-neighbours, ripestat-asn-neighbours-history."
)


def _unavailable() -> list[types.TextContent]:
    return [
        types.TextContent(
            type="text",
            text=serialize_for_llm({"error": _UNAVAILABLE_MSG}),
        ),
    ]


class BGPViewASNParams(BaseModel):
    asn: int = Field(..., description="Autonomous System Number.")


class BGPViewASNPrefixesParams(BaseModel):
    asn: int = Field(..., description="Autonomous System Number.")


class BGPViewASNPeersParams(BaseModel):
    asn: int = Field(..., description="Autonomous System Number.")


class BGPViewASNUpstreamsParams(BaseModel):
    asn: int = Field(..., description="Autonomous System Number.")


class BGPViewASNDownstreamsParams(BaseModel):
    asn: int = Field(..., description="Autonomous System Number.")


class BGPViewIPParams(BaseModel):
    ip: str = Field(..., description="IPv4 or IPv6 address.")


class BGPViewPrefixParams(BaseModel):
    prefix: str = Field(..., description="IP prefix without mask length.")
    cidr: int = Field(..., description="CIDR mask length.")


class BGPViewSearchParams(BaseModel):
    query_term: str = Field(
        ...,
        description="ASN number, network name, IP, or description.",
    )


_DEPRECATED_NOTE = " [SERVICE UNAVAILABLE — use ripestat-* tools instead]"


async def _handle_unavailable(
    arguments: dict[str, Any] | None = None,
) -> list[types.TextContent]:
    return _unavailable()


# Named exports kept for backward compatibility
handle_bgpview_asn = _handle_unavailable
handle_bgpview_asn_prefixes = _handle_unavailable
handle_bgpview_asn_peers = _handle_unavailable
handle_bgpview_asn_upstreams = _handle_unavailable
handle_bgpview_asn_downstreams = _handle_unavailable
handle_bgpview_ip = _handle_unavailable
handle_bgpview_prefix = _handle_unavailable
handle_bgpview_search = _handle_unavailable


# The three ASN-relationship tools carry an MCP Apps binding to the
# network-topology app. The rest don't fit the panel's payload contract
# (a prefix-info or ASN-info call has no graph to render).
_NETWORK_TOPOLOGY_TOOLS = {
    "bgpview-asn-peers",
    "bgpview-asn-upstreams",
    "bgpview-asn-downstreams",
}

for _name, _desc, _schema in [
    ("bgpview-asn-info", "ASN details" + _DEPRECATED_NOTE, BGPViewASNParams),
    (
        "bgpview-asn-prefixes",
        "ASN announced prefixes" + _DEPRECATED_NOTE,
        BGPViewASNPrefixesParams,
    ),
    ("bgpview-asn-peers", "ASN BGP peers" + _DEPRECATED_NOTE, BGPViewASNPeersParams),
    (
        "bgpview-asn-upstreams",
        "ASN upstream providers" + _DEPRECATED_NOTE,
        BGPViewASNUpstreamsParams,
    ),
    (
        "bgpview-asn-downstreams",
        "ASN downstream customers" + _DEPRECATED_NOTE,
        BGPViewASNDownstreamsParams,
    ),
    ("bgpview-ip-info", "IP address BGP info" + _DEPRECATED_NOTE, BGPViewIPParams),
    (
        "bgpview-prefix-info",
        "IP prefix BGP info" + _DEPRECATED_NOTE,
        BGPViewPrefixParams,
    ),
    (
        "bgpview-search",
        "Search ASNs and prefixes" + _DEPRECATED_NOTE,
        BGPViewSearchParams,
    ),
]:
    _tool_kwargs: dict[str, Any] = {
        "name": _name,
        "description": _desc,
        "input_schema": _schema.model_json_schema(),
    }
    if _name in _NETWORK_TOPOLOGY_TOOLS:
        # MCP Apps binding: render via the Phase 5 network-topology app.
        # Use the alias keyword ``_meta=`` — ``meta=`` silently drops
        # into extras; see tests/test_ui_resource.py::
        # test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _tool_kwargs["_meta"] = {"ui": {"resourceUri": NETWORK_TOPOLOGY_URI}}
    TOOLS.append(types.Tool(**_tool_kwargs))
    TOOLS_HANDLERS[_name] = _handle_unavailable


# ---------------------------------------------------------------------------
# Phase 5 adapter — BGPView → network-topology payload contract.
#
# Kept here even though the upstream API is offline: a future replacement
# provider may re-use the BGPView response shape (the data model is the
# obvious one for ASN peer/upstream/downstream queries), and routing
# such a response through this adapter then through the bound panel is
# zero-touch on the day the upstream comes back.
# ---------------------------------------------------------------------------


_BGPVIEW_REL_KEY_TO_RELATIONSHIP = {
    "peers": "peer",
    "upstreams": "upstream",
    "downstreams": "downstream",
}


def _coerce_asn(value: Any) -> int | None:
    """Normalise an ASN-ish value to an int, or ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().upper()
        s = s.removeprefix("AS")
        if s.isdigit():
            return int(s)
    return None


def _bgpview_asn_relationships_to_topology_payload(
    data: Any,
    focus: Any,
    relationship_key: str,
) -> dict:
    """Adapt a BGPView ``/asn/<asn>/{peers,upstreams,downstreams}`` JSON
    response to the
    ``ui://meta-data-mcp/app/network-topology/v1`` payload contract.

    BGPView responses share a top-level shape::

        {
          "status": "ok",
          "data": {
            "ipv4_peers"|"ipv4_upstreams"|"ipv4_downstreams": [
              {"asn": <int>, "name": "...", "country_code": "...", ...}, ...
            ],
            "ipv6_peers"|...: [ ... ]
          }
        }

    The ``relationship_key`` is one of ``"peers"`` / ``"upstreams"`` /
    ``"downstreams"`` and selects the corresponding ipv4_ and ipv6_
    list to merge. The resulting graph is a star — every neighbour ASN
    is connected directly to the focus ASN with the matching
    relationship label.

    Unparseable entries are dropped silently; the function NEVER raises
    on malformed input — the panel renders an empty graph for that.
    """
    focus_asn = _coerce_asn(focus)
    asns: list[dict] = []
    edges: list[dict] = []
    seen_asns: set[int] = set()
    seen_edges: set[tuple[int, int, str]] = set()

    if focus_asn is not None:
        asns.append({"asn": focus_asn})
        seen_asns.add(focus_asn)

    if not isinstance(data, dict):
        return {"asns": asns, "edges": edges, "focus_asn": focus_asn}
    inner = data.get("data")
    if not isinstance(inner, dict):
        return {"asns": asns, "edges": edges, "focus_asn": focus_asn}

    # Unknown relationship keys → no edges (we don't manufacture transit
    # claims from a typo). Bail before scanning so a caller passing
    # "typo" doesn't accidentally pick up an ``ipv4_typo`` list.
    if relationship_key not in _BGPVIEW_REL_KEY_TO_RELATIONSHIP:
        return {"asns": asns, "edges": edges, "focus_asn": focus_asn}
    relationship = _BGPVIEW_REL_KEY_TO_RELATIONSHIP[relationship_key]

    # Merge ipv4_ and ipv6_ lists.
    candidates: list[Any] = []
    for prefix in ("ipv4_", "ipv6_"):
        key = prefix + relationship_key
        sub = inner.get(key)
        if isinstance(sub, list):
            candidates.extend(sub)

    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        neighbour_asn = _coerce_asn(entry.get("asn"))
        if neighbour_asn is None:
            continue
        if focus_asn is not None and neighbour_asn == focus_asn:
            continue
        if neighbour_asn not in seen_asns:
            asn_entry: dict[str, Any] = {"asn": neighbour_asn}
            name = entry.get("name") or entry.get("description")
            if isinstance(name, str) and name:
                asn_entry["name"] = name
            country = entry.get("country_code")
            if isinstance(country, str) and country:
                asn_entry["country"] = country
            asns.append(asn_entry)
            seen_asns.add(neighbour_asn)
        if focus_asn is None:
            continue
        # Dedupe edges across the ipv4+ipv6 merge — same (src, tgt, rel)
        # showing up twice would over-weight the link in the bundle.
        edge_key = (focus_asn, neighbour_asn, relationship)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        edges.append(
            {
                "source_asn": focus_asn,
                "target_asn": neighbour_asn,
                "relationship": relationship,
            },
        )

    return {"asns": asns, "edges": edges, "focus_asn": focus_asn}


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-bgpview",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )
    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
