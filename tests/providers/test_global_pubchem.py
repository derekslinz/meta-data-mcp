from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.global_pubchem import (
    TOOLS,
    handle_pubchem_compound,
    handle_pubchem_substance,
)
from meta_data_mcp.ui_resources.app_molecular_v1 import URI as MOLECULAR_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_pubchem_compound_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "PC_Compounds": [{"id": {"id": {"cid": 241}}}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_pubchem_compound(
            {"identifier": "aspirin", "namespace": "name"},
        )
        assert len(result) == 1
        assert "241" in result[0].text


@pytest.mark.anyio
async def test_pubchem_substance_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"PC_Substances": [{"sid": 12345}]}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_pubchem_substance({"sid": 12345})
        assert len(result) == 1
        assert "12345" in result[0].text


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps molecular app binding.
# ---------------------------------------------------------------------------


def test_pubchem_compound_tool_binds_to_molecular_app():
    """``pubchem-compound`` returns CID + properties; the molecular
    app derives a /cid/<CID>/SDF?record_type=3d URL from it for the
    actual 3D coordinates. Pin both the Python-side ``.meta`` attribute
    AND the wire-level alias (``model_dump(by_alias=True)`` emits
    ``_meta``) so a future SDK regression on the populate_by_name
    footgun is caught here.
    """
    tool = next(t for t in TOOLS if t.name == "pubchem-compound")
    assert tool.meta == {"ui": {"resourceUri": MOLECULAR_URI}}, (
        f"pubchem-compound is not bound to {MOLECULAR_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == MOLECULAR_URI


def test_pubchem_substance_tool_not_bound_to_molecular_app():
    """``pubchem-substance`` is intentionally NOT bound to the molecular
    app: substances are depositor-supplied records that often lack 3D
    coordinates, so the viewer would render an empty canvas. If a
    future refactor binds it, document why on this assertion.
    """
    tool = next(t for t in TOOLS if t.name == "pubchem-substance")
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert "_meta" not in wire, (
        "pubchem-substance gained a _meta binding — substances rarely "
        "carry 3D coordinates; either point at a different app or "
        "delete this assertion with rationale."
    )
