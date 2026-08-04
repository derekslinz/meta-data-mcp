"""Repo-wide invariants enforced in CI (post-v2.0 architecture review).

Two checks, both deliberately simple and fast (<50ms total) so they sit
inside the regular ``test`` job rather than a dedicated workflow:

1. **Generator-TODO lint (M3 from the v2.0 review)** — the generator
   emits a ``# TODO: write a _<snake>_to_shape_payload(data) adapter``
   comment whenever a tool spec sets ``response_shape``. If a generated
   provider ships with that comment still in place, the bundle gets
   un-shape-mapped data routed through the size-bounded serializer and
   renders empty (the bug class v2.0 closed; this test ensures we
   don't re-open it).

2. **Bundle CDN origin allowlist (M4)** — every external ``<script src=>``
   and resource URL in a ``ui://`` bundle must point at a known
   origin. Catches supply-chain drift: a future bundle that adds an
   unreviewed CDN would land silently otherwise (bundle-size budget
   catches inflation, not origin drift).

3. **README registry coverage (M5)** — the bundled-plugin catalog and
   optional environment-variable table must stay in sync with the
   source-of-truth provider registry so docs don't silently lose new
   providers or auth knobs.

Both tests parse with regular expressions because htmls in this repo
are hand-authored, single-file, and small enough that pulling in
lxml/beautifulsoup would be more risk than parser-leniency saves.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from meta_data_mcp.registry import REGISTRY

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVIDERS_DIR = REPO_ROOT / "meta_data_mcp" / "providers"
BUNDLES_DIR = REPO_ROOT / "meta_data_mcp" / "ui_resources"
README_PATH = REPO_ROOT / "README.md"

# MCP tool names are surfaced to hosts (e.g. Claude) as Anthropic API tool
# names, which must match ``^[a-zA-Z0-9_-]{1,64}$``. A name containing a dot,
# space, slash, or colon — or one over 64 chars — gets rejected by the host
# (or silently sanitized into a name that no longer matches our handler dict
# keys), breaking the whole connector. Dotted names (``opendata.providers.find``)
# were the bug class this guard closes.
TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


# ---------------------------------------------------------------------------
# M3 — generator TODO lint
# ---------------------------------------------------------------------------

# Matches the literal comment the generator emits in
# ``tools/generate_provider.py::_render_handler`` for shape-bound tools.
ADAPTER_TODO_RE = re.compile(
    r"^\s*#\s*TODO:\s*write\s+a\s+_\w+_to_shape_payload\(data\)\s+adapter\b",
    re.MULTILINE,
)


def test_every_tool_name_matches_mcp_charset() -> None:
    """Every tool registered by any provider module must use a name the MCP
    host will accept (``^[a-zA-Z0-9_-]{1,64}$``).

    Importing each provider module is enough: every provider appends to its
    module-level ``TOOLS`` list at import time. This catches the dotted-name
    bug class (e.g. ``opendata.providers.find``, ``mcp.registry.search``) that
    a regex over ``name="..."`` literals would miss for dynamically-built
    names, and pins it for any future provider too.
    """
    offenders: list[str] = []
    for path in sorted(PROVIDERS_DIR.glob("*.py")):
        stem = path.stem
        if stem.startswith("__"):  # __init__, __template__
            continue
        module = importlib.import_module(f"meta_data_mcp.providers.{stem}")
        for tool in getattr(module, "TOOLS", []):
            if not TOOL_NAME_RE.match(tool.name):
                offenders.append(f"{stem}: {tool.name!r}")

    assert not offenders, (
        "Tool name(s) violate the MCP charset ^[a-zA-Z0-9_-]{1,64}$ — "
        "hosts reject dots/spaces/slashes/colons and over-64-char names:\n  "
        + "\n  ".join(offenders)
    )


def test_no_generated_provider_ships_with_unwritten_shape_adapter() -> None:
    """A generated provider must not reach main with the placeholder
    adapter TODO still in place — the bundle would render empty even
    though CI is green.
    """
    offenders: list[tuple[Path, int]] = []
    for path in sorted(PROVIDERS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in ADAPTER_TODO_RE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append((path, line))

    if not offenders:
        return

    rendered = "\n".join(
        f"  - {path.relative_to(REPO_ROOT)}:{line}" for path, line in offenders
    )
    pytest.fail(
        "Generator placeholder adapters still in tree — write the "
        "_<snake>_to_shape_payload(data) function and replace the TODO "
        "before merging the provider:\n" + rendered,
    )


# ---------------------------------------------------------------------------
# M4 — bundle CDN-origin allowlist
# ---------------------------------------------------------------------------

# Origins explicitly approved for ``ui://`` bundles. Adding to this list
# is an architectural decision — do it in the same PR that introduces
# the new dependency and document why in the bundle's header comment.
ALLOWED_BUNDLE_ORIGINS: frozenset[str] = frozenset(
    {
        # JS libraries pulled from their canonical CDNs.
        "cdn.plot.ly",  # Plotly (shape_timeseries)
        "cdn.jsdelivr.net",  # D3 + d3-sankey (entity-graph, network-topology, trade-flows)
        "unpkg.com",  # Reserved (Leaflet via unpkg is the documented fallback)
        "3dmol.org",  # 3Dmol.js (molecular app)
        # Data origins — bundles fetch these via the host's network,
        # not via <script>, but the URL strings appear in the bundle
        # source and the regex catches both.
        "files.rcsb.org",  # RCSB PDB structure downloads (molecular)
        "pubchem.ncbi.nlm.nih.gov",  # PubChem SDF downloads (molecular)
        "www.openstreetmap.org",  # OSM attribution link (geofeatures)
    },
)

# Matches any ``https://<host>`` reference in a bundle. We deliberately
# don't try to distinguish <script src=> from a tooltip-link href=,
# because the security boundary is "no unreviewed origin ever appears in
# the bundle" — link OR script.
ORIGIN_RE = re.compile(r"https://([a-zA-Z0-9.-]+)")


def _bundles() -> list[Path]:
    return sorted(BUNDLES_DIR.glob("*.html"))


@pytest.mark.parametrize("bundle", _bundles(), ids=lambda p: p.name)
def test_bundle_external_origins_are_allowlisted(bundle: Path) -> None:
    """Every external ``https://<host>`` reference in a bundle must
    point at an allowlisted origin. Catches a future bundle that adds
    an unreviewed CDN or data-source dependency.
    """
    text = bundle.read_text(encoding="utf-8")
    seen_origins = {match.group(1) for match in ORIGIN_RE.finditer(text)}
    rogue = sorted(seen_origins - ALLOWED_BUNDLE_ORIGINS)
    assert not rogue, (
        f"{bundle.name} references un-allowlisted external origin(s): {rogue}. "
        f"If this is intentional, add to ALLOWED_BUNDLE_ORIGINS in {Path(__file__).name} "
        f"in the same PR that adds the dependency, and document why in the bundle's "
        f"header comment."
    )


def test_bundle_directory_is_populated() -> None:
    """Parametrize-with-empty-list silently produces zero test cases.
    Pin a floor so a refactor that moves the directory fails loudly.
    """
    assert _bundles(), (
        f"No bundles found in {BUNDLES_DIR} — directory moved or glob is stale."
    )


# Matches a dotted meta/registry tool name (the pre-v2.5 form) anywhere in a
# bundle. ``ui://`` bundles dispatch tool calls by literal name from JS, so a
# stale dotted name there breaks the app at runtime without failing any other
# test. The charset invariant only sees Python ``TOOLS``; this catches the HTML.
DOTTED_TOOL_NAME_RE = re.compile(
    r"\b(?:opendata|mcp)(?:\.[a-z_]+){2,}\b",
)


@pytest.mark.parametrize("bundle", _bundles(), ids=lambda p: p.name)
def test_bundle_uses_no_dotted_tool_names(bundle: Path) -> None:
    """A bundle must not reference dotted tool names (e.g.
    ``opendata.providers.find``). Hosts expose tools under the underscore
    form, so a dotted dispatch name no longer resolves once renamed.
    """
    text = bundle.read_text(encoding="utf-8")
    offenders = sorted({match.group(0) for match in DOTTED_TOOL_NAME_RE.finditer(text)})
    assert not offenders, (
        f"{bundle.name} dispatches dotted tool name(s) that no longer resolve "
        f"(use underscore form): {offenders}"
    )


# ---------------------------------------------------------------------------
# M5 — README coverage for bundled providers and optional env vars
# ---------------------------------------------------------------------------

README_BUNDLED_SECTION_RE = re.compile(
    r"^## Bundled plugins(?: \(\d+\))?\n(?P<body>.*?)(?=^## Optional environment variables$)",
    re.MULTILINE | re.DOTALL,
)
README_OPTIONAL_ENV_SECTION_RE = re.compile(
    r"^## Optional environment variables\n(?P<body>.*?)(?=^## Transports$)",
    re.MULTILINE | re.DOTALL,
)
README_PROVIDER_ROW_RE = re.compile(r"^\| `([a-z0-9_]+)` \|", re.MULTILINE)
README_ENV_ROW_RE = re.compile(r"^\| `([A-Z][A-Z0-9_]+)` \|", re.MULTILINE)


def _readme_section(pattern: re.Pattern[str], *, section_name: str) -> str:
    text = README_PATH.read_text(encoding="utf-8")
    match = pattern.search(text)
    assert match is not None, f"Could not find README section: {section_name}"
    return match.group("body")


def test_readme_bundled_plugins_cover_registry() -> None:
    """Every static provider in the registry must be documented in the
    bundled-plugin catalog so new providers don't ship undocumented.
    """
    body = _readme_section(
        README_BUNDLED_SECTION_RE,
        section_name="Bundled plugins",
    )
    documented = {match.group(1) for match in README_PROVIDER_ROW_RE.finditer(body)}
    registry_ids = {entry.id for entry in REGISTRY}
    missing = sorted(registry_ids - documented)
    extra = sorted(documented - registry_ids)
    assert not missing and not extra, (
        "README bundled-plugin catalog drifted from meta_data_mcp.registry: "
        f"missing={missing}, extra={extra}"
    )


def test_readme_optional_env_vars_cover_registry() -> None:
    """Every registry-declared provider env var must be documented in the
    optional-env table so users see the available auth/rate-limit knobs.
    """
    body = _readme_section(
        README_OPTIONAL_ENV_SECTION_RE,
        section_name="Optional environment variables",
    )
    documented = {match.group(1) for match in README_ENV_ROW_RE.finditer(body)}
    registry_vars = {var for entry in REGISTRY for var in entry.requires_env}
    missing = sorted(registry_vars - documented)
    assert not missing, (
        f"README optional-env table is missing registry-declared variable(s): {missing}"
    )
