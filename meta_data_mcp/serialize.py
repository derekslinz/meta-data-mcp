"""Tool-result serializers.

Two families:

- ``serialize_for_llm`` / ``to_json_text`` — generic JSON serializers.
  ``serialize_for_llm`` truncates by mid-string slice (legacy, kept for
  back-compat in tools that don't bind to an MCP Apps shape).
  ``to_json_text`` produces valid JSON in every code path by wrapping the
  payload in a ``{"truncated": true, "preview": "..."}`` envelope on
  overflow.
- ``to_records_text`` / ``to_geofeatures_text`` / ``to_entity_graph_text``
  — shape-bound serializers. They preserve the payload contract by
  binary-search-trimming the relevant list (rows / features / nodes) to
  the largest prefix that still fits within ``max_chars``. The MCP Apps
  bundles parse with ``JSON.parse``; invalid JSON makes them render
  empty, so these helpers are the single source of truth for "shaped
  payload, size-bounded, host can always parse".

Module split out of ``utils.py`` in the v2.1 hygiene pass (architecture
review §H1). ``meta_data_mcp.utils`` re-exports every public symbol so
all existing call sites keep working.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

# Maximum character length for tool/resource text responses. Kept here so
# the serializers form a self-contained module; the rest of the codebase
# re-exports it via ``meta_data_mcp.utils``.
MAX_RESPONSE_CHARS = 20_000


def serialize_for_llm(data: Any) -> str:
    """Serialize ``data`` to a JSON string truncated to ``MAX_RESPONSE_CHARS``.

    Uses ``json.dumps`` so that LLMs receive valid JSON (``true``/``false``,
    ``null``, double-quoted keys) instead of Python's ``repr`` output.

    ``default=str`` is used as a fallback serializer for types that are not
    natively JSON-serializable (e.g. ``datetime``, ``UUID``).
    """
    return json.dumps(data, default=str, ensure_ascii=False)[:MAX_RESPONSE_CHARS]


def _json_dumps(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )


def to_json_text(payload: Any, max_chars: int | None = None) -> str:
    """Serialize data to deterministic JSON text for MCP responses."""
    text = _json_dumps(payload)
    if max_chars is None or len(text) <= max_chars:
        return text
    if max_chars < 2:
        raise ValueError("max_chars must be >= 2")

    truncated_payload = {
        "truncated": True,
        "original_length": len(text),
        "max_chars": max_chars,
        "preview": text,
    }
    truncated_text = _json_dumps(truncated_payload)
    if len(truncated_text) <= max_chars:
        return truncated_text

    preview = text
    while preview:
        truncated_payload["preview"] = preview
        truncated_text = _json_dumps(truncated_payload)
        if len(truncated_text) <= max_chars:
            return truncated_text
        preview = preview[:-1]

    minimal_truncated_payload = {
        "truncated": True,
        "original_length": len(text),
        "max_chars": max_chars,
    }
    minimal_truncated_text = _json_dumps(minimal_truncated_payload)
    if len(minimal_truncated_text) <= max_chars:
        return minimal_truncated_text

    # Ordered from most informative to smallest object-shaped JSON to preserve
    # context while still honoring strict max_chars limits.
    for fallback_payload in ({"truncated": True}, {}):
        fallback = _json_dumps(fallback_payload)
        if len(fallback) <= max_chars:
            return fallback

    raise ValueError("max_chars is too small for a valid JSON object fallback")


def _max_prefix_json_text(
    items: Sequence[Any],
    build_payload: Callable[[Sequence[Any]], Any],
    max_chars: int,
) -> str | None:
    """Return the largest prefix payload that still fits within ``max_chars``."""
    low = 0
    high = len(items)
    best_text: str | None = None
    while low <= high:
        mid = (low + high) // 2
        candidate_text = _json_dumps(build_payload(items[:mid]))
        if len(candidate_text) <= max_chars:
            best_text = candidate_text
            low = mid + 1
        else:
            high = mid - 1
    return best_text


def to_records_text(payload: Any, max_chars: int = MAX_RESPONSE_CHARS) -> str:
    """Serialize a records-shape payload while preserving valid shape JSON.

    Mirrors :func:`to_geofeatures_text` for the
    ``ui://meta-data-mcp/shape/records/v1`` envelope. If the serialized
    payload exceeds ``max_chars``, the ``rows`` list is trimmed to the
    largest prefix that still fits while keeping the records contract
    intact — i.e. the result is always a valid JSON object with
    ``rows`` (possibly empty) plus the original ``schema`` /
    ``default_facets`` metadata.

    Why this exists instead of ``serialize_for_llm``: the records bundle
    parses the response with ``JSON.parse``; a truncated-by-slicing JSON
    string raises and the table renders empty. Why this exists instead of
    ``to_json_text(max_chars=...)``: that helper replaces the payload with
    ``{"truncated": true, "preview": "..."}`` when over budget, which
    drops the ``rows`` key and again leaves the bundle with nothing to
    render.

    Returns valid JSON within ``max_chars`` in every code path. Falls
    back to ``to_json_text`` only when the payload is not a records-shape
    dict (the dict envelope itself doesn't have a ``rows`` list).
    """
    text = _json_dumps(payload)
    if len(text) <= max_chars:
        return text
    if not isinstance(payload, dict):
        return to_json_text(payload, max_chars=max_chars)

    rows = payload.get("rows")
    if isinstance(rows, list):
        bounded_text = _max_prefix_json_text(
            rows,
            lambda bounded_rows: {**payload, "rows": list(bounded_rows)},
            max_chars,
        )
        if bounded_text is not None:
            return bounded_text

    return to_json_text(payload, max_chars=max_chars)


def to_geofeatures_text(payload: Any, max_chars: int = MAX_RESPONSE_CHARS) -> str:
    """Serialize a geofeatures payload while preserving valid shape JSON.

    If the payload exceeds ``max_chars``, this trims the feature list to the
    largest prefix that still fits while keeping the geofeatures contract
    intact. Supports both option B payloads
    (``{"features": [{lat, lon, attrs}, ...]}``) and option A native GeoJSON
    payloads (``{"features": {"type": "FeatureCollection", "features": [...]}}``).
    Falls back to ``to_json_text`` only when the payload is not recognized as a
    geofeatures envelope.
    """
    text = _json_dumps(payload)
    if len(text) <= max_chars:
        return text
    if not isinstance(payload, dict):
        return to_json_text(payload, max_chars=max_chars)

    features = payload.get("features")
    if isinstance(features, list):
        bounded_text = _max_prefix_json_text(
            features,
            lambda bounded_features: {**payload, "features": list(bounded_features)},
            max_chars,
        )
        if bounded_text is not None:
            return bounded_text
        return to_json_text(payload, max_chars=max_chars)

    if (
        isinstance(features, dict)
        and features.get("type") == "FeatureCollection"
        and isinstance(features.get("features"), list)
    ):
        collection_features = features["features"]
        bounded_text = _max_prefix_json_text(
            collection_features,
            lambda bounded_features: {
                **payload,
                "features": {**features, "features": list(bounded_features)},
            },
            max_chars,
        )
        if bounded_text is not None:
            return bounded_text

    return to_json_text(payload, max_chars=max_chars)


def to_entity_graph_text(payload: Any, max_chars: int = MAX_RESPONSE_CHARS) -> str:
    """Serialize an entity-graph payload while preserving valid shape JSON.

    Mirrors :func:`to_records_text` / :func:`to_geofeatures_text` for the
    ``ui://meta-data-mcp/app/entity-graph/v1`` and
    ``ui://meta-data-mcp/app/network-topology/v1`` envelopes
    (``{"nodes": [...], "edges": [...]}``). When the serialized payload
    exceeds ``max_chars``, trims ``nodes`` to the largest prefix that
    still fits, then drops any ``edges`` referencing dropped nodes so
    the graph stays internally consistent.

    Why: ``serialize_for_llm`` truncates by mid-string slice, producing
    invalid JSON that the host can't parse and the bundle can't render.
    ``to_json_text(max_chars=...)`` produces valid JSON but wraps the
    payload in ``{"truncated": true, "preview": "..."}``, dropping the
    ``nodes``/``edges`` keys the bundle needs.

    Falls back to ``to_json_text`` only when the payload is not a
    graph-shape dict.
    """
    text = _json_dumps(payload)
    if len(text) <= max_chars:
        return text
    if not isinstance(payload, dict):
        return to_json_text(payload, max_chars=max_chars)

    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list):
        return to_json_text(payload, max_chars=max_chars)

    edge_list: list[Any] = edges if isinstance(edges, list) else []

    def _build(bounded_nodes: Sequence[Any]) -> dict[str, Any]:
        kept_ids = {
            n.get("id")
            for n in bounded_nodes
            if isinstance(n, dict) and n.get("id") is not None
        }
        bounded_edges = [
            e
            for e in edge_list
            if isinstance(e, dict)
            and e.get("source") in kept_ids
            and e.get("target") in kept_ids
        ]
        result = {**payload, "nodes": list(bounded_nodes)}
        if isinstance(edges, list):
            result["edges"] = bounded_edges
        return result

    bounded_text = _max_prefix_json_text(nodes, _build, max_chars)
    if bounded_text is not None:
        return bounded_text

    return to_json_text(payload, max_chars=max_chars)


__all__ = [
    "MAX_RESPONSE_CHARS",
    "serialize_for_llm",
    "to_entity_graph_text",
    "to_geofeatures_text",
    "to_json_text",
    "to_records_text",
]
