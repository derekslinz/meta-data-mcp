"""Smithery triggers extension for meta-data-mcp.

Implements the ai.smithery/events protocol so meta-data-mcp can push
events to Smithery subscribers when providers are activated/deactivated
or new plugins are created.

Three events are exposed:
  - provider.activated   — fired by opendata.providers.activate
  - provider.deactivated — fired by opendata.providers.deactivate
  - plugin.created       — fired by opendata.plugins.create

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
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event catalogue
# ---------------------------------------------------------------------------

EVENTS = [
    {
        "name": "provider.activated",
        "description": "Fires when a provider is activated via opendata.providers.activate.",
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
        "description": "Fires when a provider is deactivated via opendata.providers.deactivate.",
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
        "description": "Fires when a new plugin is created via opendata.plugins.create.",
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

    # Smithery JSON-RPC messages are tiny; anything larger is a real MCP
    # payload and should never be inspected. Cap at 64 KB to prevent OOM.
    _MAX_INSPECT_BYTES = 65_536

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        # Buffer the request body up to the inspection cap.
        # Once the cap is exceeded we stop accumulating — additional chunks
        # are drained (to satisfy the ASGI receive contract) but discarded
        # so we never buffer more than _MAX_INSPECT_BYTES in memory.
        chunks: list[bytes] = []
        total = 0
        too_large = False
        while True:
            event = await receive()
            if event.get("type") == "http.disconnect":
                return
            chunk = event.get("body", b"")
            if not too_large:
                chunks.append(chunk)
                total += len(chunk)
                if total > self._MAX_INSPECT_BYTES:
                    too_large = True
            # Drain remaining chunks without storing them.
            if not event.get("more_body", False):
                break
        body_bytes = b"".join(chunks)

        # Check if it's a Smithery method (only when body is small enough).
        if not too_large:
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

        # Not a Smithery call — rebuild a receive channel from the buffered body.
        # Use an Event to detect when the response is fully sent so replay_receive
        # returns http.disconnect promptly rather than sleeping arbitrarily long.
        body_sent = False
        response_done = asyncio.Event()

        async def replay_receive() -> dict:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
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

        await self.app(scope, replay_receive, send_wrapper)
