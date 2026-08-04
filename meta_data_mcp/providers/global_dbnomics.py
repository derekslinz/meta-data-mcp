"""DBnomics Provider

This module provides interfaces to the DBnomics API, which aggregates economic data
from over 100 providers worldwide (IMF, World Bank, ECB, Fed, etc.).

License: DBnomics data is usually open, but specific provider licenses apply.
See https://db.nomics.world/ for details.

API Documentation: https://api.db.nomics.world/apidocs
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
PROVIDER_ID = "global-dbnomics"
BASE_URL = "https://api.db.nomics.world/v22"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# DBnomics Search
###################


class DBnomicsSearchParams(BaseModel):
    """Parameters for searching DBnomics datasets and series."""

    query: str = Field(..., description="Search query string")
    limit: int = Field(default=10, description="Number of results to return (max 50)")
    offset: int = Field(default=0, description="Number of results to skip")


def search_dbnomics(params: DBnomicsSearchParams) -> Any:
    """Search for datasets and series on DBnomics."""
    query_params = {"q": params.query, "limit": params.limit, "offset": params.offset}
    response = http_get(f"{BASE_URL}/search", params=query_params, provider=PROVIDER_ID)
    return response.json()


async def handle_dbnomics_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the dbnomics-search tool call."""
    try:
        params = DBnomicsSearchParams(**(arguments or {}))
        data = search_dbnomics(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching DBnomics: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="dbnomics-search",
        description="Search for economic datasets and series on DBnomics.",
        input_schema=DBnomicsSearchParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["dbnomics-search"] = handle_dbnomics_search

###################
# DBnomics Providers
###################


def list_dbnomics_providers() -> Any:
    """List all data providers available on DBnomics."""
    response = http_get(f"{BASE_URL}/providers", provider=PROVIDER_ID)
    return response.json()


async def handle_dbnomics_list_providers(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the dbnomics-list-providers tool call."""
    try:
        data = list_dbnomics_providers()
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing DBnomics providers: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="dbnomics-list-providers",
        description="List all economic data providers aggregated by DBnomics.",
        input_schema={"type": "object", "properties": {}},
    ),
)
TOOLS_HANDLERS["dbnomics-list-providers"] = handle_dbnomics_list_providers

###################
# DBnomics Series
###################


class DBnomicsSeriesParams(BaseModel):
    """Parameters for fetching DBnomics series data."""

    series_ids: str = Field(
        ...,
        description="Comma-separated list of series IDs (e.g. 'IMF/WEO:2024-04/ABW.NGDP_RPCH')",
    )


def fetch_dbnomics_series(params: DBnomicsSeriesParams) -> Any:
    """Fetch data for specific series from DBnomics."""
    query_params = {"series_ids": params.series_ids}
    response = http_get(f"{BASE_URL}/series", params=query_params, provider=PROVIDER_ID)
    return response.json()


def _dbnomics_series_to_shape_payload(data: dict) -> dict:
    """Adapt DBnomics' ``{series: {docs: [{period, value, series_code, ...}]}}``
    response to the ``ui://meta-data-mcp/shape/timeseries/v1`` payload:
    ``{points: [{date, value, series}], axes: {x, y}}``.

    Each series doc contributes one point per ``(period[i], value[i])`` pair,
    tagged with the series code so multi-series requests render as separate
    lines.
    """
    series_block = data.get("series") or {}
    docs = series_block.get("docs") or []
    points: list[dict[str, Any]] = []
    unit_label = ""
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        series_code = (
            doc.get("series_code")
            or doc.get("series_name")
            or doc.get("dataset_code")
            or ""
        )
        if not unit_label:
            unit_label = doc.get("unit") or doc.get("@frequency") or ""
        periods = doc.get("period") or []
        values = doc.get("value") or []
        if not isinstance(periods, list) or not isinstance(values, list):
            continue
        # strict=False: if DBnomics returns ragged arrays (rare but
        # documented for partial-series responses), better to surface
        # the prefix that does line up than to drop the whole series
        # by raising.
        for period, value in zip(periods, values, strict=False):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not isinstance(period, str):
                continue
            point: dict[str, Any] = {"date": period, "value": value}
            if series_code:
                point["series"] = series_code
            points.append(point)
    points.sort(key=lambda p: (p["date"], p.get("series", "")))
    return {
        "points": points,
        "axes": {"x": "Period", "y": unit_label or "Value"},
    }


async def handle_dbnomics_series(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the dbnomics-series tool call.

    The response is in the ``ui://meta-data-mcp/shape/timeseries/v1``
    payload format so MCP Apps hosts can render the chart inline.
    """
    try:
        params = DBnomicsSeriesParams(**(arguments or {}))
        data = fetch_dbnomics_series(params)
        payload = _dbnomics_series_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_json_text(payload))]
    except Exception as e:
        log.error(f"Error fetching DBnomics series: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="dbnomics-series",
        description="Fetch data for specific economic series from DBnomics.",
        input_schema=DBnomicsSeriesParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["dbnomics-series"] = handle_dbnomics_series


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-dbnomics",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
