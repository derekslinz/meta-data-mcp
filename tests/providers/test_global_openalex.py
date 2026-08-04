import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_openalex import (
    TOOLS,
    _openalex_works_to_entity_graph_payload,
    handle_openalex_get_author,
    handle_openalex_get_work,
    handle_openalex_search_authors,
    handle_openalex_search_concepts,
    handle_openalex_search_institutions,
    handle_openalex_search_publishers,
    handle_openalex_search_sources,
    handle_openalex_search_works,
)
from meta_data_mcp.ui_resources.app_entity_graph_v1 import URI as ENTITY_GRAPH_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_openalex_search_works_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "meta": {"count": 1},
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "title": "Attention Is All You Need",
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openalex_search_works({"search": "attention"})
        assert "Attention Is All You Need" in result[0].text


@pytest.mark.anyio
async def test_openalex_search_works_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("OpenAlex down")

        with pytest.raises(httpx.HTTPError):
            await handle_openalex_search_works({"search": "x"})


@pytest.mark.anyio
async def test_openalex_search_works_adds_mailto():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"meta": {}, "results": []}
        mock_get.return_value.raise_for_status = Mock()

        await handle_openalex_search_works({"search": "x"})

        params = mock_get.call_args.kwargs.get("params", {})
        assert "mailto" in params
        assert params["mailto"]  # non-empty


@pytest.mark.anyio
async def test_openalex_search_works_respects_contact_env(monkeypatch):
    monkeypatch.setenv("META_DATA_MCP_CONTACT", "test@example.com")
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"meta": {}, "results": []}
        mock_get.return_value.raise_for_status = Mock()

        await handle_openalex_search_works({"search": "x"})

        params = mock_get.call_args.kwargs.get("params", {})
        assert params.get("mailto") == "test@example.com"


@pytest.mark.anyio
async def test_openalex_get_work_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "id": "https://openalex.org/W123",
            "doi": "https://doi.org/10.1/abc",
            "title": "Specific work",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openalex_get_work({"id": "W123"})
        assert "Specific work" in result[0].text


@pytest.mark.anyio
async def test_openalex_get_work_requires_id():
    with pytest.raises(ValueError):
        await handle_openalex_get_work({})


@pytest.mark.anyio
async def test_openalex_search_authors_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "meta": {"count": 1},
            "results": [
                {"id": "https://openalex.org/A1", "display_name": "Geoffrey Hinton"},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openalex_search_authors({"search": "hinton"})
        assert "Geoffrey Hinton" in result[0].text


@pytest.mark.anyio
async def test_openalex_get_author_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "id": "https://openalex.org/A1",
            "display_name": "Geoffrey Hinton",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openalex_get_author({"id": "A1"})
        assert "Geoffrey Hinton" in result[0].text


@pytest.mark.anyio
async def test_openalex_search_institutions_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "meta": {"count": 1},
            "results": [{"display_name": "University of Toronto"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openalex_search_institutions({"search": "toronto"})
        assert "University of Toronto" in result[0].text


@pytest.mark.anyio
async def test_openalex_search_sources_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "meta": {"count": 1},
            "results": [{"display_name": "Nature"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openalex_search_sources({"search": "nature"})
        assert "Nature" in result[0].text


@pytest.mark.anyio
async def test_openalex_search_concepts_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "meta": {"count": 1},
            "results": [{"display_name": "Machine learning"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openalex_search_concepts({"search": "machine learning"})
        assert "Machine learning" in result[0].text


@pytest.mark.anyio
async def test_openalex_search_publishers_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "meta": {"count": 1},
            "results": [{"display_name": "Springer Nature"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_openalex_search_publishers({"search": "springer"})
        assert "Springer Nature" in result[0].text


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps entity-graph adapter + binding.
# ---------------------------------------------------------------------------


def test_openalex_entity_graph_adapter_flattens_works_authors_concepts():
    """The adapter turns OpenAlex's nested ``authorships`` + ``concepts``
    arrays into flat node/edge lists; works→authors are ``authored``,
    works→concepts are ``about``.
    """
    raw = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "title": "Attention Is All You Need",
                "publication_year": 2017,
                "cited_by_count": 100000,
                "authorships": [
                    {
                        "author": {
                            "id": "https://openalex.org/A1",
                            "display_name": "Ashish Vaswani",
                        },
                        "institutions": [{"display_name": "Google"}],
                    },
                    {
                        "author": {
                            "id": "https://openalex.org/A2",
                            "display_name": "Noam Shazeer",
                        },
                    },
                ],
                "concepts": [
                    {
                        "id": "https://openalex.org/C1",
                        "display_name": "Transformer",
                        "level": 3,
                        "score": 0.9,
                    },
                ],
            },
        ],
    }
    payload = _openalex_works_to_entity_graph_payload(raw)
    node_ids = {n["id"] for n in payload["nodes"]}
    assert "https://openalex.org/W1" in node_ids
    assert "https://openalex.org/A1" in node_ids
    assert "https://openalex.org/A2" in node_ids
    assert "https://openalex.org/C1" in node_ids
    edge_pairs = {(e["source"], e["target"], e["label"]) for e in payload["edges"]}
    assert (
        "https://openalex.org/W1",
        "https://openalex.org/A1",
        "authored",
    ) in edge_pairs
    assert (
        "https://openalex.org/W1",
        "https://openalex.org/C1",
        "about",
    ) in edge_pairs


def test_openalex_entity_graph_adapter_dedupes_authors_across_works():
    """Two works sharing an author surface a SINGLE author node with
    two ``authored`` edges — that's what makes co-author clusters
    visible in the force layout.
    """
    raw = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "title": "First paper",
                "authorships": [
                    {"author": {"id": "https://openalex.org/A1", "display_name": "X"}},
                ],
            },
            {
                "id": "https://openalex.org/W2",
                "title": "Second paper",
                "authorships": [
                    {"author": {"id": "https://openalex.org/A1", "display_name": "X"}},
                ],
            },
        ],
    }
    payload = _openalex_works_to_entity_graph_payload(raw)
    author_nodes = [n for n in payload["nodes"] if n["type"] == "author"]
    assert len(author_nodes) == 1, "author should dedupe across works"
    authored_edges = [e for e in payload["edges"] if e["label"] == "authored"]
    assert len(authored_edges) == 2
    # Weight should reflect the shared-works count.
    assert all(e["weight"] == 2 for e in authored_edges)


def test_openalex_entity_graph_adapter_handles_empty_results():
    """No ``results`` key → empty graph, no exceptions."""
    assert _openalex_works_to_entity_graph_payload({})["nodes"] == []
    assert _openalex_works_to_entity_graph_payload({"results": []})["edges"] == []


def test_openalex_search_works_tool_binds_to_entity_graph():
    """Pin both the Python-side ``.meta`` attribute AND the wire-level
    alias (``model_dump(by_alias=True)`` emits ``_meta``) so a future
    SDK regression on the populate_by_name footgun is caught here.
    """
    tool = next(t for t in TOOLS if t.name == "openalex-search-works")
    assert tool.meta == {"ui": {"resourceUri": ENTITY_GRAPH_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == ENTITY_GRAPH_URI


@pytest.mark.anyio
async def test_openalex_search_works_returns_entity_graph_payload():
    """The handler must now return the entity-graph shape, not the
    raw OpenAlex response — that's what makes the bound bundle's
    payload contract honest end-to-end.
    """
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [
                {
                    "id": "https://openalex.org/W1",
                    "title": "A paper",
                    "authorships": [
                        {
                            "author": {
                                "id": "https://openalex.org/A1",
                                "display_name": "Author",
                            },
                        },
                    ],
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_openalex_search_works({"search": "x"})
        parsed = json.loads(result[0].text)
        assert "nodes" in parsed and "edges" in parsed
        assert any(n["type"] == "work" for n in parsed["nodes"])
        assert any(n["type"] == "author" for n in parsed["nodes"])
