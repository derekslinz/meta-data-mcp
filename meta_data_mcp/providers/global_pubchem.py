"""PubChem Provider

This module provides interfaces to the NCBI PubChem API, the world's largest
collection of freely accessible chemical information.

License: PubChem data is in the public domain.
See https://pubchem.ncbi.nlm.nih.gov/docs/programmatic-access for details.

API Documentation: https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.app_molecular_v1 import URI as MOLECULAR_APP_URI
from meta_data_mcp.utils import MAX_RESPONSE_CHARS, http_get, to_json_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-pubchem"
BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# PubChem Compound
###################


class PubChemCompoundParams(BaseModel):
    """Parameters for fetching PubChem compound data."""

    identifier: str = Field(..., description="Compound name, CID, SMILES, or InChI")
    namespace: str = Field(
        default="name",
        description="Type of identifier (name, cid, smiles, inchi)",
    )


def fetch_pubchem_compound(params: PubChemCompoundParams) -> Any:
    """Fetch compound data from PubChem."""
    response = http_get(
        f"{BASE_URL}/compound/{params.namespace}/{params.identifier}/JSON",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_pubchem_compound(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the pubchem-compound tool call."""
    try:
        params = PubChemCompoundParams(**(arguments or {}))
        data = fetch_pubchem_compound(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching PubChem compound: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="pubchem-compound",
        description="Fetch chemical compound data from PubChem by name, CID, etc.",
        input_schema=PubChemCompoundParams.model_json_schema(),
        # MCP Apps binding: render via the molecular structure app. The
        # compound endpoint returns CID + properties; the app derives a
        # /cid/<CID>/SDF?record_type=3d URL for the actual 3D coordinates.
        # pubchem-substance is intentionally NOT bound: substances are
        # depositor-supplied records that often lack 3D coordinates, and
        # the viewer would render an empty canvas.
        _meta={"ui": {"resourceUri": MOLECULAR_APP_URI}},
    ),
)
TOOLS_HANDLERS["pubchem-compound"] = handle_pubchem_compound

###################
# PubChem Substance
###################


class PubChemSubstanceParams(BaseModel):
    """Parameters for fetching PubChem substance data."""

    sid: int = Field(..., description="PubChem Substance ID (SID)")


def fetch_pubchem_substance(params: PubChemSubstanceParams) -> Any:
    """Fetch substance data from PubChem."""
    response = http_get(
        f"{BASE_URL}/substance/sid/{params.sid}/JSON",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_pubchem_substance(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the pubchem-substance tool call."""
    try:
        params = PubChemSubstanceParams(**(arguments or {}))
        data = fetch_pubchem_substance(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching PubChem substance: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="pubchem-substance",
        description="Fetch chemical substance data from PubChem by SID.",
        input_schema=PubChemSubstanceParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["pubchem-substance"] = handle_pubchem_substance


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-pubchem",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
