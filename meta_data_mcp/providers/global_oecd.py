"""OECD SDMX Provider

This module provides interfaces to the OECD public SDMX REST API, exposing
economic, social, and environmental statistics for OECD and partner countries.

License: Most OECD data is published under the OECD Terms and Conditions
(https://www.oecd.org/termsandconditions/). Many series are freely
redistributable; check the dataflow metadata for specific terms.

Features:
- Dataflow discovery (list/get)
- Data-structure (DSD) retrieval
- Time-series data retrieval (SDMX-JSON)
- Codelist and conceptscheme metadata
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
PROVIDER_ID = "global-oecd"
BASE_URL = "https://sdmx.oecd.org/public/rest"
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


class OECDListDataflowsParams(BaseModel):
    """Parameters for listing OECD dataflows."""


def fetch_list_dataflows(_params: OECDListDataflowsParams) -> dict:
    """Fetch the catalogue of OECD dataflows."""
    response = http_get(
        f"{BASE_URL}/dataflow/all/all/latest",
        params={"format": "jsonobject"},
        timeout=60.0,
        headers={"Accept": SDMX_STRUCT_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_list_dataflows(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the oecd-list-dataflows tool call."""
    try:
        params = OECDListDataflowsParams(**(arguments or {}))
        data = fetch_list_dataflows(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing OECD dataflows: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="oecd-list-dataflows",
        description="List all available OECD SDMX dataflows.",
        input_schema=OECDListDataflowsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["oecd-list-dataflows"] = handle_list_dataflows


###################
# Get Dataflow
###################


class OECDGetDataflowParams(BaseModel):
    """Parameters for fetching a single OECD dataflow."""

    agencyId: str = Field(..., description="Agency ID (e.g. 'OECD.SDD.NAD')")
    flowId: str = Field(
        ...,
        description="Dataflow ID (e.g. 'DSD_NAMAIN1@DF_QNA_EXPENDITURE')",
    )


def fetch_get_dataflow(params: OECDGetDataflowParams) -> dict:
    """Fetch a single OECD dataflow descriptor."""
    response = http_get(
        f"{BASE_URL}/dataflow/{params.agencyId}/{params.flowId}/latest",
        params={"format": "jsonobject"},
        timeout=60.0,
        headers={"Accept": SDMX_STRUCT_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_dataflow(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the oecd-get-dataflow tool call."""
    try:
        if not arguments or "agencyId" not in arguments or "flowId" not in arguments:
            raise ValueError("agencyId and flowId are required")
        params = OECDGetDataflowParams(**arguments)
        data = fetch_get_dataflow(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OECD dataflow: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="oecd-get-dataflow",
        description="Get metadata for a single OECD dataflow.",
        input_schema=OECDGetDataflowParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["oecd-get-dataflow"] = handle_get_dataflow


###################
# Get Data Structure
###################


class OECDGetDataStructureParams(BaseModel):
    """Parameters for fetching an OECD data-structure definition (DSD)."""

    agencyId: str = Field(..., description="Agency ID (e.g. 'OECD.SDD.NAD')")
    structureId: str = Field(..., description="Data-structure ID (e.g. 'DSD_NAMAIN1')")


def fetch_get_datastructure(params: OECDGetDataStructureParams) -> dict:
    """Fetch an OECD data-structure definition."""
    response = http_get(
        f"{BASE_URL}/datastructure/{params.agencyId}/{params.structureId}",
        params={"format": "jsonobject"},
        timeout=60.0,
        headers={"Accept": SDMX_STRUCT_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_datastructure(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the oecd-get-datastructure tool call."""
    try:
        if (
            not arguments
            or "agencyId" not in arguments
            or "structureId" not in arguments
        ):
            raise ValueError("agencyId and structureId are required")
        params = OECDGetDataStructureParams(**arguments)
        data = fetch_get_datastructure(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OECD data-structure: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="oecd-get-datastructure",
        description="Get an OECD data-structure definition (DSD) describing dimensions and attributes.",
        input_schema=OECDGetDataStructureParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["oecd-get-datastructure"] = handle_get_datastructure


###################
# Get Data
###################


class OECDGetDataParams(BaseModel):
    """Parameters for fetching OECD SDMX time-series data."""

    agencyId: str = Field(..., description="Agency ID (e.g. 'OECD.SDD.NAD')")
    flowId: str = Field(..., description="Dataflow ID")
    version: str = Field(default="1.0", description="Dataflow version (e.g. '1.0')")
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


def fetch_get_data(params: OECDGetDataParams) -> dict:
    """Fetch OECD SDMX time-series data."""
    query_params: dict[str, Any] = {"format": "jsondata"}
    if params.startPeriod is not None:
        query_params["startPeriod"] = params.startPeriod
    if params.endPeriod is not None:
        query_params["endPeriod"] = params.endPeriod

    url = (
        f"{BASE_URL}/data/"
        f"{params.agencyId},{params.flowId},{params.version}/{params.key}"
    )
    response = http_get(
        url,
        params=query_params,
        timeout=60.0,
        headers={"Accept": SDMX_DATA_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


def _oecd_get_data_to_shape_payload(data: dict) -> dict:
    """Adapt OECD SDMX-JSON output to the
    ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    Same shape as ECB SDMX-JSON: ``data.dataSets[0].series[<series-key>]
    .observations[<obs-idx>][0]`` indexed back through
    ``data.structure.dimensions.observation[0].values`` (TIME_PERIOD) and
    ``data.structure.dimensions.series`` (for series labels).
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
            series_label = _oecd_series_label(series_key, series_dims)
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


def _oecd_series_label(series_key: str, series_dims: list[dict]) -> str:
    """Re-project an SDMX series index key into a dotted label.

    Skips ``FREQ``; falls back to the raw key when dimensions are absent.
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
    """Handle the oecd-get-data tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        if not arguments or "agencyId" not in arguments or "flowId" not in arguments:
            raise ValueError("agencyId and flowId are required")
        params = OECDGetDataParams(**arguments)
        data = fetch_get_data(params)
        payload = _oecd_get_data_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error fetching OECD data: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="oecd-get-data",
        description="Fetch OECD SDMX time-series data for a dataflow and series key.",
        input_schema=OECDGetDataParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["oecd-get-data"] = handle_get_data


###################
# List Codelist
###################


class OECDListCodelistParams(BaseModel):
    """Parameters for fetching an OECD codelist."""

    agencyId: str = Field(..., description="Agency ID (e.g. 'OECD.SDD.NAD')")
    codelistId: str = Field(..., description="Codelist ID (e.g. 'CL_AREA')")
    version: str = Field(default="1.0", description="Codelist version")


def fetch_list_codelist(params: OECDListCodelistParams) -> dict:
    """Fetch a codelist from the OECD SDMX API."""
    response = http_get(
        f"{BASE_URL}/codelist/{params.agencyId}/{params.codelistId}/{params.version}",
        params={"format": "jsonobject"},
        timeout=60.0,
        headers={"Accept": SDMX_STRUCT_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_list_codelist(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the oecd-list-codelist tool call."""
    try:
        if (
            not arguments
            or "agencyId" not in arguments
            or "codelistId" not in arguments
        ):
            raise ValueError("agencyId and codelistId are required")
        params = OECDListCodelistParams(**arguments)
        data = fetch_list_codelist(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OECD codelist: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="oecd-list-codelist",
        description="Fetch a codelist (controlled vocabulary) from the OECD SDMX API.",
        input_schema=OECDListCodelistParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["oecd-list-codelist"] = handle_list_codelist


###################
# List Concept Scheme
###################


class OECDListConceptSchemeParams(BaseModel):
    """Parameters for fetching an OECD concept scheme."""

    agencyId: str = Field(..., description="Agency ID (e.g. 'OECD.SDD.NAD')")
    schemeId: str = Field(..., description="Concept scheme ID")
    version: str = Field(default="1.0", description="Concept scheme version")


def fetch_list_conceptscheme(params: OECDListConceptSchemeParams) -> dict:
    """Fetch a concept scheme from the OECD SDMX API."""
    response = http_get(
        f"{BASE_URL}/conceptscheme/{params.agencyId}/{params.schemeId}/{params.version}",
        params={"format": "jsonobject"},
        timeout=60.0,
        headers={"Accept": SDMX_STRUCT_ACCEPT},
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_list_conceptscheme(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the oecd-list-conceptscheme tool call."""
    try:
        if not arguments or "agencyId" not in arguments or "schemeId" not in arguments:
            raise ValueError("agencyId and schemeId are required")
        params = OECDListConceptSchemeParams(**arguments)
        data = fetch_list_conceptscheme(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OECD concept scheme: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="oecd-list-conceptscheme",
        description="Fetch a concept scheme from the OECD SDMX API.",
        input_schema=OECDListConceptSchemeParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["oecd-list-conceptscheme"] = handle_list_conceptscheme


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-oecd",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
