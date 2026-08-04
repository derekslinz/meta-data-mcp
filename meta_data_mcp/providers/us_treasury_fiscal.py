"""US Treasury Fiscal Data Provider

This module exposes the US Department of the Treasury's Fiscal Data API at
api.fiscaldata.treasury.gov. The API publishes daily and historical data
about federal debt, the Daily Treasury Statement (DTS), interest rates,
and exchange rates used for foreign currency reporting.

License note:
    Fiscal Data is a Treasury Bureau of the Fiscal Service publication;
    all datasets are in the public domain as US Government works.

Features:
- Debt to the Penny: total US public debt outstanding (daily)
- Average interest rates on the Treasury's outstanding marketable securities
- DTS Operating Cash Balance
- DTS Public Debt Transactions
- Treasury Reporting Rates of Exchange (quarterly)
- List of known fiscal-service endpoints
- Generic records-search wrapper for any /v1 or /v2 endpoint

Filter syntax:
    The Fiscal Data API uses filters of the form
        filter=record_date:gte:2024-01-01
    Multiple filters can be comma-separated; ranges/operators include
    eq, neq, gt, gte, lt, lte, in.

Pagination:
    The `page[size]` query parameter uses square brackets and is passed
    through httpx as part of a `params` dict.

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
from meta_data_mcp.utils import http_get, serialize_for_llm

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-treasury-fiscal"
BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"

# Known Fiscal Data endpoints surfaced by treasury-list-endpoints. The Fiscal
# Data API does not currently expose a machine-readable index of endpoints,
# so we keep a curated list of the major datasets this provider wraps.
KNOWN_ENDPOINTS: list[dict[str, str]] = [
    {
        "path": "/v2/accounting/od/debt_to_penny",
        "name": "Debt to the Penny",
        "description": "Daily total US public debt outstanding.",
    },
    {
        "path": "/v2/accounting/od/avg_interest_rates",
        "name": "Average Interest Rates on US Treasury Securities",
        "description": "Monthly weighted-average interest rates on Treasury debt.",
    },
    {
        "path": "/v1/accounting/dts/operating_cash_balance",
        "name": "DTS Operating Cash Balance",
        "description": "Daily Treasury Statement: operating cash balance.",
    },
    {
        "path": "/v1/accounting/dts/public_debt_transactions",
        "name": "DTS Public Debt Transactions",
        "description": "Daily Treasury Statement: public-debt transactions.",
    },
    {
        "path": "/v1/accounting/od/rates_of_exchange",
        "name": "Treasury Reporting Rates of Exchange",
        "description": "Quarterly exchange rates used by federal agencies for foreign-currency reporting.",
    },
]

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


def _build_params(
    *,
    fields: str | None = None,
    page_size: int | None = None,
    filter: str | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    """Build a Fiscal Data params dict honoring the `page[size]` bracket key."""
    params: dict[str, Any] = {}
    if fields:
        params["fields"] = fields
    if page_size is not None:
        params["page[size]"] = page_size
    if filter:
        params["filter"] = filter
    if sort:
        params["sort"] = sort
    return params


###################
# Debt to the Penny
###################


class TreasuryDebtToPennyParams(BaseModel):
    """Parameters for the Debt to the Penny dataset."""

    fields: str | None = Field(
        None,
        description="Comma-separated list of fields to return, e.g. 'record_date,tot_pub_debt_out_amt'.",
    )
    page_size: int = Field(default=100, description="Maximum records per page.")
    filter: str | None = Field(
        None,
        description="Fiscal Data filter expression, e.g. 'record_date:gte:2024-01-01'.",
    )


def fetch_treasury_debt_to_penny(params: TreasuryDebtToPennyParams) -> dict:
    """Call /v2/accounting/od/debt_to_penny on the Fiscal Data API."""
    response = http_get(
        f"{BASE_URL}/v2/accounting/od/debt_to_penny",
        params=_build_params(
            fields=params.fields,
            page_size=params.page_size,
            filter=params.filter,
        ),
        provider=PROVIDER_ID,
    )
    return response.json()


def _treasury_debt_to_penny_to_shape_payload(data: dict) -> dict:
    """Adapt Fiscal Data's ``{data: [{record_date, tot_pub_debt_out_amt, ...}]}``
    response to the ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    The numeric column ``tot_pub_debt_out_amt`` is delivered as a string;
    we coerce defensively and skip non-coercible rows.
    """
    rows = data.get("data") or []
    points: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        date = row.get("record_date")
        raw_value = row.get("tot_pub_debt_out_amt")
        if not isinstance(date, str) or raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        points.append({"date": date, "value": value})
    points.sort(key=lambda p: p["date"])
    return {
        "points": points,
        "axes": {"x": "Date", "y": "Total public debt outstanding (USD)"},
    }


async def handle_treasury_get_debt_to_penny(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the treasury-get-debt-to-penny tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        params = TreasuryDebtToPennyParams(**(arguments or {}))
        data = fetch_treasury_debt_to_penny(params)
        payload = _treasury_debt_to_penny_to_shape_payload(data)
        return [types.TextContent(type="text", text=serialize_for_llm(payload))]
    except Exception as e:
        log.error(f"Error fetching Treasury debt-to-penny: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="treasury-get-debt-to-penny",
        description="Fetch the US Treasury 'Debt to the Penny' dataset (daily total public debt outstanding).",
        input_schema=TreasuryDebtToPennyParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["treasury-get-debt-to-penny"] = handle_treasury_get_debt_to_penny


###################
# Average Interest Rates
###################


class TreasuryAvgInterestRatesParams(BaseModel):
    """Parameters for the Average Interest Rates dataset."""

    page_size: int = Field(default=100, description="Maximum records per page.")
    filter: str | None = Field(
        None,
        description="Fiscal Data filter expression, e.g. 'record_date:gte:2024-01-01'.",
    )


def fetch_treasury_avg_interest_rates(
    params: TreasuryAvgInterestRatesParams,
) -> dict:
    """Call /v2/accounting/od/avg_interest_rates on the Fiscal Data API."""
    response = http_get(
        f"{BASE_URL}/v2/accounting/od/avg_interest_rates",
        params=_build_params(page_size=params.page_size, filter=params.filter),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_treasury_get_avg_interest_rates(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the treasury-get-avg-interest-rates tool call."""
    try:
        params = TreasuryAvgInterestRatesParams(**(arguments or {}))
        data = fetch_treasury_avg_interest_rates(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Treasury avg-interest-rates: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="treasury-get-avg-interest-rates",
        description="Fetch monthly average interest rates on outstanding US Treasury marketable securities.",
        input_schema=TreasuryAvgInterestRatesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["treasury-get-avg-interest-rates"] = (
    handle_treasury_get_avg_interest_rates
)


###################
# DTS Operating Cash Balance
###################


class TreasuryDtsOperatingCashParams(BaseModel):
    """Parameters for the DTS Operating Cash Balance dataset."""

    page_size: int = Field(default=100, description="Maximum records per page.")
    filter: str | None = Field(
        None,
        description="Fiscal Data filter expression, e.g. 'record_date:gte:2024-01-01'.",
    )


def fetch_treasury_dts_operating_cash(
    params: TreasuryDtsOperatingCashParams,
) -> dict:
    """Call /v1/accounting/dts/operating_cash_balance on the Fiscal Data API."""
    response = http_get(
        f"{BASE_URL}/v1/accounting/dts/operating_cash_balance",
        params=_build_params(page_size=params.page_size, filter=params.filter),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_treasury_get_dts_operating_cash(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the treasury-get-dts-operating-cash tool call."""
    try:
        params = TreasuryDtsOperatingCashParams(**(arguments or {}))
        data = fetch_treasury_dts_operating_cash(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Treasury DTS operating cash balance: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="treasury-get-dts-operating-cash",
        description="Fetch the Daily Treasury Statement (DTS) Operating Cash Balance dataset.",
        input_schema=TreasuryDtsOperatingCashParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["treasury-get-dts-operating-cash"] = (
    handle_treasury_get_dts_operating_cash
)


###################
# DTS Public Debt Transactions
###################


class TreasuryDtsPublicDebtParams(BaseModel):
    """Parameters for the DTS Public Debt Transactions dataset."""

    page_size: int = Field(default=100, description="Maximum records per page.")
    filter: str | None = Field(
        None,
        description="Fiscal Data filter expression, e.g. 'record_date:gte:2024-01-01'.",
    )


def fetch_treasury_dts_public_debt(
    params: TreasuryDtsPublicDebtParams,
) -> dict:
    """Call /v1/accounting/dts/public_debt_transactions on the Fiscal Data API."""
    response = http_get(
        f"{BASE_URL}/v1/accounting/dts/public_debt_transactions",
        params=_build_params(page_size=params.page_size, filter=params.filter),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_treasury_get_dts_public_debt(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the treasury-get-dts-public-debt tool call."""
    try:
        params = TreasuryDtsPublicDebtParams(**(arguments or {}))
        data = fetch_treasury_dts_public_debt(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Treasury DTS public-debt transactions: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="treasury-get-dts-public-debt",
        description="Fetch the Daily Treasury Statement (DTS) Public Debt Transactions dataset.",
        input_schema=TreasuryDtsPublicDebtParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["treasury-get-dts-public-debt"] = handle_treasury_get_dts_public_debt


###################
# Exchange Rates
###################


class TreasuryExchangeRatesParams(BaseModel):
    """Parameters for the Treasury Reporting Rates of Exchange dataset."""

    page_size: int = Field(default=100, description="Maximum records per page.")
    filter: str | None = Field(
        None,
        description="Fiscal Data filter expression, e.g. 'record_date:gte:2024-01-01' or 'country_currency_desc:eq:Canada-Dollar'.",
    )


def fetch_treasury_exchange_rates(
    params: TreasuryExchangeRatesParams,
) -> dict:
    """Call /v1/accounting/od/rates_of_exchange on the Fiscal Data API."""
    response = http_get(
        f"{BASE_URL}/v1/accounting/od/rates_of_exchange",
        params=_build_params(page_size=params.page_size, filter=params.filter),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_treasury_get_exchange_rates(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the treasury-get-exchange-rates tool call."""
    try:
        params = TreasuryExchangeRatesParams(**(arguments or {}))
        data = fetch_treasury_exchange_rates(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Treasury exchange rates: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="treasury-get-exchange-rates",
        description="Fetch the US Treasury Reporting Rates of Exchange (quarterly).",
        input_schema=TreasuryExchangeRatesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["treasury-get-exchange-rates"] = handle_treasury_get_exchange_rates


###################
# List of known endpoints
###################


class TreasuryListEndpointsParams(BaseModel):
    """Parameters for listing known Treasury Fiscal Data endpoints."""


def fetch_treasury_list_endpoints(
    params: TreasuryListEndpointsParams,
) -> dict:
    """Return the curated list of known Fiscal Data endpoints this provider wraps."""
    return {"base_url": BASE_URL, "endpoints": KNOWN_ENDPOINTS}


async def handle_treasury_list_endpoints(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the treasury-list-endpoints tool call."""
    try:
        params = TreasuryListEndpointsParams(**(arguments or {}))
        data = fetch_treasury_list_endpoints(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error listing Treasury Fiscal Data endpoints: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="treasury-list-endpoints",
        description="List the curated set of US Treasury Fiscal Data endpoints supported by this provider.",
        input_schema=TreasuryListEndpointsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["treasury-list-endpoints"] = handle_treasury_list_endpoints


###################
# Generic records search
###################


class TreasurySearchRecordsParams(BaseModel):
    """Parameters for a generic Fiscal Data records search."""

    endpoint: str = Field(
        ...,
        description="Fiscal Data endpoint path (without host), e.g. '/v2/accounting/od/debt_to_penny'.",
    )
    fields: str | None = Field(
        None,
        description="Comma-separated list of fields to return.",
    )
    page_size: int = Field(default=100, description="Maximum records per page.")
    filter: str | None = Field(
        None,
        description="Fiscal Data filter expression, e.g. 'record_date:gte:2024-01-01'.",
    )
    sort: str | None = Field(
        None,
        description="Sort key, e.g. '-record_date' for newest-first.",
    )


def fetch_treasury_search_records(
    params: TreasurySearchRecordsParams,
) -> dict:
    """Call an arbitrary Fiscal Data endpoint with standard query params."""
    path = params.endpoint
    if not path.startswith("/"):
        path = "/" + path
    response = http_get(
        f"{BASE_URL}{path}",
        params=_build_params(
            fields=params.fields,
            page_size=params.page_size,
            filter=params.filter,
            sort=params.sort,
        ),
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_treasury_search_records(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the treasury-search-records tool call."""
    try:
        if not arguments or "endpoint" not in arguments:
            raise ValueError("endpoint is required")
        params = TreasurySearchRecordsParams(**arguments)
        data = fetch_treasury_search_records(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error searching Treasury Fiscal Data: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="treasury-search-records",
        description="Query any US Treasury Fiscal Data endpoint with fields/filter/sort/page_size.",
        input_schema=TreasurySearchRecordsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["treasury-search-records"] = handle_treasury_search_records


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-treasury-fiscal",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
