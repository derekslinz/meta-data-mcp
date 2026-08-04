"""global-faostat provider.

FAOSTAT — Food and Agriculture Organization (FAO) statistics. Crop and
livestock production, trade, food balances, prices, land use, fertilizers
and pesticides, forestry, fisheries, food security indicators, emissions,
population — covering 245 countries and territories from 1961 to the
current year.

Homepage: https://www.fao.org/faostat/en/
API docs: https://faostatservices.fao.org/api/v1/en/
License: Most datasets are CC BY 4.0; cite "FAOSTAT" with the dataset
domain code.
Auth: None required.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI
from meta_data_mcp.utils import (
    create_mcp_server,
    http_get,
    run_server,
    serialize_for_llm,
)

log = logging.getLogger(__name__)

PROVIDER_ID = "global-faostat"
BASE_URL = "https://faostatservices.fao.org/api/v1/en"

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# faostat-list-domains
###################


class FaostatListDomainsParams(BaseModel):
    """Parameters for faostat-list-domains."""


def fetch_faostat_list_domains(params: FaostatListDomainsParams) -> Any:
    """List all FAOSTAT data domains (e.g. QC = crop production, TI = trade)."""
    response = http_get(f"{BASE_URL}/groupsanddomains", provider=PROVIDER_ID)
    return response.json()


async def handle_faostat_list_domains(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the faostat-list-domains tool call."""
    params = FaostatListDomainsParams(**(arguments or {}))
    data = fetch_faostat_list_domains(params)
    return [types.TextContent(type="text", text=serialize_for_llm(data))]


TOOLS.append(
    types.Tool(
        name="faostat-list-domains",
        description=(
            "List all FAOSTAT data domains with their group and code. The "
            "domain code (e.g. 'QCL' for Crops and livestock, 'TM' for Trade "
            "of crops and livestock, 'FBS' for Food balance) is the first "
            "argument to faostat-list-items and faostat-data."
        ),
        input_schema=FaostatListDomainsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["faostat-list-domains"] = handle_faostat_list_domains


###################
# faostat-list-items
###################


class FaostatListItemsParams(BaseModel):
    """Parameters for faostat-list-items."""

    domain_code: str = Field(
        ...,
        min_length=1,
        description=(
            "FAOSTAT domain code (e.g. 'QCL' for crops/livestock, 'TM' for "
            "trade). Use faostat-list-domains to discover codes."
        ),
    )


def fetch_faostat_list_items(params: FaostatListItemsParams) -> Any:
    """List items (e.g. 'Maize', 'Cattle') available in a FAOSTAT domain."""
    response = http_get(
        f"{BASE_URL}/dimensions/{params.domain_code}/items",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_faostat_list_items(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the faostat-list-items tool call."""
    params = FaostatListItemsParams(**(arguments or {}))
    data = fetch_faostat_list_items(params)
    return [types.TextContent(type="text", text=serialize_for_llm(data))]


TOOLS.append(
    types.Tool(
        name="faostat-list-items",
        description=(
            "List the items (commodities, livestock, food products) tracked "
            "in a specific FAOSTAT domain. Each item has a numeric code used "
            "by faostat-data."
        ),
        input_schema=FaostatListItemsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["faostat-list-items"] = handle_faostat_list_items


###################
# faostat-data
###################


class FaostatDataParams(BaseModel):
    """Parameters for faostat-data."""

    domain_code: str = Field(
        ...,
        min_length=1,
        description=("FAOSTAT domain code (e.g. 'QCL', 'TM', 'FBS'). Required."),
    )
    area_codes: str | None = Field(
        None,
        description=(
            "Comma-separated FAO area (country) codes — e.g. '231' for USA, "
            "'21' for Brazil. Use 'area=>area_codes_m49' codes from the "
            "FAOSTAT M49 dimension."
        ),
    )
    item_codes: str | None = Field(
        None,
        description=(
            "Comma-separated FAOSTAT item codes from faostat-list-items "
            "(e.g. '56' for Maize)."
        ),
    )
    element_codes: str | None = Field(
        None,
        description=(
            "Comma-separated element codes — e.g. '5510' (Production) or "
            "'5910' (Export quantity)."
        ),
    )
    year: str | None = Field(
        None,
        description=(
            "Comma-separated years or a range (e.g. '2020,2021,2022' or '2015-2022')."
        ),
    )
    limit: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Maximum rows to return.",
    )


def fetch_faostat_data(params: FaostatDataParams) -> Any:
    """Query FAOSTAT data for a domain with optional filters."""
    query: dict[str, Any] = {
        "output_type": "objects",
        "limit": params.limit,
    }
    if params.area_codes is not None:
        query["area"] = params.area_codes
    if params.item_codes is not None:
        query["item"] = params.item_codes
    if params.element_codes is not None:
        query["element"] = params.element_codes
    if params.year is not None:
        query["year"] = params.year
    response = http_get(
        f"{BASE_URL}/data/{params.domain_code}",
        params=query,
        provider=PROVIDER_ID,
    )
    return response.json()


def _faostat_data_to_shape_payload(data: dict) -> dict:
    """Adapt FAOSTAT's ``{data: [{Year, Value, Area, Item, Element, Unit, ...}]}``
    response to the ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    The series label combines the discriminator dimensions (area + item +
    element) when more than one row varies on them, so plots stay
    distinguishable for cross-country / cross-commodity queries. The Y-axis
    uses the dataset Unit when uniform.
    """
    rows = data.get("data") or []
    if not isinstance(rows, list):
        return {"points": [], "axes": {"x": "Year", "y": "Value"}}

    # Field-name variants: FAOSTAT sometimes serializes with capitalized
    # space-separated labels ("Area"), sometimes with lowercase ("area").
    def _pick(row: dict, *candidates: str) -> Any:
        for c in candidates:
            if c in row and row[c] is not None:
                return row[c]
        return None

    units: set[str] = set()
    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        year = _pick(row, "Year", "year")
        if year is None:
            continue
        date = str(year)
        raw_value = _pick(row, "Value", "value")
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if isinstance(raw_value, bool):
            continue
        unit = _pick(row, "Unit", "unit")
        if unit:
            units.add(unit)
        series_bits = [
            _pick(row, "Area", "area"),
            _pick(row, "Item", "item"),
            _pick(row, "Element", "element"),
        ]
        series_label = " · ".join(str(b) for b in series_bits if b)
        point: dict[str, Any] = {"date": date, "value": value}
        if series_label:
            point["series"] = series_label
        points.append(point)

    points.sort(key=lambda p: (p["date"], p.get("series", "")))
    y_label = (units.pop() if len(units) == 1 else "Value") if units else "Value"
    return {
        "points": points,
        "axes": {"x": "Year", "y": y_label},
    }


async def handle_faostat_data(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the faostat-data tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    params = FaostatDataParams(**(arguments or {}))
    data = fetch_faostat_data(params)
    payload = _faostat_data_to_shape_payload(data)
    return [types.TextContent(type="text", text=serialize_for_llm(payload))]


TOOLS.append(
    types.Tool(
        name="faostat-data",
        description=(
            "Query a FAOSTAT data domain with optional filters for area "
            "(country), item (commodity), element (measure), and year. "
            "Returns time-series rows — country, item, element, year, unit, "
            "value, and flag. Use faostat-list-domains and faostat-list-items "
            "first to discover codes."
        ),
        input_schema=FaostatDataParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["faostat-data"] = handle_faostat_data


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    server = create_mcp_server(
        "global-faostat",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )
    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
