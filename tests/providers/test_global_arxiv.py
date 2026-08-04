import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_arxiv import (
    TOOLS,
    _arxiv_atom_to_shape_payload,
    handle_arxiv_get_paper,
    handle_arxiv_query,
    handle_arxiv_search_by_author,
    handle_arxiv_search_by_category,
    handle_arxiv_search_by_title,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


ATOM_SAMPLE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    "<entry><id>http://arxiv.org/abs/2104.08653</id>"
    "<title>Attention Is All You Need</title>"
    "<author><name>Ashish Vaswani</name></author>"
    '<category term="cs.LG"/>'
    "</entry></feed>"
)


@pytest.mark.anyio
async def test_arxiv_query_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_arxiv_query({"search_query": "all:attention"})
        assert "Attention Is All You Need" in result[0].text


@pytest.mark.anyio
async def test_arxiv_query_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("arXiv down")

        with pytest.raises(httpx.HTTPError):
            await handle_arxiv_query({"search_query": "all:anything"})


@pytest.mark.anyio
async def test_arxiv_query_requires_search_query():
    with pytest.raises(ValueError):
        await handle_arxiv_query({})


@pytest.mark.anyio
async def test_arxiv_search_by_title_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_arxiv_search_by_title({"title": "Attention"})
        assert "Attention Is All You Need" in result[0].text


@pytest.mark.anyio
async def test_arxiv_search_by_author_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_arxiv_search_by_author({"author": "Vaswani"})
        assert "Ashish Vaswani" in result[0].text


@pytest.mark.anyio
async def test_arxiv_search_by_category_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_arxiv_search_by_category({"category": "cs.LG"})
        assert "cs.LG" in result[0].text


@pytest.mark.anyio
async def test_arxiv_get_paper_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_arxiv_get_paper({"arxiv_id": "2104.08653"})
        assert "2104.08653" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for arxiv-query.
# ---------------------------------------------------------------------------


def test_arxiv_adapter_parses_atom_feed_into_rows():
    payload = _arxiv_atom_to_shape_payload(ATOM_SAMPLE)
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["id"] == "2104.08653"
    assert row["title"] == "Attention Is All You Need"
    assert row["authors"] == "Ashish Vaswani"
    assert "cs.LG" in row["categories"]
    assert payload["default_facets"] == ["primary_category"]


def test_arxiv_adapter_handles_empty_feed():
    payload = _arxiv_atom_to_shape_payload(
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>',
    )
    assert payload["rows"] == []


def test_arxiv_adapter_handles_malformed_xml():
    payload = _arxiv_atom_to_shape_payload("<not closed")
    assert payload["rows"] == []


def test_arxiv_adapter_handles_empty_string():
    payload = _arxiv_atom_to_shape_payload("")
    assert payload["rows"] == []


def test_arxiv_query_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "arxiv-query")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_arxiv_query_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_arxiv_query({"search_query": "all:attention"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "Attention Is All You Need"
        assert "schema" in body
