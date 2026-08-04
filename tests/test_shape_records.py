"""Tests for the ``ui://meta-data-mcp/shape/records/v1`` primitive.

Phase 2c of the v2.0 presentation-plane plan. The records shape primitive
is a dependency-free HTML table bundle that ~29 providers will bind to in
Phase 4 via ``_meta={"ui": {"resourceUri": <URI>}}``.

These tests pin the contract for the bundle so adopters in Phase 4 can
rely on:

- The resource is registered under the canonical, versioned URI.
- The bundle is non-empty, contains inline JS (``<script``), exposes a
  ``<table`` element for tabular rendering, and has a root ``<div id="...">``
  mount point.
- ``resources/read`` returns the same bytes as the HTML file on disk
  (i.e. the loader and the handler don't drift).
- Bundle weight stays under 100KB — that's the Phase 6b budget the plan
  calls out, enforced here so we catch drift at unit-test time.
- NO external ``<script src="http...">`` references — this primitive is
  dependency-free by design (the plan explicitly contrasts records with
  timeseries/geofeatures on this point).
"""

from __future__ import annotations

import re
from importlib.resources import files

from pydantic import AnyUrl

from meta_data_mcp.ui_resources import register_shapes
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


def _fresh_state():
    return [], {}


def test_register_shapes_registers_records_at_expected_uri():
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    assert RECORDS_URI == "ui://meta-data-mcp/shape/records/v1"
    assert RECORDS_URI in handlers
    uris = [str(r.uri) for r in resources]
    assert RECORDS_URI in uris


def test_register_shapes_registers_canonical_resource_metadata():
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    res = next(r for r in resources if str(r.uri) == RECORDS_URI)
    # MCP Apps requires the ``;profile=mcp-app`` parameter. See
    # tests/test_ui_resource.py for the end-to-end regression.
    assert res.mime_type == "text/html;profile=mcp-app"
    assert res.name == "shape/records/v1"
    # The description is what surfaces in a host's resource catalog;
    # make sure it conveys what the bundle is.
    assert "records" in res.description.lower()


def test_bundle_is_non_empty():
    html = _load_bundle()
    assert len(html) > 0, "records bundle is empty"


def test_bundle_contains_script_tag():
    """The bundle is HTML + inlined JS. Without a <script> the iframe
    has no behavior.
    """
    html = _load_bundle()
    assert "<script" in html.lower(), "bundle contains no <script tag"


def test_bundle_contains_table_element():
    """Records is a table-based primitive. The static markup may not
    contain a fully-populated <table> (rows are rendered dynamically),
    but the bundle MUST include a <table at minimum so the rendering
    mounts somewhere predictable. Pin it so a refactor to a card/grid
    layout would have to update tests + provider expectations together.
    """
    html = _load_bundle().lower()
    assert "<table" in html, "bundle has no <table element"


def test_bundle_has_root_div_mount():
    """Iframe host needs a stable mount point. We don't pin the exact
    id (so the bundle can iterate), only the shape: a <div id="...">
    exists somewhere in the bundle.
    """
    html = _load_bundle()
    lower = html.lower()
    assert '<div id="' in lower or "<div id='" in lower, (
        'bundle has no <div id="..."> root element'
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
    served = handlers[RECORDS_URI](AnyUrl(RECORDS_URI))
    assert served == on_disk


def test_bundle_size_under_100kb():
    """Phase 6b bundle-budget enforcement, asserted here at unit-test
    time so we catch growth in the PR that introduces it rather than
    in a later CI sweep. Records is the dependency-free primitive — it
    should be comfortably under budget.
    """
    html = _load_bundle()
    size_kb = len(html.encode("utf-8")) / 1024
    assert size_kb < 100, f"bundle is {size_kb:.1f}KB (budget: <100KB)"


def test_bundle_has_no_external_script_sources():
    """Records is the dependency-free primitive by design (per
    Plans/linear-swimming-pond.md §2c: 'plain HTML table + vanilla JS,
    no dependency'). If a refactor smuggles in a CDN dependency, the
    plan's stance on CSP whitelisting (Gotcha G2) and the contrast with
    timeseries (Plotly via CDN) / geofeatures (Leaflet self-hosted)
    quietly breaks. Lock the no-external-deps invariant down here.
    """
    html = _load_bundle()
    # Match <script ... src="http..."> or src='http...' tolerantly:
    # the regex flags any <script tag with an src attribute pointing
    # to an http(s) URL.
    pattern = re.compile(
        r"<script\b[^>]*\bsrc\s*=\s*[\"']https?://",
        flags=re.IGNORECASE,
    )
    matches = pattern.findall(html)
    assert not matches, (
        f"bundle smuggled in external <script src> tag(s): {matches!r}. "
        "Records is dependency-free by design."
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_bundle() -> str:
    """Read the bundle the same way the registration module does, via
    importlib.resources, so packaging issues surface here too. Use the
    Traversable's ``read_text`` directly (not ``Path(str(...))``) so
    this works under zipimport / wheels, not just source checkouts.
    """
    return (files("meta_data_mcp.ui_resources") / "shape_records_v1.html").read_text(
        encoding="utf-8",
    )
