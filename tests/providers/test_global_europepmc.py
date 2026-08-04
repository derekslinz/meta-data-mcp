import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_europepmc import (
    TOOLS,
    _europepmc_search_to_shape_payload,
    handle_europepmc_citations,
    handle_europepmc_fulltext_xml,
    handle_europepmc_get_article,
    handle_europepmc_references,
    handle_europepmc_search,
    handle_europepmc_supplementaryfiles,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_europepmc_search_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hitCount": 1,
            "resultList": {
                "result": [
                    {"id": "12345", "source": "MED", "title": "CRISPR gene editing"},
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_europepmc_search({"query": "CRISPR"})
        assert "CRISPR gene editing" in result[0].text


@pytest.mark.anyio
async def test_europepmc_search_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("EBI down")

        with pytest.raises(httpx.HTTPError):
            await handle_europepmc_search({"query": "anything"})


@pytest.mark.anyio
async def test_europepmc_search_requires_query():
    with pytest.raises(ValueError):
        await handle_europepmc_search({})


@pytest.mark.anyio
async def test_europepmc_get_article_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": {"id": "12345", "title": "A core paper", "authorList": {}},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_europepmc_get_article({"source": "MED", "id": "12345"})
        assert "A core paper" in result[0].text


@pytest.mark.anyio
async def test_europepmc_references_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hitCount": 1,
            "referenceList": {"reference": [{"id": "999", "title": "Cited paper"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_europepmc_references({"source": "MED", "id": "12345"})
        assert "Cited paper" in result[0].text


@pytest.mark.anyio
async def test_europepmc_citations_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hitCount": 1,
            "citationList": {"citation": [{"id": "1001", "title": "Citing paper"}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_europepmc_citations({"source": "MED", "id": "12345"})
        assert "Citing paper" in result[0].text


@pytest.mark.anyio
async def test_europepmc_fulltext_xml_success():
    sample_xml = (
        '<?xml version="1.0"?>'
        "<article><front><title>Full text article</title></front></article>"
    )
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = sample_xml
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_europepmc_fulltext_xml({"source": "PMC", "id": "PMC1234"})
        assert "Full text article" in result[0].text


@pytest.mark.anyio
async def test_europepmc_supplementaryfiles_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "supplementaryFiles": [
                {"name": "table_s1.xlsx", "url": "http://example.com/s1.xlsx"},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_europepmc_supplementaryfiles(
            {"source": "PMC", "id": "PMC1234"},
        )
        assert "table_s1.xlsx" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for europepmc-search.
# ---------------------------------------------------------------------------


def test_europepmc_adapter_flattens_result_list_to_rows():
    raw = {
        "hitCount": 1,
        "nextCursorMark": "ABCD",
        "resultList": {
            "result": [
                {
                    "id": "12345",
                    "source": "MED",
                    "pmid": "12345",
                    "doi": "10.1038/x",
                    "title": "CRISPR review",
                    "authorString": "Doudna J, Charpentier E",
                    "journalTitle": "Nature",
                    "pubYear": 2020,
                    "pubType": "research-article; journal article",
                    "isOpenAccess": "Y",
                    "citedByCount": 1234,
                },
            ],
        },
    }
    payload = _europepmc_search_to_shape_payload(raw)
    assert payload["hitCount"] == 1
    assert payload["nextCursorMark"] == "ABCD"
    row = payload["rows"][0]
    assert row["title"] == "CRISPR review"
    assert row["source"] == "MED"
    assert row["citedByCount"] == 1234
    assert payload["default_facets"] == ["source", "pubType", "journal"]


def test_europepmc_adapter_handles_missing_result_list():
    assert _europepmc_search_to_shape_payload({})["rows"] == []
    assert _europepmc_search_to_shape_payload({"resultList": {}})["rows"] == []


def test_europepmc_search_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "europepmc-search")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_europepmc_search_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hitCount": 1,
            "resultList": {"result": [{"id": "12345", "title": "CRISPR review"}]},
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_europepmc_search({"query": "CRISPR"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "CRISPR review"
