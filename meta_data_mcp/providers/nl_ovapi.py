"""OVapi Data Provider — Dutch public transport, live.

OVapi (https://gtfs.ovapi.nl/) is the community-run service that
redistributes the Dutch NDOV open transit data in two forms:

- **Bulk feeds** at ``gtfs.ovapi.nl`` — GTFS zips and GTFS-RT protobuf
  files (trip updates, vehicle positions, alerts). Those are downloads,
  not queryable JSON, so the ``ovapi-gtfs-feeds`` tool lists them (name,
  URL, kind) for callers that want to fetch a feed themselves.
- **A JSON API** at ``v0.ovapi.nl`` — live, queryable: every line in
  the country, per-line stop networks and real-time vehicle positions,
  and per-stop real-time departures. The remaining tools wrap this.

The natural flow: ``ovapi-lines`` (filtered search — the full national
list is thousands of entries, so filtering happens server-side) →
``ovapi-line-details`` (stops with their timing-point codes, plus live
vehicles) → ``ovapi-stop-departures`` (real-time departures for one or
more timing-point codes).

Note: ``v0.ovapi.nl`` is HTTP-only (no TLS endpoint is offered); the
data is public transit schedules, nothing sensitive rides the wire.
"""

import logging
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urljoin, urlparse

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.utils import http_get, to_json_text

log = logging.getLogger(__name__)

PROVIDER_ID = "nl-ovapi"

API_BASE = "http://v0.ovapi.nl"  # HTTP-only; the service offers no TLS endpoint
GTFS_BASE = "https://gtfs.ovapi.nl/"

# The national line list changes rarely; departures change constantly.
LINES_CACHE_TTL = 3600.0
LINE_DETAILS_CACHE_TTL = 60.0

_LINE_ID_RE = re.compile(r"^[A-Za-z0-9:_-]+$")
_TPC_LIST_RE = re.compile(r"^[0-9]+(,[0-9]+)*$")

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}

###################
# GTFS feed listing (gtfs.ovapi.nl)
###################


class OvapiGtfsFeedsParams(BaseModel):
    """Parameters for listing bulk GTFS / GTFS-RT feed files."""

    path: str = Field(default="/", description="Directory to list (e.g. '/', '/new/')")


def list_gtfs_feeds(path: str) -> list[dict]:
    """Parse the Cherokee directory index at gtfs.ovapi.nl."""
    if not path.startswith("/"):
        path = "/" + path
    url = urljoin(GTFS_BASE, path.lstrip("/"))

    # Guard against SSRF: the resolved URL must stay on the expected host.
    parsed, expected = urlparse(url), urlparse(GTFS_BASE)
    if parsed.netloc != expected.netloc or parsed.scheme != expected.scheme:
        raise ValueError(
            f"Resolved URL '{url}' is outside the allowed host '{GTFS_BASE}'",
        )

    # Directory index is HTML — override the kernel's JSON default.
    response = http_get(
        url,
        timeout=15.0,
        headers={"Accept": "text/html"},
        provider=PROVIDER_ID,
    )

    entries = []
    # Cherokee emits rows as <a class="link" href="...">name</a> — allow
    # arbitrary attributes before href.
    for href, text in re.findall(
        r'<a [^>]*?href="([^"?]+)"[^>]*>([^<]+)</a>',
        response.text,
    ):
        if href in ("../", "/") or href.startswith(("?", "/cherokee_themes")):
            continue
        is_dir = href.endswith("/")
        kind = "directory" if is_dir else "file"
        if href.endswith(".pb"):
            kind = "gtfs-rt protobuf"
        elif href.endswith(".zip"):
            kind = "gtfs zip"
        entries.append(
            {
                "name": text.strip().rstrip("/"),
                "kind": kind,
                "url": urljoin(url, href),
            },
        )
    return entries


async def handle_ovapi_gtfs_feeds(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ovapi-gtfs-feeds tool call."""
    try:
        params = OvapiGtfsFeedsParams(**(arguments or {}))
        entries = list_gtfs_feeds(params.path)
        return [types.TextContent(type="text", text=to_json_text(entries))]
    except Exception as e:
        log.error(f"Error listing OVapi GTFS feeds: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ovapi-gtfs-feeds",
        description=(
            "List the bulk GTFS zip and GTFS-RT protobuf feed files published "
            "at gtfs.ovapi.nl (Dutch national transit), with download URLs."
        ),
        input_schema=OvapiGtfsFeedsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ovapi-gtfs-feeds"] = handle_ovapi_gtfs_feeds

###################
# Line search (v0.ovapi.nl/line/)
###################


class OvapiLinesParams(BaseModel):
    """Parameters for searching the national line list."""

    query: str | None = Field(
        None,
        description=(
            "Substring match against line number, line name, or destination "
            "(case-insensitive)"
        ),
    )
    transport_type: str | None = Field(
        None,
        description="Filter by mode: BUS, TRAM, METRO, TRAIN, BOAT",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of lines to return",
    )


def _compact_line(line_id: str, raw: dict) -> dict:
    # No operator field: 97% of the national list is keyed NL_* with
    # DataOwnerCode "NL", so neither the id prefix nor DataOwnerCode
    # names the actual operator. The operator surfaces per-departure
    # and per-vehicle as OperatorCode in the other tools.
    return {
        "id": line_id,
        "line": raw.get("LinePublicNumber"),
        "name": raw.get("LineName"),
        "transport_type": raw.get("TransportType"),
        "destination": raw.get("DestinationName50"),
        "direction": raw.get("LineDirection"),
    }


def search_lines(params: OvapiLinesParams) -> dict:
    """Fetch the national line list and filter it server-side.

    The unfiltered response is thousands of entries — filtering here
    keeps tool results at LLM-friendly size.
    """
    response = http_get(
        f"{API_BASE}/line/",
        timeout=30.0,
        provider=PROVIDER_ID,
        cache_ttl=LINES_CACHE_TTL,
    )
    data = response.json()

    query = params.query.lower() if params.query else None
    mode = params.transport_type.upper() if params.transport_type else None

    matches = []
    total_matched = 0
    for line_id, raw in data.items():
        if not isinstance(raw, dict):
            continue
        if mode and (raw.get("TransportType") or "").upper() != mode:
            continue
        if query:
            haystack = " ".join(
                str(raw.get(k) or "")
                for k in ("LinePublicNumber", "LineName", "DestinationName50")
            ).lower()
            if query not in haystack:
                continue
        total_matched += 1
        if len(matches) < params.limit:
            matches.append(_compact_line(line_id, raw))

    return {"total_matched": total_matched, "returned": len(matches), "lines": matches}


async def handle_ovapi_lines(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ovapi-lines tool call."""
    try:
        params = OvapiLinesParams(**(arguments or {}))
        result = search_lines(params)
        return [types.TextContent(type="text", text=to_json_text(result))]
    except Exception as e:
        log.error(f"Error searching OVapi lines: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ovapi-lines",
        description=(
            "Search all Dutch public transport lines (bus, tram, metro, train, "
            "ferry) by number, name, destination, or mode. Returns line ids "
            "usable with ovapi-line-details."
        ),
        input_schema=OvapiLinesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ovapi-lines"] = handle_ovapi_lines

###################
# Line details (v0.ovapi.nl/line/{id})
###################


class OvapiLineDetailsParams(BaseModel):
    """Parameters for fetching one line's stops and live vehicles."""

    line_id: str = Field(
        ...,
        description=(
            "Line id from ovapi-lines, e.g. 'GVB_13_1' "
            "(dataowner_lineplanningnumber_direction)"
        ),
    )


def _compact_stop(raw: dict) -> dict:
    return {
        "order": raw.get("UserStopOrderNumber"),
        "timing_point_code": raw.get("TimingPointCode"),
        "name": raw.get("TimingPointName"),
        "town": raw.get("TimingPointTown"),
        "lat": raw.get("Latitude"),
        "lon": raw.get("Longitude"),
    }


def get_line_details(line_id: str) -> dict:
    """Fetch one line: metadata, stop network, live vehicle positions."""
    if not _LINE_ID_RE.match(line_id):
        raise ValueError(f"Invalid line_id: {line_id!r}")

    response = http_get(
        f"{API_BASE}/line/{line_id}",
        timeout=15.0,
        provider=PROVIDER_ID,
        cache_ttl=LINE_DETAILS_CACHE_TTL,
    )
    payload = response.json().get(line_id) or {}

    # Network: {journey_pattern_code: {stop_order: stop_dict}}
    patterns = {}
    for pattern_code, stops in (payload.get("Network") or {}).items():
        if not isinstance(stops, dict):
            continue
        ordered = sorted(
            (_compact_stop(s) for s in stops.values() if isinstance(s, dict)),
            key=lambda s: s["order"] if isinstance(s["order"], int) else 0,
        )
        patterns[pattern_code] = ordered

    # Actuals: live vehicles currently on the line.
    vehicles = []
    for raw in (payload.get("Actuals") or {}).values():
        if not isinstance(raw, dict):
            continue
        vehicles.append(
            {
                "journey_number": raw.get("JourneyNumber"),
                "destination": raw.get("DestinationName50"),
                "lat": raw.get("Latitude"),
                "lon": raw.get("Longitude"),
                "operator": raw.get("OperatorCode"),
            },
        )

    line_raw = payload.get("Line") or {}
    return {
        "line": _compact_line(line_id, line_raw),
        "server_time": payload.get("ServerTime"),
        "stop_patterns": patterns,
        "live_vehicles": vehicles,
    }


async def handle_ovapi_line_details(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ovapi-line-details tool call."""
    try:
        if not arguments or "line_id" not in arguments:
            raise ValueError("line_id is required")
        params = OvapiLineDetailsParams(**arguments)
        result = get_line_details(params.line_id)
        return [types.TextContent(type="text", text=to_json_text(result))]
    except Exception as e:
        log.error(f"Error fetching OVapi line details: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ovapi-line-details",
        description=(
            "Fetch one Dutch transit line: metadata, ordered stops per journey "
            "pattern (with timing-point codes for ovapi-stop-departures), and "
            "live vehicle positions."
        ),
        input_schema=OvapiLineDetailsParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ovapi-line-details"] = handle_ovapi_line_details

###################
# Real-time departures (v0.ovapi.nl/tpc/{codes})
###################


class OvapiStopDeparturesParams(BaseModel):
    """Parameters for real-time departures at timing points."""

    timing_point_codes: str = Field(
        ...,
        description=(
            "One or more numeric timing-point codes, comma-separated "
            "(e.g. '30005093' or '30005093,30005094'). Find codes via "
            "ovapi-line-details."
        ),
    )
    limit: int = Field(
        default=15,
        ge=1,
        le=50,
        description="Maximum departures per stop",
    )


def _compact_pass(raw: dict) -> dict:
    return {
        "line": raw.get("LinePublicNumber"),
        "destination": raw.get("DestinationName50"),
        "transport_type": raw.get("TransportType"),
        "operator": raw.get("OperatorCode"),
        "target_departure": raw.get("TargetDepartureTime"),
        "expected_departure": raw.get("ExpectedDepartureTime"),
        "status": raw.get("TripStopStatus"),
    }


def get_stop_departures(params: OvapiStopDeparturesParams) -> dict:
    """Fetch real-time departures for one or more timing-point codes."""
    codes = params.timing_point_codes.replace(" ", "")
    if not _TPC_LIST_RE.match(codes):
        raise ValueError(
            "timing_point_codes must be numeric codes separated by commas, "
            f"got {params.timing_point_codes!r}",
        )

    # Real-time data: never cached.
    response = http_get(
        f"{API_BASE}/tpc/{codes}",
        timeout=15.0,
        provider=PROVIDER_ID,
    )
    data = response.json()

    stops = {}
    for code, entry in data.items():
        if not isinstance(entry, dict):
            continue
        stop_info = entry.get("Stop") or {}
        passes = [
            _compact_pass(p)
            for p in (entry.get("Passes") or {}).values()
            if isinstance(p, dict)
        ]
        passes.sort(key=lambda p: p.get("expected_departure") or "")
        stops[code] = {
            "stop": {
                "name": stop_info.get("TimingPointName"),
                "town": stop_info.get("TimingPointTown"),
                "lat": stop_info.get("Latitude"),
                "lon": stop_info.get("Longitude"),
            },
            "departures": passes[: params.limit],
            "total_upcoming": len(passes),
        }
    return stops


async def handle_ovapi_stop_departures(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the ovapi-stop-departures tool call."""
    try:
        if not arguments or "timing_point_codes" not in arguments:
            raise ValueError("timing_point_codes is required")
        params = OvapiStopDeparturesParams(**arguments)
        result = get_stop_departures(params)
        return [types.TextContent(type="text", text=to_json_text(result))]
    except Exception as e:
        log.error(f"Error fetching OVapi departures: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="ovapi-stop-departures",
        description=(
            "Real-time departures (with delays) for Dutch transit stops by "
            "timing-point code — target vs expected time, line, destination, "
            "operator, status."
        ),
        input_schema=OvapiStopDeparturesParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["ovapi-stop-departures"] = handle_ovapi_stop_departures


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "nl-ovapi",
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
