"""Remote HTTP discovery client for meta-data-mcp (SDK-2, Level 1).

Connects to a running ``meta-data-mcp`` SSE server and exposes the same
discovery-tool interface as :class:`meta_data_mcp.sdk.DiscoveryClient`,
but over the network instead of in-process.

Usage::

    import asyncio
    from meta_data_mcp.sdk.remote import RemoteClient

    # One-shot (opens a new MCP session per call)
    client = RemoteClient("https://mcp.example.com", token="secret")
    results = await client.find_providers("earthquake data")

    # Persistent session (more efficient for multiple calls)
    async with RemoteClient("https://mcp.example.com", token="secret") as client:
        results = await client.find_providers("earthquake data", limit=5)
        domains  = await client.list_domains()

Authentication
--------------
Pass the bearer token via ``token=`` or set the
``META_DATA_MCP_AUTH_TOKEN`` environment variable.  Servers configured
without ``META_DATA_MCP_AUTH_TOKEN`` accept unauthenticated connections.

Response format
---------------
``find_providers`` returns the same list-of-dicts structure as the
embedded client — ``{"entry": {...}, "breakdown": {...} | None}`` — but
without a ``score`` field because the MCP tool protocol does not expose
the composite routing score.  Ranking order is preserved.
"""

from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from typing import Any

__all__ = ["RemoteClient"]


class RemoteClient:
    """Remote discovery client that talks to a running meta-data-mcp SSE server.

    Can be used in two modes:

    **One-shot** — opens a fresh MCP session per call; simpler but slower::

        client = RemoteClient(base_url, token=token)
        results = await client.find_providers("floods")

    **Persistent** — enters once, reuses the session across calls::

        async with RemoteClient(base_url, token=token) as client:
            results = await client.find_providers("floods")
            snap    = await client.health_snapshot()

    Args:
        base_url: Root URL of the server (e.g. ``"https://mcp.example.com"``).
                  The ``/sse`` path is appended automatically.
        token:    Bearer token.  Falls back to the ``META_DATA_MCP_AUTH_TOKEN``
                  environment variable if omitted.  Pass ``token=""`` to
                  disable auth explicitly on unauthenticated servers.
        timeout:  Connection timeout in seconds for the initial SSE handshake
                  (default 30 s).
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 30.0,
        auth: Any = None,
    ) -> None:
        """
        Args:
            base_url: Root URL of the server (e.g. ``"https://mcp.example.com"``).
                The ``/sse`` path is appended automatically.
            token: Bearer token for the server's auth middleware.
                Falls back to ``META_DATA_MCP_AUTH_TOKEN`` env var.
                Mutually exclusive with ``auth``.
            timeout: Connection timeout in seconds (default 30 s).
            auth: MCP-compatible OAuth client provider for Authorization Code
                + PKCE flows.  Accepts any object implementing the
                ``mcp.client.sse`` ``auth=`` interface (e.g. a custom
                ``OAuthClientProvider``).  Mutually exclusive with ``token``.
        """
        if token is not None and auth is not None:
            raise ValueError(
                "token and auth are mutually exclusive — use one or the other"
            )
        self._base_url = base_url.rstrip("/")
        self._token = (
            token if token is not None else os.getenv("META_DATA_MCP_AUTH_TOKEN")
        )
        self._auth = auth  # mcp-compatible OAuthClientProvider, or None
        self._timeout = timeout
        self._session: Any = None  # mcp.client.session.ClientSession when active
        self._exit_stack: AsyncExitStack | None = None

    # ------------------------------------------------------------------
    # Context-manager support (persistent session)
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "RemoteClient":
        from mcp.client.session import ClientSession
        from mcp.client.sse import sse_client

        self._exit_stack = AsyncExitStack()
        sse_kwargs: dict[str, Any] = {
            "url": f"{self._base_url}/sse",
            "timeout": self._timeout,
        }
        if self._auth is not None:
            sse_kwargs["auth"] = self._auth
        else:
            sse_kwargs["headers"] = self._headers()
        streams = await self._exit_stack.enter_async_context(sse_client(**sse_kwargs))
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(*streams)
        )
        await self._session.initialize()
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one MCP tool and return the parsed JSON payload.

        Uses the persistent session when called inside ``async with``,
        otherwise opens a one-shot session for this call.
        """
        if self._session is not None:
            result = await self._session.call_tool(name, arguments)
        else:
            from mcp.client.session import ClientSession
            from mcp.client.sse import sse_client

            one_shot_kwargs: dict[str, Any] = {
                "url": f"{self._base_url}/sse",
                "timeout": self._timeout,
            }
            if self._auth is not None:
                one_shot_kwargs["auth"] = self._auth
            else:
                one_shot_kwargs["headers"] = self._headers()
            async with sse_client(**one_shot_kwargs) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)

        if result.isError:
            raise RuntimeError(f"Tool {name!r} returned an MCP error: {result.content}")
        if not result.content:
            return {}

        raw = getattr(result.content[0], "text", None)
        if raw is None:
            return {}
        payload: dict[str, Any] = json.loads(raw)
        if "error" in payload:
            raise RuntimeError(f"Tool {name!r} failed: {payload['error']}")
        return payload

    # ------------------------------------------------------------------
    # Public API (async)
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

        Returns the same list-of-dicts structure as the embedded SDK-1
        client, **except without a ``score`` field** — the MCP tool
        protocol does not expose composite routing scores.  Ranking order
        is preserved (best match first).

        When ``explain=True`` the ``breakdown`` key is populated with the
        per-strategy score dict (token, fuzzy, metadata, semantic, health).
        """
        args: dict[str, Any] = {"limit": limit, "activate_top": 0}
        if query is not None:
            args["query"] = query
        if domain is not None:
            args["domain"] = domain
        if region is not None:
            args["region"] = region
        payload = await self._call_tool("opendata-find-providers", args)
        providers: list[dict[str, Any]] = payload.get("providers", [])
        breakdowns: dict[str, Any] = payload.get("breakdowns", {})
        return [
            {
                "entry": p,
                "breakdown": breakdowns.get(p["id"]) if explain else None,
            }
            for p in providers
        ]

    async def list_domains(self) -> list[str]:
        """Return all domain tags from the server's registry."""
        payload = await self._call_tool("opendata-list-domains", {})
        return payload.get("domains", [])

    async def list_regions(self) -> list[str]:
        """Return all region tags from the server's registry."""
        payload = await self._call_tool("opendata-list-regions", {})
        return payload.get("regions", [])

    async def describe_provider(self, provider_id: str) -> dict[str, Any] | None:
        """Return the registry entry for *provider_id*, or ``None`` if unknown."""
        payload = await self._call_tool(
            "opendata-describe-provider", {"provider_id": provider_id}
        )
        return payload.get("provider") or None

    async def activate_provider(self, provider_id: str) -> dict[str, Any]:
        """Activate the named provider on the remote server.

        Returns the server's status dict (``status``, ``provider_id``,
        ``tools_added``, ``new_tool_names``).
        """
        return await self._call_tool(
            "opendata-activate-provider", {"provider_id": provider_id}
        )

    async def health_snapshot(self) -> dict[str, float]:
        """Return a ``{provider_id: health_score}`` snapshot from the server."""
        payload = await self._call_tool("opendata-health-snapshot", {})
        return payload.get("scores", {})
