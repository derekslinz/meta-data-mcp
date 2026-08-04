import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.uk_legislation import (
    TOOLS,
    _uk_legislation_atom_to_shape_payload,
    handle_uk_legislation_changes_feed,
    handle_uk_legislation_get_document_html,
    handle_uk_legislation_get_document_xml,
    handle_uk_legislation_list_by_year,
    handle_uk_legislation_list_types,
    handle_uk_legislation_search,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


ATOM_SAMPLE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom">'
    "<title>UK Legislation Search</title>"
    "<entry><title>Data Protection Act 2018</title>"
    '<link href="https://www.legislation.gov.uk/ukpga/2018/12"/>'
    "</entry></feed>"
)

XML_SAMPLE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<akomaNtoso><act><meta><identification><title>Test Act</title>"
    "</identification></meta></act></akomaNtoso>"
)

HTML_SAMPLE = (
    "<html><body><h1>Browse Legislation</h1><ul><li>ukpga</li></ul></body></html>"
)


@pytest.mark.anyio
async def test_uk_legislation_search_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_legislation_search({"title": "Data Protection"})
        assert "Data Protection Act 2018" in result[0].text


@pytest.mark.anyio
async def test_uk_legislation_search_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("legislation.gov.uk down")

        with pytest.raises(httpx.HTTPError):
            await handle_uk_legislation_search({"title": "anything"})


@pytest.mark.anyio
async def test_uk_legislation_search_uses_atom_accept_header():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        await handle_uk_legislation_search({"text": "data"})

        headers = mock_get.call_args.kwargs.get("headers", {})
        assert headers.get("Accept") == "application/atom+xml"


@pytest.mark.anyio
async def test_uk_legislation_list_by_year_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_legislation_list_by_year(
            {"type": "ukpga", "year": 2018},
        )
        assert "Data Protection Act 2018" in result[0].text


@pytest.mark.anyio
async def test_uk_legislation_list_by_year_requires_args():
    with pytest.raises(ValueError):
        await handle_uk_legislation_list_by_year({"type": "ukpga"})


@pytest.mark.anyio
async def test_uk_legislation_get_document_xml_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = XML_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_legislation_get_document_xml(
            {"type": "ukpga", "year": 2018, "number": 12},
        )
        assert "Test Act" in result[0].text
        assert "akomaNtoso" in result[0].text


@pytest.mark.anyio
async def test_uk_legislation_get_document_xml_url():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = XML_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        await handle_uk_legislation_get_document_xml(
            {"type": "ukpga", "year": 2018, "number": 12},
        )
        called_url = mock_get.call_args.args[0]
        assert called_url.endswith("/ukpga/2018/12/data.xml")


@pytest.mark.anyio
async def test_uk_legislation_get_document_html_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = HTML_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_legislation_get_document_html(
            {"type": "ukpga", "year": 2018, "number": 12},
        )
        assert "Browse Legislation" in result[0].text


@pytest.mark.anyio
async def test_uk_legislation_list_types_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = HTML_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_legislation_list_types({})
        assert "Browse Legislation" in result[0].text
        assert "ukpga" in result[0].text


@pytest.mark.anyio
async def test_uk_legislation_changes_feed_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_legislation_changes_feed(
            {"type": "ukpga", "year": 2018, "number": 12},
        )
        assert "UK Legislation Search" in result[0].text


@pytest.mark.anyio
async def test_uk_legislation_changes_feed_requires_args():
    with pytest.raises(ValueError):
        await handle_uk_legislation_changes_feed({"type": "ukpga", "year": 2018})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for uk-legislation-search.
# ---------------------------------------------------------------------------


def test_uk_legislation_adapter_parses_atom_feed_into_rows():
    payload = _uk_legislation_atom_to_shape_payload(ATOM_SAMPLE)
    assert payload["rows"][0]["title"] == "Data Protection Act 2018"
    assert payload["rows"][0]["link"] == "https://www.legislation.gov.uk/ukpga/2018/12"
    assert payload["default_facets"] == ["type", "year"]


def test_uk_legislation_adapter_handles_empty_feed():
    payload = _uk_legislation_atom_to_shape_payload(
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>',
    )
    assert payload["rows"] == []


def test_uk_legislation_adapter_handles_malformed_xml():
    assert _uk_legislation_atom_to_shape_payload("<bad")["rows"] == []


def test_uk_legislation_search_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "uk-legislation-search")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_uk_legislation_search_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = ATOM_SAMPLE
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_uk_legislation_search({"title": "Data Protection"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "Data Protection Act 2018"
        assert "schema" in body
