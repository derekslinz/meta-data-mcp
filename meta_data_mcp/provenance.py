"""Optional sha256 + timestamp provenance for tool-call results.

Opt in by setting the ``META_DATA_MCP_PROVENANCE`` environment variable
to a truthy value (``1``, ``true``, ``yes``, ``on`` — case-insensitive,
surrounding whitespace ignored). When enabled, the server wraps every
``call_tool`` response with provenance metadata attached to the first
content block's ``_meta`` field, under the key
``meta-data-mcp/provenance``:

    {
        "sha256": "<lowercase hex digest>",
        "timestamp": "YYYY-MM-DDTHH:MM:SS.mmmZ"
    }

**What the digest covers.** The hash binds the call's identity to its
output — specifically a canonical JSON envelope:

    {
        "tool": "<tool name>",
        "arguments": <arguments dict or {}>,
        "content": [<content blocks with _meta stripped>]
    }

Including the tool name and arguments is what makes the digest
useful for audit: a receiver can detect not just content tampering
but also "wrong tool" / "wrong inputs" mismatches between what they
asked for and what they got back. Without that binding, two different
calls returning the same payload would share a fingerprint.

**Receiver verification recipe** (the documented contract — anyone
following these exact steps will reproduce the advertised digest):

    import hashlib, json

    rendered = []
    for block in response_content:
        # mode="json" is load-bearing: it coerces nested AnyUrl / Decimal /
        # datetime fields into JSON-native types. exclude_none=True drops
        # optional fields the SDK leaves unset, which the sender does too.
        dumped = block.model_dump(mode="json", by_alias=True, exclude_none=True)
        dumped.pop("_meta", None)
        rendered.append(dumped)

    envelope = {
        "tool": tool_name,
        "arguments": arguments or {},
        "content": rendered,
    }
    canonical = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()

Every kwarg in the recipe is load-bearing — change any of them and the
digest will not match. ``mode="json"`` coerces nested ``AnyUrl`` /
``Decimal`` / ``datetime`` fields to JSON-native types (without it
``EmbeddedResource``'s nested ``uri: AnyUrl`` blows up the serializer).
``exclude_none=True`` is required so optional SDK fields that default
to ``None`` don't enter the canonical form on the sender side but not
the receiver side. ``ensure_ascii=True`` pins the unicode-escape
behavior so a receiver using a JSON library with different defaults
still produces matching bytes. ``sort_keys=True`` + the compact
``separators`` collapse insertion-order and whitespace variability.

Default is OFF. Callers that need tamper-evidence or an audit trail
opt in; everyone else pays zero per-call overhead. The flip happens at
the dispatcher in :func:`meta_data_mcp.server.create_mcp_server` so
provenance applies uniformly to every tool — meta tools, plugin tools,
future tools — without per-handler wiring.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Sequence
from typing import Any

from mcp import types

from meta_data_mcp._meta_common import (
    Content,
    merge_into_first_block,
)
from meta_data_mcp._meta_common import (
    utc_iso_ms as _utc_iso_ms,
)

PROVENANCE_META_KEY = "meta-data-mcp/provenance"

_ENV_VAR = "META_DATA_MCP_PROVENANCE"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

log = logging.getLogger(__name__)


def is_enabled() -> bool:
    """True iff ``META_DATA_MCP_PROVENANCE`` is set to a truthy value."""
    return os.getenv(_ENV_VAR, "").strip().lower() in _TRUTHY


def _canonicalize(
    tool_name: str,
    arguments: dict[str, Any] | None,
    content: Sequence[Content],
) -> bytes:
    """Stable byte form of the (tool, arguments, content) envelope.

    Each content block is dumped with
    ``model_dump(mode="json", by_alias=True, exclude_none=True)`` so
    the SDK's wire format is what gets hashed, and the ``_meta`` field
    is stripped from every block — the advertised digest must be
    reproducible by a receiver that doesn't see the provenance metadata
    itself.

    Every ``model_dump`` / ``json.dumps`` kwarg is part of the public
    contract; see the module docstring for the receiver verification
    recipe.
    """
    rendered: list[dict[str, Any]] = []
    for block in content:
        dumped = block.model_dump(mode="json", by_alias=True, exclude_none=True)
        dumped.pop("_meta", None)
        rendered.append(dumped)
    envelope = {
        "tool": tool_name,
        "arguments": arguments or {},
        "content": rendered,
    }
    return json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def attach(
    content: Sequence[Content],
    *,
    tool_name: str,
    arguments: dict[str, Any] | None,
) -> list[Content]:
    """Return a fresh content list with provenance attached.

    Provenance is added to the first content block's ``_meta`` field
    under :data:`PROVENANCE_META_KEY`. Any pre-existing ``_meta`` on
    that block is preserved (the provenance key is merged in). When
    ``content`` is empty, a stub ``TextContent(text="")`` is synthesized
    to carry the metadata — a handler returning empty content is almost
    certainly buggy, so we also emit a warning log line to make the
    anomaly visible to operators.

    The input sequence is not mutated; the first block is rebuilt via
    :py:meth:`pydantic.BaseModel.model_copy`. ``tool_name`` and
    ``arguments`` are required keyword arguments — they're part of the
    digest input, so callers can't accidentally compute an output-only
    digest that loses input-output binding.
    """
    blocks: list[Content] = list(content)
    if not blocks:
        log.warning(
            "provenance.attach: tool '%s' returned empty content; "
            "synthesizing stub TextContent to carry the fingerprint",
            tool_name,
        )
        blocks = [types.TextContent(type="text", text="")]

    digest = hashlib.sha256(_canonicalize(tool_name, arguments, blocks)).hexdigest()
    return merge_into_first_block(
        blocks,
        {
            PROVENANCE_META_KEY: {
                "sha256": digest,
                "timestamp": _utc_iso_ms(),
            },
        },
    )


__all__ = ["PROVENANCE_META_KEY", "attach", "is_enabled"]
