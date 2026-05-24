"""
Provider Registry — structured metadata for every opendata-mcp provider.

This file is the single source of truth describing each provider's domains,
regions, data types, keywords, and any required environment variables.

It powers:
- The `meta_data_mcp` aggregator provider, which exposes `find-providers`,
  `list-domains`, `list-regions`, and `describe-provider` tools so LLMs can
  discover which provider answers which question.
- The CLI's `list` and `info` commands (enriched output).
- External integrations that want to enumerate providers programmatically.

Keep entries terse. Keywords should be the words a user might type, not the
exact tool names. Domains and regions use a small controlled vocabulary so
faceted search works.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Iterator


# Controlled vocabularies — keep these short.
DOMAINS: tuple[str, ...] = (
    "government",
    "statistics",
    "economics",
    "finance",
    "health",
    "earth-science",
    "environment",
    "biodiversity",
    "weather",
    "space",
    "astronomy",
    "physics",
    "transit",
    "aviation",
    "geo",
    "geocoding",
    "knowledge",
    "scholarly",
    "culture",
    "books",
    "legal",
    "crypto",
    "demographics",
    "biology",
    "chemistry",
    "networking",
    "security",
    "news",
    "agriculture",
    "trade",
)

REGIONS: tuple[str, ...] = (
    "global",
    "us",
    "eu",
    "uk",
    "de",
    "fr",
    "nl",
    "ch",
    "ca",
    "au",
    "sg",
)


@dataclass(frozen=True)
class ProviderEntry:
    """One row in the provider registry."""

    id: str
    server_name: str
    title: str
    description: str
    domains: tuple[str, ...]
    regions: tuple[str, ...]
    keywords: tuple[str, ...]
    homepage: str
    license_note: str = ""
    requires_env: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Source-of-truth static entries. Order: alphabetical by id to match
# `pkgutil.iter_modules`. Wrapped by the ``Registry`` instance below.
_STATIC_ENTRIES: tuple[ProviderEntry, ...] = (
    ProviderEntry(
        id="au_data_gov",
        server_name="au-data-gov",
        title="Australian Government Open Data (CKAN)",
        description="Australia's federal open data catalog. Search datasets, organizations, groups, and tags.",
        domains=("government",),
        regions=("au",),
        keywords=(
            "australia",
            "ckan",
            "catalog",
            "datasets",
            "government",
            "open data",
        ),
        homepage="https://data.gov.au/",
    ),
    ProviderEntry(
        id="ca_open_gov",
        server_name="ca-open-gov",
        title="Government of Canada Open Data (CKAN)",
        description="Canada's federal open data portal — datasets across departments and provinces.",
        domains=("government",),
        regions=("ca",),
        keywords=("canada", "ckan", "catalog", "datasets", "federal"),
        homepage="https://open.canada.ca/",
    ),
    ProviderEntry(
        id="cern_opendata",
        server_name="cern-opendata",
        title="CERN Open Data Portal",
        description="Open particle physics datasets, software releases, and event collections from CERN experiments (CMS, ATLAS, LHCb, ALICE).",
        domains=("physics",),
        regions=("global", "eu"),
        keywords=("cern", "physics", "lhc", "cms", "atlas", "particles", "datasets"),
        homepage="https://opendata.cern.ch/",
    ),
    ProviderEntry(
        id="ch_sbb",
        server_name="ch-sbb",
        title="Swiss Federal Railways (SBB)",
        description="Train network disruptions and service data from Switzerland's national railway.",
        domains=("transit",),
        regions=("ch",),
        keywords=("sbb", "swiss", "train", "rail", "disruption", "switzerland"),
        homepage="https://data.sbb.ch/",
    ),
    ProviderEntry(
        id="de_db",
        server_name="de-db",
        title="Deutsche Bahn (DB)",
        description="German railway open data — stations, timetables, and operational data.",
        domains=("transit",),
        regions=("de",),
        keywords=("deutsche bahn", "germany", "train", "rail", "timetable"),
        homepage="https://data.deutschebahn.com/",
    ),
    ProviderEntry(
        id="eu_copernicus",
        server_name="eu-copernicus",
        title="Copernicus (EU)",
        description="European Earth observation and climate datasets from Copernicus services.",
        domains=("earth-science", "environment"),
        regions=("eu", "global"),
        keywords=("copernicus", "earth", "satellite", "climate", "europe"),
        homepage="https://www.copernicus.eu/",
    ),
    ProviderEntry(
        id="eu_ecb",
        server_name="eu-ecb",
        title="European Central Bank Data Portal",
        description="ECB statistical data via SDMX REST — exchange rates, monetary policy, banking statistics.",
        domains=("economics", "finance"),
        regions=("eu",),
        keywords=(
            "ecb",
            "european central bank",
            "fx",
            "exchange rates",
            "monetary",
            "sdmx",
        ),
        homepage="https://data.ecb.europa.eu/",
    ),
    ProviderEntry(
        id="eu_euvd",
        server_name="eu-euvd",
        title="ENISA EU Vulnerability Database (EUVD)",
        description="Latest, exploited, critical, and filtered vulnerability search from ENISA's EU vulnerability database.",
        domains=("security",),
        regions=("eu", "global"),
        keywords=(
            "euvd",
            "enisa",
            "vulnerability",
            "cve",
            "exploited",
            "critical",
            "security",
        ),
        homepage="https://euvd.enisa.europa.eu/apidoc",
    ),
    ProviderEntry(
        id="eu_eurostat",
        server_name="eu-eurostat",
        title="Eurostat",
        description="European Union statistics — demographics, economy, society, environment.",
        domains=("statistics",),
        regions=("eu",),
        keywords=("eurostat", "europe", "statistics", "demographics", "gdp"),
        homepage="https://ec.europa.eu/eurostat",
    ),
    ProviderEntry(
        id="fr_data_gouv",
        server_name="fr-data-gouv",
        title="French Government Open Data",
        description="France's open data platform — datasets, organizations, reuses, topics.",
        domains=("government",),
        regions=("fr",),
        keywords=("france", "data gouv", "catalog", "datasets", "français"),
        homepage="https://www.data.gouv.fr/",
    ),
    ProviderEntry(
        id="global_arxiv",
        server_name="global-arxiv",
        title="arXiv Preprints",
        description="arXiv preprint metadata across physics, math, CS, biology, economics. Returns Atom XML.",
        domains=("scholarly",),
        regions=("global",),
        keywords=("arxiv", "preprint", "papers", "research", "science"),
        homepage="https://arxiv.org/",
    ),
    ProviderEntry(
        id="global_bgpview",
        server_name="global-bgpview",
        title="BGPView",
        description="BGP routing data — ASN details, announced prefixes, peers, upstreams, downstreams, and IP/prefix lookups.",
        domains=("networking",),
        regions=("global",),
        keywords=(
            "bgp",
            "asn",
            "routing",
            "prefix",
            "internet",
            "network",
            "autonomous system",
            "bgpview",
        ),
        homepage="https://bgpview.io/",
    ),
    ProviderEntry(
        id="global_circl_cve",
        server_name="global-circl-cve",
        title="CIRCL CVE Search",
        description="Public CVE search, vendor/product browsing, and CVE detail lookups from CIRCL.",
        domains=("security",),
        regions=("global",),
        keywords=(
            "circl",
            "cve",
            "vulnerability",
            "vendor",
            "product",
            "security",
        ),
        homepage="https://www.circl.lu/services/cve-search/",
    ),
    ProviderEntry(
        id="global_coingecko",
        server_name="global-coingecko",
        title="CoinGecko",
        description="Cryptocurrency market data — prices, market caps, historical charts, trending.",
        domains=("crypto", "finance"),
        regions=("global",),
        keywords=(
            "crypto",
            "bitcoin",
            "ethereum",
            "cryptocurrency",
            "price",
            "coingecko",
        ),
        homepage="https://www.coingecko.com/",
    ),
    ProviderEntry(
        id="global_crossref",
        server_name="global-crossref",
        title="Crossref",
        description="Scholarly metadata — DOIs, citations, journals, publishers, funders.",
        domains=("scholarly",),
        regions=("global",),
        keywords=("doi", "crossref", "citations", "journals", "publishers"),
        homepage="https://www.crossref.org/",
    ),
    ProviderEntry(
        id="global_crtsh",
        server_name="global-crtsh",
        title="crt.sh Certificate Transparency",
        description="Certificate transparency search for domains and certificate names via crt.sh.",
        domains=("security", "networking"),
        regions=("global",),
        keywords=(
            "crt.sh",
            "certificate transparency",
            "tls",
            "certificate",
            "x509",
            "security",
        ),
        homepage="https://crt.sh",
    ),
    ProviderEntry(
        id="global_dbnomics",
        server_name="global-dbnomics",
        title="DBnomics",
        description="Global economic data aggregator from 100+ providers (IMF, World Bank, ECB, Fed, etc.).",
        domains=("economics", "statistics"),
        regions=("global",),
        keywords=("economics", "imf", "world bank", "ecb", "series", "statistics"),
        homepage="https://db.nomics.world/",
    ),
    ProviderEntry(
        id="global_disease_sh",
        server_name="global-disease-sh",
        title="disease.sh",
        description="Aggregated disease tracking — COVID-19, influenza, vaccine coverage, historical.",
        domains=("health",),
        regions=("global",),
        keywords=("covid", "disease", "influenza", "vaccine", "pandemic"),
        homepage="https://disease.sh/",
    ),
    ProviderEntry(
        id="global_doaj",
        server_name="global-doaj",
        title="Directory of Open Access Journals (DOAJ)",
        description="Open-access journal and article search across the DOAJ index.",
        domains=("scholarly",),
        regions=("global",),
        keywords=(
            "doaj",
            "open access",
            "journals",
            "articles",
            "scholarly",
            "oa",
        ),
        homepage="https://doaj.org/",
    ),
    ProviderEntry(
        id="global_epss",
        server_name="global-epss",
        title="FIRST.org EPSS",
        description="Exploit Prediction Scoring System — daily-updated 30-day exploitation probability and percentile rank for every scored CVE. Useful for prioritizing patching beyond raw CVSS.",
        domains=("security",),
        regions=("global",),
        keywords=(
            "epss",
            "exploit",
            "prediction",
            "vulnerability",
            "cve",
            "first",
            "prioritization",
        ),
        homepage="https://www.first.org/epss/",
        license_note="EPSS data is published under CC BY 4.0; cite FIRST.org.",
    ),
    ProviderEntry(
        id="global_europepmc",
        server_name="global-europepmc",
        title="Europe PMC",
        description="Biomedical and life sciences literature — articles, references, citations, fulltext XML.",
        domains=("scholarly", "health"),
        regions=("global", "eu"),
        keywords=("europepmc", "pubmed", "biomedical", "literature", "papers"),
        homepage="https://europepmc.org/",
    ),
    ProviderEntry(
        id="global_faostat",
        server_name="global-faostat",
        title="FAOSTAT",
        description="UN Food and Agriculture Organization statistics — crop and livestock production, trade, food balances, prices, land use, fertilizers and pesticides, forestry, fisheries, food security indicators, emissions, population. 245 countries since 1961.",
        domains=("agriculture", "statistics", "environment"),
        regions=("global",),
        keywords=(
            "fao",
            "faostat",
            "agriculture",
            "food",
            "crops",
            "livestock",
            "fisheries",
            "forestry",
            "production",
            "yield",
        ),
        homepage="https://www.fao.org/faostat/en/",
        license_note="Most FAOSTAT datasets are CC BY 4.0; cite FAOSTAT with the dataset domain code.",
    ),
    ProviderEntry(
        id="global_frankfurter",
        server_name="global-frankfurter",
        title="Frankfurter (ECB FX rates)",
        description="Free, key-less FX rates sourced from European Central Bank reference rates. Latest, historical, time-series.",
        domains=("finance",),
        regions=("global", "eu"),
        keywords=("fx", "exchange rates", "currency", "forex", "ecb"),
        homepage="https://www.frankfurter.app/",
    ),
    ProviderEntry(
        id="global_gbif",
        server_name="global-gbif",
        title="Global Biodiversity Information Facility (GBIF)",
        description="Species occurrence records and taxonomic backbone from biodiversity datasets worldwide.",
        domains=("biodiversity", "earth-science"),
        regions=("global",),
        keywords=("gbif", "species", "biodiversity", "occurrence", "taxonomy"),
        homepage="https://www.gbif.org/",
    ),
    ProviderEntry(
        id="global_gdelt",
        server_name="global-gdelt",
        title="GDELT 2.0",
        description="The Global Database of Events, Language and Tone — monitors broadcast, print and web news from every country in 100+ languages, codifying events, themes, persons and tone. Article search and volume / tone time-series.",
        domains=("news", "knowledge"),
        regions=("global",),
        keywords=(
            "gdelt",
            "news",
            "events",
            "media",
            "tone",
            "sentiment",
            "global",
            "monitoring",
        ),
        homepage="https://www.gdeltproject.org/",
        license_note="GDELT data is in the public domain; please credit 'GDELT Project' when redistributing.",
    ),
    ProviderEntry(
        id="global_imf",
        server_name="global-imf",
        title="International Monetary Fund",
        description="IMF statistical data via SDMX 2.1 — IFS, BOP, DOT, GFS, government finance.",
        domains=("statistics", "economics", "finance"),
        regions=("global",),
        keywords=("imf", "international monetary fund", "ifs", "bop", "sdmx"),
        homepage="https://data.imf.org/",
    ),
    ProviderEntry(
        id="global_inaturalist",
        server_name="global-inaturalist",
        title="iNaturalist",
        description="Citizen science species observations — taxa, places, projects, users.",
        domains=("biodiversity",),
        regions=("global",),
        keywords=("inaturalist", "species", "observations", "citizen science", "taxa"),
        homepage="https://www.inaturalist.org/",
    ),
    ProviderEntry(
        id="global_met_museum",
        server_name="global-met-museum",
        title="Metropolitan Museum of Art",
        description="Met Museum Open Access collection (CC0) — objects, search, departments, artists.",
        domains=("culture",),
        regions=("global", "us"),
        keywords=("met", "museum", "art", "collection", "metropolitan"),
        homepage="https://www.metmuseum.org/art/collection",
    ),
    ProviderEntry(
        id="global_nvd_cve",
        server_name="global-nvd-cve",
        title="NVD CVE Database",
        description="NIST National Vulnerability Database CVE 2.0 API — search and fetch CVE records by keyword, CPE name, CVSS v3 severity, or publish/modify date range. Includes CVE change history.",
        domains=("security", "government"),
        regions=("us", "global"),
        keywords=(
            "cve",
            "vulnerability",
            "security",
            "advisory",
            "nvd",
            "nist",
            "cvss",
            "cpe",
            "exploit",
        ),
        homepage="https://nvd.nist.gov/developers/vulnerabilities",
        license_note="NVD data is in the public domain; no attribution required. Set NVD_API_KEY env var for higher rate limits.",
        requires_env=("NVD_API_KEY",),
    ),
    ProviderEntry(
        id="global_openalex",
        server_name="global-openalex",
        title="OpenAlex",
        description="Open scholarly metadata — works, authors, institutions, sources, concepts, publishers. Polite-pool via mailto.",
        domains=("scholarly",),
        regions=("global",),
        keywords=("openalex", "scholarly", "papers", "citations", "research", "doi"),
        homepage="https://openalex.org/",
    ),
    ProviderEntry(
        id="global_oecd",
        server_name="global-oecd",
        title="OECD Statistics (SDMX)",
        description="OECD economic and social statistics via SDMX REST — country comparisons, indicators.",
        domains=("statistics", "economics"),
        regions=("global",),
        keywords=("oecd", "statistics", "economics", "indicators", "sdmx"),
        homepage="https://data.oecd.org/",
    ),
    ProviderEntry(
        id="global_open_library",
        server_name="global-open-library",
        title="Open Library",
        description="Books, authors, works, editions metadata from Internet Archive's Open Library.",
        domains=("books", "culture"),
        regions=("global",),
        keywords=("books", "open library", "isbn", "authors", "works", "literature"),
        homepage="https://openlibrary.org/",
    ),
    ProviderEntry(
        id="global_open_meteo",
        server_name="global-open-meteo",
        title="Open-Meteo",
        description="Global open-source weather forecasts, historical archive (80+ years), and air quality.",
        domains=("weather", "environment"),
        regions=("global",),
        keywords=("weather", "forecast", "temperature", "precipitation", "air quality"),
        homepage="https://open-meteo.com/",
    ),
    ProviderEntry(
        id="global_openaq",
        server_name="global-openaq",
        title="OpenAQ",
        description="Open global air-quality data aggregated from government reference monitors and low-cost sensors. PM2.5, PM10, NO2, SO2, CO, O3, BC, plus relative humidity and temperature at thousands of stations worldwide.",
        domains=("environment", "earth-science"),
        regions=("global",),
        keywords=(
            "openaq",
            "air-quality",
            "pollution",
            "pm25",
            "pm10",
            "no2",
            "ozone",
            "sensors",
            "monitoring",
        ),
        homepage="https://openaq.org",
        license_note="Most OpenAQ sources are CC BY 4.0; per-location attribution available on each entity.",
        requires_env=("OPENAQ_API_KEY",),
    ),
    ProviderEntry(
        id="global_opensanctions",
        server_name="global-opensanctions",
        title="OpenSanctions",
        description="Database of persons and companies of political, criminal, or economic interest, compiled from 200+ official sources: OFAC SDN, UN consolidated, EU consolidated, UK HMT, national PEP lists, debarment registers, ICIJ disclosures.",
        domains=("security", "government"),
        regions=("global",),
        keywords=(
            "sanctions",
            "pep",
            "ofac",
            "watchlist",
            "compliance",
            "kyc",
            "aml",
            "screening",
            "opensanctions",
        ),
        homepage="https://www.opensanctions.org",
        license_note="Most data is CC-BY 4.0; cite 'OpenSanctions'. Commercial use requires a license — see https://www.opensanctions.org/licensing/.",
        requires_env=("OPENSANCTIONS_API_KEY",),
    ),
    ProviderEntry(
        id="global_opensky",
        server_name="global-opensky",
        title="OpenSky Network",
        description="Live ADS-B flight tracking — aircraft state vectors, arrivals, departures.",
        domains=("aviation",),
        regions=("global",),
        keywords=("opensky", "flight", "aircraft", "ads-b", "aviation", "tracking"),
        homepage="https://opensky-network.org/",
    ),
    ProviderEntry(
        id="global_osm_nominatim",
        server_name="global-osm-nominatim",
        title="OpenStreetMap Nominatim",
        description="Geocoding and reverse-geocoding from OpenStreetMap. Strict 1 req/sec fair-use limit.",
        domains=("geo", "geocoding"),
        regions=("global",),
        keywords=(
            "geocoding",
            "address",
            "nominatim",
            "osm",
            "openstreetmap",
            "lat",
            "lon",
        ),
        homepage="https://nominatim.openstreetmap.org/",
    ),
    ProviderEntry(
        id="global_osv_dev",
        server_name="global-osv-dev",
        title="OSV.dev",
        description="Open Source Vulnerabilities — Google-aggregated advisories across GHSA, PYSEC, RustSec, Go, npm, Maven, NuGet, Debian, Alpine, and more. Query by id or by package+ecosystem (+optional version).",
        domains=("security",),
        regions=("global",),
        keywords=(
            "osv",
            "vulnerability",
            "advisory",
            "ghsa",
            "pysec",
            "rustsec",
            "supply-chain",
            "open-source",
            "package",
        ),
        homepage="https://osv.dev",
        license_note="Vulnerability data is published under Apache 2.0.",
    ),
    ProviderEntry(
        id="global_overpass",
        server_name="global-overpass",
        title="OSM Overpass API",
        description="Query OpenStreetMap data with Overpass QL — nodes, ways, relations by tags and bbox.",
        domains=("geo",),
        regions=("global",),
        keywords=("overpass", "osm", "openstreetmap", "query", "tags", "amenity"),
        homepage="https://overpass-api.de/",
    ),
    ProviderEntry(
        id="global_pubchem",
        server_name="global-pubchem",
        title="NCBI PubChem",
        description="World's largest collection of freely accessible chemical information.",
        domains=("health", "biology", "chemistry"),
        regions=("global",),
        keywords=(
            "chemicals",
            "compounds",
            "substances",
            "drugs",
            "toxicology",
            "ncbi",
        ),
        homepage="https://pubchem.ncbi.nlm.nih.gov/",
    ),
    ProviderEntry(
        id="global_pwned_passwords",
        server_name="global-pwned-passwords",
        title="Pwned Passwords",
        description="Anonymous k-anonymity range lookups for breached password SHA-1 hash prefixes.",
        domains=("security",),
        regions=("global",),
        keywords=(
            "pwned passwords",
            "have i been pwned",
            "breach",
            "password",
            "k-anonymity",
            "sha1",
            "security",
        ),
        homepage="https://haveibeenpwned.com/API/v3#SearchingPwnedPasswordsByRange",
    ),
    ProviderEntry(
        id="global_rcsb_pdb",
        server_name="global-rcsb-pdb",
        title="RCSB Protein Data Bank (PDB)",
        description="Primary source for 3D biological macromolecular structures.",
        domains=("biology", "scholarly"),
        regions=("global",),
        keywords=("protein", "structure", "dna", "rna", "biology", "macromolecules"),
        homepage="https://www.rcsb.org/",
    ),
    ProviderEntry(
        id="global_ripe_stat",
        server_name="global-ripe-stat",
        title="RIPE NCC RIPEstat",
        description="Production-grade BGP data from RIPE NCC — network info, BGP state, prefix overview, routing history, geolocation.",
        domains=("networking",),
        regions=("global",),
        keywords=(
            "bgp",
            "ripe",
            "routing",
            "prefix",
            "asn",
            "internet",
            "network",
            "ripestat",
            "geolocation",
        ),
        homepage="https://stat.ripe.net/",
    ),
    ProviderEntry(
        id="global_rest_countries",
        server_name="global-rest-countries",
        title="REST Countries",
        description="Country reference data — borders, capitals, currencies, languages, populations, timezones, and regions for 250+ countries.",
        domains=("geo", "demographics", "government", "knowledge"),
        regions=("global",),
        keywords=(
            "countries",
            "iso",
            "borders",
            "capitals",
            "currencies",
            "languages",
            "populations",
            "geography",
            "reference",
        ),
        homepage="https://restcountries.com/",
    ),
    ProviderEntry(
        id="global_ssllabs",
        server_name="global-ssllabs",
        title="SSL Labs",
        description="Public TLS configuration analysis and endpoint details from Qualys SSL Labs.",
        domains=("security", "networking"),
        regions=("global",),
        keywords=(
            "ssl labs",
            "tls",
            "ssl",
            "certificate",
            "https",
            "endpoint",
            "security",
        ),
        homepage="https://api.ssllabs.com/api/v3",
    ),
    ProviderEntry(
        id="global_un_comtrade",
        server_name="global-un-comtrade",
        title="UN Comtrade",
        description="UN Comtrade — world's largest international trade statistics repository. Annual and monthly bilateral merchandise (HS/SITC/BEC) and services (EBOPS) trade reported by 200+ statistical authorities since 1962.",
        domains=("trade", "economics", "statistics"),
        regions=("global",),
        keywords=(
            "comtrade",
            "trade",
            "imports",
            "exports",
            "hs",
            "sitc",
            "tariff",
            "bilateral",
            "un",
        ),
        homepage="https://comtradeplus.un.org/",
        license_note="UN Comtrade data is free for non-commercial use; cite 'UN Comtrade'. Higher-tier subscriptions remove the 500-rows/call limit.",
        requires_env=("UN_COMTRADE_API_KEY",),
    ),
    ProviderEntry(
        id="global_unesco_heritage",
        server_name="global-unesco-heritage",
        title="UNESCO World Heritage Sites",
        description="UNESCO World Heritage Centre data — natural, cultural, and mixed World Heritage Sites with details, country, and region filters.",
        domains=("culture", "knowledge"),
        regions=("global",),
        keywords=(
            "unesco",
            "world heritage",
            "cultural heritage",
            "natural heritage",
            "historic sites",
            "monuments",
        ),
        homepage="https://whc.unesco.org/",
    ),
    ProviderEntry(
        id="global_who_gho",
        server_name="global-who-gho",
        title="WHO Global Health Observatory",
        description="WHO indicators via OData v4 — life expectancy, mortality, immunization, NCDs.",
        domains=("health", "statistics"),
        regions=("global",),
        keywords=("who", "world health", "gho", "indicators", "health statistics"),
        homepage="https://www.who.int/data/gho",
    ),
    ProviderEntry(
        id="global_wikidata",
        server_name="global-wikidata",
        title="Wikidata",
        description="Structured knowledge graph — entities, properties, SPARQL.",
        domains=("knowledge",),
        regions=("global",),
        keywords=("wikidata", "sparql", "knowledge graph", "entities", "wikimedia"),
        homepage="https://www.wikidata.org/",
    ),
    ProviderEntry(
        id="global_wikipedia",
        server_name="global-wikipedia",
        title="Wikipedia REST API",
        description="Wikipedia article summaries, related articles, page views, on-this-day. Multi-language.",
        domains=("knowledge",),
        regions=("global",),
        keywords=("wikipedia", "article", "summary", "encyclopedia", "wikimedia"),
        homepage="https://en.wikipedia.org/",
    ),
    ProviderEntry(
        id="global_world_bank",
        server_name="global-world-bank",
        title="World Bank Open Data",
        description="World Bank development indicators — GDP, population, poverty, education by country.",
        domains=("statistics", "economics"),
        regions=("global",),
        keywords=("world bank", "gdp", "indicators", "development", "economics"),
        homepage="https://data.worldbank.org/",
    ),
    ProviderEntry(
        id="nl_cbs",
        server_name="nl-cbs",
        title="Statistics Netherlands (CBS)",
        description="Dutch statistical datasets via OData v2/v3 — demographics, economy, society.",
        domains=("statistics",),
        regions=("nl",),
        keywords=("cbs", "netherlands", "dutch", "statistics", "odata"),
        homepage="https://opendata.cbs.nl/",
    ),
    ProviderEntry(
        id="nl_ndov",
        server_name="nl-ndov",
        title="NDOV Loket",
        description="Dutch public transport open data — schedules, vehicles, GTFS.",
        domains=("transit",),
        regions=("nl",),
        keywords=("ndov", "netherlands", "transit", "gtfs", "public transport"),
        homepage="https://www.ndovloket.nl/",
    ),
    ProviderEntry(
        id="nl_rechtspraak",
        server_name="nl-rechtspraak",
        title="Dutch Rechtspraak (Case Law)",
        description="Official Dutch court rulings and case law — search by ECLI and full text.",
        domains=("legal",),
        regions=("nl",),
        keywords=(
            "rechtspraak",
            "netherlands",
            "dutch",
            "legal",
            "court",
            "ruling",
            "ecli",
        ),
        homepage="https://www.rechtspraak.nl/",
    ),
    ProviderEntry(
        id="nl_tweedekamer",
        server_name="nl-tweedekamer",
        title="Tweede Kamer (Dutch Parliament)",
        description="Open data from the Dutch House of Representatives — bills, votes, members, debates.",
        domains=("government",),
        regions=("nl",),
        keywords=(
            "tweede kamer",
            "dutch parliament",
            "netherlands",
            "legislature",
            "bills",
        ),
        homepage="https://opendata.tweedekamer.nl/",
    ),
    ProviderEntry(
        id="sg_data_gov",
        server_name="sg-data-gov",
        title="Singapore Government Open Data",
        description="Singapore's government open data API — datasets and collections.",
        domains=("government",),
        regions=("sg",),
        keywords=("singapore", "data gov sg", "datasets", "catalog"),
        homepage="https://data.gov.sg/",
    ),
    ProviderEntry(
        id="uk_gov",
        server_name="uk-gov",
        title="UK Government Open Data (CKAN)",
        description="UK government open data catalog via CKAN v3 — datasets, organizations, groups, tags.",
        domains=("government",),
        regions=("uk",),
        keywords=("uk", "britain", "ckan", "catalog", "datasets", "data.gov.uk"),
        homepage="https://data.gov.uk/",
    ),
    ProviderEntry(
        id="uk_legislation",
        server_name="uk-legislation",
        title="UK legislation.gov.uk",
        description="UK Acts of Parliament, statutory instruments, devolved legislation. Atom feeds and Akoma Ntoso XML.",
        domains=("legal", "government"),
        regions=("uk",),
        keywords=(
            "legislation",
            "uk",
            "law",
            "statute",
            "act",
            "parliament",
            "akoma ntoso",
        ),
        homepage="https://www.legislation.gov.uk/",
    ),
    ProviderEntry(
        id="uk_ons",
        server_name="uk-ons",
        title="UK Office for National Statistics",
        description="ONS datasets — population, economy, labour, prices, with editions and versions.",
        domains=("statistics",),
        regions=("uk",),
        keywords=(
            "ons",
            "office for national statistics",
            "uk",
            "britain",
            "demographics",
        ),
        homepage="https://www.ons.gov.uk/",
    ),
    ProviderEntry(
        id="us_arcgis_item",
        server_name="us-arcgis-item",
        title="ArcGIS Public Item Metadata",
        description="Fetch public ArcGIS item metadata by item ID from the ArcGIS REST API — layers, maps, services, and files.",
        domains=("geo",),
        regions=("us",),
        keywords=(
            "arcgis",
            "gis",
            "item",
            "metadata",
            "esri",
            "layer",
            "map service",
        ),
        homepage="https://developers.arcgis.com/rest/users-groups-and-items/item/",
    ),
    ProviderEntry(
        id="us_cary",
        server_name="us-cary",
        title="Town of Cary Open Data",
        description="Town of Cary, NC open data via Socrata — public safety, transportation, utilities, parks.",
        domains=("government",),
        regions=("us",),
        keywords=("cary", "north carolina", "nc", "socrata", "town"),
        homepage="https://data.townofcary.org",
    ),
    ProviderEntry(
        id="us_cisa_kev",
        server_name="us-cisa-kev",
        title="CISA Known Exploited Vulnerabilities",
        description="US-CISA Known Exploited Vulnerabilities catalog — authoritative list of vulnerabilities observed exploited in the wild, with remediation due-dates for US federal agencies under BOD 22-01. Filterable by vendor, product, ransomware-known-use, or date_added.",
        domains=("security", "government"),
        regions=("us", "global"),
        keywords=(
            "cisa",
            "kev",
            "known-exploited",
            "vulnerability",
            "exploit",
            "cve",
            "ransomware",
            "bod-22-01",
        ),
        homepage="https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
        license_note="CISA KEV is in the public domain (US federal work).",
    ),
    ProviderEntry(
        id="us_cdc_socrata",
        server_name="us-cdc-socrata",
        title="US CDC Open Data (Socrata)",
        description="US Centers for Disease Control open data — disease surveillance, vital stats, vaccinations.",
        domains=("health",),
        regions=("us",),
        keywords=(
            "cdc",
            "centers for disease control",
            "health",
            "surveillance",
            "vaccinations",
        ),
        homepage="https://data.cdc.gov/",
    ),
    ProviderEntry(
        id="us_census_geocoder",
        server_name="us-census-geocoder",
        title="US Census Geocoding",
        description="Census Bureau geocoder — addresses, coordinates, geographies. Keyless.",
        domains=("geocoding", "demographics"),
        regions=("us",),
        keywords=("census", "geocoding", "address", "coordinates", "us"),
        homepage="https://geocoding.geo.census.gov/",
    ),
    ProviderEntry(
        id="us_clinicaltrials",
        server_name="us-clinicaltrials",
        title="ClinicalTrials.gov",
        description="NIH/NLM clinical trials registry v2 API — studies, conditions, interventions, locations.",
        domains=("health", "scholarly"),
        regions=("us",),
        keywords=("clinicaltrials", "nih", "trials", "studies", "medicine"),
        homepage="https://clinicaltrials.gov/",
    ),
    ProviderEntry(
        id="us_courtlistener",
        server_name="us-courtlistener",
        title="CourtListener (Free Law Project)",
        description="US federal & state court opinions, dockets, judges, oral arguments. Anonymous access at low volumes.",
        domains=("legal",),
        regions=("us",),
        keywords=(
            "courtlistener",
            "legal",
            "court",
            "opinion",
            "case law",
            "judge",
            "docket",
            "recap",
            "scotus",
        ),
        homepage="https://www.courtlistener.com/",
    ),
    ProviderEntry(
        id="us_data_gov",
        server_name="us-data-gov",
        title="Data.gov",
        description="US federal government open data catalog — search and inspect federal datasets.",
        domains=("government",),
        regions=("us",),
        keywords=("data.gov", "us", "federal", "catalog", "datasets"),
        homepage="https://data.gov/",
    ),
    ProviderEntry(
        id="us_faa_nasstatus",
        server_name="us-faa-nasstatus",
        title="FAA NAS Status",
        description="FAA National Airspace System status — ground stops, delays, advisories. XML.",
        domains=("aviation",),
        regions=("us",),
        keywords=("faa", "airport", "aviation", "delays", "ground stop", "nas"),
        homepage="https://nasstatus.faa.gov/",
    ),
    ProviderEntry(
        id="us_fayetteville",
        server_name="us-fayetteville",
        title="City of Fayetteville Open Data",
        description="City of Fayetteville, NC open data via Socrata — public safety, infrastructure, community services.",
        domains=("government",),
        regions=("us",),
        keywords=("fayetteville", "north carolina", "nc", "socrata", "city"),
        homepage="https://data.fayettevillenc.gov",
    ),
    ProviderEntry(
        id="us_fda_openfda",
        server_name="us-fda-openfda",
        title="openFDA",
        description="FDA adverse events, recalls, labels for drugs/devices/food.",
        domains=("health",),
        regions=("us",),
        keywords=("fda", "openfda", "drug", "device", "food", "recall", "adverse"),
        homepage="https://open.fda.gov/",
    ),
    ProviderEntry(
        id="us_federal_register",
        server_name="us-federal-register",
        title="US Federal Register",
        description="Daily Federal Register — rules, proposed rules, notices, presidential documents, executive orders.",
        domains=("legal", "government"),
        regions=("us",),
        keywords=(
            "federal register",
            "rule",
            "executive order",
            "regulation",
            "notice",
            "presidential",
        ),
        homepage="https://www.federalregister.gov/",
    ),
    ProviderEntry(
        id="us_nasa",
        server_name="us-nasa",
        title="NASA",
        description="Astronomy Picture of the Day, Near Earth Objects, Mars rover photos, ACE solar wind.",
        domains=("astronomy", "space"),
        regions=("us", "global"),
        keywords=(
            "nasa",
            "apod",
            "asteroids",
            "mars",
            "space",
            "astronomy",
        ),
        homepage="https://api.nasa.gov/",
    ),
    ProviderEntry(
        id="us_healthdata_gov",
        server_name="us-healthdata-gov",
        title="HealthData.gov",
        description="HHS open health data catalog via Socrata — datasets on health outcomes, insurance, demographics, and public health programs.",
        domains=("health", "government"),
        regions=("us",),
        keywords=(
            "healthdata",
            "hhs",
            "health",
            "socrata",
            "public health",
            "insurance",
        ),
        homepage="https://healthdata.gov",
    ),
    ProviderEntry(
        id="us_noaa_awc",
        server_name="us-noaa-awc",
        title="NOAA Aviation Weather Center",
        description="Aviation weather data including METARs, TAFs, and station metadata.",
        domains=("weather", "aviation"),
        regions=("us",),
        keywords=("weather", "metar", "taf", "aviation", "noaa", "airport"),
        homepage="https://aviationweather.gov/",
    ),
    ProviderEntry(
        id="us_noaa_ncei",
        server_name="us-noaa-ncei",
        title="NOAA NCEI Climate Data",
        description="NOAA National Centers for Environmental Information — climate, weather, oceans.",
        domains=("weather", "earth-science"),
        regions=("us", "global"),
        keywords=(
            "noaa",
            "ncei",
            "climate",
            "weather",
            "temperature",
            "precipitation",
        ),
        homepage="https://www.ncei.noaa.gov/",
    ),
    ProviderEntry(
        id="us_noaa_tides",
        server_name="us-noaa-tides",
        title="NOAA Tides & Currents",
        description="Water levels, tide predictions, currents from CO-OPS stations.",
        domains=("weather", "earth-science"),
        regions=("us",),
        keywords=("tides", "noaa", "water level", "currents", "predictions"),
        homepage="https://tidesandcurrents.noaa.gov/",
    ),
    ProviderEntry(
        id="us_nc_onemap",
        server_name="us-nc-onemap",
        title="NC OneMap",
        description="North Carolina's authoritative GIS data clearinghouse via ArcGIS REST — statewide geographic layers and map services.",
        domains=("geo", "government"),
        regions=("us",),
        keywords=(
            "north carolina",
            "nc",
            "arcgis",
            "onemap",
            "gis",
            "geographic",
        ),
        homepage="https://www.nconemap.gov/",
    ),
    ProviderEntry(
        id="us_ncdeq_gis",
        server_name="us-ncdeq-gis",
        title="NC DEQ Environmental GIS",
        description="North Carolina Department of Environmental Quality ArcGIS Hub — environmental permits, air quality, water quality, hazardous waste.",
        domains=("environment", "geo"),
        regions=("us",),
        keywords=(
            "ncdeq",
            "denr",
            "north carolina",
            "environment",
            "gis",
            "arcgis",
            "air quality",
            "water",
        ),
        homepage="https://data-ncdenr.opendata.arcgis.com",
    ),
    ProviderEntry(
        id="us_raleigh",
        server_name="us-raleigh",
        title="City of Raleigh Open Data",
        description="City of Raleigh open data via Socrata — public safety, infrastructure, parks, planning.",
        domains=("government",),
        regions=("us",),
        keywords=("raleigh", "north carolina", "nc", "socrata", "city"),
        homepage="https://data.raleighnc.gov",
    ),
    ProviderEntry(
        id="us_sec_edgar",
        server_name="us-sec-edgar",
        title="SEC EDGAR",
        description="US SEC EDGAR — public company filings, XBRL financial facts, company concepts.",
        domains=("finance",),
        regions=("us",),
        keywords=("sec", "edgar", "10-k", "filings", "xbrl", "stock", "company"),
        homepage="https://www.sec.gov/edgar.shtml",
    ),
    ProviderEntry(
        id="us_treasury_fiscal",
        server_name="us-treasury-fiscal",
        title="US Treasury Fiscal Data",
        description="Federal debt, daily Treasury statement, exchange rates, interest rates.",
        domains=("finance", "government"),
        regions=("us",),
        keywords=("treasury", "debt", "fiscal", "interest rates", "exchange rates"),
        homepage="https://fiscaldata.treasury.gov/",
    ),
    ProviderEntry(
        id="us_usgs_earthquake",
        server_name="us-usgs-earthquake",
        title="USGS Earthquake Hazards",
        description="USGS FDSN Event Service — real-time and historical earthquakes worldwide.",
        domains=("earth-science",),
        regions=("us", "global"),
        keywords=("usgs", "earthquake", "seismic", "magnitude", "geojson"),
        homepage="https://earthquake.usgs.gov/",
    ),
    ProviderEntry(
        id="global_mcp_registry",
        server_name="global-mcp-registry",
        title="MCP Server Registry",
        description="Official Model Context Protocol server registry — search and list published MCP servers.",
        domains=("knowledge",),
        regions=("global",),
        keywords=(
            "mcp",
            "model-context-protocol",
            "registry",
            "servers",
            "discovery",
            "ai-tools",
        ),
        homepage="https://registry.modelcontextprotocol.io",
    ),
)


@dataclass
class Registry:
    """Unified provider registry.

    Replaces the prior bimodal ``REGISTRY: tuple + DYNAMIC_REGISTRY: list``
    surface with a single container that holds both static (compile-time)
    and dynamic (runtime-registered) entries. ``add()`` is idempotent by
    ``id``; ``find()`` is the single point of resolution; iteration yields
    every entry in insertion order (statics first, dynamics in the order
    they were registered).

    The class is intentionally not frozen — ``add()`` and ``remove()`` are
    the only sanctioned mutators; tests use ``snapshot()`` / ``restore()``
    to isolate state. Internal storage is private.
    """

    _entries: list[ProviderEntry] = field(default_factory=list)
    _by_id: dict[str, ProviderEntry] = field(default_factory=dict)
    _static_count: int = 0

    @classmethod
    def from_static(cls, entries: Iterable[ProviderEntry]) -> "Registry":
        """Seed a Registry from the compile-time entries."""
        r = cls()
        for e in entries:
            r.add(e)
        r._static_count = len(r._entries)
        return r

    def add(self, entry: ProviderEntry) -> bool:
        """Add a provider. Idempotent by id.

        Returns True if a new entry was added, False if the id was already
        present (in which case the existing entry is unchanged).
        """
        if entry.id in self._by_id:
            return False
        self._entries.append(entry)
        self._by_id[entry.id] = entry
        return True

    def remove(self, provider_id: str) -> bool:
        """Remove a provider by id. Returns True if it was present.

        Removing a static entry is supported but should be rare — it's
        primarily here for tests. When the removed entry was part of the
        static seed, ``_static_count`` is decremented so subsequent
        ``dynamic()`` slices keep their correct frontier; otherwise a
        later ``add()`` would land inside the static range and be
        invisible to ``dynamic()``.
        """
        entry = self._by_id.pop(provider_id, None)
        if entry is None:
            return False
        index = self._entries.index(entry)
        del self._entries[index]
        if index < self._static_count:
            self._static_count -= 1
        return True

    def find(self, provider_id: str) -> ProviderEntry | None:
        """Resolve a provider id (case-insensitive) to its entry."""
        entry = self._by_id.get(provider_id)
        if entry is not None:
            return entry
        needle = provider_id.lower()
        for e in self._entries:
            if e.id.lower() == needle:
                return e
        return None

    def dynamic(self) -> list[ProviderEntry]:
        """Return the runtime-added entries (i.e. everything past the static seed).

        Intended for tests and for the autonomous-creation flow which needs
        to know which entries it materialized. Returns a *new* list each call.
        """
        return list(self._entries[self._static_count :])

    def snapshot(self) -> tuple[list[ProviderEntry], dict[str, ProviderEntry], int]:
        """Snapshot for restoration in tests."""
        return (list(self._entries), dict(self._by_id), self._static_count)

    def restore(
        self,
        snap: tuple[list[ProviderEntry], dict[str, ProviderEntry], int],
    ) -> None:
        """Restore from a snapshot()."""
        self._entries = list(snap[0])
        self._by_id = dict(snap[1])
        self._static_count = snap[2]

    def __iter__(self) -> Iterator[ProviderEntry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, provider_id: object) -> bool:
        return isinstance(provider_id, str) and provider_id in self._by_id


# The canonical Registry instance. Tests, providers, routing, and the
# meta-server all share this single object.
REGISTRY: Registry = Registry.from_static(_STATIC_ENTRIES)


def register_plugin(entry: ProviderEntry) -> None:
    """Register a plugin discovered at runtime.

    Used by the autonomous-creation flow after `generate_provider.py` has
    materialized a new plugin module. Subsequent calls to `find_providers`,
    `get_provider`, `all_ids`, `list_domains`, and `list_regions` will
    include the new entry. Idempotent — re-registering the same id is a no-op.
    """
    REGISTRY.add(entry)


def iter_registry() -> Iterable[ProviderEntry]:
    """Iterate over every registered entry (static + dynamic)."""
    yield from REGISTRY


def _normalize(text: str) -> str:
    return text.lower().strip()


def find_providers(
    query: str | None = None,
    domain: str | None = None,
    region: str | None = None,
    limit: int = 20,
) -> list[ProviderEntry]:
    """Return providers matching the optional query / domain / region filters.

    Scoring: keyword/title/description text matches contribute to score; domain
    and region filters are hard filters (entries lacking them are excluded).
    Results sorted by score descending, then by id alphabetically.
    """
    q = _normalize(query) if query else ""
    dom = _normalize(domain) if domain else None
    reg = _normalize(region) if region else None

    scored: list[tuple[int, ProviderEntry]] = []
    for entry in iter_registry():
        if dom and dom not in entry.domains:
            continue
        if reg and reg not in entry.regions:
            continue

        score = 0
        if q:
            haystack = " ".join(
                (
                    entry.id,
                    entry.title,
                    entry.description,
                    " ".join(entry.keywords),
                    " ".join(entry.domains),
                    " ".join(entry.regions),
                )
            ).lower()
            # Token-level matching for stable scoring across phrasing variants.
            for token in q.split():
                if not token:
                    continue
                if token in haystack:
                    score += 1
                # Bonus weight for keyword exact-match.
                if token in entry.keywords:
                    score += 2

            if score == 0:
                continue
        else:
            score = 1  # No query => all surviving filters are equal-score.

        scored.append((score, entry))

    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [entry for _, entry in scored[:limit]]


def get_provider(provider_id: str) -> ProviderEntry | None:
    """Return the registry entry for `provider_id`, or None if not present."""
    return REGISTRY.find(_normalize(provider_id))


def list_domains() -> list[str]:
    """Distinct domains actually used by registered providers."""
    found: set[str] = set()
    for entry in iter_registry():
        found.update(entry.domains)
    return sorted(found)


def list_regions() -> list[str]:
    """Distinct regions actually used by registered providers."""
    found: set[str] = set()
    for entry in iter_registry():
        found.update(entry.regions)
    return sorted(found)


def all_ids() -> list[str]:
    return [entry.id for entry in iter_registry()]


def _check_registry_vocabulary() -> Iterable[str]:
    """Yield human-readable warnings for any registry entries that use a
    domain or region outside the controlled vocabularies. Intended for tests."""
    for entry in iter_registry():
        for d in entry.domains:
            if d not in DOMAINS:
                yield f"{entry.id}: unknown domain '{d}'"
        for r in entry.regions:
            if r not in REGIONS:
                yield f"{entry.id}: unknown region '{r}'"
