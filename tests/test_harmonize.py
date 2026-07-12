"""Tests for the cross-provider harmonization primitives.

Covers the three primitive families:

1. ``normalize_geo`` — ISO alpha-2/alpha-3/M49 codes, official names,
   aliases, Eurostat quirks (EL/UK), Kosovo, statistical aggregates
   (flagged, not dropped), and unknowns returning None.
2. ``normalize_period`` — annual/semester/quarter/month/date formats
   in the variants the bundled stats providers emit, with canonical
   forms and comparable start dates; garbage returns None.
3. Frequency tooling — dominant-frequency detection, coarsest-common
   selection, and period truncation (which refuses to invent data by
   "truncating" to a finer frequency).
"""

from __future__ import annotations

import pytest

from meta_data_mcp.harmonize import (
    FREQUENCIES,
    coarsest_frequency,
    detect_frequency,
    normalize_geo,
    normalize_period,
    truncate_period,
)

# ---------------------------------------------------------------------------
# normalize_geo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "iso3"),
    [
        ("DE", "DEU"),  # alpha-2
        ("deu", "DEU"),  # alpha-3, case-insensitive
        ("276", "DEU"),  # M49 numeric
        ("004", "AFG"),  # M49 zero-padded
        ("4", "AFG"),  # M49 bare
        ("Germany", "DEU"),  # official English name
        ("  france ", "FRA"),  # whitespace + case
    ],
)
def test_normalize_geo_iso_forms(value: str, iso3: str) -> None:
    match = normalize_geo(value)
    assert match is not None
    assert match.iso3 == iso3
    assert match.is_aggregate is False


@pytest.mark.parametrize(
    ("value", "iso3"),
    [
        ("EL", "GRC"),  # Eurostat Greece
        ("UK", "GBR"),  # Eurostat United Kingdom
        ("XK", "XKX"),  # Kosovo (Eurostat)
        ("XKX", "XKX"),  # Kosovo (World Bank)
    ],
)
def test_normalize_geo_provider_quirks(value: str, iso3: str) -> None:
    match = normalize_geo(value)
    assert match is not None
    assert match.iso3 == iso3


@pytest.mark.parametrize(
    ("value", "iso3"),
    [
        ("USA", "USA"),
        ("united states", "USA"),
        ("South Korea", "KOR"),
        ("russia", "RUS"),
        ("Turkey", "TUR"),
        ("vietnam", "VNM"),
        ("Czech Republic", "CZE"),
        ("ivory coast", "CIV"),
    ],
)
def test_normalize_geo_aliases(value: str, iso3: str) -> None:
    match = normalize_geo(value)
    assert match is not None
    assert match.iso3 == iso3


@pytest.mark.parametrize("value", ["WLD", "EU27_2020", "hic", "EA20", "SSF"])
def test_normalize_geo_aggregates_flagged(value: str) -> None:
    match = normalize_geo(value)
    assert match is not None
    assert match.is_aggregate is True


def test_normalize_geo_unknown_returns_none() -> None:
    assert normalize_geo("not-a-place") is None
    assert normalize_geo("") is None


def test_normalize_geo_greece_native_codes_still_work() -> None:
    # The EL quirk must not shadow Greece's real ISO codes.
    assert normalize_geo("GR").iso3 == "GRC"
    assert normalize_geo("GRC").iso3 == "GRC"


# ---------------------------------------------------------------------------
# normalize_period
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "freq", "canonical", "start"),
    [
        ("2015", "A", "2015", "2015-01-01"),
        (2015, "A", "2015", "2015-01-01"),  # World Bank int years
        ("2015-Q1", "Q", "2015-Q1", "2015-01-01"),
        ("2015Q3", "Q", "2015-Q3", "2015-07-01"),
        ("2015-q4", "Q", "2015-Q4", "2015-10-01"),
        ("2015-03", "M", "2015-03", "2015-03-01"),
        ("2015M3", "M", "2015-03", "2015-03-01"),
        ("2015M11", "M", "2015-11", "2015-11-01"),
        ("2015-S1", "S", "2015-S1", "2015-01-01"),
        ("2015-H2", "S", "2015-S2", "2015-07-01"),  # SDMX half-years
        ("2015-06-15", "D", "2015-06-15", "2015-06-15"),
    ],
)
def test_normalize_period_formats(
    value: str | int, freq: str, canonical: str, start: str
) -> None:
    period = normalize_period(value)
    assert period is not None
    assert period.freq == freq
    assert period.canonical == canonical
    assert period.start_date == start


@pytest.mark.parametrize(
    "value", ["", "abc", "2015-13", "2015M13", "2015-Q5", "20155", "2015-02-31"]
)
def test_normalize_period_rejects_garbage(value: str) -> None:
    assert normalize_period(value) is None


def test_start_dates_sort_across_frequencies() -> None:
    # start_date gives one comparable axis for mixed-frequency series.
    periods = [normalize_period(v) for v in ["2015-Q3", "2015", "2015-06", "2016"]]
    ordered = sorted(p.start_date for p in periods)
    assert ordered == ["2015-01-01", "2015-06-01", "2015-07-01", "2016-01-01"]


# ---------------------------------------------------------------------------
# frequency tooling
# ---------------------------------------------------------------------------


def test_frequencies_ordering_constant() -> None:
    assert FREQUENCIES == ("A", "S", "Q", "M", "D")


def test_detect_frequency_majority_vote() -> None:
    assert detect_frequency(["2015-01", "2015-02", "2015"]) == "M"
    assert detect_frequency(["2015", "2016", "2017-Q1"]) == "A"


def test_detect_frequency_none_when_unparseable() -> None:
    assert detect_frequency(["n/a", ""]) is None
    assert detect_frequency([]) is None


def test_coarsest_frequency() -> None:
    assert coarsest_frequency(["M", "Q", "A"]) == "A"
    assert coarsest_frequency(["D", "M"]) == "M"
    assert coarsest_frequency(["bogus"]) is None


@pytest.mark.parametrize(
    ("value", "freq", "expected"),
    [
        ("2015-03", "A", "2015"),
        ("2015-07-15", "Q", "2015-Q3"),
        ("2015-11-02", "M", "2015-11"),
        ("2015-Q2", "S", "2015-S1"),
        ("2015-Q4", "A", "2015"),
        ("2015-08-01", "S", "2015-S2"),
        ("2015-01-15", "D", "2015-01-15"),
    ],
)
def test_truncate_period_to_coarser(value: str, freq: str, expected: str) -> None:
    assert truncate_period(value, freq) == expected


def test_truncate_period_refuses_to_invent_finer_data() -> None:
    # An annual figure has no quarterly truth — truncating "down" must
    # fail rather than fabricate.
    assert truncate_period("2015", "Q") is None
    assert truncate_period("2015-Q1", "M") is None


def test_truncate_period_unknown_inputs() -> None:
    assert truncate_period("garbage", "A") is None
    assert truncate_period("2015-03", "X") is None
