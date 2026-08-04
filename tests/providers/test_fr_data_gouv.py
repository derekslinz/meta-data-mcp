import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.fr_data_gouv import (
    TOOLS,
    _udata_search_to_shape_payload,
    handle_fr_datagouv_get_dataset,
    handle_fr_datagouv_get_organization,
    handle_fr_datagouv_list_organizations,
    handle_fr_datagouv_list_tags,
    handle_fr_datagouv_list_topics,
    handle_fr_datagouv_search_datasets,
    handle_fr_datagouv_search_reuses,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_fr_datagouv_search_datasets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": [{"slug": "transports-publics", "title": "Transports Publics"}],
            "total": 1,
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fr_datagouv_search_datasets({"q": "transports"})
        assert "Transports Publics" in result[0].text


@pytest.mark.anyio
async def test_fr_datagouv_search_datasets_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Panne de réseau")

        with pytest.raises(httpx.HTTPError):
            await handle_fr_datagouv_search_datasets({"q": "x"})


@pytest.mark.anyio
async def test_fr_datagouv_get_dataset_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "slug": "transports-publics",
            "title": "Transports Publics",
            "resources": [{"format": "csv"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fr_datagouv_get_dataset({"id": "transports-publics"})
        assert "Transports Publics" in result[0].text


@pytest.mark.anyio
async def test_fr_datagouv_list_organizations_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": [{"slug": "insee", "name": "INSEE"}],
            "total": 1,
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fr_datagouv_list_organizations({"page_size": 5})
        assert "INSEE" in result[0].text


@pytest.mark.anyio
async def test_fr_datagouv_get_organization_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "slug": "insee",
            "name": "INSEE",
            "description": "Institut national de la statistique",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fr_datagouv_get_organization({"id": "insee"})
        assert "INSEE" in result[0].text


@pytest.mark.anyio
async def test_fr_datagouv_search_reuses_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": [{"title": "Carte des Transports", "url": "https://example.fr"}],
            "total": 1,
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fr_datagouv_search_reuses({"q": "carte"})
        assert "Carte des Transports" in result[0].text


@pytest.mark.anyio
async def test_fr_datagouv_list_topics_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": [{"slug": "sante", "name": "Santé"}],
            "total": 1,
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fr_datagouv_list_topics({"page_size": 5})
        assert "Santé" in result[0].text


@pytest.mark.anyio
async def test_fr_datagouv_list_tags_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"text": "transport"},
            {"text": "transport-public"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fr_datagouv_list_tags({"q": "trans"})
        assert "transport" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for fr-data-gouv-search-datasets.
# ---------------------------------------------------------------------------


def test_udata_adapter_flattens_data_array_to_rows():
    raw = {
        "data": [
            {
                "slug": "transports-publics",
                "title": "Transports Publics",
                "organization": {"name": "Ministère des Transports"},
                "license": "lov2",
                "tags": ["transport", "public"],
                "resources": [{"format": "csv"}],
                "created_at": "2020-01-01",
                "last_modified": "2024-01-01",
            },
        ],
        "total": 1,
    }
    payload = _udata_search_to_shape_payload(raw)
    assert payload["total"] == 1
    row = payload["rows"][0]
    assert row["title"] == "Transports Publics"
    assert row["organization"] == "Ministère des Transports"
    assert row["license"] == "lov2"
    assert "transport" in row["tags"]
    assert "CSV" in row["formats"]
    assert payload["default_facets"] == ["organization", "license", "formats"]


def test_udata_adapter_handles_empty_data():
    payload = _udata_search_to_shape_payload({"data": [], "total": 0})
    assert payload["rows"] == []


def test_udata_adapter_handles_missing_data_key():
    payload = _udata_search_to_shape_payload({})
    assert payload["rows"] == []


def test_search_datasets_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "fr-data-gouv-search-datasets")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_fr_datagouv_search_datasets_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": [{"slug": "x", "title": "X"}],
            "total": 1,
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_fr_datagouv_search_datasets({"q": "x"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "X"
        assert body["total"] == 1
