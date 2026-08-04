"""Tests for the us-healthdata-gov provider.

Includes Phase 4 records-shape binding tests for us-healthdata-search-catalog.
"""

import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_healthdata_gov import (
    TOOLS,
    _socrata_views_to_shape_payload,
    handle_us_healthdata_get_metadata,
    handle_us_healthdata_search_catalog,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_us_healthdata_search_catalog_success():
    """Smoke test: us-healthdata-search-catalog returns records-shape payload."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"id": "abcd-1234", "name": "Hospital Capacity Dataset"},
        ]
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200
        result = await handle_us_healthdata_search_catalog({})
        body = json.loads(result[0].text)
        assert body["rows"][0]["name"] == "Hospital Capacity Dataset"


@pytest.mark.anyio
async def test_us_healthdata_search_catalog_http_error():
    """us-healthdata-search-catalog propagates httpx errors."""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_us_healthdata_search_catalog({})


@pytest.mark.anyio
async def test_us_healthdata_get_metadata_success():
    """Smoke test: us-healthdata-get-metadata returns success payload."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "ok": True,
            "marker": "GENERATED_TEST_MARKER",
        }
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200
        result = await handle_us_healthdata_get_metadata({"dataset_id": "test"})
        assert len(result) == 1
        assert "GENERATED_TEST_MARKER" in result[0].text


@pytest.mark.anyio
async def test_us_healthdata_get_metadata_http_error():
    """us-healthdata-get-metadata propagates httpx errors."""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_us_healthdata_get_metadata({"dataset_id": "test"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for us-healthdata-search-catalog.
# ---------------------------------------------------------------------------


def test_socrata_adapter_flattens_views_to_rows():
    raw = [
        {
            "id": "abcd-1234",
            "name": "Hospital Capacity",
            "category": "Health",
            "attribution": "HHS",
        },
    ]
    payload = _socrata_views_to_shape_payload(raw)
    assert payload["rows"][0]["name"] == "Hospital Capacity"
    assert payload["rows"][0]["attribution"] == "HHS"
    assert payload["default_facets"] == ["category", "viewType", "attribution"]


def test_socrata_adapter_handles_non_list_input():
    assert _socrata_views_to_shape_payload({})["rows"] == []


def test_search_catalog_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "us-healthdata-search-catalog")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI
