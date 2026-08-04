import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_treasury_fiscal import (
    TOOLS,
    TOOLS_HANDLERS,
    _treasury_debt_to_penny_to_shape_payload,
    handle_treasury_get_avg_interest_rates,
    handle_treasury_get_debt_to_penny,
    handle_treasury_get_dts_operating_cash,
    handle_treasury_get_dts_public_debt,
    handle_treasury_get_exchange_rates,
    handle_treasury_list_endpoints,
    handle_treasury_search_records,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_tools_registered():
    """All registered tools must have a matching handler entry."""
    names = [t.name for t in TOOLS]
    for name in names:
        assert name in TOOLS_HANDLERS


@pytest.fixture
def mock_debt_to_penny_response():
    return {
        "data": [
            {
                "record_date": "2024-01-02",
                "tot_pub_debt_out_amt": "34001493655565.48",
            },
        ],
        "meta": {"count": 1, "total-count": 1, "total-pages": 1},
    }


@pytest.fixture
def mock_avg_interest_rates_response():
    return {
        "data": [
            {
                "record_date": "2024-01-31",
                "security_type_desc": "Marketable",
                "avg_interest_rate_amt": "3.142",
            },
        ],
        "meta": {"count": 1},
    }


@pytest.fixture
def mock_dts_operating_cash_response():
    return {
        "data": [{"record_date": "2024-01-02", "open_today_bal": "700123"}],
        "meta": {"count": 1},
    }


@pytest.fixture
def mock_dts_public_debt_response():
    return {
        "data": [
            {
                "record_date": "2024-01-02",
                "transaction_type": "Public Debt Cash Issues",
            },
        ],
        "meta": {"count": 1},
    }


@pytest.fixture
def mock_exchange_rates_response():
    return {
        "data": [
            {
                "record_date": "2023-12-31",
                "country_currency_desc": "Canada-Dollar",
                "exchange_rate": "1.330",
            },
        ],
        "meta": {"count": 1},
    }


@pytest.mark.anyio
async def test_treasury_get_debt_to_penny_success(mock_debt_to_penny_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_debt_to_penny_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_treasury_get_debt_to_penny(
            {"page_size": 5, "filter": "record_date:gte:2024-01-01"},
        )
        assert "points" in result[0].text
        # Verify page[size] query key is forwarded properly through httpx params dict.
        sent_params = mock_get.call_args.kwargs["params"]
        assert sent_params["page[size]"] == 5
        assert sent_params["filter"] == "record_date:gte:2024-01-01"


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for treasury-get-debt-to-penny.
# ---------------------------------------------------------------------------


def test_treasury_debt_adapter_flattens_to_points():
    raw = {
        "data": [
            {"record_date": "2024-01-02", "tot_pub_debt_out_amt": "34001493655565.48"},
            {"record_date": "2024-01-03", "tot_pub_debt_out_amt": "34010000000000.00"},
        ],
    }
    payload = _treasury_debt_to_penny_to_shape_payload(raw)
    assert payload["axes"]["x"] == "Date"
    assert payload["axes"]["y"] == "Total public debt outstanding (USD)"
    assert len(payload["points"]) == 2
    assert payload["points"][0]["value"] == 34001493655565.48


def test_treasury_debt_adapter_handles_empty():
    payload = _treasury_debt_to_penny_to_shape_payload({"data": []})
    assert payload["points"] == []


def test_treasury_debt_adapter_skips_non_numeric():
    raw = {
        "data": [
            {"record_date": "2024-01-02", "tot_pub_debt_out_amt": "bad"},
            {"record_date": "2024-01-03", "tot_pub_debt_out_amt": "1.0"},
            {"record_date": "2024-01-04"},  # missing field
        ],
    }
    payload = _treasury_debt_to_penny_to_shape_payload(raw)
    assert len(payload["points"]) == 1
    assert payload["points"][0]["date"] == "2024-01-03"


def test_treasury_debt_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "treasury-get-debt-to-penny")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_treasury_debt_to_penny_returns_shape_payload(
    mock_debt_to_penny_response,
):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_debt_to_penny_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_treasury_get_debt_to_penny({})
        body = json.loads(result[0].text)
        assert body["axes"]["x"] == "Date"
        assert len(body["points"]) == 1
        assert body["points"][0]["date"] == "2024-01-02"


@pytest.mark.anyio
async def test_treasury_get_debt_to_penny_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_treasury_get_debt_to_penny({})


@pytest.mark.anyio
async def test_treasury_get_avg_interest_rates_success(
    mock_avg_interest_rates_response,
):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_avg_interest_rates_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_treasury_get_avg_interest_rates({"page_size": 1})
        assert "avg_interest_rate_amt" in result[0].text


@pytest.mark.anyio
async def test_treasury_get_dts_operating_cash_success(
    mock_dts_operating_cash_response,
):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_dts_operating_cash_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_treasury_get_dts_operating_cash({"page_size": 1})
        assert "open_today_bal" in result[0].text


@pytest.mark.anyio
async def test_treasury_get_dts_public_debt_success(mock_dts_public_debt_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_dts_public_debt_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_treasury_get_dts_public_debt({"page_size": 1})
        assert "Public Debt Cash Issues" in result[0].text


@pytest.mark.anyio
async def test_treasury_get_exchange_rates_success(mock_exchange_rates_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_exchange_rates_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_treasury_get_exchange_rates(
            {"page_size": 1, "filter": "country_currency_desc:eq:Canada-Dollar"},
        )
        assert "Canada-Dollar" in result[0].text


@pytest.mark.anyio
async def test_treasury_list_endpoints_success():
    # No HTTP call expected; this is purely a curated list.
    result = await handle_treasury_list_endpoints({})
    assert "debt_to_penny" in result[0].text
    assert "rates_of_exchange" in result[0].text


@pytest.mark.anyio
async def test_treasury_search_records_success(mock_debt_to_penny_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_debt_to_penny_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_treasury_search_records(
            {
                "endpoint": "/v2/accounting/od/debt_to_penny",
                "fields": "record_date,tot_pub_debt_out_amt",
                "page_size": 10,
                "filter": "record_date:gte:2024-01-01",
                "sort": "-record_date",
            },
        )
        assert "tot_pub_debt_out_amt" in result[0].text
        sent_params = mock_get.call_args.kwargs["params"]
        assert sent_params["fields"] == "record_date,tot_pub_debt_out_amt"
        assert sent_params["sort"] == "-record_date"
        assert sent_params["page[size]"] == 10


@pytest.mark.anyio
async def test_treasury_search_records_missing_endpoint():
    with pytest.raises(ValueError):
        await handle_treasury_search_records({})
