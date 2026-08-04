import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_ripe_stat import (
    TOOLS,
    _ripestat_asn_neighbours_to_topology_payload,
    _ripestat_geoloc_to_shape_payload,
    handle_ripestat_announced_prefixes,
    handle_ripestat_asn_neighbours,
    handle_ripestat_asn_neighbours_history,
    handle_ripestat_bgp_state,
    handle_ripestat_geoloc,
    handle_ripestat_network_info,
    handle_ripestat_prefix_overview,
    handle_ripestat_routing_history,
)
from meta_data_mcp.ui_resources.app_network_topology_v1 import (
    URI as NETWORK_TOPOLOGY_URI,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_ripestat_network_info_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "193.0.0.0",
                "asns": ["3333"],
                "prefix": "193.0.0.0/21",
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_network_info({"resource": "193.0.0.1"})
        assert "193.0.0.0/21" in result[0].text
        assert "3333" in result[0].text


@pytest.mark.anyio
async def test_ripestat_network_info_missing_param():
    with pytest.raises(ValueError):
        await handle_ripestat_network_info({})


@pytest.mark.anyio
async def test_ripestat_network_info_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Timeout")
        with pytest.raises(httpx.HTTPError):
            await handle_ripestat_network_info({"resource": "193.0.0.1"})


@pytest.mark.anyio
async def test_ripestat_bgp_state_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "193.0.0.0/21",
                "routes": [{"prefix": "193.0.0.0/21", "origin": "AS3333"}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_bgp_state({"resource": "193.0.0.0/21"})
        assert "AS3333" in result[0].text


@pytest.mark.anyio
async def test_ripestat_bgp_state_with_rrcs():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {"routes": []},
        }
        mock_get.return_value.raise_for_status = Mock()

        await handle_ripestat_bgp_state(
            {"resource": "193.0.0.0/21", "rrcs": "rrc00,rrc01"},
        )
        call_params = mock_get.call_args.kwargs.get("params", {})
        assert call_params.get("rrcs") == "rrc00,rrc01"


@pytest.mark.anyio
async def test_ripestat_prefix_overview_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "193.0.0.0/21",
                "asns": [{"asn": 3333, "holder": "RIPE-NCC-AS"}],
                "visibility": {"v4": {"observed_neighbours": 200}},
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_prefix_overview({"resource": "193.0.0.0/21"})
        assert "RIPE-NCC-AS" in result[0].text


@pytest.mark.anyio
async def test_ripestat_announced_prefixes_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "AS3333",
                "prefixes": [
                    {"prefix": "193.0.0.0/21", "timelines": []},
                    {"prefix": "2001:67c:2e8::/48", "timelines": []},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_announced_prefixes({"resource": "AS3333"})
        assert "193.0.0.0/21" in result[0].text


@pytest.mark.anyio
async def test_ripestat_routing_history_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "193.0.0.0/21",
                "by_origin": [{"origin": "AS3333", "prefixes": []}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_routing_history({"resource": "193.0.0.0/21"})
        assert "AS3333" in result[0].text


@pytest.mark.anyio
async def test_ripestat_geoloc_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "193.0.0.1",
                "locations": [
                    {
                        "country": "NL",
                        "city": "Amsterdam",
                        "latitude": 52.37,
                        "longitude": 4.9,
                        "resources": ["193.0.0.1"],
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_geoloc({"resource": "193.0.0.1"})
        assert "Amsterdam" in result[0].text
        assert "NL" in result[0].text


@pytest.mark.anyio
async def test_ripestat_geoloc_missing_param():
    with pytest.raises(ValueError):
        await handle_ripestat_geoloc({})


@pytest.mark.anyio
async def test_ripestat_asn_neighbours_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "AS3333",
                "neighbours": [
                    {"asn": 1234, "type": "left"},
                    {"asn": 5678, "type": "right"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_asn_neighbours({"resource": "AS3333"})
        assert "1234" in result[0].text
        assert "5678" in result[0].text


@pytest.mark.anyio
async def test_ripestat_asn_neighbours_missing_param():
    with pytest.raises(ValueError):
        await handle_ripestat_asn_neighbours({})


@pytest.mark.anyio
async def test_ripestat_asn_neighbours_history_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "AS3333",
                "neighbours_series": [
                    {
                        "starttime": "2024-01-01T00:00:00",
                        "endtime": "2024-01-07T00:00:00",
                        "neighbours": [{"asn": 1234, "type": "left"}],
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_asn_neighbours_history({"resource": "AS3333"})
        assert "neighbours_series" in result[0].text
        assert "1234" in result[0].text


@pytest.mark.anyio
async def test_ripestat_asn_neighbours_history_with_params():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"status": "ok", "data": {}}
        mock_get.return_value.raise_for_status = Mock()

        await handle_ripestat_asn_neighbours_history(
            {"resource": "AS3333", "starttime": "2024-01-01T00:00:00", "max_rows": 100},
        )
        call_params = mock_get.call_args.kwargs.get("params", {})
        assert call_params.get("starttime") == "2024-01-01T00:00:00"
        assert call_params.get("max_rows") == 100


@pytest.mark.anyio
async def test_ripestat_asn_neighbours_history_missing_param():
    with pytest.raises(ValueError):
        await handle_ripestat_asn_neighbours_history({})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for ripestat-geoloc.
# ---------------------------------------------------------------------------


def test_adapter_maps_locations_to_features():
    raw = {
        "status": "ok",
        "data": {
            "resource": "193.0.0.1",
            "locations": [
                {
                    "country": "NL",
                    "city": "Amsterdam",
                    "latitude": 52.37,
                    "longitude": 4.9,
                    "resources": ["193.0.0.1"],
                },
                {
                    "country": "DE",
                    "city": "Frankfurt",
                    "latitude": 50.11,
                    "longitude": 8.68,
                    "resources": ["193.0.0.2"],
                },
            ],
        },
    }
    payload = _ripestat_geoloc_to_shape_payload(raw)
    assert len(payload["features"]) == 2
    assert payload["features"][0]["lat"] == 52.37
    assert payload["features"][0]["lon"] == 4.9
    assert payload["features"][0]["attrs"]["city"] == "Amsterdam"
    assert payload["features"][0]["attrs"]["country"] == "NL"
    # Coordinate keys stripped from attrs (already promoted)
    assert "latitude" not in payload["features"][0]["attrs"]


def test_adapter_handles_empty_locations():
    raw = {"data": {"resource": "x", "locations": []}}
    assert _ripestat_geoloc_to_shape_payload(raw) == {"features": []}


def test_adapter_handles_missing_structure():
    """Empty data block or missing locations key must not crash."""
    assert _ripestat_geoloc_to_shape_payload({}) == {"features": []}
    assert _ripestat_geoloc_to_shape_payload({"data": {}}) == {"features": []}
    assert _ripestat_geoloc_to_shape_payload("err") == {"features": []}


def test_adapter_skips_locations_without_coords():
    raw = {
        "data": {
            "locations": [
                {"country": "NL", "city": "no coords"},
                {"latitude": "bad", "longitude": 4.9, "city": "bad lat"},
                {"latitude": 200.0, "longitude": 4.9, "city": "out of range"},
                {"latitude": 52.37, "longitude": 4.9, "city": "ok"},
            ],
        },
    }
    payload = _ripestat_geoloc_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["city"] == "ok"


def test_geoloc_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "ripestat-geoloc")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_ripestat_geoloc_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "193.0.0.1",
                "locations": [
                    {
                        "country": "NL",
                        "city": "Amsterdam",
                        "latitude": 52.37,
                        "longitude": 4.9,
                        "resources": ["193.0.0.1"],
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_geoloc({"resource": "193.0.0.1"})
        body = json.loads(result[0].text)
        assert body["features"][0]["lat"] == 52.37
        assert body["features"][0]["lon"] == 4.9
        assert body["features"][0]["attrs"]["city"] == "Amsterdam"
        assert body["features"][0]["attrs"]["country"] == "NL"


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps binding for the network-topology app.
#
# The asn-neighbours tool is the canonical lighting-up signal for the
# ASN graph panel — these tests pin both the wire-level binding and the
# adapter that projects RIPEstat's neighbour list onto the panel's
# {asns, edges, focus_asn} payload contract.
#
# We deliberately do NOT re-test the ripestat-geoloc binding here (it
# lives above) and must NOT accidentally regress it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    ["ripestat-asn-neighbours", "ripestat-asn-neighbours-history"],
)
def test_asn_neighbours_tools_bind_to_network_topology_app(tool_name):
    """Both ASN-neighbour tools render through the Phase 5 network-
    topology app. The binding's wire-level alias (``_meta``) is what
    the host reads — populate_by_name isn't enabled on the SDK Tool
    model so ``meta=`` would silently drop into extras.
    """
    tool = next(t for t in TOOLS if t.name == tool_name)
    assert tool.meta == {"ui": {"resourceUri": NETWORK_TOPOLOGY_URI}}, (
        f"{tool_name} is not bound to {NETWORK_TOPOLOGY_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert (
        wire.get("_meta", {}).get("ui", {}).get("resourceUri") == NETWORK_TOPOLOGY_URI
    )


def test_geoloc_binding_unchanged_by_phase5():
    """Belt-and-braces regression — the Phase 4 geofeatures binding on
    ripestat-geoloc must NOT be clobbered by the Phase 5 network-
    topology work. If a future refactor unifies the binding tables and
    accidentally re-points geoloc at the topology app, this fires
    immediately.
    """
    tool = next(t for t in TOOLS if t.name == "ripestat-geoloc")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    assert tool.meta != {"ui": {"resourceUri": NETWORK_TOPOLOGY_URI}}


def test_topology_adapter_maps_neighbours_to_directed_edges():
    raw = {
        "status": "ok",
        "data": {
            "resource": "3333",
            "neighbours": [
                {"asn": 1234, "type": "left", "power": 60},
                {"asn": 5678, "type": "right", "power": 40},
                {"asn": 9999, "type": "peer"},
                {"asn": 2222, "type": "uncertain"},
            ],
        },
    }
    payload = _ripestat_asn_neighbours_to_topology_payload(raw, "AS3333")
    # Focus ASN is materialised from the ``resource`` argument and is the
    # FIRST node so the panel can find it without scanning the array.
    assert payload["focus_asn"] == 3333
    assert payload["asns"][0]["asn"] == 3333
    asn_set = {a["asn"] for a in payload["asns"]}
    assert asn_set == {3333, 1234, 5678, 9999, 2222}

    edges_by_target = {e["target_asn"]: e for e in payload["edges"]}
    assert edges_by_target[1234]["relationship"] == "upstream"
    assert edges_by_target[1234]["source_asn"] == 3333
    assert edges_by_target[1234]["weight"] == 60.0
    assert edges_by_target[5678]["relationship"] == "downstream"
    assert edges_by_target[9999]["relationship"] == "peer"
    # "uncertain" RIPEstat type falls back to "peer" — we don't want to
    # surface a guessed transit hierarchy.
    assert edges_by_target[2222]["relationship"] == "peer"


def test_topology_adapter_handles_empty_neighbours():
    raw = {"status": "ok", "data": {"resource": "3333", "neighbours": []}}
    payload = _ripestat_asn_neighbours_to_topology_payload(raw, "AS3333")
    assert payload["focus_asn"] == 3333
    # Focus ASN still emitted even with zero neighbours — the panel
    # should still mark "this ASN was queried, just has no neighbour
    # data" rather than rendering an empty graph.
    assert payload["asns"] == [{"asn": 3333}]
    assert payload["edges"] == []


def test_topology_adapter_handles_missing_structure():
    """Empty data block / missing neighbours key / non-dict input must
    not crash — the panel renders an empty graph for these.
    """
    expected_empty_with_focus = {
        "asns": [{"asn": 3333}],
        "edges": [],
        "focus_asn": 3333,
    }
    assert (
        _ripestat_asn_neighbours_to_topology_payload({}, "AS3333")
        == expected_empty_with_focus
    )
    assert (
        _ripestat_asn_neighbours_to_topology_payload({"data": {}}, "AS3333")
        == expected_empty_with_focus
    )
    assert (
        _ripestat_asn_neighbours_to_topology_payload("err", "AS3333")
        == expected_empty_with_focus
    )


def test_topology_adapter_drops_unparseable_and_self_edges():
    raw = {
        "data": {
            "neighbours": [
                {"asn": "not-a-number", "type": "left"},
                {"asn": 3333, "type": "peer"},  # self-edge — must be dropped
                {"type": "left"},  # missing asn — must be dropped
                {"asn": 1234, "type": "left"},
            ],
        },
    }
    payload = _ripestat_asn_neighbours_to_topology_payload(raw, "3333")
    # Only the (3333 → 1234) edge survives the filters.
    assert len(payload["edges"]) == 1
    assert payload["edges"][0]["source_asn"] == 3333
    assert payload["edges"][0]["target_asn"] == 1234


def test_topology_adapter_handles_focus_without_as_prefix():
    raw = {"data": {"neighbours": [{"asn": 1234, "type": "left"}]}}
    # Bare-int and "3333" both must be recognised as ASN 3333.
    p1 = _ripestat_asn_neighbours_to_topology_payload(raw, 3333)
    p2 = _ripestat_asn_neighbours_to_topology_payload(raw, "3333")
    p3 = _ripestat_asn_neighbours_to_topology_payload(raw, "AS3333")
    assert p1["focus_asn"] == p2["focus_asn"] == p3["focus_asn"] == 3333


@pytest.mark.anyio
async def test_asn_neighbours_handler_returns_topology_payload():
    """End-to-end: handler should emit network-topology contract JSON
    (not the raw RIPEstat envelope) so the bound app can render the
    result inline without bundle-side reshape logic.
    """
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "data": {
                "resource": "3333",
                "neighbours": [
                    {"asn": 1234, "type": "left"},
                    {"asn": 5678, "type": "right"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ripestat_asn_neighbours({"resource": "AS3333"})
        body = json.loads(result[0].text)
        # Contract keys present.
        assert set(body.keys()) >= {"asns", "edges", "focus_asn"}
        assert body["focus_asn"] == 3333
        # Original ASN numbers preserved end-to-end.
        asn_values = {a["asn"] for a in body["asns"]}
        assert {3333, 1234, 5678}.issubset(asn_values)
        # Edge relationships are the panel's three-way palette, not the
        # raw RIPEstat "left"/"right" terms.
        relationships = {e["relationship"] for e in body["edges"]}
        assert relationships.issubset({"peer", "upstream", "downstream"})
        assert "left" not in result[0].text
        assert "right" not in result[0].text
