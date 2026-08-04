import json
from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.global_dbnomics import (
    TOOLS,
    _dbnomics_series_to_shape_payload,
    handle_dbnomics_list_providers,
    handle_dbnomics_search,
    handle_dbnomics_series,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_dbnomics_search_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "datasets": {"docs": [{"code": "WEO"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_dbnomics_search({"query": "GDP"})
        assert len(result) == 1
        assert "WEO" in result[0].text


@pytest.mark.anyio
async def test_dbnomics_list_providers_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "providers": {"docs": [{"code": "IMF"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_dbnomics_list_providers()
        assert len(result) == 1
        assert "IMF" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for dbnomics-series.
# ---------------------------------------------------------------------------


def test_dbnomics_adapter_flattens_docs_to_points():
    raw = {
        "series": {
            "docs": [
                {
                    "series_code": "ABW.NGDP_RPCH",
                    "unit": "Percent",
                    "period": ["2020", "2021", "2022"],
                    "value": [-23.46, 18.41, 5.21],
                },
            ],
        },
    }
    payload = _dbnomics_series_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Period", "y": "Percent"}
    assert len(payload["points"]) == 3
    assert payload["points"][0] == {
        "date": "2020",
        "value": -23.46,
        "series": "ABW.NGDP_RPCH",
    }


def test_dbnomics_adapter_multi_series():
    raw = {
        "series": {
            "docs": [
                {
                    "series_code": "A",
                    "period": ["2020"],
                    "value": [1.0],
                },
                {
                    "series_code": "B",
                    "period": ["2020", "2021"],
                    "value": [2.0, 3.0],
                },
            ],
        },
    }
    payload = _dbnomics_series_to_shape_payload(raw)
    assert len(payload["points"]) == 3
    series = {p["series"] for p in payload["points"]}
    assert series == {"A", "B"}


def test_dbnomics_adapter_empty():
    assert _dbnomics_series_to_shape_payload({}) == {
        "points": [],
        "axes": {"x": "Period", "y": "Value"},
    }


def test_dbnomics_adapter_skips_non_numeric():
    raw = {
        "series": {
            "docs": [
                {
                    "series_code": "X",
                    "period": ["2020", "2021", "2022"],
                    "value": ["NA", 1.0, True],
                },
            ],
        },
    }
    payload = _dbnomics_series_to_shape_payload(raw)
    assert len(payload["points"]) == 1
    assert payload["points"][0]["value"] == 1.0


def test_dbnomics_series_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "dbnomics-series")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_dbnomics_series_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "series": {
                "docs": [
                    {
                        "series_code": "IMF/WEO:NGDP",
                        "unit": "USD",
                        "period": ["2020", "2021"],
                        "value": [100.0, 110.0],
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_dbnomics_series({"series_ids": "IMF/WEO:NGDP"})
        body = json.loads(result[0].text)
        assert "points" in body and "axes" in body
        assert body["axes"] == {"x": "Period", "y": "USD"}
        assert body["points"] == [
            {"date": "2020", "value": 100.0, "series": "IMF/WEO:NGDP"},
            {"date": "2021", "value": 110.0, "series": "IMF/WEO:NGDP"},
        ]
