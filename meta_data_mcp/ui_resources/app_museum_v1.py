"""``ui://meta-data-mcp/app/museum/v1`` — Phase 5 museum app.

Wraps the Metropolitan Museum of Art Open Access tools into one
image-driven panel:

- **``met-search``** (``global_met_museum``) — free-text search that
  returns a list of ``objectIDs`` matching a query plus optional
  faceted filters (department, medium, geography, date range).
- **``met-get-object``** (``global_met_museum``) — full single-object
  metadata: title, artist, date, medium, primary image URL, plus the
  provenance / culture / dimensions / repository text the Met
  publishes alongside each Open-Access record.

The panel renders the returned objects as a lazy-loaded CSS-grid of
thumbnails; clicking a tile opens a detail panel with the full
provenance and a link back to metmuseum.org. The Met Open Access
program publishes every public-domain image under Creative Commons
Zero, which makes a "browse the Met" surface the natural Phase 5
image-driven app.

postMessage protocol inherits the Phase 3 ``tool_call`` envelope —
see ``app_discovery_v1`` for the full description. ``met-search``
returns just object IDs (no embedded image/title metadata), so the
bundle's recommended host wiring is to hydrate each ID with a
follow-up ``met-get-object`` call before pushing the envelope to the
app. The bundle accepts either shape: a pre-hydrated
``{objects: [{id, title, image_url, ...}, ...]}`` payload (the
documented contract) **or** a raw ``{objectIDs: [...]}`` payload —
the host integrator's choice.

Visualisations are pure CSS grid + ``<img loading="lazy">`` — no
chart library, no canvas, no CDN. The bundle stays dependency-free
like the records primitive.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files

from mcp import types

from meta_data_mcp.utils import register_ui_resource

URI: str = "ui://meta-data-mcp/app/museum/v1"

# Surfaced in the host's resource catalog. Documents the bidirectional
# postMessage protocol and the expected payload contract so a host
# integrator can wire app↔host without reading the bundle source.
_DESCRIPTION = (
    "Museum browser app: lazy-loaded image grid of Met Museum objects "
    "with click-to-open provenance / artist / date / medium detail panel "
    "and a free-text search box. Wraps met-search + met-get-object. "
    "Payload contract: {objects: [{id, title, artist, date, medium, "
    "image_url, primary_image_url, url, provenance, tags}, ...], "
    "facets?: [...]}. Raw {objectIDs: [...]} from met-search is also "
    "accepted — the bundle renders an id-only fallback grid. "
    "Dependency-free vanilla JS + CSS grid. "
    "postMessage protocol — "
    "host→app: {type: 'tool_result'|'render', id?, tool?, payload}; "
    "app→host: {type: 'tool_call', id, name, arguments} (Phase 3 invention)."
)

_HTML: str = (files("meta_data_mcp.ui_resources") / "app_museum_v1.html").read_text(
    encoding="utf-8",
)


def register(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> str:
    """Append the museum app resource to the server's catalog.

    Returns the canonical URI so callers (and tests) can assert against it
    without duplicating the constant.
    """
    return register_ui_resource(
        name="app/museum/v1",
        html=_HTML,
        description=_DESCRIPTION,
        resources=resources,
        resources_handlers=resources_handlers,
    )
