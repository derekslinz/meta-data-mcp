"""Tests for the global-un-comtrade provider."""

from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.global_un_comtrade import (
    TOOLS,
    ComtradeTradeDataParams,
    fetch_comtrade_trade_data,
    handle_comtrade_trade_data,
)
from meta_data_mcp.ui_resources.app_trade_flows_v1 import URI as TRADE_FLOWS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _ok(payload: dict) -> Mock:
    r = Mock()
    r.json.return_value = payload
    r.raise_for_status = Mock()
    r.status_code = 200
    r.headers = {}
    return r


def test_required_params_sent():
    """typeCode, freqCode, clCode go in the URL path; rest are query params."""
    payload = {"count": 0, "data": []}
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        params = ComtradeTradeDataParams(period="2022", reporter_code="840")
        fetch_comtrade_trade_data(params)
        # URL path: /get/{typeCode}/{freqCode}/{clCode}
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://comtradeapi.un.org/data/v1/get/C/A/HS"
        sent = mock_get.call_args[1]["params"]
        # These must NOT appear as query params anymore.
        assert "typeCode" not in sent
        assert "freqCode" not in sent
        assert "clCode" not in sent
        # These remain as query params.
        assert sent["period"] == "2022"
        assert sent["reporterCode"] == "840"
        assert sent["maxRecords"] == 500


def test_optional_filters_passed_through():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"data": []})
        params = ComtradeTradeDataParams(
            period="202301,202302",
            reporter_code="840",
            partner_code="156",
            cmd_code="1001",
            flow_code="M,X",
            freq_code="M",
        )
        fetch_comtrade_trade_data(params)
        # freq_code='M' must end up in the URL path now, not query.
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://comtradeapi.un.org/data/v1/get/C/M/HS"
        sent = mock_get.call_args[1]["params"]
        assert sent["partnerCode"] == "156"
        assert sent["cmdCode"] == "1001"
        assert sent["flowCode"] == "M,X"
        # freqCode no longer appears as a query param.
        assert "freqCode" not in sent


def test_validates_required_fields():
    with pytest.raises(Exception):
        ComtradeTradeDataParams(period="", reporter_code="840")
    with pytest.raises(Exception):
        ComtradeTradeDataParams(period="2022", reporter_code="")
    with pytest.raises(Exception):
        ComtradeTradeDataParams(period="22", reporter_code="840")  # period too short


def test_max_records_cap():
    with pytest.raises(Exception):
        ComtradeTradeDataParams(period="2022", reporter_code="840", max_records=501)


def test_subscription_key_sent_when_env_set(monkeypatch):
    monkeypatch.setenv("UN_COMTRADE_API_KEY", "subscr-key")
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"data": []})
        fetch_comtrade_trade_data(
            ComtradeTradeDataParams(period="2022", reporter_code="840"),
        )
        sent_headers = mock_get.call_args[1]["headers"]
        assert sent_headers["Ocp-Apim-Subscription-Key"] == "subscr-key"


@pytest.mark.anyio
async def test_handle():
    payload = {"data": [{"period": 2022, "reporterDesc": "USA", "tradeValue": 999}]}
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        result = await handle_comtrade_trade_data(
            {"period": "2022", "reporter_code": "840"},
        )
        assert "USA" in result[0].text


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps shape primitive binding (trade-flows app).
# ---------------------------------------------------------------------------


def test_comtrade_trade_data_tool_binds_to_trade_flows_app():
    """comtrade-trade-data renders through the Phase 5 trade-flows app."""
    tool = next(t for t in TOOLS if t.name == "comtrade-trade-data")
    assert tool.meta == {"ui": {"resourceUri": TRADE_FLOWS_URI}}, (
        f"comtrade-trade-data is not bound to {TRADE_FLOWS_URI}"
    )
    # Pin that the binding survives the wire-format round-trip — the
    # _meta= alias keyword must reach the on-the-wire ``_meta`` field,
    # not the silent ``meta=`` extras bucket. See tests/test_ui_resource.py
    # ::test_tool_meta_constructor_kwarg_does_not_reach_wire for the
    # underlying footgun this guards against.
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TRADE_FLOWS_URI
