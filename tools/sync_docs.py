#!/usr/bin/env python3
"""Sync dynamic values across documentation files.

Run automatically as a pre-commit hook or manually:
    uv run python tools/sync_docs.py

Patches in-place:
    README.md          — provider count, plugin section header
    server.json        — version, provider count in description/pitch
    smithery.yaml      — (no dynamic values currently)

Values are derived from the single source of truth:
    meta_data_mcp/__init__.py   → __version__
    meta_data_mcp/registry.py   → _STATIC_ENTRIES count
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _provider_count() -> int:
    sys.path.insert(0, str(REPO))
    from meta_data_mcp.registry import _STATIC_ENTRIES

    return len(_STATIC_ENTRIES)


def _version() -> str:
    sys.path.insert(0, str(REPO))
    from meta_data_mcp import __version__

    return __version__


def _patch(path: Path, pattern: str, replacement: str) -> bool:
    text = path.read_text()
    new_text, n = re.subn(pattern, replacement, text)
    if n and new_text != text:
        path.write_text(new_text)
        return True
    return False


def sync_readme(count: int) -> list[str]:
    path = REPO / "README.md"
    changed = []
    patterns = [
        (
            r"routes user requests to \d+ open-data sources",
            f"routes user requests to {count} open-data sources",
        ),
        (r"bundles \d+ \*plugins\*", f"bundles {count} *plugins*"),
        (r"from the \d+ bundled plugins", f"from the {count} bundled plugins"),
        (r"## Bundled plugins \(\d+\)", f"## Bundled plugins ({count})"),
        # roadmap shipped line
        (
            r"Expand provider coverage beyond the current \d+\.",
            f"Expand provider coverage beyond the current {count}.",
        ),
    ]
    for pattern, replacement in patterns:
        if _patch(path, pattern, replacement):
            changed.append(f"README.md: updated provider count to {count}")
    return changed


def sync_server_json(count: int, version: str) -> list[str]:
    path = REPO / "server.json"
    if not path.exists():
        return []
    changed = []
    patterns = [
        # version fields
        (r'"version":\s*"[\d.]+"', f'"version": "{version}"'),
        # description count
        (r"Query \d+ open data", f"Query {count} open data"),
        # pitch count
        (r"One MCP server, \d+ open data", f"One MCP server, {count} open data"),
        # tool description count
        (
            r"Semantic search across \d+ providers",
            f"Semantic search across {count} providers",
        ),
        # list all providers count
        (
            r"List all available providers.*?\.",
            "List all available providers with metadata and health scores.",
        ),
    ]
    for pattern, replacement in patterns:
        if _patch(path, pattern, replacement):
            changed.append("server.json: updated")
    return changed


def main() -> int:
    count = _provider_count()
    version = _version()

    all_changes: list[str] = []
    all_changes += sync_readme(count)
    all_changes += sync_server_json(count, version)

    if all_changes:
        print(
            f"sync_docs: patched {len(all_changes)} value(s) (providers={count}, version={version})",
        )
        for change in all_changes:
            print(f"  {change}")
        return 1  # signal pre-commit to re-stage the modified files
    print(f"sync_docs: all docs consistent (providers={count}, version={version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
