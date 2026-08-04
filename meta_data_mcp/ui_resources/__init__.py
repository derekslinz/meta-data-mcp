"""MCP Apps shape primitives and apps (v2.0 Phases 2 & 3).

Two flavors of UI resource live here:

- ``ui://meta-data-mcp/shape/<name>/<version>`` — *primitives*: passive
  renderers for the three canonical payload contracts (timeseries,
  geofeatures, records). Phase 2.

- ``ui://meta-data-mcp/app/<name>/<version>`` — *apps*: interactive
  panels that issue outbound ``tool_call`` messages back to the host.
  Phase 3 shipped ``discovery``; Phase 5 adds ``vulnerability`` /
  ``museum`` / ``molecular`` / ``news-tone`` / ``network-topology``
  and will add ``entity-graph`` / ``trade-flows`` / etc. as follow-ups.

The two public entry points are :func:`register_shapes` and
:func:`register_apps`, called once from the discovery provider during
server boot. Each registers its resources against the server's shared
``RESOURCES`` / ``RESOURCES_HANDLERS`` collections.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp import types

from .app_discovery_v1 import URI as _APP_DISCOVERY_URI
from .app_discovery_v1 import register as _register_discovery_app
from .app_entity_graph_v1 import URI as _APP_ENTITY_GRAPH_URI
from .app_entity_graph_v1 import register as _register_entity_graph_app
from .app_molecular_v1 import URI as _APP_MOLECULAR_URI
from .app_molecular_v1 import register as _register_molecular_app
from .app_museum_v1 import URI as _APP_MUSEUM_URI
from .app_museum_v1 import register as _register_museum_app
from .app_network_topology_v1 import URI as _APP_NETWORK_TOPOLOGY_URI
from .app_network_topology_v1 import register as _register_network_topology_app
from .app_news_tone_v1 import URI as _APP_NEWS_TONE_URI
from .app_news_tone_v1 import register as _register_news_tone_app
from .app_trade_flows_v1 import URI as _APP_TRADE_FLOWS_URI
from .app_trade_flows_v1 import register as _register_trade_flows_app
from .app_vulnerability_v1 import URI as _APP_VULNERABILITY_URI
from .app_vulnerability_v1 import register as _register_vulnerability_app
from .shape_geofeatures_v1 import URI as _SHAPE_GEOFEATURES_URI
from .shape_geofeatures_v1 import register as _register_geofeatures
from .shape_records_v1 import URI as _SHAPE_RECORDS_URI
from .shape_records_v1 import register as _register_records
from .shape_timeseries_v1 import URI as _SHAPE_TIMESERIES_URI
from .shape_timeseries_v1 import register as _register_timeseries

__all__ = ["URIS", "register_apps", "register_shapes"]


# Canonical catalog of every ui:// resource this package ships. Surfaced as
# a flat mapping so callers that need to introspect the full set (tests,
# bundle-budget checks, future tooling like an MCP-Apps lint pass) don't
# have to re-walk every submodule. Architecture review §M1 (v2.1 hygiene
# pass).
#
# Keys are stable identifiers — "<class>/<name>/<version>" — chosen to
# match the dict keys returned by ``register_shapes`` and ``register_apps``
# so the three surfaces stay aligned. Adding a new shape or app means
# adding to BOTH this mapping AND the corresponding register_* function.
# tests/test_ui_resources_catalog.py pins the alignment so a half-wired
# addition fails CI.
URIS: dict[str, str] = {
    "shape/timeseries/v1": _SHAPE_TIMESERIES_URI,
    "shape/geofeatures/v1": _SHAPE_GEOFEATURES_URI,
    "shape/records/v1": _SHAPE_RECORDS_URI,
    "app/discovery/v1": _APP_DISCOVERY_URI,
    "app/trade-flows/v1": _APP_TRADE_FLOWS_URI,
    "app/vulnerability/v1": _APP_VULNERABILITY_URI,
    "app/entity-graph/v1": _APP_ENTITY_GRAPH_URI,
    "app/museum/v1": _APP_MUSEUM_URI,
    "app/molecular/v1": _APP_MOLECULAR_URI,
    "app/news-tone/v1": _APP_NEWS_TONE_URI,
    "app/network-topology/v1": _APP_NETWORK_TOPOLOGY_URI,
}


def register_shapes(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> dict[str, str]:
    """Register all v2 shape primitives on the given server state.

    Called once from the discovery provider (``providers/meta_data_mcp.py``)
    at module import time.

    Returns a dict mapping shape name → URI for callers that want to
    log or surface the wiring; the return value is advisory and may be
    ignored.
    """
    return {
        "timeseries/v1": _register_timeseries(resources, resources_handlers),
        "geofeatures/v1": _register_geofeatures(resources, resources_handlers),
        "records/v1": _register_records(resources, resources_handlers),
    }


def register_apps(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> dict[str, str]:
    """Register all v2 apps on the given server state.

    Apps are split from shapes because they're conceptually different:
    shapes render passively, apps issue outbound ``tool_call`` messages
    back to the host. Splitting the registration entry points makes it
    obvious which discovery-provider tools should bind to which class
    of resource.

    Returns a dict mapping app name → URI; the return value is advisory.
    """
    return {
        "discovery/v1": _register_discovery_app(resources, resources_handlers),
        "trade-flows/v1": _register_trade_flows_app(resources, resources_handlers),
        "vulnerability/v1": _register_vulnerability_app(resources, resources_handlers),
        "entity-graph/v1": _register_entity_graph_app(resources, resources_handlers),
        "museum/v1": _register_museum_app(resources, resources_handlers),
        "molecular/v1": _register_molecular_app(resources, resources_handlers),
        "news-tone/v1": _register_news_tone_app(resources, resources_handlers),
        "network-topology/v1": _register_network_topology_app(
            resources,
            resources_handlers,
        ),
    }
