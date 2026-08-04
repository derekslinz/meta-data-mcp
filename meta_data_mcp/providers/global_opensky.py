"""OpenSky Network Flight Tracking Provider

This module provides interfaces to the OpenSky Network REST API, which
exposes live and historical ADS-B flight tracking data for aircraft
worldwide.

License: OpenSky data is available for non-commercial research and
educational use under the OpenSky Network Terms of Use. See
https://opensky-network.org/about/terms-of-use for full details.

Environment variables:
- None required for anonymous access. Anonymous users get reduced rate
  limits.

Features:
- Live state vectors for all aircraft (optionally bounded by a bbox)
- State vector lookup for a specific aircraft
- Historical flight intervals per aircraft
- Airport arrival and departure logs

Notes:
- All time parameters are Unix epoch seconds.
- The API is heavily rate-limited; back off aggressively on 429.

Usage:
    The module can be run directly to start a server handling API requests.

"""

import logging
from collections.abc import Sequence
from typing import Any

from mcp import types
from pydantic import BaseModel, Field

from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI
from meta_data_mcp.utils import http_get, serialize_for_llm, to_geofeatures_text

# Initialize logging
log = logging.getLogger(__name__)

# Constants
PROVIDER_ID = "global-opensky"
BASE_URL = "https://opensky-network.org/api"

# Registration Variables
RESOURCES: list[Any] = []
RESOURCES_HANDLERS: dict[str, Any] = {}
TOOLS: list[types.Tool] = []
TOOLS_HANDLERS: dict[str, Any] = {}


###################
# States All
###################


class OpenSkyStatesAllParams(BaseModel):
    """Parameters for fetching all live aircraft state vectors."""

    lamin: float | None = Field(
        None,
        description="Lower-bound latitude of bounding box (WGS84)",
    )
    lomin: float | None = Field(
        None,
        description="Lower-bound longitude of bounding box (WGS84)",
    )
    lamax: float | None = Field(
        None,
        description="Upper-bound latitude of bounding box (WGS84)",
    )
    lomax: float | None = Field(
        None,
        description="Upper-bound longitude of bounding box (WGS84)",
    )
    time: int | None = Field(
        None,
        description="Unix epoch seconds; omit for the latest snapshot",
    )


def fetch_states_all(params: OpenSkyStatesAllParams) -> dict:
    """Fetch all live aircraft state vectors from OpenSky."""
    query_params: dict[str, Any] = {}
    if params.lamin is not None:
        query_params["lamin"] = params.lamin
    if params.lomin is not None:
        query_params["lomin"] = params.lomin
    if params.lamax is not None:
        query_params["lamax"] = params.lamax
    if params.lomax is not None:
        query_params["lomax"] = params.lomax
    if params.time is not None:
        query_params["time"] = params.time

    response = http_get(
        f"{BASE_URL}/states/all",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


def _opensky_states_to_shape_payload(data: Any) -> dict:
    """Adapt the OpenSky ``/states/all`` indexed-array response to the
    geofeatures payload contract.

    Each state vector is a positional array (per OpenSky docs):
    ``[icao24, callsign, origin_country, time_position, last_contact,
       longitude, latitude, baro_altitude, on_ground, velocity,
       true_track, vertical_rate, sensors, geo_altitude, squawk, spi,
       position_source, ...]``

    Only state vectors with usable longitude+latitude (indices 5 and 6)
    are kept. Named fields land in ``attrs`` for the bundle to render
    in marker popups.
    """
    features: list[dict] = []
    if not isinstance(data, dict):
        return {"features": features}
    states = data.get("states")
    if not isinstance(states, list):
        return {"features": features}
    for state in states:
        if not isinstance(state, list) or len(state) < 7:
            continue
        lon_raw = state[5]
        lat_raw = state[6]
        try:
            lon = float(lon_raw) if lon_raw is not None else None
            lat = float(lat_raw) if lat_raw is not None else None
        except (TypeError, ValueError):
            continue
        if lon is None or lat is None:
            continue
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
            continue
        # Promote a small set of well-known fields by index; the bundle
        # only needs enough for marker tooltips, not the full vector.
        attrs: dict[str, Any] = {}
        if len(state) > 0:
            attrs["icao24"] = state[0]
        if len(state) > 1:
            callsign = state[1]
            attrs["callsign"] = (
                callsign.strip() if isinstance(callsign, str) else callsign
            )
        if len(state) > 2:
            attrs["origin_country"] = state[2]
        if len(state) > 7:
            attrs["baro_altitude"] = state[7]
        if len(state) > 8:
            attrs["on_ground"] = state[8]
        if len(state) > 9:
            attrs["velocity"] = state[9]
        if len(state) > 10:
            attrs["true_track"] = state[10]
        features.append({"lat": lat, "lon": lon, "attrs": attrs})
    return {"features": features}


async def handle_get_states_all(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opensky-get-states-all tool call.

    The response is in the ``ui://meta-data-mcp/shape/geofeatures/v1``
    payload format so the MCP Apps host can render live aircraft
    positions inline via the bound shape primitive.
    """
    try:
        params = OpenSkyStatesAllParams(**(arguments or {}))
        data = fetch_states_all(params)
        payload = _opensky_states_to_shape_payload(data)
        return [types.TextContent(type="text", text=to_geofeatures_text(payload))]
    except Exception as e:
        log.error(f"Error fetching OpenSky all states: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opensky-get-states-all",
        description="Fetch live ADS-B state vectors for all aircraft (optionally within a bounding box).",
        input_schema=OpenSkyStatesAllParams.model_json_schema(),
        # MCP Apps binding: render via the shared geofeatures shape primitive.
        # Use the alias keyword `_meta=` — see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": GEOFEATURES_URI}},
    ),
)
TOOLS_HANDLERS["opensky-get-states-all"] = handle_get_states_all


###################
# States By Aircraft
###################


class OpenSkyStatesByAircraftParams(BaseModel):
    """Parameters for fetching state vectors for a specific aircraft."""

    icao24: str = Field(..., description="Lowercase ICAO24 transponder address")
    time: int | None = Field(
        None,
        description="Unix epoch seconds; omit for the latest snapshot",
    )


def fetch_states_by_aircraft(params: OpenSkyStatesByAircraftParams) -> dict:
    """Fetch state vectors for a specific aircraft."""
    query_params: dict[str, Any] = {"icao24": params.icao24}
    if params.time is not None:
        query_params["time"] = params.time

    response = http_get(
        f"{BASE_URL}/states/all",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_states_by_aircraft(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opensky-get-states-by-aircraft tool call."""
    try:
        if not arguments or "icao24" not in arguments:
            raise ValueError("icao24 is required")
        params = OpenSkyStatesByAircraftParams(**arguments)
        data = fetch_states_by_aircraft(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OpenSky states by aircraft: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opensky-get-states-by-aircraft",
        description="Fetch state vectors for a specific ICAO24 aircraft.",
        input_schema=OpenSkyStatesByAircraftParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["opensky-get-states-by-aircraft"] = handle_get_states_by_aircraft


###################
# Flights By Aircraft
###################


class OpenSkyFlightsAircraftParams(BaseModel):
    """Parameters for fetching historical flights for a specific aircraft."""

    icao24: str = Field(..., description="Lowercase ICAO24 transponder address")
    begin: int = Field(..., description="Unix epoch seconds (inclusive)")
    end: int = Field(..., description="Unix epoch seconds (exclusive)")


def fetch_flights_aircraft(params: OpenSkyFlightsAircraftParams) -> list:
    """Fetch historical flights for a specific aircraft."""
    query_params = {
        "icao24": params.icao24,
        "begin": params.begin,
        "end": params.end,
    }
    response = http_get(
        f"{BASE_URL}/flights/aircraft",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_flights_aircraft(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opensky-get-flights-aircraft tool call."""
    try:
        if (
            not arguments
            or "icao24" not in arguments
            or "begin" not in arguments
            or "end" not in arguments
        ):
            raise ValueError("icao24, begin, and end are required")
        params = OpenSkyFlightsAircraftParams(**arguments)
        data = fetch_flights_aircraft(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OpenSky flights for aircraft: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opensky-get-flights-aircraft",
        description="Fetch historical flights for a specific ICAO24 aircraft within a time interval.",
        input_schema=OpenSkyFlightsAircraftParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["opensky-get-flights-aircraft"] = handle_get_flights_aircraft


###################
# Flights Arrival
###################


class OpenSkyFlightsArrivalParams(BaseModel):
    """Parameters for fetching arrivals at an airport."""

    airport: str = Field(..., description="ICAO airport code (e.g. 'EDDF')")
    begin: int = Field(..., description="Unix epoch seconds (inclusive)")
    end: int = Field(..., description="Unix epoch seconds (exclusive)")


def fetch_flights_arrival(params: OpenSkyFlightsArrivalParams) -> list:
    """Fetch arrivals at a specific airport in a time interval."""
    query_params = {
        "airport": params.airport,
        "begin": params.begin,
        "end": params.end,
    }
    response = http_get(
        f"{BASE_URL}/flights/arrival",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_flights_arrival(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opensky-get-flights-arrival tool call."""
    try:
        if (
            not arguments
            or "airport" not in arguments
            or "begin" not in arguments
            or "end" not in arguments
        ):
            raise ValueError("airport, begin, and end are required")
        params = OpenSkyFlightsArrivalParams(**arguments)
        data = fetch_flights_arrival(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OpenSky arrivals: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opensky-get-flights-arrival",
        description="Fetch arrivals at an ICAO airport within a time interval.",
        input_schema=OpenSkyFlightsArrivalParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["opensky-get-flights-arrival"] = handle_get_flights_arrival


###################
# Flights Departure
###################


class OpenSkyFlightsDepartureParams(BaseModel):
    """Parameters for fetching departures from an airport."""

    airport: str = Field(..., description="ICAO airport code (e.g. 'EDDF')")
    begin: int = Field(..., description="Unix epoch seconds (inclusive)")
    end: int = Field(..., description="Unix epoch seconds (exclusive)")


def fetch_flights_departure(params: OpenSkyFlightsDepartureParams) -> list:
    """Fetch departures from a specific airport in a time interval."""
    query_params = {
        "airport": params.airport,
        "begin": params.begin,
        "end": params.end,
    }
    response = http_get(
        f"{BASE_URL}/flights/departure",
        params=query_params,
        timeout=30.0,
        provider=PROVIDER_ID,
    )
    return response.json()


async def handle_get_flights_departure(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opensky-get-flights-departure tool call."""
    try:
        if (
            not arguments
            or "airport" not in arguments
            or "begin" not in arguments
            or "end" not in arguments
        ):
            raise ValueError("airport, begin, and end are required")
        params = OpenSkyFlightsDepartureParams(**arguments)
        data = fetch_flights_departure(params)
        return [types.TextContent(type="text", text=serialize_for_llm(data))]
    except Exception as e:
        log.error(f"Error fetching OpenSky departures: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opensky-get-flights-departure",
        description="Fetch departures from an ICAO airport within a time interval.",
        input_schema=OpenSkyFlightsDepartureParams.model_json_schema(),
    ),
)
TOOLS_HANDLERS["opensky-get-flights-departure"] = handle_get_flights_departure


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    server = create_mcp_server(
        "global-opensky",
        RESOURCES,
        RESOURCES_HANDLERS,
        TOOLS,
        TOOLS_HANDLERS,
    )

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
