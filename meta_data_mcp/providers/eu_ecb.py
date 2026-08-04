"""European Central Bank Data Portal Provider

This module exposes the European Central Bank's SDMX 2.1 REST API (the
ECB Data Portal). Data is requested in SDMX-JSON (`format=jsondata`) so
responses are JSON-shaped, although consumers still need to understand
SDMX-style series keys (dotted dimension values like 'D.USD.EUR.SP00.A').

License / source:
    ECB statistical data is generally available for re-use under the
    European Central Bank's general terms of use. Always confirm the
    licence on individual dataflows.

Features:
- List all ECB dataflows (datasets)
- Get the metadata for a single dataflow
- Pull observations for a dataflow + key
- Inspect codelists (controlled value lists)
- Inspect concept schemes (definitions of dimensions/attributes)

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_json_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "eu-ecb"
BASE_URL = "https://data-api.ecb.europa.eu/service"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# List dataflows
###################


class ECBListDataflowsParams(BaseModel):
    """Parameters for listing all ECB dataflows (no inputs)."""


def fetch_ecb_list_dataflows(_: ECBListDataflowsParams) -> dict:
    """Fetch every ECB dataflow definition."""
    query_params: dict[str, Any] = {"format": "jsondata"}
    response = http_get(
        f"{BASE_URL}/dataflow/ECB/all/latest",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ecb_list_dataflows(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ecb-list-dataflows tool call."""
    try:
        params = ECBListDataflowsParams(**(arguments or {}))
        data = fetch_ecb_list_dataflows(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing ECB dataflows: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ecb-list-dataflows",
        description="List every ECB SDMX dataflow (dataset) available on the ECB Data Portal.",
        input_schema=ECBListDataflowsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ecb-list-dataflows"] = handle_ecb_list_dataflows


###################
# Get dataflow
###################


class ECBGetDataflowParams(BaseModel):
    """Parameters for fetching a single dataflow's metadata."""

    id: str = Field(..., description="Dataflow ID (e.g. 'EXR' for exchange rates).")


def fetch_ecb_get_dataflow(params: ECBGetDataflowParams) -> dict:
    """Fetch metadata for a single ECB dataflow."""
    query_params: dict[str, Any] = {"format": "jsondata"}
    response = http_get(
        f"{BASE_URL}/dataflow/ECB/{params.id}/latest",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ecb_get_dataflow(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ecb-get-dataflow tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = ECBGetDataflowParams(**arguments)
        data = fetch_ecb_get_dataflow(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching ECB dataflow {arguments}: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ecb-get-dataflow",
        description="Get metadata for a single ECB dataflow by ID (e.g. 'EXR').",
        input_schema=ECBGetDataflowParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ecb-get-dataflow"] = handle_ecb_get_dataflow


###################
# Get data
###################


class ECBGetDataParams(BaseModel):
    """Parameters for pulling observations from a dataflow."""

    flow: str = Field(..., description="Dataflow ID (e.g. 'EXR').")
    key: str = Field(
        ...,
        description=(
            "Dotted SDMX series key (e.g. 'D.USD.EUR.SP00.A' for daily "
            "USD/EUR reference rates). Use 'all' to fetch the whole flow."
        ),
    )
    startPeriod: str | None = Field(
        default=None,
        description="Inclusive start period (e.g. '2024-01-01' or '2024-Q1').",
    )
    endPeriod: str | None = Field(
        default=None,
        description="Inclusive end period.",
    )
    lastNObservations: int | None = Field(
        default=None,
        ge=1,
        description="If set, return only the last N observations (mutually exclusive with period filters).",
    )


def fetch_ecb_get_data(params: ECBGetDataParams) -> dict:
    """Fetch observations from an ECB dataflow."""
    query_params: dict[str, Any] = {"format": "jsondata"}
    if params.startPeriod:
        query_params["startPeriod"] = params.startPeriod
    if params.endPeriod:
        query_params["endPeriod"] = params.endPeriod
    if params.lastNObservations:
        query_params["lastNObservations"] = params.lastNObservations
    response = http_get(
        f"{BASE_URL}/data/{params.flow}/{params.key}",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _ecb_get_data_to_shape_payload(data: dict) -> dict:
    """Adapt SDMX-JSON output from the ECB Data Portal to the
    ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    SDMX-JSON nests observations as
    ``data.dataSets[0].series[<series-key>].observations[<obs-idx>][0]``,
    where the obs index maps back to ``data.structure.dimensions.observation[i].values[idx].id``
    (typically ``TIME_PERIOD``). The series key is a colon-separated string
    of dimension value indexes; we re-project it through
    ``data.structure.dimensions.series`` to build a human-readable
    ``series`` label (skipping the ``FREQ`` dimension, which is uniform per
    call).
    """
    block = data.get("data") or data
    structure = block.get("structure") or {}
    dimensions = structure.get("dimensions") or {}
    obs_dims = dimensions.get("observation") or []
    series_dims = dimensions.get("series") or []

    # Pre-extract the time axis from the first observation dimension.
    time_values: list[str] = []
    time_label = "Period"
    if obs_dims:
        first_obs = obs_dims[0]
        time_label = first_obs.get("name") or first_obs.get("id") or "Period"
        time_values = [v.get("id", "") for v in (first_obs.get("values") or [])]

    # Y-axis label: try to find a UNIT / UNIT_MEASURE attribute, else fall
    # back to the dataset name.
    y_label = "Value"
    for attr in (structure.get("attributes") or {}).get("series", []) or []:
        attr_id = (attr.get("id") or "").upper()
        if attr_id in ("UNIT", "UNIT_MEASURE"):
            values = attr.get("values") or []
            if values:
                y_label = values[0].get("name") or y_label
            break

    datasets = block.get("dataSets") or []
    points: list[dict[str, Any]] = []
    for dataset in datasets:
        series_map = dataset.get("series") or {}
        for series_key, series_block in series_map.items():
            if not isinstance(series_block, dict):
                continue
            series_label = _ecb_series_label(series_key, series_dims)
            observations = series_block.get("observations") or {}
            for obs_idx_str, obs_value in observations.items():
                try:
                    obs_idx = int(obs_idx_str)
                except (TypeError, ValueError):
                    continue
                if obs_idx < 0 or obs_idx >= len(time_values):
                    continue
                if not isinstance(obs_value, list) or not obs_value:
                    continue
                value = obs_value[0]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                point: dict[str, Any] = {
                    "date": time_values[obs_idx],
                    "value": value,
                }
                if series_label:
                    point["series"] = series_label
                points.append(point)

    points.sort(key=lambda p: (p["date"], p.get("series", "")))
    return {
        "points": points,
        "axes": {"x": time_label, "y": y_label},
    }


def _ecb_series_label(series_key: str, series_dims: list[dict]) -> str:
    """Re-project an SDMX series index key (e.g. ``'0:1:2:0:0'``) into a
    dotted, human-readable series label using the dimension code IDs.

    Skips the ``FREQ`` dimension (frequency is uniform per request) and
    falls back to the raw key when dimensions are missing.
    """
    if not series_dims:
        return series_key
    indexes = series_key.split(":")
    parts: list[str] = []
    for i, idx_str in enumerate(indexes):
        if i >= len(series_dims):
            break
        dim = series_dims[i] or {}
        if (dim.get("id") or "").upper() == "FREQ":
            continue
        try:
            idx = int(idx_str)
        except (TypeError, ValueError):
            continue
        values = dim.get("values") or []
        if 0 <= idx < len(values):
            parts.append(values[idx].get("id") or "")
    return ".".join(p for p in parts if p) or series_key


async def handle_ecb_get_data(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ecb-get-data tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts can render the chart inline.
    """
    try:
        if not arguments or "flow" not in arguments or "key" not in arguments:
            raise ValueError("flow and key are required")
        params = ECBGetDataParams(**arguments)
        data = fetch_ecb_get_data(params)
        payload = _ecb_get_data_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error fetching ECB data: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ecb-get-data",
        description=(
            "Fetch observations from an ECB dataflow. Example: flow='EXR', "
            "key='D.USD.EUR.SP00.A' for daily USD/EUR reference rate."
        ),
        input_schema=ECBGetDataParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["ecb-get-data"] = handle_ecb_get_data


###################
# Get codelist
###################


class ECBGetCodelistParams(BaseModel):
    """Parameters for fetching a codelist."""

    id: str = Field(..., description="Codelist ID (e.g. 'CL_CURRENCY').")


def fetch_ecb_get_codelist(params: ECBGetCodelistParams) -> dict:
    """Fetch an ECB codelist."""
    query_params: dict[str, Any] = {"format": "jsondata"}
    response = http_get(
        f"{BASE_URL}/codelist/ECB/{params.id}/latest",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ecb_get_codelist(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ecb-get-codelist tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = ECBGetCodelistParams(**arguments)
        data = fetch_ecb_get_codelist(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching ECB codelist: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ecb-get-codelist",
        description="Get the values that make up an ECB SDMX codelist (e.g. 'CL_CURRENCY').",
        input_schema=ECBGetCodelistParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ecb-get-codelist"] = handle_ecb_get_codelist


###################
# Get conceptscheme
###################


class ECBGetConceptSchemeParams(BaseModel):
    """Parameters for fetching a concept scheme."""

    id: str = Field(..., description="Concept scheme ID (e.g. 'ECB_CONCEPTS').")


def fetch_ecb_get_conceptscheme(params: ECBGetConceptSchemeParams) -> dict:
    """Fetch an ECB concept scheme."""
    query_params: dict[str, Any] = {"format": "jsondata"}
    response = http_get(
        f"{BASE_URL}/conceptscheme/ECB/{params.id}/latest",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_ecb_get_conceptscheme(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ecb-get-conceptscheme tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = ECBGetConceptSchemeParams(**arguments)
        data = fetch_ecb_get_conceptscheme(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching ECB concept scheme: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ecb-get-conceptscheme",
        description="Get an ECB SDMX concept scheme (definitions of dimensions and attributes).",
        input_schema=ECBGetConceptSchemeParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ecb-get-conceptscheme"] = handle_ecb_get_conceptscheme


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "eu-ecb",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
