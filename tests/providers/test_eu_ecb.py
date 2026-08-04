import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.eu_ecb import (
    TOOLS,
    _ecb_get_data_to_shape_payload,
    handle_ecb_get_codelist,
    handle_ecb_get_conceptscheme,
    handle_ecb_get_data,
    handle_ecb_get_dataflow,
    handle_ecb_list_dataflows,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _ecb_sdmx_response() -> dict:
    """Build a minimal SDMX-JSON response with two series and two periods."""
    return {
        "data": {
            "structure": {
                "dimensions": {
                    "observation": [
                        {
                            "id": "TIME_PERIOD",
                            "name": "Time period",
                            "values": [
                                {"id": "2024-01-01"},
                                {"id": "2024-01-02"},
                            ],
                        },
                    ],
                    "series": [
                        {"id": "FREQ", "values": [{"id": "D"}]},
                        {"id": "CURRENCY", "values": [{"id": "USD"}, {"id": "GBP"}]},
                    ],
                },
                "attributes": {
                    "series": [
                        {"id": "UNIT", "values": [{"name": "Euro per unit"}]},
                    ],
                },
            },
            "dataSets": [
                {
                    "series": {
                        "0:0": {
                            "observations": {
                                "0": [1.085],
                                "1": [1.086],
                            },
                        },
                        "0:1": {
                            "observations": {
                                "0": [0.85],
                                "1": [0.86],
                            },
                        },
                    },
                },
            ],
        },
    }


@pytest.mark.anyio
async def test_ecb_list_dataflows_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {
                "dataflows": [
                    {"id": "EXR", "name": "Exchange Rates"},
                    {"id": "BSI", "name": "Balance Sheet Items"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ecb_list_dataflows()
        assert "Exchange Rates" in result[0].text


@pytest.mark.anyio
async def test_ecb_list_dataflows_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("ECB unreachable")

        with pytest.raises(httpx.HTTPError):
            await handle_ecb_list_dataflows()


@pytest.mark.anyio
async def test_ecb_get_dataflow_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {"dataflows": [{"id": "EXR", "name": "Exchange Rates"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ecb_get_dataflow({"id": "EXR"})
        assert "Exchange Rates" in result[0].text


@pytest.mark.anyio
async def test_ecb_get_dataflow_missing_id():
    with pytest.raises(Exception):
        await handle_ecb_get_dataflow({})


@pytest.mark.anyio
async def test_ecb_get_data_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = _ecb_sdmx_response()
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ecb_get_data(
            {
                "flow": "EXR",
                "key": "D.USD.EUR.SP00.A",
                "lastNObservations": 1,
            },
        )
        assert "1.085" in result[0].text


@pytest.mark.anyio
async def test_ecb_get_data_missing_args():
    with pytest.raises(Exception):
        await handle_ecb_get_data({"flow": "EXR"})


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for ecb-get-data.
# ---------------------------------------------------------------------------


def test_ecb_adapter_flattens_sdmx_to_points():
    payload = _ecb_get_data_to_shape_payload(_ecb_sdmx_response())
    assert payload["axes"] == {"x": "Time period", "y": "Euro per unit"}
    # 2 series × 2 periods = 4 points, sorted by date then series.
    assert len(payload["points"]) == 4
    # FREQ is skipped, only the CURRENCY dimension carries into the label.
    series_labels = {p["series"] for p in payload["points"]}
    assert series_labels == {"USD", "GBP"}
    assert payload["points"][0]["date"] == "2024-01-01"


def test_ecb_adapter_handles_empty_response():
    payload = _ecb_get_data_to_shape_payload({"data": {}})
    assert payload["points"] == []
    assert payload["axes"]["x"] == "Period"


def test_ecb_adapter_skips_non_numeric_values():
    raw = _ecb_sdmx_response()
    raw["data"]["dataSets"][0]["series"]["0:0"]["observations"]["0"] = ["NA"]
    payload = _ecb_get_data_to_shape_payload(raw)
    # One USD obs dropped (non-numeric), GBP still has both, USD has one.
    values_by_series: dict[str, list[float]] = {}
    for p in payload["points"]:
        values_by_series.setdefault(p["series"], []).append(p["value"])
    assert len(values_by_series["USD"]) == 1
    assert len(values_by_series["GBP"]) == 2


def test_ecb_adapter_falls_back_to_raw_key_when_dims_missing():
    raw = {
        "data": {
            "structure": {
                "dimensions": {
                    "observation": [
                        {"id": "TIME_PERIOD", "values": [{"id": "2024-01-01"}]},
                    ],
                },
            },
            "dataSets": [{"series": {"0:0": {"observations": {"0": [1.0]}}}}],
        },
    }
    payload = _ecb_get_data_to_shape_payload(raw)
    assert payload["points"][0]["series"] == "0:0"


def test_ecb_get_data_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "ecb-get-data")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_ecb_get_data_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = _ecb_sdmx_response()
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ecb_get_data({"flow": "EXR", "key": "D.USD.EUR.SP00.A"})
        body = json.loads(result[0].text)
        assert "points" in body and "axes" in body
        assert body["axes"]["y"] == "Euro per unit"
        assert len(body["points"]) == 4


@pytest.mark.anyio
async def test_ecb_get_codelist_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {
                "codelists": [
                    {
                        "id": "CL_CURRENCY",
                        "codes": [{"id": "USD", "name": "US Dollar"}],
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ecb_get_codelist({"id": "CL_CURRENCY"})
        assert "US Dollar" in result[0].text


@pytest.mark.anyio
async def test_ecb_get_conceptscheme_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {
                "conceptSchemes": [
                    {"id": "ECB_CONCEPTS", "concepts": [{"id": "FREQ"}]}
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ecb_get_conceptscheme({"id": "ECB_CONCEPTS"})
        assert "ECB_CONCEPTS" in result[0].text
