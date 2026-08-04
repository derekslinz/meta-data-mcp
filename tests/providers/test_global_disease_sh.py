import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_disease_sh import (
    TOOLS,
    _disease_sh_historical_to_shape_payload,
    handle_countries,
    handle_country,
    handle_global,
    handle_historical_all,
    handle_historical_country,
    handle_vaccine_coverage,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_disease_sh_global_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "cases": 700000000,
            "deaths": 7000000,
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_global()
        assert "700000000" in result[0].text


@pytest.mark.anyio
async def test_disease_sh_global_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_global()


@pytest.mark.anyio
async def test_disease_sh_countries_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"country": "USA", "cases": 100000000},
            {"country": "India", "cases": 45000000},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_countries({"sort": "cases"})
        assert "USA" in result[0].text
        assert "India" in result[0].text


@pytest.mark.anyio
async def test_disease_sh_country_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "country": "France",
            "cases": 38000000,
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_country({"country": "France"})
        assert "France" in result[0].text


@pytest.mark.anyio
async def test_disease_sh_country_requires_country():
    with pytest.raises(ValueError):
        await handle_country({})


@pytest.mark.anyio
async def test_disease_sh_historical_all_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "cases": {"5/1/24": 700000000},
            "deaths": {"5/1/24": 7000000},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_historical_all({"lastdays": 7})
        assert "cases" in result[0].text
        assert "points" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for disease-sh-historical-all.
# ---------------------------------------------------------------------------


def test_disease_sh_adapter_flattens_metrics_to_series():
    raw = {
        "cases": {"5/1/24": 700000000, "5/2/24": 700001000},
        "deaths": {"5/1/24": 7000000},
    }
    payload = _disease_sh_historical_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Date", "y": "Count"}
    series = {p["series"] for p in payload["points"]}
    assert series == {"cases", "deaths"}
    # Date normalized to ISO.
    assert payload["points"][0]["date"] == "2024-05-01"


def test_disease_sh_adapter_empty():
    payload = _disease_sh_historical_to_shape_payload({})
    assert payload["points"] == []


def test_disease_sh_adapter_skips_bad_dates_and_values():
    raw = {
        "cases": {
            "5/1/24": 1.0,
            "bad-date": 2.0,
            "5/2/24": "string-value",
        },
    }
    payload = _disease_sh_historical_to_shape_payload(raw)
    assert len(payload["points"]) == 1
    assert payload["points"][0]["date"] == "2024-05-01"


def test_disease_sh_historical_all_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "disease-sh-historical-all")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_disease_sh_historical_all_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "cases": {"5/1/24": 100},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_historical_all({"lastdays": 1})
        body = json.loads(result[0].text)
        assert body["points"] == [
            {"date": "2024-05-01", "value": 100, "series": "cases"},
        ]


@pytest.mark.anyio
async def test_disease_sh_historical_country_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "country": "Germany",
            "timeline": {"cases": {"5/1/24": 38000000}},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_historical_country({"country": "Germany", "lastdays": 30})
        assert "Germany" in result[0].text


@pytest.mark.anyio
async def test_disease_sh_vaccine_coverage_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"5/1/24": 13000000000}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_vaccine_coverage({"lastdays": 30})
        assert "13000000000" in result[0].text
