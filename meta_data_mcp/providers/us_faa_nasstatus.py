"""FAA National Airspace System (NAS) Status Provider

This module exposes the FAA's NAS Status feed, which reports current
ground stops, ground delays, arrival delays, and other airspace
advisories.

API peculiarities:
    The NAS Status feed serves XML (`application/xml`), not JSON. This
    provider sends `Accept: application/xml` and returns the raw response
    text. Consumers are expected to parse XML themselves. Responses are
    truncated to 20,000 characters to keep tool output bounded.

    Only the top-level `/airport-status-information` endpoint is well
    documented in practice. The event-type filters
    (`/airport-events?eventType=GS|GD|ARRDLY`) reflect the categories the
    FAA exposes in their public widget; some deployments may only return
    the consolidated status payload rather than a true filtered list. The
    handlers here surface whatever the API returns for that URL.

License / source:
    FAA NAS Status data is a US Federal Government work and not subject
    to copyright in the United States.

Features:
- Current airport status XML feed
- Ground stops filter
- Departure delays filter
- Arrival delays filter

Usage:
    The module can be run directly to start an MCP server handling these
    tools.
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel

from meta_data_mcp.utils import MAX_RESPONSE_CHARS, http_get

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "us-faa-nasstatus"
BASE_URL = "https://nasstatus.faa.gov/api"
XML_HEADERS = {"Accept": "application/xml"}

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# Airport status
###################


class FAAAirportStatusParams(BaseModel):
    """Parameters for fetching the current airport status feed (no inputs)."""


def fetch_faa_airport_status(_: FAAAirportStatusParams) -> str:
    """Fetch the FAA airport status XML feed.

    Returns the raw response text (XML); consumers must parse it.
    """
    response = http_get(
        f"{BASE_URL}/airport-status-information",
        headers=XML_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_faa_airport_status(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the faa-airport-status tool call."""
    try:
        params = FAAAirportStatusParams(**(arguments or {}))
        text = fetch_faa_airport_status(params)
        return [types.TextContent(type="text", text=text[:MAX_RESPONSE_CHARS])]
    except Exception as e:
        log.error(f"Error fetching FAA airport status: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="faa-airport-status",
        description=(
            "Get the FAA NAS Status airport status feed (XML). Returned as "
            "raw text capped at 20,000 characters; consumers must parse XML."
        ),
        input_schema=FAAAirportStatusParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["faa-airport-status"] = handle_faa_airport_status


###################
# Ground stops
###################


class FAAGroundStopsParams(BaseModel):
    """Parameters for fetching active ground stops (no inputs)."""


def fetch_faa_ground_stops(_: FAAGroundStopsParams) -> str:
    """Fetch active ground stops from the NAS Status feed.

    Some deployments do not expose a true filtered list and instead
    return the consolidated status payload. The raw response text is
    returned either way.
    """
    response = http_get(
        f"{BASE_URL}/airport-events",
        params={"eventType": "GS"},
        headers=XML_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_faa_ground_stops(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the faa-ground-stops tool call."""
    try:
        params = FAAGroundStopsParams(**(arguments or {}))
        text = fetch_faa_ground_stops(params)
        return [types.TextContent(type="text", text=text[:MAX_RESPONSE_CHARS])]
    except Exception as e:
        log.error(f"Error fetching FAA ground stops: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="faa-ground-stops",
        description=(
            "Get active FAA ground stops (eventType=GS). Returns raw XML text; "
            "some deployments fall back to the full status payload."
        ),
        input_schema=FAAGroundStopsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["faa-ground-stops"] = handle_faa_ground_stops


###################
# Departure delays
###################


class FAADepartureDelaysParams(BaseModel):
    """Parameters for fetching ground departure delays (no inputs)."""


def fetch_faa_departure_delays(_: FAADepartureDelaysParams) -> str:
    """Fetch active ground departure delays from the NAS Status feed."""
    response = http_get(
        f"{BASE_URL}/airport-events",
        params={"eventType": "GD"},
        headers=XML_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_faa_departure_delays(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the faa-departure-delays tool call."""
    try:
        params = FAADepartureDelaysParams(**(arguments or {}))
        text = fetch_faa_departure_delays(params)
        return [types.TextContent(type="text", text=text[:MAX_RESPONSE_CHARS])]
    except Exception as e:
        log.error(f"Error fetching FAA departure delays: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="faa-departure-delays",
        description=(
            "Get active FAA ground departure delays (eventType=GD). Returns raw "
            "XML text; some deployments fall back to the full status payload."
        ),
        input_schema=FAADepartureDelaysParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["faa-departure-delays"] = handle_faa_departure_delays


###################
# Arrival delays
###################


class FAAArrivalDelaysParams(BaseModel):
    """Parameters for fetching arrival delays (no inputs)."""


def fetch_faa_arrival_delays(_: FAAArrivalDelaysParams) -> str:
    """Fetch active arrival delays from the NAS Status feed."""
    response = http_get(
        f"{BASE_URL}/airport-events",
        params={"eventType": "ARRDLY"},
        headers=XML_HEADERS,
        provider=PROVIDER_ID,
    )
    return response.text


async def handle_faa_arrival_delays(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the faa-arrival-delays tool call."""
    try:
        params = FAAArrivalDelaysParams(**(arguments or {}))
        text = fetch_faa_arrival_delays(params)
        return [types.TextContent(type="text", text=text[:MAX_RESPONSE_CHARS])]
    except Exception as e:
        log.error(f"Error fetching FAA arrival delays: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="faa-arrival-delays",
        description=(
            "Get active FAA arrival delays (eventType=ARRDLY). Returns raw XML "
            "text; some deployments fall back to the full status payload."
        ),
        input_schema=FAAArrivalDelaysParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["faa-arrival-delays"] = handle_faa_arrival_delays


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "us-faa-nasstatus",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
