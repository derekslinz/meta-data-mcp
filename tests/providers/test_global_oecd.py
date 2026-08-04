import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_oecd import (
    TOOLS,
    _oecd_get_data_to_shape_payload,
    handle_get_data,
    handle_get_dataflow,
    handle_get_datastructure,
    handle_list_codelist,
    handle_list_conceptscheme,
    handle_list_dataflows,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _oecd_sdmx_response() -> dict:
    return {
        "data": {
            "structure": {
                "dimensions": {
                    "observation": [
                        {
                            "id": "TIME_PERIOD",
                            "name": "Time period",
                            "values": [{"id": "2020"}, {"id": "2021"}],
                        },
                    ],
                    "series": [
                        {"id": "FREQ", "values": [{"id": "A"}]},
                        {"id": "REF_AREA", "values": [{"id": "USA"}, {"id": "GBR"}]},
                    ],
                },
                "attributes": {
                    "series": [{"id": "UNIT_MEASURE", "values": [{"name": "Index"}]}],
                },
            },
            "dataSets": [
                {
                    "series": {
                        "0:0": {"observations": {"0": [100.0], "1": [101.5]}},
                        "0:1": {"observations": {"0": [99.0], "1": [98.0]}},
                    },
                },
            ],
        },
    }


@pytest.mark.anyio
async def test_oecd_list_dataflows_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {
                "dataflows": [
                    {"id": "DF_QNA_EXPENDITURE", "name": "Quarterly National Accounts"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_dataflows()
        assert "DF_QNA_EXPENDITURE" in result[0].text


@pytest.mark.anyio
async def test_oecd_list_dataflows_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_list_dataflows()


@pytest.mark.anyio
async def test_oecd_get_dataflow_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {"dataflows": [{"id": "DF_QNA_EXPENDITURE", "version": "1.0"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_dataflow(
            {"agencyId": "OECD.SDD.NAD", "flowId": "DF_QNA_EXPENDITURE"},
        )
        assert "DF_QNA_EXPENDITURE" in result[0].text


@pytest.mark.anyio
async def test_oecd_get_dataflow_missing_args():
    with pytest.raises(ValueError):
        await handle_get_dataflow({"agencyId": "OECD.SDD.NAD"})


@pytest.mark.anyio
async def test_oecd_get_datastructure_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {
                "dataStructures": [
                    {"id": "DSD_NAMAIN1", "name": "National Accounts main"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_datastructure(
            {"agencyId": "OECD.SDD.NAD", "structureId": "DSD_NAMAIN1"},
        )
        assert "DSD_NAMAIN1" in result[0].text


@pytest.mark.anyio
async def test_oecd_get_data_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = _oecd_sdmx_response()
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_data(
            {
                "agencyId": "OECD.SDD.NAD",
                "flowId": "DF_QNA_EXPENDITURE",
                "version": "1.0",
                "key": "USA",
                "startPeriod": "2020",
                "endPeriod": "2021",
            },
        )
        assert "points" in result[0].text


@pytest.mark.anyio
async def test_oecd_get_data_missing_args():
    with pytest.raises(ValueError):
        await handle_get_data({"agencyId": "OECD.SDD.NAD"})


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for oecd-get-data.
# ---------------------------------------------------------------------------


def test_oecd_adapter_flattens_sdmx_to_points():
    payload = _oecd_get_data_to_shape_payload(_oecd_sdmx_response())
    assert payload["axes"] == {"x": "Time period", "y": "Index"}
    assert len(payload["points"]) == 4
    series_labels = {p["series"] for p in payload["points"]}
    assert series_labels == {"USA", "GBR"}


def test_oecd_adapter_handles_empty_response():
    payload = _oecd_get_data_to_shape_payload({"data": {}})
    assert payload["points"] == []


def test_oecd_adapter_skips_non_numeric():
    raw = _oecd_sdmx_response()
    raw["data"]["dataSets"][0]["series"]["0:0"]["observations"]["0"] = ["NA"]
    payload = _oecd_get_data_to_shape_payload(raw)
    usa_points = [p for p in payload["points"] if p["series"] == "USA"]
    assert len(usa_points) == 1


def test_oecd_get_data_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "oecd-get-data")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_oecd_get_data_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = _oecd_sdmx_response()
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_data({"agencyId": "OECD.SDD.NAD", "flowId": "DF"})
        body = json.loads(result[0].text)
        assert "points" in body and "axes" in body
        assert body["axes"]["y"] == "Index"


@pytest.mark.anyio
async def test_oecd_list_codelist_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {
                "codelists": [
                    {
                        "id": "CL_AREA",
                        "codes": [{"id": "USA", "name": "United States"}],
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_codelist(
            {"agencyId": "OECD.SDD.NAD", "codelistId": "CL_AREA"},
        )
        assert "CL_AREA" in result[0].text
        assert "United States" in result[0].text


@pytest.mark.anyio
async def test_oecd_list_codelist_missing_args():
    with pytest.raises(ValueError):
        await handle_list_codelist({"agencyId": "OECD.SDD.NAD"})


@pytest.mark.anyio
async def test_oecd_list_conceptscheme_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {"conceptSchemes": [{"id": "CS_GENERIC", "concepts": []}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_conceptscheme(
            {"agencyId": "OECD.SDD.NAD", "schemeId": "CS_GENERIC"},
        )
        assert "CS_GENERIC" in result[0].text
