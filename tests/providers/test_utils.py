import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.stdio import stdio_server

from meta_data_mcp.utils import create_mcp_server

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
RESOURCE_URI = "test://resource"

RESOURCES = [
    types.Resource(
        name="test-resource",
        uri=RESOURCE_URI,
        description="Test resource",
        mime_type="text/plain",
    ),
]


def handle_test_resource(resource_uri: str) -> str:
    return f"Resource for {resource_uri}"


RESOURCES_HANDLERS = {RESOURCE_URI: handle_test_resource}
TOOLS = [
    types.Tool(
        name="test-tool",
        description="Test tool",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
        },
    ),
]


async def handle_test_tool(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    return [types.TextContent(type="text", text=f"Hello {arguments['name']}!")]


TOOLS_HANDLERS = {"test-tool": handle_test_tool}

# Create server with the greeting tool
server = create_mcp_server("test", RESOURCES, RESOURCES_HANDLERS, TOOLS, TOOLS_HANDLERS)


async def main():
    log.info("Starting server")
    # run the server
    async with stdio_server() as streams:
        log.info("Running server")
        await server.run(streams[0], streams[1], server.create_initialization_options())


# Set up server parameters
server_params = StdioServerParameters(
    command=sys.executable,
    args=[
        "-c",
        (
            "import anyio, sys; "
            f"sys.path[:0] = {[str(ROOT), str(Path(__file__).parent)]}; "
            "from test_utils import main; "
            "anyio.run(main, backend='asyncio')"
        ),
    ],
)


@pytest.mark.asyncio
async def test_client_server_interaction():
    log.info("Starting client")
    async with stdio_client(server_params) as (read, write):
        log.info("Initialized client")
        async with ClientSession(read, write) as session:
            log.info("Initialized session")
            # Initialize the connection
            await session.initialize()
            log.info("Initialized connection")

            # List available tools
            log.info("Listing tools")
            tools = await session.list_tools()
            assert tools.tools[0].name == "test-tool"
            assert tools.tools[0].description == "Test tool"

            resources = await session.list_resources()
            assert len(resources.resources) == 1
            assert str(resources.resources[0].uri) == RESOURCE_URI

            resource = await session.read_resource(RESOURCE_URI)
            assert len(resource.contents) == 1
            assert resource.contents[0].text == f"Resource for {RESOURCE_URI}"

            # Test calling the tool
            log.info("Calling tool")
            result = await session.call_tool(
                "test-tool",
                arguments={
                    "name": "Alice",
                },
            )

            assert isinstance(result, types.CallToolResult)
            assert len(result.content) == 1
            assert result.content[0].type == "text"
            log.info(f"Result: {result.content[0]}")
            assert result.content[0].text == "Hello Alice!"
