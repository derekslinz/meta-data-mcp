from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.global_rcsb_pdb import (
    TOOLS,
    handle_pdb_entry,
    handle_pdb_polymer,
)
from meta_data_mcp.ui_resources.app_molecular_v1 import URI as MOLECULAR_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_pdb_entry_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "rcsb_id": "4HHB",
            "struct": {"title": "Hemoglobin"},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_pdb_entry({"entry_id": "4HHB"})
        assert len(result) == 1
        assert "4HHB" in result[0].text
        assert "Hemoglobin" in result[0].text


@pytest.mark.anyio
async def test_pdb_polymer_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "rcsb_id": "4HHB_1",
            "rcsb_polymer_entity": {"details": "Alpha chain"},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_pdb_polymer({"entry_id": "4HHB", "entity_id": "1"})
        assert len(result) == 1
        assert "Alpha chain" in result[0].text


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps molecular app binding.
# ---------------------------------------------------------------------------


def test_pdb_entry_tool_binds_to_molecular_app():
    """``pdb-entry`` returns entry metadata (title, resolution, method);
    the molecular app derives a files.rcsb.org/download/<ID>.pdb URL
    from it for the actual atoms. Pin both the Python-side ``.meta``
    attribute AND the wire-level alias (``model_dump(by_alias=True)``
    emits ``_meta``) so a future SDK regression on the populate_by_name
    footgun is caught here.
    """
    tool = next(t for t in TOOLS if t.name == "pdb-entry")
    assert tool.meta == {"ui": {"resourceUri": MOLECULAR_URI}}, (
        f"pdb-entry is not bound to {MOLECULAR_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == MOLECULAR_URI


def test_pdb_polymer_tool_not_bound_to_molecular_app():
    """``pdb-polymer-entity`` is intentionally NOT bound to the
    molecular app: it returns metadata for one chain inside an entry,
    with no separable structure URL — the viewer would have nothing to
    render that the parent entry doesn't already provide. If a future
    refactor binds it, document why on this assertion.
    """
    tool = next(t for t in TOOLS if t.name == "pdb-polymer-entity")
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert "_meta" not in wire, (
        "pdb-polymer-entity gained a _meta binding — polymer entities "
        "have no separable structure URL; either point at a different "
        "app or delete this assertion with rationale."
    )
