# meta-data-mcp

<!-- mcp-name: io.github.derekslinz/meta-data-mcp -->

> A single MCP server that transparently routes user requests to 76 open-data sources.

`meta-data-mcp` is one MCP server — not many. Under the hood it bundles 76 *plugins*, each wrapping a different open-data API. The plugins are an implementation detail; from your LLM's perspective there is one server and one place to ask "where can I find data about X?"

You install one server. You get all the data, discoverable through built-in routing tools.

## Why "meta"?

Finding open data isn't the hard part — there's an absurd amount of it available. The hard part is finding the right dataset *when you need it*. `meta-data-mcp` makes that automatic:

- The LLM calls `opendata.providers.find` ("FX rates", "court rulings", "earthquakes near Lisbon") and the server routes the query against an internal registry of every bundled plugin.
- The LLM then calls the matching tool directly. No setup step in between, no separate servers, no per-provider install rituals.

This project was forked from [opendata-mcp](https://github.com/OpenDataMCP/OpenDataMCP) and reshaped around the single-server idea once the catalogue passed a few dozen plugins.

## Installation

You'll need `uv` (a Python package manager).

```bash
# macOS — install uv via Homebrew so MCP clients can find it
brew install uv

# Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then register the server with every MCP client installed on your machine:

```bash
uv run meta-data-mcp setup
```

The command auto-detects which MCP clients you have installed and adds **one** `meta-data-mcp` entry under `mcpServers` in each. Supported clients:

| Client | Config file |
|---|---|
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) / `%APPDATA%/Claude/claude_desktop_config.json` (Windows) |
| Claude Code | `~/.claude.json` |
| Cursor | `~/.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Gemini CLI | `~/.gemini/settings.json` |
| LM Studio | `~/.cache/lm-studio/mcp.json` |

Each existing config is backed up to `<file>.bak` before writing. Restart the affected client(s) and you'll see one new server with discovery tools available immediately; plugin tools can then be activated on demand.

Inspect what's detected / configured on your machine:

```bash
uv run meta-data-mcp clients
```

Target a single client (or write to every supported client regardless of detection):

```bash
uv run meta-data-mcp setup --client claude-code
uv run meta-data-mcp setup --client all
```

If you want to see the JSON snippet without touching any config file (e.g. to paste into a client we don't support yet):

```bash
uv run meta-data-mcp setup --print-json
```

When `META_DATA_MCP_AUTH_TOKEN` is set, `--print-json` also surfaces the SSE-client snippet (with the real token) to stderr so you can wire a remote client.

### Hosting `meta-data-mcp` as a remote SSE server

For deploying behind your own domain with bearer-token authentication, see [`docs/hosting.md`](docs/hosting.md). It covers `systemd`, Caddy/nginx TLS termination, token rotation, and the threat model.

## CLI

There is one server, so the CLI takes no "provider" argument. Every command operates on the one `meta-data-mcp` server.

| Command | What it does |
|---|---|
| `uv run meta-data-mcp run` | Run the server (default SSE; pass `--transport stdio` for Claude Desktop). |
| `uv run meta-data-mcp setup` | Register the server in detected MCP client configs (or one target via `--client`). |
| `uv run meta-data-mcp remove` | Unregister the server from detected MCP client configs (or one target via `--client`). |
| `uv run meta-data-mcp cleanup` | Detect and remove legacy multi-server entries (`--apply` to commit). |
| `uv run meta-data-mcp inspect` | Launch [mcp-inspector](https://modelcontextprotocol.io/docs/tools/inspector) against the server. |
| `uv run meta-data-mcp list` | Informational: list the internal plugins bundled in this server. |
| `uv run meta-data-mcp info` | Informational: show server overview. Pass `--plugin <name>` for plugin-level details. |
| `uv run meta-data-mcp version` | Print the package version. |

The `list` command exists for transparency about what's bundled — **plugins are not separately installable, runnable, or addressable**. They are loaded automatically when the server starts.


## Server tools (what the LLM calls)

Once `meta-data-mcp` is running, the LLM has access to two layers of tools — and you don't need to mention either to the user:

1. **Meta tools** — the 13 server-level tools below. They make routing transparent: the LLM uses them to find, activate, and (if needed) create the right plugin without you telling it which tool to call.
2. **Plugin tools** — ~330 tools coming from the 76 bundled plugins. In the default discovery-only mode they are activated per provider at runtime (or preloaded via `META_DATA_MCP_PRELOAD`). The LLM picks one after consulting the meta tools.

### Meta tools

| Tool | Purpose |
|---|---|
| `opendata.providers.find` | Free-text search over the plugin registry. Returns ranked matches. When nothing matches the response carries a `no_match: true` flag and a `next_step` hint pointing at `opendata.plugins.draft` + `opendata.plugins.create`. |
| `opendata.explain.choice` | Show the scoring breakdown for a search (useful for debugging routing decisions). |
| `opendata.domains.list` | Enumerate the controlled domain vocabulary (`health`, `legal`, `finance`, `earth-science`, …). |
| `opendata.regions.list` | Enumerate the controlled region vocabulary (`us`, `eu`, `uk`, `global`, …). |
| `opendata.providers.describe` | Full metadata for one plugin by id — title, description, domains, regions, keywords, homepage, required env vars. |
| `opendata.providers.list` | Paginated dump of the whole registry. |
| `opendata.providers.activate` | Activate one provider so its tools become callable in this session. |
| `opendata.providers.deactivate` | Remove an activated provider's tools from the current session catalog. |
| `opendata.providers.list_active` | List currently active providers and the tool names each contributes. |
| `opendata.health.snapshot` | Return per-provider health scores used by discovery health badges and routing context. |
| `opendata.plugins.draft` | **Build a validated plugin YAML spec from structured inputs.** Takes id, base_url, tool definitions (name, endpoint, params), and registry metadata. Validates id/tool-name casing, path-placeholder/param consistency, and parameter types, then emits a YAML string ready to feed into `opendata.plugins.create`. Use this so the LLM never has to hand-author YAML. |
| `opendata.plugins.create` | **Autonomously create a new plugin.** Takes a YAML spec (typically produced by `opendata.plugins.draft`), runs the generator, imports the new module, registers it in the live registry, and hot-loads its tools onto the running server. Use this when `opendata.providers.find` returns no match. |
| `opendata.tool.call` | Proxy-call an activated plugin tool by name for environments that cannot directly invoke dynamically added tools. |

### The autonomous discovery flow

The reason this server is called "meta" is that it routes data requests on the user's behalf — including by *creating* the route when one doesn't exist yet. The full flow:

1. **User asks for data**, e.g. "show me the most recent published CVEs."
2. **LLM calls `opendata.providers.find`** with the query (`cve`, `vulnerability`, …).
3. **If the registry has a match**: the LLM activates the matching provider (`opendata.providers.activate`, or `activate_top` in find) and then calls the plugin tool.
4. **If the registry has no match**: the response includes `no_match: true` and a `next_step` field that explains the autonomous creation path. The LLM:
   1. Tells the user it's about to add coverage for this data source.
   2. Web-searches for an open API that exposes the requested data (e.g. the NVD or CIRCL CVE API).
   3. Calls `opendata.plugins.draft` with the API's id, base URL, and structured tool definitions. The server validates the inputs (id casing, path-placeholder consistency, parameter types) and returns a YAML string.
   4. Passes that YAML to `opendata.plugins.create`. The server materializes the plugin module + tests, imports the module, registers a `ProviderEntry` in the in-memory dynamic registry, and merges the new tools into the running server's tool list.
   5. Calls the newly-available tool to answer the user's original question.
5. **User gets their answer** — and the plugin remains available for the rest of the session.

The materialized plugin lives on disk (`meta_data_mcp/providers/{id}.py` + `tests/providers/test_{id}.py`); contributors can clean it up, add it to `meta_data_mcp/registry.py` as a static entry, and open a PR so it becomes part of every shipped install.

### Plugin tools

Every bundled plugin contributes its own tools under the one server. Their names are unique kebab-case identifiers, often using a provider-specific prefix (e.g. `usgs-eq-feed-significant-week`, `frankfurter-latest`, `wikipedia-fetch-summary`). The LLM discovers them through `opendata.providers.find`/`opendata.providers.describe`, activates the provider when needed, and can inspect session state with `opendata.providers.list_active`.

## Presentation layer (MCP Apps)

v2.0 adds a visual layer on top of every tool result. Hosts that support the [MCP Apps extension](https://modelcontextprotocol.io/docs/extensions/apps) (Claude Desktop, MCP Inspector, others) render bound tool results inline as interactive panels in a sandboxed iframe instead of as JSON text. Hosts that don't speak MCP Apps fall back to the same JSON they always got — the binding is purely additive.

Each MCP-Apps-aware tool declares its panel via `_meta.ui.resourceUri` on the tool description. The host fetches the `ui://` resource (HTML + bundled JS, single payload, no external requests besides explicitly-whitelisted CDNs) and dispatches bidirectional `postMessage` events between the iframe and itself.

### Shape primitives — `ui://meta-data-mcp/shape/<name>/v1`

Three reusable bundles cover the common payload contracts. Any tool whose response matches one of these shapes binds to the corresponding primitive automatically and gets a rich renderer for free.

| Shape | Renders | Payload contract |
|---|---|---|
| `timeseries/v1` | Line chart + auto-computed profile (min/max/mean/stddev/gap-count) via Plotly. | `{points: [{date, value, series?}], axes: {x, y}, annotations?}` |
| `geofeatures/v1` | Leaflet map + marker cluster (with density layer for high-cardinality outputs). | `{features: GeoJSON | [{lat, lon, attrs}], layers?, facets?}` |
| `records/v1` | Faceted, sortable, paginated HTML table + per-column auto-profile (type inference, top-k, null rate, range). | `{rows: [...], schema?, default_facets?}` |

### Custom apps — `ui://meta-data-mcp/app/<name>/v1`

Some data shapes don't fit a generic primitive. v2.0 ships dedicated apps for them:

| App | Drives | Visualization |
|---|---|---|
| `discovery/v1` | `opendata.providers.find`, `opendata.domains.list`, `opendata.regions.list`, `opendata.providers.activate`, etc. | Faceted plugin browser with live health badges. |
| `vulnerability/v1` | `nvd-*`, `osv-*`, `epss-*`, `cisa-kev`. | CVSS radar + severity heatmap + exploitation-probability gauge. |
| `entity-graph/v1` | `crossref-works-by-author`, `openalex-search-works`, `wikidata-search-entities`, `opensanctions-search`. | Force-directed graph (D3) with co-authorship overlay. |
| `trade-flows/v1` | `comtrade-trade-data`. | Reporter → commodity → partner Sankey + commodity treemap. |
| `news-tone/v1` | `gdelt-article-search`, `gdelt-volume-timeline`. | Volume + tone timeline with country-pair chord diagram. |
| `network-topology/v1` | `ripestat-asn-neighbours` and friends. | Force-directed ASN peering/upstream/downstream graph. |
| `molecular/v1` | `pubchem-compound`, `pdb-entry`. | WebGL 3D structure viewer (3Dmol.js, cartoon for proteins, stick+sphere for ligands). |
| `museum/v1` | `met-search`, `met-search-by-artist`, `met-get-object`. | Lazy-loaded CSS-grid image gallery + provenance detail panel. |

### Building new apps

Adding a UI binding to a generated provider is now a one-line spec change:

```yaml
tools:
  - name: my-tool
    description: ...
    endpoint: /foo
    response_shape: records   # ← binds to the shape primitive
```

See [`tools/specs/README.md`](tools/specs/README.md) for the full reference. Bundle-size budgets are enforced in CI (warn ≥ 100 KB, error ≥ 1 MB); the v2.0 bundles range from 14 KB (timeseries primitive) to 34 KB (vulnerability app), all comfortably inside the budget.

## Bundled plugins (76)

This is what's inside the one server. You don't install these individually — they all come along.

### Government / Civic

| Plugin | Source | Description |
|---|---|---|
| `au_data_gov` | Australian Government Open Data | CKAN catalog at data.gov.au |
| `ca_open_gov` | Canada Open Data | CKAN catalog at open.canada.ca |
| `fr_data_gouv` | data.gouv.fr | French government open data platform |
| `nl_tweedekamer` | Tweede Kamer | Dutch Parliament open data |
| `sg_data_gov` | Singapore Open Data | data.gov.sg datasets and collections |
| `uk_gov` | data.gov.uk | UK government CKAN catalog |
| `us_cary` | Town of Cary Open Data | Town of Cary, NC open data via Socrata — public safety, transportation, utilities, parks |
| `us_data_gov` | Data.gov | US federal government open datasets |
| `us_fayetteville` | City of Fayetteville Open Data | City of Fayetteville, NC open data via Socrata — public safety, infrastructure, community services |
| `us_raleigh` | City of Raleigh Open Data | City of Raleigh open data via Socrata — public safety, infrastructure, parks, planning |

### Statistics / Economics

| Plugin | Source | Description |
|---|---|---|
| `eu_eurostat` | Eurostat | European Union statistics |
| `global_imf` | International Monetary Fund | IMF SDMX 2.1 statistical data |
| `global_faostat` | FAOSTAT | UN food and agriculture statistics — production, prices, trade, land use, emissions |
| `global_dbnomics` | DBnomics | Global economic data aggregator (IMF, World Bank, etc.) |
| `global_oecd` | OECD | OECD economic & social statistics (SDMX) |
| `global_world_bank` | World Bank | Development indicators by country |
| `nl_cbs` | Statistics Netherlands (CBS) | Dutch statistical datasets (OData v2/v3) |
| `uk_ons` | UK ONS | UK Office for National Statistics |

### Finance / Markets

| Plugin | Source | Description |
|---|---|---|
| `eu_ecb` | European Central Bank | ECB data portal (SDMX) — FX, monetary, banking |
| `global_coingecko` | CoinGecko | Cryptocurrency market data |
| `global_frankfurter` | Frankfurter | ECB reference FX rates (key-less) |
| `us_sec_edgar` | SEC EDGAR | Public company filings, XBRL financials |
| `us_treasury_fiscal` | US Treasury Fiscal Data | Federal debt, daily Treasury statement, FX rates |

### Health & Life Sciences

| Plugin | Source | Description |
|---|---|---|
| `global_disease_sh` | disease.sh | COVID-19, influenza, vaccine aggregator |
| `global_pubchem` | NCBI PubChem | Chemical compounds and substances |
| `global_rcsb_pdb` | RCSB PDB | 3D protein and macromolecular structures |
| `global_who_gho` | WHO GHO | WHO Global Health Observatory (OData) |
| `us_cdc_socrata` | US CDC | CDC open data via Socrata |
| `us_clinicaltrials` | ClinicalTrials.gov | NIH/NLM clinical trials registry v2 |
| `us_fda_openfda` | openFDA | FDA adverse events, recalls, labels |
| `us_healthdata_gov` | HealthData.gov | HHS open health data via Socrata — outcomes, insurance, demographics, public health |

### Earth Science / Weather / Environment

| Plugin | Source | Description |
|---|---|---|
| `eu_copernicus` | Copernicus (EU) | European Earth observation and climate datasets |
| `global_open_meteo` | Open-Meteo | Weather forecast + historical + air quality |
| `global_openaq` | OpenAQ | Global air-quality measurements from reference monitors and sensors |
| `us_ncdeq_gis` | NC DEQ Environmental GIS | NC Dept. of Environmental Quality ArcGIS Hub — permits, air/water quality, hazardous waste |
| `us_noaa_ncei` | NOAA NCEI | Climate data access services (key-less) |
| `us_noaa_tides` | NOAA Tides & Currents | Water levels, tides, currents |
| `us_usgs_earthquake` | USGS Earthquakes | Real-time and historical seismic events |

### Biodiversity / Space / Physics

| Plugin | Source | Description |
|---|---|---|
| `cern_opendata` | CERN Open Data | Particle physics datasets and software |
| `global_gbif` | GBIF | Global biodiversity occurrence records |
| `global_inaturalist` | iNaturalist | Citizen-science species observations |
| `global_opensky` | OpenSky Network | Live ADS-B flight tracking |
| `us_nasa` | NASA | APOD, Near Earth Objects, Mars rover photos |

### Geo / Mapping / Knowledge

| Plugin | Source | Description |
|---|---|---|
| `global_mcp_registry` | MCP Server Registry | Official MCP server registry — search and list published MCP servers |
| `global_osm_nominatim` | OSM Nominatim | Geocoding / reverse-geocoding (1 req/sec) |
| `global_overpass` | OSM Overpass | Query OpenStreetMap with Overpass QL |
| `global_rest_countries` | REST Countries | Country reference data — borders, capitals, currencies, languages, populations |
| `global_wikidata` | Wikidata | Structured knowledge graph + SPARQL |
| `global_wikipedia` | Wikipedia | Article summaries, related, page views |
| `us_arcgis_item` | ArcGIS REST API | Fetch public ArcGIS item metadata by ID — layers, maps, services, files |
| `us_census_geocoder` | US Census Geocoder | Address ⇄ coordinates ⇄ geographies |
| `us_nc_onemap` | NC OneMap | NC's authoritative GIS clearinghouse via ArcGIS REST — statewide geographic layers |

### Agriculture / Trade

| Plugin | Source | Description |
|---|---|---|
| `global_un_comtrade` | UN Comtrade | International merchandise and services trade statistics |

### Security / Vulnerability

| Plugin | Source | Description |
|---|---|---|
| `global_epss` | FIRST.org EPSS | Exploit prediction scores and percentile ranks for CVEs |
| `global_nvd_cve` | NVD CVE Database | NIST CVE records, filters, and change history |
| `global_opensanctions` | OpenSanctions | Sanctions, PEP, debarment, and related risk datasets |
| `global_osv_dev` | OSV.dev | Open source vulnerability advisories across ecosystems |
| `us_cisa_kev` | CISA KEV | Known Exploited Vulnerabilities catalog with remediation deadlines |

### Transit / Aviation

| Plugin | Source | Description |
|---|---|---|
| `ch_sbb` | Swiss Federal Railways | Swiss train disruptions and service data |
| `de_db` | Deutsche Bahn | German railway open data |
| `nl_ndov` | NDOV Loket | Dutch public transport data |
| `us_faa_nasstatus` | FAA NAS Status | US airspace status, delays, ground stops (XML) |
| `us_noaa_awc` | NOAA Aviation Weather | METAR, TAF, and station weather data |

### Scholarly Literature

| Plugin | Source | Description |
|---|---|---|
| `global_arxiv` | arXiv | Preprint metadata (Atom XML) |
| `global_crossref` | Crossref | DOI metadata, citations, journals |
| `global_europepmc` | Europe PMC | Biomedical literature + fulltext XML |
| `global_openalex` | OpenAlex | Open scholarly metadata |

### Culture / Books

| Plugin | Source | Description |
|---|---|---|
| `global_met_museum` | Met Museum | Met Museum Open Access (CC0) |
| `global_open_library` | Open Library | Books, authors, works (Internet Archive) |
| `global_unesco_heritage` | UNESCO World Heritage Sites | Natural, cultural & mixed World Heritage Sites |

### News / Media

| Plugin | Source | Description |
|---|---|---|
| `global_gdelt` | GDELT 2.0 | Global news, event, and tone monitoring across 100+ languages |

### Networking / Internet

| Plugin | Source | Description |
|---|---|---|
| `global_bgpview` | BGPView | BGP routing data — ASN info, prefixes, peers (key-less) |
| `global_ripe_stat` | RIPE NCC RIPEstat | Production-grade BGP data (key-less) |

### Legal

| Plugin | Source | Description |
|---|---|---|
| `nl_rechtspraak` | Dutch Rechtspraak | Dutch court rulings and case law (ECLI) |
| `uk_legislation` | UK legislation.gov.uk | UK Acts, statutory instruments (XML/Atom) |
| `us_courtlistener` | CourtListener | US court opinions, dockets, judges (Free Law Project) |
| `us_federal_register` | US Federal Register | Daily rules, notices, executive orders |

## Optional environment variables

A few bundled plugins accept optional API keys for higher rate limits. Set these in your shell or in the Claude Desktop server config's `env` block:

| Variable | Plugin | Purpose |
|---|---|---|
| `COURTLISTENER_API_TOKEN` | `us_courtlistener` | Anonymous access works at low volumes |
| `NVD_API_KEY` | `global_nvd_cve` | Raises NVD API rate limits |
| `META_DATA_MCP_CONTACT` | all | Your email, used in User-Agent for polite-pool APIs (Crossref, OpenAlex, OSM, SEC EDGAR). Defaults to `meta-data-mcp@example.org`. |
| `OPENAQ_API_KEY` | `global_openaq` | Enables authenticated OpenAQ API access |
| `OPENSANCTIONS_API_KEY` | `global_opensanctions` | Enables authenticated OpenSanctions API access |
| `UN_COMTRADE_API_KEY` | `global_un_comtrade` | Enables higher-tier UN Comtrade API access |

### Server runtime flags

| Variable | Purpose |
|---|---|
| `META_DATA_MCP_PRELOAD` | Comma-separated plugin ids to activate at startup, or `*` for all. Default unset = discovery-only (~13 meta tools). |
| `META_DATA_MCP_AUTH_TOKEN` | When set on the SSE transport, requires `Authorization: Bearer <token>` on `/sse` and `/messages`. |
| `META_DATA_MCP_OAUTH_ISSUER` | Enable OAuth 2.0 Authorization Code + PKCE. Set to the server's public base URL (e.g. `http://localhost:8000`). Mounts `/.well-known/oauth-authorization-server`, `/register`, `/authorize`, `/token`, `/revoke`, and a consent page at `/oauth/consent`. Coexists with `META_DATA_MCP_AUTH_TOKEN` — both auth methods remain valid simultaneously. |
| `META_DATA_MCP_PROVENANCE` | Truthy (`1`, `true`, `yes`, `on`) enables a `meta-data-mcp/provenance` entry on every tool-call result's first content block's `_meta`, carrying `sha256` and `timestamp` (ISO 8601 UTC, ms precision). The digest covers the canonical `(tool, arguments, content)` envelope — content blocks dumped via `model_dump(mode="json", by_alias=True, exclude_none=True)` with `_meta` stripped, JSON-serialized with `sort_keys=True, separators=(",",":"), ensure_ascii=True`. Binding tool name + arguments into the hash means audit logs can distinguish "tool A returned X" from "tool B returned X". Default off — opt in when you need tamper-evidence. See `meta_data_mcp/provenance.py` module docstring for the verbatim receiver recipe. |

## Transports

`run` defaults to **SSE** (HTTP, port 8000) so you can connect from the MCP Inspector or remote clients. For Claude Desktop (which the `setup` command targets), the spawned process uses **stdio**:

```bash
uv run meta-data-mcp run                                  # SSE on 127.0.0.1:8000
uv run meta-data-mcp run --transport stdio                # stdio
uv run meta-data-mcp run --host 0.0.0.0 --port 3001       # SSE bound to all interfaces
```


## Roadmap

### Shipped

- **Hierarchical discovery (v2.0):** `opendata.providers.find` with ranked scoring replaces the originally-planned browse/list tools.
- **Agent-driven generation (v2.1):** `opendata.plugins.draft` + `opendata.plugins.create` let the model close coverage gaps autonomously. Hardened in v2.1.1 with input allowlists, path containment, and a post-generation AST validator (14 RCE/path-traversal/bypass paths closed).
- **Self-hosted SSE deployment (v2.1):** bearer-auth-protected, systemd-managed, reverse-proxied.
- **Multi-language SDK (v2.2):** Python embedded client (`meta_data_mcp.sdk`) and TypeScript/Node client (`@meta-data-mcp/sdk`) for discovery over MCP SSE.
- **OAuth 2.0 (v2.3):** Authorization Code + PKCE + Dynamic Client Registration. Works with Claude.ai (StreamableHTTP) and MCP Inspector. `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource`, and `/.well-known/openid-configuration` all served.
- **MCP registry provider (v2.3.4):** `mcp.registry.search` and `mcp.registry.list` — discover other MCP servers from within meta-data-mcp. Listed on the official MCP registry and Smithery.

### Still ahead

- Expand provider coverage beyond the current 76.


## Credits

- Originally conceived by [grll](https://github.com/grll) as `opendata-mcp`.
- Forked and reshaped around the single-server "meta-mcp" model.
- Built on [Anthropic's open-source MCP spec](https://spec.modelcontextprotocol.io/).


## License

MIT — see [LICENSE](LICENSE).
