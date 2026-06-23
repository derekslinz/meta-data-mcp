"""Tests for the lazy-load + activate/deactivate mechanism in meta_data_mcp.

These tests reach into the meta_data_mcp module's globals (_active_providers,
_owner_by_tool, TOOLS, TOOLS_HANDLERS). To avoid cross-test pollution, every
test resets that state in a fixture before running.
"""

import json
from typing import Any

import pytest

import meta_data_mcp.providers.meta_data_mcp as srv


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolate_meta_state():
    """Snapshot and restore the meta server's mutable activation state.

    A single ``_state.snapshot()`` / ``restore()`` round-trip captures every
    mutable field — tools, handlers, owner map, active set, and the running
    Server reference. Adding a new field to ``ActivationState`` does not
    require updating this fixture.
    """
    snap = srv._state.snapshot()
    yield
    srv._state.restore(snap)


def _payload(result) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


# ---------------------------------------------------------------------------
# Default-state expectations
# ---------------------------------------------------------------------------


def test_meta_module_advertises_only_meta_tools_by_default():
    """At import time, only meta tools are in TOOLS / TOOLS_HANDLERS."""
    # The meta tools — anything we explicitly register in this module.
    tool_names = {t.name for t in srv.TOOLS}
    # Sanity: the meta tools we expect should all be present.
    expected_meta = {
        "opendata_providers_find",
        "opendata_plugins_create",
        "opendata_plugins_draft",
        "opendata_explain_choice",
        "opendata_domains_list",
        "opendata_regions_list",
        "opendata_providers_describe",
        "opendata_providers_list",
        "opendata_providers_activate",
        "opendata_providers_deactivate",
        "opendata_providers_list_active",
    }
    assert expected_meta.issubset(tool_names)
    # None of the data-plugin tools should be in the catalog at import time.
    assert "openaq-list-locations" not in tool_names
    assert "us-datagov-list-datasets" not in tool_names


# ---------------------------------------------------------------------------
# Activate / deactivate
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_activate_provider_adds_tools_and_marks_active():
    before = len(srv.TOOLS)
    r = await srv.handle_activate_provider({"provider_id": "global-openaq"})
    payload = _payload(r)
    assert payload["status"] == "activated"
    assert payload["provider_id"] == "global_openaq"
    assert payload["tools_added"] >= 1
    assert "global_openaq" in srv._active_providers
    assert len(srv.TOOLS) > before


@pytest.mark.anyio
async def test_activate_provider_idempotent():
    await srv.handle_activate_provider({"provider_id": "global-openaq"})
    r = await srv.handle_activate_provider({"provider_id": "global-openaq"})
    assert _payload(r)["status"] == "already_active"


@pytest.mark.anyio
async def test_activate_unknown_provider_returns_error():
    r = await srv.handle_activate_provider({"provider_id": "does-not-exist"})
    payload = _payload(r)
    assert payload["status"] == "error"
    assert "unknown provider id" in payload["error"]


@pytest.mark.anyio
async def test_activate_meta_provider_id_rejected():
    """Meta provider id should not be activatable as a plugin."""
    r = await srv.handle_activate_provider({"provider_id": "meta_data_mcp"})
    assert _payload(r)["status"] == "error"


@pytest.mark.anyio
async def test_deactivate_removes_tools_and_clears_active():
    await srv.handle_activate_provider({"provider_id": "global-openaq"})
    tools_after_activate = len(srv.TOOLS)
    r = await srv.handle_deactivate_provider({"provider_id": "global_openaq"})
    payload = _payload(r)
    assert payload["status"] == "deactivated"
    assert payload["tools_removed"] >= 1
    assert "global_openaq" not in srv._active_providers
    assert len(srv.TOOLS) < tools_after_activate
    # The deactivated tools must not be left in TOOLS_HANDLERS either.
    for name in payload["tools"]:
        assert name not in srv.TOOLS_HANDLERS


@pytest.mark.anyio
async def test_deactivate_unknown_provider_returns_not_active():
    r = await srv.handle_deactivate_provider({"provider_id": "global-openaq"})
    assert _payload(r)["status"] == "not_active"


@pytest.mark.anyio
async def test_id_accepts_both_underscore_and_hyphen_forms():
    r1 = await srv.handle_activate_provider({"provider_id": "global_openaq"})
    assert _payload(r1)["status"] == "activated"
    r2 = await srv.handle_deactivate_provider({"provider_id": "global-openaq"})
    assert _payload(r2)["status"] == "deactivated"


# ---------------------------------------------------------------------------
# list-active-providers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_active_empty_when_nothing_activated():
    r = await srv.handle_list_active_providers({})
    payload = _payload(r)
    assert payload["count"] == 0
    assert payload["active_providers"] == []
    assert payload["plugin_tool_count"] == 0
    assert payload["meta_tool_count"] >= 8


@pytest.mark.anyio
async def test_list_active_after_activation():
    await srv.handle_activate_provider({"provider_id": "global-openaq"})
    await srv.handle_activate_provider({"provider_id": "us_data_gov"})
    r = await srv.handle_list_active_providers({})
    payload = _payload(r)
    assert payload["count"] == 2
    assert set(payload["active_providers"]) == {"global_openaq", "us_data_gov"}
    assert "openaq-list-locations" in payload["tools_per_provider"]["global_openaq"]
    assert "us-datagov-list-datasets" in payload["tools_per_provider"]["us_data_gov"]


# ---------------------------------------------------------------------------
# find-providers: activate_top opt-in
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_find_providers_default_is_read_only():
    """With activate_top unset (default 0), find-providers must not load tools."""
    tools_before = len(srv.TOOLS)
    active_before = set(srv._active_providers)

    r = await srv.handle_find_providers({"query": "air quality", "limit": 3})
    payload = _payload(r)

    assert "auto_activated" not in payload
    assert "activate" in payload.get("next_step", "")
    assert len(srv.TOOLS) == tools_before
    assert srv._active_providers == active_before


@pytest.mark.anyio
async def test_find_providers_activate_top_loads_tools():
    """activate_top=N must load the top N providers' tools in the same turn."""
    tools_before = len(srv.TOOLS)
    r = await srv.handle_find_providers(
        {"query": "air quality", "activate_top": 2, "limit": 3}
    )
    payload = _payload(r)

    assert "auto_activated" in payload
    assert len(payload["auto_activated"]) == 2
    statuses = {a["status"] for a in payload["auto_activated"]}
    assert statuses.issubset({"activated", "already_active"})
    assert len(srv.TOOLS) > tools_before
    assert len(srv._active_providers) >= 1


@pytest.mark.anyio
async def test_find_providers_no_match_keeps_create_plugin_hint():
    """Empty result + non-empty query still surfaces the autonomous-create flow."""
    r = await srv.handle_find_providers({"query": "zzzzz_definitely_no_provider_zzzzz"})
    payload = _payload(r)
    assert payload["count"] == 0
    assert payload.get("no_match") is True
    assert "opendata_plugins_create" in payload.get("next_step", "")


# ---------------------------------------------------------------------------
# META_DATA_MCP_PRELOAD env var
# ---------------------------------------------------------------------------


def test_load_all_plugins_default_is_empty(monkeypatch):
    """No env var ⇒ no preloading."""
    monkeypatch.delenv("META_DATA_MCP_PRELOAD", raising=False)
    # Reset state in-place; can't easily re-import the module without side
    # effects on other tests, so we just exercise the helper directly.
    srv._active_providers.clear()
    # Confirm the helper short-circuits.
    loaded, added = srv._load_all_plugins()
    assert loaded == 0
    assert added == 0


def test_load_all_plugins_preload_specific(monkeypatch):
    """Comma-separated env var preloads only the named providers."""
    monkeypatch.setenv(
        "META_DATA_MCP_PRELOAD", "global-openaq, us_data_gov, no-such-thing"
    )
    # Need to clear state first because we're sharing module-level state.
    srv._active_providers.clear()
    srv._owner_by_tool.clear()
    # Remove any plugin tools left from previous tests. ``_load_all_plugins``
    # doesn't read ``TOOLS_HANDLERS`` for the meta tools it leaves behind —
    # the isolate fixture restores the full handler dict after the test —
    # so we only need to reset the plugin-side state above.
    srv.TOOLS[:] = [t for t in srv.TOOLS if t.name.startswith("opendata-")]
    srv.TOOLS_HANDLERS.clear()

    loaded, added = srv._load_all_plugins()
    assert loaded == 2  # two valid ids preloaded
    assert added >= 2
    assert "global_openaq" in srv._active_providers
    assert "us_data_gov" in srv._active_providers
    # Unknown id ("no-such-thing") must be skipped, not silently mis-routed.
    assert "no-such-thing" not in srv._active_providers
    assert "no_such_thing" not in srv._active_providers
