"""Tests for the ``ui://meta-data-mcp/app/museum/v1`` app bundle.

Phase 5 of the v2.0 plan. The museum app wraps the Met Museum Open
Access ``met-search`` + ``met-get-object`` tools into one image-grid
panel with click-through provenance detail.

The bundle is a CC0-friendly art-discovery surface — the Met Open
Access program publishes every public-domain image under Creative
Commons Zero, which makes a "show me the Met's collection" panel
the natural Phase 5 image-driven app.

End-to-end host validation lives in the Phase 6c Playwright smoke
suite; this file's tests run in-process and don't spin up an iframe.
"""

from __future__ import annotations

import re
from importlib.resources import files

from pydantic import AnyUrl

from meta_data_mcp.ui_resources import register_apps
from meta_data_mcp.ui_resources.app_museum_v1 import URI as MUSEUM_URI


def _fresh_state():
    return [], {}


def _load_bundle() -> str:
    return (files("meta_data_mcp.ui_resources") / "app_museum_v1.html").read_text(
        encoding="utf-8",
    )


def test_register_apps_registers_museum_at_expected_uri():
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    assert MUSEUM_URI == "ui://meta-data-mcp/app/museum/v1"
    assert MUSEUM_URI in handlers
    uris = [str(r.uri) for r in resources]
    assert MUSEUM_URI in uris


def test_register_apps_registers_canonical_resource_metadata():
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    res = next(r for r in resources if str(r.uri) == MUSEUM_URI)
    # MCP Apps requires the ``;profile=mcp-app`` parameter — without it
    # hosts reject the resource as "Unsupported UI resource content
    # format". See tests/test_ui_resource.py for the regression that
    # pins this end-to-end through the read_resource envelope.
    assert res.mime_type == "text/html;profile=mcp-app"
    assert res.name == "app/museum/v1"
    desc = res.description.lower()
    assert "museum" in desc or "met" in desc, (
        "description should advertise the Met Museum surface"
    )
    # Image grid + provenance is the plan's promised form for this app —
    # a host integrator should be able to learn the panel shape from
    # the catalog description alone.
    for signal in ("image", "grid"):
        assert signal in desc, f"description missing form signal: {signal}"


def test_register_apps_returns_museum_mapping():
    """``register_apps`` returns ``{name: uri}`` so callers can log or
    surface the wiring. Pin the ``museum/v1`` entry so a future refactor
    that drops the return value (or renames the key) is caught at
    unit-test time.
    """
    resources, handlers = _fresh_state()
    result = register_apps(resources, handlers)
    assert result.get("museum/v1") == MUSEUM_URI


def test_bundle_is_non_empty():
    assert len(_load_bundle()) > 0


def test_bundle_contains_script_tag():
    assert "<script" in _load_bundle().lower(), "bundle has no <script tag"


def test_bundle_has_search_input():
    """The free-text search input drives the panel — it's the primary
    user input for filtering the on-screen grid. Pin its existence so
    a redesign that drops the input has to update tests and provider
    expectations together.
    """
    html = _load_bundle().lower()
    assert 'id="search-input"' in html, "bundle has no #search-input"


def test_bundle_has_grid_mount_point():
    """The grid is the panel's centerpiece. Pin its mount id so a
    refactor that renames it has to update the smoke ROOT_SELECTORS
    entry in lockstep.
    """
    html = _load_bundle().lower()
    assert 'id="grid"' in html, "bundle has no #grid mount"


def test_bundle_advertises_tool_call_envelope():
    """The museum app reuses the Phase 3 ``tool_call`` envelope. If a
    refactor switches envelope shapes, every Phase 5 app's matching
    test will fail together — pin one in each app so a divergence
    between them is impossible silently.
    """
    html = _load_bundle()
    assert "type: 'tool_call'" in html or 'type: "tool_call"' in html, (
        "bundle has no tool_call envelope construction"
    )


def test_bundle_listens_for_tool_result_from_parent():
    html = _load_bundle()
    assert "'tool_result'" in html or '"tool_result"' in html
    assert "window.parent" in html, "bundle doesn't guard ev.source vs window.parent"


def test_bundle_uses_lazy_loading_on_images():
    """The grid can render hundreds of thumbnails at once — without
    ``loading="lazy"`` every image hits the network on first paint,
    which kills perceived performance and tanks the host's iframe
    budget. Pin the attribute so a refactor that drops it surfaces
    here rather than as a host-side regression.
    """
    html = _load_bundle().lower()
    assert 'loading="lazy"' in html or "loading: 'lazy'" in html, (
        "bundle does not opt images into lazy loading"
    )


def test_handler_returns_same_bytes_as_file_on_disk():
    on_disk = _load_bundle()
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    served = handlers[MUSEUM_URI](AnyUrl(MUSEUM_URI))
    assert served == on_disk


def test_bundle_size_under_100kb():
    """Phase 6b bundle-budget enforcement. The museum app is mostly
    CSS grid + a thin JS renderer (no chart libraries, no markdown
    parser), so it should be well under budget.
    """
    html = _load_bundle()
    size_kb = len(html.encode("utf-8")) / 1024
    assert size_kb < 100, f"museum bundle is {size_kb:.1f}KB (budget: <100KB)"


def test_bundle_has_no_external_script_sources():
    """Dependency-free by design — see the plan §5 visualisations note:
    image grids are achievable in pure CSS, no chart library required.
    """
    html = _load_bundle()
    pattern = re.compile(
        r"<script\b[^>]*\bsrc\s*=\s*[\"']https?://",
        flags=re.IGNORECASE,
    )
    matches = pattern.findall(html)
    assert not matches, (
        f"bundle smuggled in external <script src> tag(s): {matches!r}. "
        "Museum app is dependency-free by design."
    )


def test_bundle_does_not_use_dangerous_html_assignment():
    """Met Museum metadata (titles, artist names, provenance,
    descriptions) flows through this bundle untrusted. No assignment
    to the DOM property whose name is ``"inner"`` + ``"HTML"`` anywhere
    — all DOM mutation has to go through ``textContent`` /
    ``replaceChildren`` / explicit element creation so markup injection
    is impossible by construction.
    """
    html = _load_bundle()
    forbidden = "." + "inner" + "HTML"
    pattern = re.compile(re.escape(forbidden) + r"\s*[+]?=")
    matches = pattern.findall(html)
    assert not matches, (
        f"bundle assigns to {forbidden} ({matches!r}); use textContent / "
        "replaceChildren instead."
    )
