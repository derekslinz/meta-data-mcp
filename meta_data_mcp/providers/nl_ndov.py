"""NDOV Loket Data Provider

This module provides an interface to browse and discover Dutch public transport
open data from data.ndovloket.nl. Since the source is a file-based directory
listing, this provider parses the HTML index pages to offer browsing capabilities.

Features:
- Directory browsing using ndov-list-path

Usage:
    The module can be run directly to start a server handling API requests,
    or its components can be imported and used individually.
"""

import logging
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urljoin

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.utils import http_get, to_json_text

# Initialize logging
log = logging.getLogger(__name__)

PROVIDER_ID = "nl-ndov"

# Constants
BASE_URL = "https://data.ndovloket.nl/"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# NDOV Loket Browsing
###################


class NdovListPathParams(BaseModel):
    """Parameters for listing contents of an NDOV Loket directory."""

    path: str = Field(
        default="/",
        description="Path to list (e.g., '/', '/haltes/', '/ns/')",
    )


def list_ndov_path(path: str) -> list[dict]:
    """Fetch and parse the HTML directory listing from NDOV Loket."""
    from urllib.parse import urlparse

    # Ensure path starts and ends correctly
    if not path.startswith("/"):
        path = "/" + path
    last_segment = path.split("/")[-1]
    if not path.endswith("/") and "." not in last_segment:
        path = path + "/"

    url = urljoin(BASE_URL, path.lstrip("/"))

    # Guard against SSRF: ensure the resolved URL stays on the expected host
    parsed = urlparse(url)
    expected = urlparse(BASE_URL)
    if parsed.netloc != expected.netloc or parsed.scheme != expected.scheme:
        raise ValueError(
            f"Resolved URL '{url}' is outside the allowed host '{BASE_URL}'",
        )
    # Apache directory index is HTML — override the kernel's JSON default.
    response = http_get(
        url,
        timeout=10.0,
        headers={"Accept": "text/html"},
        provider=PROVIDER_ID,
    )

    # Simple regex-based parsing of the directory listing
    # Example line: <a href="haltes/">haltes/</a>
    # We look for links that are not parent directories or sorting links
    links = re.findall(r'<a href="([^"?]+)">([^<]+)</a>', response.text)

    entries = []
    for href, text in links:
        if href == "../" or "order=" in href:
            continue

        is_dir = href.endswith("/")
        entries.append(
            {
                "name": text.rstrip("/"),
                "path": urljoin(path, href),
                "type": "directory" if is_dir else "file",
                "url": urljoin(BASE_URL, urljoin(path.lstrip("/"), href)),
            },
        )

    return entries


async def handle_ndov_list_path(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ndov-list-path tool call."""
    try:
        params = NdovListPathParams(**(arguments or {}))
        entries = list_ndov_path(params.path)
        return [types.TextContent(type="text", text=to_json_text(entries))]
    except Exception as e:
        log.error(
            f"Error listing NDOV path {arguments.get('path') if arguments else ''}: {e}",
        )
        raise


TOOLS.append(
    types.Tool(
        name="ndov-list-path",
        description="List files and subdirectories on data.ndovloket.nl for Dutch transit data.",
        input_schema=NdovListPathParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ndov-list-path"] = handle_ndov_list_path


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "nl-ndov",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


# Server initialization
if __name__ == "__main__":
    import anyio

    anyio.run(main)
