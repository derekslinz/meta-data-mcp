"""``ui://meta-data-mcp/shape/timeseries/v1`` — line-chart shape primitive.

The bundle (``shape_timeseries_v1.html``) renders payloads of the form::

    {
      "points":      [{"date": ..., "value": <number>, "series"?: ...}, ...],
      "axes":        {"x": "<label>", "y": "<label>"},
      "annotations": [{"date": ..., "label": ..., "value"?: <number>}, ...]
    }

It's CDN-loaded Plotly + ~16KB of self-contained HTML/JS. The CDN origin
(``cdn.plot.ly``) is documented in the bundle so Phase 4 provider adopters
can wire it into each tool's ``_meta.ui.csp`` correctly.

See ``Plans/linear-swimming-pond.md`` §2a for the coverage matrix.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files

from mcp import types

from meta_data_mcp.utils import register_ui_resource

URI: str = "ui://meta-data-mcp/shape/timeseries/v1"

_DESCRIPTION = (
    "Time-series shape primitive: line chart + profile panel "
    "(min/max/mean/stddev/gaps). Payload: "
    "{points: [{date, value, series?}], axes: {x, y}, annotations?}. "
    "Loads Plotly from cdn.plot.ly — adopters must allow that origin in "
    "the tool's _meta.ui.csp."
)

# Read the bundle once at import time. ``importlib.resources.files`` keeps
# this working both from a source checkout and from an installed wheel.
_HTML: str = (
    files("meta_data_mcp.ui_resources") / "shape_timeseries_v1.html"
).read_text(encoding="utf-8")


def register(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> str:
    """Append the timeseries shape resource to the server's catalog.

    Returns the canonical URI so callers (and tests) can assert against it
    without duplicating the constant.
    """
    return register_ui_resource(
        name="shape/timeseries/v1",
        html=_HTML,
        description=_DESCRIPTION,
        resources=resources,
        resources_handlers=resources_handlers,
    )
