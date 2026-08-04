"""Registration for ``ui://meta-data-mcp/shape/geofeatures/v1``.

The HTML bundle (``shape_geofeatures_v1.html``) ships alongside this module
and is loaded once at import time via :mod:`importlib.resources`. The
:func:`register` entry point appends a ``ui://`` resource to the server's
catalog and wires a handler that returns the bundle HTML text verbatim.

Phase 4 of the v2.0 plan binds providers to this primitive by setting
``Tool._meta = {"ui": {"resourceUri": URI}}``. The contract that
adopters depend on is pinned by ``tests/test_shape_geofeatures.py``.
"""

from __future__ import annotations

from importlib.resources import files

from meta_data_mcp.utils import register_ui_resource

URI = "ui://meta-data-mcp/shape/geofeatures/v1"

# Load the bundle once at import time. Using importlib.resources rather
# than __file__-relative paths so the resource is reachable from both
# editable installs and wheel installs (hatch packages the .html into
# the wheel because it sits inside the ``meta_data_mcp`` package tree).
_HTML = (files("meta_data_mcp.ui_resources") / "shape_geofeatures_v1.html").read_text(
    encoding="utf-8",
)

_DESCRIPTION = (
    "GeoFeatures shape primitive (v1): Leaflet map with marker clustering. "
    "Payload contract: {features: GeoJSON FeatureCollection | "
    "[{lat, lon, attrs}], layers?, facets?}."
)


def register(resources, resources_handlers) -> str:
    """Register the geofeatures/v1 bundle on the given server state.

    Returns the fully-qualified ``ui://`` URI so callers can wire it into
    a tool's ``_meta={"ui": {"resourceUri": ...}}`` declaration.
    """
    return register_ui_resource(
        name="shape/geofeatures/v1",
        html=_HTML,
        description=_DESCRIPTION,
        resources=resources,
        resources_handlers=resources_handlers,
    )
