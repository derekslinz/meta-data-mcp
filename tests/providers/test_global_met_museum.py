from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_met_museum import (
    TOOLS,
    handle_met_get_object,
    handle_met_list_departments,
    handle_met_list_objects,
    handle_met_search,
    handle_met_search_by_artist,
)
from meta_data_mcp.ui_resources.app_museum_v1 import URI as MUSEUM_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_met_list_objects_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total": 2,
            "objectIDs": [123, 456],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_met_list_objects({"departmentIds": "11"})
        assert "123" in result[0].text
        assert "objectIDs" in result[0].text


@pytest.mark.anyio
async def test_met_list_objects_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Met API down")

        with pytest.raises(httpx.HTTPError):
            await handle_met_list_objects({})


@pytest.mark.anyio
async def test_met_get_object_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "objectID": 436535,
            "title": "Wheat Field with Cypresses",
            "artistDisplayName": "Vincent van Gogh",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_met_get_object({"objectID": 436535})
        assert "Wheat Field with Cypresses" in result[0].text
        assert "Vincent van Gogh" in result[0].text


@pytest.mark.anyio
async def test_met_get_object_requires_id():
    with pytest.raises(ValueError):
        await handle_met_get_object({})


@pytest.mark.anyio
async def test_met_search_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total": 1,
            "objectIDs": [436535],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_met_search(
            {"q": "sunflowers", "hasImages": True, "departmentId": 11},
        )
        assert "436535" in result[0].text


@pytest.mark.anyio
async def test_met_list_departments_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "departments": [
                {"departmentId": 1, "displayName": "American Decorative Arts"},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_met_list_departments({})
        assert "American Decorative Arts" in result[0].text


@pytest.mark.anyio
async def test_met_search_by_artist_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total": 5,
            "objectIDs": [1, 2, 3, 4, 5],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_met_search_by_artist({"q": "Van Gogh"})
        assert "objectIDs" in result[0].text


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps binding (museum app).
# ---------------------------------------------------------------------------


def test_met_search_tool_binds_to_museum_app():
    """met-search renders through the Phase 5 museum app."""
    tool = next(t for t in TOOLS if t.name == "met-search")
    assert tool.meta == {"ui": {"resourceUri": MUSEUM_URI}}, (
        f"met-search is not bound to {MUSEUM_URI}"
    )
    # Wire-level: the alias keyword ``_meta=`` must surface on the model
    # dump under ``_meta`` (not under ``meta``). Without ``by_alias=True``
    # the SDK would silently drop ``_meta`` from the JSON envelope and the
    # host would never preload the museum app — see
    # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == MUSEUM_URI


def test_met_get_object_tool_binds_to_museum_app():
    """met-get-object renders the single-object detail modal in the
    museum app. Pin the binding so a refactor that drops it (and
    thereby breaks single-object hydration from a non-search source)
    surfaces here.
    """
    tool = next(t for t in TOOLS if t.name == "met-get-object")
    assert tool.meta == {"ui": {"resourceUri": MUSEUM_URI}}, (
        f"met-get-object is not bound to {MUSEUM_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == MUSEUM_URI


def test_met_search_by_artist_binds_to_museum_app():
    """``met-search-by-artist`` returns the same ``{total, objectIDs}``
    shape as ``met-search``, so it must bind to the same museum app —
    otherwise the artist-search surface renders as plain text while the
    free-text-search surface renders as an image grid, which is a
    confusing inconsistency.
    """
    tool = next(t for t in TOOLS if t.name == "met-search-by-artist")
    assert tool.meta == {"ui": {"resourceUri": MUSEUM_URI}}, (
        f"met-search-by-artist is not bound to {MUSEUM_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == MUSEUM_URI


def test_unbound_met_tools_have_no_ui_meta():
    """list-objects and list-departments intentionally don't bind to the
    museum app — list-objects returns a raw id list with no filter UX
    and list-departments is a metadata helper, not a viewing surface.
    Pin the absence so a future refactor that flips them accidentally
    surfaces here.
    """
    for name in ("met-list-objects", "met-list-departments"):
        tool = next(t for t in TOOLS if t.name == name)
        assert tool.meta is None, f"{name} has unexpected _meta binding: {tool.meta!r}"
