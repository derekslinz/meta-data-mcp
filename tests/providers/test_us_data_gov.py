import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.errors import NotFoundError
from meta_data_mcp.providers.us_data_gov import (
    TOOLS,
    DataGovGetDatasetParams,
    DataGovListDatasetsParams,
    _datagov_search_to_shape_payload,
    fetch_datagov_dataset,
    handle_datagov_get_dataset,
    handle_datagov_list_datasets,
    list_datagov_datasets,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_search_response():
    return {
        "after": "next-cursor",
        "results": [
            {
                "identifier": "CCDB",
                "slug": "consumer-complaint-database",
                "title": "Consumer Complaint Database",
                "publisher": "Consumer Financial Protection Bureau",
                "organization": {"name": "Consumer Financial Protection Bureau"},
                "description": "A collection of complaints received by the CFPB.",
                "harvest_record": "https://catalog.data.gov/harvest_record/abc",
            },
        ],
    }


@pytest.fixture
def mock_show_response():
    return {
        "results": [
            {
                "identifier": "CCDB",
                "slug": "consumer-complaint-database",
                "title": "Consumer Complaint Database",
                "dcat": {
                    "identifier": "CCDB",
                    "title": "Consumer Complaint Database",
                    "distribution": [
                        {
                            "title": "CSV Data",
                            "format": "CSV",
                            "accessURL": "https://example.com/data.csv",
                        },
                    ],
                },
            },
        ],
    }


def test_list_datagov_datasets(mock_search_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_search_response
        mock_get.return_value.raise_for_status = Mock()

        params = DataGovListDatasetsParams(search="complaints")
        result = list_datagov_datasets(params)
        assert result["after"] == "next-cursor"
        assert result["results"][0]["slug"] == "consumer-complaint-database"
        mock_get.assert_called_once()
        assert mock_get.call_args[0][0] == "https://catalog.data.gov/search"
        assert mock_get.call_args[1]["params"] == {"q": "complaints", "per_page": 20}
        assert mock_get.call_args[1]["timeout"] == 10.0


@pytest.mark.anyio
async def test_handle_datagov_list_datasets(mock_search_response):
    """Handler returns the records shape primitive's payload format
    (v2.0 Phase 4): rows + schema replace the legacy {datasets: [...]}
    envelope.
    """
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_search_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_datagov_list_datasets({"search": "complaints"})
        assert len(result) == 1
        assert "Consumer Complaint Database" in result[0].text
        payload = json.loads(result[0].text)
        assert payload["count"] == 1
        assert payload["rows"][0]["title"] == "Consumer Complaint Database"
        assert "schema" in payload
        assert payload["after"] == "next-cursor"


def test_fetch_datagov_dataset(mock_show_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_show_response
        mock_get.return_value.raise_for_status = Mock()

        params = DataGovGetDatasetParams(dataset_id="consumer-complaint-database")
        result = fetch_datagov_dataset(params)
        assert result["title"] == "Consumer Complaint Database"
        assert len(result["dcat"]["distribution"]) == 1
        mock_get.assert_called_once()
        assert mock_get.call_args[0][0] == "https://catalog.data.gov/search"
        assert mock_get.call_args[1]["params"] == {
            "q": "consumer-complaint-database",
            "per_page": 25,
        }
        assert mock_get.call_args[1]["timeout"] == 10.0


@pytest.mark.anyio
async def test_handle_datagov_get_dataset(mock_show_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_show_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_datagov_get_dataset(
            {"dataset_id": "consumer-complaint-database"},
        )
        assert len(result) == 1
        assert "CSV Data" in result[0].text
        payload = json.loads(result[0].text)
        assert payload["dcat"]["distribution"][0]["title"] == "CSV Data"


def test_api_error_handling():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"results": []}
        mock_get.return_value.raise_for_status = Mock()

        params = DataGovGetDatasetParams(dataset_id="invalid")
        with pytest.raises(ValueError, match="API Error: dataset not found: invalid"):
            fetch_datagov_dataset(params)


@pytest.mark.anyio
async def test_handle_get_dataset_translates_http_404_to_not_found():
    """An upstream 404 from inside the handler must surface as NotFoundError."""
    req = httpx.Request("GET", "https://catalog.data.gov/search")
    resp = httpx.Response(status_code=404, request=req)
    status_err = httpx.HTTPStatusError("not found", request=req, response=resp)

    with patch("httpx.get") as mock_get:
        mock_get.return_value.raise_for_status = Mock(side_effect=status_err)

        with pytest.raises(NotFoundError) as exc_info:
            await handle_datagov_get_dataset({"dataset_id": "missing"})

        err = exc_info.value
        assert err.provider == "us-data-gov"
        assert err.status == 404
        assert err.kind == "not_found"
        # str() form must not leak the upstream URL.
        assert "catalog.data.gov" not in str(err)


@pytest.mark.anyio
async def test_handle_get_dataset_does_not_translate_non_http_errors():
    with (
        patch(
            "meta_data_mcp.providers.us_data_gov.fetch_datagov_dataset",
            side_effect=ValueError("boom"),
        ),
        pytest.raises(ValueError, match="boom"),
    ):
        await handle_datagov_get_dataset({"dataset_id": "missing"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for us-datagov-list-datasets.
# ---------------------------------------------------------------------------


def test_datagov_adapter_flattens_search_results_to_rows(mock_search_response):
    payload = _datagov_search_to_shape_payload(mock_search_response)
    assert payload["count"] == 1
    assert payload["after"] == "next-cursor"
    row = payload["rows"][0]
    assert row["title"] == "Consumer Complaint Database"
    assert row["organization"] == "Consumer Financial Protection Bureau"
    assert payload["default_facets"] == ["organization", "publisher"]


def test_datagov_adapter_handles_empty_results():
    payload = _datagov_search_to_shape_payload({"results": []})
    assert payload["rows"] == []
    assert payload["count"] == 0


def test_list_datasets_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "us-datagov-list-datasets")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI
