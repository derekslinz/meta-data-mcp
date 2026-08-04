import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.ca_open_gov import (
    TOOLS,
    _ckan_package_search_to_shape_payload,
    handle_ca_opengov_get_dataset,
    handle_ca_opengov_get_organization,
    handle_ca_opengov_list_groups,
    handle_ca_opengov_list_licenses,
    handle_ca_opengov_list_organizations,
    handle_ca_opengov_list_tags,
    handle_ca_opengov_search_datasets,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_ca_opengov_search_datasets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {
                "count": 1,
                "results": [
                    {"name": "canada-census-2021", "title": "Canada Census 2021"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ca_opengov_search_datasets({"q": "census"})
        assert "Canada Census 2021" in result[0].text


@pytest.mark.anyio
async def test_ca_opengov_search_datasets_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Boom")

        with pytest.raises(httpx.HTTPError):
            await handle_ca_opengov_search_datasets({"q": "x"})


@pytest.mark.anyio
async def test_ca_opengov_get_dataset_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {
                "name": "canada-census-2021",
                "title": "Canada Census 2021",
                "resources": [{"format": "CSV"}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ca_opengov_get_dataset({"id": "canada-census-2021"})
        assert "Canada Census 2021" in result[0].text


@pytest.mark.anyio
async def test_ca_opengov_list_organizations_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": [{"name": "statcan", "title": "Statistics Canada"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ca_opengov_list_organizations({"limit": 10})
        assert "Statistics Canada" in result[0].text


@pytest.mark.anyio
async def test_ca_opengov_get_organization_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {"name": "statcan", "title": "Statistics Canada"},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ca_opengov_get_organization({"id": "statcan"})
        assert "Statistics Canada" in result[0].text


@pytest.mark.anyio
async def test_ca_opengov_list_groups_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": [{"name": "environment", "display_name": "Environment"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ca_opengov_list_groups({})
        assert "Environment" in result[0].text


@pytest.mark.anyio
async def test_ca_opengov_list_tags_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": ["climate", "climate-change"],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ca_opengov_list_tags({"query": "climate"})
        assert "climate" in result[0].text


@pytest.mark.anyio
async def test_ca_opengov_list_licenses_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": [
                {"id": "ca-ogl-lgo", "title": "Open Government Licence - Canada"},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ca_opengov_list_licenses({})
        assert "Open Government Licence" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for ca-open-gov-search-datasets.
# ---------------------------------------------------------------------------


def test_adapter_flattens_ckan_package_search_to_rows():
    raw = {
        "result": {
            "count": 1,
            "results": [
                {
                    "name": "canada-census-2021",
                    "title": "Canada Census 2021",
                    "organization": {"title": "Statistics Canada"},
                    "license_title": "OGL-Canada",
                    "tags": [{"display_name": "Census"}],
                    "resources": [{"format": "csv"}],
                },
            ],
        },
    }
    payload = _ckan_package_search_to_shape_payload(raw)
    assert payload["rows"][0]["organization"] == "Statistics Canada"
    assert "CSV" in payload["rows"][0]["formats"]
    assert payload["default_facets"] == ["organization", "license", "formats"]


def test_adapter_handles_empty_results():
    payload = _ckan_package_search_to_shape_payload(
        {"result": {"count": 0, "results": []}},
    )
    assert payload["rows"] == []


def test_search_datasets_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "ca-open-gov-search-datasets")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_ca_opengov_search_datasets_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {
                "count": 1,
                "results": [{"name": "x", "title": "X"}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_ca_opengov_search_datasets({"q": "x"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "X"
        assert "schema" in body
