import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_sec_edgar import (
    TOOLS,
    TOOLS_HANDLERS,
    _edgar_company_concept_to_shape_payload,
    _pad_cik,
    handle_edgar_get_company_concept,
    handle_edgar_get_company_facts,
    handle_edgar_get_company_submissions,
    handle_edgar_get_frames,
    handle_edgar_list_tickers,
    handle_edgar_search_by_name,
    handle_edgar_search_by_ticker,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_tools_registered():
    names = [t.name for t in TOOLS]
    for name in names:
        assert name in TOOLS_HANDLERS


def test_pad_cik():
    assert _pad_cik("320193") == "0000320193"
    assert _pad_cik(320193) == "0000320193"
    assert _pad_cik("0000320193") == "0000320193"


@pytest.mark.anyio
async def test_edgar_get_company_submissions_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "cik": "320193",
            "name": "Apple Inc.",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_edgar_get_company_submissions({"cik": "320193"})
        assert len(result) == 1
        assert "Apple" in result[0].text


@pytest.mark.anyio
async def test_edgar_get_company_submissions_missing_cik():
    with pytest.raises(ValueError):
        await handle_edgar_get_company_submissions({})


@pytest.mark.anyio
async def test_edgar_get_company_concept_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "cik": 320193,
            "tag": "Revenues",
            "label": "Revenues",
            "units": {
                "USD": [
                    {"end": "2022-09-24", "val": 394328000000, "fy": 2022},
                    {"end": "2023-09-30", "val": 383285000000, "fy": 2023},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_edgar_get_company_concept(
            {"cik": "320193", "concept": "Revenues"},
        )
        assert "Revenues" in result[0].text
        assert "points" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for edgar-get-company-concept.
# ---------------------------------------------------------------------------


def test_edgar_concept_adapter_flattens_units_to_points():
    raw = {
        "cik": 320193,
        "tag": "Revenues",
        "label": "Revenues",
        "units": {
            "USD": [
                {"end": "2022-09-24", "val": 394328000000},
                {"end": "2023-09-30", "val": 383285000000},
            ],
        },
    }
    payload = _edgar_company_concept_to_shape_payload(raw)
    assert payload["axes"]["y"] == "Revenues (USD)"
    assert payload["axes"]["x"] == "Period end"
    assert len(payload["points"]) == 2
    assert payload["points"][0]["series"] == "USD"


def test_edgar_concept_adapter_multiple_units():
    raw = {
        "label": "Shares Outstanding",
        "units": {
            "shares": [{"end": "2023-09-30", "val": 1.5e10}],
            "pure": [{"end": "2023-09-30", "val": 1.0}],
        },
    }
    payload = _edgar_company_concept_to_shape_payload(raw)
    # Multiple units → label without parenthetical
    assert payload["axes"]["y"] == "Shares Outstanding"


def test_edgar_concept_adapter_empty():
    payload = _edgar_company_concept_to_shape_payload({})
    assert payload["points"] == []


def test_edgar_concept_adapter_skips_non_numeric():
    raw = {
        "units": {
            "USD": [
                {"end": "2023-09-30", "val": None},
                {"end": "2024-09-30", "val": "bad"},
                {"end": "2025-09-30", "val": 1.0},
            ],
        },
    }
    payload = _edgar_company_concept_to_shape_payload(raw)
    assert len(payload["points"]) == 1


def test_edgar_get_company_concept_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "edgar-get-company-concept")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_edgar_get_company_concept_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "label": "Revenues",
            "units": {"USD": [{"end": "2023-09-30", "val": 100.0}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_edgar_get_company_concept(
            {"cik": "320193", "concept": "Revenues"},
        )
        body = json.loads(result[0].text)
        assert body["points"][0] == {
            "date": "2023-09-30",
            "value": 100.0,
            "series": "USD",
        }


@pytest.mark.anyio
async def test_edgar_get_company_facts_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "cik": 320193,
            "facts": {"us-gaap": {"Revenues": {}}},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_edgar_get_company_facts({"cik": "320193"})
        assert "facts" in result[0].text


@pytest.mark.anyio
async def test_edgar_get_frames_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "tag": "Revenues",
            "data": [{"cik": 1, "val": 100}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_edgar_get_frames(
            {"concept": "Revenues", "year": 2023, "quarter": 1},
        )
        assert "Revenues" in result[0].text


@pytest.mark.anyio
async def test_edgar_list_tickers_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_edgar_list_tickers()
        assert "AAPL" in result[0].text


@pytest.mark.anyio
async def test_edgar_search_by_ticker_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_edgar_search_by_ticker({"ticker": "AAPL"})
        assert "Apple" in result[0].text
        assert "0000320193" in result[0].text


@pytest.mark.anyio
async def test_edgar_search_by_ticker_not_found():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_edgar_search_by_ticker({"ticker": "ZZZZ"})
        assert "No company found" in result[0].text


@pytest.mark.anyio
async def test_edgar_search_by_name_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_edgar_search_by_name({"name": "apple"})
        assert "Apple" in result[0].text


@pytest.mark.anyio
async def test_edgar_get_company_submissions_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_edgar_get_company_submissions({"cik": "320193"})
