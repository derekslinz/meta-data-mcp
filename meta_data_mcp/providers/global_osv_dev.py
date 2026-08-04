"""global-osv-dev provider.

OSV.dev — Google's distributed Open Source Vulnerabilities database. Aggregates
advisories from GitHub (GHSA), PyPI Advisory DB, RustSec, OSS-Fuzz, npm, Go,
Maven, Debian / Alpine / Rocky, and others into a unified schema. Free,
no-auth REST/JSON API; supports lookups by OSV/CVE id and by package
identity.

Homepage: https://osv.dev
License: Vulnerability data is published under Apache 2.0.
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
    http_post,
    run_server,
    serialize_for_llm,
)

log = logging.getLogger(__name__)

PROVIDER_ID = "global-osv-dev"
BASE_URL = "https://api.osv.dev/v1"

RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# osv-get-vulnerability
###################


class OsvGetVulnParams(BaseModel):
    """Parameters for osv-get-vulnerability."""

    vuln_id: str = Field(
        ...,
        min_length=1,
        # Restrict to characters that appear in OSV / CVE / GHSA / PYSEC / GO
        # / RUSTSEC / SNYK identifiers. Crucially rejects '/' so the value
        # cannot break out of the /v1/vulns/{id} path component.
        pattern=r"^[A-Za-z0-9._:\-]+$",
        description=(
            "Vulnerability id in any of the supported namespaces: "
            "CVE-YYYY-N, GHSA-xxxx-xxxx-xxxx, PYSEC-YYYY-N, GO-YYYY-N, "
            "RUSTSEC-YYYY-N, OSV-YYYY-N, etc. Restricted to alphanumerics, "
            "dot, underscore, colon, and hyphen."
        ),
    )


def fetch_osv_get_vuln(params: OsvGetVulnParams) -> Any:
    """Fetch a single OSV vulnerability record by id."""
    # Path-parameter URL — OSV requires the id in the path, not as a query.
    response = http_get(
        f"{BASE_URL}/vulns/{params.vuln_id}",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_osv_get_vuln(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the osv-get-vulnerability tool call."""
    params = OsvGetVulnParams(**(arguments or {}))
    data = fetch_osv_get_vuln(params)
    return [types.TextContent(type="text", text=serialize_for_llm(data))]


TOOLS.append(
    types.Tool(
        name="osv-get-vulnerability",
        description=(
            "Fetch a single vulnerability record from OSV.dev by id. Accepts "
            "CVE, GHSA, PYSEC, GO, RUSTSEC, OSV, and similar namespace ids."
        ),
        input_schema=OsvGetVulnParams.model_json_schema(),
        # MCP Apps binding: render via the vulnerability app.
        _meta={"ui": {"resourceUri": VULN_APP_URI}},
    ),
)
TOOLS_HANDLERS["osv-get-vulnerability"] = handle_osv_get_vuln


###################
# osv-query-package
###################


class OsvQueryPackageParams(BaseModel):
    """Parameters for osv-query-package."""

    name: str = Field(
        ...,
        min_length=1,
        description=(
            "Package name (e.g. 'requests' for PyPI, '@types/node' for npm, "
            "'github.com/gorilla/mux' for Go)."
        ),
    )
    ecosystem: str = Field(
        ...,
        min_length=1,
        description=(
            "Package ecosystem: 'PyPI', 'npm', 'Go', 'Maven', 'NuGet', "
            "'crates.io', 'RubyGems', 'Packagist', 'Debian', 'Alpine', etc. "
            "Exact spelling matters; see https://ossf.github.io/osv-schema/."
        ),
    )
    version: str | None = Field(
        None,
        description=(
            "Specific version to query (e.g. '2.31.0'). When omitted, all "
            "vulnerabilities affecting any version of the package are returned."
        ),
    )


def fetch_osv_query_package(params: OsvQueryPackageParams) -> Any:
    """Query OSV.dev for all vulnerabilities affecting a package (optionally pinned)."""
    body: dict[str, Any] = {
        "package": {"name": params.name, "ecosystem": params.ecosystem},
    }
    if params.version is not None:
        body["version"] = params.version
    response = http_post(f"{BASE_URL}/query", json=body, provider=PROVIDER_ID)
    return response.json()


async def handle_osv_query_package(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the osv-query-package tool call."""
    params = OsvQueryPackageParams(**(arguments or {}))
    data = fetch_osv_query_package(params)
    return [types.TextContent(type="text", text=serialize_for_llm(data))]


TOOLS.append(
    types.Tool(
        name="osv-query-package",
        description=(
            "Query OSV.dev for all vulnerabilities affecting a package in a given "
            "ecosystem, optionally pinned to a specific version. Returns advisories "
            "aggregated from GHSA, PYSEC, RustSec, Go, npm, and other namespace "
            "feeds in the OSV schema."
        ),
        input_schema=OsvQueryPackageParams.model_json_schema(),
        _meta={"ui": {"resourceUri": VULN_APP_URI}},
    ),
)
TOOLS_HANDLERS["osv-query-package"] = handle_osv_query_package


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    server = create_mcp_server(
        "global-osv-dev",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )
    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
