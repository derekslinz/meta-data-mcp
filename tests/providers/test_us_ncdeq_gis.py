"""Tests for the us-ncdeq-gis provider.

Includes Phase 4 records-shape binding tests for us-ncdeq-search-catalog.
"""

import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_ncdeq_gis import (
    TOOLS,
    _arcgis_search_to_shape_payload,
    handle_us_ncdeq_search_catalog,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_us_ncdeq_search_catalog_success():
    """Smoke test: us-ncdeq-search-catalog returns records-shape payload."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "data": [
                {
                    "id": "abcd1234",
                    "type": "Feature Service",
                    "attributes": {"name": "Watersheds", "owner": "ncdeq"},
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200
        result = await handle_us_ncdeq_search_catalog({})
        body = json.loads(result[0].text)
        assert body["rows"][0]["name"] == "Watersheds"


@pytest.mark.anyio
async def test_us_ncdeq_search_catalog_http_error():
    """us-ncdeq-search-catalog propagates httpx errors."""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_us_ncdeq_search_catalog({})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for us-ncdeq-search-catalog.
# ---------------------------------------------------------------------------


def test_arcgis_adapter_flattens_jsonapi_to_rows():
    raw = {
        "data": [
            {
                "id": "abcd1234",
                "type": "Feature Service",
                "attributes": {
                    "name": "Watersheds",
                    "owner": "ncdeq",
                    "source": "NCDEQ",
                    "tags": ["water", "gis"],
                    "snippet": "All NC watersheds.",
                    "created": 1500000000,
                    "modified": 1700000000,
                    "url": "https://ncdeq.example/watersheds",
                },
            },
        ],
        "meta": {"queryParameters": {}, "stats": {"totalCount": 1}},
    }
    payload = _arcgis_search_to_shape_payload(raw)
    row = payload["rows"][0]
    assert row["name"] == "Watersheds"
    assert row["type"] == "Feature Service"
    assert "water" in row["tags"]
    assert payload["default_facets"] == ["type", "owner", "source"]


def test_arcgis_adapter_handles_missing_data():
    assert _arcgis_search_to_shape_payload({})["rows"] == []


def test_search_catalog_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "us-ncdeq-search-catalog")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI
