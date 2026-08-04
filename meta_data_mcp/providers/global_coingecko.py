"""CoinGecko Cryptocurrency Data Provider (free public tier)

This module exposes CoinGecko's public REST endpoints for cryptocurrency
market data. The free, key-less tier is rate-limited to roughly 30 calls
per minute; sustained heavy use should switch to a paid API key.

License / source:
    Data is provided by CoinGecko under their public terms of service.
    Consult https://www.coingecko.com/en/api/terms for redistribution
    rules and attribution requirements.

Features:
- Simple spot prices for one or more coins versus one or more fiats
- Full coin list (id / symbol / name)
- Market data table (top coins, sorted by market cap)
- Detailed coin metadata
- Historical snapshot for a specific date
- Time-series market chart for a coin
- Trending searches
- Global market summary

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI
from meta_data_mcp.utils import http_get, serialize_for_llm

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-coingecko"
BASE_URL = "https://api.coingecko.com/api/v3"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Simple Price
###################


class CoinGeckoSimplePriceParams(BaseModel):
    """Parameters for fetching simple spot prices."""

    ids: str = Field(
        ...,
        description="Comma-separated CoinGecko coin IDs (e.g. 'bitcoin,ethereum').",
    )
    vs_currencies: str = Field(
        default="usd",
        description="Comma-separated quote currencies (e.g. 'usd,eur').",
    )
    include_market_cap: bool = Field(
        default=True,
        description="Include market cap data in the response.",
    )
    include_24hr_change: bool = Field(
        default=True,
        description="Include 24-hour price change data.",
    )


def fetch_coingecko_simple_price(params: CoinGeckoSimplePriceParams) -> dict:
    """Fetch simple spot prices from CoinGecko."""
    query_params: dict[str, Any] = {
        "ids": params.ids,
        "vs_currencies": params.vs_currencies,
        "include_market_cap": str(params.include_market_cap).lower(),
        "include_24hr_change": str(params.include_24hr_change).lower(),
    }
    response = http_get(
        f"{BASE_URL}/simple/price",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_coingecko_simple_price(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the coingecko-simple-price tool call."""
    try:
        if not arguments or "ids" not in arguments:
            raise ValueError("ids is required")
        params = CoinGeckoSimplePriceParams(**arguments)
        data = fetch_coingecko_simple_price(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CoinGecko simple price: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="coingecko-simple-price",
        description="Get simple spot prices for one or more coins versus one or more fiat currencies.",
        input_schema=CoinGeckoSimplePriceParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["coingecko-simple-price"] = handle_coingecko_simple_price


###################
# List coins
###################


class CoinGeckoListCoinsParams(BaseModel):
    """Parameters for listing all coins."""

    include_platform: bool = Field(
        default=False,
        description="Include platform contract address info per coin.",
    )


def fetch_coingecko_list_coins(params: CoinGeckoListCoinsParams) -> list:
    """Fetch the list of all CoinGecko coins."""
    query_params: dict[str, Any] = {
        "include_platform": str(params.include_platform).lower(),
    }
    response = http_get(
        f"{BASE_URL}/coins/list",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_coingecko_list_coins(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the coingecko-list-coins tool call."""
    try:
        params = CoinGeckoListCoinsParams(**(arguments or {}))
        data = fetch_coingecko_list_coins(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CoinGecko coin list: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="coingecko-list-coins",
        description="List every coin known to CoinGecko (id, symbol, name).",
        input_schema=CoinGeckoListCoinsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["coingecko-list-coins"] = handle_coingecko_list_coins


###################
# Coins markets
###################


class CoinGeckoCoinsMarketsParams(BaseModel):
    """Parameters for fetching the markets table."""

    vs_currency: str = Field(
        default="usd",
        description="Quote currency (single ISO 4217 fiat code, e.g. 'usd').",
    )
    ids: str | None = Field(
        default=None,
        description="Optional comma-separated CoinGecko coin IDs to filter to.",
    )
    order: str = Field(
        default="market_cap_desc",
        description="Sort order (e.g. market_cap_desc, volume_desc, id_asc).",
    )
    per_page: int = Field(
        default=100,
        ge=1,
        le=250,
        description="Results per page (1-250).",
    )
    page: int = Field(default=1, ge=1, description="Page number (1-indexed).")


def fetch_coingecko_coins_markets(params: CoinGeckoCoinsMarketsParams) -> list:
    """Fetch markets data from CoinGecko."""
    query_params: dict[str, Any] = {
        "vs_currency": params.vs_currency,
        "order": params.order,
        "per_page": params.per_page,
        "page": params.page,
    }
    if params.ids:
        query_params["ids"] = params.ids
    response = http_get(
        f"{BASE_URL}/coins/markets",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_coingecko_coins_markets(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the coingecko-coins-markets tool call."""
    try:
        params = CoinGeckoCoinsMarketsParams(**(arguments or {}))
        data = fetch_coingecko_coins_markets(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CoinGecko markets: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="coingecko-coins-markets",
        description="Get top coins by market cap (or a filtered list) with price, volume, and cap data.",
        input_schema=CoinGeckoCoinsMarketsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["coingecko-coins-markets"] = handle_coingecko_coins_markets


###################
# Get coin
###################


class CoinGeckoGetCoinParams(BaseModel):
    """Parameters for fetching detailed coin metadata."""

    id: str = Field(..., description="CoinGecko coin ID (e.g. 'bitcoin', 'ethereum').")


def fetch_coingecko_get_coin(params: CoinGeckoGetCoinParams) -> dict:
    """Fetch full coin metadata from CoinGecko."""
    query_params: dict[str, Any] = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
    }
    response = http_get(
        f"{BASE_URL}/coins/{params.id}",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_coingecko_get_coin(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the coingecko-get-coin tool call."""
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = CoinGeckoGetCoinParams(**arguments)
        data = fetch_coingecko_get_coin(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CoinGecko coin {arguments}: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="coingecko-get-coin",
        description="Get detailed metadata and market data for a single coin by CoinGecko ID.",
        input_schema=CoinGeckoGetCoinParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["coingecko-get-coin"] = handle_coingecko_get_coin


###################
# Coin history
###################


class CoinGeckoCoinHistoryParams(BaseModel):
    """Parameters for fetching a historical snapshot for a coin."""

    id: str = Field(..., description="CoinGecko coin ID.")
    date: str = Field(
        ...,
        pattern=r"^\d{2}-\d{2}-\d{4}$",
        description="Snapshot date in DD-MM-YYYY format (CoinGecko convention).",
    )


def fetch_coingecko_coin_history(params: CoinGeckoCoinHistoryParams) -> dict:
    """Fetch a historical coin snapshot from CoinGecko."""
    query_params: dict[str, Any] = {"date": params.date}
    response = http_get(
        f"{BASE_URL}/coins/{params.id}/history",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_coingecko_coin_history(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the coingecko-coin-history tool call."""
    try:
        if not arguments or "id" not in arguments or "date" not in arguments:
            raise ValueError("id and date are required")
        params = CoinGeckoCoinHistoryParams(**arguments)
        data = fetch_coingecko_coin_history(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CoinGecko coin history: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="coingecko-coin-history",
        description="Get a historical snapshot for a coin on a specific date (DD-MM-YYYY).",
        input_schema=CoinGeckoCoinHistoryParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["coingecko-coin-history"] = handle_coingecko_coin_history


###################
# Coin market chart
###################


class CoinGeckoCoinMarketChartParams(BaseModel):
    """Parameters for fetching a market chart time series."""

    id: str = Field(..., description="CoinGecko coin ID.")
    vs_currency: str = Field(
        default="usd",
        description="Quote currency (e.g. 'usd', 'eur').",
    )
    days: str = Field(
        default="30",
        description="Number of days back (1, 7, 14, 30, 90, 180, 365, 'max').",
    )


def fetch_coingecko_coin_market_chart(
    params: CoinGeckoCoinMarketChartParams,
) -> dict:
    """Fetch a market chart series from CoinGecko."""
    query_params: dict[str, Any] = {
        "vs_currency": params.vs_currency,
        "days": params.days,
    }
    response = http_get(
        f"{BASE_URL}/coins/{params.id}/market_chart",
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _coingecko_market_chart_to_shape_payload(data: dict, vs_currency: str = "") -> dict:
    """Adapt CoinGecko's ``{prices: [[ts_ms, v], ...], market_caps: [...],
    total_volumes: [...]}`` response to the
    ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    The three vectors are emitted as separate series so multi-pane viewers
    can keep them on independent scales. Timestamps are millisecond Unix
    epochs; we convert to ISO-8601 strings.
    """
    series_map = {
        "prices": "Price",
        "market_caps": "Market cap",
        "total_volumes": "Volume",
    }
    points: list[dict[str, Any]] = []
    for raw_key, series_label in series_map.items():
        for pair in data.get(raw_key) or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            ts_ms, value = pair[0], pair[1]
            if not isinstance(ts_ms, (int, float)) or isinstance(ts_ms, bool):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            try:
                date = (
                    datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            except (OverflowError, OSError, ValueError):
                continue
            points.append({"date": date, "value": value, "series": series_label})

    points.sort(key=lambda p: (p["date"], p["series"]))
    y_label = f"Value ({vs_currency.upper()})" if vs_currency else "Value"
    return {
        "points": points,
        "axes": {"x": "Time", "y": y_label},
    }


async def handle_coingecko_coin_market_chart(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the coingecko-coin-market-chart tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        if not arguments or "id" not in arguments:
            raise ValueError("id is required")
        params = CoinGeckoCoinMarketChartParams(**arguments)
        data = fetch_coingecko_coin_market_chart(params)
        payload = _coingecko_market_chart_to_shape_payload(
            data,
            vs_currency=params.vs_currency,
        )
        return [types.TextContent(type="text", text=serialize_for_llm(payload))]
    except Exception as e:
        log.error(f"Error fetching CoinGecko market chart: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="coingecko-coin-market-chart",
        description="Get historical price/market-cap/volume series for a coin over N days.",
        input_schema=CoinGeckoCoinMarketChartParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["coingecko-coin-market-chart"] = handle_coingecko_coin_market_chart


###################
# Search trending
###################


class CoinGeckoSearchTrendingParams(BaseModel):
    """Parameters for fetching trending searches (no inputs)."""


def fetch_coingecko_search_trending(_: CoinGeckoSearchTrendingParams) -> dict:
    """Fetch trending searches from CoinGecko."""
    response = http_get(f"{BASE_URL}/search/trending", provider=PROVIDER_ID)
    return response.json()


async def handle_coingecko_search_trending(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the coingecko-search-trending tool call."""
    try:
        params = CoinGeckoSearchTrendingParams(**(arguments or {}))
        data = fetch_coingecko_search_trending(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CoinGecko trending: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="coingecko-search-trending",
        description="Get the current trending searches on CoinGecko.",
        input_schema=CoinGeckoSearchTrendingParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["coingecko-search-trending"] = handle_coingecko_search_trending


###################
# Global
###################


class CoinGeckoGlobalParams(BaseModel):
    """Parameters for the global market summary (no inputs)."""


def fetch_coingecko_global(_: CoinGeckoGlobalParams) -> dict:
    """Fetch the global market summary from CoinGecko."""
    response = http_get(f"{BASE_URL}/global", provider=PROVIDER_ID)
    return response.json()


async def handle_coingecko_global(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the coingecko-global tool call."""
    try:
        params = CoinGeckoGlobalParams(**(arguments or {}))
        data = fetch_coingecko_global(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching CoinGecko global: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="coingecko-global",
        description="Get the global cryptocurrency market summary (total market cap, dominance, etc.).",
        input_schema=CoinGeckoGlobalParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["coingecko-global"] = handle_coingecko_global


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-coingecko",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
