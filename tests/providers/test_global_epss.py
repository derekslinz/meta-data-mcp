"""Tests for the global-epss provider."""

from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_epss import (
    TOOLS,
    EpssScoresParams,
    fetch_epss_scores,
    handle_epss_scores,
)
from meta_data_mcp.ui_resources.app_vulnerability_v1 import URI as VULN_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_epss_payload():
    return {
        "status": "OK",
        "status-code": 200,
        "version": "1.0",
        "access": "public",
        "total": 2,
        "data": [
            {"cve": "CVE-2021-44228", "epss": "0.97500", "percentile": "1.00000"},
            {"cve": "CVE-2024-3094", "epss": "0.92000", "percentile": "0.99500"},
        ],
    }


def _stub_get(payload: dict) -> Mock:
    mock_get = Mock()
    mock_get.return_value.json.return_value = payload
    mock_get.return_value.raise_for_status = Mock()
    mock_get.return_value.status_code = 200
    return mock_get


def test_fetch_epss_scores_with_cve_list(mock_epss_payload):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_epss_payload
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200

        params = EpssScoresParams(cve="CVE-2021-44228,CVE-2024-3094")
        result = fetch_epss_scores(params)

        assert result["total"] == 2
        assert result["data"][0]["cve"] == "CVE-2021-44228"
        # http_get embeds the URL as the first positional arg.
        assert mock_get.call_args[0][0] == "https://api.first.org/data/v1/epss"
        assert mock_get.call_args[1]["params"]["cve"] == "CVE-2021-44228,CVE-2024-3094"
        # http_get always sets envelope=true and pretty=false for stable JSON.
        assert mock_get.call_args[1]["params"]["envelope"] == "true"


def test_fetch_epss_scores_with_filter_combination(mock_epss_payload):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_epss_payload
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200

        params = EpssScoresParams(
            days=7,
            epss_gt=0.5,
            order="!epss",
            limit=50,
            offset=0,
        )
        fetch_epss_scores(params)

        sent = mock_get.call_args[1]["params"]
        assert sent["days"] == 7
        assert sent["epss-gt"] == 0.5
        assert sent["order"] == "!epss"
        assert sent["limit"] == 50


def test_epss_scores_rejects_out_of_range_probability():
    with pytest.raises(Exception):
        EpssScoresParams(epss_gt=1.5)
    with pytest.raises(Exception):
        EpssScoresParams(percentile_gt=200.0)
    with pytest.raises(Exception):
        EpssScoresParams(days=0)
    with pytest.raises(Exception):
        EpssScoresParams(days=31)


@pytest.mark.anyio
async def test_handle_epss_scores(mock_epss_payload):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_epss_payload
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200

        result = await handle_epss_scores({"cve": "CVE-2021-44228"})
        assert len(result) == 1
        assert "CVE-2021-44228" in result[0].text


@pytest.mark.anyio
async def test_handle_epss_scores_translates_404_via_provider_kwarg():
    """Provider= kwarg in http_get must translate upstream 404 to NotFoundError."""
    from meta_data_mcp.errors import NotFoundError

    req = httpx.Request("GET", "https://api.first.org/data/v1/epss")
    resp = httpx.Response(status_code=404, request=req)
    status_err = httpx.HTTPStatusError("not found", request=req, response=resp)

    with patch("httpx.get") as mock_get:
        mock_get.return_value.raise_for_status = Mock(side_effect=status_err)
        mock_get.return_value.status_code = 404

        with pytest.raises(NotFoundError) as exc_info:
            await handle_epss_scores({"cve": "CVE-9999-99999"})

        assert exc_info.value.provider == "global-epss"
        assert "api.first.org" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps shape primitive binding (vulnerability app).
# ---------------------------------------------------------------------------


def test_epss_scores_tool_binds_to_vulnerability_app():
    """epss-scores renders through the Phase 5 vulnerability app."""
    tool = next(t for t in TOOLS if t.name == "epss-scores")
    assert tool.meta == {"ui": {"resourceUri": VULN_URI}}, (
        f"epss-scores is not bound to {VULN_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == VULN_URI
