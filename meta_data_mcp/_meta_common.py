"""Shared plumbing for the ``_meta`` layers on tool results.

Two modules attach call-scoped metadata to the first content block of a
tool result: :mod:`meta_data_mcp.provenance` (tamper-evidence digest)
and :mod:`meta_data_mcp.citations` (source-citation manifest). Both need
the same three pieces — the content-block union type, the ISO-8601-UTC-
millisecond timestamp format that is part of each layer's documented
contract, and the non-mutating first-block ``_meta`` merge. Keeping them
here means a format or merge-semantics change lands in one place and
both layers stay in lockstep.
"""

from __future__ import annotations

from datetime import UTC, datetime

from mcp import types

Content = types.TextContent | types.ImageContent | types.EmbeddedResource


def utc_iso_ms() -> str:
    """ISO 8601 UTC with millisecond precision and a trailing ``Z``.

    This exact format is part of the public contract of both the
    provenance ``timestamp`` and the citations ``fetched_at`` fields.
    """
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def merge_into_first_block(
    blocks: list[Content],
    payload: dict[str, object],
) -> list[Content]:
    """Return a fresh list with ``payload`` merged into the first block's
    ``_meta``.

    Pre-existing ``_meta`` keys on the block are preserved (``payload``
    keys win on collision, which is how the layers stay independent —
    each owns a distinct namespaced key). The input list and its blocks
    are not mutated; the first block is rebuilt via
    :py:meth:`pydantic.BaseModel.model_copy`. Callers are responsible
    for deciding what to do with empty content — this helper requires at
    least one block.
    """
    first = blocks[0]
    merged_meta = dict(first.meta) if first.meta else {}
    merged_meta.update(payload)
    out = list(blocks)
    out[0] = first.model_copy(update={"meta": merged_meta})
    return out


__all__ = ["Content", "merge_into_first_block", "utc_iso_ms"]
