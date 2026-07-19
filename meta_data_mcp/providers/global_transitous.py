"""
Transitous Data Provider — door-to-door public transit routing, worldwide.

Transitous (https://transitous.org) is a community-run MOTIS routing
service built on open GTFS feeds from hundreds of transit agencies.
It answers the question the per-agency providers can't: **how long does
it take to get from A to B by public transport**, with transfers, walk
legs, and real-time adjustments where the underlying feed provides
them. No API key; the service asks callers to identify themselves via
User-Agent (the transport kernel does) and keep request rates modest.

The natural flow: ``transitous-geocode`` (place name → stop ids and
coordinates) → ``transitous-plan`` (A→B itineraries with durations,
transfers, and per-leg times) or ``transitous-departures`` (upcoming
departures at one stop).

Responses are compacted server-side: MOTIS itineraries carry per-leg
polyline geometry and step-by-step walking instructions that would
swamp an LLM context; those are dropped, times and structure are kept.
"""

import logging
from typing import Any, List, Sequence

import mcp.types as types
from pydantic import BaseModel, Field

from meta_data_mcp.utils import http_get, to_json_text

log = logging.getLogger(__name__)

PROVIDER_ID = "global-transitous"

BASE_URL = "https://api.transitous.org"

# Registration Variables
RESOURCES: List[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: List[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


def _minutes(seconds: Any) -> float | None:
    if isinstance(seconds, (int, float)):
        return round(seconds / 60, 1)
    return None


###################
# Geocoding (place name → stops)
###################


class TransitousGeocodeParams(BaseModel):
    """Parameters for resolving a place name to stops/locations."""

    text: str = Field(..., description="Place, station, or address to search for")
    language: str | None = Field(
        None, description="Preferred language for results (e.g. 'en', 'nl', 'de')"
    )
    limit: int = Field(default=8, ge=1, le=20, description="Maximum matches to return")


def _compact_match(raw: dict) -> dict:
    # The city-level area gives the human context ("which Centraal?").
    city = next(
        (
            a.get("name")
            for a in raw.get("areas") or []
            if isinstance(a, dict) and a.get("adminLevel") == 8
        ),
        None,
    )
    return {
        "name": raw.get("name"),
        "id": raw.get("id"),
        "type": raw.get("type"),
        "lat": raw.get("lat"),
        "lon": raw.get("lon"),
        "city": city,
        "country": raw.get("country"),
    }


def geocode(params: TransitousGeocodeParams) -> List[dict]:
    """Resolve a place name to stop ids and coordinates."""
    query: dict[str, Any] = {"text": params.text}
    if params.language:
        query["language"] = params.language
    response = http_get(
        f"{BASE_URL}/api/v1/geocode",
        params=query,
        timeout=15.0,
        provider=PROVIDER_ID,
        cache_ttl=3600.0,
    )
    data = response.json()
    if not isinstance(data, list):
        return []
    return [_compact_match(m) for m in data[: params.limit] if isinstance(m, dict)]


async def handle_transitous_geocode(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the transitous-geocode tool call."""
    try:
        if not arguments or "text" not in arguments:
            raise ValueError("text is required")
        params = TransitousGeocodeParams(**arguments)
        matches = geocode(params)
        return [types.TextContent(type="text", text=to_json_text(matches))]
    except Exception as e:
        log.error(f"Error geocoding via Transitous: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="transitous-geocode",
        description=(
            "Resolve a place, station, or address name to transit stop ids and "
            "coordinates (worldwide). Use the returned id or 'lat,lon' with "
            "transitous-plan and transitous-departures."
        ),
        inputSchema=TransitousGeocodeParams.model_json_schema(),
    )
)
TOOLS_HANDLERS["transitous-geocode"] = handle_transitous_geocode

###################
# Journey planning (A → B transit times)
###################


class TransitousPlanParams(BaseModel):
    """Parameters for planning a door-to-door transit journey."""

    from_place: str = Field(
        ...,
        description=(
            "Origin: 'lat,lon' (e.g. '52.3791,4.9003') or a stop id from "
            "transitous-geocode"
        ),
    )
    to_place: str = Field(
        ...,
        description="Destination: 'lat,lon' or a stop id from transitous-geocode",
    )
    time: str | None = Field(
        None,
        description=(
            "Departure (or arrival, with arrive_by) time as ISO 8601 "
            "(e.g. '2026-07-19T08:30:00Z'). Defaults to now."
        ),
    )
    arrive_by: bool = Field(
        default=False,
        description="Treat 'time' as the latest arrival instead of departure",
    )
    max_itineraries: int = Field(
        default=4, ge=1, le=10, description="Maximum itineraries to return"
    )


def _compact_leg(raw: dict) -> dict:
    origin = raw.get("from") or {}
    dest = raw.get("to") or {}
    leg = {
        "mode": raw.get("mode"),
        "line": raw.get("routeShortName"),
        "headsign": raw.get("headsign"),
        "from": origin.get("name"),
        "to": dest.get("name"),
        "departure": raw.get("startTime"),
        "arrival": raw.get("endTime"),
        "duration_minutes": _minutes(raw.get("duration")),
        "real_time": raw.get("realTime"),
    }
    return {k: v for k, v in leg.items() if v is not None}


def _compact_itinerary(raw: dict) -> dict:
    return {
        "departure": raw.get("startTime"),
        "arrival": raw.get("endTime"),
        "duration_minutes": _minutes(raw.get("duration")),
        "transfers": raw.get("transfers"),
        "legs": [
            _compact_leg(leg) for leg in raw.get("legs") or [] if isinstance(leg, dict)
        ],
    }


def plan_journey(params: TransitousPlanParams) -> dict:
    """Plan A→B transit itineraries and compact them to times + structure."""
    query: dict[str, Any] = {
        "fromPlace": params.from_place.strip(),
        "toPlace": params.to_place.strip(),
        "numItineraries": params.max_itineraries,
    }
    if params.time:
        query["time"] = params.time
    if params.arrive_by:
        query["arriveBy"] = "true"

    response = http_get(
        f"{BASE_URL}/api/v3/plan",
        params=query,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    data = response.json()

    itineraries = [
        _compact_itinerary(it)
        for it in data.get("itineraries") or []
        if isinstance(it, dict)
    ]
    # 'direct' holds non-transit alternatives (pure walk/bike) when the
    # trip is short enough that they beat transit.
    direct = [
        _compact_itinerary(it)
        for it in data.get("direct") or []
        if isinstance(it, dict)
    ]
    return {"itineraries": itineraries, "direct": direct}


async def handle_transitous_plan(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the transitous-plan tool call."""
    try:
        if not arguments:
            raise ValueError("from_place and to_place are required")
        params = TransitousPlanParams(**arguments)
        result = plan_journey(params)
        return [types.TextContent(type="text", text=to_json_text(result))]
    except Exception as e:
        log.error(f"Error planning journey via Transitous: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="transitous-plan",
        description=(
            "Door-to-door public transit journey planning, worldwide: A→B "
            "itineraries with total travel time, transfers, and per-leg "
            "departure/arrival times (real-time-adjusted where available). "
            "Origins/destinations as 'lat,lon' or stop ids from "
            "transitous-geocode."
        ),
        inputSchema=TransitousPlanParams.model_json_schema(),
    )
)
TOOLS_HANDLERS["transitous-plan"] = handle_transitous_plan

###################
# Stop departures
###################


class TransitousDeparturesParams(BaseModel):
    """Parameters for upcoming departures at one stop."""

    stop_id: str = Field(
        ..., description="Stop id from transitous-geocode (e.g. 'nl-OpenOV_...')"
    )
    time: str | None = Field(None, description="ISO 8601 start time; defaults to now")
    limit: int = Field(
        default=10, ge=1, le=30, description="Maximum departures to return"
    )


def get_departures(params: TransitousDeparturesParams) -> List[dict]:
    """Fetch upcoming departures at a stop."""
    query: dict[str, Any] = {"stopId": params.stop_id, "n": params.limit}
    if params.time:
        query["time"] = params.time
    response = http_get(
        f"{BASE_URL}/api/v1/stoptimes",
        params=query,
        timeout=15.0,
        provider=PROVIDER_ID,
    )
    data = response.json()

    departures = []
    for st in data.get("stopTimes") or []:
        if not isinstance(st, dict):
            continue
        place = st.get("place") or {}
        entry = {
            "line": st.get("routeShortName"),
            "mode": st.get("mode"),
            "headsign": st.get("headsign"),
            "stop": place.get("name"),
            "departure": place.get("departure"),
            "scheduled_departure": place.get("scheduledDeparture"),
            "track": place.get("track"),
            "real_time": st.get("realTime"),
        }
        departures.append({k: v for k, v in entry.items() if v is not None})
    # The API's ``n`` is advisory (same-minute departures come back
    # grouped) — enforce the limit here.
    return departures[: params.limit]


async def handle_transitous_departures(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the transitous-departures tool call."""
    try:
        if not arguments or "stop_id" not in arguments:
            raise ValueError("stop_id is required")
        params = TransitousDeparturesParams(**arguments)
        departures = get_departures(params)
        return [types.TextContent(type="text", text=to_json_text(departures))]
    except Exception as e:
        log.error(f"Error fetching Transitous departures: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="transitous-departures",
        description=(
            "Upcoming departures at any transit stop worldwide (by stop id "
            "from transitous-geocode) — line, destination, scheduled vs "
            "real-time departure, track."
        ),
        inputSchema=TransitousDeparturesParams.model_json_schema(),
    )
)
TOOLS_HANDLERS["transitous-departures"] = handle_transitous_departures


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-transitous", RESOURCES, RESOURCES_HANDLERS, TOOLS, TOOLS_HANDLERS
    )

    await run_server(server, transport, port, host)


# Server initialization
if __name__ == "__main__":
    import anyio

    anyio.run(main)
