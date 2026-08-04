import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_who_gho import (
    TOOLS,
    _who_gho_indicator_data_to_shape_payload,
    handle_get_indicator_data,
    handle_list_countries,
    handle_list_dimension_values,
    handle_list_dimensions,
    handle_list_indicators,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_who_gho_list_indicators_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "value": [
                {
                    "IndicatorCode": "WHOSIS_000001",
                    "IndicatorName": "Life expectancy at birth (years)",
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_indicators({"top": 1})
        assert "WHOSIS_000001" in result[0].text


@pytest.mark.anyio
async def test_who_gho_list_indicators_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_list_indicators()


@pytest.mark.anyio
async def test_who_gho_get_indicator_data_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "value": [{"SpatialDim": "USA", "TimeDim": 2020, "NumericValue": 78.5}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_indicator_data(
            {"indicator_code": "WHOSIS_000001", "filter": "SpatialDim eq 'USA'"},
        )
        assert "USA" in result[0].text
        assert "78.5" in result[0].text
        assert "points" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for who-gho-get-indicator-data.
# ---------------------------------------------------------------------------


def test_who_gho_adapter_flattens_value_array():
    raw = {
        "value": [
            {"SpatialDim": "USA", "TimeDim": 2020, "NumericValue": 78.5},
            {"SpatialDim": "USA", "TimeDim": 2021, "NumericValue": 79.0},
            {"SpatialDim": "GBR", "TimeDim": 2020, "NumericValue": 81.0},
        ],
    }
    payload = _who_gho_indicator_data_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Year", "y": "Value"}
    assert len(payload["points"]) == 3
    series = {p["series"] for p in payload["points"]}
    assert series == {"USA", "GBR"}


def test_who_gho_adapter_empty():
    payload = _who_gho_indicator_data_to_shape_payload({})
    assert payload["points"] == []


def test_who_gho_adapter_skips_null_numeric():
    raw = {
        "value": [
            {"SpatialDim": "USA", "TimeDim": 2020, "NumericValue": None},
            {"SpatialDim": "USA", "TimeDim": 2021, "NumericValue": 79.0},
        ],
    }
    payload = _who_gho_indicator_data_to_shape_payload(raw)
    assert len(payload["points"]) == 1


def test_who_gho_indicator_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "who-gho-get-indicator-data")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_who_gho_indicator_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "value": [{"SpatialDim": "USA", "TimeDim": 2020, "NumericValue": 78.5}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_indicator_data({"indicator_code": "WHOSIS_000001"})
        body = json.loads(result[0].text)
        assert body["points"][0] == {
            "date": "2020",
            "value": 78.5,
            "series": "USA",
        }


@pytest.mark.anyio
async def test_who_gho_get_indicator_data_requires_code():
    with pytest.raises(ValueError):
        await handle_get_indicator_data({})


@pytest.mark.anyio
async def test_who_gho_list_dimensions_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "value": [
                {"Code": "COUNTRY", "Title": "Country"},
                {"Code": "SEX", "Title": "Sex"},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_dimensions()
        assert "COUNTRY" in result[0].text
        assert "SEX" in result[0].text


@pytest.mark.anyio
async def test_who_gho_list_dimension_values_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "value": [{"Code": "USA", "Title": "United States of America"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_dimension_values({"dim": "COUNTRY"})
        assert "United States of America" in result[0].text


@pytest.mark.anyio
async def test_who_gho_list_dimension_values_requires_dim():
    with pytest.raises(ValueError):
        await handle_list_dimension_values({})


@pytest.mark.anyio
async def test_who_gho_list_countries_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "value": [{"Code": "FRA", "Title": "France"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_countries()
        assert "France" in result[0].text
