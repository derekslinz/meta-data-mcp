import json
from datetime import UTC, datetime

import pytest

from meta_data_mcp.utils import (
    MAX_RESPONSE_CHARS,
    to_entity_graph_text,
    to_geofeatures_text,
    to_json_text,
    to_records_text,
)


def test_to_json_text_serializes_datetime():
    payload = {"updated_at": datetime(2024, 1, 1, tzinfo=UTC)}
    text = to_json_text(payload)
    assert '"updated_at":"2024-01-01 00:00:00+00:00"' in text


def test_to_json_text_applies_max_chars():
    max_chars = 20
    text = to_json_text({"value": "a" * 100}, max_chars=max_chars)
    assert len(text) <= max_chars
    payload = json.loads(text)
    assert payload["truncated"] is True


def test_to_json_text_sorts_keys():
    text = to_json_text({"b": 2, "a": 1})
    assert text == '{"a":1,"b":2}'


def test_to_json_text_truncation_remains_valid_json():
    source_payload = {"value": "a" * 100}
    low, high = 1, 200
    while low < high:
        mid = (low + high) // 2
        if '"preview"' in to_json_text(source_payload, max_chars=mid):
            high = mid
        else:
            low = mid + 1
    max_chars = low
    text = to_json_text(source_payload, max_chars=max_chars)
    payload = json.loads(text)
    assert payload["truncated"] is True
    assert payload["preview"].startswith("{")


def test_to_json_text_rejects_too_small_max_chars():
    with pytest.raises(ValueError, match="max_chars must be >= 2"):
        to_json_text({"value": "abcdef"}, max_chars=1)


def test_to_geofeatures_text_trims_feature_list_to_valid_json():
    payload = {
        "features": [
            {
                "lat": 1.0,
                "lon": 2.0,
                "attrs": {"name": f"feature-{i}", "blob": "x" * 150},
            }
            for i in range(200)
        ],
    }

    text = to_geofeatures_text(payload, max_chars=MAX_RESPONSE_CHARS)

    assert len(text) <= MAX_RESPONSE_CHARS
    bounded = json.loads(text)
    assert isinstance(bounded["features"], list)
    assert 0 < len(bounded["features"]) < len(payload["features"])


def test_to_geofeatures_text_trims_geojson_feature_collection_to_valid_json():
    payload = {
        "features": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": f"feature-{i}",
                    "geometry": {"type": "Point", "coordinates": [i, i]},
                    "properties": {"blob": "y" * 400},
                }
                for i in range(200)
            ],
        },
    }

    text = to_geofeatures_text(payload, max_chars=MAX_RESPONSE_CHARS)

    assert len(text) <= MAX_RESPONSE_CHARS
    bounded = json.loads(text)
    assert bounded["features"]["type"] == "FeatureCollection"
    assert (
        0 < len(bounded["features"]["features"]) < len(payload["features"]["features"])
    )


def test_to_records_text_passes_through_small_payloads_unchanged():
    payload = {
        "rows": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}],
        "schema": {"columns": [{"name": "id", "type": "number"}]},
        "default_facets": ["name"],
    }
    text = to_records_text(payload, max_chars=MAX_RESPONSE_CHARS)
    parsed = json.loads(text)
    assert parsed["rows"] == payload["rows"]
    assert parsed["schema"] == payload["schema"]
    assert parsed["default_facets"] == payload["default_facets"]


def test_to_records_text_trims_rows_list_to_valid_json():
    """Large records payloads must trim ``rows`` rather than slicing the
    JSON string (which would produce invalid JSON) or replacing the
    payload with a ``{truncated: true, preview: "..."}`` wrapper that
    drops the ``rows`` key entirely (which would leave the records bundle
    rendering empty).
    """
    payload = {
        "rows": [
            {"id": i, "name": f"record-{i}", "blob": "x" * 200} for i in range(500)
        ],
        "schema": {"columns": [{"name": "id", "type": "number"}]},
    }

    text = to_records_text(payload, max_chars=MAX_RESPONSE_CHARS)

    assert len(text) <= MAX_RESPONSE_CHARS
    bounded = json.loads(text)
    assert isinstance(bounded["rows"], list)
    assert 0 < len(bounded["rows"]) < len(payload["rows"])
    # Schema must survive — the bundle reads it for column type hints.
    assert bounded["schema"] == payload["schema"]


def test_to_records_text_empty_rows_remains_valid_shape():
    """Even when the envelope itself is too big to fit (no rows can be
    included), the fallback must still be valid JSON.
    """
    payload = {"rows": [], "schema": {"columns": [{"name": "id"}] * 100}}
    text = to_records_text(payload, max_chars=200)
    parsed = json.loads(text)
    # Either the trimmed shape with empty rows, or the to_json_text
    # truncation fallback — both are valid JSON, that's the contract.
    assert isinstance(parsed, dict)
    assert len(text) <= 200


def test_to_records_text_falls_back_to_json_text_for_non_dict_payload():
    """Defense: if a non-dict accidentally reaches this helper, defer to
    the generic truncator rather than crashing.
    """
    text = to_records_text([1, 2, 3] * 1000, max_chars=50)
    assert len(text) <= 50
    parsed = json.loads(text)
    assert parsed["truncated"] is True


def test_to_entity_graph_text_passes_through_small_payloads_unchanged():
    payload = {
        "nodes": [
            {"id": "a", "label": "A", "type": "author"},
            {"id": "b", "label": "B", "type": "author"},
        ],
        "edges": [{"source": "a", "target": "b", "label": "coauthor"}],
    }
    parsed = json.loads(to_entity_graph_text(payload))
    assert parsed["nodes"] == payload["nodes"]
    assert parsed["edges"] == payload["edges"]


def test_to_entity_graph_text_trims_nodes_and_filters_orphan_edges():
    """Large entity-graph payloads must trim ``nodes`` to a prefix and
    drop any ``edges`` referencing dropped nodes — otherwise the bundle
    would draw edges into thin air.
    """
    payload = {
        "nodes": [
            {
                "id": f"n{i}",
                "label": f"node-{i}",
                "type": "author",
                "attrs": {"x": "y" * 50},
            }
            for i in range(500)
        ],
        "edges": [{"source": f"n{i}", "target": f"n{i + 1}"} for i in range(499)],
    }

    text = to_entity_graph_text(payload, max_chars=MAX_RESPONSE_CHARS)

    assert len(text) <= MAX_RESPONSE_CHARS
    bounded = json.loads(text)
    assert isinstance(bounded["nodes"], list)
    assert 0 < len(bounded["nodes"]) < len(payload["nodes"])
    kept_ids = {n["id"] for n in bounded["nodes"]}
    # Every surviving edge must reference only surviving nodes.
    for edge in bounded["edges"]:
        assert edge["source"] in kept_ids
        assert edge["target"] in kept_ids


def test_to_entity_graph_text_falls_back_to_json_text_when_no_nodes_list():
    """If ``nodes`` is missing or not a list, defer to the generic
    truncator rather than crashing.
    """
    text = to_entity_graph_text({"something": "x" * 50000}, max_chars=200)
    assert len(text) <= 200
    parsed = json.loads(text)
    assert parsed["truncated"] is True
