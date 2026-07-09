"""Per-call source citations for tool results.

Every provider call already flows through the transport kernel
(``http_get(provider=)`` / ``http_post(provider=)``). This module
records each upstream HTTP exchange in a context-local log; the
dispatcher in :func:`meta_data_mcp.server.create_mcp_server` wraps each
tool call in a :func:`recording_span` and attaches the collected
manifest to the first content block's ``_meta`` field, under the key
``meta-data-mcp/citations``:

    {
        "sources": [
            {
                "provider": "eu-eurostat",
                "title": "Eurostat",
                "homepage": "https://ec.europa.eu/eurostat",
                "license": "...",                  # when known
                "url": "https://.../data/nama_10_gdp?format=JSON&lang=en",
                "method": "GET",
                "status": 200,
                "fetched_at": "YYYY-MM-DDTHH:MM:SS.mmmZ",
                "cache_hit": false
            }
        ]
    }

This is what makes an LLM data answer auditable: the exact URL(s) the
result came from (query params included), when they were fetched,
whether they were served from the transport cache, and the provider's
license/attribution terms. A reader can re-issue the URL and check the
claim.

**Secret redaction.** Query strings can carry API keys (``api_key``,
``appid``, ``token``, …). :func:`redact_url` replaces the values of
sensitive parameters with ``REDACTED`` before anything enters the
manifest; the parameter *names* are preserved so the request stays
reproducible for anyone holding their own credentials. Headers never
enter the manifest at all.

**Failed exchanges are cited too.** A 4xx/5xx response that a handler
catches (or that federation reports per-query) is part of how the
result was produced; consumers can filter on ``status``. Pre-response
network failures produce no exchange and are not recorded.

**POST bodies** are intentionally not captured in v3.0 — only the URL.
Reproducibility for POST-based providers is a documented follow-up.

Default is ON. Citations are additive ``_meta``, cheap to compute, and
redaction makes them safe; set ``META_DATA_MCP_CITATIONS`` to a falsy
value (``0``, ``false``, ``no``, ``off``) to disable. This is the
opposite default from the tamper-evidence digest in
:mod:`meta_data_mcp.provenance`, which is contract-heavy and opt-in.

Concurrency: the log lives in a :class:`contextvars.ContextVar`, so
concurrent tool calls (each dispatched in its own task context) never
see each other's records. ``anyio.to_thread`` propagates context, so
handlers that push sync HTTP work onto threads keep recording.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generator, Sequence

import httpx
from mcp import types

CITATIONS_META_KEY = "meta-data-mcp/citations"

REDACTED = "REDACTED"

_ENV_VAR = "META_DATA_MCP_CITATIONS"
_FALSY = frozenset({"0", "false", "no", "off"})

# Lowercased query-parameter names whose values are replaced with
# REDACTED. Matching is case-insensitive on the parameter name.
_SENSITIVE_PARAMS = frozenset(
    {
        "api_key",
        "api-key",
        "apikey",
        "apitoken",
        "access_token",
        "auth",
        "client_secret",
        "key",
        "password",
        "secret",
        "signature",
        "token",
        "appid",
        "app_id",
    }
)

log = logging.getLogger(__name__)

Content = types.TextContent | types.ImageContent | types.EmbeddedResource

_RECORDING: ContextVar[list["SourceRecord"] | None] = ContextVar(
    "meta_data_mcp_citations_recording", default=None
)


def is_enabled() -> bool:
    """True unless ``META_DATA_MCP_CITATIONS`` is set to a falsy value.

    Unset and empty both mean enabled — citations are the default.
    """
    return os.getenv(_ENV_VAR, "").strip().lower() not in _FALSY


@dataclass(frozen=True)
class SourceRecord:
    """One upstream HTTP exchange that contributed to a tool result."""

    provider: str  # kebab server_name, as passed to the kernel
    url: str  # full request URL, secrets redacted
    method: str
    status: int | None
    fetched_at: str  # UTC ISO 8601, millisecond precision, trailing Z
    cache_hit: bool


def _utc_iso_ms() -> str:
    """ISO 8601 UTC with millisecond precision and a trailing ``Z``."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def redact_url(url: str | httpx.URL) -> str:
    """Return ``url`` with sensitive query-parameter values replaced.

    Parameter names are matched case-insensitively against
    ``_SENSITIVE_PARAMS``; names and ordering are preserved so the
    redacted URL remains a usable template for re-issuing the request
    with one's own credentials. URLs without a query pass through
    unchanged.
    """
    u = httpx.URL(url)
    if not u.query:
        return str(u)
    qp = httpx.QueryParams(u.query)
    redacted = httpx.QueryParams(
        [
            (k, REDACTED if k.lower() in _SENSITIVE_PARAMS else v)
            for k, v in qp.multi_items()
        ]
    )
    return str(u.copy_with(query=str(redacted).encode()))


def record(
    *,
    provider: str,
    url: str,
    params: dict[str, Any] | None = None,
    response: httpx.Response | None = None,
    method: str = "GET",
    status: int | None = None,
    cache_hit: bool = False,
) -> None:
    """Append one exchange to the active recording span. No-op outside one.

    The recorded URL prefers ``response.request.url`` — the ground truth
    of what was actually sent (httpx *replaces* a URL-embedded query
    string when ``params`` is passed, so recomposing can lie). Falls
    back to composing from ``url`` + ``params`` for responses that don't
    carry a request (e.g. cache stand-ins or test doubles).
    """
    records = _RECORDING.get()
    if records is None:
        return

    request_url: httpx.URL | None = None
    if response is not None:
        request = getattr(response, "request", None)
        request_url = getattr(request, "url", None)
    if request_url is None:
        request_url = httpx.URL(url, params=params) if params else httpx.URL(url)

    if status is None and response is not None:
        status = getattr(response, "status_code", None)

    records.append(
        SourceRecord(
            provider=provider,
            url=redact_url(request_url),
            method=method,
            status=status,
            fetched_at=_utc_iso_ms(),
            cache_hit=cache_hit,
        )
    )


@contextmanager
def recording_span() -> Generator[list[SourceRecord], None, None]:
    """Open a fresh recording span for the duration of one tool call.

    Yields the live list that :func:`record` appends to. Spans nest by
    shadowing: an inner span records into its own list and the outer
    list resumes on exit.
    """
    token = _RECORDING.set([])
    try:
        yield _RECORDING.get()  # type: ignore[misc]  # set([]) above, never None
    finally:
        _RECORDING.reset(token)


def _enrich(record_: SourceRecord) -> dict[str, Any]:
    """Render one record as a manifest entry, joined with registry metadata.

    The kernel receives the kebab ``server_name`` (e.g. ``eu-eurostat``)
    while :meth:`Registry.find` keys on the snake ``id`` — resolution
    goes through :meth:`Registry.find_by_server_name`. Unknown providers
    (out-of-tree callers) still cite; they just carry no registry fields.
    """
    from meta_data_mcp.registry import REGISTRY

    entry: dict[str, Any] = {
        "provider": record_.provider,
        "url": record_.url,
        "method": record_.method,
        "status": record_.status,
        "fetched_at": record_.fetched_at,
        "cache_hit": record_.cache_hit,
    }
    provider_entry = REGISTRY.find_by_server_name(record_.provider)
    if provider_entry is not None:
        entry["title"] = provider_entry.title
        entry["homepage"] = provider_entry.homepage
        if provider_entry.license_note:
            entry["license"] = provider_entry.license_note
    return entry


def attach(
    content: Sequence[Content],
    records: Sequence[SourceRecord],
) -> list[Content]:
    """Return a fresh content list with the citation manifest attached.

    The manifest goes on the first content block's ``_meta`` under
    :data:`CITATIONS_META_KEY`; pre-existing ``_meta`` keys are
    preserved, and the provenance digest (which hashes ``_meta``-stripped
    content) is unaffected regardless of attach order. With no records
    the content passes through unchanged — a tool that made no upstream
    calls (pure meta tools, registry lookups) has nothing to cite.

    When ``content`` is empty but records exist, a stub
    ``TextContent(text="")`` is synthesized to carry the manifest,
    mirroring :func:`meta_data_mcp.provenance.attach`.
    """
    blocks: list[Content] = list(content)
    if not records:
        return blocks
    if not blocks:
        log.warning(
            "citations.attach: empty content with %d source record(s); "
            "synthesizing stub TextContent to carry the manifest",
            len(records),
        )
        blocks = [types.TextContent(type="text", text="")]

    payload = {CITATIONS_META_KEY: {"sources": [_enrich(r) for r in records]}}

    first = blocks[0]
    merged_meta = dict(first.meta) if first.meta else {}
    merged_meta.update(payload)
    blocks[0] = first.model_copy(update={"meta": merged_meta})
    return blocks


__all__ = [
    "CITATIONS_META_KEY",
    "REDACTED",
    "SourceRecord",
    "attach",
    "is_enabled",
    "record",
    "recording_span",
    "redact_url",
]
