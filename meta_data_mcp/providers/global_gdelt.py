"""global-gdelt provider.

GDELT 2.0 — The Global Database of Events, Language and Tone. Monitors
broadcast, print and web news from across every country in over 100
languages and codifies the events, themes, persons and tone. This
provider exposes the GDELT DOC 2.0 API for full-text article search and
volume-over-time aggregates.

Homepage: https://www.gdeltproject.org/
API docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
License: GDELT data is in the public domain; please credit
"GDELT Project" when redistributing.
Auth: None required.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.app_news_tone_v1 import URI as NEWS_TONE_APP_URI
from meta_data_mcp.utils import (
    MAX_RESPONSE_CHARS,
    create_mcp_server,
    http_get,
    run_server,
    to_json_text,
)

log = logging.getLogger(__name__)

PROVIDER_ID = "global-gdelt"
DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# gdelt-article-search
###################


class GdeltArticleSearchParams(BaseModel):
    """Parameters for gdelt-article-search."""

    query: str = Field(
        ...,
        min_length=1,
        description=(
            "GDELT query string. Supports phrases (in quotes), AND, OR, NOT, "
            "and field operators: sourcelang:eng, sourcecountry:US, theme:HEALTH, "
            "domain:nytimes.com, tone>5, tone<-5, near5:'X Y' (proximity). "
            "Example: '\"supply chain\" sourcelang:eng tone<-3'."
        ),
    )
    timespan: str | None = Field(
        None,
        description=(
            "Lookback window — '15m', '1h', '24h', '3d', '1w', '1m'. "
            "Defaults to ~3 hours when unset."
        ),
    )
    maxrecords: int = Field(
        default=50,
        ge=1,
        le=250,
        description="Max articles to return (1-250).",
    )
    sort: str | None = Field(
        None,
        description=(
            "Sort order — 'DateDesc' (newest), 'DateAsc' (oldest), 'Tone' "
            "(most-positive first), 'ToneAsc' (most-negative first), "
            "'HybridRel' (relevance-balanced)."
        ),
    )


def fetch_gdelt_article_search(params: GdeltArticleSearchParams) -> Any:
    """Search GDELT 2.0 for news articles matching a query."""
    query: dict[str, Any] = {
        "query": params.query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": params.maxrecords,
    }
    if params.timespan is not None:
        query["timespan"] = params.timespan
    if params.sort is not None:
        query["sort"] = params.sort
    response = http_get(DOC_API_URL, params=query, provider=PROVIDER_ID)
    return response.json()


async def handle_gdelt_article_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gdelt-article-search tool call."""
    params = GdeltArticleSearchParams(**(arguments or {}))
    data = fetch_gdelt_article_search(params)
    return [
        types.TextContent(
            type="text",
            text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
        ),
    ]


TOOLS.append(
    types.Tool(
        name="gdelt-article-search",
        description=(
            "Search GDELT 2.0's global news index. Returns matching articles "
            "with url, title, source country, language, tone, and timestamp. "
            "Use GDELT's field operators (sourcelang, sourcecountry, theme, "
            "domain, tone) for precise filtering."
        ),
        input_schema=GdeltArticleSearchParams.model_json_schema(),
        # MCP Apps binding: render via the news-tone app. Use the alias
        # keyword (``_meta=``) — ``meta=`` silently drops into extras; see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": NEWS_TONE_APP_URI}},
    ),
)
TOOLS_HANDLERS["gdelt-article-search"] = handle_gdelt_article_search


###################
# gdelt-volume-timeline
###################


class GdeltVolumeTimelineParams(BaseModel):
    """Parameters for gdelt-volume-timeline."""

    query: str = Field(
        ...,
        min_length=1,
        description=(
            "GDELT query string (see gdelt-article-search description for "
            "operator syntax)."
        ),
    )
    timespan: str | None = Field(
        None,
        description="Lookback window — same syntax as gdelt-article-search.",
    )
    timeline_mode: str = Field(
        default="TimelineVol",
        description=(
            "Timeline mode — 'TimelineVol' (volume as % of all coverage), "
            "'TimelineVolRaw' (raw article counts), 'TimelineTone' (average "
            "tone), or 'TimelineLang' (volume by language)."
        ),
    )


def fetch_gdelt_volume_timeline(params: GdeltVolumeTimelineParams) -> Any:
    """Fetch a GDELT timeline series (volume or tone over time)."""
    query: dict[str, Any] = {
        "query": params.query,
        "mode": params.timeline_mode,
        "format": "json",
    }
    if params.timespan is not None:
        query["timespan"] = params.timespan
    response = http_get(DOC_API_URL, params=query, provider=PROVIDER_ID)
    return response.json()


async def handle_gdelt_volume_timeline(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the gdelt-volume-timeline tool call."""
    params = GdeltVolumeTimelineParams(**(arguments or {}))
    data = fetch_gdelt_volume_timeline(params)
    return [
        types.TextContent(
            type="text",
            text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
        ),
    ]


TOOLS.append(
    types.Tool(
        name="gdelt-volume-timeline",
        description=(
            "Fetch a time-series for a GDELT query — choose volume (share or raw "
            "counts), average tone, or volume-by-language. Useful for tracking how "
            "coverage of a topic evolves and for spotting tone shifts."
        ),
        input_schema=GdeltVolumeTimelineParams.model_json_schema(),
        _meta={"ui": {"resourceUri": NEWS_TONE_APP_URI}},
    ),
)
TOOLS_HANDLERS["gdelt-volume-timeline"] = handle_gdelt_volume_timeline


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    server = create_mcp_server(
        "global-gdelt",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )
    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
