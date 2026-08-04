import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_world_bank import (
    TOOLS,
    _world_bank_indicator_data_to_shape_payload,
    handle_get_country,
    handle_get_indicator_data,
    handle_list_countries,
    handle_list_income_levels,
    handle_list_indicators,
    handle_list_sources,
    handle_list_topics,
    handle_search_indicators,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_world_bank_list_countries_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"page": 1, "pages": 1, "per_page": 300, "total": 2},
            [
                {"id": "USA", "name": "United States"},
                {"id": "BRA", "name": "Brazil"},
            ],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_countries()
        assert len(result) == 1
        assert "United States" in result[0].text
        assert "Brazil" in result[0].text


@pytest.mark.anyio
async def test_world_bank_list_countries_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_list_countries()


@pytest.mark.anyio
async def test_world_bank_get_country_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"page": 1, "pages": 1, "per_page": 50, "total": 1},
            [{"id": "BRA", "iso2Code": "BR", "name": "Brazil"}],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_country({"country": "BRA"})
        assert "Brazil" in result[0].text


@pytest.mark.anyio
async def test_world_bank_get_country_missing_arg():
    with pytest.raises(ValueError):
        await handle_get_country({})


@pytest.mark.anyio
async def test_world_bank_list_indicators_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"page": 1, "pages": 5, "per_page": 200, "total": 1000},
            [{"id": "NY.GDP.MKTP.CD", "name": "GDP (current US$)"}],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_indicators()
        assert "NY.GDP.MKTP.CD" in result[0].text


@pytest.mark.anyio
async def test_world_bank_search_indicators_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"page": 1, "pages": 1, "per_page": 200, "total": 1},
            [{"id": "SP.POP.TOTL", "name": "Population, total"}],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_indicators({"topic": 8})
        assert "Population, total" in result[0].text


@pytest.mark.anyio
async def test_world_bank_get_indicator_data_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"page": 1, "pages": 1, "per_page": 100, "total": 2},
            [
                {
                    "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                    "country": {"id": "US", "value": "United States"},
                    "value": 23315080560000.0,
                    "date": "2021",
                },
            ],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_indicator_data(
            {
                "country": "USA",
                "indicator": "NY.GDP.MKTP.CD",
                "start": 2020,
                "end": 2021,
            },
        )
        assert "United States" in result[0].text


@pytest.mark.anyio
async def test_world_bank_get_indicator_data_missing_args():
    with pytest.raises(ValueError):
        await handle_get_indicator_data({"country": "USA"})


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for world-bank-get-indicator-data.
# ---------------------------------------------------------------------------


def test_world_bank_adapter_flattens_obs_to_points():
    raw = [
        {"page": 1, "pages": 1},
        [
            {
                "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                "country": {"id": "US", "value": "United States"},
                "value": 100.0,
                "date": "2020",
            },
            {
                "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                "country": {"id": "GB", "value": "United Kingdom"},
                "value": 50.0,
                "date": "2020",
            },
        ],
    ]
    payload = _world_bank_indicator_data_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Year", "y": "GDP (current US$)"}
    assert len(payload["points"]) == 2
    series = {p["series"] for p in payload["points"]}
    assert series == {"United States", "United Kingdom"}


def test_world_bank_adapter_handles_empty():
    payload = _world_bank_indicator_data_to_shape_payload([{}, []])
    assert payload["points"] == []
    assert payload["axes"]["x"] == "Year"


def test_world_bank_adapter_handles_non_list():
    payload = _world_bank_indicator_data_to_shape_payload({})
    assert payload["points"] == []


def test_world_bank_adapter_skips_null_values():
    raw = [
        {},
        [
            {
                "indicator": {"value": "X"},
                "country": {"value": "USA"},
                "value": None,
                "date": "2020",
            },
            {
                "indicator": {"value": "X"},
                "country": {"value": "USA"},
                "value": 1.5,
                "date": "2021",
            },
        ],
    ]
    payload = _world_bank_indicator_data_to_shape_payload(raw)
    assert len(payload["points"]) == 1
    assert payload["points"][0]["value"] == 1.5


def test_world_bank_indicator_data_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "world-bank-get-indicator-data")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_world_bank_get_indicator_data_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {},
            [
                {
                    "indicator": {"value": "GDP"},
                    "country": {"value": "USA"},
                    "value": 100.0,
                    "date": "2020",
                },
            ],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_indicator_data(
            {"country": "USA", "indicator": "NY.GDP.MKTP.CD"},
        )
        body = json.loads(result[0].text)
        assert body["axes"]["y"] == "GDP"
        assert body["points"][0] == {
            "date": "2020",
            "value": 100.0,
            "series": "USA",
        }


@pytest.mark.anyio
async def test_world_bank_list_topics_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"page": 1, "pages": 1, "per_page": 50, "total": 1},
            [{"id": "1", "value": "Agriculture & Rural Development"}],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_topics()
        assert "Agriculture" in result[0].text


@pytest.mark.anyio
async def test_world_bank_list_sources_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"page": 1, "pages": 1, "per_page": 50, "total": 1},
            [{"id": "2", "name": "World Development Indicators"}],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_sources()
        assert "World Development Indicators" in result[0].text


@pytest.mark.anyio
async def test_world_bank_list_income_levels_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"page": 1, "pages": 1, "per_page": 50, "total": 1},
            [{"id": "HIC", "value": "High income"}],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_income_levels()
        assert "High income" in result[0].text
