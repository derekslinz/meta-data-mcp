"""Tests for the Transitous provider (worldwide transit routing).

Mocks at the ``httpx.get`` boundary per repo convention. Covers geocode
compaction (city extraction from admin areas, limit), journey-plan
compaction (durations to minutes, leg trimming — no geometry/steps in
output, None-field dropping, direct non-transit alternatives), the
arrive_by/time query passthrough, and departure compaction.
"""

from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.global_transitous import (
    TransitousDeparturesParams,
    TransitousGeocodeParams,
    TransitousPlanParams,
    geocode,
    get_departures,
    plan_journey,
)


def _response(json_data):
    mock = Mock()
    mock.json.return_value = json_data
    mock.raise_for_status = Mock()
    return mock


# ---------------------------------------------------------------------------
# transitous-geocode
# ---------------------------------------------------------------------------

_GEOCODE = [
    {
        "type": "STOP",
        "name": "Amsterdam Centraal",
        "id": "de-DELFI_000008400058",
        "lat": 52.37919,
        "lon": 4.899431,
        "country": "NL",
        "areas": [
            {"name": "Nederland", "adminLevel": 2},
            {"name": "Noord-Holland", "adminLevel": 4},
            {"name": "Amsterdam", "adminLevel": 8},
        ],
    },
    {
        "type": "STOP",
        "name": "Amsterdam Zuid",
        "id": "nl-OpenOV_xyz",
        "lat": 52.34,
        "lon": 4.87,
        "country": "NL",
        "areas": [],
    },
]


def test_geocode_compacts_and_extracts_city():
    with patch("httpx.get", return_value=_response(_GEOCODE)):
        matches = geocode(TransitousGeocodeParams(text="Amsterdam Centraal"))
    assert matches[0] == {
        "name": "Amsterdam Centraal",
        "id": "de-DELFI_000008400058",
        "type": "STOP",
        "lat": 52.37919,
        "lon": 4.899431,
        "city": "Amsterdam",
        "country": "NL",
    }
    assert matches[1]["city"] is None


def test_geocode_applies_limit():
    with patch("httpx.get", return_value=_response(_GEOCODE)):
        matches = geocode(TransitousGeocodeParams(text="Amsterdam", limit=1))
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# transitous-plan
# ---------------------------------------------------------------------------

_PLAN = {
    "itineraries": [
        {
            "duration": 2700,
            "startTime": "2026-07-19T04:20:00Z",
            "endTime": "2026-07-19T05:05:00Z",
            "transfers": 1,
            "legs": [
                {
                    "mode": "WALK",
                    "duration": 420,
                    "startTime": "2026-07-19T04:20:00Z",
                    "endTime": "2026-07-19T04:27:00Z",
                    "from": {"name": "START"},
                    "to": {"name": "Amsterdam Centraal"},
                    "legGeometry": {"points": "should-not-survive"},
                    "steps": [{"huge": "walking instructions"}],
                },
                {
                    "mode": "RAIL",
                    "routeShortName": "IC 823",
                    "headsign": "Maastricht",
                    "duration": 1560,
                    "startTime": "2026-07-19T04:27:00Z",
                    "endTime": "2026-07-19T04:53:00Z",
                    "realTime": True,
                    "from": {"name": "Amsterdam Centraal"},
                    "to": {"name": "Utrecht Centraal"},
                },
            ],
        },
    ],
    "direct": [
        {
            "duration": 12000,
            "startTime": "2026-07-19T04:20:00Z",
            "endTime": "2026-07-19T07:40:00Z",
            "transfers": 0,
            "legs": [{"mode": "BIKE", "duration": 12000}],
        },
    ],
}


def test_plan_compacts_itineraries_and_drops_geometry():
    params = TransitousPlanParams(
        from_place="52.3791,4.9003",
        to_place="52.0894,5.1077",
    )
    with patch("httpx.get", return_value=_response(_PLAN)):
        result = plan_journey(params)

    itinerary = result["itineraries"][0]
    assert itinerary["duration_minutes"] == 45.0
    assert itinerary["transfers"] == 1

    rail = itinerary["legs"][1]
    assert rail["line"] == "IC 823"
    assert rail["duration_minutes"] == 26.0
    assert rail["real_time"] is True
    assert rail["from"] == "Amsterdam Centraal"

    walk = itinerary["legs"][0]
    assert "legGeometry" not in walk and "steps" not in walk
    # None-valued fields (no line/headsign on a walk leg) are dropped.
    assert "line" not in walk and "headsign" not in walk

    assert result["direct"][0]["duration_minutes"] == 200.0


def test_plan_passes_time_and_arrive_by():
    params = TransitousPlanParams(
        from_place="a",
        to_place="b",
        time="2026-07-19T08:30:00Z",
        arrive_by=True,
        max_itineraries=2,
    )
    with patch("httpx.get", return_value=_response(_PLAN)) as mock_get:
        plan_journey(params)
    query = mock_get.call_args.kwargs.get("params") or mock_get.call_args.args[1]
    assert query["time"] == "2026-07-19T08:30:00Z"
    assert query["arriveBy"] == "true"
    assert query["numItineraries"] == 2


def test_plan_omits_arrive_by_and_time_by_default():
    params = TransitousPlanParams(from_place="a", to_place="b")
    with patch("httpx.get", return_value=_response(_PLAN)) as mock_get:
        plan_journey(params)
    query = mock_get.call_args.kwargs.get("params") or mock_get.call_args.args[1]
    assert "time" not in query
    assert "arriveBy" not in query


# ---------------------------------------------------------------------------
# transitous-departures
# ---------------------------------------------------------------------------

_STOPTIMES = {
    "stopTimes": [
        {
            "mode": "RAIL",
            "routeShortName": "IC 823",
            "headsign": "Maastricht",
            "realTime": True,
            "place": {
                "name": "Amsterdam, Centraal Station",
                "departure": "2026-07-19T04:19:00Z",
                "scheduledDeparture": "2026-07-19T04:18:00Z",
                "track": "D",
            },
        },
    ],
}


def test_departures_compact_with_track_and_realtime():
    params = TransitousDeparturesParams(stop_id="nl-OpenOV_3980940")
    with patch("httpx.get", return_value=_response(_STOPTIMES)):
        departures = get_departures(params)
    assert departures == [
        {
            "line": "IC 823",
            "mode": "RAIL",
            "headsign": "Maastricht",
            "stop": "Amsterdam, Centraal Station",
            "departure": "2026-07-19T04:19:00Z",
            "scheduled_departure": "2026-07-19T04:18:00Z",
            "track": "D",
            "real_time": True,
        },
    ]


def test_departures_handles_empty_response():
    params = TransitousDeparturesParams(stop_id="nowhere")
    with patch("httpx.get", return_value=_response({})):
        assert get_departures(params) == []


def test_departures_enforces_limit_client_side():
    # The API's ``n`` is advisory — same-minute departures come back
    # grouped and can exceed it.
    many = {"stopTimes": _STOPTIMES["stopTimes"] * 5}
    params = TransitousDeparturesParams(stop_id="x", limit=2)
    with patch("httpx.get", return_value=_response(many)):
        assert len(get_departures(params)) == 2


@pytest.mark.parametrize("missing", [{}, {"text": None}])
def test_geocode_requires_text(missing):
    with pytest.raises(Exception):
        TransitousGeocodeParams(**missing)
