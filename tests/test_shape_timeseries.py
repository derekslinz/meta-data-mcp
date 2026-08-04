"""Tests for the ``ui://meta-data-mcp/shape/timeseries/v1`` shape primitive.

This is the first of three v2.0 shape primitives (Phase 2a). The bundle is
a self-contained HTML+JS resource that renders ``{points, axes, annotations?}``
payloads as a line chart with a profile panel (min/max/mean/stddev/gap-count).

These tests cover the kernel-side contract:

- The resource is registered at the expected canonical URI.
- The bundle is non-empty, contains a script element, and references the
  charting library (Plotly) so it can't silently drift to a different lib.
- The bundle has a root container element.
- The handler returns the same bytes as the file on disk (no surprise
  transformation at registration time).
- Bundle weight stays under the 100KB warning threshold (the plan's
  Gotcha G3 — heavy bundles hurt latency over the MCP transport).
"""

from __future__ import annotations

from importlib.resources import files

import pytest
from pydantic import AnyUrl

from meta_data_mcp.ui_resources import register_shapes
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI

# Plan gotcha G3: bundles ride a single MCP transport payload. Warn early
# if the timeseries bundle drifts toward heavy-handed inlining.
BUNDLE_SIZE_WARN_BYTES = 100 * 1024


def _fresh_state():
    return [], {}


def test_timeseries_uri_is_canonical():
    """The URI is pinned so Phase 4 provider adopters can reference it
    statically. If it changes, every binding must change with it.
    """
    assert TIMESERIES_URI == "ui://meta-data-mcp/shape/timeseries/v1"


def test_register_shapes_registers_timeseries_at_expected_uri():
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)

    uris = {str(r.uri) for r in resources}
    assert TIMESERIES_URI in uris
    assert TIMESERIES_URI in handlers


def test_register_shapes_is_not_idempotent_collisions_raise():
    """Boot-time registration is once-per-process — calling
    ``register_shapes`` twice on the same state MUST raise instead of
    silently dedup'ing. Silent dedup would mask real bugs (e.g. two
    discovery providers booting against shared globals).
    """
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    with pytest.raises(ValueError, match="already registered"):
        register_shapes(resources, handlers)


def test_bundle_is_non_empty():
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    body = handlers[TIMESERIES_URI](AnyUrl(TIMESERIES_URI))
    assert isinstance(body, str)
    assert body.strip(), "timeseries bundle is empty"


def test_bundle_contains_script_element():
    """It's HTML+JS, not just HTML. The presence of a ``<script`` tag is
    the cheapest possible smoke that we shipped the interactive layer.
    """
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    body = handlers[TIMESERIES_URI](AnyUrl(TIMESERIES_URI))
    assert "<script" in body.lower()


def test_bundle_references_plotly():
    """The plan picks Plotly for timeseries (see Plans/linear-swimming-pond.md
    §2a — chosen for bundle weight via CDN). If this test starts failing,
    someone swapped the library and didn't update the plan or the docs that
    Phase 4 adopters use for CSP configuration.
    """
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    body = handlers[TIMESERIES_URI](AnyUrl(TIMESERIES_URI))
    lowered = body.lower()
    assert "plotly" in lowered


def test_bundle_has_root_container_element():
    """The bundle ships SOME ``<div id="...">`` root container so the JS
    can mount a chart. We don't pin the exact id — just that there is one.
    """
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    body = handlers[TIMESERIES_URI](AnyUrl(TIMESERIES_URI))
    # Permissive match: <div id="..."> or <div id='...'>, any whitespace.
    import re

    assert re.search(r"<div\s+[^>]*id\s*=\s*['\"][^'\"]+['\"]", body, re.IGNORECASE), (
        "no <div id=...> root container found in bundle"
    )


def test_handler_returns_bytes_identical_to_file_on_disk():
    """No transformation at registration time — what's in the file is what
    the host receives. This guards against any future 'helpful' templating
    that would make the bundle non-debuggable from the file alone.
    """
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    served = handlers[TIMESERIES_URI](AnyUrl(TIMESERIES_URI))

    # Use Traversable's read_text directly, not Path(str(...)), so the
    # test exercises packaging/install scenarios (zipimport / wheels)
    # not just filesystem-resident source checkouts.
    on_disk = (
        files("meta_data_mcp.ui_resources") / "shape_timeseries_v1.html"
    ).read_text(encoding="utf-8")
    assert served == on_disk


def test_bundle_size_under_warn_threshold():
    """Plan gotcha G3: keep bundles small so they fit in a single MCP
    transport payload with room for the data. Plotly itself is CDN-loaded
    and doesn't count against this.
    """
    # Read via the Traversable API instead of coercing to a filesystem
    # Path (which would fail under zipimport). len(.encode("utf-8")) is
    # the same as stat().st_size for text resources written without BOM.
    size_bytes = len(
        (files("meta_data_mcp.ui_resources") / "shape_timeseries_v1.html")
        .read_text(encoding="utf-8")
        .encode("utf-8"),
    )
    assert size_bytes < BUNDLE_SIZE_WARN_BYTES, (
        f"timeseries bundle is {size_bytes} bytes "
        f"(threshold {BUNDLE_SIZE_WARN_BYTES}). "
        "If this is intentional, raise the threshold; otherwise trim "
        "inline JS / move large helpers to the CDN-loaded library."
    )


def test_resource_is_registered_on_discovery_provider_import():
    """The discovery provider module wires ``register_shapes`` into its
    module-level ``RESOURCES`` / ``RESOURCES_HANDLERS`` at import time so
    the shape resource is visible on a fresh server boot. Verify here
    rather than mocking the discovery provider — this is the integration
    point that matters for Phase 4.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        RESOURCES_HANDLERS as discovery_handlers,
    )

    assert TIMESERIES_URI in discovery_handlers, (
        "timeseries shape resource is not registered on the discovery "
        "provider — register_shapes() must be called from "
        "meta_data_mcp/providers/meta_data_mcp.py at module load."
    )


def test_bundle_documents_plotly_cdn_origin():
    """Phase 4 adopters need to know which CDN origin to allow in
    ``_meta.ui.csp``. The bundle must mention the CDN host inline (in a
    comment or in the script src itself) so the origin is discoverable
    from the bundle alone.
    """
    resources, handlers = _fresh_state()
    register_shapes(resources, handlers)
    body = handlers[TIMESERIES_URI](AnyUrl(TIMESERIES_URI))
    assert "cdn.plot.ly" in body.lower(), (
        "bundle must reference cdn.plot.ly so adopters can configure CSP. "
        "If you moved to a different CDN, update the docs in the bundle."
    )
