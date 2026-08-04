import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_coingecko import (
    TOOLS,
    _coingecko_market_chart_to_shape_payload,
    handle_coingecko_coin_history,
    handle_coingecko_coin_market_chart,
    handle_coingecko_coins_markets,
    handle_coingecko_get_coin,
    handle_coingecko_global,
    handle_coingecko_list_coins,
    handle_coingecko_search_trending,
    handle_coingecko_simple_price,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_coingecko_simple_price_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "bitcoin": {"usd": 60000, "usd_market_cap": 1.2e12, "usd_24h_change": 2.5},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_coingecko_simple_price(
            {"ids": "bitcoin", "vs_currencies": "usd"},
        )
        assert "bitcoin" in result[0].text
        assert "60000" in result[0].text


@pytest.mark.anyio
async def test_coingecko_simple_price_missing_ids():
    with pytest.raises(Exception):
        await handle_coingecko_simple_price({})


@pytest.mark.anyio
async def test_coingecko_simple_price_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Rate limited")

        with pytest.raises(httpx.HTTPError):
            await handle_coingecko_simple_price({"ids": "bitcoin"})


@pytest.mark.anyio
async def test_coingecko_list_coins_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"id": "bitcoin", "symbol": "btc", "name": "Bitcoin"},
            {"id": "ethereum", "symbol": "eth", "name": "Ethereum"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_coingecko_list_coins()
        assert "Bitcoin" in result[0].text
        assert "Ethereum" in result[0].text


@pytest.mark.anyio
async def test_coingecko_coins_markets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"id": "bitcoin", "current_price": 60000, "market_cap": 1.2e12},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_coingecko_coins_markets(
            {"vs_currency": "usd", "per_page": 1},
        )
        assert "bitcoin" in result[0].text


@pytest.mark.anyio
async def test_coingecko_get_coin_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "id": "bitcoin",
            "name": "Bitcoin",
            "market_data": {"current_price": {"usd": 60000}},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_coingecko_get_coin({"id": "bitcoin"})
        assert "Bitcoin" in result[0].text


@pytest.mark.anyio
async def test_coingecko_coin_history_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "id": "bitcoin",
            "market_data": {"current_price": {"usd": 40000}},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_coingecko_coin_history(
            {"id": "bitcoin", "date": "01-01-2023"},
        )
        assert "40000" in result[0].text


@pytest.mark.anyio
async def test_coingecko_coin_market_chart_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "prices": [[1700000000000, 50000], [1700100000000, 51000]],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_coingecko_coin_market_chart(
            {"id": "bitcoin", "days": "7"},
        )
        assert "50000" in result[0].text
        assert "points" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for coingecko-coin-market-chart.
# ---------------------------------------------------------------------------


def test_coingecko_market_chart_adapter_flattens_three_series():
    raw = {
        "prices": [[1700000000000, 50000.0]],
        "market_caps": [[1700000000000, 1.2e12]],
        "total_volumes": [[1700000000000, 25e9]],
    }
    payload = _coingecko_market_chart_to_shape_payload(raw, vs_currency="usd")
    assert payload["axes"] == {"x": "Time", "y": "Value (USD)"}
    series = {p["series"] for p in payload["points"]}
    assert series == {"Price", "Market cap", "Volume"}
    assert len(payload["points"]) == 3
    # ISO-8601 with Z suffix
    assert payload["points"][0]["date"].endswith("Z")


def test_coingecko_market_chart_adapter_handles_empty():
    payload = _coingecko_market_chart_to_shape_payload({})
    assert payload["points"] == []
    assert payload["axes"]["y"] == "Value"


def test_coingecko_market_chart_adapter_skips_malformed():
    raw = {
        "prices": [
            [1700000000000, 50000.0],
            "not-a-pair",
            [1700000000000, "bad"],
            [None, 1.0],
        ],
    }
    payload = _coingecko_market_chart_to_shape_payload(raw)
    assert len(payload["points"]) == 1


def test_coingecko_market_chart_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "coingecko-coin-market-chart")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_coingecko_market_chart_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "prices": [[1700000000000, 50000.0], [1700100000000, 51000.0]],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_coingecko_coin_market_chart(
            {"id": "bitcoin", "vs_currency": "usd", "days": "1"},
        )
        body = json.loads(result[0].text)
        assert body["axes"]["y"] == "Value (USD)"
        assert len(body["points"]) == 2
        assert all(p["series"] == "Price" for p in body["points"])


@pytest.mark.anyio
async def test_coingecko_search_trending_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "coins": [{"item": {"id": "pepe", "name": "Pepe"}}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_coingecko_search_trending()
        assert "Pepe" in result[0].text


@pytest.mark.anyio
async def test_coingecko_global_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {
                "active_cryptocurrencies": 10000,
                "total_market_cap": {"usd": 2.5e12},
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_coingecko_global()
        assert "active_cryptocurrencies" in result[0].text
