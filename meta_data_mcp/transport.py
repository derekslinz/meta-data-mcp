"""HTTP transport for every provider call.

Owns the mandatory ``http_get(provider=)`` / ``http_post(provider=)``
kernel contract: typed errors, health feedback, retry/backoff, response
caching with auth-aware partitioning, and User-Agent identification.

Module split out of ``utils.py`` in the v2.1 hygiene pass (architecture
review §H1). ``meta_data_mcp.utils`` re-exports the public surface so
existing call sites continue to import the same names.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from meta_data_mcp import __version__, citations
from meta_data_mcp._meta_common import utc_iso_ms

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TTL response cache
# ---------------------------------------------------------------------------

_CACHE_DEFAULT_TTL: float = float(os.getenv("OPENDATA_MCP_CACHE_TTL", "0"))
_CACHE_MAX_SIZE: int = 256


class _TTLCache:
    """Thread-safe in-memory TTL cache for HTTP responses.

    Evicts the oldest entry when the cache is full. Entries older than
    their individual TTL (or the default TTL) are treated as absent and
    evicted on next access.
    """

    def __init__(self, maxsize: int = _CACHE_MAX_SIZE, ttl: float = 60.0) -> None:
        self._cache: dict[str, tuple[float, Any, float]] = {}
        self._maxsize = maxsize
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, val, entry_ttl = entry
            if time.monotonic() - ts > entry_ttl:
                del self._cache[key]
                return None
            return val

    def set(self, key: str, val: Any, ttl: float | None = None) -> None:
        with self._lock:
            if len(self._cache) >= self._maxsize:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest]
            self._cache[key] = (
                time.monotonic(),
                val,
                ttl if ttl is not None else self._ttl,
            )

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


_response_cache = _TTLCache(maxsize=_CACHE_MAX_SIZE, ttl=max(_CACHE_DEFAULT_TTL, 1.0))


def _cache_key(
    url: str,
    params: dict | None,
    accept: str,
    has_auth: bool = False,
) -> str:
    """Hash a stable cache key. ``has_auth`` partitions authenticated and
    anonymous responses so they never share a cache entry — see the
    auth-aware cache key rationale in ``http_get``.
    """
    payload = json.dumps(
        {
            "url": url,
            "params": sorted((params or {}).items()),
            "accept": accept,
            "has_auth": bool(has_auth),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# HTTP retry helpers (429 / 5xx with Retry-After support)
# ---------------------------------------------------------------------------

_RETRY_AFTER_CAP_SECONDS: float = 30.0
_RETRY_BACKOFF_BASE: float = 0.5
_RETRY_BACKOFF_CAP_SECONDS: float = 8.0


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header value into seconds.

    Supports both formats from RFC 7231 section 7.1.3:
    - delta-seconds (e.g. ``"120"``)
    - HTTP-date (e.g. ``"Fri, 31 Dec 1999 23:59:59 GMT"``)

    Returns ``None`` if the value is missing or unparseable. Caller is
    responsible for capping the result.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = (dt - datetime.now(UTC)).total_seconds()
    return max(0.0, delta)


def _retry_sleep_seconds(attempt: int, retry_after: str | None) -> float:
    """Compute the seconds to sleep before the next retry.

    ``Retry-After`` is honored when present (capped at 30s); otherwise an
    exponential backoff ``0.5 * 2**attempt`` is used (capped at 8s).
    """
    parsed = _parse_retry_after(retry_after)
    if parsed is not None:
        return min(parsed, _RETRY_AFTER_CAP_SECONDS)
    return min(_RETRY_BACKOFF_BASE * (2**attempt), _RETRY_BACKOFF_CAP_SECONDS)


def _default_user_agent() -> str:
    """Return the default User-Agent string for outbound HTTP requests.

    Several open-data APIs (Crossref, Europe PMC, OSM Nominatim, SEC EDGAR)
    require an identifiable User-Agent including a contact address. Callers
    may override `META_DATA_MCP_CONTACT` via environment variable.
    """
    contact = os.getenv("META_DATA_MCP_CONTACT")
    base = f"meta-data-mcp/{__version__} (+https://github.com/derekslinz/meta-data-mcp"
    if contact:
        return f"{base}; {contact})"
    return f"{base})"


def http_get(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    provider: str,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
    cache_ttl: float | None = None,
) -> httpx.Response:
    """Perform a GET request with sensible defaults for open-data APIs.

    ``provider`` is **mandatory** — every kernel guarantee (typed errors,
    health feedback, URL-redacted exception messages) is keyed on a stable
    provider id. There is intentionally no anonymous path; callers that
    don't want the kernel contract should use ``httpx`` directly and
    accept full responsibility for the consequences.

    - Sets a default User-Agent identifying opendata-mcp (override via
      ``META_DATA_MCP_CONTACT`` env var or ``headers`` argument).
    - Sets ``Accept: application/json`` by default; override via ``headers``.
    - Calls ``raise_for_status()`` so handlers see a clean exception path.
    - Optional response caching: pass ``cache_ttl=<seconds>`` to cache the
      response body. The global ``OPENDATA_MCP_CACHE_TTL`` env var sets the
      default TTL (default 0 = disabled). Only successful (2xx) responses
      are cached. Cache keys are partitioned by presence of ``Authorization``
      / ``Cookie`` headers so authenticated and anonymous responses never
      share an entry.
    - Retries on ``429`` and ``5xx`` responses and on ``httpx.RequestError``
      pre-response failures (configurable via ``OPENDATA_MCP_HTTP_RETRIES``
      env var, default 2 retries = 3 attempts). Honors ``Retry-After`` when
      present (capped at 30s); otherwise uses capped exponential backoff.
    - Translates ``httpx.HTTPStatusError`` and ``httpx.RequestError`` to the
      :mod:`meta_data_mcp.errors` ``ProviderError`` hierarchy and feeds
      :mod:`meta_data_mcp.health` (``record_success`` on 2xx, ``record_failure``
      on non-recoverable error).

    Args:
        url: The endpoint URL.
        params: Optional query parameters.
        provider: Stable provider id (e.g. ``"us-data-gov"``). Required.
        timeout: Request timeout in seconds (default 10.0).
        headers: Optional header overrides merged on top of defaults.
        cache_ttl: Seconds to cache this response. ``None`` uses the global
            default (``OPENDATA_MCP_CACHE_TTL`` env var, default 0 = off).

    Returns:
        The httpx.Response (already status-checked). When served from cache
        the object is a lightweight stand-in with ``.json()`` and ``.text``
        populated from the cached data.

    Raises:
        meta_data_mcp.errors.ProviderError: subclass appropriate to the
            failure mode (``BadRequestError``, ``NotFoundError``,
            ``AuthError``, ``RateLimitError``, ``UpstreamError``, or
            ``NetworkError``). Never raises raw ``httpx`` exceptions.

    """
    # Lazy imports to avoid a circular at module load (health & errors
    # both import logging-only constants from this module's siblings).
    from meta_data_mcp import health
    from meta_data_mcp.errors import translate_http_error

    merged_headers = {
        "User-Agent": _default_user_agent(),
        "Accept": "application/json",
    }
    if headers:
        merged_headers.update(headers)

    has_auth = any(k.lower() in {"authorization", "cookie"} for k in merged_headers)

    effective_ttl = _CACHE_DEFAULT_TTL if cache_ttl is None else cache_ttl

    cache_key: str | None = None
    if effective_ttl > 0:
        cache_key = _cache_key(
            url,
            params,
            merged_headers.get("Accept", ""),
            has_auth=has_auth,
        )
        cached = _response_cache.get(cache_key)
        if cached is not None:
            log.debug("Cache hit for %s", url)
            cached_response, cached_fetched_at = cached
            citations.record(
                provider=provider,
                url=url,
                params=params,
                response=cached_response,
                cache_hit=True,
                fetched_at=cached_fetched_at,
            )
            return cached_response

    _DEFAULT_HTTP_RETRIES = 2
    try:
        _configured = int(
            os.getenv("OPENDATA_MCP_HTTP_RETRIES", str(_DEFAULT_HTTP_RETRIES)),
        )
        max_attempts = max(0, _configured) + 1
    except (ValueError, TypeError):
        max_attempts = _DEFAULT_HTTP_RETRIES + 1
    response: httpx.Response | None = None
    for attempt in range(max_attempts):
        try:
            response = httpx.get(
                url,
                params=params,
                timeout=timeout,
                headers=merged_headers,
                follow_redirects=True,
            )
        except httpx.RequestError as e:
            # Pre-response network failure. NetworkError.retryable=True, so
            # retry with backoff until exhausted, then translate.
            if attempt < max_attempts - 1:
                sleep_for = _retry_sleep_seconds(attempt, None)
                log.debug(
                    "Retrying %s after %.2fs (attempt %d/%d, network error: %s)",
                    url,
                    sleep_for,
                    attempt + 1,
                    max_attempts,
                    e,
                )
                time.sleep(sleep_for)
                continue
            health.record_failure(provider, status=None)
            raise translate_http_error(provider, e) from e
        status = getattr(response, "status_code", None)
        is_retryable = isinstance(status, int) and (
            status == 429 or 500 <= status < 600
        )
        if not is_retryable or attempt == max_attempts - 1:
            break
        # This attempt's exchange is retried away, but it still happened
        # — cite it so the manifest reflects upstream flakiness honestly.
        citations.record(provider=provider, url=url, params=params, response=response)
        retry_after = None
        try:
            retry_after = response.headers.get("Retry-After")
        except AttributeError:
            retry_after = None
        sleep_for = _retry_sleep_seconds(attempt, retry_after)
        log.debug(
            "Retrying %s after %.2fs (attempt %d/%d, status %d)",
            url,
            sleep_for,
            attempt + 1,
            max_attempts,
            status,
        )
        time.sleep(sleep_for)

    assert response is not None  # loop runs at least once
    citations.record(provider=provider, url=url, params=params, response=response)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status_code = getattr(e.response, "status_code", None)
        # V12 fix (Phase 3): 401/403 indicate caller-side credential misconfig,
        # not upstream fault. Penalizing the provider's health score would
        # bias routing away from a provider that's actually healthy — the
        # right outcome is for the caller to fix their env vars. Translate
        # the error (so the caller still sees a structured AuthError) but
        # don't degrade health. Pinned by
        # tests/test_health.py::test_record_failure_skipped_for_401_403.
        if status_code not in (401, 403):
            health.record_failure(provider, status=status_code)
        raise translate_http_error(provider, e) from e

    health.record_success(provider)

    if effective_ttl > 0 and cache_key is not None:
        # Store the fetch time alongside the response: cache-hit
        # citations must report when the bytes were actually fetched,
        # not when the cache was read.
        _response_cache.set(cache_key, (response, utc_iso_ms()), ttl=effective_ttl)

    return response


def http_post(
    url: str,
    json: Any | None = None,
    *,
    provider: str,
    timeout: float = 10.0,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Perform a POST request with the same kernel guarantees as ``http_get``.

    ``provider`` is **mandatory**, mirroring ``http_get``. Same retry,
    health-feedback, and ProviderError-translation contract.

    Cache behavior is intentionally NOT implemented — POST is non-idempotent
    in the general case, so caching responses would be incorrect. Callers
    that need caching on idempotent POST queries should layer their own
    semantic cache on top.

    Args:
        url: The endpoint URL.
        json: Optional JSON-serializable body. ``None`` sends no body.
        provider: Stable provider id. Required.
        timeout: Request timeout in seconds (default 10.0).
        headers: Optional header overrides merged on top of defaults.

    Returns:
        The httpx.Response (already status-checked).

    Raises:
        meta_data_mcp.errors.ProviderError: subclass appropriate to the
            failure mode. Never raises raw ``httpx`` exceptions.

    """
    from meta_data_mcp import health
    from meta_data_mcp.errors import translate_http_error

    merged_headers = {
        "User-Agent": _default_user_agent(),
        "Accept": "application/json",
    }
    if headers:
        merged_headers.update(headers)

    _DEFAULT_HTTP_RETRIES = 2
    try:
        _configured = int(
            os.getenv("OPENDATA_MCP_HTTP_RETRIES", str(_DEFAULT_HTTP_RETRIES)),
        )
        max_attempts = max(0, _configured) + 1
    except (ValueError, TypeError):
        max_attempts = _DEFAULT_HTTP_RETRIES + 1

    response: httpx.Response | None = None
    for attempt in range(max_attempts):
        try:
            response = httpx.post(
                url,
                json=json,
                timeout=timeout,
                headers=merged_headers,
                follow_redirects=True,
            )
        except httpx.RequestError as e:
            if attempt < max_attempts - 1:
                sleep_for = _retry_sleep_seconds(attempt, None)
                log.debug(
                    "Retrying POST %s after %.2fs (attempt %d/%d, network error: %s)",
                    url,
                    sleep_for,
                    attempt + 1,
                    max_attempts,
                    e,
                )
                time.sleep(sleep_for)
                continue
            health.record_failure(provider, status=None)
            raise translate_http_error(provider, e) from e
        status = getattr(response, "status_code", None)
        is_retryable = isinstance(status, int) and (
            status == 429 or 500 <= status < 600
        )
        if not is_retryable or attempt == max_attempts - 1:
            break
        # Cite the retried-away attempt; see the matching comment in
        # ``http_get``.
        citations.record(provider=provider, url=url, response=response, method="POST")
        retry_after = None
        try:
            retry_after = response.headers.get("Retry-After")
        except AttributeError:
            retry_after = None
        sleep_for = _retry_sleep_seconds(attempt, retry_after)
        log.debug(
            "Retrying POST %s after %.2fs (attempt %d/%d, status %d)",
            url,
            sleep_for,
            attempt + 1,
            max_attempts,
            status,
        )
        time.sleep(sleep_for)

    assert response is not None
    citations.record(provider=provider, url=url, response=response, method="POST")
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status_code = getattr(e.response, "status_code", None)
        # V12 fix (Phase 3): see the matching comment in ``http_get``. 401/403
        # is caller misconfig, not upstream fault — don't degrade health.
        if status_code not in (401, 403):
            health.record_failure(provider, status=status_code)
        raise translate_http_error(provider, e) from e

    health.record_success(provider)

    return response


__all__ = [
    "http_get",
    "http_post",
]
