"""MCP server bootstrap.

Owns ``create_mcp_server`` (assembles a low-level
:class:`mcp.server.Server` from the resources/tools/prompts dicts each
provider builds at import time), the ``register_ui_resource`` helper
that wires ``ui://`` resources into the server's catalog, the
``BearerAuthMiddleware`` that protects SSE endpoints, and ``run_server``
which dispatches stdio vs. SSE transports.

Module split out of ``utils.py`` in the v2.1 hygiene pass (architecture
review §H1). ``meta_data_mcp.utils`` re-exports the public symbols so
existing call sites continue to import the same names.
"""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Callable, Sequence

from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from pydantic import AnyUrl

from meta_data_mcp import provenance

log = logging.getLogger(__name__)


def register_ui_resource(
    *,
    name: str,
    html: str,
    description: str,
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[AnyUrl], str | bytes]],
    server_name: str = "meta-data-mcp",
    mime: str = "text/html;profile=mcp-app",
) -> str:
    """Register a ``ui://<server_name>/<name>`` resource backed by static HTML.

    The MCP Apps extension (https://modelcontextprotocol.io/docs/extensions/apps)
    lets a server return interactive UIs by:

    1. Declaring a ``ui://`` resource that holds the HTML (optionally with
       inlined JS and CSS).
    2. Binding a tool to it via ``_meta={"ui": {"resourceUri": ...}}`` so
       the host renders that resource in a sandboxed iframe alongside the
       tool's result. Pass the alias keyword ``_meta=`` — NOT ``meta=``.
       The SDK's ``Tool`` model has ``meta`` aliased to ``_meta`` but does
       not enable ``populate_by_name``, so ``meta=`` silently lands in
       extras and never reaches the wire. See ``tests/test_ui_resource.py``
       for the regression that pins this footgun.

    This helper covers step 1. It appends a ``types.Resource`` to the caller's
    ``resources`` list and registers a handler in ``resources_handlers`` that
    returns the HTML string when the host calls ``resources/read``.

    Args:
        name: Path component for the URI (e.g. ``"discovery"``,
            ``"shape/timeseries/v1"``). Slashes are allowed.
        html: The full resource body. Usually HTML with inlined `<script>`
            and `<style>` for self-contained delivery.
        description: Short human-readable description that surfaces in the
            host's resource catalog.
        resources: The server's ``RESOURCES`` list (mutated in place).
        resources_handlers: The server's ``RESOURCES_HANDLERS`` dict
            (mutated in place).
        server_name: Authority component of the URI. Defaults to
            ``"meta-data-mcp"``; override per server if reusing this helper
            from outside the meta server.
        mime: Content-Type of the resource. Defaults to
            ``"text/html;profile=mcp-app"`` — the MCP Apps standard MIME
            type. Hosts use the ``;profile=mcp-app`` parameter to
            distinguish renderable MCP-UI bundles from arbitrary HTML;
            without it they reject the resource with
            ``"Unsupported UI resource content format"``. See
            https://mcpui.dev/guide/protocol-details.html.

    Returns:
        The fully-qualified ``ui://`` URI as a string, suitable for passing
        as ``_meta={"ui": {"resourceUri": <returned>}}`` on a Tool.

    Raises:
        ValueError: If ``name`` is empty or the resulting URI collides with
            an already-registered handler.
    """
    if not name:
        raise ValueError("name must be non-empty")
    uri = f"ui://{server_name}/{name.lstrip('/')}"
    if uri in resources_handlers:
        raise ValueError(f"ui resource already registered: {uri}")
    resources.append(
        types.Resource(
            uri=AnyUrl(uri),
            name=name,
            description=description,
            mimeType=mime,
        )
    )

    def _handler(_uri: AnyUrl) -> str:
        return html

    resources_handlers[uri] = _handler
    return uri


def create_mcp_server(
    server_name: str,
    resources: list[types.Resource] | None = None,
    resources_handlers: dict[str, Callable[[AnyUrl], str | bytes]] | None = None,
    tools: list[types.Tool] | None = None,
    tools_handlers: dict[
        str,
        Callable[
            [dict[str, Any] | None],
            Sequence[types.TextContent | types.ImageContent | types.EmbeddedResource],
        ],
    ]
    | None = None,
    prompts: list[types.Prompt] | None = None,
    prompts_handlers: dict[
        str,
        Callable[
            [dict[str, str] | None],
            types.GetPromptResult,
        ],
    ]
    | None = None,
    resource_templates: list[types.ResourceTemplate] | None = None,
) -> Server:
    """
    Create a MCP server with the given resources, tools, and prompts.

    Args:
        server_name: The name of the server.
        resources: The list of resources to register.
        resources_handlers: The dictionary of resource handlers.
        tools: The list of tools to register.
        tools_handlers: The dictionary of tools handlers.
        prompts: The list of prompts to register.
        prompts_handlers: The dictionary of prompt handlers.
        resource_templates: The list of resource templates to register.

    Returns:
        The created MCP server.
    """
    _resources = resources or []
    _resources_handlers = resources_handlers or {}
    _tools = tools or []
    _tools_handlers = tools_handlers or {}
    _prompts = prompts or []
    _prompts_handlers = prompts_handlers or {}
    _resource_templates = resource_templates or []

    # instantiate the server
    server = Server(server_name)

    # register resources
    @server.list_resources()
    async def handle_list_resources() -> list[types.Resource]:
        return _resources

    # Build a fast (URI → mimeType) lookup once so the read handler can
    # propagate the registered MIME without rescanning ``_resources`` on
    # every call. Falls back to ``text/plain`` only when a resource was
    # registered without a ``mimeType`` (defensive — every codepath in
    # this repo sets one explicitly).
    _mime_by_uri: dict[str, str] = {
        str(r.uri): (r.mimeType or "text/plain") for r in _resources
    }

    @server.read_resource()
    async def handle_read_resource(
        resource_uri: AnyUrl,
    ) -> list[ReadResourceContents]:
        """Return resource contents with the registered MIME type attached.

        The MCP SDK's ``read_resource`` decorator wraps a bare ``str`` /
        ``bytes`` return into a content envelope, but it defaults the
        envelope's ``mimeType`` to ``text/plain`` (or
        ``application/octet-stream`` for bytes) — completely independent
        of whatever the registered ``Resource.mimeType`` declares. The
        host reads the envelope's ``mimeType``, not the catalog entry's,
        when deciding how to render. An HTML ``ui://`` resource
        registered as ``text/html`` was therefore being served as
        ``text/plain`` on read, and the host refused to mount it.

        Returning ``Iterable[ReadResourceContents]`` lets us pin the
        correct MIME and also silences the SDK's deprecation warning
        about returning bare strings.

        See:
        - ``register_ui_resource`` for where each resource declares its MIME
        - the SDK ``read_resource`` decorator (``mcp.server.lowlevel``)
        - tests/test_ui_resource.py::test_read_resource_returns_text_html_mime
        """
        resource_key = str(resource_uri)

        if resource_key not in _resources_handlers:
            log.error(f"Resource {resource_uri} not found")
            raise AttributeError(f"Resource {resource_uri} not found")

        payload = _resources_handlers[resource_key](resource_uri)
        mime = _mime_by_uri.get(resource_key, "text/plain")
        return [ReadResourceContents(content=payload, mime_type=mime)]

    # register resource templates
    @server.list_resource_templates()
    async def handle_list_resource_templates() -> list[types.ResourceTemplate]:
        return _resource_templates

    # register the tools
    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return _tools

    # register the tools handlers
    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None = None
    ) -> Sequence[types.TextContent | types.ImageContent | types.EmbeddedResource]:
        if name not in _tools_handlers:
            log.error(f"Tool {name} not found")
            raise AttributeError(f"Tool {name} not found")

        try:
            result = await _tools_handlers[name](arguments)
        except Exception as e:
            log.error(f"Error calling tool {name}: {e}")
            raise

        if provenance.is_enabled():
            result = provenance.attach(result, tool_name=name, arguments=arguments)
        return result

    # register the prompts
    @server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        return _prompts

    # register the prompts handlers
    @server.get_prompt()
    async def handle_get_prompt(
        name: str, arguments: dict[str, str] | None = None
    ) -> types.GetPromptResult:
        if name not in _prompts_handlers:
            log.error(f"Prompt {name} not found")
            raise AttributeError(f"Prompt {name} not found")

        try:
            return await _prompts_handlers[name](arguments)
        except Exception as e:
            log.error(f"Error getting prompt {name}: {e}")
            raise

    return server


class BearerAuthMiddleware:
    """Require ``Authorization: Bearer <token>`` on protected ASGI paths.

    Pure ASGI middleware (not BaseHTTPMiddleware) so it does not buffer
    streaming SSE responses. The health check at ``/`` is left open so
    uptime probes work without credentials.

    Accepts two token sources (either is sufficient):
    - Static token via ``token`` parameter (``META_DATA_MCP_AUTH_TOKEN``).
    - OAuth-issued access token via ``oauth_provider`` — verified by calling
      ``provider.verify_access_token(presented)`` asynchronously.

    Both sources may be active simultaneously (coexistence mode).
    """

    def __init__(
        self,
        app: Any,
        token: str | None = None,
        protected_prefixes: Sequence[str] = ("/sse", "/messages"),
        oauth_provider: Any = None,
    ) -> None:
        self.app = app
        self.token = token
        self.protected_prefixes = tuple(protected_prefixes)
        self.oauth_provider = oauth_provider

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or not any(
            scope.get("path", "").startswith(p) for p in self.protected_prefixes
        ):
            await self.app(scope, receive, send)
            return

        auth_header = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                auth_header = value.decode("latin-1")
                break

        scheme = ""
        presented = ""
        parts = auth_header.split(" ", 1)
        if len(parts) == 2:
            scheme, presented = parts

        if scheme.casefold() == "bearer" and presented:
            # Check static token first (constant-time compare).
            if self.token and hmac.compare_digest(presented, self.token):
                await self.app(scope, receive, send)
                return
            # Fall back to OAuth access token verification.
            if self.oauth_provider is not None:
                access_token = await self.oauth_provider.verify_access_token(presented)
                if access_token is not None:
                    await self.app(scope, receive, send)
                    return

        from starlette.responses import JSONResponse

        response = JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="meta-data-mcp"'},
        )
        await response(scope, receive, send)


async def run_server(
    server: Server, transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"
):
    """
    Run the MCP server with the specified transport.

    SSE auth: if ``META_DATA_MCP_AUTH_TOKEN`` is set, requests to ``/sse``
    and ``/messages`` must include ``Authorization: Bearer <token>``. When
    unset, SSE is served unauthenticated (logs a startup warning).
    """
    if transport == "stdio":
        from mcp.server.stdio import stdio_server

        async with stdio_server() as streams:
            await server.run(
                streams[0], streams[1], server.create_initialization_options()
            )
    elif transport == "sse":
        import uvicorn
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.middleware.cors import CORSMiddleware
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        sse = SseServerTransport("/messages")

        class SseApp:
            async def __call__(self, scope, receive, send):
                log.info(f"New SSE connection request from {scope.get('client')}")
                try:
                    async with sse.connect_sse(scope, receive, send) as streams:
                        log.info("SSE connection established, running server...")
                        await server.run(
                            streams[0],
                            streams[1],
                            server.create_initialization_options(),
                        )
                except Exception as e:
                    # Connection closed by client is common and can be ignored or logged at debug
                    log.debug(f"SSE connection error: {e}")
                finally:
                    log.info("SSE connection closed")

        async def root(request):
            return JSONResponse(
                {
                    "status": "running",
                    "server": server.name,
                    "transport": "sse",
                    "endpoints": {"sse": "/sse", "messages": "/messages"},
                }
            )

        # ----------------------------------------------------------------
        # OAuth 2.0 (optional — enabled by META_DATA_MCP_OAUTH_ISSUER)
        # ----------------------------------------------------------------
        oauth_provider = None
        extra_routes: list = []

        oauth_issuer = os.getenv("META_DATA_MCP_OAUTH_ISSUER")
        if oauth_issuer:
            try:
                from mcp.server.auth.routes import (
                    create_auth_routes,
                    create_protected_resource_routes,
                )
            except ImportError as exc:
                raise RuntimeError(
                    "OAuth support requires an MCP SDK version that provides "
                    "`mcp.server.auth.routes.create_protected_resource_routes`. "
                    "Please upgrade the `mcp` package to a compatible version or "
                    "unset META_DATA_MCP_OAUTH_ISSUER to disable OAuth."
                ) from exc
            from mcp.server.auth.settings import ClientRegistrationOptions
            from pydantic import AnyHttpUrl
            from starlette.responses import HTMLResponse
            from starlette.requests import Request

            from meta_data_mcp.oauth_provider import InMemoryOAuthProvider

            oauth_provider = InMemoryOAuthProvider(issuer_url=oauth_issuer)

            import html as _html
            from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

            def _add_query_params(base_url: str, params: dict[str, str]) -> str:
                """Append params to base_url, preserving any existing query string.

                Uses list-of-tuples rather than a dict so that existing duplicate
                or blank-value query params are never silently dropped. Only the
                keys explicitly provided in ``params`` are added/overwritten.
                """
                parts = urlsplit(base_url)
                # Keep all existing params except any that we're explicitly setting,
                # then append the new ones. This preserves duplicate keys and blanks.
                existing = [
                    (k, v)
                    for k, v in parse_qsl(parts.query, keep_blank_values=True)
                    if k not in params
                ]
                merged = existing + list(params.items())
                return urlunsplit(parts._replace(query=urlencode(merged)))

            # Consent page — GET /oauth/consent?session=<token>
            async def consent_get(request: Request) -> HTMLResponse:
                session_token = request.query_params.get("session", "")
                # Use the provider's own peek method so expiry is enforced
                # without consuming the session (POST /approve does that).
                session = oauth_provider.peek_session(session_token)
                if session is None:
                    return HTMLResponse(
                        "<h1>Session expired or invalid.</h1>", status_code=400
                    )
                # HTML-escape all values derived from dynamic client registration
                # to prevent XSS via a maliciously crafted client_name or scope.
                client_name = _html.escape(
                    str(session.get("client_name", session.get("client_id", "?")))
                )
                scopes_html = _html.escape(
                    ", ".join(session.get("scopes", [])) or "(default)"
                )
                session_token_escaped = _html.escape(session_token)
                return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Authorize — meta-data-mcp</title>
<style>body{{font-family:sans-serif;max-width:480px;margin:3rem auto;padding:0 1rem}}
  .card{{border:1px solid #ddd;border-radius:8px;padding:1.5rem}}
  h2{{margin-top:0}} .scope{{color:#555;font-size:.9rem}}
  button{{padding:.6rem 1.4rem;border:none;border-radius:4px;cursor:pointer;font-size:1rem}}
  .approve{{background:#2563eb;color:#fff}} .deny{{background:#e5e7eb;color:#111;margin-left:.5rem}}
</style></head><body>
<div class="card">
  <h2>Authorize access</h2>
  <p><strong>{client_name}</strong> is requesting access to your meta-data-mcp server.</p>
  <p class="scope">Requested scopes: {scopes_html}</p>
  <form method="POST" action="/oauth/consent/approve">
    <input type="hidden" name="session" value="{session_token_escaped}">
    <button type="submit" class="approve">Approve</button>
    <button type="submit" name="deny" value="1" class="deny">Deny</button>
  </form>
</div></body></html>""")

            # Consent approval — POST /oauth/consent/approve
            async def consent_post(request: Request) -> HTMLResponse:
                from starlette.responses import RedirectResponse

                form = await request.form()
                session_token = str(form.get("session", ""))
                session = oauth_provider.consume_session(session_token)
                if session is None:
                    return HTMLResponse(
                        "<h1>Session expired or invalid.</h1>", status_code=400
                    )
                if form.get("deny"):
                    redirect_uri = session["redirect_uri"]
                    params: dict[str, str] = {"error": "access_denied"}
                    if session.get("state"):
                        params["state"] = session["state"]
                    return RedirectResponse(
                        _add_query_params(redirect_uri, params), status_code=302
                    )
                code = oauth_provider.create_authorization_code(session)
                params = {"code": code}
                if session.get("state"):
                    params["state"] = session["state"]
                return RedirectResponse(
                    _add_query_params(session["redirect_uri"], params), status_code=302
                )

            configured_resource_public_url = (
                os.getenv("META_DATA_MCP_PUBLIC_URL", "").strip() or None
            )
            resource_public_url = configured_resource_public_url or oauth_issuer
            validated_resource_url = AnyHttpUrl(resource_public_url)
            validated_oauth_issuer = AnyHttpUrl(oauth_issuer)

            if configured_resource_public_url is None:
                log.info(
                    "META_DATA_MCP_PUBLIC_URL is not set; defaulting protected "
                    "resource URL to META_DATA_MCP_OAUTH_ISSUER (%s)",
                    oauth_issuer,
                )
            elif hmac.compare_digest(
                resource_public_url.rstrip("/"), oauth_issuer.rstrip("/")
            ):
                log.info(
                    "META_DATA_MCP_PUBLIC_URL matches META_DATA_MCP_OAUTH_ISSUER; "
                    "using %s for both protected resource URL and issuer",
                    resource_public_url,
                )
            else:
                log.warning(
                    "META_DATA_MCP_PUBLIC_URL (%s) differs from "
                    "META_DATA_MCP_OAUTH_ISSUER (%s); using "
                    "META_DATA_MCP_PUBLIC_URL for the protected resource URL "
                    "and META_DATA_MCP_OAUTH_ISSUER for the authorization server "
                    "issuer",
                    resource_public_url,
                    oauth_issuer,
                )

            # Serve both RFC 9728 variants:
            # - Base: /.well-known/oauth-protected-resource
            # - Path: /.well-known/oauth-protected-resource/sse
            # Clients (e.g. Claude Desktop) probe both.
            protected_resource_routes = create_protected_resource_routes(
                resource_url=validated_resource_url,
                authorization_servers=[validated_oauth_issuer],
                scopes_supported=["opendata"],
            ) + create_protected_resource_routes(
                resource_url=AnyHttpUrl(resource_public_url.rstrip("/") + "/sse"),
                authorization_servers=[validated_oauth_issuer],
                scopes_supported=["opendata"],
            )
            oauth_routes = create_auth_routes(
                provider=oauth_provider,
                issuer_url=validated_oauth_issuer,
                client_registration_options=ClientRegistrationOptions(
                    enabled=True,
                    valid_scopes=["opendata"],
                    default_scopes=["opendata"],
                ),
            )
            from starlette.responses import RedirectResponse as _RedirectResponse

            async def oidc_discovery(_request):
                return _RedirectResponse(
                    "/.well-known/oauth-authorization-server", status_code=301
                )

            extra_routes = (
                protected_resource_routes
                + oauth_routes
                + [
                    Route(
                        "/.well-known/openid-configuration",
                        endpoint=oidc_discovery,
                        methods=["GET"],
                    ),
                    Route("/oauth/consent", endpoint=consent_get, methods=["GET"]),
                    Route(
                        "/oauth/consent/approve",
                        endpoint=consent_post,
                        methods=["POST"],
                    ),
                ]
            )
            log.info(f"OAuth 2.0 enabled — issuer: {oauth_issuer}")

        app = Starlette(
            debug=False,
            routes=[
                Route("/", endpoint=root),
                Route("/sse", endpoint=SseApp()),
                Mount("/messages", app=sse.handle_post_message),
            ]
            + extra_routes,
        )

        auth_token = os.getenv("META_DATA_MCP_AUTH_TOKEN")
        auth_enabled = bool(auth_token or oauth_provider)
        if auth_enabled:
            app.add_middleware(
                BearerAuthMiddleware,
                token=auth_token,
                oauth_provider=oauth_provider,
            )
            log.info(
                "SSE auth enabled — %s",
                " + ".join(
                    filter(
                        None,
                        [
                            "bearer token" if auth_token else None,
                            "OAuth 2.0" if oauth_provider else None,
                        ],
                    )
                ),
            )
        else:
            log.warning(
                "SSE auth DISABLED — set META_DATA_MCP_AUTH_TOKEN or "
                "META_DATA_MCP_OAUTH_ISSUER to protect /sse and /messages"
            )

        # CORSMiddleware must be added last so it is outermost; this ensures
        # that OPTIONS preflight requests receive CORS headers before reaching
        # BearerAuthMiddleware (which would otherwise reject them with 401).
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        )

        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="debug",
            timeout_keep_alive=65,
            timeout_notify=60,
        )
        uvicorn_server = uvicorn.Server(config)
        await uvicorn_server.serve()
    else:
        raise ValueError(f"Unknown transport: {transport}")


__all__ = [
    "BearerAuthMiddleware",
    "create_mcp_server",
    "register_ui_resource",
    "run_server",
]
