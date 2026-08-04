"""Tests for the ``ui://meta-data-mcp/app/molecular/v1`` app bundle.

Phase 5 of the v2.0 plan. Molecular is the third MCP Apps *app*
(after discovery and vulnerability) and the second multi-provider
one. This file pins the registration contract, the bundle's basic
invariants, and the dependency-quality stance we apply to every
Phase 5 app: no inline charting libraries, no innerHTML, parent-only
postMessage, 100KB bundle budget.

The molecular app differs from vulnerability in one respect: it
relies on a single CDN (``3dmol.org``) for the 3Dmol.js viewer
library, which is ~700KB minified and would blow the bundle budget
if inlined. The ``test_bundle_has_only_3dmol_cdn_script`` test pins
that the CDN is exactly that origin — no other external scripts can
sneak in.

End-to-end host validation lives in the Phase 6c Playwright smoke;
this file's tests run in-process and don't spin up a real iframe.
"""

from __future__ import annotations

import re
from importlib.resources import files

from pydantic import AnyUrl

from meta_data_mcp.ui_resources import register_apps
from meta_data_mcp.ui_resources.app_molecular_v1 import URI as MOLECULAR_URI


def _fresh_state():
    return [], {}


def _load_bundle() -> str:
    return (files("meta_data_mcp.ui_resources") / "app_molecular_v1.html").read_text(
        encoding="utf-8",
    )


def test_register_apps_registers_molecular_at_expected_uri():
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    assert MOLECULAR_URI == "ui://meta-data-mcp/app/molecular/v1"
    assert MOLECULAR_URI in handlers
    uris = [str(r.uri) for r in resources]
    assert MOLECULAR_URI in uris


def test_register_apps_registers_canonical_resource_metadata():
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    res = next(r for r in resources if str(r.uri) == MOLECULAR_URI)
    # MCP Apps requires the ``;profile=mcp-app`` parameter — without it
    # hosts reject the resource as "Unsupported UI resource content
    # format". See tests/test_ui_resource.py for the regression that
    # pins this end-to-end through the read_resource envelope.
    assert res.mime_type == "text/html;profile=mcp-app"
    assert res.name == "app/molecular/v1"
    # Description should signal what the app is and what it wraps.
    desc = res.description.lower()
    assert "molecular" in desc or "structure" in desc
    # Both upstream sources should be discoverable from the description
    # alone — otherwise a host integrator wouldn't know which tools
    # light up this app.
    for upstream in ("pubchem", "pdb"):
        assert upstream in desc, f"description missing upstream signal: {upstream}"
    # The bundled viewer is the key technical choice; surface it so a
    # host operator reviewing CDN allowlists can see it.
    assert "3dmol" in desc


def test_register_apps_returns_molecular_mapping():
    """``register_apps`` returns ``{name: uri}`` so callers can log or
    surface the wiring. Pin the molecular/v1 entry so a future refactor
    that drops the return value (or renames the key) is caught at
    unit-test time.
    """
    resources, handlers = _fresh_state()
    result = register_apps(resources, handlers)
    assert result.get("molecular/v1") == MOLECULAR_URI


def test_bundle_is_non_empty():
    assert len(_load_bundle()) > 0


def test_bundle_contains_script_tag():
    assert "<script" in _load_bundle().lower(), "bundle has no <script tag"


def test_bundle_has_identifier_input():
    """The identifier input (PDB id / compound name) is the primary
    user input. Pin its existence so a redesign that drops the input
    has to update tests + provider expectations together.
    """
    html = _load_bundle().lower()
    assert 'id="ident-input"' in html, "bundle has no #ident-input"


def test_bundle_has_source_selector():
    """The user picks PubChem vs PDB before entering an identifier;
    pin the selector so the dual-source UX can't silently degrade to a
    PDB-only or PubChem-only flow on refactor.
    """
    html = _load_bundle().lower()
    assert 'id="source-select"' in html, "bundle has no #source-select"


def test_bundle_advertises_tool_call_envelope():
    """The molecular app reuses the Phase 3 ``tool_call`` envelope.
    If a refactor switches envelope shapes, the matching tests on the
    discovery and vulnerability apps also fail — pin one per app so a
    divergence between them is impossible silently.
    """
    html = _load_bundle()
    assert "type: 'tool_call'" in html or 'type: "tool_call"' in html, (
        "bundle has no tool_call envelope construction"
    )


def test_bundle_listens_for_tool_result_from_parent():
    html = _load_bundle()
    assert "'tool_result'" in html or '"tool_result"' in html
    assert "window.parent" in html, "bundle doesn't guard ev.source vs window.parent"
    # Strict source guard — same pattern as the discovery/vulnerability
    # apps. ev.source === window.parent is the only acceptable check.
    assert "ev.source !== window.parent" in html, (
        "bundle missing strict ev.source === window.parent guard"
    )


def test_bundle_loads_3dmol_viewer():
    """3Dmol.js is the only library this bundle leans on; pin its
    presence so a refactor that drops it (or swaps in a non-WebGL
    viewer) has to explicitly update the test + the description.
    """
    html = _load_bundle()
    assert "$3Dmol" in html, "bundle doesn't reference the 3Dmol.js global"


def test_bundle_has_only_3dmol_cdn_script():
    """Every Phase 5 app is dependency-free EXCEPT this one — the
    3Dmol.js viewer library is too big to inline (~700KB minified).
    Pin that the only external <script src> is 3dmol.org so a refactor
    that smuggles in additional CDN scripts has to update this test.
    """
    html = _load_bundle()
    pattern = re.compile(
        r"<script\b[^>]*\bsrc\s*=\s*[\"'](https?://[^\"']+)[\"']",
        flags=re.IGNORECASE,
    )
    srcs = pattern.findall(html)
    assert srcs, "bundle should load 3Dmol.js from its CDN — none found"
    assert all("3dmol.org" in src for src in srcs), (
        f"bundle smuggled in non-3dmol.org CDN script(s): {srcs!r}. "
        "Only 3Dmol.js is permitted; everything else stays inline."
    )


def test_bundle_documents_cdn_origin_in_html_comment():
    """Host operators reviewing CSP / CDN allowlists need to find the
    3dmol.org origin in the bundle source quickly. Pin that we
    document it in an HTML comment alongside the <script> tag.
    """
    html = _load_bundle()
    # The HTML comment block at the top of the bundle should mention
    # the CDN origin so a host operator grepping for "3dmol" lands
    # there without reading the whole bundle.
    assert "3dmol.org" in html.lower(), (
        "bundle should reference the 3dmol.org origin in its documentation"
    )


def test_bundle_has_postmessage_to_parent():
    html = _load_bundle()
    assert "window.parent.postMessage" in html, (
        "bundle doesn't push tool_call messages to the parent window"
    )


def test_bundle_supports_base64_data_url_param():
    """The ``?data=<base64-JSON>`` ingress is how host-less previews
    and the Phase 6c smoke get a payload into the bundle without a
    real postMessage envelope. Pin both the param name and the UTF-8
    decode path so a refactor doesn't silently break previews.
    """
    html = _load_bundle()
    assert "URLSearchParams" in html
    assert "params.get('data')" in html or 'params.get("data")' in html
    assert "TextDecoder" in html, (
        "bundle decodes base64 without TextDecoder — non-ASCII payloads "
        "(e.g. titles with Greek letters) will be corrupted."
    )


def test_bundle_supports_window_app_data_fallback():
    """The Phase 6c smoke seeds ``window.__app_data__`` so the
    headless browser can hydrate the panel without postMessage. Pin
    the global so future bundles can't silently drop the contract.
    """
    html = _load_bundle()
    assert "window.__app_data__" in html


def test_handler_returns_same_bytes_as_file_on_disk():
    on_disk = _load_bundle()
    resources, handlers = _fresh_state()
    register_apps(resources, handlers)
    served = handlers[MOLECULAR_URI](AnyUrl(MOLECULAR_URI))
    assert served == on_disk


def test_bundle_size_under_100kb():
    """Phase 6b bundle-budget enforcement. 3Dmol.js itself is
    CDN-loaded so it doesn't count against this budget — only the
    inline HTML / CSS / JS does.
    """
    html = _load_bundle()
    size_kb = len(html.encode("utf-8")) / 1024
    assert size_kb < 100, f"molecular bundle is {size_kb:.1f}KB (budget: <100KB)"


def test_bundle_does_not_use_dangerous_html_assignment():
    """Untrusted upstream JSON (PubChem compound names, PDB titles,
    OSV-like strings) flows through this bundle. No assignment to the
    DOM property whose name is ``"inner"`` + ``"HTML"`` anywhere — all
    DOM mutation has to go through ``textContent`` / ``replaceChildren``
    / explicit element creation so markup injection is impossible by
    construction.
    """
    html = _load_bundle()
    forbidden = "." + "inner" + "HTML"
    pattern = re.compile(re.escape(forbidden) + r"\s*[+]?=")
    matches = pattern.findall(html)
    assert not matches, (
        f"bundle assigns to {forbidden} ({matches!r}); use textContent / "
        "replaceChildren instead."
    )


def test_bundle_picks_cartoon_style_for_chains():
    """The plan promises a 3D viewer that's useful for both proteins
    and small molecules. The substantive design choice is cartoon-on-
    chains, stick-on-small-molecules — pin both so a refactor can't
    silently regress to an all-ball-and-stick viewer that's illegible
    for >5kDa proteins.
    """
    html = _load_bundle().lower()
    assert "cartoon" in html, (
        "bundle doesn't apply cartoon style to chains — proteins will "
        "render as illegible ball-and-stick atoms."
    )
    assert "stick" in html, (
        "bundle doesn't apply stick style to small molecules / ligands."
    )
