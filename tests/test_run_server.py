"""Tests for the run_server() utility in meta_data_mcp.utils."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meta_data_mcp.utils import create_mcp_server, run_server


@pytest.fixture
def server():
    """Minimal MCP server for transport tests."""
    return create_mcp_server("test-run-server")


@pytest.mark.asyncio
async def test_run_server_stdio(server):
    """run_server with transport='stdio' delegates to stdio_server."""
    mock_streams = (AsyncMock(), AsyncMock())

    @asynccontextmanager
    async def fake_stdio_server():
        yield mock_streams

    # stdio_server is imported lazily inside run_server; patch at its source location.
    with patch("mcp.server.stdio.stdio_server", fake_stdio_server):
        with patch.object(server, "run", new_callable=AsyncMock) as mock_run:
            await run_server(server, transport="stdio")

    mock_run.assert_awaited_once_with(
        mock_streams[0],
        mock_streams[1],
        server.create_initialization_options(),
    )


@pytest.mark.asyncio
async def test_run_server_sse_binds_localhost(server):
    """run_server with transport='sse' configures Uvicorn on 127.0.0.1."""

    class FakeUvicornServer:
        def __init__(self, config):
            pass

        async def serve(self):
            pass

    # Patch uvicorn.Config to capture kwargs, and Server to avoid real network binding.
    with (
        patch("uvicorn.Config") as mock_config,
        patch("uvicorn.Server", FakeUvicornServer),
    ):
        mock_config.return_value = MagicMock()
        await run_server(server, transport="sse", port=9123)

    mock_config.assert_called_once()
    _, kwargs = mock_config.call_args
    assert kwargs["host"] == "127.0.0.1", (
        "SSE server must bind to 127.0.0.1, not 0.0.0.0"
    )
    assert kwargs["port"] == 9123


@pytest.mark.asyncio
async def test_run_server_sse_debug_disabled(server):
    """run_server with transport='sse' creates Starlette with debug=False."""

    class FakeUvicornServer:
        def __init__(self, config):
            pass

        async def serve(self):
            pass

    with (
        patch("uvicorn.Config", return_value=MagicMock()),
        patch("uvicorn.Server", FakeUvicornServer),
        patch("starlette.applications.Starlette") as mock_starlette,
    ):
        mock_starlette.return_value = MagicMock()
        await run_server(server, transport="sse", port=8000)

    mock_starlette.assert_called_once()
    _, kwargs = mock_starlette.call_args
    assert kwargs.get("debug") is False, (
        "SSE Starlette app must not run with debug=True"
    )


@pytest.mark.asyncio
async def test_run_server_unknown_transport_raises(server):
    """run_server raises ValueError for an unrecognised transport name."""
    with pytest.raises(ValueError, match="Unknown transport"):
        await run_server(server, transport="grpc")
