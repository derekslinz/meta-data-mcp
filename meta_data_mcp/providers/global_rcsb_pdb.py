"""RCSB PDB Provider

This module provides interfaces to the RCSB Protein Data Bank (PDB) API,
the primary source for 3D biological macromolecular structures.

License: PDB data is in the public domain.
See https://www.rcsb.org/pages/usage-policy for details.

API Documentation: https://data.rcsb.org/redoc/index.html
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
PROVIDER_ID = "global-rcsb-pdb"
BASE_URL = "https://data.rcsb.org/rest/v1"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# PDB Entry
###################


class PDBEntryParams(BaseModel):
    """Parameters for fetching PDB entry data."""

    entry_id: str = Field(..., description="PDB Entry ID (e.g. '4HHB')")


def fetch_pdb_entry(params: PDBEntryParams) -> Any:
    """Fetch entry metadata from RCSB PDB."""
    response = http_get(
        f"{BASE_URL}/core/entry/{params.entry_id}",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_pdb_entry(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the pdb-entry tool call."""
    try:
        params = PDBEntryParams(**(arguments or {}))
        data = fetch_pdb_entry(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching PDB entry: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="pdb-entry",
        description="Fetch 3D macromolecular structure metadata from RCSB PDB by entry ID.",
        input_schema=PDBEntryParams.model_json_schema(),
        # MCP Apps binding: render via the molecular structure app. The
        # entry endpoint returns metadata (title, resolution, method); the
        # app derives a files.rcsb.org/download/<ID>.pdb URL for the atoms.
        # pdb-polymer-entity is intentionally NOT bound: it returns one
        # chain's metadata only, with no separable structure URL — the
        # viewer would have nothing to render that the parent entry
        # doesn't already provide.
        _meta={"ui": {"resourceUri": MOLECULAR_APP_URI}},
    ),
)
TOOLS_HANDLERS["pdb-entry"] = handle_pdb_entry

###################
# PDB Polymer Entity
###################


class PDBPolymerParams(BaseModel):
    """Parameters for fetching PDB polymer entity data."""

    entry_id: str = Field(..., description="PDB Entry ID (e.g. '4HHB')")
    entity_id: str = Field(..., description="Entity ID within the entry (e.g. '1')")


def fetch_pdb_polymer(params: PDBPolymerParams) -> Any:
    """Fetch polymer entity data from RCSB PDB."""
    response = http_get(
        f"{BASE_URL}/core/polymer_entity/{params.entry_id}/{params.entity_id}",
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_pdb_polymer(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the pdb-polymer-entity tool call."""
    try:
        params = PDBPolymerParams(**(arguments or {}))
        data = fetch_pdb_polymer(params)
        return [
            types.TextContent(
                type="text",
                text=to_json_text(data, max_chars=MAX_RESPONSE_CHARS),
            ),
        ]
    except Exception as e:
        log.error(f"Error fetching PDB polymer entity: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="pdb-polymer-entity",
        description="Fetch polymer entity details for a PDB entry.",
        input_schema=PDBPolymerParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["pdb-polymer-entity"] = handle_pdb_polymer


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-rcsb-pdb",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
