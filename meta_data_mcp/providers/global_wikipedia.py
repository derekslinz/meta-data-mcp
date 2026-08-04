"""Wikipedia Provider

This module exposes the public Wikipedia REST API (api/rest_v1) plus a small
slice of the MediaWiki Action API for title search. The provider supports
multiple language editions via a `lang` parameter that swaps the host prefix.

License note:
    Wikipedia article text is dual-licensed under CC BY-SA 4.0 and GFDL.
    Summaries returned by the REST API include attribution metadata that
    callers should preserve. Wikimedia requests a descriptive User-Agent;
    this module relies on the project's default User-Agent set in
    meta_data_mcp.utils.http_get.

Features:
- Page summaries (lead extract + thumbnail)
- Full page HTML
- Mobile-sections breakdown for a page
- Related-pages recommendations
- Media list (images/sounds) for a page
- Title prefix search (MediaWiki opensearch)
- Daily per-article page-view metrics
- 'On this day' feed (events, holidays, births, deaths, selected)

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
import urllib.parse
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_records_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-wikipedia"
BASE_URL = "https://en.wikipedia.org/api/rest_v1"


def _rest_base(lang: str) -> str:
    """Return the REST v1 base URL for a given Wikipedia language edition."""
    return f"https://{lang}.wikipedia.org/api/rest_v1"


def _action_api(lang: str) -> str:
    """Return the MediaWiki Action API URL for a given Wikipedia language edition."""
    return f"https://{lang}.wikipedia.org/w/api.php"


def _encode_title(title: str) -> str:
    """Percent-encode a Wikipedia title for use in a REST URL path segment."""
    # Wikipedia REST API expects titles with spaces as underscores, then URL-encoded.
    return urllib.parse.quote(title.replace(" ", "_"), safe="")


# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Page summary
###################


class WikipediaSummaryParams(BaseModel):
    """Parameters for fetching a Wikipedia page summary."""

    title: str = Field(..., description="Article title, e.g. 'Albert Einstein'.")
    lang: str = Field(default="en", description="Wikipedia language code, e.g. 'en'.")


def fetch_wikipedia_summary(params: WikipediaSummaryParams) -> dict:
    """Call /page/summary/{title} on the Wikipedia REST API."""
    url = f"{_rest_base(params.lang)}/page/summary/{_encode_title(params.title)}"
    response = http_get(url, provider=PROVIDER_ID)
    return response.json()


async def handle_wikipedia_get_summary(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikipedia-get-summary tool call."""
    try:
        if not arguments or "title" not in arguments:
            raise ValueError("title is required")
        params = WikipediaSummaryParams(**arguments)
        data = fetch_wikipedia_summary(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Wikipedia summary: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikipedia-get-summary",
        description="Fetch a Wikipedia page summary (lead extract + thumbnail).",
        input_schema=WikipediaSummaryParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikipedia-get-summary"] = handle_wikipedia_get_summary


###################
# Page HTML
###################


class WikipediaHtmlParams(BaseModel):
    """Parameters for fetching the full HTML of a Wikipedia page."""

    title: str = Field(..., description="Article title.")
    lang: str = Field(default="en", description="Wikipedia language code.")


def fetch_wikipedia_html(params: WikipediaHtmlParams) -> str:
    """Call /page/html/{title} on the Wikipedia REST API; returns raw HTML."""
    url = f"{_rest_base(params.lang)}/page/html/{_encode_title(params.title)}"
    response = http_get(url, headers={"Accept": "text/html"}, provider=PROVIDER_ID)
    return response.text


async def handle_wikipedia_get_html(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikipedia-get-html tool call."""
    try:
        if not arguments or "title" not in arguments:
            raise ValueError("title is required")
        params = WikipediaHtmlParams(**arguments)
        data = fetch_wikipedia_html(params)
        return [types.TextContent(type="text", text=data[:20000])]
    except Exception as e:
        log.error(f"Error fetching Wikipedia HTML: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikipedia-get-html",
        description="Fetch the full rendered HTML of a Wikipedia page (truncated to 20k chars).",
        input_schema=WikipediaHtmlParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikipedia-get-html"] = handle_wikipedia_get_html


###################
# Mobile sections
###################


class WikipediaMobileSectionsParams(BaseModel):
    """Parameters for fetching the mobile-sections payload of a Wikipedia page."""

    title: str = Field(..., description="Article title.")
    lang: str = Field(default="en", description="Wikipedia language code.")


def fetch_wikipedia_mobile_sections(params: WikipediaMobileSectionsParams) -> dict:
    """Call /page/mobile-sections/{title} on the Wikipedia REST API."""
    url = (
        f"{_rest_base(params.lang)}/page/mobile-sections/{_encode_title(params.title)}"
    )
    response = http_get(url, provider=PROVIDER_ID)
    return response.json()


async def handle_wikipedia_get_mobile_sections(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikipedia-get-mobile-sections tool call."""
    try:
        if not arguments or "title" not in arguments:
            raise ValueError("title is required")
        params = WikipediaMobileSectionsParams(**arguments)
        data = fetch_wikipedia_mobile_sections(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Wikipedia mobile sections: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikipedia-get-mobile-sections",
        description="Fetch a Wikipedia page broken into mobile-friendly sections.",
        input_schema=WikipediaMobileSectionsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikipedia-get-mobile-sections"] = handle_wikipedia_get_mobile_sections


###################
# Related pages
###################


class WikipediaRelatedParams(BaseModel):
    """Parameters for fetching related Wikipedia pages."""

    title: str = Field(..., description="Article title to base recommendations on.")
    lang: str = Field(default="en", description="Wikipedia language code.")


def fetch_wikipedia_related(params: WikipediaRelatedParams) -> dict:
    """Call /page/related/{title} on the Wikipedia REST API."""
    url = f"{_rest_base(params.lang)}/page/related/{_encode_title(params.title)}"
    response = http_get(url, provider=PROVIDER_ID)
    return response.json()


async def handle_wikipedia_get_related(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikipedia-get-related tool call."""
    try:
        if not arguments or "title" not in arguments:
            raise ValueError("title is required")
        params = WikipediaRelatedParams(**arguments)
        data = fetch_wikipedia_related(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Wikipedia related pages: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikipedia-get-related",
        description="Fetch related-page recommendations for a Wikipedia title.",
        input_schema=WikipediaRelatedParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikipedia-get-related"] = handle_wikipedia_get_related


###################
# Media list
###################


class WikipediaMediaListParams(BaseModel):
    """Parameters for fetching the media list for a Wikipedia page."""

    title: str = Field(..., description="Article title.")
    lang: str = Field(default="en", description="Wikipedia language code.")


def fetch_wikipedia_media_list(params: WikipediaMediaListParams) -> dict:
    """Call /page/media-list/{title} on the Wikipedia REST API."""
    url = f"{_rest_base(params.lang)}/page/media-list/{_encode_title(params.title)}"
    response = http_get(url, provider=PROVIDER_ID)
    return response.json()


async def handle_wikipedia_get_media_list(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikipedia-get-media-list tool call."""
    try:
        if not arguments or "title" not in arguments:
            raise ValueError("title is required")
        params = WikipediaMediaListParams(**arguments)
        data = fetch_wikipedia_media_list(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Wikipedia media list: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikipedia-get-media-list",
        description="Fetch the media list (images, sounds) for a Wikipedia page.",
        input_schema=WikipediaMediaListParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikipedia-get-media-list"] = handle_wikipedia_get_media_list


###################
# Title search (opensearch via Action API)
###################


class WikipediaSearchTitleParams(BaseModel):
    """Parameters for searching Wikipedia titles by prefix."""

    search: str = Field(..., description="Search prefix or term.")
    lang: str = Field(default="en", description="Wikipedia language code.")
    limit: int = Field(default=10, description="Maximum number of titles to return.")


def fetch_wikipedia_search_title(params: WikipediaSearchTitleParams) -> list:
    """Call action=opensearch on the language-specific MediaWiki API."""
    query_params: dict[str, Any] = {
        "action": "opensearch",
        "search": params.search,
        "limit": params.limit,
        "format": "json",
    }
    response = http_get(
        _action_api(params.lang),
        params=query_params,
        provider=PROVIDER_ID,
    )
    return response.json()


def _wikipedia_opensearch_to_shape_payload(data: list) -> dict:
    """Adapt the MediaWiki opensearch response to the records shape
    primitive's payload.

    Opensearch returns ``[query, titles, descriptions, urls]`` (four
    parallel arrays). We zip those into one row per title with title,
    description, and url columns.
    """
    rows: list[dict[str, Any]] = []
    query = ""
    if isinstance(data, list) and len(data) >= 4:
        query = data[0] if isinstance(data[0], str) else ""
        titles = data[1] if isinstance(data[1], list) else []
        descriptions = data[2] if isinstance(data[2], list) else []
        urls = data[3] if isinstance(data[3], list) else []
        for i, title in enumerate(titles):
            if not isinstance(title, str):
                continue
            rows.append(
                {
                    "title": title,
                    "description": descriptions[i]
                    if i < len(descriptions) and isinstance(descriptions[i], str)
                    else "",
                    "url": urls[i]
                    if i < len(urls) and isinstance(urls[i], str)
                    else "",
                },
            )
    payload: dict[str, Any] = {
        "rows": rows,
        "schema": {
            "columns": [
                {"name": "title", "type": "string", "description": "Article title"},
                {
                    "name": "description",
                    "type": "string",
                    "description": "Short description",
                },
                {"name": "url", "type": "string", "description": "Article URL"},
            ],
        },
        "default_facets": [],
    }
    if query:
        payload["query"] = query
    return payload


async def handle_wikipedia_search_title(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikipedia-search-title tool call.

    Returns the response in the records shape primitive payload.
    """
    try:
        if not arguments or "search" not in arguments:
            raise ValueError("search is required")
        params = WikipediaSearchTitleParams(**arguments)
        data = fetch_wikipedia_search_title(params)
        payload = _wikipedia_opensearch_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_records_text(payload))]
    except Exception as e:
        log.error(f"Error searching Wikipedia titles: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikipedia-search-title",
        description="Prefix-search Wikipedia article titles via the MediaWiki opensearch endpoint.",
        input_schema=WikipediaSearchTitleParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["wikipedia-search-title"] = handle_wikipedia_search_title


###################
# Page views
###################


class WikipediaPageViewsParams(BaseModel):
    """Parameters for fetching daily page-view metrics for a Wikipedia article."""

    title: str = Field(..., description="Article title.")
    start: str = Field(
        ...,
        description="Start date in YYYYMMDD format (Wikimedia metrics convention).",
    )
    end: str = Field(..., description="End date in YYYYMMDD format.")
    lang: str = Field(default="en", description="Wikipedia language code.")


def fetch_wikipedia_page_views(params: WikipediaPageViewsParams) -> dict:
    """Call /metrics/pageviews/per-article/.../daily/{start}/{end} on REST API."""
    url = (
        f"{_rest_base(params.lang)}/metrics/pageviews/per-article/"
        f"{params.lang}.wikipedia/all-access/all-agents/"
        f"{_encode_title(params.title)}/daily/{params.start}/{params.end}"
    )
    response = http_get(url, provider=PROVIDER_ID)
    return response.json()


async def handle_wikipedia_get_page_views(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikipedia-get-page-views tool call."""
    try:
        if (
            not arguments
            or "title" not in arguments
            or "start" not in arguments
            or "end" not in arguments
        ):
            raise ValueError("title, start, and end are required")
        params = WikipediaPageViewsParams(**arguments)
        data = fetch_wikipedia_page_views(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Wikipedia page views: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikipedia-get-page-views",
        description="Fetch daily Wikipedia page-view metrics for an article between two YYYYMMDD dates.",
        input_schema=WikipediaPageViewsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikipedia-get-page-views"] = handle_wikipedia_get_page_views


###################
# On this day
###################


class WikipediaOnThisDayParams(BaseModel):
    """Parameters for fetching the Wikipedia 'on this day' feed."""

    type: str = Field(
        default="events",
        description="Feed type: 'all', 'selected', 'births', 'deaths', 'events', or 'holidays'.",
    )
    month: str = Field(..., description="Two-digit month MM, e.g. '07'.")
    day: str = Field(..., description="Two-digit day DD, e.g. '04'.")
    lang: str = Field(default="en", description="Wikipedia language code.")


def fetch_wikipedia_on_this_day(params: WikipediaOnThisDayParams) -> dict:
    """Call /feed/onthisday/{type}/{MM}/{DD} on the Wikipedia REST API."""
    url = (
        f"{_rest_base(params.lang)}/feed/onthisday/"
        f"{params.type}/{params.month}/{params.day}"
    )
    response = http_get(url, provider=PROVIDER_ID)
    return response.json()


async def handle_wikipedia_get_on_this_day(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the wikipedia-get-on-this-day tool call."""
    try:
        if not arguments or "month" not in arguments or "day" not in arguments:
            raise ValueError("month and day are required")
        params = WikipediaOnThisDayParams(**arguments)
        data = fetch_wikipedia_on_this_day(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching Wikipedia on-this-day feed: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="wikipedia-get-on-this-day",
        description="Fetch Wikipedia's 'on this day' feed (events, holidays, births, deaths, selected) for a given month/day.",
        input_schema=WikipediaOnThisDayParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["wikipedia-get-on-this-day"] = handle_wikipedia_get_on_this_day


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-wikipedia",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
