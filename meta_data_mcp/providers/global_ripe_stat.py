"""RIPE NCC RIPEstat Provider

This module exposes RIPE NCC's RIPEstat data API, which provides production-
grade BGP routing data, network information, and Internet routing analytics.

License / source:
    Data is provided by RIPE NCC under their terms of service.
    Most RIPEstat data is available free of charge without authentication.
    Consult https://stat.ripe.net/docs/data_api for details.

Fair-use notes:
    - No API key is required for basic queries.
    - Identify your application by setting META_DATA_MCP_CONTACT.

Features:
- Network info for an IP or prefix (covering ASN and RIR details)
- BGP state (routing table snapshot for a prefix or ASN)
- Prefix overview (origin ASNs, visibility)
- Prefixes announced by an ASN
- Routing history for a prefix or ASN
- Geolocation of an IP address

Usage:
    The module can be run directly to start an MCP server, or its components
    can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.app_network_topology_v1 import (
    URI as NETWORK_TOPOLOGY_URI,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI
from meta_data_mcp.utils import (
    MAX_RESPONSE_CHARS,
    http_get,
    to_entity_graph_text,
    to_geofeatures_text,
    to_json_text,
)

log = logging.getLogger(__name__)

PROVIDER_ID = "global-ripe-stat"
BASE_URL = "https://stat.ripe.net/data"

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Network Info
###################


class RIPEStatNetworkInfoParams(BaseModel):
    """Parameters for fetching network info for an IP or prefix."""

    resource: str = Field(
        ...,
        description=(
            "An IP address, prefix (e.g. '193.0.0.0/21'), or ASN (e.g. 'AS3333'). "
            "Returns the containing prefix and announcing ASN."
        ),
    )


def fetch_ripestat_network_info(params: RIPEStatNetworkInfoParams) -> dict:
    """Fetch network info from RIPEstat."""
    response = http_get(
        f"{BASE_URL}/network-info/data.json",
        params={"resource": params.resource},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ripestat_network_info(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ripestat-network-info tool call."""
    try:
        if not arguments or "resource" not in arguments:
            raise ValueError("resource is required")
        params = RIPEStatNetworkInfoParams(**arguments)
        data = fetch_ripestat_network_info(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching RIPEstat network info: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ripestat-network-info",
        description=(
            "Get the BGP prefix and announcing ASN(s) for an IP address or prefix using RIPE NCC RIPEstat."
        ),
        input_schema=RIPEStatNetworkInfoParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ripestat-network-info"] = handle_ripestat_network_info


###################
# BGP State
###################


class RIPEStatBGPStateParams(BaseModel):
    """Parameters for fetching the current BGP routing table state."""

    resource: str = Field(
        ...,
        description=(
            "An IP prefix (e.g. '193.0.0.0/21') or ASN (e.g. 'AS3333'). "
            "Returns current BGP routing entries from RIPE RIS collectors."
        ),
    )
    rrcs: str | None = Field(
        None,
        description="Comma-separated list of RIPE RIS Route Collectors (e.g. 'rrc00,rrc01'). Defaults to all.",
    )


def fetch_ripestat_bgp_state(params: RIPEStatBGPStateParams) -> dict:
    """Fetch BGP state from RIPEstat."""
    query_params: dict[str, Any] = {"resource": params.resource}
    if params.rrcs:
        query_params["rrcs"] = params.rrcs
    response = http_get(
        f"{BASE_URL}/bgp-state/data.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ripestat_bgp_state(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ripestat-bgp-state tool call."""
    try:
        if not arguments or "resource" not in arguments:
            raise ValueError("resource is required")
        params = RIPEStatBGPStateParams(**arguments)
        data = fetch_ripestat_bgp_state(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching RIPEstat BGP state: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ripestat-bgp-state",
        description=(
            "Get a snapshot of the current BGP routing table state for a prefix or ASN "
            "from RIPE NCC RIS collectors."
        ),
        input_schema=RIPEStatBGPStateParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ripestat-bgp-state"] = handle_ripestat_bgp_state


###################
# Prefix Overview
###################


class RIPEStatPrefixOverviewParams(BaseModel):
    """Parameters for fetching a prefix overview."""

    resource: str = Field(
        ...,
        description="IP prefix (e.g. '193.0.0.0/21'). Returns origin ASNs, visibility, and RIR info.",
    )


def fetch_ripestat_prefix_overview(params: RIPEStatPrefixOverviewParams) -> dict:
    """Fetch prefix overview from RIPEstat."""
    response = http_get(
        f"{BASE_URL}/prefix-overview/data.json",
        params={"resource": params.resource},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ripestat_prefix_overview(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ripestat-prefix-overview tool call."""
    try:
        if not arguments or "resource" not in arguments:
            raise ValueError("resource is required")
        params = RIPEStatPrefixOverviewParams(**arguments)
        data = fetch_ripestat_prefix_overview(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching RIPEstat prefix overview: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ripestat-prefix-overview",
        description=(
            "Get an overview of an IP prefix: originating ASNs, visibility across route "
            "collectors, RIR, and related prefixes."
        ),
        input_schema=RIPEStatPrefixOverviewParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ripestat-prefix-overview"] = handle_ripestat_prefix_overview


###################
# Announced Prefixes
###################


class RIPEStatAnnouncedPrefixesParams(BaseModel):
    """Parameters for listing prefixes announced by an ASN."""

    resource: str = Field(
        ...,
        description="ASN (e.g. 'AS3333' or '3333'). Returns all prefixes originated by this ASN.",
    )
    starttime: str | None = Field(
        None,
        description="Start of the time window (ISO 8601, e.g. '2024-01-01T00:00:00'). Defaults to 2 weeks ago.",
    )
    endtime: str | None = Field(
        None,
        description="End of the time window (ISO 8601). Defaults to now.",
    )
    min_peers_seeing: int | None = Field(
        None,
        description="Minimum number of RIS peers that must have seen a prefix to be included.",
    )


def fetch_ripestat_announced_prefixes(
    params: RIPEStatAnnouncedPrefixesParams,
) -> dict:
    """Fetch announced prefixes for an ASN from RIPEstat."""
    query_params: dict[str, Any] = {"resource": params.resource}
    if params.starttime:
        query_params["starttime"] = params.starttime
    if params.endtime:
        query_params["endtime"] = params.endtime
    if params.min_peers_seeing is not None:
        query_params["min_peers_seeing"] = params.min_peers_seeing
    response = http_get(
        f"{BASE_URL}/announced-prefixes/data.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ripestat_announced_prefixes(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ripestat-announced-prefixes tool call."""
    try:
        if not arguments or "resource" not in arguments:
            raise ValueError("resource is required")
        params = RIPEStatAnnouncedPrefixesParams(**arguments)
        data = fetch_ripestat_announced_prefixes(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching RIPEstat announced prefixes: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ripestat-announced-prefixes",
        description="List all IP prefixes announced by an ASN over a time window.",
        input_schema=RIPEStatAnnouncedPrefixesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ripestat-announced-prefixes"] = handle_ripestat_announced_prefixes


###################
# Routing History
###################


class RIPEStatRoutingHistoryParams(BaseModel):
    """Parameters for fetching routing history."""

    resource: str = Field(
        ...,
        description=(
            "IP prefix (e.g. '193.0.0.0/21') or ASN (e.g. 'AS3333'). "
            "Returns a timeline of routing changes."
        ),
    )
    starttime: str | None = Field(
        None,
        description="Start of the time window (ISO 8601). Defaults to 2 weeks ago.",
    )
    endtime: str | None = Field(
        None,
        description="End of the time window (ISO 8601). Defaults to now.",
    )


def fetch_ripestat_routing_history(params: RIPEStatRoutingHistoryParams) -> dict:
    """Fetch routing history from RIPEstat."""
    query_params: dict[str, Any] = {"resource": params.resource}
    if params.starttime:
        query_params["starttime"] = params.starttime
    if params.endtime:
        query_params["endtime"] = params.endtime
    response = http_get(
        f"{BASE_URL}/routing-history/data.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ripestat_routing_history(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ripestat-routing-history tool call."""
    try:
        if not arguments or "resource" not in arguments:
            raise ValueError("resource is required")
        params = RIPEStatRoutingHistoryParams(**arguments)
        data = fetch_ripestat_routing_history(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching RIPEstat routing history: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ripestat-routing-history",
        description=(
            "Get the routing history (announcements and withdrawals) for an IP prefix or ASN."
        ),
        input_schema=RIPEStatRoutingHistoryParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ripestat-routing-history"] = handle_ripestat_routing_history


###################
# ASN Neighbours
###################


class RIPEStatASNNeighboursParams(BaseModel):
    """Parameters for fetching current BGP neighbours of an ASN."""

    resource: str = Field(
        ...,
        description="ASN (e.g. 'AS3333' or '3333'). Returns current BGP neighbors.",
    )
    starttime: str | None = Field(
        None,
        description="Start of the time window (ISO 8601). Defaults to most recent data.",
    )
    endtime: str | None = Field(
        None,
        description="End of the time window (ISO 8601). Defaults to now.",
    )


def fetch_ripestat_asn_neighbours(params: RIPEStatASNNeighboursParams) -> dict:
    query_params: dict[str, Any] = {"resource": params.resource}
    if params.starttime:
        query_params["starttime"] = params.starttime
    if params.endtime:
        query_params["endtime"] = params.endtime
    response = http_get(
        f"{BASE_URL}/asn-neighbours/data.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _coerce_asn(value: Any) -> int | None:
    """Normalise an ASN-ish value to an int, or ``None`` if it doesn't look
    like one. RIPEstat returns ASNs as numbers, but the resource echoed in
    ``data.resource`` is a string (e.g. ``"3333"`` or ``"AS3333"``).
    """
    if isinstance(value, bool):
        # bool is an int subclass; never an ASN.
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().upper()
        s = s.removeprefix("AS")
        if s.isdigit():
            return int(s)
    return None


# RIPEstat ``/data/asn-neighbours`` uses a directional convention:
#   - "left"      → the neighbour sits upstream of the resource ASN
#                    (i.e. the neighbour is a transit-provider TO us).
#   - "right"     → the neighbour sits downstream (a transit-customer
#                    of the resource ASN).
#   - "peer"      → lateral / settlement-free interconnection.
#   - "uncertain" → relationship not classified by the heuristic.
#
# We project these onto the panel's three-way edge palette (peer /
# upstream / downstream). "uncertain" defaults to "peer" because that's
# the most charitable / least misleading classification — we don't want
# to render a guessed transit hierarchy.
_RIPESTAT_NEIGHBOUR_TYPE_MAP = {
    "left": "upstream",
    "right": "downstream",
    "peer": "peer",
    "uncertain": "peer",
}


def _ripestat_asn_neighbours_to_topology_payload(data: Any, focus: Any) -> dict:
    """Adapt the RIPEstat ``/data/asn-neighbours`` response to the
    ``ui://meta-data-mcp/app/network-topology/v1`` payload contract.

    The response carries ``data.neighbours: [{"asn": <int>, "type":
    "left"|"right"|"peer"|"uncertain", ...}]`` with one entry per
    observed neighbour ASN. The focus ASN itself isn't included in the
    neighbour list — we synthesise it from the caller-supplied
    ``focus`` argument (which is the ``resource`` the tool was called
    with) so the resulting graph is connected.

    Entries that can't be coerced to an integer ASN are dropped silently
    rather than crashing the whole render. Self-edges (neighbour ASN ==
    focus ASN — shouldn't happen but the upstream can be inconsistent)
    are also dropped.
    """
    focus_asn = _coerce_asn(focus)
    asns: list[dict] = []
    edges: list[dict] = []
    seen_asns: set[int] = set()

    if focus_asn is not None:
        asns.append({"asn": focus_asn})
        seen_asns.add(focus_asn)

    if not isinstance(data, dict):
        return {"asns": asns, "edges": edges, "focus_asn": focus_asn}
    neighbours = data.get("data", {}).get("neighbours")
    if not isinstance(neighbours, list):
        return {"asns": asns, "edges": edges, "focus_asn": focus_asn}

    for entry in neighbours:
        if not isinstance(entry, dict):
            continue
        neighbour_asn = _coerce_asn(entry.get("asn"))
        if neighbour_asn is None:
            continue
        if focus_asn is not None and neighbour_asn == focus_asn:
            continue
        if neighbour_asn not in seen_asns:
            asns.append({"asn": neighbour_asn})
            seen_asns.add(neighbour_asn)
        if focus_asn is None:
            # No focus to anchor edges from — skip edge construction.
            continue
        ripe_type = entry.get("type")
        relationship = _RIPESTAT_NEIGHBOUR_TYPE_MAP.get(
            ripe_type if isinstance(ripe_type, str) else "",
            "peer",
        )
        edge: dict[str, Any] = {
            "source_asn": focus_asn,
            "target_asn": neighbour_asn,
            "relationship": relationship,
        }
        # Power (peer count) is RIPEstat's signal strength for the
        # observation. Surface it as edge weight when available so the
        # bundle can vary stroke width.
        power = entry.get("power")
        if isinstance(power, (int, float)) and not isinstance(power, bool):
            edge["weight"] = float(power)
        edges.append(edge)

    return {"asns": asns, "edges": edges, "focus_asn": focus_asn}


async def handle_ripestat_asn_neighbours(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ripestat-asn-neighbours tool call.

    Returns a payload in the
    ``ui://meta-data-mcp/app/network-topology/v1`` contract so the MCP
    Apps host can render the result inline via the bound app panel.
    The original RIPEstat JSON shape is intentionally not surfaced
    here — the bound app reads ``asns`` / ``edges`` / ``focus_asn`` and
    nothing else, and the contract payload still preserves every ASN
    number from the upstream response so LLM callers can reason about
    it textually.
    """
    try:
        if not arguments or "resource" not in arguments:
            raise ValueError("resource is required")
        params = RIPEStatASNNeighboursParams(**arguments)
        data = fetch_ripestat_asn_neighbours(params)
        payload = _ripestat_asn_neighbours_to_topology_payload(data, params.resource)
        return [types.TextContent(type="text", text=to_entity_graph_text(payload))]
    except Exception as e:
        log.error(f"Error fetching RIPEstat ASN neighbours: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ripestat-asn-neighbours",
        description=(
            "Get the current BGP neighbors (peers, upstreams, downstreams) of an ASN "
            "from RIPE NCC RIS. Returns neighbor ASNs grouped by relationship type."
        ),
        input_schema=RIPEStatASNNeighboursParams.model_json_schema(),
        # MCP Apps binding: render via the Phase 5 network-topology app.
        # Use the alias keyword ``_meta=`` — ``meta=`` silently drops into
        # extras; see tests/test_ui_resource.py::
        # test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": NETWORK_TOPOLOGY_URI}},
    ),
)
TOOLS_HANDLERS["ripestat-asn-neighbours"] = handle_ripestat_asn_neighbours


###################
# ASN Neighbours History
###################


class RIPEStatASNNeighboursHistoryParams(BaseModel):
    """Parameters for fetching historical BGP neighbour data for an ASN."""

    resource: str = Field(
        ...,
        description="ASN (e.g. 'AS3333' or '3333'). Returns historical BGP neighbor changes.",
    )
    starttime: str | None = Field(
        None,
        description="Start of the time window (ISO 8601). Defaults to 2 weeks ago.",
    )
    endtime: str | None = Field(
        None,
        description="End of the time window (ISO 8601). Defaults to now.",
    )
    max_rows: int | None = Field(
        None,
        description="Maximum number of result rows to return.",
    )


def fetch_ripestat_asn_neighbours_history(
    params: RIPEStatASNNeighboursHistoryParams,
) -> dict:
    query_params: dict[str, Any] = {"resource": params.resource}
    if params.starttime:
        query_params["starttime"] = params.starttime
    if params.endtime:
        query_params["endtime"] = params.endtime
    if params.max_rows is not None:
        query_params["max_rows"] = params.max_rows
    response = http_get(
        f"{BASE_URL}/asn-neighbours-history/data.json",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ripestat_asn_neighbours_history(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    try:
        if not arguments or "resource" not in arguments:
            raise ValueError("resource is required")
        params = RIPEStatASNNeighboursHistoryParams(**arguments)
        data = fetch_ripestat_asn_neighbours_history(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching RIPEstat ASN neighbours history: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ripestat-asn-neighbours-history",
        description=(
            "Get the historical BGP neighbour relationships for an ASN over a time window. "
            "Shows how peering, transit, and customer relationships changed over time."
        ),
        input_schema=RIPEStatASNNeighboursHistoryParams.model_json_schema(),
        # MCP Apps binding: render via the Phase 5 network-topology app.
        # The handler intentionally returns the raw RIPEstat history
        # response (not a network-topology payload) — the history shape
        # is a time-bucketed series of neighbour sets, and projecting it
        # onto the single-snapshot {asns, edges, focus_asn} contract
        # would silently drop the time dimension. The bound panel
        # currently renders only the latest bucket via the
        # ripestat-asn-neighbours tool; this binding declares intent so
        # a future "evolving topology" payload contract has a place to
        # plug in without re-touching the wire format.
        _meta={"ui": {"resourceUri": NETWORK_TOPOLOGY_URI}},
    ),
)
TOOLS_HANDLERS["ripestat-asn-neighbours-history"] = (
    handle_ripestat_asn_neighbours_history
)


###################
# Geolocation
###################


class RIPEStatGeolocParams(BaseModel):
    """Parameters for geolocating an IP address or prefix."""

    resource: str = Field(
        ...,
        description="IP address or prefix (e.g. '193.0.0.1' or '193.0.0.0/21').",
    )


def fetch_ripestat_geoloc(params: RIPEStatGeolocParams) -> dict:
    """Fetch geolocation data from RIPEstat."""
    response = http_get(
        f"{BASE_URL}/geoloc/data.json",
        params={"resource": params.resource},
        provider=PROVIDER_ID,
    )
    return response.json()


def _ripestat_geoloc_to_shape_payload(data: Any) -> dict:
    """Adapt the RIPEstat ``/data/geoloc`` response to the geofeatures
    payload contract.

    The response carries ``data.locations: [{latitude, longitude, ...}]``
    with one entry per geolocation record found for the resource (an IP
    or prefix may map to multiple cities). Entries lacking usable
    coordinates are dropped silently.
    """
    features: list[dict] = []
    if not isinstance(data, dict):
        return {"features": features}
    locations = data.get("data", {}).get("locations")
    if not isinstance(locations, list):
        return {"features": features}
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        lat_raw = loc.get("latitude")
        lon_raw = loc.get("longitude")
        try:
            lat = float(lat_raw) if lat_raw is not None else None
            lon = float(lon_raw) if lon_raw is not None else None
        except (TypeError, ValueError):
            continue
        if lat is None or lon is None:
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            continue
        attrs = {k: v for k, v in loc.items() if k not in ("latitude", "longitude")}
        features.append({"lat": lat, "lon": lon, "attrs": attrs})
    return {"features": features}


async def handle_ripestat_geoloc(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ripestat-geoloc tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format so the MCP Apps host can render the result inline
    via the bound shape primitive.
    """
    try:
        if not arguments or "resource" not in arguments:
            raise ValueError("resource is required")
        params = RIPEStatGeolocParams(**arguments)
        data = fetch_ripestat_geoloc(params)
        payload = _ripestat_geoloc_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_geofeatures_text(payload))]
    except Exception as e:
        log.error(f"Error fetching RIPEstat geoloc: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ripestat-geoloc",
        description="Get the geolocation (country, coordinates) of an IP address or prefix using RIPE NCC data.",
        input_schema=RIPEStatGeolocParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["ripestat-geoloc"] = handle_ripestat_geoloc


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-ripe-stat",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
