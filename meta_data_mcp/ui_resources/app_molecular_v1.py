"""``ui://meta-data-mcp/app/molecular/v1`` — Phase 5 molecular structure app.

Renders 3D molecular structures from two providers via 3Dmol.js:

- **PubChem** (``global_pubchem``) — small-molecule chemistry. Tool:
  ``pubchem-compound``. Returns CID + SMILES + InChI + properties; the
  bundle constructs a ``…/cid/<CID>/SDF?record_type=3d`` URL to pull the
  actual 3D coordinates on demand.
- **RCSB PDB** (``global_rcsb_pdb``) — macromolecular crystallography.
  Tool: ``pdb-entry``. Returns entry metadata (title, resolution,
  experimental method); the bundle constructs a
  ``https://files.rcsb.org/download/<ID>.pdb`` URL for the actual atoms.

The viewer chooses a sensible default style per format: cartoon for
chains (PDB / mmCIF) so secondary structure is legible, and stick +
small spheres for ligands and small molecules. 3Dmol.js handles
rotation / zoom / pan natively — no custom orbit code in the bundle.

3Dmol.js is loaded from its canonical CDN
(``https://3dmol.org/build/3Dmol-min.js``, BSD-3) rather than inlined —
the library is ~700KB minified and would blow the 100KB Phase 6b
budget if bundled. The HTML comment in the bundle documents the CDN
origin for host CSP review. If WebGL is unavailable (Phase 6c headless
smoke, or a host that blocks the CDN), the UI shell still mounts and
the metadata panel renders — only the GL canvas stays empty.

Payload contract (designed for this app, not a shape primitive):

    {
      "structure": {
        "format":     "pdb" | "mmcif" | "sdf" | "smiles" | "inchi",
        "data":       "<inline structure text>",   // for small bundles
        "url":        "<remote URL>",                // for large structs
        "identifier": "1ABC" | "2244"                // CID, PDB id, etc.
      },
      "metadata": {
        "name":   "...",                             // short display id
        "title":  "...",                             // long human title
        "source": "PubChem" | "RCSB PDB",
        "attrs":  { <provider-specific properties> }
      }
    }

The bundle also accepts raw ``pdb-entry`` / ``pubchem-compound`` tool
results and adapts them inline — see ``adaptPdbEntry`` /
``adaptPubchemCompound``. That keeps the Python providers ignorant of
the envelope: any host that forwards a tool result over postMessage
(``type: "tool_result"``) hydrates the viewer with no glue code.

postMessage protocol matches every Phase 5 app (invented in Phase 3's
discovery app):

    host → app:   { type: "tool_result" | "render", id?, tool?, payload }
    app → host:   { type: "tool_call", id, name, arguments }

See ``Plans/linear-swimming-pond.md`` §5 — molecular is one of the
seven Phase 5 apps; PR #71 (vulnerability) is the canonical
multi-provider recipe this app follows.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files

from mcp import types

from meta_data_mcp.utils import register_ui_resource

URI: str = "ui://meta-data-mcp/app/molecular/v1"

# Surfaced in the host's resource catalog. Documents the bidirectional
# postMessage protocol so a host integrator can wire app↔host without
# reading the bundle source. Inherited from the Phase 3 discovery app.
_DESCRIPTION = (
    "Molecular structure app: 3D viewer for proteins (RCSB PDB) and small "
    "molecules (PubChem). Renders via 3Dmol.js (BSD-3, CDN-loaded). "
    "Cartoon style for chains, stick+sphere for ligands. Wraps "
    "pubchem-compound and pdb-entry. "
    "postMessage protocol — "
    "host→app: {type: 'tool_result'|'render', id?, tool?, payload}; "
    "app→host: {type: 'tool_call', id, name, arguments} (Phase 3 invention). "
    "Payload envelope: {structure: {format, data?|url?, identifier}, "
    "metadata: {name, title, source, attrs}}."
)

_HTML: str = (files("meta_data_mcp.ui_resources") / "app_molecular_v1.html").read_text(
    encoding="utf-8",
)


def register(
    resources: list[types.Resource],
    resources_handlers: dict[str, Callable[[str], str | bytes]],
) -> str:
    """Append the molecular app resource to the server's catalog.

    Returns the canonical URI so callers (and tests) can assert against it
    without duplicating the constant.
    """
    return register_ui_resource(
        name="app/molecular/v1",
        html=_HTML,
        description=_DESCRIPTION,
        resources=resources,
        resources_handlers=resources_handlers,
    )
