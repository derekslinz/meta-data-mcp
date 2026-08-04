import json
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import httpx
import pytest

from meta_data_mcp.providers.global_wikipedia import (
    TOOLS,
    TOOLS_HANDLERS,
    _wikipedia_opensearch_to_shape_payload,
    handle_wikipedia_get_html,
    handle_wikipedia_get_media_list,
    handle_wikipedia_get_mobile_sections,
    handle_wikipedia_get_on_this_day,
    handle_wikipedia_get_page_views,
    handle_wikipedia_get_related,
    handle_wikipedia_get_summary,
    handle_wikipedia_search_title,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_tools_registered():
    names = [t.name for t in TOOLS]
    for name in names:
        assert name in TOOLS_HANDLERS


@pytest.mark.anyio
async def test_wikipedia_get_summary_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "title": "Albert Einstein",
            "extract": "German-born theoretical physicist...",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikipedia_get_summary({"title": "Albert Einstein"})
        assert len(result) == 1
        assert "Einstein" in result[0].text


@pytest.mark.anyio
async def test_wikipedia_get_summary_lang_override():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "title": "Albert Einstein",
            "extract": "Physicien...",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikipedia_get_summary(
            {"title": "Albert Einstein", "lang": "fr"},
        )
        # Verify the URL the caller built points at the French Wikipedia.
        called_url = mock_get.call_args.args[0]
        assert urlparse(called_url).hostname == "fr.wikipedia.org"
        assert "Einstein" in result[0].text


@pytest.mark.anyio
async def test_wikipedia_get_summary_missing_title():
    with pytest.raises(ValueError):
        await handle_wikipedia_get_summary({})


@pytest.mark.anyio
async def test_wikipedia_get_html_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = "<html><body>Article body</body></html>"
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikipedia_get_html(
            {"title": "Python_(programming_language)"},
        )
        assert "Article body" in result[0].text


@pytest.mark.anyio
async def test_wikipedia_get_mobile_sections_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"lead": {"sections": []}}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikipedia_get_mobile_sections({"title": "Python"})
        assert "sections" in result[0].text


@pytest.mark.anyio
async def test_wikipedia_get_related_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"pages": [{"title": "Ruby"}]}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikipedia_get_related({"title": "Python"})
        assert "Ruby" in result[0].text


@pytest.mark.anyio
async def test_wikipedia_get_media_list_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "items": [{"title": "File:Logo.png"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikipedia_get_media_list({"title": "Python"})
        assert "Logo.png" in result[0].text


@pytest.mark.anyio
async def test_wikipedia_search_title_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            "Pyth",
            ["Python", "Python (programming language)"],
            ["", ""],
            ["https://en.wikipedia.org/wiki/Python", ""],
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikipedia_search_title({"search": "Pyth"})
        assert "Python" in result[0].text


@pytest.mark.anyio
async def test_wikipedia_get_page_views_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "items": [{"timestamp": "2024010100", "views": 1000}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikipedia_get_page_views(
            {"title": "Python", "start": "20240101", "end": "20240107"},
        )
        assert "1000" in result[0].text


@pytest.mark.anyio
async def test_wikipedia_get_on_this_day_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "events": [{"text": "Something historical", "year": 1969}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikipedia_get_on_this_day({"month": "07", "day": "20"})
        assert "1969" in result[0].text


@pytest.mark.anyio
async def test_wikipedia_get_summary_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_wikipedia_get_summary({"title": "Anything"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for wikipedia-search-title.
# ---------------------------------------------------------------------------


def test_wikipedia_adapter_zips_opensearch_arrays_to_rows():
    raw = [
        "Pyth",
        ["Python", "Python (programming language)"],
        ["", "Programming language"],
        [
            "https://en.wikipedia.org/wiki/Python",
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
        ],
    ]
    payload = _wikipedia_opensearch_to_shape_payload(raw)
    assert payload["query"] == "Pyth"
    assert len(payload["rows"]) == 2
    assert payload["rows"][0]["title"] == "Python"
    assert payload["rows"][1]["description"] == "Programming language"


def test_wikipedia_adapter_handles_malformed_response():
    assert _wikipedia_opensearch_to_shape_payload([])["rows"] == []
    assert _wikipedia_opensearch_to_shape_payload(None)["rows"] == []
    assert _wikipedia_opensearch_to_shape_payload(["only", "two"])["rows"] == []


def test_search_title_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "wikipedia-search-title")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_wikipedia_search_title_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            "Pyth",
            ["Python"],
            [""],
            ["https://en.wikipedia.org/wiki/Python"],
        ]
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_wikipedia_search_title({"search": "Pyth"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "Python"
        assert "schema" in body
