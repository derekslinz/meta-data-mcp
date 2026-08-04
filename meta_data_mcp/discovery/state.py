"""Mutable activation state for the meta server.

The meta server in :mod:`meta_data_mcp.providers.meta_data_mcp` owns
five pieces of state that change as plugins activate and deactivate at
runtime: the advertised tool list, the tool-name → handler map, the
set of activated provider ids, the tool-name → owning-provider map,
and an optional reference to the running ``Server`` instance (used to
fire ``tools/list_changed`` notifications when activation mutates the
catalog).

Pre-v2.1 these were five module-level globals. Bundling them into one
:class:`ActivationState` dataclass shrinks the test surface (snapshot /
restore is one call, not five), keeps the "what's mutable about the
discovery surface" question answerable in one file, and stops new
mutable state from being silently added without touching every test
fixture.

The module-level ``_state`` singleton plus the back-compat alias names
(``TOOLS``, ``TOOLS_HANDLERS``, ``RESOURCES``, ``RESOURCES_HANDLERS``,
``_active_providers``, ``_owner_by_tool``) live here too — the meta
server module re-exports them so existing call sites
(``from meta_data_mcp.providers.meta_data_mcp import TOOLS``) keep
working unchanged.

**Critical invariant.** ``TOOLS`` and ``TOOLS_HANDLERS`` are the *same*
list and dict objects passed into ``create_mcp_server``. Mutating them
in place (``TOOLS.append(...)``, ``TOOLS_HANDLERS[name] = handler``,
``TOOLS[:] = [...]``) is the mechanism by which the running server's
advertised catalog stays in sync with activation. Do not reassign
either name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mcp import types


@dataclass
class ActivationState:
    """All mutable state owned by the meta server's activation surface.

    Previously this was five module-level globals (``TOOLS``, ``TOOLS_HANDLERS``,
    ``_active_providers``, ``_owner_by_tool``, ``_server``). Bundling them into
    one object means tests only have to snapshot/restore a single thing, and
    adding a new mutable field doesn't require touching every isolation fixture.

    ``tools`` and ``tools_handlers`` are deliberately the *same list and dict*
    objects passed to ``create_mcp_server``; mutating them in place is the
    mechanism by which ``activate_provider`` / ``deactivate_provider`` update
    the running server's advertised tool catalog. Do not reassign them.
    """

    tools: list[types.Tool] = field(default_factory=list)
    tools_handlers: dict[str, Any] = field(default_factory=dict)
    active_providers: set[str] = field(default_factory=set)
    # tool_name → provider id ("meta" for the meta server's own tools).
    owner_by_tool: dict[str, str] = field(default_factory=dict)
    # Set by main() once the Server is constructed; used to emit
    # tools/list_changed notifications. Best-effort.
    server: Any | None = None

    def snapshot(self) -> tuple[Any, ...]:
        """Capture the full state for restoration in tests."""
        return (
            list(self.tools),
            dict(self.tools_handlers),
            set(self.active_providers),
            dict(self.owner_by_tool),
            self.server,
        )

    def restore(self, snap: tuple[Any, ...]) -> None:
        """Restore from a snapshot(). Mutates lists/dicts in place so any
        references held by ``create_mcp_server`` closures stay valid.
        """
        saved_tools, saved_handlers, saved_active, saved_owner, saved_server = snap
        self.tools[:] = saved_tools
        self.tools_handlers.clear()
        self.tools_handlers.update(saved_handlers)
        self.active_providers.clear()
        self.active_providers.update(saved_active)
        self.owner_by_tool.clear()
        self.owner_by_tool.update(saved_owner)
        self.server = saved_server


# Process-wide singleton. Importing this module twice returns the same
# ActivationState (Python caches modules in ``sys.modules``); both the
# meta server's tool definitions and the loader read/mutate this one
# instance.
_state = ActivationState()

# Back-compat module-level aliases. RESOURCES and RESOURCES_HANDLERS are
# independent lists (resources don't change at runtime today, so they
# don't live on ActivationState). TOOLS and TOOLS_HANDLERS point at the
# SAME list/dict objects owned by ``_state``, so mutations via either
# name are visible everywhere. Do not reassign these names.
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = _state.tools
TOOLS_HANDLERS: dict[str, Any] = _state.tools_handlers

# Convenience aliases for the activation-tracking sub-state. Same
# "alias to the underlying object" semantics as TOOLS / TOOLS_HANDLERS.
_active_providers = _state.active_providers
_owner_by_tool = _state.owner_by_tool


__all__ = [
    "RESOURCES",
    "RESOURCES_HANDLERS",
    "TOOLS",
    "TOOLS_HANDLERS",
    "ActivationState",
    "_active_providers",
    "_owner_by_tool",
    "_state",
]
