"""Tests for the OVapi provider (Dutch live public transport).

Mocks at the ``httpx.get`` boundary per repo convention. Covers the
Cherokee index parsing (bulk GTFS feed listing), server-side line
filtering, line-details compaction (stop patterns + live vehicles),
real-time departure compaction and sorting, and the input-sanitization
guards (SSRF path guard, line-id charset, numeric timing-point codes).
"""

from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.nl_ovapi import (
    OvapiLinesParams,
    OvapiStopDeparturesParams,
    get_line_details,
    get_stop_departures,
    list_gtfs_feeds,
    search_lines,
)


def _response(json_data=None, text=""):
    mock = Mock()
    mock.json.return_value = json_data
    mock.text = text
    mock.raise_for_status = Mock()
    return mock


# ---------------------------------------------------------------------------
# ovapi-gtfs-feeds
# ---------------------------------------------------------------------------

# Real Cherokee markup: sort links are bare <a href="?...">, rows carry
# class="link" before href.
_CHEROKEE_INDEX = """
<html><body><h1>Index of /</h1><table>
<tr><td><a href="?order=N">Name</a></td></tr>
<tr><td><a class="link" href="../">Parent</a></td></tr>
<tr><td><a href="/cherokee_themes/default/theme.css">theme</a></td></tr>
<tr><td><a class="link" href="gtfs-nl.zip">gtfs-nl.zip</a></td></tr>
<tr><td><a class="link" href="tripUpdates.pb">tripUpdates.pb</a></td></tr>
<tr><td><a class="link" href="new/">new</a></td></tr>
</table></body></html>
"""


def test_list_gtfs_feeds_parses_index():
    with patch("httpx.get", return_value=_response(text=_CHEROKEE_INDEX)):
        entries = list_gtfs_feeds("/")
    by_name = {e["name"]: e for e in entries}
    assert by_name["gtfs-nl.zip"]["kind"] == "gtfs zip"
    assert by_name["gtfs-nl.zip"]["url"] == "https://gtfs.ovapi.nl/gtfs-nl.zip"
    assert by_name["tripUpdates.pb"]["kind"] == "gtfs-rt protobuf"
    assert by_name["new"]["kind"] == "directory"
    # Sort links, parent links, and theme assets are excluded.
    assert "Parent" not in by_name
    assert "Name" not in by_name
    assert "theme" not in by_name


def test_list_gtfs_feeds_rejects_offsite_paths():
    # An absolute URL smuggled in as "path" must be rejected before any
    # HTTP happens (the mock raises if the guard were bypassed).
    with patch("httpx.get", side_effect=AssertionError("guard bypassed")):
        with pytest.raises(ValueError, match="outside the allowed host"):
            list_gtfs_feeds("https://evil.example.com/")


# ---------------------------------------------------------------------------
# ovapi-lines
# ---------------------------------------------------------------------------

# Realistic national-list ids: nearly everything is keyed NL_* with
# DataOwnerCode "NL" regardless of the actual operator.
_LINES = {
    "NL_13_1": {
        "LinePublicNumber": "13",
        "LineName": "Lijn 13",
        "TransportType": "TRAM",
        "DestinationName50": "Zoutkeetsgracht",
        "DataOwnerCode": "NL",
        "LineDirection": 1,
    },
    "HTM_1_2": {
        "LinePublicNumber": "1",
        "LineName": "Tramlijn 1",
        "TransportType": "METRO",
        "DestinationName50": "Binnenhof",
        "DataOwnerCode": "HTM",
        "LineDirection": 2,
    },
    "NL_5302_1": {
        "LinePublicNumber": "302",
        "LineName": "U-liner Amersfoort - Vianen",
        "TransportType": "BUS",
        "DestinationName50": "Amersfoort CS",
        "DataOwnerCode": "NL",
        "LineDirection": 1,
    },
}


def test_search_lines_filters_by_mode():
    with patch("httpx.get", return_value=_response(json_data=_LINES)):
        result = search_lines(OvapiLinesParams(transport_type="tram"))
    assert result["total_matched"] == 1
    assert result["lines"][0]["id"] == "NL_13_1"
    assert result["lines"][0]["line"] == "13"


def test_search_lines_query_matches_name_and_destination():
    with patch("httpx.get", return_value=_response(json_data=_LINES)):
        result = search_lines(OvapiLinesParams(query="amersfoort"))
    assert [line["id"] for line in result["lines"]] == ["NL_5302_1"]


def test_search_lines_limit_caps_results_but_counts_all():
    with patch("httpx.get", return_value=_response(json_data=_LINES)):
        result = search_lines(OvapiLinesParams(limit=1))
    assert result["total_matched"] == 3
    assert result["returned"] == 1


# ---------------------------------------------------------------------------
# ovapi-line-details
# ---------------------------------------------------------------------------

_LINE_DETAILS = {
    "GVB_13_1": {
        "Line": _LINES["NL_13_1"],
        "ServerTime": 1770000000000,
        "Network": {
            "529909593": {
                "12": {
                    "UserStopOrderNumber": 12,
                    "TimingPointCode": "30002116",
                    "TimingPointName": "Second Stop",
                    "TimingPointTown": "Amsterdam",
                    "Latitude": 52.39,
                    "Longitude": 4.89,
                },
                "11": {
                    "UserStopOrderNumber": 11,
                    "TimingPointCode": "30002115",
                    "TimingPointName": "Nw. Willemsstraat",
                    "TimingPointTown": "Amsterdam",
                    "Latitude": 52.38,
                    "Longitude": 4.88,
                },
            },
        },
        "Actuals": {
            "NL_20260719_13_14043_0": {
                "JourneyNumber": 14043,
                "DestinationName50": "Zoutkeetsgracht",
                "Latitude": 52.377,
                "Longitude": 4.803,
                "OperatorCode": "GVB",
            },
        },
    },
}


def test_get_line_details_orders_stops_and_lists_vehicles():
    with patch("httpx.get", return_value=_response(json_data=_LINE_DETAILS)):
        result = get_line_details("GVB_13_1")
    stops = result["stop_patterns"]["529909593"]
    assert [s["order"] for s in stops] == [11, 12]
    assert stops[0]["timing_point_code"] == "30002115"
    assert result["live_vehicles"][0]["journey_number"] == 14043
    assert result["line"]["transport_type"] == "TRAM"


@pytest.mark.parametrize("bad", ["../etc", "GVB/13", "a b", ""])
def test_get_line_details_rejects_invalid_ids(bad: str):
    with pytest.raises(ValueError, match="Invalid line_id"):
        get_line_details(bad)


# ---------------------------------------------------------------------------
# ovapi-stop-departures
# ---------------------------------------------------------------------------

_DEPARTURES = {
    "30005093": {
        "Stop": {
            "TimingPointName": "Barentszplein",
            "TimingPointTown": "Amsterdam",
            "Latitude": 52.389015,
            "Longitude": 4.891562,
        },
        "Passes": {
            "later": {
                "LinePublicNumber": "48",
                "DestinationName50": "Centraal Station",
                "TransportType": "BUS",
                "OperatorCode": "GVB",
                "TargetDepartureTime": "2026-07-19T12:10:00",
                "ExpectedDepartureTime": "2026-07-19T12:14:00",
                "TripStopStatus": "DRIVING",
            },
            "sooner": {
                "LinePublicNumber": "48",
                "DestinationName50": "Centraal Station",
                "TransportType": "BUS",
                "OperatorCode": "GVB",
                "TargetDepartureTime": "2026-07-19T12:00:00",
                "ExpectedDepartureTime": "2026-07-19T12:01:00",
                "TripStopStatus": "DRIVING",
            },
        },
    },
}


def test_get_stop_departures_sorts_by_expected_time():
    params = OvapiStopDeparturesParams(timing_point_codes="30005093")
    with patch("httpx.get", return_value=_response(json_data=_DEPARTURES)):
        result = get_stop_departures(params)
    stop = result["30005093"]
    assert stop["stop"]["name"] == "Barentszplein"
    assert stop["total_upcoming"] == 2
    times = [d["expected_departure"] for d in stop["departures"]]
    assert times == sorted(times)


def test_get_stop_departures_applies_limit():
    params = OvapiStopDeparturesParams(timing_point_codes="30005093", limit=1)
    with patch("httpx.get", return_value=_response(json_data=_DEPARTURES)):
        result = get_stop_departures(params)
    assert len(result["30005093"]["departures"]) == 1
    assert result["30005093"]["total_upcoming"] == 2


@pytest.mark.parametrize("bad", ["abc", "123;456", "30005093,", "../30005093"])
def test_get_stop_departures_rejects_invalid_codes(bad: str):
    params = OvapiStopDeparturesParams(timing_point_codes=bad)
    with pytest.raises(ValueError, match="timing_point_codes"):
        get_stop_departures(params)
