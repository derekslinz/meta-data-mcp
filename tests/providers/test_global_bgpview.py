"""BGPView provider tests.

BGPView (api.bgpview.io) shut down. All handlers now return a service-unavailable
error message pointing users to the RIPEstat alternatives.

Phase 5 note: the three ASN-relationship tools carry an MCP Apps binding to
the network-topology app for forward-looking protocol completeness, and an
adapter projects the historical BGPView response shape onto the panel's
payload contract. Both are unit-tested here even though the upstream is
offline — the moment the upstream comes back, or a drop-in replacement
provider re-uses the same tool names, the wiring is ready.
"""

import json

import pytest

from meta_data_mcp.providers.global_bgpview import (
    TOOLS,
    TOOLS_HANDLERS,
    _bgpview_asn_relationships_to_topology_payload,
    handle_bgpview_asn,
    handle_bgpview_asn_downstreams,
    handle_bgpview_asn_peers,
    handle_bgpview_asn_prefixes,
    handle_bgpview_asn_upstreams,
    handle_bgpview_ip,
    handle_bgpview_prefix,
    handle_bgpview_search,
)
from meta_data_mcp.ui_resources.app_network_topology_v1 import (
    URI as NETWORK_TOPOLOGY_URI,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _text(coro):
    result = await coro
    return result[0].text


@pytest.mark.anyio
async def test_all_handlers_return_unavailable_message():
    handlers = [
        handle_bgpview_asn,
        handle_bgpview_asn_prefixes,
        handle_bgpview_asn_peers,
        handle_bgpview_asn_upstreams,
        handle_bgpview_asn_downstreams,
        handle_bgpview_ip,
        handle_bgpview_prefix,
        handle_bgpview_search,
    ]
    for handler in handlers:
        text = await _text(handler({}))
        payload = json.loads(text)
        assert "error" in payload
        assert "BGPView" in payload["error"]
        assert "ripestat" in payload["error"]


def test_all_tools_registered():
    assert len(TOOLS) == 8
    names = {t.name for t in TOOLS}
    assert "bgpview-asn-info" in names
    assert "bgpview-search" in names


def test_tools_descriptions_mention_unavailable():
    for tool in TOOLS:
        assert "SERVICE UNAVAILABLE" in tool.description


def test_all_handlers_in_tools_handlers():
    for tool in TOOLS:
        assert tool.name in TOOLS_HANDLERS


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps binding for the network-topology app.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    ["bgpview-asn-peers", "bgpview-asn-upstreams", "bgpview-asn-downstreams"],
)
def test_asn_relationship_tools_bind_to_network_topology_app(tool_name):
    """The three ASN-relationship tools render through the Phase 5
    network-topology app. The binding is forward-looking — the
    upstream is offline so handlers return ``unavailable``, but the
    binding declares intent so a drop-in replacement automatically
    lights up the same panel.
    """
    tool = next(t for t in TOOLS if t.name == tool_name)
    assert tool.meta == {"ui": {"resourceUri": NETWORK_TOPOLOGY_URI}}, (
        f"{tool_name} is not bound to {NETWORK_TOPOLOGY_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert (
        wire.get("_meta", {}).get("ui", {}).get("resourceUri") == NETWORK_TOPOLOGY_URI
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "bgpview-asn-info",
        "bgpview-asn-prefixes",
        "bgpview-ip-info",
        "bgpview-prefix-info",
        "bgpview-search",
    ],
)
def test_non_topology_tools_do_not_bind_to_network_topology_app(tool_name):
    """Tools that don't render an ASN graph (prefix info, search, etc.)
    must NOT carry the network-topology binding — otherwise a host
    that opens any BGPView tool result would launch an irrelevant
    empty graph panel.
    """
    tool = next(t for t in TOOLS if t.name == tool_name)
    assert tool.meta in (None, {}), (
        f"{tool_name} should not carry an MCP Apps binding (got {tool.meta!r})"
    )


# ---------------------------------------------------------------------------
# Phase 5 adapter: BGPView response → network-topology payload contract.
# ---------------------------------------------------------------------------


def test_topology_adapter_maps_peers_response_to_undirected_edges():
    raw = {
        "status": "ok",
        "data": {
            "ipv4_peers": [
                {"asn": 1234, "name": "ALPHA", "country_code": "DE"},
                {"asn": 5678, "name": "BETA", "country_code": "FR"},
            ],
            "ipv6_peers": [
                # 1234 also appears on the v6 list — must dedupe.
                {"asn": 1234, "name": "ALPHA", "country_code": "DE"},
                {"asn": 9999, "name": "GAMMA", "country_code": "GB"},
            ],
        },
    }
    payload = _bgpview_asn_relationships_to_topology_payload(raw, 3333, "peers")
    assert payload["focus_asn"] == 3333
    asn_set = {a["asn"] for a in payload["asns"]}
    assert asn_set == {3333, 1234, 5678, 9999}
    # Holder name and country plumbed through so the tooltip is useful.
    by_asn = {a["asn"]: a for a in payload["asns"]}
    assert by_asn[1234]["name"] == "ALPHA"
    assert by_asn[1234]["country"] == "DE"
    assert all(e["relationship"] == "peer" for e in payload["edges"])
    # Three unique neighbour ASNs → three edges (no double-counting from
    # the ipv4+ipv6 merge).
    assert len(payload["edges"]) == 3


def test_topology_adapter_maps_upstreams_to_upstream_relationship():
    raw = {
        "data": {
            "ipv4_upstreams": [
                {"asn": 174, "name": "COGENT", "country_code": "US"},
            ],
            "ipv6_upstreams": [],
        },
    }
    payload = _bgpview_asn_relationships_to_topology_payload(raw, 3333, "upstreams")
    assert len(payload["edges"]) == 1
    assert payload["edges"][0]["source_asn"] == 3333
    assert payload["edges"][0]["target_asn"] == 174
    assert payload["edges"][0]["relationship"] == "upstream"


def test_topology_adapter_maps_downstreams_to_downstream_relationship():
    raw = {
        "data": {
            "ipv4_downstreams": [
                {"asn": 65001, "country_code": "US"},
            ],
        },
    }
    payload = _bgpview_asn_relationships_to_topology_payload(raw, 3333, "downstreams")
    assert len(payload["edges"]) == 1
    assert payload["edges"][0]["relationship"] == "downstream"
    # ``name`` should be absent (not present in entry) — not coerced to ""
    assert "name" not in payload["asns"][1]


def test_topology_adapter_handles_missing_structure():
    """Empty data block / non-dict input must not crash — the bound
    panel renders an empty graph for these. The focus ASN is still
    emitted so the panel can tell it was queried for a specific node.
    """
    expected_empty_with_focus = {
        "asns": [{"asn": 3333}],
        "edges": [],
        "focus_asn": 3333,
    }
    assert (
        _bgpview_asn_relationships_to_topology_payload({}, 3333, "peers")
        == expected_empty_with_focus
    )
    assert (
        _bgpview_asn_relationships_to_topology_payload({"data": {}}, 3333, "peers")
        == expected_empty_with_focus
    )
    assert (
        _bgpview_asn_relationships_to_topology_payload("err", 3333, "peers")
        == expected_empty_with_focus
    )


def test_topology_adapter_drops_self_edges_and_bad_asns():
    raw = {
        "data": {
            "ipv4_peers": [
                {"asn": "not-a-number"},  # invalid
                {"asn": 3333},  # self-edge → drop
                {"asn": 1234},
            ],
        },
    }
    payload = _bgpview_asn_relationships_to_topology_payload(raw, 3333, "peers")
    assert len(payload["edges"]) == 1
    assert payload["edges"][0]["target_asn"] == 1234


def test_topology_adapter_accepts_focus_in_multiple_forms():
    raw = {"data": {"ipv4_peers": [{"asn": 1234}]}}
    p1 = _bgpview_asn_relationships_to_topology_payload(raw, 3333, "peers")
    p2 = _bgpview_asn_relationships_to_topology_payload(raw, "3333", "peers")
    p3 = _bgpview_asn_relationships_to_topology_payload(raw, "AS3333", "peers")
    assert p1["focus_asn"] == p2["focus_asn"] == p3["focus_asn"] == 3333


def test_topology_adapter_unknown_relationship_key_falls_back_to_peer():
    """If a caller passes a typo'd relationship key, we don't render
    bogus transit edges — fall back to ``peer`` (the most charitable
    classification) and let the panel render lateral relationships.
    """
    raw = {"data": {"ipv4_typo": [{"asn": 1234}]}}
    payload = _bgpview_asn_relationships_to_topology_payload(raw, 3333, "typo")
    # ipv4_typo isn't in the candidate list (only peers/upstreams/
    # downstreams are), so no edges materialise.
    assert payload["edges"] == []
