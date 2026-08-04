import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_overpass import (
    TOOLS,
    _overpass_elements_to_shape_payload,
    handle_around_amenity,
    handle_bbox_feature,
    handle_query,
    handle_status,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _mock_json_response(mock_get, payload):
    """Configure mock_get to return JSON content-type."""
    mock_get.return_value.json.return_value = payload
    mock_get.return_value.raise_for_status = Mock()
    mock_get.return_value.headers = {"content-type": "application/json; charset=utf-8"}
    mock_get.return_value.text = str(payload)


def _mock_text_response(mock_get, text):
    """Configure mock_get to return plain-text content-type."""
    mock_get.return_value.raise_for_status = Mock()
    mock_get.return_value.headers = {"content-type": "text/plain; charset=utf-8"}
    mock_get.return_value.text = text
    # _run_overpass_query falls back to json() if content-type doesn't match;
    # status uses .text directly, but make json() raise to force fallback.
    mock_get.return_value.json.side_effect = ValueError("not json")


@pytest.mark.anyio
async def test_overpass_query_success():
    with patch("httpx.get") as mock_get:
        _mock_json_response(
            mock_get,
            {"elements": [{"type": "node", "id": 1, "tags": {"amenity": "cafe"}}]},
        )
        result = await handle_query(
            {
                "query": '[out:json];node["amenity"="cafe"](around:500,52.52,13.41);out body;',
            },
        )
        assert "cafe" in result[0].text


@pytest.mark.anyio
async def test_overpass_query_requires_query():
    with pytest.raises(ValueError):
        await handle_query({})


@pytest.mark.anyio
async def test_overpass_query_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_query({"query": "[out:json];out body;"})


@pytest.mark.anyio
async def test_overpass_status_success():
    with patch("httpx.get") as mock_get:
        _mock_text_response(mock_get, "Connected as: 12345\n1 slots available")
        result = await handle_status()
        assert "slots available" in result[0].text


@pytest.mark.anyio
async def test_overpass_around_amenity_success():
    with patch("httpx.get") as mock_get:
        _mock_json_response(
            mock_get,
            {"elements": [{"type": "node", "id": 99, "tags": {"amenity": "cafe"}}]},
        )
        result = await handle_around_amenity(
            {"amenity": "cafe", "lat": 52.52, "lon": 13.41, "radius": 500},
        )
        assert "cafe" in result[0].text
        # Verify the constructed query contains the around clause
        called_kwargs = mock_get.call_args.kwargs
        assert "params" in called_kwargs
        assert "around:500,52.52,13.41" in called_kwargs["params"]["data"]


@pytest.mark.anyio
async def test_overpass_around_amenity_requires_args():
    with pytest.raises(ValueError):
        await handle_around_amenity({"amenity": "cafe"})


@pytest.mark.anyio
async def test_overpass_bbox_feature_success():
    with patch("httpx.get") as mock_get:
        _mock_json_response(
            mock_get,
            {
                "elements": [
                    {
                        "type": "node",
                        "id": 7,
                        "lat": 52.55,
                        "lon": 13.4,
                        "tags": {"highway": "primary"},
                    },
                ],
            },
        )
        result = await handle_bbox_feature(
            {
                "key": "highway",
                "value": "primary",
                "s": 52.5,
                "w": 13.3,
                "n": 52.6,
                "e": 13.5,
            },
        )
        assert "primary" in result[0].text
        called_kwargs = mock_get.call_args.kwargs
        assert "params" in called_kwargs
        # Should have both node and way clauses inside the bbox
        assert '"highway"="primary"' in called_kwargs["params"]["data"]
        assert "52.5,13.3,52.6,13.5" in called_kwargs["params"]["data"]
        assert "out body center;" in called_kwargs["params"]["data"]


@pytest.mark.anyio
async def test_overpass_bbox_feature_requires_args():
    with pytest.raises(ValueError):
        await handle_bbox_feature({"key": "highway", "value": "primary"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for overpass-bbox-feature.
# ---------------------------------------------------------------------------


def test_adapter_maps_node_elements_to_features():
    raw = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 52.52,
                "lon": 13.41,
                "tags": {"amenity": "cafe"},
            },
            {
                "type": "node",
                "id": 2,
                "lat": 48.86,
                "lon": 2.35,
                "tags": {"amenity": "restaurant"},
            },
        ],
    }
    payload = _overpass_elements_to_shape_payload(raw)
    assert len(payload["features"]) == 2
    assert payload["features"][0] == {
        "lat": 52.52,
        "lon": 13.41,
        "attrs": {"type": "node", "id": 1, "tags": {"amenity": "cafe"}},
    }


def test_adapter_uses_center_for_way_elements():
    """Ways/relations carry coords under `center` when OverpassQL uses
    `out center`. The adapter falls back to that block when top-level
    lat/lon are missing.
    """
    raw = {
        "elements": [
            {
                "type": "way",
                "id": 99,
                "center": {"lat": 1.5, "lon": 2.5},
                "tags": {"highway": "primary"},
            },
        ],
    }
    payload = _overpass_elements_to_shape_payload(raw)
    assert payload["features"] == [
        {
            "lat": 1.5,
            "lon": 2.5,
            "attrs": {
                "type": "way",
                "id": 99,
                "tags": {"highway": "primary"},
            },
        },
    ]


def test_adapter_handles_empty_elements():
    payload = _overpass_elements_to_shape_payload({"elements": []})
    assert payload == {"features": []}


def test_adapter_handles_non_dict_response():
    """A plain-text Overpass response (status/error) must produce a valid
    envelope with no features, not crash.
    """
    payload = _overpass_elements_to_shape_payload("error: rate limited")
    assert payload == {"features": []}


def test_adapter_skips_invalid_coords_defensively():
    raw = {
        "elements": [
            {"type": "node", "id": 1, "lat": "bad", "lon": 13.0},
            {"type": "node", "id": 2, "lat": 200.0, "lon": 13.0},  # out of range
            {"type": "node", "id": 3, "lat": 0.0, "lon": -181.0},  # out of range
            {"type": "node", "id": 4},  # missing coords
            {"type": "node", "id": 5, "lat": 0.0, "lon": 0.0},  # valid
        ],
    }
    payload = _overpass_elements_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["id"] == 5


def test_adapter_coerces_string_coords():
    raw = {"elements": [{"type": "node", "id": 1, "lat": "52.5", "lon": "13.4"}]}
    payload = _overpass_elements_to_shape_payload(raw)
    assert payload["features"][0]["lat"] == 52.5
    assert payload["features"][0]["lon"] == 13.4


def test_bbox_feature_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "overpass-bbox-feature")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_overpass_bbox_feature_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        _mock_json_response(
            mock_get,
            {
                "elements": [
                    {
                        "type": "node",
                        "id": 7,
                        "lat": 52.55,
                        "lon": 13.4,
                        "tags": {"highway": "primary"},
                    },
                ],
            },
        )
        result = await handle_bbox_feature(
            {
                "key": "highway",
                "value": "primary",
                "s": 52.5,
                "w": 13.3,
                "n": 52.6,
                "e": 13.5,
            },
        )
        body = json.loads(result[0].text)
        assert "features" in body
        assert body["features"][0]["lat"] == 52.55
        assert body["features"][0]["lon"] == 13.4
        assert body["features"][0]["attrs"]["tags"] == {"highway": "primary"}
