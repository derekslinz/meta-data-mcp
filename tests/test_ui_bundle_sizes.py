"""Bundle-size budget for ``ui://`` resources (v2.0 Phase 6b).

The MCP Apps spec ships each bundle as a single ``resources/read`` payload
back to the host. Heavy bundles slow every tool call that binds to them
(host has to fetch them on first render), and a runaway bundle can blow
through transport buffers on stdio. We enforce a budget in CI so a future
agent that decides to inline Plotly or Cesium gets caught immediately.

Two thresholds (matched to the v2.0 plan §6b):

- ``WARN_BYTES`` (100 KB): emits a pytest warning. The bundle is still
  shipping; the test does not fail. Triggers a Codex/Copilot review nudge.
- ``ERROR_BYTES`` (1 MB): hard fail. No ``ui://`` bundle should approach
  this; if it does, we want CI to stop the PR.

Current baseline (May 2026): largest bundle is ``app_vulnerability_v1.html``
at ~33 KB — every bundle has a comfortable 3× margin to the warn line and
a 30× margin to the error line.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

WARN_BYTES = 100 * 1024
ERROR_BYTES = 1024 * 1024

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLES_DIR = REPO_ROOT / "meta_data_mcp" / "ui_resources"


def _bundles() -> list[Path]:
    """All ``ui://`` HTML bundles in the package."""
    return sorted(BUNDLES_DIR.glob("*.html"))


@pytest.mark.parametrize("bundle", _bundles(), ids=lambda p: p.name)
def test_bundle_under_error_budget(bundle: Path) -> None:
    """Every ``ui://`` HTML bundle must stay under 1 MB.

    A bundle this large would slow every tool call bound to it and risks
    blowing through MCP stdio transport buffers. If you genuinely need
    a large library, host it on a CDN and load it from the bundle —
    don't inline the bytes.
    """
    size = bundle.stat().st_size
    assert size <= ERROR_BYTES, (
        f"{bundle.name} is {size:,} bytes — exceeds the {ERROR_BYTES:,}-byte "
        f"hard limit. Move heavy dependencies to a CDN and reference them "
        f"from the bundle instead."
    )


@pytest.mark.parametrize("bundle", _bundles(), ids=lambda p: p.name)
def test_bundle_under_warn_budget(bundle: Path) -> None:
    """Warn when a ``ui://`` HTML bundle exceeds 100 KB.

    Emits a pytest warning (not a hard failure) so a borderline bundle
    surfaces in CI logs without blocking the PR. If you intentionally
    cross this line, raise ``WARN_BYTES`` in the same PR with a comment
    explaining why.
    """
    size = bundle.stat().st_size
    if size > WARN_BYTES:
        warnings.warn(
            f"{bundle.name} is {size:,} bytes — over the {WARN_BYTES:,}-byte "
            f"soft budget. Consider splitting or CDN-hosting heavy code.",
            stacklevel=1,
        )


def test_bundle_directory_is_populated() -> None:
    """If the bundle glob returns nothing, the test parameters above
    silently produce zero cases and the budget would be unenforced.
    Pin a non-empty floor so a refactor that moves the directory fails
    loudly here.
    """
    bundles = _bundles()
    assert bundles, (
        f"No ``ui://`` bundles found in {BUNDLES_DIR}. Either the directory "
        f"moved or the glob pattern needs updating."
    )
