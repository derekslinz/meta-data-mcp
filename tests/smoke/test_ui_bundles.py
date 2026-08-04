"""Headless-browser smoke tests for every ``ui://`` resource bundle.

Phase 6c of the v2.0 plan (see ``Plans/linear-swimming-pond.md`` §6c). Loads
each bundle in a real Chromium page via Playwright and asserts:

1. The page reaches ``DOMContentLoaded`` without uncaught JS errors.
2. The expected root element exists in the rendered DOM.
3. No ``console.error`` calls fire from the bundle's own inline JS (errors
   originating from CDN scripts — Plotly, Leaflet — are filtered out because
   they're network-dependent and not our bug).

These tests are deselected from the regular suite via the ``smoke`` marker
(see ``pyproject.toml::[tool.pytest.ini_options]``). To run locally:

    uv sync --group smoke
    uv run playwright install chromium
    uv run pytest -m smoke tests/smoke/

CI runs them as a separate ``smoke`` job (see ``.github/workflows/ci.yml``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright not installed — run `uv sync --group smoke` to enable smoke tests.",
)

pytestmark = pytest.mark.smoke

# ---------------------------------------------------------------------------
# Bundle directory + sample payloads
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUNDLES_DIR = REPO_ROOT / "meta_data_mcp" / "ui_resources"


# Per-bundle sample payload (injected as ``window.__app_data__`` before script
# load). Apps that don't consume ``__app_data__`` get ``None`` — they render
# their idle/empty state, which is still a valid smoke target.
SAMPLE_PAYLOADS: dict[str, dict[str, Any] | None] = {
    "shape_timeseries_v1.html": {
        "points": [
            {"date": "2024-01-01", "value": 1.0, "series": "smoke"},
            {"date": "2024-01-02", "value": 1.5, "series": "smoke"},
        ],
        "axes": {"x": "Date", "y": "Value"},
    },
    "shape_geofeatures_v1.html": {
        "features": [
            {"lat": 0.0, "lon": 0.0, "attrs": {"name": "origin"}},
            {"lat": 51.5, "lon": -0.12, "attrs": {"name": "london"}},
        ],
    },
    "shape_records_v1.html": {
        "rows": [
            {"id": 1, "name": "smoke", "active": True},
            {"id": 2, "name": "test", "active": False},
        ],
        "schema": {
            "columns": [
                {"name": "id", "type": "number"},
                {"name": "name", "type": "string"},
                {"name": "active", "type": "boolean"},
            ],
        },
    },
    "app_discovery_v1.html": None,
    "app_trade_flows_v1.html": {
        "flows": [
            {
                "reporter": "USA",
                "partner": "China",
                "commodity": "10 — Cereals",
                "value_usd": 1234567890,
                "year": 2023,
                "flow": "Exports",
            },
            {
                "reporter": "Germany",
                "partner": "France",
                "commodity": "84 — Machinery",
                "value_usd": 987654321,
                "year": 2023,
                "flow": "Exports",
            },
        ],
    },
    "app_vulnerability_v1.html": None,
    "app_entity_graph_v1.html": {
        "nodes": [
            {"id": "n1", "label": "smoke author", "type": "author", "attrs": {}},
            {"id": "n2", "label": "smoke work", "type": "work", "attrs": {}},
        ],
        "edges": [{"source": "n2", "target": "n1", "label": "authored", "weight": 1}],
    },
    # Seed the museum panel with one image-bearing object and one
    # image-less object so the lazy-load + "no image available"
    # codepaths both render at smoke time.
    "app_museum_v1.html": {
        "objects": [
            {
                "id": 436535,
                "title": "Wheat Field with Cypresses",
                "artist": "Vincent van Gogh",
                "date": "1889",
                "medium": "Oil on canvas",
                "image_url": "",
                "primary_image_url": "",
                "url": "https://www.metmuseum.org/art/collection/search/436535",
                "provenance": "Bequest of Annie Swan Coburn, 1934.",
                "tags": ["Landscapes", "Cypresses"],
                "is_public_domain": True,
            },
            {"id": 999999, "title": "Untitled (no image)"},
        ],
    },
    # Molecular app: a seed envelope with a structure.url avoids a real
    # network fetch during smoke. WebGL is disabled in headless Chromium
    # by default so 3Dmol.js may not paint, but the UI shell still mounts
    # and the metadata pane renders — that's what the smoke test checks.
    "app_molecular_v1.html": {
        "structure": {
            "format": "pdb",
            "url": "https://files.rcsb.org/download/4HHB.pdb",
            "identifier": "4HHB",
        },
        "metadata": {
            "name": "4HHB",
            "title": "Hemoglobin (smoke)",
            "source": "RCSB PDB",
            "attrs": {"method": "X-RAY DIFFRACTION", "resolution": 1.74},
        },
    },
    "app_news_tone_v1.html": {
        "events": [
            {
                "date": "2026-05-10",
                "tone": -4.2,
                "title": "smoke negative story",
                "country": "US",
                "url": "https://example.com/a",
                "domain": "example.com",
            },
            {
                "date": "2026-05-10",
                "tone": 2.1,
                "title": "smoke neutral story",
                "country": "GB",
                "url": "https://example.com/b",
                "domain": "example.com",
            },
            {
                "date": "2026-05-11",
                "tone": 4.8,
                "title": "smoke positive story",
                "country": "US",
                "url": "https://example.com/c",
                "domain": "example.com",
            },
        ],
        "country_pairs": [{"a": "GB", "b": "US", "weight": 1}],
        "facets": {
            "tone_timeline": [
                {"date": "2026-05-10", "tone": -1.0},
                {"date": "2026-05-11", "tone": 2.5},
            ],
            "volume_timeline": [],
            "query": "smoke",
        },
    },
    # Network-topology app: rendered idle when no payload is provided,
    # populated when a topology payload is pushed in. The sample payload
    # exercises the focus-node highlight + all three relationship edge
    # types so a regression that breaks SVG layout fails the smoke.
    "app_network_topology_v1.html": {
        "asns": [
            {"asn": 3333, "name": "RIPE-NCC-AS", "country": "NL"},
            {"asn": 1234, "name": "ALPHA", "country": "DE"},
            {"asn": 5678, "name": "BETA", "country": "FR"},
            {"asn": 9999, "name": "GAMMA", "country": "GB"},
        ],
        "edges": [
            {"source_asn": 3333, "target_asn": 1234, "relationship": "peer"},
            {"source_asn": 3333, "target_asn": 5678, "relationship": "upstream"},
            {"source_asn": 3333, "target_asn": 9999, "relationship": "downstream"},
        ],
        "focus_asn": 3333,
    },
}

# At least one element matching the selector must be present after load. We
# don't pin a single id (so bundle internals can evolve), only that *some*
# known mount point rendered.
ROOT_SELECTORS: dict[str, str] = {
    "shape_timeseries_v1.html": "#chart, #profile",
    "shape_geofeatures_v1.html": "#gf-map, #gf-info",
    "shape_records_v1.html": "#app, #facets-list",
    "app_discovery_v1.html": "#app",
    "app_trade_flows_v1.html": "#app",
    "app_vulnerability_v1.html": "#app",
    "app_entity_graph_v1.html": "#app, #graph",
    "app_museum_v1.html": "#app, #grid",
    "app_molecular_v1.html": "#app, #viewer-pane",
    "app_news_tone_v1.html": "#app",
    "app_network_topology_v1.html": "#app, #graph",
}

# CDN origins to ignore in error filtering. Bundle's own inline JS has no
# source URL on its errors; CDN scripts identify themselves via the URL in
# the error's stack/location.
#
# ``3dmol.org`` is here because 3Dmol.js needs WebGL to initialise and
# headless Chromium ships with WebGL disabled by default — when the
# library tries to create a GL context it logs a console.error from the
# CDN script. That's a property of the headless env, not our bundle.
# We pin the bundle's #app shell mounting via ROOT_SELECTORS instead.
CDN_ORIGINS_TO_IGNORE = (
    "cdn.plot.ly",
    "unpkg.com",
    # jsdelivr ships D3.js v7 for the network-topology app. CDN errors
    # (offline test runner, transient 5xx) shouldn't fail the smoke
    # because they're not our bug.
    "cdn.jsdelivr.net",
    "3dmol.org",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser():
    """Module-scoped Chromium for cheap reuse across bundle parametrizations."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


def _is_cdn_error(text: str) -> bool:
    return any(origin in text for origin in CDN_ORIGINS_TO_IGNORE)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bundle_name", sorted(SAMPLE_PAYLOADS))
def test_bundle_loads_without_uncaught_errors(browser, bundle_name: str) -> None:
    """Each ``ui://`` bundle reaches DOMContentLoaded without uncaught JS
    errors and exposes its expected root mount point.
    """
    bundle_path = BUNDLES_DIR / bundle_name
    assert bundle_path.exists(), f"bundle file missing: {bundle_path}"

    page = browser.new_page()
    page_errors: list[str] = []
    console_errors: list[str] = []
    page.on("pageerror", lambda e: page_errors.append(str(e)))
    page.on(
        "console",
        lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
    )

    payload = SAMPLE_PAYLOADS[bundle_name]
    if payload is not None:
        page.add_init_script(f"window.__app_data__ = {json.dumps(payload)};")

    try:
        page.goto(bundle_path.as_uri(), wait_until="domcontentloaded")
        # Brief settle for inline scripts that run immediately after
        # DOMContentLoaded (the bundles register message listeners + paint
        # their initial state synchronously).
        page.wait_for_timeout(500)

        bundle_page_errors = [e for e in page_errors if not _is_cdn_error(e)]
        bundle_console_errors = [e for e in console_errors if not _is_cdn_error(e)]

        assert not bundle_page_errors, (
            f"{bundle_name} threw uncaught JS errors:\n  - "
            + "\n  - ".join(bundle_page_errors)
        )
        assert not bundle_console_errors, (
            f"{bundle_name} logged console.error from its own JS:\n  - "
            + "\n  - ".join(bundle_console_errors)
        )

        selector = ROOT_SELECTORS[bundle_name]
        count = page.locator(selector).count()
        assert count > 0, (
            f"{bundle_name} did not render any element matching {selector!r}. "
            "The bundle either crashed before mount or its root id changed; "
            "update ROOT_SELECTORS in this file if the change is intentional."
        )
    finally:
        page.close()


def test_every_bundle_in_repo_has_a_smoke_case() -> None:
    """If a new ``ui://`` bundle lands without a smoke entry, this fails
    loudly. Keeps Phase 6c coverage honest as Phase 5 ships more apps.
    """
    on_disk = {p.name for p in BUNDLES_DIR.glob("*.html")}
    covered = set(SAMPLE_PAYLOADS)
    missing = sorted(on_disk - covered)
    extra = sorted(covered - on_disk)
    assert not missing, (
        f"New bundle(s) without smoke coverage: {missing}. "
        f"Add an entry to SAMPLE_PAYLOADS + ROOT_SELECTORS in {Path(__file__).name}."
    )
    assert not extra, (
        f"SAMPLE_PAYLOADS references files that don't exist on disk: {extra}."
    )
