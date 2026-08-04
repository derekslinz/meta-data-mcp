import json
from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.nl_cbs import (
    TOOLS,
    CBSDataParams,
    CBSListTablesParams,
    CBSMetadataParams,
    _cbs_typed_dataset_to_shape_payload,
    fetch_cbs_data,
    fetch_cbs_metadata,
    handle_cbs_data,
    handle_cbs_list_tables,
    handle_cbs_metadata,
    list_cbs_tables,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_cbs_data_response():
    return {
        "value": [
            {
                "ID": 0,
                "Periods": "2023MM01",
                "Euro95_1": 1.500,
                "Diesel_2": 1.200,
                "LPG_3": 0.600,
            },
        ],
    }


@pytest.fixture
def mock_cbs_metadata_response():
    return {
        "value": [
            {
                "Key": "Euro95_1",
                "Title": "Euro 95",
                "Description": "Unleaded motor petrol",
            },
        ],
    }


@pytest.fixture
def mock_cbs_catalog_response():
    return {
        "value": [
            {
                "Identifier": "80416ENG",
                "ShortTitle": "Fuel prices",
                "Title": "Motor fuel prices; by day",
            },
        ],
    }


def test_fetch_cbs_data(mock_cbs_data_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_cbs_data_response
        mock_get.return_value.raise_for_status = Mock()

        params = CBSDataParams(table_id="80416ENG")
        response = fetch_cbs_data(params)

        assert "value" in response
        assert response["value"][0]["Euro95_1"] == 1.500


@pytest.mark.anyio
async def test_handle_cbs_data(mock_cbs_data_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_cbs_data_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_cbs_data({"table_id": "80416ENG"})

        assert len(result) == 1
        assert "2023MM01" in result[0].text
        payload = json.loads(result[0].text)
        assert "points" in payload
        # 3 numeric columns × 1 row = 3 points
        assert len(payload["points"]) == 3


def test_fetch_cbs_metadata(mock_cbs_metadata_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_cbs_metadata_response
        mock_get.return_value.raise_for_status = Mock()

        params = CBSMetadataParams(table_id="80416ENG")
        response = fetch_cbs_metadata(params)

        assert response["value"][0]["Title"] == "Euro 95"


@pytest.mark.anyio
async def test_handle_cbs_metadata(mock_cbs_metadata_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_cbs_metadata_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_cbs_metadata({"table_id": "80416ENG"})

        assert "Euro 95" in result[0].text


def test_list_cbs_tables(mock_cbs_catalog_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_cbs_catalog_response
        mock_get.return_value.raise_for_status = Mock()

        params = CBSListTablesParams(search="Fuel")
        response = list_cbs_tables(params)

        assert response["value"][0]["Identifier"] == "80416ENG"


@pytest.mark.anyio
async def test_handle_cbs_list_tables(mock_cbs_catalog_response):
    # Add odata.count to mock response
    mock_response = mock_cbs_catalog_response.copy()
    mock_response["odata.count"] = 100

    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_response
        mock_get.return_value.raise_for_status = Mock()

        # Test with search
        result = await handle_cbs_list_tables({"search": "Fuel"})
        assert "80416ENG" in result[0].text
        payload = json.loads(result[0].text)
        assert payload["total_count"] == 100
        args, kwargs = mock_get.call_args
        assert kwargs["params"]["$top"] == 10
        assert kwargs["params"]["$skip"] == 0
        assert kwargs["params"]["$inlinecount"] == "allpages"

        # Test with pagination
        result = await handle_cbs_list_tables({"top": 5, "skip": 10})
        _args, kwargs = mock_get.call_args
        assert kwargs["params"]["$top"] == 5
        assert kwargs["params"]["$skip"] == 10


@pytest.mark.anyio
async def test_handle_cbs_data_pagination(mock_cbs_data_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_cbs_data_response
        mock_get.return_value.raise_for_status = Mock()

        # Test with top and skip
        result = await handle_cbs_data({"table_id": "80416ENG", "top": 5, "skip": 10})
        assert "2023MM01" in result[0].text
        _args, kwargs = mock_get.call_args
        assert kwargs["params"]["$top"] == 5
        assert kwargs["params"]["$skip"] == 10


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for cbs-get-typed-dataset.
# ---------------------------------------------------------------------------


def test_cbs_adapter_flattens_multi_column_table():
    raw = {
        "value": [
            {
                "ID": 0,
                "Periods": "2023MM01",
                "Euro95_1": 1.5,
                "Diesel_2": 1.2,
            },
            {
                "ID": 1,
                "Periods": "2023MM02",
                "Euro95_1": 1.6,
                "Diesel_2": 1.3,
            },
        ],
    }
    payload = _cbs_typed_dataset_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Period", "y": "Value"}
    assert len(payload["points"]) == 4
    series = {p["series"] for p in payload["points"]}
    assert series == {"Euro95_1", "Diesel_2"}


def test_cbs_adapter_handles_perioden_field():
    raw = {"value": [{"Perioden": "2023MM01", "Waarde": 1.5}]}
    payload = _cbs_typed_dataset_to_shape_payload(raw)
    assert payload["points"][0]["date"] == "2023MM01"
    assert payload["points"][0]["series"] == "Waarde"


def test_cbs_adapter_empty():
    payload = _cbs_typed_dataset_to_shape_payload({})
    assert payload["points"] == []


def test_cbs_adapter_skips_non_numeric():
    raw = {
        "value": [
            {
                "Periods": "2023MM01",
                "Euro95_1": "bad",
                "Diesel_2": 1.2,
                "Brand_3": True,
            },
        ],
    }
    payload = _cbs_typed_dataset_to_shape_payload(raw)
    assert len(payload["points"]) == 1
    assert payload["points"][0]["series"] == "Diesel_2"


def test_cbs_typed_dataset_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "cbs-get-typed-dataset")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_cbs_typed_dataset_returns_shape_payload(mock_cbs_data_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_cbs_data_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_cbs_data({"table_id": "80416ENG"})
        body = json.loads(result[0].text)
        assert body["axes"]["x"] == "Period"
        assert len(body["points"]) == 3
