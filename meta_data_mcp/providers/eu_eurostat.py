"""Eurostat Data API Client

This module provides interfaces to access the Eurostat Statistics API and Catalogue API.
It allows querying various European statistical datasets, including economic,
geographic, and social data.

Features:
- Dataset discovery through the Table of Contents (TOC)
- Data retrieval in JSON-stat 2.0 format
- Metadata retrieval for dataset dimensions and labels

Usage:
    The module can be run directly to start a server handling API requests,
    or its components can be imported and used individually.
"""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI
from meta_data_mcp.utils import http_get, to_json_text

PROVIDER_ID = "eu-eurostat"

# Initialize logging
log = logging.getLogger(__name__)

# Constants
BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination"
TOC_URL = f"{BASE_URL}/catalogue/toc/xml?lang=en"
DATA_URL = f"{BASE_URL}/statistics/1.0/data"
METADATA_URL = f"{BASE_URL}/statistics/1.0/metadata/dataset"

# Namespaces for TOC XML
NAMESPACES = {"nt": "urn:eu.europa.ec.eurostat.navtree"}

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# Eurostat Dataset Discovery
###################


class EurostatListDatasetsParams(BaseModel):
    """Parameters for listing or searching Eurostat datasets."""

    search: str | None = Field(
        None,
        description="Keyword to search in dataset titles or codes",
    )
    limit: int = Field(default=20, description="Maximum number of datasets to return")


class EurostatDatasetInfo(BaseModel):
    """Basic information about a Eurostat dataset."""

    code: str = Field(..., description="The unique code of the dataset")
    title: str = Field(..., description="The title of the dataset")
    type: str = Field(..., description="Type of the node (table, dataset, etc.)")
    last_update: str | None = Field(None, description="Last update of the metadata")
    last_data_update: str | None = Field(None, description="Last update of the data")


def list_eurostat_datasets(
    params: EurostatListDatasetsParams,
) -> list[EurostatDatasetInfo]:
    """Search or list available Eurostat datasets from the TOC XML."""
    # TOC is XML — override the kernel's default Accept: application/json
    # so eurostat doesn't refuse content negotiation.
    response = http_get(
        TOC_URL,
        timeout=30.0,
        headers={"Accept": "application/xml"},
        provider=PROVIDER_ID,
    )

    root = ET.fromstring(response.content)
    datasets = []

    # We look for <nt:leaf> elements which represent actual data products
    for leaf in root.findall(".//nt:leaf", NAMESPACES):
        code_elem = leaf.find("nt:code", NAMESPACES)
        title_elem = leaf.find("nt:title[@language='en']", NAMESPACES)

        if code_elem is None or title_elem is None:
            continue

        code = code_elem.text or ""
        title = title_elem.text or ""
        node_type = leaf.get("type", "unknown")

        # Filtering
        if params.search:
            search_lower = params.search.lower()
            if search_lower not in code.lower() and search_lower not in title.lower():
                continue

        last_update = leaf.findtext("nt:lastUpdate", namespaces=NAMESPACES)
        last_data_update = leaf.findtext("nt:lastDataUpdate", namespaces=NAMESPACES)

        datasets.append(
            EurostatDatasetInfo(
                code=code,
                title=title,
                type=node_type,
                last_update=last_update,
                last_data_update=last_data_update,
            ),
        )

        if len(datasets) >= params.limit:
            break

    return datasets


async def handle_eurostat_list_datasets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the eurostat-list-datasets tool call."""
    try:
        params = EurostatListDatasetsParams(**(arguments or {}))
        datasets = list_eurostat_datasets(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text([d.model_dump() for d in datasets]),
            ),
        ]
    except Exception as e:
        log.error(f"Error listing Eurostat datasets: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="eurostat-list-datasets",
        description="Search or list available Eurostat datasets from the Table of Contents.",
        input_schema=EurostatListDatasetsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["eurostat-list-datasets"] = handle_eurostat_list_datasets

###################
# Eurostat Data Retrieval
###################


class EurostatDataParams(BaseModel):
    """Parameters for fetching data from a Eurostat dataset."""

    dataset_code: str = Field(
        ...,
        description="The code of the dataset (e.g., 'nama_10_gdp')",
    )
    lang: str = Field(
        default="en",
        description="Language of labels: 'en', 'fr', or 'de'",
    )


def fetch_eurostat_data(params: EurostatDataParams) -> dict:
    """Fetch data from a specific Eurostat dataset in JSON-stat format."""
    endpoint = f"{DATA_URL}/{params.dataset_code}"
    query_params = {"format": "JSON", "lang": params.lang}

    response = http_get(
        endpoint,
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


def _eurostat_dataset_to_shape_payload(data: dict) -> dict:
    """Adapt a Eurostat JSON-stat 2.0 response to the
    ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    JSON-stat encodes a multi-dimensional cube as a flat ``value`` array
    indexed in row-major order against the ``size`` array. We locate the
    time dimension (``time`` or the dimension whose role.time is set),
    treat all other dimensions as series, and emit one point per
    ``(non-time-coord, time-coord)`` slice that has a numeric value.

    Sparse ``value`` objects (``{flat_idx: v}``) and dense arrays are
    both supported. Missing entries are skipped.
    """
    ids = data.get("id") or []
    sizes = data.get("size") or []
    dimension = data.get("dimension") or {}
    values_raw = data.get("value")
    if not ids or not sizes or values_raw is None or len(ids) != len(sizes):
        return {"points": [], "axes": {"x": "Time", "y": data.get("label") or "Value"}}

    # Locate the time dimension. Eurostat conventionally names it 'time';
    # JSON-stat also allows role.time to mark it explicitly.
    time_idx = -1
    if "time" in ids:
        time_idx = ids.index("time")
    else:
        role_time = (data.get("role") or {}).get("time") or []
        if role_time:
            for cand in role_time:
                if cand in ids:
                    time_idx = ids.index(cand)
                    break
    if time_idx < 0:
        # No time dimension → not a time series; defer to empty.
        return {"points": [], "axes": {"x": "Time", "y": data.get("label") or "Value"}}

    # Build per-dimension code lookups: position -> code.
    pos_to_code: list[list[str]] = []
    # strict=True: ids and sizes come from the same JSON-stat
    # ``dimension`` block and must be parallel. A mismatch means the
    # upstream response is malformed; fail loudly rather than truncate.
    for dim_id, size in zip(ids, sizes, strict=True):
        dim_block = dimension.get(dim_id) or {}
        category = dim_block.get("category") or {}
        index_map = category.get("index") or {}
        codes: list[str] = ["" for _ in range(size)]
        if isinstance(index_map, dict):
            for code, pos in index_map.items():
                if isinstance(pos, int) and 0 <= pos < size:
                    codes[pos] = code
        elif isinstance(index_map, list):
            for pos, code in enumerate(index_map):
                if pos < size:
                    codes[pos] = code
        pos_to_code.append(codes)

    # Row-major strides (last dim varies fastest).
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    total = strides[0] * sizes[0] if sizes else 0

    # Normalize values to {flat_idx: value}.
    flat: dict[int, Any] = {}
    if isinstance(values_raw, dict):
        for k, v in values_raw.items():
            try:
                flat[int(k)] = v
            except (TypeError, ValueError):
                continue
    elif isinstance(values_raw, list):
        for i, v in enumerate(values_raw):
            if v is None:
                continue
            flat[i] = v

    time_label = (dimension.get(ids[time_idx]) or {}).get("label") or "Time"
    y_label = data.get("label") or "Value"

    # For each populated flat index, decode coordinates and emit a point.
    points: list[dict[str, Any]] = []
    for flat_idx, value in flat.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if flat_idx < 0 or flat_idx >= total:
            continue
        coords = []
        remaining = flat_idx
        # strides was computed from sizes immediately above; the two
        # lists are invariant-equal length. strict=True documents that
        # and catches refactors that break it.
        for _size, stride in zip(sizes, strides, strict=True):
            coord = remaining // stride
            remaining = remaining % stride
            coords.append(coord)
        time_pos = coords[time_idx]
        if not (0 <= time_pos < len(pos_to_code[time_idx])):
            continue
        date = pos_to_code[time_idx][time_pos]
        if not date:
            continue
        series_parts = [
            pos_to_code[i][coords[i]]
            for i in range(len(ids))
            if i != time_idx and 0 <= coords[i] < len(pos_to_code[i])
        ]
        point: dict[str, Any] = {"date": date, "value": value}
        series_label = ".".join(p for p in series_parts if p)
        if series_label:
            point["series"] = series_label
        points.append(point)

    points.sort(key=lambda p: (p["date"], p.get("series", "")))
    return {
        "points": points,
        "axes": {"x": time_label, "y": y_label},
    }


async def handle_eurostat_get_dataset(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the eurostat-get-dataset tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        if not arguments or "dataset_code" not in arguments:
            raise ValueError("dataset_code is required")

        params = EurostatDataParams(**arguments)
        data = fetch_eurostat_data(params)
        payload = _eurostat_dataset_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error fetching Eurostat data: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="eurostat-get-dataset",
        description="Fetch data from a specific Eurostat dataset (JSON-stat).",
        input_schema=EurostatDataParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["eurostat-get-dataset"] = handle_eurostat_get_dataset

###################
# Eurostat Metadata
###################


class EurostatMetadataParams(BaseModel):
    """Parameters for fetching metadata for a Eurostat dataset."""

    dataset_code: str = Field(
        ...,
        description="The code of the dataset (e.g., 'nama_10_gdp')",
    )
    lang: str = Field(default="en", description="Language: 'en', 'fr', or 'de'")


def fetch_eurostat_metadata(params: EurostatMetadataParams) -> dict:
    """Fetch dimension/label metadata for a specific Eurostat dataset.

    Returns only the 'dimension' and 'label' keys from the JSON-stat response,
    which contain the dataset structure (variables, categories, labels) without
    the full data values.
    """
    endpoint = f"{DATA_URL}/{params.dataset_code}"
    query_params = {"format": "JSON", "lang": params.lang}

    response = http_get(
        endpoint,
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    data = response.json()
    # Return only structural metadata, not the full data values
    return {
        "id": data.get("id"),
        "size": data.get("size"),
        "dimension": data.get("dimension"),
        "label": data.get("label"),
        "updated": data.get("updated"),
        "source": data.get("source"),
    }


async def handle_eurostat_get_metadata(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the eurostat-get-metadata tool call."""
    try:
        if not arguments or "dataset_code" not in arguments:
            raise ValueError("dataset_code is required")

        params = EurostatMetadataParams(**arguments)
        metadata = fetch_eurostat_metadata(params)
        return [types.TextContent(type="text", text=to_json_text(metadata))]
    except Exception as e:
        log.error(f"Error fetching Eurostat metadata: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="eurostat-get-metadata",
        description="Fetch metadata (dimensions, labels) for a specific Eurostat dataset.",
        input_schema=EurostatMetadataParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["eurostat-get-metadata"] = handle_eurostat_get_metadata


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "eu-eurostat",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


# Server initialization
if __name__ == "__main__":
    import anyio

    anyio.run(main)
