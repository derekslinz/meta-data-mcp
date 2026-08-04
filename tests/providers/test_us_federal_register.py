import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_federal_register import (
    TOOLS,
    FedRegListExecutiveOrdersParams,
    FedRegSearchDocumentsParams,
    _fedreg_search_to_shape_payload,
    handle_fedreg_get_agency,
    handle_fedreg_get_document,
    handle_fedreg_list_agencies,
    handle_fedreg_list_executive_orders,
    handle_fedreg_public_inspection,
    handle_fedreg_search_documents,
    handle_fedreg_suggested_searches,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_fedreg_search_documents_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "results": [
                {
                    "document_number": "2024-12345",
                    "title": "Clean Air Act amendments",
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fedreg_search_documents({"term": "clean air"})
        assert "Clean Air Act amendments" in result[0].text


@pytest.mark.anyio
async def test_fedreg_search_documents_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("FedReg down")

        with pytest.raises(httpx.HTTPError):
            await handle_fedreg_search_documents({"term": "x"})


@pytest.mark.anyio
async def test_fedreg_search_documents_passes_term_filter():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"count": 0, "results": []}
        mock_get.return_value.raise_for_status = Mock()

        await handle_fedreg_search_documents({"term": "ozone", "per_page": 5})

        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("conditions[term]") == "ozone"
        assert params.get("per_page") == 5


@pytest.mark.anyio
async def test_fedreg_get_document_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "document_number": "2024-12345",
            "title": "Rule X",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fedreg_get_document({"document_number": "2024-12345"})
        assert "Rule X" in result[0].text
        assert "2024-12345" in result[0].text


@pytest.mark.anyio
async def test_fedreg_get_document_requires_document_number():
    with pytest.raises(ValueError):
        await handle_fedreg_get_document({})


@pytest.mark.anyio
async def test_fedreg_list_agencies_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"slug": "environmental-protection-agency", "name": "EPA"},
            {"slug": "department-of-energy", "name": "DOE"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fedreg_list_agencies({})
        assert "EPA" in result[0].text
        assert "environmental-protection-agency" in result[0].text


@pytest.mark.anyio
async def test_fedreg_get_agency_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "slug": "environmental-protection-agency",
            "name": "Environmental Protection Agency",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fedreg_get_agency(
            {"slug": "environmental-protection-agency"},
        )
        assert "Environmental Protection Agency" in result[0].text


@pytest.mark.anyio
async def test_fedreg_public_inspection_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "results": [{"document_number": "PI-1", "title": "Pre-pub"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fedreg_public_inspection({"available_on": "2024-05-10"})
        assert "Pre-pub" in result[0].text


@pytest.mark.anyio
async def test_fedreg_list_executive_orders_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "results": [
                {
                    "document_number": "2024-99999",
                    "title": "Executive Order on Energy",
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fedreg_list_executive_orders({"president": "joe-biden"})
        assert "Executive Order on Energy" in result[0].text

        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("conditions[presidential_document_type]") == "executive_order"


@pytest.mark.anyio
async def test_fedreg_suggested_searches_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"title": "Climate change", "slug": "climate-change"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_fedreg_suggested_searches({"sections": "environment"})
        assert "Climate change" in result[0].text


@pytest.mark.parametrize(
    "param_model_class",
    [FedRegSearchDocumentsParams, FedRegListExecutiveOrdersParams],
)
def test_fedreg_page_schema_keeps_default_and_optional(param_model_class):
    schema = param_model_class.model_json_schema()
    assert schema["properties"]["page"]["default"] == 1
    assert "required" not in schema or "page" not in schema["required"]


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for fedreg-search-documents.
# ---------------------------------------------------------------------------


def test_fedreg_adapter_flattens_results_to_rows():
    raw = {
        "count": 1,
        "results": [
            {
                "document_number": "2024-12345",
                "title": "Clean Air Act amendments",
                "type": "Rule",
                "publication_date": "2024-06-01",
                "agencies": [{"name": "EPA"}],
                "html_url": "https://federalregister.gov/d/2024-12345",
                "abstract": "Rule about CAA.",
            },
        ],
    }
    payload = _fedreg_search_to_shape_payload(raw)
    assert payload["count"] == 1
    row = payload["rows"][0]
    assert row["title"] == "Clean Air Act amendments"
    assert row["type"] == "Rule"
    assert row["agencies"] == "EPA"
    assert payload["default_facets"] == ["type", "agencies"]


def test_fedreg_adapter_handles_missing_results():
    assert _fedreg_search_to_shape_payload({})["rows"] == []


def test_fedreg_search_documents_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "fedreg-search-documents")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_fedreg_search_documents_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "results": [{"document_number": "2024-12345", "title": "Clean Air"}],
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_fedreg_search_documents({"term": "clean air"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "Clean Air"
        assert "schema" in body
