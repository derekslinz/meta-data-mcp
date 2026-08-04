"""US SEC EDGAR Provider

This module exposes the SEC EDGAR public APIs at data.sec.gov for company
filings and XBRL-tagged financial facts, plus the company ticker mapping
file at www.sec.gov.

License note:
    SEC filings and structured data are US Government works and are not
    subject to copyright. The SEC requires that all programmatic clients
    send a descriptive User-Agent including a contact email; this module
    relies on meta_data_mcp.utils.http_get, which sets such a User-Agent
    (override via the META_DATA_MCP_CONTACT environment variable).

Features:
- Company submissions feed (recent filings) by CIK
- XBRL company-concept facts (one tagged concept across history) by CIK
- XBRL company-facts (all tagged concepts) by CIK
- XBRL frames (one concept across all filers for a calendar period)
- Full ticker->CIK mapping list
- Lookup CIK by exchange ticker
- Fuzzy search company by name

Usage:
    The module can be run directly to start an MCP server handling these
    tools, or its components can be imported individually.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI
from meta_data_mcp.utils import http_get, serialize_for_llm

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-sec-edgar"
BASE_URL = "https://data.sec.gov"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


def _pad_cik(cik: str | int) -> str:
    """Return the CIK as a zero-padded 10-digit string, as required by EDGAR."""
    raw = str(cik).strip().lstrip("0") or "0"
    return raw.zfill(10)


###################
# Company submissions
###################


class EdgarCompanySubmissionsParams(BaseModel):
    """Parameters for fetching a company's submissions feed."""

    cik: str = Field(
        ...,
        description="SEC CIK (Central Index Key). Leading zeros are added automatically.",
    )


def fetch_edgar_company_submissions(params: EdgarCompanySubmissionsParams) -> dict:
    """Call /submissions/CIK{padded}.json on EDGAR."""
    padded = _pad_cik(params.cik)
    response = http_get(
        f"{BASE_URL}/submissions/CIK{padded}.json",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_edgar_get_company_submissions(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the edgar-get-company-submissions tool call."""
    try:
        if not arguments or "cik" not in arguments:
            raise ValueError("cik is required")
        params = EdgarCompanySubmissionsParams(**arguments)
        data = fetch_edgar_company_submissions(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching EDGAR company submissions: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="edgar-get-company-submissions",
        description="Fetch a company's SEC EDGAR submissions feed (recent filings) by CIK.",
        input_schema=EdgarCompanySubmissionsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["edgar-get-company-submissions"] = handle_edgar_get_company_submissions


###################
# Company concept (single XBRL tag)
###################


class EdgarCompanyConceptParams(BaseModel):
    """Parameters for fetching a single XBRL concept for a company."""

    cik: str = Field(..., description="SEC CIK; padded to 10 digits automatically.")
    concept: str = Field(
        ...,
        description="US-GAAP concept tag, e.g. 'AccountsPayableCurrent', 'Revenues'.",
    )


def fetch_edgar_company_concept(params: EdgarCompanyConceptParams) -> dict:
    """Call /api/xbrl/companyconcept/CIK{padded}/us-gaap/{concept}.json on EDGAR."""
    padded = _pad_cik(params.cik)
    url = (
        f"{BASE_URL}/api/xbrl/companyconcept/CIK{padded}/us-gaap/{params.concept}.json"
    )
    response = http_get(url, provider=PROVIDER_ID)
    return response.json()


def _edgar_company_concept_to_shape_payload(data: dict) -> dict:
    """Adapt EDGAR's ``{cik, taxonomy, tag, label, units: {USD: [{end, val,
    fy, fp, ...}]}}`` company-concept response to the
    ``ui://meta-data-mcp/shape/timeseries/v1`` payload.

    Each reporting unit (e.g. USD, shares) becomes its own series. The
    ``end`` date is the x-axis (period end), ``val`` is the y value.
    """
    units = data.get("units")
    if not isinstance(units, dict):
        return {"points": [], "axes": {"x": "Period end", "y": "Value"}}

    points: list[dict[str, Any]] = []
    unit_set: set[str] = set()
    for unit, rows in units.items():
        if not isinstance(rows, list):
            continue
        unit_set.add(str(unit))
        for row in rows:
            if not isinstance(row, dict):
                continue
            end = row.get("end")
            value = row.get("val")
            if not isinstance(end, str):
                continue
            if value is None or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)):
                continue
            points.append({"date": end, "value": value, "series": str(unit)})

    points.sort(key=lambda p: (p["date"], p["series"]))
    label = data.get("label") or data.get("tag") or "Value"
    if len(unit_set) == 1:
        y_label = f"{label} ({unit_set.pop()})"
    else:
        y_label = label
    return {
        "points": points,
        "axes": {"x": "Period end", "y": y_label},
    }


async def handle_edgar_get_company_concept(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the edgar-get-company-concept tool call.

    Returns the ``ui://meta-data-mcp/shape/timeseries/v1`` payload so
    MCP Apps hosts render the chart inline.
    """
    try:
        if not arguments or "cik" not in arguments or "concept" not in arguments:
            raise ValueError("cik and concept are required")
        params = EdgarCompanyConceptParams(**arguments)
        data = fetch_edgar_company_concept(params)
        payload = _edgar_company_concept_to_shape_payload(data)
        return [types.TextContent(type="text", text=serialize_for_llm(payload))]
    except Exception as e:
        log.error(f"Error fetching EDGAR company concept: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="edgar-get-company-concept",
        description="Fetch historical values for a single US-GAAP XBRL concept on one company.",
        input_schema=EdgarCompanyConceptParams.model_json_schema(),
        _meta={"ui": {"resourceUri": TIMESERIES_URI}},
    ),
)
TOOLS_HANDLERS["edgar-get-company-concept"] = handle_edgar_get_company_concept


###################
# Company facts (all tagged concepts)
###################


class EdgarCompanyFactsParams(BaseModel):
    """Parameters for fetching all XBRL facts for a company."""

    cik: str = Field(..., description="SEC CIK; padded to 10 digits automatically.")


def fetch_edgar_company_facts(params: EdgarCompanyFactsParams) -> dict:
    """Call /api/xbrl/companyfacts/CIK{padded}.json on EDGAR."""
    padded = _pad_cik(params.cik)
    response = http_get(
        f"{BASE_URL}/api/xbrl/companyfacts/CIK{padded}.json",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_edgar_get_company_facts(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the edgar-get-company-facts tool call."""
    try:
        if not arguments or "cik" not in arguments:
            raise ValueError("cik is required")
        params = EdgarCompanyFactsParams(**arguments)
        data = fetch_edgar_company_facts(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching EDGAR company facts: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="edgar-get-company-facts",
        description="Fetch all XBRL-tagged facts for a single company (full company-facts payload).",
        input_schema=EdgarCompanyFactsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["edgar-get-company-facts"] = handle_edgar_get_company_facts


###################
# Frames (concept across filers for one calendar period)
###################


class EdgarFramesParams(BaseModel):
    """Parameters for fetching an XBRL frame slice."""

    concept: str = Field(..., description="US-GAAP concept tag, e.g. 'Revenues'.")
    year: int = Field(..., description="Calendar year, e.g. 2023.")
    quarter: int = Field(..., description="Calendar quarter 1-4.")
    unit: str = Field(default="USD", description="Reporting unit, default 'USD'.")


def fetch_edgar_frames(params: EdgarFramesParams) -> dict:
    """Call /api/xbrl/frames/us-gaap/{concept}/{unit}/CY{year}Q{q}I.json on EDGAR."""
    url = (
        f"{BASE_URL}/api/xbrl/frames/us-gaap/{params.concept}/{params.unit}/"
        f"CY{params.year}Q{params.quarter}I.json"
    )
    response = http_get(url, provider=PROVIDER_ID)
    return response.json()


async def handle_edgar_get_frames(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the edgar-get-frames tool call."""
    try:
        if (
            not arguments
            or "concept" not in arguments
            or "year" not in arguments
            or "quarter" not in arguments
        ):
            raise ValueError("concept, year, and quarter are required")
        params = EdgarFramesParams(**arguments)
        data = fetch_edgar_frames(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching EDGAR XBRL frame: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="edgar-get-frames",
        description="Fetch one US-GAAP XBRL concept across all filers for a single calendar quarter (instant).",
        input_schema=EdgarFramesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["edgar-get-frames"] = handle_edgar_get_frames


###################
# Ticker -> CIK mapping list
###################


class EdgarListTickersParams(BaseModel):
    """Parameters for listing the EDGAR ticker->CIK mapping."""


def fetch_edgar_list_tickers(params: EdgarListTickersParams) -> dict:
    """Fetch the full company_tickers.json mapping from www.sec.gov."""
    response = http_get(TICKERS_URL, provider=PROVIDER_ID)
    return response.json()


async def handle_edgar_list_tickers(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the edgar-list-tickers tool call."""
    try:
        params = EdgarListTickersParams(**(arguments or {}))
        data = fetch_edgar_list_tickers(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching EDGAR ticker list: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="edgar-list-tickers",
        description="Fetch the full SEC EDGAR company_tickers.json (ticker -> CIK + title mapping).",
        input_schema=EdgarListTickersParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["edgar-list-tickers"] = handle_edgar_list_tickers


###################
# Lookup company by ticker
###################


class EdgarSearchByTickerParams(BaseModel):
    """Parameters for looking up a company by exchange ticker."""

    ticker: str = Field(..., description="Exchange ticker symbol, e.g. 'AAPL'.")


def fetch_edgar_search_by_ticker(params: EdgarSearchByTickerParams) -> dict | None:
    """Fetch company_tickers.json and return the entry matching `ticker` (case-insensitive)."""
    response = http_get(TICKERS_URL, provider=PROVIDER_ID)
    data = response.json()
    target = params.ticker.strip().upper()
    # company_tickers.json is a dict of indexed objects: {"0": {"cik_str": ..., "ticker": ..., "title": ...}, ...}
    for entry in data.values():
        if str(entry.get("ticker", "")).upper() == target:
            padded = _pad_cik(entry.get("cik_str", ""))
            return {
                "cik": padded,
                "cik_str": entry.get("cik_str"),
                "ticker": entry.get("ticker"),
                "title": entry.get("title"),
            }
    return None


async def handle_edgar_search_by_ticker(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the edgar-search-company-by-ticker tool call."""
    try:
        if not arguments or "ticker" not in arguments:
            raise ValueError("ticker is required")
        params = EdgarSearchByTickerParams(**arguments)
        match = fetch_edgar_search_by_ticker(params)
        if match is None:
            return [
                types.TextContent(
                    type="text",
                    text=f"No company found for ticker '{params.ticker}'.",
                ),
            ]
        return [types.TextContent(type="text", text=str(match)[:20000])]
    except Exception as e:
        log.error(f"Error searching EDGAR by ticker: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="edgar-search-company-by-ticker",
        description="Look up an SEC-registered company (CIK + title) by exchange ticker symbol.",
        input_schema=EdgarSearchByTickerParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["edgar-search-company-by-ticker"] = handle_edgar_search_by_ticker


###################
# Fuzzy search by company name
###################


class EdgarSearchByNameParams(BaseModel):
    """Parameters for fuzzy-searching companies by name."""

    name: str = Field(..., description="Substring of the company name to match.")
    limit: int = Field(default=10, description="Maximum number of results.")


def fetch_edgar_search_by_name(params: EdgarSearchByNameParams) -> list:
    """Fetch company_tickers.json and return entries whose `title` contains `name` (case-insensitive)."""
    response = http_get(TICKERS_URL, provider=PROVIDER_ID)
    data = response.json()
    target = params.name.strip().lower()
    matches = []
    for entry in data.values():
        title = str(entry.get("title", ""))
        if target in title.lower():
            matches.append(
                {
                    "cik": _pad_cik(entry.get("cik_str", "")),
                    "cik_str": entry.get("cik_str"),
                    "ticker": entry.get("ticker"),
                    "title": title,
                },
            )
            if len(matches) >= params.limit:
                break
    return matches


async def handle_edgar_search_by_name(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the edgar-search-company-by-name tool call."""
    try:
        if not arguments or "name" not in arguments:
            raise ValueError("name is required")
        params = EdgarSearchByNameParams(**arguments)
        matches = fetch_edgar_search_by_name(params)
        return [types.TextContent(type="text", text=str(matches)[:20000])]
    except Exception as e:
        log.error(f"Error searching EDGAR by name: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="edgar-search-company-by-name",
        description="Fuzzy-search SEC-registered companies by a substring of their official name.",
        input_schema=EdgarSearchByNameParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["edgar-search-company-by-name"] = handle_edgar_search_by_name


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-sec-edgar",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
