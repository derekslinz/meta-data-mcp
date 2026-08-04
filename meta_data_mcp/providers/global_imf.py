"""IMF SDMX 2.1 Provider

This module provides interfaces to the International Monetary Fund's public
SDMX 2.1 REST API, exposing macroeconomic and financial statistics for member
countries (e.g. IFS, BOP, GFS, WEO).

License: IMF data is published under the IMF's data terms of use
(https://www.imf.org/external/terms.htm). Many series are freely
redistributable for non-commercial purposes; check the dataflow metadata.

Features:
- Dataflow discovery (list/get) under any SDMX agency (default 'IMF.STA')
- Data-structure (DSD) retrieval
- Time-series data retrieval (SDMX-JSON)
- Codelist metadata
- No API key required

Usage:
    The module can be run directly to start a server handling API requests.
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
PROVIDER_ID = "global-imf"
BASE_URL = "https://api.imf.org/external/sdmx/2.1"
DEFAULT_AGENCY = "IMF.STA"
SDMX_DATA_ACCEPT = "application/vnd.sdmx.data+json;version=1.0.0"
SDMX_STRUCT_ACCEPT = "application/vnd.sdmx.structure+json;version=1.0.0"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# List Dataflows
###################


class IMFListDataflowsParams(BaseModel):
    """Parameters for listing IMF SDMX dataflows."""

    agencyId: str = Field(
        default=DEFAULT_AGENCY,
        description="SDMX agency ID (default 'IMF.STA')",
    )


def fetch_list_dataflows(params: IMFListDataflowsParams) -> dict:
    """Fetch the IMF dataflow catalogue."""
    response = http_get(
        f"{BASE_URL}/dataflow/{params.agencyId}",
        timeout=60.0,
        headers={"Accept": SDMX_STRUCT_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_list_dataflows(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the imf-list-dataflows tool call."""
    try:
        params = IMFListDataflowsParams(**(arguments or {}))
        data = fetch_list_dataflows(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing IMF dataflows: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="imf-list-dataflows",
        description="List IMF SDMX dataflows for the given agency (default 'IMF.STA').",
        input_schema=IMFListDataflowsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["imf-list-dataflows"] = handle_list_dataflows


###################
# Get Dataflow
###################


class IMFGetDataflowParams(BaseModel):
    """Parameters for fetching a single IMF dataflow."""

    agencyId: str = Field(
        default=DEFAULT_AGENCY,
        description="SDMX agency ID (default 'IMF.STA')",
    )
    flowId: str = Field(..., description="Dataflow ID (e.g. 'IFS' or 'BOP')")


def fetch_get_dataflow(params: IMFGetDataflowParams) -> dict:
    """Fetch a single IMF dataflow descriptor."""
    response = http_get(
        f"{BASE_URL}/dataflow/{params.agencyId}/{params.flowId}",
        timeout=60.0,
        headers={"Accept": SDMX_STRUCT_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_dataflow(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the imf-get-dataflow tool call."""
    try:
        if not arguments or "flowId" not in arguments:
            raise ValueError("flowId is required")
        params = IMFGetDataflowParams(**arguments)
        data = fetch_get_dataflow(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching IMF dataflow: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="imf-get-dataflow",
        description="Get metadata for a single IMF dataflow.",
        input_schema=IMFGetDataflowParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["imf-get-dataflow"] = handle_get_dataflow


###################
# Get Data
###################


class IMFGetDataParams(BaseModel):
    """Parameters for fetching IMF SDMX time-series data."""

    flowRef: str = Field(
        ...,
        description="Flow reference, e.g. 'IMF.STA,IFS,1.0' or just 'IFS'",
    )
    key: str = Field(
        default="all",
        description="SDMX series key with dimension values separated by dots, or 'all'",
    )
    startPeriod: str | None = Field(
        None,
        description="Start period (e.g. '2010' or '2010-Q1')",
    )
    endPeriod: str | None = Field(
        None,
        description="End period (e.g. '2020' or '2020-Q4')",
    )


def fetch_get_data(params: IMFGetDataParams) -> dict:
    """Fetch IMF SDMX time-series data."""
    query_params: dict[str, Any] = {}
    if params.startPeriod is not None:
        query_params["startPeriod"] = params.startPeriod
    if params.endPeriod is not None:
        query_params["endPeriod"] = params.endPeriod

    url = f"{BASE_URL}/data/{params.flowRef}/{params.key}"
    response = http_get(
        url,
        params=query_params,
        timeout=60.0,
        headers={"Accept": SDMX_DATA_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


def _imf_get_data_to_shape_payload(data: dict) -> dict:
    """Adapt IMF SDMX-JSON output to the
    ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    Same SDMX-JSON shape as ECB/OECD/Eurostat: observations indexed back
    through the structure's TIME_PERIOD values; series key re-projected
    through dimension code IDs (FREQ skipped).
    """
    block = data.get("data") or data
    structure = block.get("structure") or {}
    dimensions = structure.get("dimensions") or {}
    obs_dims = dimensions.get("observation") or []
    series_dims = dimensions.get("series") or []

    time_values: list[str] = []
    time_label = "Period"
    if obs_dims:
        first_obs = obs_dims[0]
        time_label = first_obs.get("name") or first_obs.get("id") or "Period"
        time_values = [v.get("id", "") for v in (first_obs.get("values") or [])]

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
            series_label = _imf_series_label(series_key, series_dims)
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


def _imf_series_label(series_key: str, series_dims: list[dict]) -> str:
    """Re-project an SDMX series index key into a dotted label.

    Skips ``FREQ`` (uniform per request); falls back to the raw key when
    dimensions are missing.
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


async def handle_get_data(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the imf-get-data tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        if not arguments or "flowRef" not in arguments:
            raise ValueError("flowRef is required")
        params = IMFGetDataParams(**arguments)
        data = fetch_get_data(params)
        payload = _imf_get_data_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error fetching IMF data: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="imf-get-data",
        description="Fetch IMF SDMX time-series data for a given flow reference and series key.",
        input_schema=IMFGetDataParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["imf-get-data"] = handle_get_data


###################
# Get Data Structure
###################


class IMFGetDataStructureParams(BaseModel):
    """Parameters for fetching an IMF SDMX data-structure definition."""

    agencyId: str = Field(
        default=DEFAULT_AGENCY,
        description="SDMX agency ID (default 'IMF.STA')",
    )
    structureId: str = Field(..., description="Data-structure ID (e.g. 'DSD_IFS')")


def fetch_get_datastructure(params: IMFGetDataStructureParams) -> dict:
    """Fetch an IMF data-structure definition."""
    response = http_get(
        f"{BASE_URL}/datastructure/{params.agencyId}/{params.structureId}",
        timeout=60.0,
        headers={"Accept": SDMX_STRUCT_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_datastructure(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the imf-get-datastructure tool call."""
    try:
        if not arguments or "structureId" not in arguments:
            raise ValueError("structureId is required")
        params = IMFGetDataStructureParams(**arguments)
        data = fetch_get_datastructure(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching IMF data-structure: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="imf-get-datastructure",
        description="Get an IMF data-structure definition (DSD) describing dimensions and attributes.",
        input_schema=IMFGetDataStructureParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["imf-get-datastructure"] = handle_get_datastructure


###################
# List Codelist
###################


class IMFListCodelistParams(BaseModel):
    """Parameters for fetching an IMF SDMX codelist."""

    agencyId: str = Field(
        default=DEFAULT_AGENCY,
        description="SDMX agency ID (default 'IMF.STA')",
    )
    codelistId: str = Field(..., description="Codelist ID (e.g. 'CL_AREA')")


def fetch_list_codelist(params: IMFListCodelistParams) -> dict:
    """Fetch a codelist from the IMF SDMX API."""
    response = http_get(
        f"{BASE_URL}/codelist/{params.agencyId}/{params.codelistId}",
        timeout=60.0,
        headers={"Accept": SDMX_STRUCT_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_list_codelist(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the imf-list-codelist tool call."""
    try:
        if not arguments or "codelistId" not in arguments:
            raise ValueError("codelistId is required")
        params = IMFListCodelistParams(**arguments)
        data = fetch_list_codelist(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching IMF codelist: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="imf-list-codelist",
        description="Fetch a codelist (controlled vocabulary) from the IMF SDMX API.",
        input_schema=IMFListCodelistParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["imf-list-codelist"] = handle_list_codelist


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-imf",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
