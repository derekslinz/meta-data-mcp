"""Tests for the ``ui://meta-data-mcp/shape/geofeatures/v1`` primitive.

Phase 2b of the v2.0 presentation-plane plan. The geofeatures shape
primitive is a Leaflet-based map bundle that ~13 providers will bind
to in Phase 4 via ``_meta={"ui": {"resourceUri": <URI>}}``.

These tests pin the contract for the bundle so adopters in Phase 4 can
rely on:

- The resource is registered under the canonical, versioned URI.
- The bundle is non-empty, contains inline JS (``<script``), references
  Leaflet by name, and exposes a root ``<div id="...">`` mount point.
- ``resources/read`` returns the same bytes as the HTML file on disk
  (i.e. the loader and the handler don't drift).
- Bundle weight stays under 100KB — that's the Phase 6b budget the
  plan calls out, enforced here so we catch drift at unit-test time.
"""

from __future__ import annotations

from importlib.resources import files

from pydantic import AnyUrl

from meta_data_mcp.ui_resources import register_shapes
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


def _fresh_state():
    return [], {}


def test_register_shapes_registers_geofeatures_at_expected_uri():
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    assert GEOFEATURES_URI == "ui://meta-data-mcp/shape/geofeatures/v1"
    assert GEOFEATURES_URI in handlers
    uris = [str(r.uri) for r in resources]
    assert GEOFEATURES_URI in uris


def test_register_shapes_registers_canonical_resource_metadata():
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    res = next(r for r in resources if str(r.uri) == GEOFEATURES_URI)
    # MCP Apps requires the ``;profile=mcp-app`` parameter. See
    # tests/test_ui_resource.py for the end-to-end regression.
    assert res.mime_type == "text/html;profile=mcp-app"
    assert res.name == "shape/geofeatures/v1"
    # The description is what surfaces in a host's resource catalog;
    # make sure it conveys what the bundle is.
    assert "geofeatures" in res.description.lower()


def test_bundle_is_non_empty():
    html = _load_bundle()
    assert len(html) > 0, "geofeatures bundle is empty"


def test_bundle_contains_script_tag():
    """The bundle is HTML + inlined JS. Without a <script> the iframe
    has no behavior.
    """
    html = _load_bundle()
    assert "<script" in html.lower(), "bundle contains no <script tag"


def test_bundle_references_leaflet():
    """Pin the library choice so the bundle doesn't silently drift to a
    different mapping library (e.g. Mapbox GL, MapLibre) on a refactor.
    Phase 4 adopters need to keep CSP whitelists in sync; surprises here
    break them.
    """
    html = _load_bundle().lower()
    assert "leaflet" in html, "bundle no longer references Leaflet"


def test_bundle_has_root_div_mount():
    """Iframe host needs a stable mount point. We don't pin the exact
    id (so the bundle can iterate), only the shape: a <div id="...">
    exists somewhere in the bundle.
    """
    html = _load_bundle()
    # Tolerant of attribute ordering / single-vs-double quotes; the
    # presence check is what matters.
    lower = html.lower()
    assert '<div id="' in lower or "<div id='" in lower, (
        'bundle has no <div id="..."> root element for Leaflet to mount on'
    )


def test_handler_returns_same_bytes_as_file_on_disk():
    """The loader (importlib.resources) and the registered handler must
    return identical content. If they ever diverge — e.g. the loader
    starts post-processing the HTML but the file on disk is what gets
    shipped — the host renders something different from what's
    versioned in git.
    """
    on_disk = _load_bundle()
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    served = handlers[GEOFEATURES_URI](AnyUrl(GEOFEATURES_URI))
    assert served == on_disk


def test_bundle_size_under_100kb():
    """Phase 6b bundle-budget enforcement, asserted here at unit-test
    time so we catch growth in the PR that introduces it rather than
    in a later CI sweep. Leaflet itself is CDN-loaded and does NOT
    count toward this 100KB budget.
    """
    html = _load_bundle()
    size_kb = len(html.encode("utf-8")) / 1024
    assert size_kb < 100, f"bundle is {size_kb:.1f}KB (budget: <100KB)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_bundle() -> str:
    """Read the bundle the same way the registration module does, via
    importlib.resources, so packaging issues surface here too. Use the
    Traversable's ``read_text`` directly (not ``Path(str(...))``) so the
    test exercises zipimport / wheel installs, not just filesystem-
    resident source checkouts.
    """
    return (
        files("meta_data_mcp.ui_resources") / "shape_geofeatures_v1.html"
    ).read_text(encoding="utf-8")
