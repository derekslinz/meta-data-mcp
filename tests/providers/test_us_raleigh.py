"""Tests for the us-raleigh provider.

Includes Phase 4 records-shape binding tests for us-raleigh-search-catalog.
"""

import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_raleigh import (
    TOOLS,
    _socrata_views_to_shape_payload,
    handle_us_raleigh_get_metadata,
    handle_us_raleigh_search_catalog,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_us_raleigh_search_catalog_success():
    """Smoke test: us-raleigh-search-catalog returns records-shape payload."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"id": "abcd-1234", "name": "Raleigh Crime Incidents"},
        ]
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200
        result = await handle_us_raleigh_search_catalog({})
        body = json.loads(result[0].text)
        assert body["rows"][0]["name"] == "Raleigh Crime Incidents"


@pytest.mark.anyio
async def test_us_raleigh_search_catalog_http_error():
    """us-raleigh-search-catalog propagates httpx errors."""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_us_raleigh_search_catalog({})


@pytest.mark.anyio
async def test_us_raleigh_get_metadata_success():
    """Smoke test: us-raleigh-get-metadata returns success payload."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "ok": True,
            "marker": "GENERATED_TEST_MARKER",
        }
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200
        result = await handle_us_raleigh_get_metadata({"dataset_id": "test"})
        assert len(result) == 1
        assert "GENERATED_TEST_MARKER" in result[0].text


@pytest.mark.anyio
async def test_us_raleigh_get_metadata_http_error():
    """us-raleigh-get-metadata propagates httpx errors."""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_us_raleigh_get_metadata({"dataset_id": "test"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for us-raleigh-search-catalog.
# ---------------------------------------------------------------------------


def test_socrata_adapter_flattens_views_to_rows():
    raw = [{"id": "abcd-1234", "name": "Raleigh Crime", "category": "Public Safety"}]
    payload = _socrata_views_to_shape_payload(raw)
    assert payload["rows"][0]["name"] == "Raleigh Crime"
    assert payload["default_facets"] == ["category", "viewType", "attribution"]


def test_socrata_adapter_handles_non_list_input():
    assert _socrata_views_to_shape_payload({})["rows"] == []


def test_search_catalog_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "us-raleigh-search-catalog")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


def test_adapter_owner_non_dict_does_not_raise_attribute_error():
    """REGRESSION: ``a.get("x") or a.get("y") if isinstance(a, dict) else None``
    parses as ``a.get("x") or (a.get("y") if isinstance(a, dict) else None)``
    — the FIRST ``.get()`` always runs, so a non-dict truthy ``owner``
    raises AttributeError. The fix wraps the or-chain in parens so the
    conditional applies to the whole expression. Mirrors the same fix in
    us_fayetteville, us_cary, us_cdc_socrata, us_healthdata_gov,
    fr_data_gouv, us_data_gov.
    """
    raw = [
        {
            "id": "abc-1234",
            "name": "Sample",
            "owner": "not-a-dict-just-a-string",
        },
    ]
    payload = _socrata_views_to_shape_payload(raw)
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["id"] == "abc-1234"
    # Row stores owner_name under the "owner" key (after adapter resolution);
    # when the raw owner is non-dict, the resolved value is None.
    assert payload["rows"][0]["owner"] is None
