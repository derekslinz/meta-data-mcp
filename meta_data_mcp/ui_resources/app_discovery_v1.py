"""``ui://meta-data-mcp/app/discovery/v1`` — Phase 3 discovery app.

Wraps the five discovery meta tools plus ``opendata_health_snapshot`` into
one panel: faceted filter (domain × region × text), ranked provider list
with score breakdown + health badge, click-to-activate, bottom active-
provider tray.

Unlike the ``ui://shape/*`` primitives this is a first-class *app*, not a
passive renderer: it issues outbound ``tool_call`` messages to the host so
filter changes and activations execute live. The host→app envelope mirrors
the shape primitives (``{type: "tool_result" | "render", payload}``).

The bundle is dependency-free vanilla JS — the discovery panel is text +
buttons, no chart libraries needed. That keeps this PR's wire surface
small enough to validate the MCP Apps pipeline end-to-end before Phase 5
spends weeks on the custom-shape apps.

See ``Plans/linear-swimming-pond.md`` §3.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files

from mcp import types

from meta_data_mcp.utils import register_ui_resource

URI: str = "ui://meta-data-mcp/app/discovery/v1"

# Surfaced in the host's resource catalog. Documents the bidirectional
# postMessage protocol so a host integrator can wire app↔host without
# reading the bundle source. The MCP Apps spec hasn't ratified an
# app→host envelope yet; this string is the contract Phase 5 apps will
# inherit.
_DESCRIPTION = (
    "Discovery app: faceted filter, ranked provider list with score "
    "breakdown and live health badges, click-to-activate. Wraps "
    "opendata_providers_find / list-domains / list-regions / "
    "list-active-providers / activate-provider / health-snapshot. "
    "Dependency-free vanilla JS. "
    "postMessage protocol — "
    "host→app: {type: 'tool_result'|'render', id?, tool?, payload}; "
    "app→host: {type: 'tool_call', id, name, arguments} (Phase 3 invention; "
    "the spec hasn't ratified an app→host shape yet)."
)

_HTML: str = (files("meta_data_mcp.ui_resources") / "app_discovery_v1.html").read_text(
    encoding="utf-8",
)


def register(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> str:
    """Append the discovery app resource to the server's catalog.

    Returns the canonical URI so callers (and tests) can assert against it
    without duplicating the constant.
    """
    return register_ui_resource(
        name="app/discovery/v1",
        html=_HTML,
        description=_DESCRIPTION,
        resources=resources,
        resources_handlers=resources_handlers,
    )
