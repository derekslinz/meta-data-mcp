"""Tests for the unified meta-data-mcp server runtime.

The discovery layer and the plugin aggregation layer now live in the SAME
module (``meta_data_mcp.providers.meta_data_mcp``). This file exercises
the plugin-merge logic that used to live in the deleted
``meta_data_mcp_all`` module.
"""

from __future__ import annotations

import importlib
import types as pytypes

import pytest
from mcp import types


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_fake_plugin_module(name: str, tool_name: str) -> pytypes.ModuleType:
    """Construct a synthetic plugin module exposing one tool."""
    module = pytypes.ModuleType(name)

    async def handler(arguments):  # pragma: no cover - signature only
        return []

    module.TOOLS = [
        types.Tool(
            name=tool_name,
            description=f"tool from {name}",
            input_schema={"type": "object", "properties": {}},
        ),
    ]
    module.TOOLS_HANDLERS = {tool_name: handler}
    module.RESOURCES = []
    module.RESOURCES_HANDLERS = {}
    return module


def test_merge_plugin_detects_collision(caplog):
    """When two plugins register the same tool name, only the first wins."""
    from meta_data_mcp.providers.meta_data_mcp import _merge_plugin

    mod_a = _make_fake_plugin_module("fake_a", "shared-tool")
    mod_b = _make_fake_plugin_module("fake_b", "shared-tool")

    # Save and restore the global TOOLS/HANDLERS so the test doesn't
    # contaminate other tests.
    from meta_data_mcp.providers import meta_data_mcp as srv

    saved_tools = list(srv.TOOLS)
    saved_handlers = dict(srv.TOOLS_HANDLERS)
    srv.TOOLS.clear()
    srv.TOOLS_HANDLERS.clear()
    try:
        owner_by_tool: dict[str, str] = {}
        added_a = _merge_plugin(mod_a, "fake_a", owner_by_tool)
        with caplog.at_level("WARNING"):
            added_b = _merge_plugin(mod_b, "fake_b", owner_by_tool)

        assert added_a == 1
        assert added_b == 0
        assert [t.name for t in srv.TOOLS] == ["shared-tool"]
        assert owner_by_tool == {"shared-tool": "fake_a"}
        assert srv.TOOLS_HANDLERS["shared-tool"] is mod_a.TOOLS_HANDLERS["shared-tool"]
        collision_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "shared-tool" in msg and "fake_a" in msg and "fake_b" in msg
            for msg in collision_msgs
        )
    finally:
        srv.TOOLS[:] = saved_tools
        srv.TOOLS_HANDLERS.clear()
        srv.TOOLS_HANDLERS.update(saved_handlers)


def test_merge_plugin_distinct_tools_both_kept():
    from meta_data_mcp.providers import meta_data_mcp as srv
    from meta_data_mcp.providers.meta_data_mcp import _merge_plugin

    saved_tools = list(srv.TOOLS)
    saved_handlers = dict(srv.TOOLS_HANDLERS)
    srv.TOOLS.clear()
    srv.TOOLS_HANDLERS.clear()
    try:
        mod_a = _make_fake_plugin_module("fake_a", "tool-a")
        mod_b = _make_fake_plugin_module("fake_b", "tool-b")

        owner_by_tool: dict[str, str] = {}
        _merge_plugin(mod_a, "fake_a", owner_by_tool)
        _merge_plugin(mod_b, "fake_b", owner_by_tool)

        assert {t.name for t in srv.TOOLS} == {"tool-a", "tool-b"}
        assert owner_by_tool == {"tool-a": "fake_a", "tool-b": "fake_b"}
    finally:
        srv.TOOLS[:] = saved_tools
        srv.TOOLS_HANDLERS.clear()
        srv.TOOLS_HANDLERS.update(saved_handlers)


def test_legacy_aggregator_module_is_gone():
    """The old ``meta_data_mcp_all`` aggregator must not exist anymore.

    The unified architecture has one server. A separate aggregator module
    is a footgun — keep this regression in place so it can't sneak back.
    """
    with pytest.raises(ImportError):
        importlib.import_module("meta_data_mcp.providers.meta_data_mcp_all")
