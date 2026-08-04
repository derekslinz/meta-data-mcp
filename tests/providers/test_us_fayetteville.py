"""Tests for the us-fayetteville provider.

Includes Phase 4 records-shape binding tests for us-fayetteville-search-catalog.
"""

import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_fayetteville import (
    TOOLS,
    _socrata_views_to_shape_payload,
    handle_us_fayetteville_get_metadata,
    handle_us_fayetteville_search_catalog,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_us_fayetteville_search_catalog_success():
    """Smoke test: us-fayetteville-search-catalog returns records-shape payload."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"id": "abcd-1234", "name": "Fayetteville Permits"},
        ]
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200
        result = await handle_us_fayetteville_search_catalog({})
        assert len(result) == 1
        body = json.loads(result[0].text)
        assert body["rows"][0]["name"] == "Fayetteville Permits"
        assert "schema" in body


@pytest.mark.anyio
async def test_us_fayetteville_search_catalog_http_error():
    """us-fayetteville-search-catalog propagates httpx errors."""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_us_fayetteville_search_catalog({})


@pytest.mark.anyio
async def test_us_fayetteville_get_metadata_success():
    """Smoke test: us-fayetteville-get-metadata returns success payload."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "ok": True,
            "marker": "GENERATED_TEST_MARKER",
        }
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200
        result = await handle_us_fayetteville_get_metadata({"dataset_id": "test"})
        assert len(result) == 1
        assert "GENERATED_TEST_MARKER" in result[0].text


@pytest.mark.anyio
async def test_us_fayetteville_get_metadata_http_error():
    """us-fayetteville-get-metadata propagates httpx errors."""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_us_fayetteville_get_metadata({"dataset_id": "test"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for us-fayetteville-search-catalog.
# ---------------------------------------------------------------------------


def test_socrata_adapter_flattens_views_to_rows():
    raw = [
        {
            "id": "abcd-1234",
            "name": "Fayetteville Permits",
            "category": "Public Safety",
            "owner": {"displayName": "City Planning"},
        },
    ]
    payload = _socrata_views_to_shape_payload(raw)
    row = payload["rows"][0]
    assert row["name"] == "Fayetteville Permits"
    assert row["owner"] == "City Planning"
    assert payload["default_facets"] == ["category", "viewType", "attribution"]


def test_socrata_adapter_handles_non_list_input():
    assert _socrata_views_to_shape_payload({})["rows"] == []


def test_search_catalog_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "us-fayetteville-search-catalog")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI
