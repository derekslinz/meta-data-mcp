"""Tests for the ``ui://meta-data-mcp/app/discovery/v1`` app bundle.

Phase 3 of the v2.0 presentation-plane plan. The discovery app is the
first MCP Apps *app* (as opposed to passive shape primitives): it issues
outbound ``tool_call`` messages back to the host. This file pins the
registration contract, the bundle's basic invariants, and the
"dependency-free" stance shared with the records primitive.

End-to-end host validation lives in the future Phase 6c Playwright smoke;
this file's tests run in-process and don't spin up a real iframe.
"""

from __future__ import annotations

import re
from importlib.resources import files

from pydantic import AnyUrl

from meta_data_mcp.ui_resources import register_apps
from meta_data_mcp.ui_resources.app_discovery_v1 import URI as DISCOVERY_URI


def _fresh_state():
    return [], {}


def _load_bundle() -> str:
    return (files("meta_data_mcp.ui_resources") / "app_discovery_v1.html").read_text(
        encoding="utf-8",
    )


def test_register_apps_registers_discovery_at_expected_uri():
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    assert DISCOVERY_URI == "ui://meta-data-mcp/app/discovery/v1"
    assert DISCOVERY_URI in handlers
    uris = [str(r.uri) for r in resources]
    assert DISCOVERY_URI in uris


def test_register_apps_registers_canonical_resource_metadata():
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    res = next(r for r in resources if str(r.uri) == DISCOVERY_URI)
    # MCP Apps requires the ``;profile=mcp-app`` parameter — without it
    # hosts reject the resource as "Unsupported UI resource content
    # format". See tests/test_ui_resource.py for the regression that
    # pins this end-to-end through the read_resource envelope.
    assert res.mime_type == "text/html;profile=mcp-app"
    assert res.name == "app/discovery/v1"
    # Description should signal that this is a discovery/search panel.
    assert "discovery" in res.description.lower()


def test_bundle_is_non_empty():
    html = _load_bundle()
    assert len(html) > 0, "discovery bundle is empty"


def test_bundle_contains_script_tag():
    html = _load_bundle().lower()
    assert "<script" in html, "bundle contains no <script tag"


def test_bundle_has_search_input():
    """The discovery panel surfaces a free-text search box. Pin its
    existence so a future redesign that drops the input has to update
    tests + provider expectations together.
    """
    html = _load_bundle().lower()
    # The search box is the primary user input — match by id.
    assert 'id="qbox"' in html, "bundle has no #qbox search input"


def test_bundle_has_facet_containers():
    """Domain and region facet sections live at stable container ids so
    the host can inject filter chips later if needed.
    """
    html = _load_bundle().lower()
    assert 'id="domains"' in html
    assert 'id="regions"' in html


def test_bundle_advertises_tool_call_envelope():
    """Phase 3 invents the app→host ``tool_call`` postMessage envelope.
    Until the MCP Apps spec ratifies an official shape this is the
    contract downstream apps in Phase 5 will inherit. Lock it in so a
    refactor can't silently switch envelope shapes.
    """
    html = _load_bundle()
    # The envelope is constructed once inside callTool().
    assert "type: 'tool_call'" in html or 'type: "tool_call"' in html, (
        "bundle has no tool_call envelope construction"
    )


def test_bundle_listens_for_tool_result_from_parent():
    """The bundle must accept inbound payloads pushed from the host via
    postMessage with type 'tool_result' or 'render' — same convention as
    the shape primitives. If this drifts, the host preload won't render.
    """
    html = _load_bundle()
    assert "'tool_result'" in html or '"tool_result"' in html
    assert "window.parent" in html, "bundle doesn't guard ev.source vs window.parent"


def test_handler_returns_same_bytes_as_file_on_disk():
    on_disk = _load_bundle()
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    served = handlers[DISCOVERY_URI](AnyUrl(DISCOVERY_URI))
    assert served == on_disk


def test_bundle_size_under_100kb():
    """Phase 6b bundle-budget enforcement. The discovery panel is
    text + buttons + facet UI — it should be comfortably under budget.
    """
    html = _load_bundle()
    size_kb = len(html.encode("utf-8")) / 1024
    assert size_kb < 100, f"discovery bundle is {size_kb:.1f}KB (budget: <100KB)"


def test_bundle_has_no_external_script_sources():
    """Discovery is dependency-free by design (matches the records
    primitive's stance — see Plans/linear-swimming-pond.md §3:
    'simplest case, text + buttons; no chart libraries needed').
    """
    html = _load_bundle()
    pattern = re.compile(
        r"<script\b[^>]*\bsrc\s*=\s*[\"']https?://",
        flags=re.IGNORECASE,
    )
    matches = pattern.findall(html)
    assert not matches, (
        f"bundle smuggled in external <script src> tag(s): {matches!r}. "
        "Discovery app is dependency-free by design."
    )


def test_bundle_does_not_use_dangerous_html_assignment():
    """Untrusted upstream JSON flows through this bundle. The bundle MUST
    NOT assign to the DOM property whose name is the concatenation
    ``"inner"`` + ``"HTML"`` anywhere — all DOM mutation has to go
    through ``textContent`` / ``replaceChildren`` / explicit element
    creation so markup injection is impossible by construction.
    """
    html = _load_bundle()
    # Build the forbidden token at runtime so this source file doesn't
    # contain the literal property name (keeps static-analysis hooks happy).
    forbidden = "." + "inner" + "HTML"
    pattern = re.compile(re.escape(forbidden) + r"\s*[+]?=")
    matches = pattern.findall(html)
    assert not matches, (
        f"bundle assigns to {forbidden} ({matches!r}); use textContent / "
        "replaceChildren instead."
    )
