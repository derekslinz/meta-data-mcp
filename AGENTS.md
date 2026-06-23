# AGENTS.md — meta-data-mcp

Working guide for AI agents (Copilot, Claude, etc.) contributing to this repo.

---

## What this repo is

One MCP server (`meta-data-mcp`) that exposes 66 open-data *plugins* under a single tool
namespace. An LLM calls `opendata_providers_find` to discover which plugin to use, then
calls that plugin's tool directly. The server can also scaffold and hot-load new plugins
at runtime via `opendata_plugins_draft` + `opendata_plugins_create`.

---

## Codebase map

```
meta_data_mcp/
  cli.py            — Click CLI (run, setup, remove, cleanup, inspect, list, info, version)
  registry.py       — REGISTRY list of ProviderEntry dataclasses; DOMAINS/REGIONS vocabs
  routing.py        — RoutingEngine: multi-criteria provider ranking (token, fuzzy, semantic, metadata)
  utils.py          — http_get(), TTL cache, MCP server factory, to_json_text()
  providers/
    __template__.py — Canonical pattern every plugin must follow
    {id}.py         — One file per plugin (66 total)
  providers/meta_data_mcp.py  — Meta tools: find-providers, draft-spec, create-plugin, etc.

tools/
  generate_provider.py        — CLI generator: YAML spec → provider module + test file
  specs/                      — YAML specs for generated plugins

tests/
  providers/test_{id}.py      — Unit tests (mock at httpx.get boundary)
  live/                       — Live integration tests (skip by default; run with -m live)
  test_routing.py             — RoutingEngine unit tests
  test_registry.py            — Registry validation tests
  test_generator.py           — Generator tests
```

---

## Dev setup

```bash
git clone https://github.com/derekslinz/meta-data-mcp.git
cd meta-data-mcp
uv venv && source .venv/bin/activate
uv sync
pre-commit install
```

Run the server locally:
```bash
uv run meta-data-mcp run                   # SSE on 127.0.0.1:8000
uv run meta-data-mcp run --transport stdio # stdio (Claude Desktop mode)
```

---

## Adding a plugin

### Generator path (preferred for REST/JSON APIs)

1. Copy `tools/specs/example_weather_alert.yaml` → `tools/specs/{id}.yaml`
2. Fill in `id`, `base_url`, tool definitions, and registry metadata
3. Dry-run: `uv run python tools/generate_provider.py tools/specs/{id}.yaml --dry-run`
4. Generate: `uv run python tools/generate_provider.py tools/specs/{id}.yaml`
5. Add a `ProviderEntry` to `meta_data_mcp/registry.py`
6. Run `uv run pytest`

See `tools/specs/README.md` for the full YAML field reference.

### Manual path (auth headers, POST, multi-step logic)

1. Create `meta_data_mcp/providers/{country}_{org}.py` from `__template__.py`
2. Use `http_get` from `meta_data_mcp.utils` for all outbound HTTP — **never call `httpx` directly**
3. Populate module-level `TOOLS: list[types.Tool]` and `TOOLS_HANDLERS: dict[str, Callable]`
4. Add a `ProviderEntry` to `meta_data_mcp/registry.py`
5. Add `tests/providers/test_{id}.py` — mock at the `http_get` (or `httpx.get`) boundary

---

## ProviderEntry fields

```python
ProviderEntry(
    id="us_example",           # snake_case, unique
    server_name="us-example",  # kebab-case version of id
    title="Example Source",    # short human title
    description="...",         # 1-2 sentences; used by routing engine
    domains=("government",),   # tuple of values from DOMAINS vocab below
    regions=("us",),           # tuple of values from REGIONS vocab below
    keywords=("example", ...), # words a user might type; drives token matching
    homepage="https://...",    # optional
    env_vars=(),               # optional env var names the plugin requires
)
```

### Controlled vocabularies

**DOMAINS** (use exact strings):
`government`, `statistics`, `economics`, `finance`, `health`, `earth-science`,
`environment`, `biodiversity`, `weather`, `space`, `astronomy`, `physics`,
`transit`, `aviation`, `geo`, `geocoding`, `knowledge`, `scholarly`, `culture`,
`books`, `legal`, `crypto`, `demographics`, `biology`, `chemistry`, `networking`

**REGIONS** (use exact strings):
`global`, `us`, `eu`, `uk`, `de`, `fr`, `nl`, `ch`, `ca`, `au`, `sg`

Do not invent new domain or region values — extend the tuples in `registry.py` if needed.

---

## Testing

```bash
uv run pytest                  # all unit tests (live tests excluded)
uv run pytest -m live          # live integration tests (make real HTTP calls)
uv run pytest tests/providers/test_{id}.py  # single provider
```

**Mocking convention:** patch `httpx.get` (or `meta_data_mcp.utils.http_get`) at the call
site; assert on `result[0].text`. See `tests/providers/test_au_data_gov.py` for the pattern.

---

## Lint

```bash
uv run ruff check .   # lint
uv run ruff format .  # format
```

Pre-commit runs ruff automatically on `git commit`.

---

## Key invariants — never violate these

- **One server, one namespace.** Plugins are not MCP servers. They expose `TOOLS` and
  `TOOLS_HANDLERS` dicts; the meta server merges them. Never create a standalone
  `mcp.Server` inside a plugin module.
- **Use `http_get` from utils.** It sets the required `User-Agent` and handles the TTL
  cache. Direct `httpx` calls bypass caching and may get blocked by polite-pool APIs.
- **Tool names are kebab-case and globally unique.** Prefix with a provider-specific
  string (e.g. `usgs-eq-feed-*`, `frankfurter-*`) to avoid collisions.
- **Pydantic for schemas.** Use `BaseModel` + `Field(description=...)` for all parameter
  models. The `description` strings are what the LLM reads when deciding which params to pass.
- **No new top-level dependencies without discussion.** The dependency surface is
  intentionally small; adding a new package requires a clear justification.
- **Registry accuracy matters.** The routing engine scores providers entirely on
  `description`, `domains`, `regions`, and `keywords`. Vague or missing metadata
  means the provider will never be discovered.
