import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_wikidata import (
    TOOLS,
    TOOLS_HANDLERS,
    _wikidata_search_to_entity_graph_payload,
    handle_wikidata_get_by_title,
    handle_wikidata_get_claims,
    handle_wikidata_get_entities,
    handle_wikidata_list_properties,
    handle_wikidata_search_entities,
    handle_wikidata_sparql,
)
from meta_data_mcp.ui_resources.app_entity_graph_v1 import URI as ENTITY_GRAPH_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_tools_registered():
    names = [t.name for t in TOOLS]
    for name in names:
        assert name in TOOLS_HANDLERS


@pytest.mark.anyio
async def test_wikidata_get_entities_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "entities": {
                "Q42": {"id": "Q42", "labels": {"en": {"value": "Douglas Adams"}}},
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikidata_get_entities({"ids": "Q42"})
        assert len(result) == 1
        assert "Douglas Adams" in result[0].text


@pytest.mark.anyio
async def test_wikidata_get_entities_missing_ids():
    with pytest.raises(ValueError):
        await handle_wikidata_get_entities({})


@pytest.mark.anyio
async def test_wikidata_search_entities_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "search": [{"id": "Q42", "label": "Douglas Adams"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikidata_search_entities({"search": "Douglas Adams"})
        assert "Q42" in result[0].text


@pytest.mark.anyio
async def test_wikidata_get_claims_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikidata_get_claims({"entity": "Q42", "property": "P31"})
        assert "P31" in result[0].text


@pytest.mark.anyio
async def test_wikidata_sparql_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": {
                "bindings": [{"item": {"value": "http://www.wikidata.org/entity/Q42"}}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikidata_sparql(
            {"query": "SELECT ?item WHERE { ?item wdt:P31 wd:Q5 } LIMIT 1"},
        )
        assert "Q42" in result[0].text


@pytest.mark.anyio
async def test_wikidata_list_properties_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "search": [{"id": "P31", "label": "instance of"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikidata_list_properties({"search": "instance of"})
        assert "P31" in result[0].text


@pytest.mark.anyio
async def test_wikidata_get_by_title_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "entities": {
                "Q937": {
                    "id": "Q937",
                    "sitelinks": {"enwiki": {"title": "Albert Einstein"}},
                },
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_wikidata_get_by_title({"titles": "Albert Einstein"})
        assert "Q937" in result[0].text


@pytest.mark.anyio
async def test_wikidata_sparql_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_wikidata_sparql(
                {"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 1"},
            )


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps entity-graph adapter + binding.
# ---------------------------------------------------------------------------


def test_wikidata_entity_graph_adapter_builds_hub_and_spoke():
    """The adapter emits an ``anchor`` node for the query and a fan-out
    ``matches`` edge to every result. Without claims fetch, this is
    the most we can surface from ``wbsearchentities`` alone.
    """
    raw = {
        "search": [
            {"id": "Q42", "label": "Douglas Adams", "description": "English author"},
            {"id": "Q1", "label": "Universe", "description": "everything"},
        ],
    }
    payload = _wikidata_search_to_entity_graph_payload(raw, query="douglas")
    types_ = {n["type"] for n in payload["nodes"]}
    assert "anchor" in types_
    assert "entity" in types_
    node_ids = {n["id"] for n in payload["nodes"]}
    assert "Q42" in node_ids and "Q1" in node_ids
    # Every result connects back to the anchor.
    anchor_id = next(n["id"] for n in payload["nodes"] if n["type"] == "anchor")
    assert all(e["source"] == anchor_id for e in payload["edges"])
    assert {e["target"] for e in payload["edges"]} == {"Q42", "Q1"}


def test_wikidata_entity_graph_adapter_edge_weight_degrades_with_rank():
    """Top match should have the highest edge weight (pulls toward the
    anchor in the force layout); later matches degrade linearly.
    """
    raw = {
        "search": [
            {"id": "Q1", "label": "first"},
            {"id": "Q2", "label": "second"},
            {"id": "Q3", "label": "third"},
        ],
    }
    payload = _wikidata_search_to_entity_graph_payload(raw, query="x")
    weights = {e["target"]: e["weight"] for e in payload["edges"]}
    assert weights["Q1"] > weights["Q2"] > weights["Q3"]


def test_wikidata_entity_graph_adapter_handles_empty_results():
    """No results → just the anchor node, no edges."""
    payload = _wikidata_search_to_entity_graph_payload({}, query="z")
    assert all(n["type"] == "anchor" for n in payload["nodes"])
    assert payload["edges"] == []


def test_wikidata_search_entities_tool_binds_to_entity_graph():
    tool = next(t for t in TOOLS if t.name == "wikidata-search-entities")
    assert tool.meta == {"ui": {"resourceUri": ENTITY_GRAPH_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == ENTITY_GRAPH_URI


@pytest.mark.anyio
async def test_wikidata_search_entities_returns_entity_graph_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "search": [
                {"id": "Q42", "label": "Douglas Adams"},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_wikidata_search_entities({"search": "douglas"})
        parsed = json.loads(result[0].text)
        assert "nodes" in parsed and "edges" in parsed
        assert any(n["type"] == "anchor" for n in parsed["nodes"])
        assert any(n["id"] == "Q42" for n in parsed["nodes"])
