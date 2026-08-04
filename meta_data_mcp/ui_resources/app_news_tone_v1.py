"""``ui://meta-data-mcp/app/news-tone/v1`` — Phase 5 news-tone app.

Surfaces the GDELT 2.0 DOC API event-stream as one panel. GDELT is the
only upstream — every other Phase 5 app multiplexes across providers, but
GDELT's tone+country+timestamp tuple is rich enough on its own to drive a
timeline + tone overlay + country chord without help.

Tools wrapped:

- ``gdelt-article-search`` — ArtList mode, returns per-article
  ``url`` / ``title`` / ``seendate`` / ``sourcecountry`` / ``tone``. The
  events feed and the country-chord co-occurrence both fall out of this
  one response.
- ``gdelt-volume-timeline`` — TimelineTone mode, returns the daily
  average tone series that overlays on top of the per-article lollipops.

The chord is derived in-bundle: each calendar day's distinct source
countries form an undirected co-occurrence edge, weighted by number of
shared days. That avoids requiring a per-country fan-out call.

postMessage protocol matches the Phase 3 discovery app — see that module
for the full envelope description. Visualisations are inline SVG (no
chart library, no CDN) so the bundle stays dependency-free.

See ``Plans/linear-swimming-pond.md`` §5 — the planned "Timeline + tone
overlay + country chord" deliverable lives here.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files

from mcp import types

from meta_data_mcp.utils import register_ui_resource

URI: str = "ui://meta-data-mcp/app/news-tone/v1"

# Surfaced in the host's resource catalog. Documents the bidirectional
# postMessage protocol so a host integrator can wire app↔host without
# reading the bundle source. Inherited from the Phase 3 discovery app.
_DESCRIPTION = (
    "News-tone app: per-article tone lollipops on a daily-volume timeline, "
    "average-tone overlay, and country co-occurrence chord diagram. "
    "Wraps gdelt-article-search (events + countries) and "
    "gdelt-volume-timeline (tone overlay). "
    "Dependency-free vanilla JS + inline SVG. "
    "Payload contract — caller may push either a raw GDELT response "
    "(sniffed by shape) or a unified envelope "
    "{events:[{date,tone,title,country,url}], "
    "country_pairs:[{a,b,weight}], "
    "facets:{tone_timeline,volume_timeline,query}}. "
    "postMessage protocol — "
    "host→app: {type: 'tool_result'|'render', id?, tool?, payload}; "
    "app→host: {type: 'tool_call', id, name, arguments} (Phase 3 invention)."
)

_HTML: str = (files("meta_data_mcp.ui_resources") / "app_news_tone_v1.html").read_text(
    encoding="utf-8",
)


def register(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> str:
    """Append the news-tone app resource to the server's catalog.

    Returns the canonical URI so callers (and tests) can assert against it
    without duplicating the constant.
    """
    return register_ui_resource(
        name="app/news-tone/v1",
        html=_HTML,
        description=_DESCRIPTION,
        resources=resources,
        resources_handlers=resources_handlers,
    )
