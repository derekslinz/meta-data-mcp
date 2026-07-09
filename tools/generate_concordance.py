"""Regenerate ``meta_data_mcp/harmonize_data.py`` from ISO 3166 source data.

Usage:
    uv run python tools/generate_concordance.py [path/to/all.json]

Without an argument, fetches the dataset from the canonical source:
https://github.com/lukes/ISO-3166-Countries-with-Regional-Codes
(all/all.json, CC BY 4.0). With an argument, reads a local copy.

The output module is checked in — regeneration is only needed when ISO
3166 changes (new country, code reassignment), which is rare enough
that a build-time fetch would be pure fragility.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE_URL = (
    "https://raw.githubusercontent.com/lukes/"
    "ISO-3166-Countries-with-Regional-Codes/master/all/all.json"
)
OUT_PATH = (
    Path(__file__).resolve().parent.parent / "meta_data_mcp" / "harmonize_data.py"
)

HEADER = '''"""ISO 3166-1 country concordance — generated, do not edit by hand.

Regenerate with ``uv run python tools/generate_concordance.py``.
Source: lukes/ISO-3166-Countries-with-Regional-Codes (CC BY 4.0),
itself derived from the UN Statistics Division M49 listing.

Each row: (english_name, alpha2, alpha3, m49_numeric).
Provider-specific quirks (Eurostat EL/UK, Kosovo XK, statistical
aggregates) live in :mod:`meta_data_mcp.harmonize`, not here — this
table is pure ISO.
"""

from __future__ import annotations

COUNTRIES: tuple[tuple[str, str, str, str], ...] = (
'''

FOOTER = ")\n"


def main() -> None:
    if len(sys.argv) > 1:
        raw = json.loads(Path(sys.argv[1]).read_text())
    else:
        import httpx

        raw = httpx.get(SOURCE_URL, timeout=30.0).raise_for_status().json()

    rows = sorted(
        (e["name"], e["alpha-2"], e["alpha-3"], e["country-code"]) for e in raw
    )
    lines = [HEADER]
    for name, a2, a3, m49 in rows:
        lines.append(f"    ({name!r}, {a2!r}, {a3!r}, {m49!r}),\n")
    lines.append(FOOTER)
    OUT_PATH.write_text("".join(lines))
    print(f"wrote {OUT_PATH} ({len(rows)} countries)")


if __name__ == "__main__":
    main()
