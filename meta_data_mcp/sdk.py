"""Embedded Python client for meta-data-mcp discovery tools.

SDK-1: Level 2 (embedded) — calls the routing engine and registry
directly, no HTTP or MCP protocol overhead.

Typical async usage::

    from meta_data_mcp.sdk import find_providers, list_domains

    results = await find_providers("earthquake data", limit=5)
    for r in results:
        print(r["entry"]["id"], r["score"])

Sync convenience (wraps ``asyncio.run``)::

    from meta_data_mcp.sdk import find_providers_sync

    results = find_providers_sync("earthquake data", limit=5)

For applications that need multiple engines with different configs,
instantiate :class:`DiscoveryClient` directly::

    client = DiscoveryClient()
    results = await client.find_providers("floods", domain="earth-science")

Level 1 (remote HTTP) — connecting to a running SSE server — is SDK-2
and lives in a future ``meta_data_mcp.sdk.remote`` module.
"""

from __future__ import annotations

import asyncio
from typing import Any

__all__ = [
    "DiscoveryClient",
    "activate_provider",
    "describe_provider",
    "find_providers",
    "find_providers_sync",
    "health_snapshot",
    "list_domains",
    "list_regions",
]


class DiscoveryClient:
    """Embedded discovery client backed by the local routing engine.

    Creates its own :class:`~meta_data_mcp.routing.RoutingEngine` instance
    on construction, so each client has an independent LRU cache. To share
    an engine across coroutines in the same process, share the client
    instance.
    """

    def __init__(self) -> None:
        from meta_data_mcp.routing import RoutingEngine

        self._engine = RoutingEngine()

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------

    async def find_providers(
        self,
        query: str | None = None,
        domain: str | None = None,
        region: str | None = None,
        limit: int = 20,
        explain: bool = False,
    ) -> list[dict[str, Any]]:
        """Find providers matching the given criteria.

        Args:
            query:   Free-text query (matched against id, title, keywords, etc.).
            domain:  Hard filter by domain (e.g. ``"health"``, ``"finance"``).
            region:  Hard filter by region (e.g. ``"us"``, ``"eu"``, ``"global"``).
            limit:   Maximum results returned (default 20).
            explain: When True, each result includes a ``breakdown`` dict of
                     per-strategy scores so callers can see why a provider ranked.

        Returns:
            List of dicts sorted by descending score::

                [
                  {
                    "entry":     {...},           # ProviderEntry.to_dict()
                    "score":     0.93,
                    "breakdown": {"token": 0.8, ...} | None
                  },
                  ...
                ]
        """
        results = await self._engine.route(
            query=query,
            domain=domain,
            region=region,
            limit=limit,
            explain=explain,
        )
        return [
            {
                "entry": r.entry.to_dict(),
                "score": r.score,
                "breakdown": dict(r.breakdown) if r.breakdown else None,
            }
            for r in results
        ]

    async def health_snapshot_async(self) -> dict[str, float]:
        """Return a ``{provider_id: health_score}`` snapshot for every provider.

        Scores are in ``[0.0, 1.0]`` — 1.0 means no recorded failures.
        This is the async variant; the sync :meth:`health_snapshot` is
        equivalent and simpler to call outside an async context.
        """
        return self.health_snapshot()

    # ------------------------------------------------------------------
    # Sync shims (asyncio.run wrappers for the async methods)
    # ------------------------------------------------------------------

    def find_providers_sync(
        self,
        query: str | None = None,
        domain: str | None = None,
        region: str | None = None,
        limit: int = 20,
        explain: bool = False,
    ) -> list[dict[str, Any]]:
        """Synchronous wrapper around :meth:`find_providers`.

        Uses ``asyncio.run()`` — do not call from inside a running event loop.
        In an async context, use ``await client.find_providers(...)`` directly.
        """
        return asyncio.run(
            self.find_providers(
                query=query,
                domain=domain,
                region=region,
                limit=limit,
                explain=explain,
            )
        )

    # ------------------------------------------------------------------
    # Sync registry / health methods (no async needed)
    # ------------------------------------------------------------------

    def list_domains(self) -> list[str]:
        """Return the sorted list of all domain tags in the registry."""
        from meta_data_mcp.registry import list_domains as _list_domains

        return _list_domains()

    def list_regions(self) -> list[str]:
        """Return the sorted list of all region tags in the registry."""
        from meta_data_mcp.registry import list_regions as _list_regions

        return _list_regions()

    def describe_provider(self, provider_id: str) -> dict[str, Any] | None:
        """Return the registry entry for *provider_id*, or ``None`` if not found.

        Accepts both canonical snake_case ids (``us_usgs_earthquake``) and
        kebab-case server names (``us-usgs-earthquake``).
        """
        from meta_data_mcp.registry import get_provider

        entry = get_provider(provider_id)
        return entry.to_dict() if entry is not None else None

    def activate_provider(self, provider_id: str) -> dict[str, Any]:
        """Lazy-load the named provider's plugin module into this process.

        Returns a status dict with ``status`` set to one of:
        ``"ok"``, ``"already_active"``, or ``"error"``.
        """
        from meta_data_mcp.discovery.loader import _activate_provider

        return _activate_provider(provider_id)

    def health_snapshot(self) -> dict[str, float]:
        """Return a ``{provider_id: health_score}`` snapshot for every provider.

        Scores are in ``[0.0, 1.0]`` — 1.0 means no recorded failures in the
        current process's in-memory health state.
        """
        from meta_data_mcp import health
        from meta_data_mcp.registry import iter_registry

        return {entry.id: health.health_score(entry.id) for entry in iter_registry()}


# ---------------------------------------------------------------------------
# Module-level convenience API
#
# Uses a single per-module DiscoveryClient instance (lazy-initialised). For
# applications that need multiple engines or independent caches, instantiate
# DiscoveryClient directly.
# ---------------------------------------------------------------------------

_client: DiscoveryClient | None = None


def _get_client() -> DiscoveryClient:
    global _client
    if _client is None:
        _client = DiscoveryClient()
    return _client


async def find_providers(
    query: str | None = None,
    domain: str | None = None,
    region: str | None = None,
    limit: int = 20,
    explain: bool = False,
) -> list[dict[str, Any]]:
    """Find providers matching the given criteria (module-level async)."""
    return await _get_client().find_providers(
        query=query,
        domain=domain,
        region=region,
        limit=limit,
        explain=explain,
    )


def find_providers_sync(
    query: str | None = None,
    domain: str | None = None,
    region: str | None = None,
    limit: int = 20,
    explain: bool = False,
) -> list[dict[str, Any]]:
    """Find providers (sync — wraps asyncio.run; not for use inside event loops)."""
    return asyncio.run(
        find_providers(
            query=query, domain=domain, region=region, limit=limit, explain=explain
        )
    )


def list_domains() -> list[str]:
    """Return all domain tags in the registry."""
    return _get_client().list_domains()


def list_regions() -> list[str]:
    """Return all region tags in the registry."""
    return _get_client().list_regions()


def describe_provider(provider_id: str) -> dict[str, Any] | None:
    """Return the registry entry for *provider_id*, or ``None`` if not found."""
    return _get_client().describe_provider(provider_id)


def activate_provider(provider_id: str) -> dict[str, Any]:
    """Lazy-load the named provider plugin into this process."""
    return _get_client().activate_provider(provider_id)


def health_snapshot() -> dict[str, float]:
    """Return ``{provider_id: health_score}`` for every registered provider."""
    return _get_client().health_snapshot()
