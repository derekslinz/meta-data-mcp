"""Tests for the global-opensanctions provider."""

import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_opensanctions import (
    TOOLS,
    OpenSanctionsGetEntityParams,
    OpenSanctionsSearchParams,
    _opensanctions_search_to_entity_graph_payload,
    fetch_opensanctions_get_entity,
    fetch_opensanctions_search,
    handle_opensanctions_search,
)
from meta_data_mcp.ui_resources.app_entity_graph_v1 import URI as ENTITY_GRAPH_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _ok(payload: dict) -> Mock:
    r = Mock()
    r.json.return_value = payload
    r.raise_for_status = Mock()
    r.status_code = 200
    r.headers = {}
    return r


def test_search_default_dataset_in_path():
    payload = {"results": [{"id": "NK-test", "schema": "Person"}]}
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        result = fetch_opensanctions_search(OpenSanctionsSearchParams(query="putin"))
        assert result["results"][0]["id"] == "NK-test"
        assert "/search/default" in mock_get.call_args[0][0]
        assert mock_get.call_args[1]["params"]["q"] == "putin"


def test_search_custom_dataset():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"results": []})
        fetch_opensanctions_search(
            OpenSanctionsSearchParams(query="acme corp", dataset="us_ofac_sdn"),
        )
        assert "/search/us_ofac_sdn" in mock_get.call_args[0][0]


def test_search_schema_alias_threads_through():
    """The schema_ field uses alias='schema' to avoid Python keyword collision."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"results": []})
        # By alias 'schema'
        params = OpenSanctionsSearchParams.model_validate(
            {"query": "x", "schema": "Person"},
        )
        fetch_opensanctions_search(params)
        assert mock_get.call_args[1]["params"]["schema"] == "Person"


@pytest.mark.anyio
async def test_handle_search_accepts_schema_alias_from_mcp_args():
    """LLM-supplied arguments will use the alias 'schema'; the handler must accept it.

    Previously the handler used OpenSanctionsSearchParams(**arguments) which
    would reject {'schema': 'Person'} because 'schema' is not a valid Python
    keyword arg even with populate_by_name. The fix uses model_validate.
    """
    from meta_data_mcp.providers.global_opensanctions import handle_opensanctions_search

    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"results": []})
        # Simulate an MCP client passing the alias name as the LLM saw it.
        result = await handle_opensanctions_search(
            {"query": "putin", "schema": "Person", "countries": "ru"},
        )
        # Handler now adapts the response to the entity-graph payload —
        # the test's goal is to assert the wire received the right params
        # under the 'schema' alias, NOT to lock the response shape.
        assert "nodes" in result[0].text
        assert mock_get.call_args[1]["params"]["schema"] == "Person"
        assert mock_get.call_args[1]["params"]["countries"] == "ru"


def test_search_countries_and_topics():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"results": []})
        fetch_opensanctions_search(
            OpenSanctionsSearchParams(
                query="x",
                countries="ru,by",
                topics="sanction,role.pep",
            ),
        )
        sent = mock_get.call_args[1]["params"]
        assert sent["countries"] == "ru,by"
        assert sent["topics"] == "sanction,role.pep"


def test_search_validates_query_and_limit():
    with pytest.raises(Exception):
        OpenSanctionsSearchParams(query="")
    with pytest.raises(Exception):
        OpenSanctionsSearchParams(query="x", limit=0)
    with pytest.raises(Exception):
        OpenSanctionsSearchParams(query="x", limit=101)


def test_get_entity_path_param():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"id": "NK-abc", "schema": "Person"})
        result = fetch_opensanctions_get_entity(
            OpenSanctionsGetEntityParams(entity_id="NK-abc"),
        )
        assert result["id"] == "NK-abc"
        assert "/entities/NK-abc" in mock_get.call_args[0][0]


def test_get_entity_rejects_empty_id():
    with pytest.raises(Exception):
        OpenSanctionsGetEntityParams(entity_id="")


def test_auth_header_sent_when_env_set(monkeypatch):
    monkeypatch.setenv("OPENSANCTIONS_API_KEY", "the-key")
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"results": []})
        fetch_opensanctions_search(OpenSanctionsSearchParams(query="x"))
        sent = mock_get.call_args[1]["headers"]
        assert sent["Authorization"] == "ApiKey the-key"


@pytest.mark.anyio
async def test_handle_search_translates_404():
    from meta_data_mcp.errors import NotFoundError

    req = httpx.Request("GET", "https://api.opensanctions.org/search/missing")
    resp = httpx.Response(status_code=404, request=req)
    status_err = httpx.HTTPStatusError("nope", request=req, response=resp)

    with patch("httpx.get") as mock_get:
        mock_get.return_value.raise_for_status = Mock(side_effect=status_err)
        mock_get.return_value.status_code = 404
        mock_get.return_value.headers = {}
        with pytest.raises(NotFoundError) as exc_info:
            await handle_opensanctions_search(
                {"query": "x", "dataset": "no_such_dataset"},
            )
        assert exc_info.value.provider == "global-opensanctions"


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps entity-graph adapter + binding.
# ---------------------------------------------------------------------------


def test_opensanctions_entity_graph_adapter_top_level_results():
    """Each search result becomes a node; the schema drives node type
    (Person → ``author`` slot for color contrast against ``entity``).
    """
    raw = {
        "results": [
            {
                "id": "NK-1",
                "caption": "Vladimir Putin",
                "schema": "Person",
                "topics": ["sanction"],
                "countries": ["ru"],
            },
            {
                "id": "NK-2",
                "caption": "ACME Co",
                "schema": "Company",
            },
        ],
    }
    payload = _opensanctions_search_to_entity_graph_payload(raw)
    node_ids = {n["id"] for n in payload["nodes"]}
    assert "NK-1" in node_ids and "NK-2" in node_ids
    person = next(n for n in payload["nodes"] if n["id"] == "NK-1")
    company = next(n for n in payload["nodes"] if n["id"] == "NK-2")
    assert person["type"] == "author"  # Person maps to author palette slot.
    assert company["type"] == "entity"
    # Topics are preserved on attrs for the side-panel.
    assert person["attrs"]["topics"] == ["sanction"]


def test_opensanctions_entity_graph_adapter_emits_property_edges():
    """Nested entity references in ``properties`` (familyMembers,
    associates, directorOf, …) become edges back to the parent. That's
    what gives the bundle its relationship surface.
    """
    raw = {
        "results": [
            {
                "id": "NK-1",
                "caption": "Person A",
                "schema": "Person",
                "properties": {
                    "familyMembers": [
                        {"id": "NK-2", "caption": "Person B", "schema": "Person"},
                    ],
                    "directorOf": [
                        {"id": "NK-3", "caption": "Some Co", "schema": "Company"},
                    ],
                    # Scalar property values (dates, strings) should be skipped.
                    "birthDate": ["1952-10-07"],
                },
            },
        ],
    }
    payload = _opensanctions_search_to_entity_graph_payload(raw)
    edge_pairs = {(e["source"], e["target"], e["label"]) for e in payload["edges"]}
    assert ("NK-1", "NK-2", "familyMembers") in edge_pairs
    assert ("NK-1", "NK-3", "directorOf") in edge_pairs
    # Scalar properties must not create stray nodes.
    node_ids = {n["id"] for n in payload["nodes"]}
    assert node_ids == {"NK-1", "NK-2", "NK-3"}


def test_opensanctions_entity_graph_adapter_handles_empty():
    """No results → empty graph, no exceptions."""
    assert _opensanctions_search_to_entity_graph_payload({})["nodes"] == []
    assert _opensanctions_search_to_entity_graph_payload({"results": []})["edges"] == []


def test_opensanctions_search_tool_binds_to_entity_graph():
    tool = next(t for t in TOOLS if t.name == "opensanctions-search")
    assert tool.meta == {"ui": {"resourceUri": ENTITY_GRAPH_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == ENTITY_GRAPH_URI


@pytest.mark.anyio
async def test_opensanctions_search_returns_entity_graph_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(
            {"results": [{"id": "NK-x", "caption": "X", "schema": "Person"}]},
        )
        result = await handle_opensanctions_search({"query": "x"})
        parsed = json.loads(result[0].text)
        assert "nodes" in parsed and "edges" in parsed
        assert any(n["id"] == "NK-x" for n in parsed["nodes"])
