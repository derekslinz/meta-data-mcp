"""Tests for the ``ui://meta-data-mcp/app/entity-graph/v1`` app bundle.

Phase 5 of the v2.0 plan. The entity-graph app is the third MCP Apps
*app* (after discovery and vulnerability) and the first one that ships
a CDN-loaded visualisation library (D3.js v7). This file pins the
registration contract, the bundle's basic invariants, the no-innerHTML
security stance shared by every app surface, the <100KB bundle budget,
and the payload contract documented in the bundle's header comment.

End-to-end host validation lives in the Phase 6c Playwright smoke;
this file's tests run in-process and don't spin up a real iframe.
"""

from __future__ import annotations

import re
from importlib.resources import files

from pydantic import AnyUrl

from meta_data_mcp.ui_resources import register_apps
from meta_data_mcp.ui_resources.app_entity_graph_v1 import URI as ENTITY_GRAPH_URI


def _fresh_state():
    return [], {}


def _load_bundle() -> str:
    return (files("meta_data_mcp.ui_resources") / "app_entity_graph_v1.html").read_text(
        encoding="utf-8",
    )


def test_register_apps_registers_entity_graph_at_expected_uri():
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    assert ENTITY_GRAPH_URI == "ui://meta-data-mcp/app/entity-graph/v1"
    assert ENTITY_GRAPH_URI in handlers
    uris = [str(r.uri) for r in resources]
    assert ENTITY_GRAPH_URI in uris


def test_register_apps_registers_canonical_resource_metadata():
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    res = next(r for r in resources if str(r.uri) == ENTITY_GRAPH_URI)
    # MCP Apps requires the ``;profile=mcp-app`` parameter — without it
    # hosts reject the resource as "Unsupported UI resource content
    # format".
    assert res.mime_type == "text/html;profile=mcp-app"
    assert res.name == "app/entity-graph/v1"
    desc = res.description.lower()
    assert "entity" in desc and "graph" in desc
    # All four upstream providers should be discoverable from the
    # description alone — otherwise a host integrator wouldn't know
    # which tools light up this app.
    for upstream in ("openalex", "wikidata", "opensanctions", "crossref"):
        assert upstream in desc, f"description missing upstream signal: {upstream}"


def test_register_apps_returns_entity_graph_mapping():
    """``register_apps`` returns ``{name: uri}`` so callers can log or
    surface the wiring. Pin the entity-graph/v1 entry.
    """
    resources, handlers = _fresh_state()
    result = register_apps(resources, handlers)
    assert result.get("entity-graph/v1") == ENTITY_GRAPH_URI


def test_bundle_is_non_empty():
    assert len(_load_bundle()) > 0


def test_bundle_contains_script_tag():
    assert "<script" in _load_bundle().lower(), "bundle has no <script tag"


def test_bundle_has_graph_container():
    """The graph mount point is the primary render target. Pin its
    existence so a redesign that drops the container has to update
    tests + provider expectations together.
    """
    html = _load_bundle().lower()
    assert 'id="graph"' in html, "bundle has no #graph mount point"


def test_bundle_has_filter_input():
    """The filter/search box is the primary node-filter input."""
    html = _load_bundle().lower()
    assert 'id="filter-input"' in html, "bundle has no #filter-input"


def test_bundle_listens_for_tool_result_from_parent():
    html = _load_bundle()
    assert "'tool_result'" in html or '"tool_result"' in html
    assert "window.parent" in html, "bundle doesn't guard ev.source vs window.parent"


def test_bundle_loads_d3_from_canonical_cdn():
    """D3.js is loaded from cdn.jsdelivr.net per the plan; pin the
    canonical URL pattern so an accidental switch to a different
    (unpinned, or supply-chain-unsafe) CDN is caught.
    """
    html = _load_bundle().lower()
    assert "cdn.jsdelivr.net" in html, "bundle doesn't load D3 from jsdelivr"
    assert "d3@7" in html, "bundle doesn't pin D3 v7"


def test_handler_returns_same_bytes_as_file_on_disk():
    on_disk = _load_bundle()
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    served = handlers[ENTITY_GRAPH_URI](AnyUrl(ENTITY_GRAPH_URI))
    assert served == on_disk


def test_bundle_size_under_100kb():
    """Phase 6b bundle-budget enforcement. The entity-graph app ships
    only the force-layout glue — D3 itself is CDN-loaded — so the
    bundle stays well under budget.
    """
    html = _load_bundle()
    size_kb = len(html.encode("utf-8")) / 1024
    assert size_kb < 100, f"entity-graph bundle is {size_kb:.1f}KB (budget: <100KB)"


def test_bundle_advertises_payload_contract_in_header():
    """The header comment documents the payload contract so a host
    integrator can wire app↔host without reading the bundle source.
    Pin that the three core keys appear so a refactor can't silently
    rename them in the comment without also updating it everywhere.
    """
    html = _load_bundle()
    head = html[: html.find("</head>")] if "</head>" in html else html
    for required in ("nodes", "edges", "label", "type"):
        assert required in head, f"payload contract missing '{required}' in header"


def test_bundle_does_not_use_dangerous_html_assignment():
    """Untrusted upstream JSON (node labels, edge labels, attrs values)
    flows through this bundle. No assignment to the DOM property whose
    name is ``"inner"`` + ``"HTML"`` anywhere — all DOM mutation has
    to go through ``textContent`` / ``replaceChildren`` / explicit
    element creation so markup injection is impossible by construction.
    """
    html = _load_bundle()
    forbidden = "." + "inner" + "HTML"
    pattern = re.compile(re.escape(forbidden) + r"\s*[+]?=")
    matches = pattern.findall(html)
    assert not matches, (
        f"bundle assigns to {forbidden} ({matches!r}); use textContent / "
        "replaceChildren instead."
    )


def test_bundle_only_external_script_is_d3_cdn():
    """The only external <script src> allowed is D3 from jsdelivr.
    Any other external script (e.g. analytics, fonts-loader, other
    chart libs) would expand the supply-chain surface for no good
    reason and should fail the smoke gate.
    """
    html = _load_bundle()
    pattern = re.compile(
        r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)[\"']",
        flags=re.IGNORECASE,
    )
    srcs = pattern.findall(html)
    for src in srcs:
        assert "cdn.jsdelivr.net" in src and "d3" in src.lower(), (
            f"bundle smuggled in non-D3 external script: {src!r}"
        )
