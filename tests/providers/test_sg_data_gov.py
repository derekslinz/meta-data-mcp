import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.sg_data_gov import (
    TOOLS,
    _sg_datasets_to_shape_payload,
    handle_sg_datagov_get_collection,
    handle_sg_datagov_get_dataset,
    handle_sg_datagov_list_collections,
    handle_sg_datagov_list_datasets,
    handle_sg_datagov_poll_download,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_sg_datagov_list_datasets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "code": 0,
            "data": {
                "datasets": [{"datasetId": "d_abc", "name": "Resale Flat Prices"}],
                "pages": {"total": 1},
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_sg_datagov_list_datasets({"page": 1, "per_page": 10})
        assert "Resale Flat Prices" in result[0].text


@pytest.mark.anyio
async def test_sg_datagov_list_datasets_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("SG outage")

        with pytest.raises(httpx.HTTPError):
            await handle_sg_datagov_list_datasets({})


@pytest.mark.anyio
async def test_sg_datagov_get_dataset_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "code": 0,
            "data": {
                "datasetId": "d_abc",
                "name": "Resale Flat Prices",
                "format": "CSV",
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_sg_datagov_get_dataset({"datasetId": "d_abc"})
        assert "Resale Flat Prices" in result[0].text


@pytest.mark.anyio
async def test_sg_datagov_list_collections_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "code": 0,
            "data": {
                "collections": [
                    {"collectionId": "c_001", "name": "Housing Collection"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_sg_datagov_list_collections({"page": 1})
        assert "Housing Collection" in result[0].text


@pytest.mark.anyio
async def test_sg_datagov_get_collection_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "code": 0,
            "data": {
                "collectionId": "c_001",
                "name": "Housing Collection",
                "description": "HDB housing datasets",
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_sg_datagov_get_collection({"collectionId": "c_001"})
        assert "Housing Collection" in result[0].text


@pytest.mark.anyio
async def test_sg_datagov_poll_download_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "code": 0,
            "data": {"status": "READY", "url": "https://signed.example/preview"},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_sg_datagov_poll_download({"datasetId": "d_abc"})
        assert "READY" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for sg-data-gov-list-datasets.
# ---------------------------------------------------------------------------


def test_sg_adapter_flattens_v2_envelope_to_rows():
    raw = {
        "code": 0,
        "data": {
            "datasets": [
                {
                    "datasetId": "d_abc",
                    "name": "Resale Flat Prices",
                    "format": "CSV",
                    "status": "PUBLISHED",
                    "managedByAgencyName": "HDB",
                },
            ],
            "pages": {"total": 1},
        },
    }
    payload = _sg_datasets_to_shape_payload(raw)
    assert payload["rows"][0]["name"] == "Resale Flat Prices"
    assert payload["rows"][0]["managedByAgencyName"] == "HDB"
    assert payload["pages"] == {"total": 1}
    assert payload["default_facets"] == ["managedByAgencyName", "format", "status"]


def test_sg_adapter_handles_missing_envelope():
    payload = _sg_datasets_to_shape_payload({})
    assert payload["rows"] == []


def test_list_datasets_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "sg-data-gov-list-datasets")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_sg_datagov_list_datasets_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "code": 0,
            "data": {"datasets": [{"datasetId": "x", "name": "X"}], "pages": {}},
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_sg_datagov_list_datasets({"page": 1})
        body = json.loads(result[0].text)
        assert body["rows"][0]["name"] == "X"
        assert "schema" in body
