"""Resolve user consent for auto-contribution via MCP elicitation.

Kept separate from ``contribute.py`` so the git/PR mechanism has no dependency
on the MCP session. Default is to proceed (auto-contribute is ON); elicitation
only downgrades to 'declined' when the user actively says no.
"""

from __future__ import annotations

import logging
from typing import Any

from meta_data_mcp import contribute

log = logging.getLogger(__name__)

_ELICIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contribute": {
            "type": "boolean",
            "title": "Contribute this plugin",
            "description": "Open a public PR so others can use it.",
            "default": True,
        }
    },
    "required": ["contribute"],
}


def _current_session() -> Any | None:
    """Return the active MCP ServerSession, or None outside a request."""
    try:
        from mcp.server.lowlevel.server import request_ctx

        return request_ctx.get().session
    except (LookupError, ImportError, AttributeError):
        return None


async def resolve_consent(plugin_id: str) -> str:
    """Return 'proceed', 'disabled', or 'declined'."""
    if not contribute.is_enabled():
        return "disabled"

    session = _current_session()
    if session is None:
        return "proceed"

    # Capability-gated: only elicit if the client supports it.
    try:
        from mcp import types as mcp_types

        cap = mcp_types.ClientCapabilities(
            elicitation=mcp_types.ElicitationCapability()
        )
        # NB: check_client_capability is a synchronous method in the mcp SDK
        # (returns bool). Do NOT await it — awaiting the bool raises TypeError,
        # which the broad except below would swallow into a silent "proceed",
        # killing the consent prompt for every real client.
        result = session.check_client_capability(cap)
        # Hardening: if someone adds `await`, the sync mock in tests returns
        # bool, not awaitable; this assertion catches the regression locally.
        assert not hasattr(result, "__await__"), (
            "check_client_capability must not be awaited"
        )
        if not result:
            return "proceed"
    except Exception as exc:  # noqa: BLE001 — capability probing is best-effort
        log.warning("capability probe failed, proceeding: %s", exc)
        return "proceed"

    try:
        result = await session.elicit(
            message=(
                f"Contribute '{plugin_id}' back to the meta-data-mcp project "
                "so others can use it? This opens a public pull request."
            ),
            requestedSchema=_ELICIT_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 — never let elicitation break create
        log.warning("elicitation failed, proceeding: %s", exc)
        return "proceed"

    if getattr(result, "action", None) == "accept":
        content = getattr(result, "content", None) or {}
        return "proceed" if content.get("contribute", True) else "declined"
    return "declined"
