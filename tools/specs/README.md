# Provider Specs

YAML descriptions of HTTP open-data APIs that the generator
(`tools/generate_provider.py`) compiles into opendata-mcp provider modules
and matching test stubs.

## Running the generator

```bash
# Dry run — just print the generated provider + test code to stdout
uv run python tools/generate_provider.py tools/specs/example_weather_alert.yaml --dry-run

# Write the files to disk (fails if outputs already exist)
uv run python tools/generate_provider.py tools/specs/example_weather_alert.yaml

# Overwrite existing files
uv run python tools/generate_provider.py tools/specs/example_weather_alert.yaml --force
```

Outputs are written to:

- `meta_data_mcp/providers/{id}.py`
- `tests/providers/test_{id}.py`

Generation is deterministic — running twice with the same spec produces
byte-identical output.

## YAML field reference

### Top-level

| Field          | Type        | Required | Description                                              |
| -------------- | ----------- | -------- | -------------------------------------------------------- |
| `id`           | string      | yes      | Snake_case module name (e.g. `us_nws_alerts`).           |
| `server_name`  | string      | yes      | MCP server name passed to `create_mcp_server` (kebab).   |
| `base_url`     | string      | yes      | Origin + base path for all endpoints (no trailing `/`).  |
| `description`  | string      | yes      | Free-form description used in the module docstring.      |
| `homepage`     | string      | no       | Provider homepage, recorded in the module docstring.     |
| `domains`      | list[str]   | no       | Registry hint — not used by the generator itself.        |
| `regions`      | list[str]   | no       | Registry hint — not used by the generator itself.        |
| `keywords`     | list[str]   | no       | Registry hint — not used by the generator itself.        |
| `requires_env` | list[str]   | no       | Env vars that must be set; generator imports and calls `require_env_key(...)` for each. |
| `tools`        | list[tool]  | yes      | One entry per MCP tool the provider exposes.             |

### Each `tools[]` entry

| Field             | Type         | Required | Description                                                 |
| ----------------- | ------------ | -------- | ----------------------------------------------------------- |
| `name`            | string       | yes      | Kebab-case MCP tool name (e.g. `nws-list-alerts`).          |
| `description`     | string       | yes      | Shown to the LLM in the tool catalog.                       |
| `endpoint`        | string       | yes      | Path appended to `base_url`. May contain `{name}` segments. |
| `response_format` | `json`/`text`| no       | Defaults to `json`. `text` returns the raw body (≤20 000c). |
| `response_shape`  | enum         | no       | One of `none` (default), `timeseries`, `geofeatures`, `records`. Wires the tool to the matching MCP Apps shape primitive — see below. |
| `params`          | list[param]  | no       | Tool input parameters (becomes a Pydantic model).           |

### Each `params[]` entry

| Field         | Type    | Required | Description                                              |
| ------------- | ------- | -------- | -------------------------------------------------------- |
| `name`        | string  | yes      | Parameter name (Python identifier, snake_case).          |
| `type`        | string  | yes      | One of `str`, `int`, `float`, `bool`.                    |
| `required`    | bool    | no       | Defaults to `false`. Required params become `Field(...)`.|
| `default`     | scalar  | no       | Default value for optional params.                       |
| `description` | string  | no       | Reflected in the Pydantic `Field(description=...)`.      |

### Path parameters

If `endpoint` contains `{placeholder}` segments, the matching `params[]`
entries are substituted into the URL via f-string interpolation and are
**not** sent as query parameters. Multiple placeholders in a single segment
(e.g. `/points/{latitude},{longitude}`) are supported.

### MCP Apps shape binding (`response_shape`)

Setting `response_shape: timeseries | geofeatures | records` does three
things automatically:

1. Adds the matching URI import from `meta_data_mcp.ui_resources` to the
   generated module.
2. Emits `_meta={"ui": {"resourceUri": <URI>}}` on the tool's
   `types.Tool(...)` registration so the MCP Apps host renders results
   through the bound shape primitive.
3. Swaps the handler's serializer for the size-bounded helper that
   preserves the shape contract on overflow:
   - `records` → `to_records_text(data)` (trims `rows` list)
   - `geofeatures` → `to_geofeatures_text(data)` (trims feature list)
   - `timeseries` → `to_json_text(data, max_chars=MAX_RESPONSE_CHARS)`
     (no list-key in the timeseries contract to trim; falls back to the
     generic truncation wrapper)

The generator emits a **`# TODO: write a _<snake>_to_shape_payload(data)`
adapter** comment in the handler. The shape-mapping function is
provider-specific (SDMX observations vs JSON-stat vs native rate tables
vs …) and the generator deliberately doesn't guess. Write it before the
provider goes live; the size-bounded serializer assumes the data already
matches the shape's payload contract.

`response_shape` requires `response_format: json` — text responses can't
carry the shape envelope.

## What the generator does NOT handle

The generator is intentionally narrow. For anything outside this list,
edit the generated module by hand — it is a starting point, not a finished
provider.

- **Auth headers** — no support for `Authorization: Bearer`, signed
  requests, or per-request token refresh. `requires_env` currently injects
  each key as an `api_key` query parameter via `require_env_key(...)`.
- **POST / PUT / DELETE** — every generated fetch uses `http_get`.
- **Non-trivial response shaping** — the handler returns
  `response.json()` or `response.text` verbatim. Custom flattening,
  pagination unrolling, decoding XML/CSV/Atom, or trimming nested fields
  must be added after generation.
- **Multi-step calls** — flows that need to hit endpoint A then feed its
  output into endpoint B require manual code.
- **Server-Sent Events / WebSockets / streaming responses.**
- **Complex parameter schemas** — lists, nested objects, enum validation,
  cross-field validation. Only scalar params with simple types are
  supported.
- **Resources or Prompts** — only Tools are generated. `RESOURCES` and
  `RESOURCES_HANDLERS` are emitted as empty lists for forward compatibility.
- **Registry entries** — the generator does NOT update
  `meta_data_mcp/registry.py`. Add a `ProviderEntry` there manually.
- **Rate-limit handling, retries, polite-pool headers** — beyond the
  defaults baked into `http_get`.

## Adding a new provider

1. Copy `example_weather_alert.yaml` to `tools/specs/{id}.yaml` and edit.
2. Run `uv run python tools/generate_provider.py tools/specs/{id}.yaml --dry-run`
   to preview.
3. Run without `--dry-run` to write the files.
4. Add a `ProviderEntry` to `meta_data_mcp/registry.py`.
5. Open the generated provider and test files; refine descriptions,
   tighten validation, and add tool-specific assertions.
