import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from meta_data_mcp.providers.nl_rechtspraak import (
    TOOLS,
    RechtspraakContentParams,
    RechtspraakSearchParams,
    _rechtspraak_search_to_shape_payload,
    fetch_rechtspraak_content,
    handle_rechtspraak_content,
    handle_rechtspraak_search,
    search_rechtspraak,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>ECLI:NL:HR:2020:1234</id>
    <title>Arbeidsrecht uitspraak</title>
    <summary>Korte samenvatting</summary>
    <updated>2020-01-02T03:04:05Z</updated>
    <link href="https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:HR:2020:1234" />
  </entry>
  <entry>
    <id>ECLI:NL:RVS:2021:2222</id>
    <title>Bestuursrecht uitspraak</title>
    <summary>Tweede samenvatting</summary>
    <updated>2021-02-03T04:05:06Z</updated>
  </entry>
</feed>
"""


def test_search_rechtspraak_parses_feed_and_handles_missing_link():
    with patch("meta_data_mcp.providers.nl_rechtspraak.http_get") as mock_get:
        mock_get.return_value.content = ATOM_XML.encode("utf-8")

        result = search_rechtspraak(
            RechtspraakSearchParams(query="arbeidsrecht", max_results=2),
        )

        assert len(result) == 2
        assert result[0]["ecli"] == "ECLI:NL:HR:2020:1234"
        assert "Arbeidsrecht uitspraak" in result[0]["title"]
        assert result[0]["link"].startswith("https://uitspraken.rechtspraak.nl/details")
        assert result[1]["ecli"] == "ECLI:NL:RVS:2021:2222"
        assert result[1]["link"] == ""


def test_search_rechtspraak_passes_expected_query_params():
    with patch("meta_data_mcp.providers.nl_rechtspraak.http_get") as mock_get:
        mock_get.return_value.content = ATOM_XML.encode("utf-8")

        search_rechtspraak(
            RechtspraakSearchParams(
                query="belastingrecht",
                max_results=7,
                date_from="2024-01-01",
            ),
        )

        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {
            "q": "belastingrecht",
            "max": 7,
            "date": "2024-01-01",
        }


@pytest.mark.anyio
async def test_handle_rechtspraak_search_returns_serialized_results():
    with patch("meta_data_mcp.providers.nl_rechtspraak.http_get") as mock_get:
        mock_get.return_value.content = ATOM_XML.encode("utf-8")

        result = await handle_rechtspraak_search(
            {"query": "arbeidsrecht", "max_results": 2},
        )

        assert len(result) == 1
        assert result[0].type == "text"
        assert "ECLI:NL:HR:2020:1234" in result[0].text
        assert "Bestuursrecht uitspraak" in result[0].text


@pytest.mark.anyio
async def test_handle_rechtspraak_search_propagates_http_errors():
    with patch("meta_data_mcp.providers.nl_rechtspraak.http_get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("service unavailable")

        with pytest.raises(httpx.HTTPError):
            await handle_rechtspraak_search({"query": "arbeidsrecht"})


@pytest.mark.anyio
async def test_handle_rechtspraak_search_requires_query_argument():
    with pytest.raises(ValidationError):
        await handle_rechtspraak_search({"max_results": 5})


def test_fetch_rechtspraak_content_returns_raw_xml_text():
    with patch("meta_data_mcp.providers.nl_rechtspraak.http_get") as mock_get:
        mock_get.return_value.text = (
            "<uitspraak><inhoud>Volledige tekst</inhoud></uitspraak>"
        )

        xml = fetch_rechtspraak_content(
            RechtspraakContentParams(ecli="ECLI:NL:HR:2020:1234"),
        )

        assert "Volledige tekst" in xml
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"id": "ECLI:NL:HR:2020:1234"}


@pytest.mark.anyio
async def test_handle_rechtspraak_content_returns_text_payload():
    with patch("meta_data_mcp.providers.nl_rechtspraak.http_get") as mock_get:
        mock_get.return_value.text = (
            "<uitspraak><id>ECLI:NL:HR:2020:1234</id></uitspraak>"
        )

        result = await handle_rechtspraak_content({"ecli": "ECLI:NL:HR:2020:1234"})

        assert len(result) == 1
        assert result[0].type == "text"
        assert "ECLI:NL:HR:2020:1234" in result[0].text


@pytest.mark.anyio
async def test_handle_rechtspraak_content_requires_ecli_argument():
    with pytest.raises(ValidationError):
        await handle_rechtspraak_content({})


@pytest.mark.anyio
async def test_handle_rechtspraak_content_propagates_http_errors():
    with patch("meta_data_mcp.providers.nl_rechtspraak.http_get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("timeout")

        with pytest.raises(httpx.HTTPError):
            await handle_rechtspraak_content({"ecli": "ECLI:NL:HR:2020:1234"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for rechtspraak-search.
# ---------------------------------------------------------------------------


def test_rechtspraak_adapter_wraps_list_into_rows():
    raw = [
        {
            "ecli": "ECLI:NL:HR:2020:1234",
            "title": "Arbeidsrecht uitspraak",
            "summary": "Korte samenvatting",
            "updated": "2020-01-02T03:04:05Z",
            "link": "https://uitspraken.rechtspraak.nl/details?id=ECLI:NL:HR:2020:1234",
        },
    ]
    payload = _rechtspraak_search_to_shape_payload(raw)
    assert payload["rows"][0]["ecli"] == "ECLI:NL:HR:2020:1234"
    assert "schema" in payload


def test_rechtspraak_adapter_handles_empty_list():
    assert _rechtspraak_search_to_shape_payload([])["rows"] == []
    assert _rechtspraak_search_to_shape_payload(None)["rows"] == []


def test_rechtspraak_search_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "rechtspraak-search")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_handle_rechtspraak_search_returns_shape_payload():
    with patch("meta_data_mcp.providers.nl_rechtspraak.http_get") as mock_get:
        mock_get.return_value.content = ATOM_XML.encode("utf-8")
        result = await handle_rechtspraak_search({"query": "arbeidsrecht"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["ecli"] == "ECLI:NL:HR:2020:1234"
        assert "schema" in body
