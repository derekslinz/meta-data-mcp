"""Plugin loading + activation primitives for the meta server.

The meta server starts in *discovery-only* mode (just the ~13 meta
tools defined in :mod:`meta_data_mcp.providers.meta_data_mcp`). Data
plugins are activated on demand — either by the user calling
``opendata.providers.activate`` or by the ``META_DATA_MCP_PRELOAD``
environment variable at startup.

This module owns the activation plumbing:

- :func:`_merge_plugin` — given a freshly-imported plugin module, copy
  its ``TOOLS`` / ``TOOLS_HANDLERS`` into the live catalog, with
  collision-detection logging.
- :func:`_resolve_provider_id` — accept either canonical underscore
  form (``us_data_gov``) or hyphenated server-name form
  (``us-data-gov``) and return the canonical id.
- :func:`_activate_provider` / :func:`_deactivate_provider` — the
  imperative operations the meta tool handlers wrap.
- :func:`_load_all_plugins` — the startup-time bulk loader driven by
  ``META_DATA_MCP_PRELOAD``.
- :func:`_notify_tools_changed` — best-effort
  ``tools/list_changed`` broadcast after activation mutates the catalog.

Pre-v2.1 these all lived inline at the bottom of the meta server
module. Extracting them into this file is the v2.1 hygiene pass
(architecture review §H2); the meta server re-exports each name so
out-of-tree call sites and the existing test suite keep working
without code changes.

All state lives in :mod:`meta_data_mcp.discovery.state` — this module
holds only the imperative logic that mutates that state.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any

from meta_data_mcp.discovery.state import (
    TOOLS,
    TOOLS_HANDLERS,
    _active_providers,
    _owner_by_tool,
    _state,
)
from meta_data_mcp.registry import REGISTRY

log = logging.getLogger("meta_data_mcp.providers.meta_data_mcp")

# Module names that should never be loaded as data plugins.
_NON_PLUGIN_MODULES: frozenset[str] = frozenset(
    {
        "__template__",
        "meta_data_mcp",  # the meta server itself
        "meta_data_mcp_all",  # legacy aggregator (removed but defensively skipped)
    }
)


def _merge_plugin(
    module: Any,
    plugin_id: str,
    owner_by_tool: dict[str, str] | None = None,
) -> int:
    """Merge a plugin module's TOOLS/TOOLS_HANDLERS into the server.

    ``owner_by_tool`` defaults to the module-level tracker. Callers that
    want collision detection only (without committing to the global
    tracker) can supply their own dict.

    Returns the number of tools actually added (after collision filtering).
    """
    if owner_by_tool is None:
        owner_by_tool = _owner_by_tool

    plugin_tools = getattr(module, "TOOLS", None) or []
    plugin_handlers = getattr(module, "TOOLS_HANDLERS", None) or {}

    added = 0
    for tool in plugin_tools:
        name = tool.name
        if name in owner_by_tool:
            log.warning(
                "Tool name collision: '%s' already registered by '%s'; "
                "skipping duplicate from '%s'.",
                name,
                owner_by_tool[name],
                plugin_id,
            )
            continue
        handler = plugin_handlers.get(name)
        if handler is None:
            log.warning(
                "Plugin '%s' lists tool '%s' but has no handler; skipping.",
                plugin_id,
                name,
            )
            continue
        TOOLS.append(tool)
        TOOLS_HANDLERS[name] = handler
        owner_by_tool[name] = plugin_id
        added += 1
    return added


def _resolve_provider_id(provider_id: str) -> str | None:
    """Find a provider id in the static or dynamic registry.

    Accepts the canonical underscore form (``us_data_gov``) or the
    hyphenated server-name form (``us-data-gov``). Returns the canonical
    id when found, or ``None`` when no match exists.
    """
    needle_underscore = provider_id.replace("-", "_")
    needle_hyphen = provider_id.replace("_", "-")
    for entry in REGISTRY:
        if entry.id in (needle_underscore, provider_id):
            return entry.id
        if entry.server_name in (needle_hyphen, provider_id):
            return entry.id
    return None


async def _notify_tools_changed() -> None:
    """Best-effort: ask the running session to refresh its tool list.

    Silent on any failure — the activation/deactivation already happened
    in our local state; failing to notify just means the client will see
    the new state on its next ``tools/list`` poll.
    """
    if _state.server is None:
        return
    try:
        ctx = _state.server.request_context
        await ctx.session.send_tool_list_changed()
    except Exception as exc:  # noqa: BLE001 — notification is best-effort
        log.debug("tools/list_changed notification skipped: %s", exc)


def _activate_provider(provider_id: str) -> dict[str, Any]:
    """Import a plugin module and merge its tools into the running server.

    Returns a status report dict. Idempotent — repeated activation of an
    already-active provider returns ``status: already_active``.
    """
    canonical = _resolve_provider_id(provider_id)
    if canonical is None:
        return {
            "status": "error",
            "provider_id": provider_id,
            "error": "unknown provider id — not in registry",
        }
    if canonical in _active_providers:
        return {
            "status": "already_active",
            "provider_id": canonical,
            "tools": sorted(
                name for name, owner in _owner_by_tool.items() if owner == canonical
            ),
        }
    if canonical in _NON_PLUGIN_MODULES:
        return {
            "status": "error",
            "provider_id": canonical,
            "error": "this id is not a data plugin",
        }
    try:
        module = importlib.import_module(f"meta_data_mcp.providers.{canonical}")
    except Exception as exc:  # noqa: BLE001 — surface any import error
        return {
            "status": "error",
            "provider_id": canonical,
            "error": f"plugin failed to import: {exc}",
        }
    added = _merge_plugin(module, canonical)
    if added == 0 and not any(o == canonical for o in _owner_by_tool.values()):
        return {
            "status": "error",
            "provider_id": canonical,
            "error": "plugin imported but exposed no usable tools",
        }
    _active_providers.add(canonical)
    return {
        "status": "activated",
        "provider_id": canonical,
        "tools_added": added,
        "tools": sorted(
            name for name, owner in _owner_by_tool.items() if owner == canonical
        ),
    }


def _deactivate_provider(provider_id: str) -> dict[str, Any]:
    """Remove a plugin's tools from the running server's advertised list.

    The Python module remains imported (Python caches modules in
    ``sys.modules``); this only removes the tools from ``TOOLS`` and
    ``TOOLS_HANDLERS`` so they no longer show up in ``tools/list``.
    """
    canonical = _resolve_provider_id(provider_id) or provider_id
    if canonical not in _active_providers:
        return {
            "status": "not_active",
            "provider_id": canonical,
        }
    removed = sorted(
        name for name, owner in list(_owner_by_tool.items()) if owner == canonical
    )
    for name in removed:
        _owner_by_tool.pop(name, None)
        TOOLS_HANDLERS.pop(name, None)
    TOOLS[:] = [t for t in TOOLS if t.name not in set(removed)]
    _active_providers.discard(canonical)
    return {
        "status": "deactivated",
        "provider_id": canonical,
        "tools_removed": len(removed),
        "tools": removed,
    }


def _load_all_plugins() -> tuple[int, int]:
    """Import the plugins selected by ``META_DATA_MCP_PRELOAD`` at startup.

    Reads the comma-separated env var. Default (unset or empty) loads no
    plugins — only meta tools are advertised. Special value ``*`` loads
    every plugin (legacy behavior, useful when the client can handle a
    large tool catalog). Individual ids may be either underscore or
    hyphen form.

    Returns (plugins_loaded, tools_added).
    """
    raw = os.getenv("META_DATA_MCP_PRELOAD", "").strip()
    # Initialize the owner-map with the meta server's own tool names.
    for name in TOOLS_HANDLERS:
        _owner_by_tool.setdefault(name, "meta")

    if not raw:
        return (0, 0)

    if raw == "*":
        target_ids = [e.id for e in REGISTRY if e.id not in _NON_PLUGIN_MODULES]
    else:
        target_ids = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            canonical = _resolve_provider_id(token)
            if canonical is None:
                log.warning("META_DATA_MCP_PRELOAD: unknown plugin id '%s'", token)
                continue
            if canonical in _NON_PLUGIN_MODULES:
                continue
            target_ids.append(canonical)

    loaded = 0
    added = 0
    for pid in target_ids:
        try:
            module = importlib.import_module(f"meta_data_mcp.providers.{pid}")
        except ImportError as exc:
            log.warning("Plugin '%s' could not be imported: %s", pid, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — one broken plugin must not block the rest
            log.warning("Plugin '%s' raised during import: %s", pid, exc)
            continue
        before = len(TOOLS)
        added += _merge_plugin(module, pid)
        if len(TOOLS) > before:
            _active_providers.add(pid)
            loaded += 1
    return loaded, added


__all__ = [
    "_NON_PLUGIN_MODULES",
    "_activate_provider",
    "_deactivate_provider",
    "_load_all_plugins",
    "_merge_plugin",
    "_notify_tools_changed",
    "_resolve_provider_id",
]
