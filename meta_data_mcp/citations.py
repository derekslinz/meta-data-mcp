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
reproducible for anyone holding their own credentials. Matching is a
denylist of exact names plus conservative suffix/substring heuristics
(``*key``, ``*token``, ``*secret*``, ``*signature*``, ``*password*``,
``*credential*``) so presigned cloud-storage URLs (``X-Amz-Signature``)
and generated plugins with provider-specific key params are covered.
The heuristics occasionally redact a benign param (e.g. a pagination
``page_token``) — the safe direction. Userinfo credentials embedded in
the URL itself (``https://user:pass@host``) are redacted too. Headers
never enter the manifest at all.

**Failed exchanges are cited too.** Every completed HTTP exchange is
recorded — including 4xx/5xx responses a handler recovers from and the
intermediate 429/5xx attempts of the kernel's retry loop — so the
manifest reflects upstream flakiness honestly; consumers can filter on
``status``. Pre-response network failures produce no exchange and are
not recorded. Note the SDK error path bypasses attachment entirely: a
tool call that *raises* returns an ``isError`` result with no manifest.

**Timestamps.** ``fetched_at`` is when the bytes were actually fetched
from upstream. Cache-served exchanges carry the original fetch time
(stored alongside the cached response), not the cache-read time, with
``cache_hit: true``.

**Recording never breaks a tool call.** This is an observability layer;
:func:`record` catches its own failures (malformed URLs, exotic
response doubles), logs a warning, and drops the record rather than
propagating into the handler.

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
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx

from meta_data_mcp._meta_common import Content, merge_into_first_block, utc_iso_ms

CITATIONS_META_KEY = "meta-data-mcp/citations"

REDACTED = "REDACTED"

_ENV_VAR = "META_DATA_MCP_CITATIONS"
_FALSY = frozenset({"0", "false", "no", "off"})

# Exact (lowercased) query-parameter names whose values are always
# redacted. Extended by the suffix/substring heuristics in
# ``_is_sensitive_param``.
_SENSITIVE_PARAMS = frozenset(
    {
        "api_key",
        "api-key",
        "apikey",
        "apitoken",
        "access_token",
        "auth",
        "auth_token",
        "client_secret",
        "key",
        "password",
        "private_token",
        "secret",
        "sig",
        "signature",
        "subscription-key",
        "token",
        "appid",
        "app_id",
    },
)

log = logging.getLogger(__name__)

_RECORDING: ContextVar[list[SourceRecord] | None] = ContextVar(
    "meta_data_mcp_citations_recording",
    default=None,
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


def _is_sensitive_param(name: str) -> bool:
    """Whether a query parameter's value must be redacted.

    Exact denylist first, then conservative heuristics that catch
    provider-specific credential names the denylist can't enumerate:
    generated plugins may map an env var onto any query-param name
    (``subscription-key``, ``user-key``), and redirect-followed
    presigned URLs carry ``X-Amz-Signature`` / ``X-Amz-Credential``.
    ``auth`` is exact-match only — a substring test would hit
    ``author``.
    """
    n = name.lower()
    return (
        n in _SENSITIVE_PARAMS
        or n.endswith(("key", "token"))
        or "secret" in n
        or "signature" in n
        or "password" in n
        or "credential" in n
    )


def redact_url(url: str | httpx.URL) -> str:
    """Return ``url`` with credential material replaced by ``REDACTED``.

    Covers sensitive query-parameter values (names and ordering are
    preserved so the redacted URL remains a usable template for
    re-issuing the request with one's own credentials) and userinfo
    embedded in the authority (``https://user:pass@host`` — both parts
    are replaced, since a username there is typically an API key).
    """
    u = httpx.URL(url)
    if u.userinfo:
        u = u.copy_with(username=REDACTED, password=REDACTED)
    if not u.query:
        return str(u)
    qp = httpx.QueryParams(u.query)
    items = qp.multi_items()
    if not any(_is_sensitive_param(k) for k, _ in items):
        return str(u)
    redacted = httpx.QueryParams(
        [(k, REDACTED if _is_sensitive_param(k) else v) for k, v in items],
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
    fetched_at: str | None = None,
) -> None:
    """Append one exchange to the active recording span. No-op outside one.

    The recorded URL prefers ``response.request.url`` — the ground truth
    of what was actually sent (httpx *replaces* a URL-embedded query
    string when ``params`` is passed, so recomposing can lie). Falls
    back to composing from ``url`` + ``params`` when the response
    carries no request (``httpx.Response.request`` raises
    ``RuntimeError`` in that case — synthetic responses, test doubles).

    ``fetched_at`` defaults to now (correct for a response recorded at
    fetch time); the transport passes the stored original fetch time for
    cache-served responses.

    Recording is an observability concern and must never break the tool
    call it observes: any failure here (malformed URL, exotic response
    double) is logged at warning level and the record is dropped.
    """
    records = _RECORDING.get()
    if records is None:
        return

    try:
        request_url: httpx.URL | None = None
        if response is not None:
            try:
                request = response.request
            except (AttributeError, RuntimeError):
                request = None
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
                status=status if isinstance(status, int) else None,
                fetched_at=fetched_at or utc_iso_ms(),
                cache_hit=cache_hit,
            ),
        )
    except Exception:
        log.warning(
            "citations.record: dropping unrecordable exchange for provider "
            "'%s' (url=%r)",
            provider,
            url,
            exc_info=True,
        )


@contextmanager
def recording_span() -> Generator[list[SourceRecord], None, None]:
    """Open a fresh recording span for the duration of one tool call.

    Yields the list that :func:`record` appends to. Spans nest by
    shadowing: an inner span records into its own list and the outer
    list resumes on exit. When citations are disabled via the env var,
    the yielded list simply stays empty (the ContextVar is never set),
    so callers need no separate enabled/disabled code path.
    """
    records: list[SourceRecord] = []
    if not is_enabled():
        yield records
        return
    token = _RECORDING.set(records)
    try:
        yield records
    finally:
        _RECORDING.reset(token)


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

    Empty content also passes through unchanged (with a warning):
    synthesizing a stub block to carry the manifest would change the
    wire shape clients see — e.g. the remote SDK treats empty content as
    "no result" — and citations must never alter results, only annotate
    them.
    """
    blocks: list[Content] = list(content)
    if not records:
        return blocks
    if not blocks:
        log.warning(
            "citations.attach: dropping %d source record(s) — result has "
            "no content block to carry the manifest",
            len(records),
        )
        return blocks

    # Lazy import keeps module load light; hoisted out of the per-record
    # loop. One registry scan per distinct provider, not per record.
    from meta_data_mcp.registry import REGISTRY

    entries: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    for record_ in records:
        source: dict[str, Any] = {
            "provider": record_.provider,
            "url": record_.url,
            "method": record_.method,
            "status": record_.status,
            "fetched_at": record_.fetched_at,
            "cache_hit": record_.cache_hit,
        }
        if record_.provider not in entries:
            entries[record_.provider] = REGISTRY.find_by_server_name(record_.provider)
        provider_entry = entries[record_.provider]
        if provider_entry is not None:
            source["title"] = provider_entry.title
            source["homepage"] = provider_entry.homepage
            if provider_entry.license_note:
                source["license"] = provider_entry.license_note
        sources.append(source)

    return merge_into_first_block(blocks, {CITATIONS_META_KEY: {"sources": sources}})


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
