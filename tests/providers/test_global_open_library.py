import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_open_library import (
    TOOLS,
    _openlibrary_search_to_shape_payload,
    handle_openlibrary_get_author,
    handle_openlibrary_get_edition,
    handle_openlibrary_get_work,
    handle_openlibrary_isbn_lookup,
    handle_openlibrary_search_authors,
    handle_openlibrary_search_books,
    handle_openlibrary_subject,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_openlibrary_search_books_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "numFound": 1,
            "docs": [
                {
                    "title": "The Lord of the Rings",
                    "author_name": ["J.R.R. Tolkien"],
                    "key": "/works/OL27448W",
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openlibrary_search_books(
            {"title": "lord of the rings", "limit": 1},
        )
        assert "Tolkien" in result[0].text


@pytest.mark.anyio
async def test_openlibrary_search_books_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("openlibrary down")

        with pytest.raises(httpx.HTTPError):
            await handle_openlibrary_search_books({"q": "anything"})


@pytest.mark.anyio
async def test_openlibrary_search_authors_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "numFound": 1,
            "docs": [{"name": "J.R.R. Tolkien", "key": "OL26320A"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openlibrary_search_authors({"q": "tolkien"})
        assert "Tolkien" in result[0].text


@pytest.mark.anyio
async def test_openlibrary_search_authors_missing_q():
    with pytest.raises(Exception):
        await handle_openlibrary_search_authors({})


@pytest.mark.anyio
async def test_openlibrary_get_work_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "title": "The Lord of the Rings",
            "key": "/works/OL27448W",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openlibrary_get_work({"work_id": "OL27448W"})
        assert "Lord of the Rings" in result[0].text


@pytest.mark.anyio
async def test_openlibrary_get_edition_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "title": "The Fellowship of the Ring",
            "isbn_13": ["9780261103573"],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openlibrary_get_edition({"edition_id": "OL7353617M"})
        assert "Fellowship of the Ring" in result[0].text


@pytest.mark.anyio
async def test_openlibrary_get_author_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "name": "J.R.R. Tolkien",
            "birth_date": "3 January 1892",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openlibrary_get_author({"author_id": "OL26320A"})
        assert "Tolkien" in result[0].text


@pytest.mark.anyio
async def test_openlibrary_isbn_lookup_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "title": "The Hobbit",
            "isbn_13": ["9780261103283"],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openlibrary_isbn_lookup({"isbn": "9780261103283"})
        assert "Hobbit" in result[0].text


@pytest.mark.anyio
async def test_openlibrary_subject_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "name": "science_fiction",
            "work_count": 12345,
            "works": [{"title": "Dune"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openlibrary_subject(
            {"subject": "science_fiction", "limit": 5},
        )
        assert "Dune" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for openlibrary-search-books.
# ---------------------------------------------------------------------------


def test_openlibrary_adapter_flattens_docs_to_rows():
    raw = {
        "numFound": 1,
        "docs": [
            {
                "key": "/works/OL45804W",
                "title": "Foundation",
                "author_name": ["Isaac Asimov"],
                "first_publish_year": 1951,
                "language": ["eng"],
                "publisher": ["Gnome Press", "Avon"],
                "edition_count": 50,
                "cover_i": 12345,
            },
        ],
    }
    payload = _openlibrary_search_to_shape_payload(raw)
    assert payload["numFound"] == 1
    row = payload["rows"][0]
    assert row["title"] == "Foundation"
    assert row["authors"] == "Isaac Asimov"
    assert row["first_publish_year"] == 1951
    assert "eng" in row["languages"]
    assert payload["default_facets"] == ["first_publish_year", "languages"]


def test_openlibrary_adapter_handles_empty_docs():
    assert _openlibrary_search_to_shape_payload({"docs": []})["rows"] == []
    assert _openlibrary_search_to_shape_payload({})["rows"] == []


def test_search_books_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "openlibrary-search-books")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_openlibrary_search_books_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "numFound": 1,
            "docs": [{"key": "/works/x", "title": "Foundation"}],
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_openlibrary_search_books({"q": "Foundation"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "Foundation"
        assert "schema" in body
