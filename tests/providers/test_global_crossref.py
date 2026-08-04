import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_crossref import (
    TOOLS,
    _crossref_works_by_author_to_entity_graph_payload,
    _crossref_works_to_shape_payload,
    handle_crossref_funders_search,
    handle_crossref_get_journal,
    handle_crossref_get_work,
    handle_crossref_journals_search,
    handle_crossref_works_by_author,
    handle_crossref_works_by_title,
    handle_crossref_works_search,
)
from meta_data_mcp.ui_resources.app_entity_graph_v1 import URI as ENTITY_GRAPH_URI
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_crossref_works_search_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "message": {
                "total-results": 1,
                "items": [
                    {
                        "DOI": "10.1038/nature12373",
                        "title": ["The structure of something"],
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_crossref_works_search({"query": "structure"})
        assert "10.1038/nature12373" in result[0].text


@pytest.mark.anyio
async def test_crossref_works_search_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Crossref down")

        with pytest.raises(httpx.HTTPError):
            await handle_crossref_works_search({"query": "anything"})


@pytest.mark.anyio
async def test_crossref_get_work_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "message": {
                "DOI": "10.1038/nature12373",
                "title": ["Specific paper"],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_crossref_get_work({"doi": "10.1038/nature12373"})
        assert "Specific paper" in result[0].text


@pytest.mark.anyio
async def test_crossref_get_work_url_encodes_doi():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"status": "ok", "message": {}}
        mock_get.return_value.raise_for_status = Mock()

        await handle_crossref_get_work({"doi": "10.1038/nature12373"})

        called_url = mock_get.call_args[0][0]
        # Slash in DOI must be URL-encoded as %2F
        assert "10.1038%2Fnature12373" in called_url


@pytest.mark.anyio
async def test_crossref_works_by_author_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "message": {
                "total-results": 1,
                "items": [{"DOI": "10.1/abc", "author": [{"family": "Hinton"}]}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_crossref_works_by_author({"author": "Hinton"})
        assert "Hinton" in result[0].text


@pytest.mark.anyio
async def test_crossref_works_by_title_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "message": {
                "total-results": 1,
                "items": [{"title": ["Deep Learning"]}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_crossref_works_by_title({"title": "Deep Learning"})
        assert "Deep Learning" in result[0].text


@pytest.mark.anyio
async def test_crossref_journals_search_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "message": {
                "total-results": 1,
                "items": [{"title": "Nature", "ISSN": ["1476-4687"]}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_crossref_journals_search({"query": "nature"})
        assert "Nature" in result[0].text


@pytest.mark.anyio
async def test_crossref_get_journal_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "message": {"title": "Nature", "ISSN": ["1476-4687"]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_crossref_get_journal({"issn": "1476-4687"})
        assert "Nature" in result[0].text


@pytest.mark.anyio
async def test_crossref_funders_search_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "message": {
                "total-results": 1,
                "items": [{"name": "National Science Foundation"}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_crossref_funders_search({"query": "science"})
        assert "National Science Foundation" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for crossref-works-search.
# ---------------------------------------------------------------------------


def test_crossref_adapter_flattens_message_items_to_rows():
    raw = {
        "status": "ok",
        "message": {
            "total-results": 1,
            "items": [
                {
                    "DOI": "10.1038/nature12373",
                    "title": ["The structure of something"],
                    "author": [
                        {"given": "Alice", "family": "Smith"},
                        {"given": "Bob", "family": "Jones"},
                    ],
                    "container-title": ["Nature"],
                    "publisher": "Springer",
                    "type": "journal-article",
                    "published-print": {"date-parts": [[2023, 5, 1]]},
                    "is-referenced-by-count": 42,
                },
            ],
        },
    }
    payload = _crossref_works_to_shape_payload(raw)
    assert payload["total_results"] == 1
    row = payload["rows"][0]
    assert row["DOI"] == "10.1038/nature12373"
    assert row["title"] == "The structure of something"
    assert "Alice Smith" in row["authors"] and "Bob Jones" in row["authors"]
    assert row["container_title"] == "Nature"
    assert row["type"] == "journal-article"
    assert row["is_referenced_by_count"] == 42
    assert payload["default_facets"] == ["type", "publisher", "container_title"]


def test_crossref_adapter_handles_missing_items():
    assert _crossref_works_to_shape_payload({})["rows"] == []
    assert _crossref_works_to_shape_payload({"message": {}})["rows"] == []


def test_works_search_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "crossref-works-search")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_crossref_works_search_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "status": "ok",
            "message": {
                "total-results": 1,
                "items": [
                    {
                        "DOI": "10.1038/nature12373",
                        "title": ["The structure of something"],
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_crossref_works_search({"query": "structure"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["DOI"] == "10.1038/nature12373"
        assert "schema" in body


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps entity-graph adapter + binding (works-by-author).
# Note: crossref-works-search remains on the records primitive — different
# tool, different shape. We pin BOTH bindings here so a refactor that
# accidentally rebinds works-search to entity-graph blows up loudly.
# ---------------------------------------------------------------------------


def test_crossref_entity_graph_adapter_flattens_works_and_authors():
    """Co-author overlay: each work in the response is a ``work`` node;
    each author is an ``author`` node; an ``authored`` edge connects
    each work to each of its authors. Authors dedupe across works.
    """
    raw = {
        "message": {
            "items": [
                {
                    "DOI": "10.1/abc",
                    "title": ["Paper A"],
                    "container-title": ["Nature"],
                    "publisher": "Springer",
                    "type": "journal-article",
                    "author": [
                        {"given": "Alice", "family": "Smith"},
                        {"given": "Bob", "family": "Jones"},
                    ],
                },
                {
                    "DOI": "10.2/def",
                    "title": ["Paper B"],
                    "author": [
                        {"given": "Alice", "family": "Smith"},
                    ],
                },
            ],
        },
    }
    payload = _crossref_works_by_author_to_entity_graph_payload(raw, "alice")
    work_nodes = [n for n in payload["nodes"] if n["type"] == "work"]
    author_nodes = [n for n in payload["nodes"] if n["type"] == "author"]
    assert {n["id"] for n in work_nodes} == {"doi:10.1/abc", "doi:10.2/def"}
    # Alice appears twice but should dedupe to one node.
    alice_nodes = [n for n in author_nodes if n["label"] == "Alice Smith"]
    assert len(alice_nodes) == 1, "Alice must dedupe across works"
    # Two authored edges should fan out from doi:10.1/abc.
    edges_from_a = [e for e in payload["edges"] if e["source"] == "doi:10.1/abc"]
    assert len(edges_from_a) == 2
    # Alice's edge weight should reflect 2 shared works.
    alice_id = alice_nodes[0]["id"]
    alice_edges = [e for e in payload["edges"] if e["target"] == alice_id]
    assert all(e["weight"] == 2 for e in alice_edges)


def test_crossref_entity_graph_adapter_orcid_id_preferred():
    """Where ORCID is available we use it as the stable node id; only
    fall back to a name-derived id when ORCID is missing.
    """
    raw = {
        "message": {
            "items": [
                {
                    "DOI": "10.1/a",
                    "title": ["P"],
                    "author": [
                        {
                            "given": "Z",
                            "family": "Q",
                            "ORCID": "https://orcid.org/0000-0001",
                        },
                    ],
                },
            ],
        },
    }
    payload = _crossref_works_by_author_to_entity_graph_payload(raw, "z")
    author_node = next(n for n in payload["nodes"] if n["type"] == "author")
    assert author_node["id"].startswith("orcid:")


def test_crossref_entity_graph_adapter_handles_empty_items():
    """No items → empty graph, no exceptions."""
    assert _crossref_works_by_author_to_entity_graph_payload({}, "x")["nodes"] == []
    assert (
        _crossref_works_by_author_to_entity_graph_payload(
            {"message": {"items": []}},
            "x",
        )["edges"]
        == []
    )


def test_works_search_stays_bound_to_records_after_entity_graph_landed():
    """Regression guard: ``crossref-works-search`` MUST remain on the
    records primitive. Phase 4 bound it there; Phase 5's entity-graph
    binding is on a DIFFERENT tool (``works-by-author``). If a refactor
    accidentally cross-wires them this fires.
    """
    tool = next(t for t in TOOLS if t.name == "crossref-works-search")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}


def test_crossref_works_by_author_tool_binds_to_entity_graph():
    tool = next(t for t in TOOLS if t.name == "crossref-works-by-author")
    assert tool.meta == {"ui": {"resourceUri": ENTITY_GRAPH_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == ENTITY_GRAPH_URI


@pytest.mark.anyio
async def test_crossref_works_by_author_returns_entity_graph_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "message": {
                "items": [
                    {
                        "DOI": "10.1/x",
                        "title": ["A paper"],
                        "author": [
                            {"given": "X", "family": "Y"},
                        ],
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_crossref_works_by_author({"author": "Y"})
        parsed = json.loads(result[0].text)
        assert "nodes" in parsed and "edges" in parsed
        assert any(n["type"] == "work" for n in parsed["nodes"])
        assert any(n["type"] == "author" for n in parsed["nodes"])
