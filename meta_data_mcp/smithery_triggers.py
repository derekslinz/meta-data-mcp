"""Smithery triggers extension for meta-data-mcp.

Implements the ai.smithery/events protocol so meta-data-mcp can push
events to Smithery subscribers when providers are activated/deactivated
or new plugins are created.

Three events are exposed:
  - provider.activated   — fired by opendata_providers_activate
  - provider.deactivated — fired by opendata_providers_deactivate
  - plugin.created       — fired by opendata_plugins_create

Smithery discovers events via ai.smithery/events/list, registers webhooks
via ai.smithery/events/subscribe, and tears them down via
ai.smithery/events/unsubscribe. The server POSTs events directly to the
registered webhook URL, signed with Standard Webhooks HMAC (sha256).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Webhook URL validation (SSRF mitigation)
# ---------------------------------------------------------------------------

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / shared address space
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved / future use
    ipaddress.ip_network("::/128"),  # IPv6 unspecified
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("ff00::/8"),  # IPv6 multicast
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]

_BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain"})


def _validate_webhook_url(url: str) -> str | None:
    """Return an error message if *url* must not be used as a webhook target.

    Rejected cases:
    - Non-HTTPS scheme (blocks http://, ftp://, file://, etc.)
    - Missing or empty host
    - Hostname that is ``localhost`` or a blocked bare name
    - Hostname that is a bare IP address in a private / loopback / link-local
      range (RFC 1918, RFC 4193, RFC 3927, etc.)

    DNS-rebinding attacks are outside the scope of this check; a full
    server-side DNS guard should be added at the HTTP-client layer if required.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "invalid webhook URL"

    if parsed.scheme != "https":
        return "webhook URL must use the https scheme"

    host = parsed.hostname or ""
    if not host:
        return "webhook URL must include a host"

    if host.lower() in _BLOCKED_HOSTNAMES:
        return f"webhook URL host '{host}' is not allowed"

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not a bare IP — hostname-based; allow (DNS rebinding not checked here)
        return None

    # Treat IPv4-mapped IPv6 as its underlying IPv4 address (e.g. ::ffff:127.0.0.1)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped

    for net in _PRIVATE_NETWORKS:
        if addr in net:
            return f"webhook URL host '{host}' is a private or loopback address"
    return None


# ---------------------------------------------------------------------------
# Event catalogue
# ---------------------------------------------------------------------------

EVENTS = [
    {
        "name": "provider.activated",
        "description": "Fires when a provider is activated via opendata_providers_activate.",
        "delivery": ["webhook"],
        "inputSchema": {"type": "object", "properties": {}},
        "payloadSchema": {
            "type": "object",
            "properties": {
                "provider_id": {"type": "string"},
                "tools_added": {"type": "integer"},
                "new_tool_names": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["provider_id"],
        },
    },
    {
        "name": "provider.deactivated",
        "description": "Fires when a provider is deactivated via opendata_providers_deactivate.",
        "delivery": ["webhook"],
        "inputSchema": {"type": "object", "properties": {}},
        "payloadSchema": {
            "type": "object",
            "properties": {
                "provider_id": {"type": "string"},
                "tools_removed": {"type": "integer"},
            },
            "required": ["provider_id"],
        },
    },
    {
        "name": "plugin.created",
        "description": "Fires when a new plugin is created via opendata_plugins_create.",
        "delivery": ["webhook"],
        "inputSchema": {"type": "object", "properties": {}},
        "payloadSchema": {
            "type": "object",
            "properties": {
                "plugin_id": {"type": "string"},
                "tools_added": {"type": "integer"},
                "new_tool_names": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["plugin_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Subscription store
# ---------------------------------------------------------------------------


@dataclass
class Subscription:
    id: str
    event_name: str
    webhook_url: str
    secret: str
    refresh_before: float  # unix timestamp


class SubscriptionStore:
    def __init__(self) -> None:
        self._subs: dict[str, Subscription] = {}

    def subscribe(self, event_name: str, webhook_url: str, secret: str) -> Subscription:
        sub_id = f"sub_{secrets.token_hex(16)}"
        sub = Subscription(
            id=sub_id,
            event_name=event_name,
            webhook_url=webhook_url,
            secret=secret,
            refresh_before=time.time() + 7 * 24 * 3600,  # 1-week TTL
        )
        self._subs[sub_id] = sub
        return sub

    def unsubscribe(self, sub_id: str) -> bool:
        return self._subs.pop(sub_id, None) is not None

    def for_event(self, event_name: str) -> list[Subscription]:
        now = time.time()
        expired = [sid for sid, s in self._subs.items() if now >= s.refresh_before]
        for sid in expired:
            self._subs.pop(sid, None)
        return [s for s in self._subs.values() if s.event_name == event_name]


_store = SubscriptionStore()


# ---------------------------------------------------------------------------
# Webhook delivery (Standard Webhooks signing)
# ---------------------------------------------------------------------------


def _sign(secret: str, msg_id: str, timestamp: int, body: str) -> str:
    """HMAC-SHA256 signature per Standard Webhooks spec."""
    to_sign = f"{msg_id}.{timestamp}.{body}"
    raw_secret = base64.b64decode(secret) if _is_base64(secret) else secret.encode()
    sig = hmac.new(raw_secret, to_sign.encode(), hashlib.sha256).digest()
    return "v1," + base64.b64encode(sig).decode()


def _is_base64(s: str) -> bool:
    try:
        base64.b64decode(s, validate=True)
        return True
    except Exception:
        return False


async def fire_event(event_name: str, data: dict[str, Any]) -> None:
    """Deliver event to all subscribers asynchronously (best-effort)."""
    subs = _store.for_event(event_name)
    if not subs:
        return

    msg_id = f"evt_{secrets.token_hex(16)}"
    timestamp = int(time.time())
    payload = {
        "eventId": msg_id,
        "name": event_name,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp)),
        "data": data,
    }
    body = json.dumps(payload, separators=(",", ":"))

    async with httpx.AsyncClient(timeout=10) as client:
        for sub in subs:
            try:
                sig = _sign(sub.secret, msg_id, timestamp, body)
                await client.post(
                    sub.webhook_url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "webhook-id": msg_id,
                        "webhook-timestamp": str(timestamp),
                        "webhook-signature": sig,
                    },
                )
                log.debug("Delivered %s to %s", event_name, sub.webhook_url)
            except Exception as exc:
                log.warning("Webhook delivery failed for %s: %s", sub.webhook_url, exc)


# ---------------------------------------------------------------------------
# JSON-RPC handler
# ---------------------------------------------------------------------------


def _rpc_ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


async def handle_smithery_rpc(body: dict) -> dict:
    """Handle ai.smithery/events/* JSON-RPC calls."""
    method = body.get("method", "")
    params = body.get("params") or {}
    req_id = body.get("id")

    if method == "ai.smithery/events/list":
        return _rpc_ok(req_id, {"events": EVENTS})

    if method == "ai.smithery/events/subscribe":
        event_name = params.get("event")
        consumer = params.get("consumer", {})
        webhook_url = consumer.get("url")
        secret = consumer.get("secret", secrets.token_hex(32))
        if not event_name or not webhook_url:
            return _rpc_err(req_id, -32602, "event and consumer.url are required")
        url_error = _validate_webhook_url(webhook_url)
        if url_error:
            return _rpc_err(req_id, -32602, f"invalid consumer.url: {url_error}")
        sub = _store.subscribe(event_name, webhook_url, secret)
        return _rpc_ok(
            req_id,
            {
                "id": sub.id,
                "refreshBefore": time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(sub.refresh_before)
                ),
            },
        )

    if method == "ai.smithery/events/unsubscribe":
        sub_id = params.get("id")
        if not sub_id:
            return _rpc_err(req_id, -32602, "id is required")
        _store.unsubscribe(sub_id)
        return _rpc_ok(req_id, {"success": True})

    return _rpc_err(req_id, -32601, f"Method not found: {method}")


# ---------------------------------------------------------------------------
# ASGI middleware — intercepts before MCP server validates request types
# ---------------------------------------------------------------------------


class SmitheryTriggersMiddleware:
    """Intercept ai.smithery/* JSON-RPC calls before the MCP SDK rejects them.

    The MCP Python SDK validates every incoming request against its known
    type union. Unknown methods fail validation before dispatch, so they
    never reach request_handlers. This middleware reads the POST body once,
    checks the method, handles Smithery calls directly, and rebuilds the
    receive channel for everything else so downstream handlers see a normal
    unconsumed body.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    # Smithery JSON-RPC messages are tiny; inspect at most 64 KB and
    # avoid buffering arbitrarily large request bodies in memory.
    _MAX_INSPECT_BYTES = 65_536

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        # Buffer only enough body to inspect for ai.smithery/* methods.
        replay_events: list[dict[str, Any]] = []
        inspect_chunks: list[bytes] = []
        inspected = 0
        too_large = False
        while True:
            event = await receive()
            if event.get("type") == "http.disconnect":
                return
            chunk = event.get("body", b"")
            more_body = event.get("more_body", False)
            replay_events.append(
                {"type": "http.request", "body": chunk, "more_body": more_body}
            )
            remaining = max(self._MAX_INSPECT_BYTES - inspected, 0)
            if remaining:
                inspect_chunks.append(chunk[:remaining])
                inspected += min(len(chunk), remaining)
            # Stop inspection once the current chunk crosses the cap or the
            # cap is exactly reached and the client indicates more body remains.
            if len(chunk) > remaining or (
                inspected >= self._MAX_INSPECT_BYTES and more_body
            ):
                too_large = True
                break
            if not more_body:
                break

        # Check if it's a Smithery method (only when body is small enough).
        if not too_large:
            body_bytes = b"".join(inspect_chunks)
            try:
                data = json.loads(body_bytes)
                method = data.get("method", "")
                if isinstance(method, str) and method.startswith("ai.smithery/"):
                    result = await handle_smithery_rpc(data)
                    response_body = json.dumps(result).encode()
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 200,
                            "headers": [
                                [b"content-type", b"application/json"],
                                [b"content-length", str(len(response_body)).encode()],
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": response_body})
                    return
            except (json.JSONDecodeError, AttributeError):
                pass

        # Not a Smithery call — replay captured events and, for oversized bodies,
        # continue reading directly from the original receive channel.
        # Use an Event so replay_receive can return http.disconnect promptly
        # after the downstream handler finishes sending the response.
        replay_index = 0
        upstream_body_complete = False
        response_done = asyncio.Event()

        async def replay_receive() -> dict:
            nonlocal replay_index, upstream_body_complete
            if replay_index < len(replay_events):
                event = replay_events[replay_index]
                replay_index += 1
                if event.get("type") == "http.request" and not event.get(
                    "more_body", False
                ):
                    upstream_body_complete = True
                return event
            if too_large and not upstream_body_complete:
                event = await receive()
                if event.get("type") == "http.request" and not event.get(
                    "more_body", False
                ):
                    upstream_body_complete = True
                return event
            # Wait until the downstream handler finishes streaming the response
            # before signalling disconnect — prevents premature ASGI termination.
            await response_done.wait()
            return {"type": "http.disconnect"}

        async def send_wrapper(event: dict) -> None:
            await send(event)
            if event.get("type") == "http.response.body" and not event.get(
                "more_body", False
            ):
                response_done.set()

        try:
            await self.app(scope, replay_receive, send_wrapper)
        finally:
            response_done.set()
