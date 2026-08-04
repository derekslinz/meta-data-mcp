import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_frankfurter import (
    TOOLS,
    _frankfurter_time_series_to_shape_payload,
    handle_frankfurter_convert,
    handle_frankfurter_currencies,
    handle_frankfurter_historical,
    handle_frankfurter_latest,
    handle_frankfurter_time_series,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_frankfurter_latest_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "amount": 1.0,
            "base": "USD",
            "date": "2024-04-01",
            "rates": {"EUR": 0.92, "CHF": 0.91, "GBP": 0.79},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_frankfurter_latest(
            {"base": "USD", "targets": "EUR,CHF,GBP"},
        )
        assert "EUR" in result[0].text
        assert "0.92" in result[0].text


@pytest.mark.anyio
async def test_frankfurter_latest_default_base():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "amount": 1.0,
            "base": "USD",
            "rates": {"EUR": 0.92},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_frankfurter_latest()
        assert "USD" in result[0].text


@pytest.mark.anyio
async def test_frankfurter_latest_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network failure")

        with pytest.raises(httpx.HTTPError):
            await handle_frankfurter_latest()


@pytest.mark.anyio
async def test_frankfurter_historical_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "amount": 1.0,
            "base": "USD",
            "date": "2024-01-02",
            "rates": {"EUR": 0.91},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_frankfurter_historical(
            {"date": "2024-01-02", "base": "USD", "targets": "EUR"},
        )
        assert "2024-01-02" in result[0].text
        assert "0.91" in result[0].text


@pytest.mark.anyio
async def test_frankfurter_historical_missing_date():
    with pytest.raises(Exception):
        await handle_frankfurter_historical({})


@pytest.mark.anyio
async def test_frankfurter_time_series_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "amount": 1.0,
            "base": "USD",
            "start_date": "2024-01-01",
            "end_date": "2024-01-03",
            "rates": {
                "2024-01-02": {"EUR": 0.91},
                "2024-01-03": {"EUR": 0.90},
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_frankfurter_time_series(
            {"start": "2024-01-01", "end": "2024-01-03", "targets": "EUR"},
        )
        assert "0.91" in result[0].text
        assert "2024-01-03" in result[0].text


@pytest.mark.anyio
async def test_frankfurter_currencies_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "USD": "United States Dollar",
            "EUR": "Euro",
            "GBP": "British Pound",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_frankfurter_currencies()
        assert "Euro" in result[0].text


@pytest.mark.anyio
async def test_frankfurter_convert_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "amount": 100.0,
            "base": "USD",
            "date": "2024-04-01",
            "rates": {"EUR": 92.0},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_frankfurter_convert(
            {"amount": 100.0, "base": "USD", "target": "EUR"},
        )
        assert "92.0" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for frankfurter-time-series.
#
# - Adapter converts Frankfurter's nested {rates: {date: {ccy: v}}} to the
#   shape primitive's {points: [{date, value, series}], axes: {x, y}}.
# - Tool's _meta wires it to ui://meta-data-mcp/shape/timeseries/v1 so the
#   MCP Apps host renders the chart inline.
# ---------------------------------------------------------------------------


def test_adapter_flattens_rates_into_points_per_currency():
    raw = {
        "amount": 1.0,
        "base": "USD",
        "start_date": "2024-01-01",
        "end_date": "2024-01-03",
        "rates": {
            "2024-01-02": {"EUR": 0.91, "GBP": 0.78},
            "2024-01-03": {"EUR": 0.90, "GBP": 0.77},
        },
    }
    payload = _frankfurter_time_series_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Date", "y": "Rate (per 1 USD)"}
    assert payload["amount"] == 1.0
    assert payload["base"] == "USD"
    assert payload["start_date"] == "2024-01-01"
    assert payload["end_date"] == "2024-01-03"
    # 2 dates × 2 currencies → 4 points, dates sorted ascending.
    assert len(payload["points"]) == 4
    assert payload["points"][0] == {
        "date": "2024-01-02",
        "value": 0.91,
        "series": "EUR",
    }
    series = {p["series"] for p in payload["points"]}
    assert series == {"EUR", "GBP"}


def test_adapter_handles_empty_rates():
    payload = _frankfurter_time_series_to_shape_payload({"base": "USD", "rates": {}})
    assert payload["points"] == []
    assert payload["axes"]["x"] == "Date"


def test_adapter_falls_back_to_generic_y_label_when_base_missing():
    payload = _frankfurter_time_series_to_shape_payload({"rates": {}})
    assert payload["axes"]["y"] == "Rate"


def test_adapter_skips_non_numeric_values_defensively():
    raw = {
        "base": "USD",
        "rates": {"2024-01-02": {"EUR": "bad", "GBP": 0.78, "CHF": True}},
    }
    payload = _frankfurter_time_series_to_shape_payload(raw)
    assert len(payload["points"]) == 1
    assert payload["points"][0]["series"] == "GBP"


def test_time_series_tool_binds_to_timeseries_shape_primitive():
    """The tool's _meta.ui.resourceUri must point at the canonical
    timeseries shape URI. If this drifts, the MCP Apps host stops
    rendering the chart inline.
    """
    tool = next(t for t in TOOLS if t.name == "frankfurter-time-series")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}, (
        "frankfurter-time-series is not bound to "
        f"{TIMESERIES_URI} — regenerated registration may have dropped _meta="
    )
    # Wire-level: by_alias must emit _meta so the binding actually reaches
    # the host. This guards against any future SDK regression on the
    # populate_by_name footgun.
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_frankfurter_time_series_returns_shape_payload():
    """Handler now returns the shape primitive's payload format. The
    LLM consumer sees points/axes instead of the legacy nested-rates
    dict — this is the v2.0 Phase 4 shift.
    """
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "amount": 1.0,
            "base": "USD",
            "start_date": "2024-01-01",
            "end_date": "2024-01-03",
            "rates": {
                "2024-01-02": {"EUR": 0.91},
                "2024-01-03": {"EUR": 0.90},
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_frankfurter_time_series(
            {"start": "2024-01-01", "end": "2024-01-03", "targets": "EUR"},
        )
        body = json.loads(result[0].text)
        assert "points" in body and "axes" in body
        assert body["axes"] == {"x": "Date", "y": "Rate (per 1 USD)"}
        assert body["points"] == [
            {"date": "2024-01-02", "value": 0.91, "series": "EUR"},
            {"date": "2024-01-03", "value": 0.90, "series": "EUR"},
        ]
        assert body["amount"] == 1.0
        assert body["base"] == "USD"
        assert body["start_date"] == "2024-01-01"
        assert body["end_date"] == "2024-01-03"
