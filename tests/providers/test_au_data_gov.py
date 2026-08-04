import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.au_data_gov import (
    TOOLS,
    _ckan_package_search_to_shape_payload,
    handle_au_datagov_get_dataset,
    handle_au_datagov_get_organization,
    handle_au_datagov_list_groups,
    handle_au_datagov_list_organizations,
    handle_au_datagov_list_tags,
    handle_au_datagov_search_datasets,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_au_datagov_search_datasets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {
                "count": 1,
                "results": [
                    {"name": "abs-population", "title": "ABS Population Estimates"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_au_datagov_search_datasets({"q": "population"})
        assert "ABS Population Estimates" in result[0].text
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert call_url == "https://data.gov.au/data/api/3/action/package_search"


@pytest.mark.anyio
async def test_au_datagov_search_datasets_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Down under outage")

        with pytest.raises(httpx.HTTPError):
            await handle_au_datagov_search_datasets({"q": "anything"})


@pytest.mark.anyio
async def test_au_datagov_get_dataset_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {
                "name": "abs-population",
                "title": "ABS Population Estimates",
                "resources": [{"format": "CSV"}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_au_datagov_get_dataset({"id": "abs-population"})
        assert "ABS Population Estimates" in result[0].text


@pytest.mark.anyio
async def test_au_datagov_list_organizations_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": [{"name": "abs", "title": "Australian Bureau of Statistics"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_au_datagov_list_organizations({"limit": 10})
        assert "Australian Bureau of Statistics" in result[0].text


@pytest.mark.anyio
async def test_au_datagov_get_organization_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {"name": "abs", "title": "Australian Bureau of Statistics"},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_au_datagov_get_organization({"id": "abs"})
        assert "Australian Bureau of Statistics" in result[0].text


@pytest.mark.anyio
async def test_au_datagov_list_groups_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": [{"name": "environment", "display_name": "Environment"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_au_datagov_list_groups({})
        assert "Environment" in result[0].text


@pytest.mark.anyio
async def test_au_datagov_list_tags_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": ["geospatial", "geology"],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_au_datagov_list_tags({"query": "geo"})
        assert "geospatial" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for au-data-gov-search-datasets.
# ---------------------------------------------------------------------------


def test_adapter_flattens_ckan_package_search_to_rows():
    raw = {
        "result": {
            "count": 1,
            "results": [
                {
                    "name": "bom-rainfall",
                    "title": "BoM Rainfall",
                    "organization": {"title": "Bureau of Meteorology"},
                    "license_title": "CC-BY 4.0",
                    "resources": [{"format": "csv"}, {"format": "json"}],
                },
            ],
        },
    }
    payload = _ckan_package_search_to_shape_payload(raw)
    assert payload["rows"][0]["organization"] == "Bureau of Meteorology"
    assert "CSV" in payload["rows"][0]["formats"]


def test_adapter_handles_empty_results():
    payload = _ckan_package_search_to_shape_payload(
        {"result": {"count": 0, "results": []}},
    )
    assert payload["rows"] == []


def test_search_datasets_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "au-data-gov-search-datasets")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_au_datagov_search_datasets_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": {
                "count": 1,
                "results": [{"name": "x", "title": "X"}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_au_datagov_search_datasets({"q": "x"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "X"
        assert "schema" in body
