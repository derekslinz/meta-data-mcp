"""Cross-provider federation primitives (v3 Phase 3).

Pure, I/O-free helpers that normalize heterogeneous provider results
onto a common geography + time axis so series pulled from different
open-data sources can be overlaid, merged, and compared. The meta tools
in ``providers/meta_data_mcp.py`` (``opendata_federate_query`` /
``opendata_federate_compare``) do the I/O — resolving sub-tools,
activating providers, running the sub-calls — and lean on the functions
here for the deterministic transformation. Keeping the transformation
here (no network, no LLM) makes federation fully unit-testable.

Building blocks come from :mod:`meta_data_mcp.harmonize`
(``normalize_geo`` → ISO alpha-3 identity, ``normalize_period`` →
canonical period + start date). This module's job is to (1) find the
data rows inside whatever envelope a provider returned, (2) locate the
geography and period fields inside each row, and (3) attach the
harmonized identities without ever silently discarding the originals.
"""

from __future__ import annotations

from typing import Any

from meta_data_mcp.harmonize import GeoMatch, Period, normalize_geo, normalize_period

# Field names providers actually use for geography, period, and value.
# Ordered by specificity so the most explicit key wins when several are
# present. Matching is case-insensitive (see ``_first_field``).
_GEO_KEYS: tuple[str, ...] = (
    "iso3",
    "iso",
    "geo",
    "ref_area",
    "area",
    "country_code",
    "country",
    "nation",
    "location",
    "region",
    "geo_code",
)
_PERIOD_KEYS: tuple[str, ...] = (
    "date",
    "period",
    "time_period",
    "time",
    "obs_time",
    "ref_period",
    "year",
    "timestamp",
)
_VALUE_KEYS: tuple[str, ...] = (
    "value",
    "obs_value",
    "indicator",
    "amount",
    "measure",
    "val",
)


def _first_field(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """First non-null value among ``keys`` in ``item`` (case-insensitive).

    Providers disagree on casing (``REF_AREA`` vs ``ref_area``), so we
    fall back to a lower-cased index. Returns ``None`` when no candidate
    key carries a value — the caller decides what a missing field means,
    never this helper.
    """
    for key in keys:
        if item.get(key) is not None:
            return item[key]
    lowered = {str(k).lower(): v for k, v in item.items()}
    for key in keys:
        if lowered.get(key) is not None:
            return lowered[key]
    return None


def _find_item_list(raw: Any) -> list[dict[str, Any]] | None:
    """Depth-first search for the first list of dict rows inside ``raw``.

    Many providers bury the actual observations several levels deep
    (``{"feed": {"entry": [...]}}``). We return the first list whose
    elements look like records (dicts), which is overwhelmingly the data
    array. Returns ``None`` when no such list exists.
    """
    if isinstance(raw, list):
        if raw and all(isinstance(x, dict) for x in raw):
            return raw
        for element in raw:
            found = _find_item_list(element)
            if found is not None:
                return found
        return None
    if isinstance(raw, dict):
        for value in raw.values():
            found = _find_item_list(value)
            if found is not None:
                return found
    return None


def _extract_items(raw: Any) -> tuple[list[dict[str, Any]], bool]:
    """Locate the record(s) inside a provider envelope.

    Returns ``(items, is_list)`` where ``is_list`` records whether the
    caller passed a collection (``_items`` / a nested array) versus a
    single object (``_item`` / a bare dict). The flag lets
    :func:`harmonize_result` mirror the input shape in its output.
    """
    if isinstance(raw, dict):
        if isinstance(raw.get("_item"), dict):
            return [raw["_item"]], False
        if isinstance(raw.get("_items"), list):
            return [x for x in raw["_items"] if isinstance(x, dict)], True

    nested = _find_item_list(raw)
    if nested is not None:
        return nested, True

    if isinstance(raw, dict):
        return [raw], False
    return [], True


def _geo_to_dict(geo: GeoMatch | None) -> dict[str, Any] | None:
    if geo is None:
        return None
    return {"iso3": geo.iso3, "name": geo.name, "is_aggregate": geo.is_aggregate}


def _period_to_dict(period: Period | None) -> dict[str, Any] | None:
    if period is None:
        return None
    return {
        "freq": period.freq,
        "canonical": period.canonical,
        "start_date": period.start_date,
    }


def _harmonize_item(
    item: dict[str, Any],
    source: str,
    *,
    geo: bool,
    time: bool,
) -> dict[str, Any]:
    """Attach harmonized geo/period identities to one record.

    The original fields are preserved verbatim; harmonization only
    *adds* (``source_provider``, ``harmonized_geo``,
    ``harmonized_period``, and a normalized ``value``). Unrecognized
    geographies/periods yield ``None`` rather than a guess.
    """
    out: dict[str, Any] = dict(item)
    out["source_provider"] = source

    geo_raw = _first_field(item, _GEO_KEYS) if geo else None
    out["harmonized_geo"] = (
        _geo_to_dict(normalize_geo(str(geo_raw))) if geo_raw is not None else None
    )

    period_raw = _first_field(item, _PERIOD_KEYS) if time else None
    out["harmonized_period"] = (
        _period_to_dict(normalize_period(period_raw))
        if period_raw is not None
        else None
    )

    value = _first_field(item, _VALUE_KEYS)
    if value is not None and "value" not in out:
        out["value"] = value

    return out


def harmonize_result(
    raw: Any,
    source: str,
    *,
    geo: bool = True,
    time: bool = True,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Normalize a provider result onto the common geo + time axis.

    ``raw`` is whatever a sub-tool returned. A single-object envelope
    (``{"_item": {...}}`` or a bare record) harmonizes to one dict; a
    collection (``{"_items": [...]}`` or an array nested anywhere in the
    envelope) harmonizes to a list, one entry per row. Each entry keeps
    its original fields and gains ``source_provider`` plus
    ``harmonized_geo`` / ``harmonized_period`` (either may be ``None``
    when the source field is absent or unrecognized).

    ``geo`` / ``time`` mirror the federate tool's ``harmonize`` flags —
    set either ``False`` to skip that axis.
    """
    items, is_list = _extract_items(raw)
    harmonized = [_harmonize_item(item, source, geo=geo, time=time) for item in items]
    if is_list:
        return harmonized
    return harmonized[0] if harmonized else {"source_provider": source}


def merge_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse harmonized rows sharing a geo + period into one row.

    Two providers reporting the same country and year for an indicator
    become a single merged row carrying every provider's value (and the
    provider labels), so the caller can render an overlay or diff. Input
    order of first appearance is preserved; an empty input yields an
    empty list.
    """
    if not results:
        return []

    groups: dict[tuple[Any, Any], dict[str, Any]] = {}
    order: list[tuple[Any, Any]] = []
    for row in results:
        geo = row.get("harmonized_geo") or {}
        period = row.get("harmonized_period") or {}
        key = (geo.get("iso3"), period.get("canonical"))
        if key not in groups:
            groups[key] = {
                "harmonized_geo": row.get("harmonized_geo"),
                "harmonized_period": row.get("harmonized_period"),
                "values": [],
                "sources": [],
            }
            order.append(key)
        value = row.get("value")
        if value is not None:
            groups[key]["values"].append(value)
        source = row.get("source_provider")
        if source is not None:
            groups[key]["sources"].append(source)

    return [groups[key] for key in order]


def coverage_matrix(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize which sources cover which geographies and periods.

    Powers the ``opendata_federate_compare`` data path: given harmonized
    rows, report the distinct sources, geographies, and periods observed
    plus a per-source list of the (geo, period) cells it filled. Purely
    descriptive — it never drops or invents rows.
    """
    sources: list[str] = []
    geos: list[str] = []
    periods: list[str] = []
    cells: dict[str, list[dict[str, Any]]] = {}

    for row in results:
        source = row.get("source_provider")
        geo = (row.get("harmonized_geo") or {}).get("iso3")
        period = (row.get("harmonized_period") or {}).get("canonical")
        if source is not None and source not in sources:
            sources.append(source)
        if geo is not None and geo not in geos:
            geos.append(geo)
        if period is not None and period not in periods:
            periods.append(period)
        if source is not None:
            cells.setdefault(source, []).append({"geo": geo, "period": period})

    return {
        "sources": sources,
        "geographies": geos,
        "periods": periods,
        "cells": cells,
    }
