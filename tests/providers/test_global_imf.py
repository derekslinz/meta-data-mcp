import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_imf import (
    TOOLS,
    _imf_get_data_to_shape_payload,
    handle_get_data,
    handle_get_dataflow,
    handle_get_datastructure,
    handle_list_codelist,
    handle_list_dataflows,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _imf_sdmx_response() -> dict:
    return {
        "data": {
            "structure": {
                "dimensions": {
                    "observation": [
                        {
                            "id": "TIME_PERIOD",
                            "values": [{"id": "2020"}, {"id": "2021"}],
                        },
                    ],
                    "series": [
                        {"id": "FREQ", "values": [{"id": "A"}]},
                        {"id": "REF_AREA", "values": [{"id": "USA"}]},
                    ],
                },
                "attributes": {
                    "series": [{"id": "UNIT_MEASURE", "values": [{"name": "Percent"}]}],
                },
            },
            "dataSets": [
                {"series": {"0:0": {"observations": {"0": [2.5], "1": [3.0]}}}},
            ],
        },
    }


@pytest.mark.anyio
async def test_imf_list_dataflows_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {
                "dataflows": [
                    {"id": "IFS", "name": "International Financial Statistics"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_dataflows()
        assert "IFS" in result[0].text


@pytest.mark.anyio
async def test_imf_list_dataflows_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_list_dataflows()


@pytest.mark.anyio
async def test_imf_list_dataflows_custom_agency():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {"dataflows": [{"id": "BOP"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_dataflows({"agencyId": "IMF.STA"})
        assert "BOP" in result[0].text


@pytest.mark.anyio
async def test_imf_get_dataflow_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {"dataflows": [{"id": "IFS", "version": "1.0"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_dataflow({"flowId": "IFS"})
        assert "IFS" in result[0].text


@pytest.mark.anyio
async def test_imf_get_dataflow_missing_args():
    with pytest.raises(ValueError):
        await handle_get_dataflow({})


@pytest.mark.anyio
async def test_imf_get_data_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = _imf_sdmx_response()
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_data(
            {
                "flowRef": "IMF.STA,IFS,1.0",
                "key": "USA",
                "startPeriod": "2020",
                "endPeriod": "2021",
            },
        )
        assert "points" in result[0].text


@pytest.mark.anyio
async def test_imf_get_data_missing_args():
    with pytest.raises(ValueError):
        await handle_get_data({})


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for imf-get-data.
# ---------------------------------------------------------------------------


def test_imf_adapter_flattens_sdmx_to_points():
    payload = _imf_get_data_to_shape_payload(_imf_sdmx_response())
    assert payload["axes"] == {"x": "TIME_PERIOD", "y": "Percent"}
    assert len(payload["points"]) == 2
    assert payload["points"][0] == {"date": "2020", "value": 2.5, "series": "USA"}


def test_imf_adapter_handles_empty():
    payload = _imf_get_data_to_shape_payload({"data": {}})
    assert payload["points"] == []


def test_imf_adapter_skips_non_numeric():
    raw = _imf_sdmx_response()
    raw["data"]["dataSets"][0]["series"]["0:0"]["observations"]["0"] = ["NA"]
    payload = _imf_get_data_to_shape_payload(raw)
    assert len(payload["points"]) == 1


def test_imf_get_data_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "imf-get-data")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_imf_get_data_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = _imf_sdmx_response()
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_data({"flowRef": "IMF.STA,IFS,1.0", "key": "USA"})
        body = json.loads(result[0].text)
        assert body["axes"]["y"] == "Percent"
        assert len(body["points"]) == 2


@pytest.mark.anyio
async def test_imf_get_datastructure_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": {"dataStructures": [{"id": "DSD_IFS"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_datastructure({"structureId": "DSD_IFS"})
        assert "DSD_IFS" in result[0].text


@pytest.mark.anyio
async def test_imf_list_codelist_success():
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

        result = await handle_list_codelist({"codelistId": "CL_AREA"})
        assert "CL_AREA" in result[0].text


@pytest.mark.anyio
async def test_imf_list_codelist_missing_args():
    with pytest.raises(ValueError):
        await handle_list_codelist({})
