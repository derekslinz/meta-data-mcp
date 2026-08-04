"""us-cisa-kev provider.

CISA Known Exploited Vulnerabilities (KEV) Catalog — the authoritative
US-CISA list of vulnerabilities that have been observed exploited in the
wild. Updated multiple times per week; entries include a remediation
due-date for US federal agencies under BOD 22-01.

The upstream catalog is a single JSON document; tools here filter
client-side by CVE id, vendor, ransomware-known-use, or date_added range.

Homepage: https://www.cisa.gov/known-exploited-vulnerabilities-catalog
License: CISA KEV is in the public domain (US federal work).
Auth: None required.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.app_vulnerability_v1 import URI as VULN_APP_URI
from meta_data_mcp.utils import (
    create_mcp_server,
    http_get,
    run_server,
    serialize_for_llm,
)

log = logging.getLogger(__name__)

PROVIDER_ID = "us-cisa-kev"
CATALOG_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/"
    "known_exploited_vulnerabilities.json"
)

# Catalog is ~2MB and refreshed several times per week. Cache for one hour
# by default to keep subsequent tool calls fast without serving stale data.
_DEFAULT_CACHE_TTL_SECONDS = 3600

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


def _fetch_catalog() -> dict:
    """Download and return the full KEV catalog payload."""
    response = http_get(
        CATALOG_URL,
        cache_ttl=_DEFAULT_CACHE_TTL_SECONDS,
        provider=PROVIDER_ID,
    )
    return response.json()


###################
# cisa-kev-list
###################


class CisaKevListParams(BaseModel):
    """Parameters for cisa-kev-list."""

    vendor: str | None = Field(
        None,
        description="Case-insensitive substring filter on vendorProject (e.g. 'cisco').",
    )
    product: str | None = Field(
        None,
        description="Case-insensitive substring filter on product (e.g. 'ios xe').",
    )
    known_ransomware: bool | None = Field(
        None,
        description="If true, only return entries with knownRansomwareCampaignUse == 'Known'.",
    )
    date_added_after: str | None = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="ISO date (YYYY-MM-DD); return entries added on/after this date.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1500,
        description="Maximum number of entries to return (default 100, max 1500).",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Pagination offset (0-indexed).",
    )


def fetch_cisa_kev_list(params: CisaKevListParams) -> dict:
    """Return a filtered slice of the KEV catalog."""
    catalog = _fetch_catalog()
    entries = catalog.get("vulnerabilities", [])

    vendor_q = params.vendor.lower() if params.vendor else None
    product_q = params.product.lower() if params.product else None

    def _matches(entry: dict) -> bool:
        if vendor_q and vendor_q not in (entry.get("vendorProject", "").lower()):
            return False
        if product_q and product_q not in (entry.get("product", "").lower()):
            return False
        if params.known_ransomware is True and (
            entry.get("knownRansomwareCampaignUse", "Unknown") != "Known"
        ):
            return False
        if params.known_ransomware is False and (
            entry.get("knownRansomwareCampaignUse", "Unknown") == "Known"
        ):
            return False
        # NOTE: this lexicographic compare is safe only because CISA emits
        # dates in ISO YYYY-MM-DD format; if that ever changes the filter
        # will return wrong results silently.
        return not (
            params.date_added_after
            and entry.get("dateAdded", "") < params.date_added_after
        )

    filtered = [e for e in entries if _matches(e)]
    sliced = filtered[params.offset : params.offset + params.limit]
    return {
        "catalog_version": catalog.get("catalogVersion"),
        "date_released": catalog.get("dateReleased"),
        "total_in_catalog": catalog.get("count"),
        "match_count": len(filtered),
        "returned": len(sliced),
        "offset": params.offset,
        "vulnerabilities": sliced,
    }


async def handle_cisa_kev_list(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cisa-kev-list tool call."""
    params = CisaKevListParams(**(arguments or {}))
    data = fetch_cisa_kev_list(params)
    return [types.TextContent(type="text", text=serialize_for_llm(data))]


TOOLS.append(
    types.Tool(
        name="cisa-kev-list",
        description=(
            "List entries from the CISA Known Exploited Vulnerabilities catalog, "
            "optionally filtered by vendor, product, ransomware-known-use, or "
            "date_added range. The catalog is the authoritative US-government "
            "list of actively exploited vulnerabilities (~1000 entries)."
        ),
        input_schema=CisaKevListParams.model_json_schema(),
        # MCP Apps binding: render via the vulnerability app. Use the alias
        # keyword (``_meta=``) — see tests/test_ui_resource.py for the footgun.
        _meta={"ui": {"resourceUri": VULN_APP_URI}},
    ),
)
TOOLS_HANDLERS["cisa-kev-list"] = handle_cisa_kev_list


###################
# cisa-kev-get
###################


class CisaKevGetParams(BaseModel):
    """Parameters for cisa-kev-get."""

    cve_id: str = Field(
        ...,
        min_length=1,
        description="CVE id, e.g. CVE-2021-44228.",
    )


def fetch_cisa_kev_get(params: CisaKevGetParams) -> dict:
    """Look up a single KEV entry by CVE id (case-insensitive)."""
    catalog = _fetch_catalog()
    needle = params.cve_id.strip().upper()
    for entry in catalog.get("vulnerabilities", []):
        if entry.get("cveID", "").upper() == needle:
            return entry
    return {"cveID": params.cve_id, "in_kev": False}


async def handle_cisa_kev_get(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the cisa-kev-get tool call."""
    params = CisaKevGetParams(**(arguments or {}))
    data = fetch_cisa_kev_get(params)
    return [types.TextContent(type="text", text=serialize_for_llm(data))]


TOOLS.append(
    types.Tool(
        name="cisa-kev-get",
        description=(
            "Look up a single CVE in the CISA KEV catalog. Returns the catalog "
            "entry (with vendorProject, product, vulnerabilityName, dateAdded, "
            "dueDate, knownRansomwareCampaignUse, requiredAction) or "
            "{cveID, in_kev: false} if the CVE is not on the list."
        ),
        input_schema=CisaKevGetParams.model_json_schema(),
        _meta={"ui": {"resourceUri": VULN_APP_URI}},
    ),
)
TOOLS_HANDLERS["cisa-kev-get"] = handle_cisa_kev_get


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    server = create_mcp_server(
        "us-cisa-kev",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )
    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
