"""Template for MCP server definitions.

This template provides a standardized structure for creating MCP server modules.
Each module should follow this pattern to ensure consistency across the codebase.

Module Structure:
1. Imports and Configuration
2. Global Constants
3. Registration Variables
4. Endpoint Sections (one per endpoint):
   - Pydantic Models (Input/Output)
   - Data Fetching Function
   - Handler Function
   - Tool Registration

Usage:
    Copy this template and replace the placeholders with actual implementation.

Kernel contract:
    Always call HTTP through ``http_get`` / ``http_post`` and always pass
    ``provider=PROVIDER_ID``. That single kwarg enables retry-on-429/5xx
    and pre-response network-error retry, partitioned response caching,
    health-registry feedback, and URL-redacted ProviderError translation.
    Direct ``httpx.get`` / ``httpx.post`` is a regression — every kernel
    guarantee opts out with it.
"""

# 1. Standard Imports Section
import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.fields import NonEmptyStr
from meta_data_mcp.utils import http_get, to_json_text  # add http_post if you POST

# Initialize logging
log = logging.getLogger(__name__)

# 2. Constants Section
PROVIDER_ID = "service-name"  # MUST match the server_name in registry.py
BASE_URL = "https://api.example.com/v1"

# 3. Registration Variables
RESOURCES: list[Any] = []  # resources that will be registered by each endpoints
RESOURCES_HANDLERS: dict[
    str,
    Any,
] = {}  # resources handlers that will be registered by each endpoints
TOOLS: list[types.Tool] = []  # tools that will be registered by each endpoints
TOOLS_HANDLERS: dict[
    str,
    Any,
] = {}  # tools handlers that will be registered by each endpoints

###################
# [Endpoint Name]
###################


# 1. Input/Output Models
class EndpointParams(BaseModel):
    """Input parameters for the endpoint."""

    param1: NonEmptyStr = Field(..., description="Description of param1")
    param2: int | None = Field(None, description="Description of param2")


class EndpointResult(BaseModel):
    """Single result item from the endpoint."""

    field1: str = Field(..., description="Description of field1")
    field2: int = Field(..., description="Description of field2")


class EndpointResponse(BaseModel):
    """Complete response from the endpoint."""

    results: list[EndpointResult] = Field(..., description="List of results")


# 2. Data Fetching Function
def fetch_endpoint_data(params: EndpointParams) -> EndpointResponse:
    """Fetch data from the endpoint.

    Args:
        params: EndpointParams object containing all query parameters

    Returns:
        EndpointResponse object containing the results

    Raises:
        meta_data_mcp.errors.ProviderError: translated kernel exception on
            HTTP / network failure. Pre-response network errors and 429/5xx
            responses are retried with capped backoff by ``http_get``.

    """
    endpoint = f"{BASE_URL}/endpoint"
    response = http_get(
        endpoint,
        params=params.model_dump(exclude_none=True),
        provider=PROVIDER_ID,
    )
    return EndpointResponse(**response.json())


# 3. Handler Function
async def handle_endpoint(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    """Handle the tool execution for this endpoint.

    Args:
        arguments: Dictionary of tool arguments

    Returns:
        Sequence of content objects

    Raises:
        Exception: If the handling fails

    """
    response = fetch_endpoint_data(EndpointParams(**(arguments or {})))
    return [types.TextContent(type="text", text=to_json_text(response.model_dump()))]


# 4. Tool Registration
TOOLS.append(
    types.Tool(
        name="endpoint-name",
        description="Description of what this endpoint does",
        input_schema=EndpointParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["endpoint-name"] = handle_endpoint

# --- Optional: MCP Apps UI binding -----------------------------------------
# To render this tool's result in a host-side iframe app (Claude Desktop's
# Apps tab, MCP Inspector's App tab, etc.), register a ui:// resource via
# ``utils.register_ui_resource`` and bind it to the tool with the alias
# keyword ``_meta=`` (NOT ``meta=`` — see test_ui_resource.py for the
# regression that pins this footgun).
#
# from meta_data_mcp.utils import register_ui_resource
# APP_URI = register_ui_resource(
#     name="endpoint-app",
#     html=ENDPOINT_APP_HTML,
#     description="Custom UI for endpoint-name.",
#     resources=RESOURCES,
#     resources_handlers=RESOURCES_HANDLERS,
# )
# TOOLS.append(
#     types.Tool(
#         name="endpoint-name",
#         description="...",
#         input_schema=EndpointParams.model_json_schema(),
#         _meta={"ui": {"resourceUri": APP_URI}},
#     )
# )
# ---------------------------------------------------------------------------


###################
# [Another Endpoint Name]
###################


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "service.name",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


# Server initialization (if module is run directly)
if __name__ == "__main__":
    import anyio

    anyio.run(main)
