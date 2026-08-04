import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_cdc_socrata import (
    TOOLS,
    _socrata_views_to_shape_payload,
    handle_count_dataset_rows,
    handle_get_dataset_metadata,
    handle_get_metadata_v1,
    handle_query_dataset,
    handle_search_datasets,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_cdc_search_datasets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"id": "9bhg-hcku", "name": "COVID-19 Cases"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_datasets({"q": "covid", "limit": 5})
        assert "COVID-19" in result[0].text


@pytest.mark.anyio
async def test_cdc_get_dataset_metadata_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "id": "9bhg-hcku",
            "name": "COVID-19 Cases",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_dataset_metadata({"dataset_id": "9bhg-hcku"})
        assert "9bhg-hcku" in result[0].text


@pytest.mark.anyio
async def test_cdc_get_dataset_metadata_missing_id():
    with pytest.raises(ValueError, match="dataset_id is required"):
        await handle_get_dataset_metadata({})


@pytest.mark.anyio
async def test_cdc_query_dataset_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [{"state": "CA", "cases": 100}]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_query_dataset(
            {
                "dataset_id": "9bhg-hcku",
                "limit": 1,
                "offset": 0,
                "where": "state='CA'",
            },
        )
        assert "CA" in result[0].text


@pytest.mark.anyio
async def test_cdc_count_dataset_rows_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [{"count": "12345"}]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_count_dataset_rows({"dataset_id": "9bhg-hcku"})
        assert "12345" in result[0].text


@pytest.mark.anyio
async def test_cdc_count_dataset_rows_missing_id():
    with pytest.raises(ValueError, match="dataset_id is required"):
        await handle_count_dataset_rows({})


@pytest.mark.anyio
async def test_cdc_get_metadata_v1_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [{"id": "9bhg-hcku", "name": "X"}]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_metadata_v1({"limit": 5})
        assert "9bhg-hcku" in result[0].text


@pytest.mark.anyio
async def test_cdc_http_error_propagates():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_search_datasets({"q": "covid"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for cdc-search-datasets.
# ---------------------------------------------------------------------------


def test_socrata_adapter_flattens_views_to_rows():
    raw = [
        {
            "id": "9bhg-hcku",
            "name": "COVID-19 Cases",
            "category": "Health",
            "attribution": "CDC",
        },
    ]
    payload = _socrata_views_to_shape_payload(raw)
    assert payload["rows"][0]["name"] == "COVID-19 Cases"
    assert payload["rows"][0]["attribution"] == "CDC"


def test_socrata_adapter_handles_non_list_input():
    assert _socrata_views_to_shape_payload({})["rows"] == []


def test_search_datasets_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "cdc-search-datasets")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_cdc_search_datasets_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"id": "9bhg-hcku", "name": "COVID-19 Cases"},
        ]
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_search_datasets({"q": "covid"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["name"] == "COVID-19 Cases"
        assert "schema" in body
