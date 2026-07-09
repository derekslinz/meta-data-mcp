"""Harmonization primitives for cross-provider federation.

Different statistics providers describe the same country and the same
time period in different vocabularies: Eurostat says ``EL`` and
``2015-Q1``, the World Bank says ``GRC`` and ``2015``, DBnomics
passes through whatever its upstream uses. Federating their answers
into one comparable series needs a common key — that's what this
module provides, as pure functions over static tables. No I/O, no LLM.

Three primitives:

- :func:`normalize_geo` — country code or name (ISO alpha-2/alpha-3,
  UN M49 numeric, English name, common aliases, provider quirks like
  Eurostat's ``EL``/``UK``) → a :class:`GeoMatch` keyed on ISO alpha-3.
  Statistical aggregates (``WLD``, ``EU27_2020``, income levels) are
  recognized and flagged rather than dropped.
- :func:`normalize_period` — a period string in any of the common
  statistical formats (``2015``, ``2015-Q1``, ``2015M03``, ``2015-03``,
  ``2015-S1``, ISO dates) → a :class:`Period` with a canonical form and
  a comparable start date.
- :func:`detect_frequency` / :func:`coarsest_frequency` /
  :func:`truncate_period` — frequency tooling for aligning series
  sampled at different granularities. Downsampling decisions belong to
  the caller (and must be reported, never silent); these helpers only
  make them expressible.

The ISO table lives in :mod:`meta_data_mcp.harmonize_data` (generated
by ``tools/generate_concordance.py``); everything provider-specific —
quirks, aliases, aggregates — lives here, where it can be reviewed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from meta_data_mcp.harmonize_data import COUNTRIES

# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeoMatch:
    """A resolved geography: ISO alpha-3 key plus display name.

    ``is_aggregate`` marks statistical groupings (World, EU27, income
    levels) that must not be treated as countries when joining series —
    they're kept so federation can pass them through labeled instead of
    silently discarding them.
    """

    iso3: str
    name: str
    is_aggregate: bool = False


# Provider-specific code quirks. Eurostat uses EL (not GR) and UK (not
# GB); neither is an assigned ISO alpha-2, so mapping them here cannot
# shadow a real country. Kosovo has no ISO code — XK/XKX are the de
# facto codes Eurostat and the World Bank use respectively.
_QUIRKS: dict[str, tuple[str, str]] = {
    "el": ("GRC", "Greece"),
    "uk": ("GBR", "United Kingdom of Great Britain and Northern Ireland"),
    "xk": ("XKX", "Kosovo"),
    "xkx": ("XKX", "Kosovo"),
    "kosovo": ("XKX", "Kosovo"),
}

# Statistical aggregates common across the bundled stats providers
# (World Bank aggregate codes, Eurostat EU/EA composites, OECD). The
# key doubles as the stable identifier in GeoMatch.iso3.
_AGGREGATES: dict[str, str] = {
    "wld": "World",
    "euu": "European Union",
    "emu": "Euro area",
    "eu27_2020": "European Union (27)",
    "eu28": "European Union (28)",
    "ea19": "Euro area (19)",
    "ea20": "Euro area (20)",
    "oecd": "OECD members",
    "hic": "High income",
    "mic": "Middle income",
    "lic": "Low income",
    "lmc": "Lower middle income",
    "umc": "Upper middle income",
    "lmy": "Low & middle income",
    "arb": "Arab World",
    "ssf": "Sub-Saharan Africa",
    "eap": "East Asia & Pacific",
    "ecs": "Europe & Central Asia",
    "lcn": "Latin America & Caribbean",
    "mea": "Middle East & North Africa",
    "nac": "North America",
    "sas": "South Asia",
}

# Common English names that differ from the official ISO short name.
# Values are ISO alpha-3.
_NAME_ALIASES: dict[str, str] = {
    "america": "USA",
    "bolivia": "BOL",
    "britain": "GBR",
    "brunei": "BRN",
    "cape verde": "CPV",
    "czech republic": "CZE",
    "democratic republic of the congo": "COD",
    "dr congo": "COD",
    "great britain": "GBR",
    "iran": "IRN",
    "ivory coast": "CIV",
    "laos": "LAO",
    "moldova": "MDA",
    "north korea": "PRK",
    "palestine": "PSE",
    "republic of korea": "KOR",
    "russia": "RUS",
    "south korea": "KOR",
    "syria": "SYR",
    "taiwan": "TWN",
    "tanzania": "TZA",
    "turkey": "TUR",
    "united kingdom": "GBR",
    "united states": "USA",
    "usa": "USA",
    "venezuela": "VEN",
    "vietnam": "VNM",
}


def _build_geo_index() -> dict[str, GeoMatch]:
    index: dict[str, GeoMatch] = {}
    by_iso3: dict[str, GeoMatch] = {}
    for name, alpha2, alpha3, m49 in COUNTRIES:
        match = GeoMatch(iso3=alpha3, name=name)
        by_iso3[alpha3] = match
        index[alpha2.lower()] = match
        index[alpha3.lower()] = match
        index[m49] = match  # zero-padded, e.g. "004"
        index[m49.lstrip("0") or "0"] = match  # bare numeric, e.g. "4"
        index[name.lower()] = match
    for alias, iso3 in _NAME_ALIASES.items():
        if iso3 in by_iso3:
            index[alias] = by_iso3[iso3]
    for code, (iso3, name) in _QUIRKS.items():
        index[code] = GeoMatch(iso3=iso3, name=name)
    for code, name in _AGGREGATES.items():
        index[code] = GeoMatch(iso3=code.upper(), name=name, is_aggregate=True)
    return index


_GEO_INDEX = _build_geo_index()


def normalize_geo(value: str) -> GeoMatch | None:
    """Resolve a country code or name to its ISO alpha-3 identity.

    Accepts ISO alpha-2 (``DE``), alpha-3 (``DEU``), UN M49 numeric
    (``276`` or ``"076"``), the official English name, common aliases
    (``south korea``), provider quirks (Eurostat ``EL``/``UK``,
    Kosovo), and known statistical aggregates (returned with
    ``is_aggregate=True``). Returns ``None`` for anything unrecognized
    — the caller decides whether to pass unknowns through or report
    them, never this function.
    """
    return _GEO_INDEX.get(value.strip().lower())


# ---------------------------------------------------------------------------
# Time periods
# ---------------------------------------------------------------------------

# Coarsest → finest. Alignment picks the coarsest frequency present so
# every series can be truncated onto it without inventing data.
FREQUENCIES = ("A", "S", "Q", "M", "D")

_FREQ_ORDER = {f: i for i, f in enumerate(FREQUENCIES)}


@dataclass(frozen=True)
class Period:
    """A normalized time period.

    ``canonical`` is the display/join key (``2015``, ``2015-Q1``,
    ``2015-01``, ``2015-S1``, ``2015-01-15``); ``start_date`` is the
    period's first day as an ISO date, giving every frequency a common
    sortable axis.
    """

    freq: str  # one of FREQUENCIES
    canonical: str
    start_date: str


_RE_ANNUAL = re.compile(r"^(\d{4})$")
_RE_QUARTER = re.compile(r"^(\d{4})[-\s]?Q([1-4])$", re.IGNORECASE)
_RE_MONTH_M = re.compile(r"^(\d{4})[-\s]?M(\d{1,2})$", re.IGNORECASE)
_RE_MONTH_ISO = re.compile(r"^(\d{4})-(\d{2})$")
_RE_SEMESTER = re.compile(r"^(\d{4})[-\s]?[SH]([12])$", re.IGNORECASE)
_RE_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def normalize_period(value: str | int) -> Period | None:
    """Parse a statistical period string into a :class:`Period`.

    Handles the formats the bundled stats providers actually emit:
    bare years (``2015``, also as int), quarters (``2015-Q1``,
    ``2015Q1``), months (``2015-03``, ``2015M3``, ``2015M03``),
    semesters (``2015-S1``, SDMX ``2015-H1``), and ISO dates. Returns
    ``None`` for anything else (callers report unparseable dates, they
    don't guess).
    """
    s = str(value).strip()

    if m := _RE_ANNUAL.match(s):
        year = m.group(1)
        return Period("A", year, f"{year}-01-01")
    if m := _RE_QUARTER.match(s):
        year, q = m.group(1), int(m.group(2))
        return Period("Q", f"{year}-Q{q}", f"{year}-{3 * (q - 1) + 1:02d}-01")
    if m := _RE_SEMESTER.match(s):
        year, half = m.group(1), int(m.group(2))
        return Period("S", f"{year}-S{half}", f"{year}-{6 * (half - 1) + 1:02d}-01")
    if m := _RE_MONTH_M.match(s) or _RE_MONTH_ISO.match(s):
        year, month = m.group(1), int(m.group(2))
        if not 1 <= month <= 12:
            return None
        return Period("M", f"{year}-{month:02d}", f"{year}-{month:02d}-01")
    if m := _RE_DATE.match(s):
        year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        return Period("D", s, s)
    return None


def detect_frequency(values: list[str | int]) -> str | None:
    """The dominant frequency among parseable period strings.

    Returns the most common frequency, or ``None`` when nothing
    parses. Mixed-frequency inputs are legal (a series with annual
    history and monthly recent data) — the caller gets the majority
    vote and can truncate the rest onto it.
    """
    counts: dict[str, int] = {}
    for v in values:
        period = normalize_period(v)
        if period is not None:
            counts[period.freq] = counts.get(period.freq, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda f: (counts[f], -_FREQ_ORDER[f]))


def coarsest_frequency(freqs: list[str]) -> str | None:
    """The coarsest of the given frequencies (``A`` beats ``Q`` beats
    ``M`` …) — the only alignment target that never invents data."""
    known = [f for f in freqs if f in _FREQ_ORDER]
    if not known:
        return None
    return min(known, key=lambda f: _FREQ_ORDER[f])


def truncate_period(value: str | int, freq: str) -> str | None:
    """Re-express a period at a coarser frequency.

    ``truncate_period("2015-03", "A") == "2015"``;
    ``truncate_period("2015-07-15", "Q") == "2015-Q3"``. Truncating to
    a *finer* frequency than the input carries returns ``None`` — that
    would be inventing data.
    """
    period = normalize_period(value)
    if period is None or freq not in _FREQ_ORDER:
        return None
    if _FREQ_ORDER[freq] > _FREQ_ORDER[period.freq]:
        return None
    year, month, _day = period.start_date.split("-")
    if freq == "A":
        return year
    if freq == "S":
        return f"{year}-S{(int(month) - 1) // 6 + 1}"
    if freq == "Q":
        return f"{year}-Q{(int(month) - 1) // 3 + 1}"
    if freq == "M":
        return f"{year}-{month}"
    return period.canonical  # freq == "D" implies input was daily


__all__ = [
    "FREQUENCIES",
    "GeoMatch",
    "Period",
    "coarsest_frequency",
    "detect_frequency",
    "normalize_geo",
    "normalize_period",
    "truncate_period",
]
