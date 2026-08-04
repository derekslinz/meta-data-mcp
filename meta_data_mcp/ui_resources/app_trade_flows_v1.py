"""``ui://meta-data-mcp/app/trade-flows/v1`` — Phase 5 trade-flows app.

Renders bilateral merchandise + services trade reported to the UN by
200+ national statistical authorities as a coordinated Sankey + treemap
pair:

- **UN Comtrade** (``global_un_comtrade``) — annual / monthly trade
  rows keyed by reporter, partner, commodity (HS/SITC/BEC/EBOPS),
  flow, and value. Sankey lays the rows out as a 3-layer flow
  reporter → commodity → partner; the treemap aggregates the same
  rows by commodity total so dominant categories surface at a glance.

UN Comtrade is the only upstream — the panel exists to make a single
provider's reporter↔partner↔commodity↔value tuples legible at a
glance. Multi-provider variants (e.g. cross-validating tariff lines
against another customs authority) can land later without changing
the bundle, since the wrapped payload contract is provider-agnostic.

The bundle accepts either the wrapped ``{flows: [...]}`` form or a
raw UN Comtrade ``{data: [...]}`` envelope; the normaliser tolerates
both ``camelCase`` (raw API) and ``snake_case`` (wrapped) keys so a
host integrator doesn't have to reshape responses before forwarding
them. See the inline payload contract in the HTML bundle for the
full key list.

postMessage protocol matches the Phase 3 discovery + Phase 5
vulnerability apps — see those modules for the full envelope
description.

Dependencies: D3.js + d3-sankey are pulled from jsDelivr at runtime;
they're heavy enough that inlining the Sankey + treemap arithmetic by
hand would dwarf the bundle. The other Phase 5 apps stay
dependency-free where they can; this one trades two CDN scripts for
a polished layout under 100KB.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files

from mcp import types

from meta_data_mcp.utils import register_ui_resource

URI: str = "ui://meta-data-mcp/app/trade-flows/v1"

# Surfaced in the host's resource catalog. Documents the bidirectional
# postMessage protocol and the bound upstream tool so a host integrator
# can wire app↔host without reading the bundle source.
_DESCRIPTION = (
    "Bilateral trade-flows app: Sankey (reporter → commodity → partner) + "
    "commodity treemap sized by total USD value. Wraps comtrade-trade-data "
    "(UN Comtrade). Filters by year, reporter, and commodity. Hover for "
    "per-flow detail. D3.js + d3-sankey via CDN. "
    "Payload contract: {flows: [{reporter, partner, commodity, value_usd, "
    "year?, flow?, ...}]} or raw UN Comtrade {data: [...]} envelope. "
    "postMessage protocol — "
    "host→app: {type: 'tool_result'|'render', id?, tool?, payload}; "
    "app→host: {type: 'tool_call', id, name, arguments} (Phase 3 invention)."
)

_HTML: str = (
    files("meta_data_mcp.ui_resources") / "app_trade_flows_v1.html"
).read_text(encoding="utf-8")


def register(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> str:
    """Append the trade-flows app resource to the server's catalog.

    Returns the canonical URI so callers (and tests) can assert against it
    without duplicating the constant.
    """
    return register_ui_resource(
        name="app/trade-flows/v1",
        html=_HTML,
        description=_DESCRIPTION,
        resources=resources,
        resources_handlers=resources_handlers,
    )
