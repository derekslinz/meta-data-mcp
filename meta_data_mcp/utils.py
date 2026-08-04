"""Back-compat shim.

This module used to hold the HTTP transport, MCP server bootstrap,
serializers, and middleware in one ~1,000-line file. The v2.1 hygiene
pass (architecture review §H1) split that surface into three focused
modules:

- :mod:`meta_data_mcp.transport` — ``http_get`` / ``http_post`` plus the
  TTL cache and retry helpers.
- :mod:`meta_data_mcp.serialize` — ``serialize_for_llm``, ``to_json_text``,
  and the shape-bound serializers (``to_records_text``,
  ``to_geofeatures_text``, ``to_entity_graph_text``).
- :mod:`meta_data_mcp.server` — ``create_mcp_server``,
  ``register_ui_resource``, ``BearerAuthMiddleware``, ``run_server``.

Every name that used to be importable from ``meta_data_mcp.utils`` is
re-exported here, so the 94 in-repo call sites (and any out-of-tree
plugins) keep working unchanged. New code should import from the focused
modules directly.
"""

from __future__ import annotations

import time  # noqa: F401

# Module references retained for back-compat: tests and provider tests
# patch ``utils.time.monotonic`` and ``utils.httpx.get`` to control the
# transport from the outside. Both modules are singletons, so patching
# through ``utils`` mutates the same object ``transport`` uses; the
# re-export only needs to keep the name reachable.
import httpx  # noqa: F401

from meta_data_mcp.serialize import (
    MAX_RESPONSE_CHARS,
    serialize_for_llm,
    to_entity_graph_text,
    to_geofeatures_text,
    to_json_text,
    to_records_text,
)
from meta_data_mcp.server import (
    BearerAuthMiddleware,
    create_mcp_server,
    register_ui_resource,
    run_server,
)
from meta_data_mcp.transport import (
    _response_cache,  # noqa: F401 — tests call ``utils._response_cache.clear()``
    http_get,
    http_post,
)

__all__ = [
    "MAX_RESPONSE_CHARS",
    "BearerAuthMiddleware",
    "create_mcp_server",
    "http_get",
    "http_post",
    "register_ui_resource",
    "run_server",
    "serialize_for_llm",
    "to_entity_graph_text",
    "to_geofeatures_text",
    "to_json_text",
    "to_records_text",
]
