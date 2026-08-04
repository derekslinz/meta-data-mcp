"""UK legislation.gov.uk Provider

This module exposes the UK National Archives' legislation.gov.uk
service, which publishes Acts of the UK Parliament, statutory
instruments, and devolved legislation from Scotland, Wales, and
Northern Ireland.

License note:
    Most legislation.gov.uk content is published under the Open
    Government Licence v3.0. Some Crown / Parliamentary copyright
    items have additional terms; consult
    https://www.legislation.gov.uk/help#openparliamentlicence for
    specifics.

Output format:
    Most endpoints return Atom XML feeds or Akoma Ntoso XML / HTML.
    Handlers therefore return the raw ``response.text`` (truncated to
    20,000 characters) so consumers can parse it themselves.

Features:
- Free-text / metadata search across all legislation (Atom feed)
- List by legislation type and year (Atom feed)
- Fetch a single document as Akoma Ntoso XML
- Fetch a single document as HTML
- Browse hub (lists available legislation types)
- Changes feed for a specific piece of legislation

Usage:
    The module can be run directly to start an MCP server, or its
    components can be imported individually.
"""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI
from meta_data_mcp.utils import (
    MAX_RESPONSE_CHARS,
    http_get,
    to_records_text,
)

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "uk-legislation"
BASE_URL = "https://www.legislation.gov.uk"

_ATOM_HEADERS = {"Accept": "application/atom+xml"}
_XML_HEADERS = {"Accept": "application/xml"}
_HTML_HEADERS = {"Accept": "text/html"}

# Records-shape adapter constants
_MAX_SUMMARY_CHARS = 500
_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Search
###################


class UkLegislationSearchParams(BaseModel):
    """Parameters for the legislation.gov.uk search feed."""

    title: str | None = Field(None, description="Title query (substring match).")
    text: str | None = Field(
        None,
        description="Free-text search across legislation content.",
    )
    type: str | None = Field(
        None,
        description=(
            "Legislation type code (e.g. 'ukpga' for UK Public General Acts, "
            "'uksi' for UK Statutory Instruments, 'asp' for Acts of Scottish "
            "Parliament)."
        ),
    )
    year: int | None = Field(None, description="Filter by year (e.g. 2024).")
    page: int = Field(default=1, description="Results page (1-indexed).")


def fetch_uk_legislation_search(params: UkLegislationSearchParams) -> str:
    """Call /all/data.feed with the given filters; return Atom XML text."""
    query_params: dict[str, Any] = {"page": params.page}
    if params.title:
        query_params["title"] = params.title
    if params.text:
        query_params["text"] = params.text
    if params.type:
        query_params["type"] = params.type
    if params.year is not None:
        query_params["year"] = params.year
    response = http_get(
        f"{BASE_URL}/all/data.feed",
        params=query_params,
        headers=_ATOM_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


def _uk_legislation_atom_to_shape_payload(xml_text: str) -> dict:
    """Adapt legislation.gov.uk Atom feed to the records shape primitive.

    Each Atom ``<entry>`` corresponds to a piece of legislation. We pull
    title, id (acts-like URL), updated, and the document type / year via
    the ``category`` attributes the feed exposes.
    """
    rows: list[dict[str, Any]] = []
    if not isinstance(xml_text, str) or not xml_text.strip():
        return {
            "rows": rows,
            "schema": _uk_legislation_schema(),
            "default_facets": ["type", "year"],
        }
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        log.warning(
            "uk_legislation adapter: failed to parse Atom XML; returning empty rows",
        )
        return {
            "rows": rows,
            "schema": _uk_legislation_schema(),
            "default_facets": ["type", "year"],
        }
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title_el = entry.find(f"{_ATOM_NS}title")
        title = (
            " ".join(title_el.text.split())
            if title_el is not None and title_el.text
            else ""
        )
        id_el = entry.find(f"{_ATOM_NS}id")
        link_el = entry.find(f"{_ATOM_NS}link")
        summary_el = entry.find(f"{_ATOM_NS}summary")
        summary = (
            " ".join(summary_el.text.split())
            if summary_el is not None and summary_el.text
            else ""
        )
        if len(summary) > _MAX_SUMMARY_CHARS:
            summary = summary[:_MAX_SUMMARY_CHARS].rstrip() + "…"
        updated_el = entry.find(f"{_ATOM_NS}updated")
        leg_type = None
        year = None
        for cat in entry.findall(f"{_ATOM_NS}category"):
            scheme = cat.get("scheme") or ""
            term = cat.get("term")
            if scheme.endswith("type") and not leg_type:
                leg_type = term
            elif scheme.endswith("year") and not year:
                year = term
        rows.append(
            {
                "title": title,
                "id": id_el.text if id_el is not None else None,
                "link": link_el.get("href") if link_el is not None else None,
                "type": leg_type,
                "year": year,
                "updated": updated_el.text if updated_el is not None else None,
                "summary": summary,
            },
        )
    return {
        "rows": rows,
        "schema": _uk_legislation_schema(),
        "default_facets": ["type", "year"],
    }


def _uk_legislation_schema() -> dict[str, Any]:
    return {
        "columns": [
            {"name": "title", "type": "string", "description": "Legislation title"},
            {"name": "id", "type": "string", "description": "Atom entry id"},
            {"name": "link", "type": "string", "description": "Resource URL"},
            {
                "name": "type",
                "type": "string",
                "description": "Legislation type code",
            },
            {"name": "year", "type": "string", "description": "Year"},
            {"name": "updated", "type": "date", "description": "Updated timestamp"},
            {
                "name": "summary",
                "type": "string",
                "description": "Summary (truncated)",
            },
        ],
    }


async def handle_uk_legislation_search(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the uk-legislation-search tool call.

    Returns the response in the records shape primitive payload (parsed
    from the upstream Atom feed). The legacy text-truncation behaviour
    is replaced by structured rows.
    """
    try:
        params = UkLegislationSearchParams(**(arguments or {}))
        text = fetch_uk_legislation_search(params)
        payload = _uk_legislation_atom_to_shape_payload(text)
        return [
            types.TextContent(
                type="text",
                text=to_records_text(payload)[:MAX_RESPONSE_CHARS],
            ),
        ]
    except Exception as e:
        log.error(f"Error searching UK legislation: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="uk-legislation-search",
        description=(
            "Search legislation.gov.uk across all legislation by title, free "
            "text, type, and/or year."
        ),
        input_schema=UkLegislationSearchParams.model_json_schema(),
        _meta={"ui": {"resourceUri": RECORDS_URI}},
    ),
)
TOOLS_HANDLERS["uk-legislation-search"] = handle_uk_legislation_search


###################
# List by Year
###################


class UkLegislationListByYearParams(BaseModel):
    """Parameters for listing legislation by type and year."""

    type: str = Field(
        ...,
        description=(
            "Legislation type code (e.g. 'ukpga', 'uksi', 'asp', 'anaw', 'nia')."
        ),
    )
    year: int = Field(..., description="Year of legislation (e.g. 2024).")
    page: int = Field(default=1, description="Results page (1-indexed).")


def fetch_uk_legislation_list_by_year(params: UkLegislationListByYearParams) -> str:
    """Call /{type}/{year}/data.feed; return Atom XML text."""
    query_params: dict[str, Any] = {"page": params.page}
    response = http_get(
        f"{BASE_URL}/{params.type}/{params.year}/data.feed",
        params=query_params,
        headers=_ATOM_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_uk_legislation_list_by_year(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the uk-legislation-list-by-year tool call."""
    try:
        if not arguments or "type" not in arguments or "year" not in arguments:
            raise ValueError("type and year are required")
        params = UkLegislationListByYearParams(**arguments)
        text = fetch_uk_legislation_list_by_year(params)
        return [types.TextContent(type="text", text=text[:MAX_RESPONSE_CHARS])]
    except Exception as e:
        log.error(f"Error listing UK legislation by year: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="uk-legislation-list-by-year",
        description=(
            "List all UK legislation of a given type for a specified year. "
            "Returns Atom XML."
        ),
        input_schema=UkLegislationListByYearParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["uk-legislation-list-by-year"] = handle_uk_legislation_list_by_year


###################
# Get Document XML
###################


class UkLegislationGetDocumentXmlParams(BaseModel):
    """Parameters for fetching a single piece of legislation as XML."""

    type: str = Field(..., description="Legislation type code (e.g. 'ukpga').")
    year: int = Field(..., description="Year of legislation.")
    number: int = Field(..., description="Sequence number within type/year.")


def fetch_uk_legislation_document_xml(
    params: UkLegislationGetDocumentXmlParams,
) -> str:
    """Call /{type}/{year}/{number}/data.xml; return Akoma Ntoso XML text."""
    response = http_get(
        f"{BASE_URL}/{params.type}/{params.year}/{params.number}/data.xml",
        headers=_XML_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_uk_legislation_get_document_xml(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the uk-legislation-get-document-xml tool call."""
    try:
        if (
            not arguments
            or "type" not in arguments
            or "year" not in arguments
            or "number" not in arguments
        ):
            raise ValueError("type, year, and number are required")
        params = UkLegislationGetDocumentXmlParams(**arguments)
        text = fetch_uk_legislation_document_xml(params)
        return [types.TextContent(type="text", text=text[:MAX_RESPONSE_CHARS])]
    except Exception as e:
        log.error(f"Error fetching UK legislation XML: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="uk-legislation-get-document-xml",
        description=(
            "Fetch the full text of a specific UK piece of legislation as "
            "Akoma Ntoso XML."
        ),
        input_schema=UkLegislationGetDocumentXmlParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["uk-legislation-get-document-xml"] = (
    handle_uk_legislation_get_document_xml
)


###################
# Get Document HTML
###################


class UkLegislationGetDocumentHtmlParams(BaseModel):
    """Parameters for fetching a single piece of legislation as HTML."""

    type: str = Field(..., description="Legislation type code (e.g. 'ukpga').")
    year: int = Field(..., description="Year of legislation.")
    number: int = Field(..., description="Sequence number within type/year.")


def fetch_uk_legislation_document_html(
    params: UkLegislationGetDocumentHtmlParams,
) -> str:
    """Call /{type}/{year}/{number}/data.htm; return HTML text."""
    response = http_get(
        f"{BASE_URL}/{params.type}/{params.year}/{params.number}/data.htm",
        headers=_HTML_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_uk_legislation_get_document_html(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the uk-legislation-get-document-html tool call."""
    try:
        if (
            not arguments
            or "type" not in arguments
            or "year" not in arguments
            or "number" not in arguments
        ):
            raise ValueError("type, year, and number are required")
        params = UkLegislationGetDocumentHtmlParams(**arguments)
        text = fetch_uk_legislation_document_html(params)
        return [types.TextContent(type="text", text=text[:MAX_RESPONSE_CHARS])]
    except Exception as e:
        log.error(f"Error fetching UK legislation HTML: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="uk-legislation-get-document-html",
        description="Fetch the HTML rendering of a specific UK piece of legislation.",
        input_schema=UkLegislationGetDocumentHtmlParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["uk-legislation-get-document-html"] = (
    handle_uk_legislation_get_document_html
)


###################
# List Types (Browse Hub)
###################


class UkLegislationListTypesParams(BaseModel):
    """Parameters for the browse hub (none — fixed endpoint)."""


def fetch_uk_legislation_list_types(params: UkLegislationListTypesParams) -> str:
    """Call /browse and return the HTML text describing legislation types."""
    response = http_get(
        f"{BASE_URL}/browse",
        headers=_HTML_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_uk_legislation_list_types(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the uk-legislation-list-types tool call."""
    try:
        params = UkLegislationListTypesParams(**(arguments or {}))
        text = fetch_uk_legislation_list_types(params)
        return [types.TextContent(type="text", text=text[:MAX_RESPONSE_CHARS])]
    except Exception as e:
        log.error(f"Error fetching UK legislation browse hub: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="uk-legislation-list-types",
        description=(
            "Fetch the legislation.gov.uk browse hub (HTML) which lists every "
            "legislation type the service publishes."
        ),
        input_schema=UkLegislationListTypesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["uk-legislation-list-types"] = handle_uk_legislation_list_types


###################
# Changes Feed
###################


class UkLegislationChangesFeedParams(BaseModel):
    """Parameters for the changes-affected feed for a piece of legislation."""

    type: str = Field(..., description="Legislation type code (e.g. 'ukpga').")
    year: int = Field(..., description="Year of legislation.")
    number: int = Field(..., description="Sequence number within type/year.")


def fetch_uk_legislation_changes_feed(params: UkLegislationChangesFeedParams) -> str:
    """Call /changes/affected/{type}/{year}/{number}/data.feed; return Atom XML."""
    response = http_get(
        f"{BASE_URL}/changes/affected/{params.type}/{params.year}/{params.number}/data.feed",
        headers=_ATOM_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_uk_legislation_changes_feed(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the uk-legislation-changes-feed tool call."""
    try:
        if (
            not arguments
            or "type" not in arguments
            or "year" not in arguments
            or "number" not in arguments
        ):
            raise ValueError("type, year, and number are required")
        params = UkLegislationChangesFeedParams(**arguments)
        text = fetch_uk_legislation_changes_feed(params)
        return [types.TextContent(type="text", text=text[:MAX_RESPONSE_CHARS])]
    except Exception as e:
        log.error(f"Error fetching UK legislation changes feed: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="uk-legislation-changes-feed",
        description=(
            "Fetch the Atom feed of changes affecting a specific piece of UK "
            "legislation."
        ),
        input_schema=UkLegislationChangesFeedParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["uk-legislation-changes-feed"] = handle_uk_legislation_changes_feed


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "uk-legislation",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
