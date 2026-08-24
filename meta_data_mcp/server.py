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
import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    BlobResourceContents,
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    InputRequiredResult,
    ListPromptsResult,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    TextResourceContents,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from meta_data_mcp import citations, provenance

log = logging.getLogger(__name__)

ToolUnstructuredContent = Iterable[types.ContentBlock]
ToolStructuredContent = dict[str, Any]
ToolCallResult = (
    ToolUnstructuredContent
    | ToolStructuredContent
    | tuple[ToolUnstructuredContent, ToolStructuredContent]
    | types.CallToolResult
)


def register_ui_resource(
    *,
    name: str,
    html: str,
    description: str,
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
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
            uri=uri,
            name=name,
            description=description,
            mimeType=mime,
        ),
    )

    def _handler(_uri: str) -> str:
        return html

    resources_handlers[uri] = _handler
    return uri


def create_mcp_server(
    server_name: str,
    resources: list[types.Resource] | None = None,
    resources_handlers: dict[str, Callable[[str], str | bytes]] | None = None,
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
    """Create a MCP server with the given resources, tools, and prompts.

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

    from pathlib import Path

    from meta_data_mcp import __version__
    from meta_data_mcp import contribute as _contribute

    _notice = _contribute.startup_notice(Path(__file__).resolve().parents[1])
    if _notice:
        log.info(_notice)

    # Build a fast (URI → mimeType) lookup once so the read handler can
    # propagate the registered MIME without rescanning ``_resources`` on
    # every call. Falls back to ``text/plain`` only when a resource was
    # registered without a ``mimeType`` (defensive — every codepath in
    # this repo sets one explicitly).
    _mime_by_uri: dict[str, str] = {
        str(r.uri): (r.mime_type or "text/plain") for r in _resources
    }

    async def _handle_list_resources(
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None = None,
    ) -> ListResourcesResult:
        return ListResourcesResult(resources=_resources)

    async def _handle_read_resource(
        ctx: ServerRequestContext,
        params: ReadResourceRequestParams,
    ) -> ReadResourceResult:
        """Return resource contents with the registered MIME type attached.

        The MCP SDK v2 wraps a bare ``str`` / ``bytes`` return into a
        content envelope that defaults to ``text/plain`` — independent of
        the registered ``Resource.mimeType``.  Returning a
        ``ReadResourceResult`` with explicit ``mimeType`` ensures the host
        sees the correct content type (critical for ``ui://`` HTML
        resources that would otherwise be rejected as
        ``text/plain``).
        """
        resource_key = str(params.uri)

        if resource_key not in _resources_handlers:
            log.error("Resource %s not found", resource_key)
            raise AttributeError(f"Resource {resource_key} not found")

        payload = _resources_handlers[resource_key](params.uri)
        mime = _mime_by_uri.get(resource_key, "text/plain")

        # Use the correct content type based on payload type
        if isinstance(payload, bytes):
            content = BlobResourceContents(
                uri=resource_key, mime_type=mime, blob=payload
            )
        else:
            content = TextResourceContents(
                uri=resource_key, mime_type=mime, text=str(payload)
            )

        return ReadResourceResult(contents=[content])

    async def _handle_list_resource_templates(
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None = None,
    ) -> ListResourceTemplatesResult:
        return ListResourceTemplatesResult(resourceTemplates=_resource_templates)

    async def _handle_list_tools(
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None = None,
    ) -> ListToolsResult:
        return ListToolsResult(tools=_tools)

    async def _handle_call_tool(
        ctx: ServerRequestContext,
        params: CallToolRequestParams,
    ) -> CallToolResult | InputRequiredResult:
        name = params.name
        arguments = params.arguments
        if name not in _tools_handlers:
            log.error("Tool %s not found", name)
            raise AttributeError(f"Tool {name} not found")

        # recording_span handles the citations env gate internally: when
        # disabled it yields a list that record() never populates.
        with citations.recording_span() as source_records:
            result = await _tools_handlers[name](arguments)
            # is still open — materializing lazy iterables here means
            # any http_get a generator performs is still recorded.

            # If handler returns CallToolResult directly, pass through
            if isinstance(result, types.CallToolResult):
                # Handler controls isError and friends — attach layers don't apply; pass through.
                return result

            # If handler returns a tuple (content, structured), use both
            if isinstance(result, tuple) and len(result) == 2:
                content, structured = result
                content = list(content)
                if source_records:
                    content = citations.attach(content, source_records)
                if provenance.is_enabled():
                    content = provenance.attach(
                        content,
                        tool_name=name,
                        arguments=arguments,
                    )
                return types.CallToolResult(
                    content=content, structured_content=structured, is_error=False
                )

            # Materialize generators/iterables
            if hasattr(result, "__iter__") and not isinstance(
                result, (str, bytes, dict)
            ):
                try:
                    result = list(result)
                except TypeError:
                    pass  # Not iterable

            # If handler returns a list of content, wrap in CallToolResult
            if isinstance(result, list):
                content = result
                if source_records:
                    content = citations.attach(content, source_records)
                if provenance.is_enabled():
                    content = provenance.attach(
                        content,
                        tool_name=name,
                        arguments=arguments,
                    )
                return types.CallToolResult(content=content, is_error=False)

            # If handler returns a dict, use as structured_content
            if isinstance(result, dict):
                content = []
                if source_records:
                    # Create a content block to carry citations
                    content = [
                        types.TextContent(
                            type="text", text=json.dumps(result, indent=2)
                        )
                    ]
                    content = citations.attach(content, source_records)
                if provenance.is_enabled():
                    if not content:
                        content = [
                            types.TextContent(
                                type="text", text=json.dumps(result, indent=2)
                            )
                        ]
                    content = provenance.attach(
                        content,
                        tool_name=name,
                        arguments=arguments,
                    )
                return types.CallToolResult(
                    content=content, structured_content=result, is_error=False
                )

            # Fallback: wrap in CallToolResult
            content = [types.TextContent(type="text", text=str(result))]
            if source_records:
                content = citations.attach(content, source_records)
            if provenance.is_enabled():
                content = provenance.attach(
                    content,
                    tool_name=name,
                    arguments=arguments,
                )
            return types.CallToolResult(content=content, is_error=False)

    async def _handle_list_prompts(
        ctx: ServerRequestContext,
        params: PaginatedRequestParams | None = None,
    ) -> ListPromptsResult:
        return ListPromptsResult(prompts=_prompts)

    async def _handle_get_prompt(
        ctx: ServerRequestContext,
        params: GetPromptRequestParams,
    ) -> GetPromptResult | InputRequiredResult:
        name = params.name
        arguments = params.arguments
        if name not in _prompts_handlers:
            log.error("Prompt %s not found", name)
            raise AttributeError(f"Prompt {name} not found")

        try:
            return await _prompts_handlers[name](arguments)
        except Exception as e:
            log.error("Error getting prompt %s: %s", name, e)
            raise

    server = Server(
        server_name,
        version=__version__,
        on_list_resources=_handle_list_resources,
        on_read_resource=_handle_read_resource,
        on_list_resource_templates=_handle_list_resource_templates,
        on_list_tools=_handle_list_tools,
        on_call_tool=_handle_call_tool,
        on_list_prompts=_handle_list_prompts,
        on_get_prompt=_handle_get_prompt,
    )

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
        resource_metadata_url: str | None = None,
        rate_limiter: Any = None,
    ) -> None:
        self.app = app
        self.token = token
        self.protected_prefixes = tuple(protected_prefixes)
        self.oauth_provider = oauth_provider
        self.resource_metadata_url = resource_metadata_url
        # Optional per-user throttle, applied only on the OAuth path (the
        # static operator token is not rate-limited). Identity is the verified
        # email when the magic-link gate bound one, else the access token.
        self.rate_limiter = rate_limiter

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
                    if self.rate_limiter is not None:
                        email_for_token = getattr(
                            self.oauth_provider,
                            "email_for_token",
                            None,
                        )
                        identity = (
                            email_for_token(presented) if email_for_token else None
                        ) or presented
                        if not self.rate_limiter.allow(identity):
                            retry_after = self.rate_limiter.retry_after(identity)
                            resp = JSONResponse(
                                {"error": "rate_limited"},
                                status_code=429,
                                headers={"Retry-After": str(retry_after)},
                            )
                            await resp(scope, receive, send)
                            return
                    await self.app(scope, receive, send)
                    return

        if self.resource_metadata_url:
            www_auth = (
                f'Bearer realm="meta-data-mcp",'
                f' resource_metadata="{self.resource_metadata_url}"'
            )
        else:
            www_auth = 'Bearer realm="meta-data-mcp"'

        response = JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": www_auth},
        )
        await response(scope, receive, send)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log HTTP requests and responses with timing."""

    def __init__(
        self,
        app,
        *,
        logger: logging.Logger | None = None,
        log_request_body: bool = False,
        log_response_body: bool = False,
        excluded_paths: set[str] | None = None,
    ):
        super().__init__(app)
        self.logger = logger or logging.getLogger("meta_data_mcp.access")
        self.log_request_body = log_request_body
        self.log_response_body = log_response_body
        self.excluded_paths = excluded_paths or {"/health", "/ready", "/live"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip excluded paths
        if request.url.path in self.excluded_paths:
            return await call_next(request)

        # Generate request ID for correlation
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        start_time = time.perf_counter()
        client_host = request.client.host if request.client else "unknown"
        method = request.method
        path = request.url.path
        query = str(request.url.query) if request.url.query else ""

        # Log request
        request_log = {
            "request_id": request_id,
            "client": client_host,
            "method": method,
            "path": path,
            "query": query,
            "user_agent": request.headers.get("user-agent", ""),
        }

        if self.log_request_body and method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
                if body:
                    request_log["body"] = body.decode("utf-8")[:1000]  # Limit size
            except Exception:
                request_log["body"] = "<unreadable>"

        self.logger.info("request", extra=request_log)

        # Process request
        try:
            response = await call_next(request)
        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.logger.error(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "client": client_host,
                    "method": method,
                    "path": path,
                    "duration_ms": round(duration_ms, 2),
                    "error": str(e),
                },
            )
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000

        # Log response
        response_log = {
            "request_id": request_id,
            "client": client_host,
            "method": method,
            "path": path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
        }

        if self.log_response_body:
            # Note: This consumes the response body, so we'd need to buffer it
            # For now, just log the status
            pass

        if response.status_code >= 400:
            self.logger.warning("response", extra=response_log)
        else:
            self.logger.info("response", extra=response_log)

        # Add request ID to response headers
        response.headers["x-request-id"] = request_id
        return response


async def run_server(
    server: Server,
    transport: str = "stdio",
    port: int = 8000,
    host: str = "127.0.0.1",
):
    """Run the MCP server with the specified transport.

    SSE auth: if ``META_DATA_MCP_AUTH_TOKEN`` is set, requests to ``/sse``
    and ``/messages`` must include ``Authorization: Bearer <token>``. When
    unset, SSE is served unauthenticated (logs a startup warning).
    """
    if transport == "stdio":
        from mcp.server.stdio import stdio_server

        async with stdio_server() as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )
    elif transport == "sse":
        from contextlib import asynccontextmanager

        import uvicorn
        from mcp.server.sse import SseServerTransport
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from starlette.applications import Starlette
        from starlette.middleware.cors import CORSMiddleware
        from starlette.routing import Mount, Route

        sse = SseServerTransport("/messages")
        streamable_manager = StreamableHTTPSessionManager(server, stateless=True)

        class SseApp:
            async def __call__(self, scope, receive, send):
                method = scope.get("method", "").upper()
                if method == "POST":
                    # StreamableHTTP transport — used by Claude.ai and newer clients.
                    await streamable_manager.handle_request(scope, receive, send)
                else:
                    # Traditional SSE transport — GET /sse → long-lived event stream.
                    log.info(f"New SSE connection from {scope.get('client')}")
                    try:
                        async with sse.connect_sse(scope, receive, send) as streams:
                            await server.run(
                                streams[0],
                                streams[1],
                                server.create_initialization_options(),
                            )
                    except Exception as e:
                        log.debug(f"SSE connection error: {e}")
                    finally:
                        log.info("SSE connection closed")

        @asynccontextmanager
        async def lifespan(_app):
            async with streamable_manager.run():
                yield

        async def root(request):
            return JSONResponse(
                {
                    "status": "running",
                    "server": server.name,
                    "transport": "sse",
                    "endpoints": {
                        "sse": "/sse",
                        "messages": "/messages",
                        "streamable": "/",
                    },
                },
            )

        async def streamable_root(scope, receive, send):
            if scope.get("method", "").upper() == "POST":
                await streamable_manager.handle_request(scope, receive, send)
            else:
                await root(scope, receive, send)

        # ----------------------------------------------------------------
        # OAuth 2.0 (optional — enabled by META_DATA_MCP_OAUTH_ISSUER)
        # ----------------------------------------------------------------
        oauth_provider = None
        extra_routes: list = []
        rate_limiter = None

        oauth_issuer = os.getenv("META_DATA_MCP_OAUTH_ISSUER")
        if not oauth_issuer and os.getenv(
            "META_DATA_MCP_EMAIL_GATE",
            "",
        ).strip().lower() in ("1", "true", "yes", "on"):
            log.warning(
                "META_DATA_MCP_EMAIL_GATE is set but META_DATA_MCP_OAUTH_ISSUER "
                "is not — the email gate rides the OAuth flow and is DISABLED "
                "until an issuer is configured.",
            )
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
                    "unset META_DATA_MCP_OAUTH_ISSUER to disable OAuth.",
                ) from exc
            from mcp.server.auth.settings import ClientRegistrationOptions
            from pydantic import AnyHttpUrl

            from meta_data_mcp.oauth_provider import InMemoryOAuthProvider

            # Optional durable OAuth state: set META_DATA_MCP_OAUTH_DB to a
            # SQLite path and registered clients + issued tokens (with their
            # email bindings) plus a sign-in audit log survive restarts.
            oauth_persistence = None
            oauth_db_path = os.getenv("META_DATA_MCP_OAUTH_DB", "").strip()
            if oauth_db_path:
                from meta_data_mcp.oauth_persistence import SqliteOAuthPersistence

                oauth_persistence = SqliteOAuthPersistence(oauth_db_path)
                log.info("OAuth persistence enabled — SQLite at %s", oauth_db_path)

            oauth_provider = InMemoryOAuthProvider(
                issuer_url=oauth_issuer,
                persistence=oauth_persistence,
            )

            # ----------------------------------------------------------------
            # Magic-link email gate (optional — META_DATA_MCP_EMAIL_GATE=1)
            # ----------------------------------------------------------------
            # When enabled, the consent page collects an email and emails a
            # single-use sign-in link; access is granted only after the link is
            # clicked, and the verified email becomes the rate-limit identity.
            # When disabled, the original one-click Approve consent is used.
            email_gate_enabled = os.getenv(
                "META_DATA_MCP_EMAIL_GATE",
                "",
            ).strip().lower() in ("1", "true", "yes", "on")
            magic_store = None
            emailer = None
            magic_link_ip_limiter = None
            magic_link_email_limiter = None
            if email_gate_enabled:
                from meta_data_mcp.auth_gate import (
                    DEFAULT_MAGIC_LINK_PER_EMAIL_MAX,
                    DEFAULT_MAGIC_LINK_PER_IP_MAX,
                    DEFAULT_RATE_LIMIT_RPM,
                    MAGIC_LINK_REQUEST_WINDOW_SECONDS,
                    MagicLinkStore,
                    RateLimiter,
                )
                from meta_data_mcp.emailer import EmailBackend, Emailer

                magic_store = MagicLinkStore()
                emailer = Emailer.from_env()
                # The console backend logs the magic link instead of sending
                # it — anyone with log access could sign in. Refuse to start a
                # gated server on it unless the operator explicitly opts in.
                if emailer.backend is EmailBackend.CONSOLE and os.getenv(
                    "META_DATA_MCP_ALLOW_CONSOLE_EMAIL",
                    "",
                ).strip().lower() not in ("1", "true", "yes", "on"):
                    raise RuntimeError(
                        "META_DATA_MCP_EMAIL_GATE is enabled but no email backend "
                        "is configured (set META_DATA_MCP_RESEND_API_KEY or "
                        "META_DATA_MCP_SMTP_HOST). The console backend logs magic "
                        "links in plaintext and is unsafe for production. To use "
                        "it for local testing, set "
                        "META_DATA_MCP_ALLOW_CONSOLE_EMAIL=1.",
                    )
                rpm = DEFAULT_RATE_LIMIT_RPM
                raw_rpm = os.getenv("META_DATA_MCP_RATE_LIMIT_RPM")
                if raw_rpm is not None:
                    try:
                        rpm = int(raw_rpm)
                    except ValueError:
                        log.warning(
                            "META_DATA_MCP_RATE_LIMIT_RPM must be an integer; "
                            "using default %d",
                            DEFAULT_RATE_LIMIT_RPM,
                        )
                rate_limiter = RateLimiter(rpm=rpm)
                # Separate anti-abuse throttles for the sign-in *request*
                # endpoint (email-bomb / spray protection), windowed over 15 min.
                magic_link_ip_limiter = RateLimiter(
                    rpm=DEFAULT_MAGIC_LINK_PER_IP_MAX,
                    window_seconds=MAGIC_LINK_REQUEST_WINDOW_SECONDS,
                )
                magic_link_email_limiter = RateLimiter(
                    rpm=DEFAULT_MAGIC_LINK_PER_EMAIL_MAX,
                    window_seconds=MAGIC_LINK_REQUEST_WINDOW_SECONDS,
                )
                log.info(
                    "Email gate enabled — magic-link sign-in via %s backend, %s",
                    emailer.backend.value,
                    f"{rpm} req/min per user" if rpm > 0 else "rate limiting off",
                )

            from meta_data_mcp.consent_routes import ConsentRoutes

            consent_routes = ConsentRoutes(
                oauth_provider=oauth_provider,
                issuer_url=oauth_issuer,
                email_gate_enabled=email_gate_enabled,
                magic_store=magic_store,
                emailer=emailer,
                ip_rate_limiter=magic_link_ip_limiter,
                email_rate_limiter=magic_link_email_limiter,
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
                resource_public_url.rstrip("/"),
                oauth_issuer.rstrip("/"),
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
            # The MCP SDK hardcodes token_endpoint_auth_methods_supported to
            # ["client_secret_post", "client_secret_basic"], omitting "none".
            # Claude's OAuth client checks this list and refuses to proceed for
            # public clients (PKCE-only, no secret) when "none" is missing.
            # Override the well-known endpoint with a corrected copy before
            # the SDK's route so Starlette's first-match wins.
            _issuer_base = str(validated_oauth_issuer).rstrip("/")

            async def patched_oauth_metadata(request: Request) -> JSONResponse:
                payload = {
                    "issuer": str(validated_oauth_issuer),
                    "authorization_endpoint": f"{_issuer_base}/authorize",
                    "token_endpoint": f"{_issuer_base}/token",
                    "registration_endpoint": f"{_issuer_base}/register",
                    "scopes_supported": ["opendata"],
                    "response_types_supported": ["code"],
                    "grant_types_supported": [
                        "authorization_code",
                        "refresh_token",
                    ],
                    "token_endpoint_auth_methods_supported": [
                        "client_secret_post",
                        "client_secret_basic",
                        "none",
                    ],
                    "code_challenge_methods_supported": ["S256"],
                }
                return JSONResponse(
                    payload,
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type, Authorization",
                        "Cache-Control": "no-store",
                    },
                )

            from starlette.responses import RedirectResponse as _RedirectResponse

            async def oidc_discovery(_request):
                return _RedirectResponse(
                    "/.well-known/oauth-authorization-server",
                    status_code=301,
                )

            extra_routes = (
                [
                    # Must precede oauth_routes so Starlette's first-match wins:
                    # the SDK hardcodes "none" out of token_endpoint_auth_methods_supported.
                    Route(
                        "/.well-known/oauth-authorization-server",
                        endpoint=patched_oauth_metadata,
                        methods=["GET", "OPTIONS"],
                    ),
                ]
                + protected_resource_routes
                + oauth_routes
                + [
                    Route(
                        "/.well-known/openid-configuration",
                        endpoint=oidc_discovery,
                        methods=["GET"],
                    ),
                ]
                + consent_routes.routes()
            )
            log.info(f"OAuth 2.0 enabled — issuer: {oauth_issuer}")

        from meta_data_mcp.smithery_triggers import SmitheryTriggersMiddleware

        app = Starlette(
            debug=False,
            lifespan=lifespan,
            routes=[
                Route("/", endpoint=streamable_root, methods=["GET", "POST"]),
                Route("/sse", endpoint=SseApp()),
                Mount("/messages", app=sse.handle_post_message),
            ]
            + extra_routes,
        )
        app.add_middleware(SmitheryTriggersMiddleware)

        # Access logging middleware (added early to capture all requests)
        log_request_body = os.getenv("META_DATA_MCP_LOG_REQUEST_BODY", "").lower() in (
            "1",
            "true",
            "yes",
        )
        log_response_body = os.getenv(
            "META_DATA_MCP_LOG_RESPONSE_BODY", ""
        ).lower() in ("1", "true", "yes")
        app.add_middleware(
            AccessLogMiddleware,
            logger=logging.getLogger("meta_data_mcp.access"),
            log_request_body=log_request_body,
            log_response_body=log_response_body,
        )

        auth_token = os.getenv("META_DATA_MCP_AUTH_TOKEN")
        auth_enabled = bool(auth_token or oauth_provider)
        if auth_enabled:
            app.add_middleware(
                BearerAuthMiddleware,
                token=auth_token,
                oauth_provider=oauth_provider,
                rate_limiter=rate_limiter,
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
                    ),
                ),
            )
        else:
            log.warning(
                "SSE auth DISABLED — set META_DATA_MCP_AUTH_TOKEN or "
                "META_DATA_MCP_OAUTH_ISSUER to protect /sse and /messages",
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

        # CRITICAL: meta-data-mcp uses process-level mutable state for plugin
        # activation (TOOLS list, TOOLS_HANDLERS dict, _active_providers set).
        # Multiple uvicorn workers share nothing — activation in one worker is
        # invisible to others, and OAuth tokens issued by one worker cannot be
        # verified by another. Always run with a single worker (the default)
        # for SSE deployments; do not use multiple uvicorn workers unless
        # plugin activation state and token verification are moved to shared
        # cross-worker storage.
        _UVICORN_LEVELS = frozenset(
            {"critical", "error", "warning", "warn", "info", "debug"},
        )
        _requested = os.environ.get("LOG_LEVEL", "info").lower()
        _uvicorn_level = _requested if _requested in _UVICORN_LEVELS else "info"
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=_uvicorn_level,
            timeout_keep_alive=65,
            timeout_notify=60,
        )
        uvicorn_server = uvicorn.Server(config)
        await uvicorn_server.serve()
    else:
        raise ValueError(f"Unknown transport: {transport}")


__all__ = [
    "AccessLogMiddleware",
    "BearerAuthMiddleware",
    "create_mcp_server",
    "register_ui_resource",
    "run_server",
]
