#!/usr/bin/env python3
"""
generate_provider.py — scaffold a new opendata-mcp provider from a YAML spec.

Usage:
    uv run python tools/generate_provider.py <spec.yaml> [--dry-run] [--force]

The generator produces two files (or prints them to stdout with --dry-run):

    meta_data_mcp/providers/{spec.id}.py    — the provider module
    tests/providers/test_{spec.id}.py      — test stubs for each tool

The output follows the canonical pattern established by
``meta_data_mcp/providers/us_nasa.py`` and uses the shared helpers
``http_get`` / ``serialize_for_llm`` / ``run_server`` / ``create_mcp_server``
from ``meta_data_mcp.utils``.

The generator is intentionally narrow: it handles the common case of a
provider that exposes one or more HTTP GET endpoints with simple query and
path parameters. See ``tools/specs/README.md`` for the full YAML reference
and a list of cases the generator does NOT handle (auth headers, POST,
multi-step transforms, etc.).

Only depends on the Python standard library plus PyYAML.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
PROVIDERS_DIR = REPO_ROOT / "meta_data_mcp" / "providers"
TESTS_DIR = REPO_ROOT / "tests" / "providers"

# Map YAML scalar type names to Python type annotations used in Pydantic models.
TYPE_MAP: dict[str, str] = {
    "str": "str",
    "string": "str",
    "int": "int",
    "integer": "int",
    "float": "float",
    "number": "float",
    "bool": "bool",
    "boolean": "bool",
}

PATH_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Allowlist regexes — gate every spec field that ends up in generated Python
# source as a raw identifier or string fragment (not via _py_literal). These
# block template-injection / RCE via the create-plugin flow where the YAML
# spec comes from an untrusted MCP client.
_PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ENDPOINT_RE = re.compile(r"^[A-Za-z0-9/_\-\.\{\},:%~]+$")
_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-\.]+$")

# MCP Apps (v2.0 Phase 6a) — declarative shape binding.
#
# When a tool's spec includes ``response_shape``, the generator emits:
#   1. An import of the shape's URI constant from meta_data_mcp.ui_resources
#   2. ``_meta={"ui": {"resourceUri": <URI_CONST>}}`` on the Tool(...) line
#      so the MCP Apps host renders the result via the shape primitive
#   3. A size-bounded serializer in the handler (records/geofeatures get
#      their dedicated trim-by-list helpers; timeseries falls back to the
#      generic ``to_json_text(max_chars=...)`` because the timeseries
#      bundle accepts a wrapped truncation envelope)
#   4. A TODO comment in the handler reminding the author to write the
#      provider-specific ``_<provider>_<tool>_to_shape_payload`` adapter
#      that maps the raw API response onto the shape contract.
#
# Why a TODO instead of an auto-generated adapter: the contract maps are
# inherently provider-specific (SDMX observations vs JSON-stat vs the
# Frankfurter/ECB native shape vs ...), and the generator deliberately
# stays narrow rather than guess wrong and ship a broken adapter.
RESPONSE_SHAPE_BINDINGS: dict[str, dict[str, str]] = {
    "timeseries": {
        "uri_module": "meta_data_mcp.ui_resources.shape_timeseries_v1",
        "uri_alias": "TIMESERIES_URI",
        "serializer_fn": "to_json_text",
        "serializer_import": "to_json_text",
        "needs_max_chars": "true",
    },
    "geofeatures": {
        "uri_module": "meta_data_mcp.ui_resources.shape_geofeatures_v1",
        "uri_alias": "GEOFEATURES_URI",
        "serializer_fn": "to_geofeatures_text",
        "serializer_import": "to_geofeatures_text",
        "needs_max_chars": "",
    },
    "records": {
        "uri_module": "meta_data_mcp.ui_resources.shape_records_v1",
        "uri_alias": "RECORDS_URI",
        "serializer_fn": "to_records_text",
        "serializer_import": "to_records_text",
        "needs_max_chars": "",
    },
}
RESPONSE_SHAPE_NONE = "none"


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


class SpecError(ValueError):
    """Raised when a YAML spec is malformed."""


def load_spec(path: Path) -> dict[str, Any]:
    """Load and validate a provider YAML spec."""
    if not path.exists():
        raise SpecError(f"Spec file not found: {path}")
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise SpecError(f"Top-level YAML must be a mapping, got {type(raw).__name__}")

    required_top = ("id", "server_name", "base_url", "description", "tools")
    for key in required_top:
        if key not in raw:
            raise SpecError(f"Missing required top-level key: {key!r}")

    if not isinstance(raw["tools"], list) or not raw["tools"]:
        raise SpecError("'tools' must be a non-empty list")

    if not re.match(r"^[a-z][a-z0-9_]*$", raw["id"]):
        raise SpecError(
            f"id {raw['id']!r} must be lowercase snake_case (matches "
            "/^[a-z][a-z0-9_]*$/)"
        )

    if not isinstance(raw["server_name"], str) or not _SERVER_NAME_RE.match(
        raw["server_name"]
    ):
        raise SpecError(
            f"server_name {raw['server_name']!r} must match "
            f"/{_SERVER_NAME_RE.pattern}/ (alphanumerics, space, underscore, "
            "hyphen, dot)"
        )

    for i, tool in enumerate(raw["tools"]):
        if not isinstance(tool, dict):
            raise SpecError(f"tools[{i}] must be a mapping")
        for key in ("name", "description", "endpoint"):
            if key not in tool:
                raise SpecError(f"tools[{i}] missing required key: {key!r}")
        if not isinstance(tool["endpoint"], str) or not _ENDPOINT_RE.match(
            tool["endpoint"]
        ):
            raise SpecError(
                f"tools[{i}].endpoint {tool['endpoint']!r} must match "
                f"/{_ENDPOINT_RE.pattern}/ — URL-path chars only, no quotes "
                "or whitespace"
            )
        # response_format defaults to 'json'
        tool.setdefault("response_format", "json")
        tool.setdefault("params", [])
        tool.setdefault("response_shape", RESPONSE_SHAPE_NONE)
        if tool["response_format"] not in ("json", "text"):
            raise SpecError(
                f"tools[{i}].response_format must be 'json' or 'text', "
                f"got {tool['response_format']!r}"
            )
        valid_shapes = {RESPONSE_SHAPE_NONE, *RESPONSE_SHAPE_BINDINGS}
        if tool["response_shape"] not in valid_shapes:
            raise SpecError(
                f"tools[{i}].response_shape {tool['response_shape']!r} must be "
                f"one of {sorted(valid_shapes)}"
            )
        if (
            tool["response_shape"] != RESPONSE_SHAPE_NONE
            and tool["response_format"] != "json"
        ):
            raise SpecError(
                f"tools[{i}].response_shape requires response_format='json'; "
                f"text responses can't carry the shape envelope contract"
            )
        for j, p in enumerate(tool["params"]):
            if not isinstance(p, dict):
                raise SpecError(f"tools[{i}].params[{j}] must be a mapping")
            for key in ("name", "type"):
                if key not in p:
                    raise SpecError(
                        f"tools[{i}].params[{j}] missing required key: {key!r}"
                    )
            if not isinstance(p["name"], str) or not _PARAM_NAME_RE.match(p["name"]):
                raise SpecError(
                    f"tools[{i}].params[{j}].name {p['name']!r} must match "
                    f"/{_PARAM_NAME_RE.pattern}/ (lowercase snake_case identifier)"
                )
            if p["type"] not in TYPE_MAP:
                raise SpecError(
                    f"tools[{i}].params[{j}].type {p['type']!r} is not one of "
                    f"{sorted(TYPE_MAP)}"
                )
            p.setdefault("required", False)
            p.setdefault("description", "")

    raw.setdefault("homepage", "")
    raw.setdefault("requires_env", [])
    return raw


# ---------------------------------------------------------------------------
# Helpers used while rendering
# ---------------------------------------------------------------------------


def _kebab_to_snake(name: str) -> str:
    """Convert a kebab-case tool name to a snake_case Python identifier."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower().replace("-", "_"))


def _kebab_to_pascal(name: str) -> str:
    """Convert a kebab-case tool name to a PascalCase identifier."""
    return "".join(part.capitalize() for part in re.split(r"[-_]", name) if part)


def _path_params(endpoint: str) -> list[str]:
    """Return the ordered list of ``{name}`` placeholders in ``endpoint``."""
    return PATH_PARAM_RE.findall(endpoint)


def _py_literal(value: Any) -> str:
    """Return a safe Python literal for a YAML scalar value.

    Strings are emitted with double quotes (matching the existing provider
    style in meta_data_mcp/providers/) when they contain no double quotes;
    otherwise we fall back to Python's ``repr`` which picks the safer
    quoting automatically.
    """
    if isinstance(value, str):
        if '"' in value or "\n" in value:
            return repr(value)
        # Escape backslashes, then wrap in double quotes.
        escaped = value.replace("\\", "\\\\")
        return f'"{escaped}"'
    return repr(value)


# ---------------------------------------------------------------------------
# Rendering — provider module
# ---------------------------------------------------------------------------


def _render_field(p: dict[str, Any]) -> str:
    """Render a single Pydantic ``Field(...)`` line."""
    py_type = TYPE_MAP[p["type"]]
    required = bool(p["required"])
    default = p.get("default", None)
    description = p.get("description", "") or ""

    if required:
        type_anno = py_type
        default_expr = "..."
    else:
        type_anno = f"Optional[{py_type}]"
        if default is None:
            default_expr = "None"
        else:
            default_expr = _py_literal(default)

    return (
        f"    {p['name']}: {type_anno} = Field(\n"
        f"        {default_expr},\n"
        f"        description={_py_literal(description)},\n"
        f"    )"
    )


def _render_params_class(tool: dict[str, Any]) -> str:
    pascal = _kebab_to_pascal(tool["name"]) + "Params"
    params = tool["params"]
    docstring = f'"""Parameters for {tool["name"]}."""'
    if not params:
        return f"class {pascal}(BaseModel):\n    {docstring}\n\n    pass"
    fields = "\n\n".join(_render_field(p) for p in params)
    return f"class {pascal}(BaseModel):\n    {docstring}\n\n{fields}"


def _render_fetch_fn(tool: dict[str, Any], requires_env: list[str]) -> str:
    """Render the synchronous fetch function for one tool."""
    snake = _kebab_to_snake(tool["name"])
    pascal = _kebab_to_pascal(tool["name"]) + "Params"
    endpoint = tool["endpoint"]
    path_params = set(_path_params(endpoint))

    # Build the URL line — substitute path params with f-string interpolation.
    if path_params:
        # Convert "/alerts/{alert_id}" -> f"{BASE_URL}/alerts/{params.alert_id}"
        url_template = endpoint
        for name in path_params:
            url_template = url_template.replace(
                "{" + name + "}", "{params." + name + "}"
            )
        url_line = f'    url = f"{{BASE_URL}}{url_template}"'
        # Strip the f-string prefix's outer braces — we want a real f-string
        # in the generated module, so write the literal source.
        url_line = '    url = f"{BASE_URL}' + url_template + '"'
    else:
        url_line = f'    url = f"{{BASE_URL}}{endpoint}"'
        url_line = '    url = f"{BASE_URL}' + endpoint + '"'

    # Build the query-params dict from non-path params.
    query_assignments: list[str] = []
    for p in tool["params"]:
        if p["name"] in path_params:
            continue
        if p["required"]:
            query_assignments.append(f'    query["{p["name"]}"] = params.{p["name"]}')
        else:
            query_assignments.append(
                f"    if params.{p['name']} is not None:\n"
                f'        query["{p["name"]}"] = params.{p["name"]}'
            )

    # Env-var injection (e.g. an api_key query parameter).
    env_lines: list[str] = []
    env_requirements: list[tuple[str, str]]
    if isinstance(requires_env, dict):
        env_requirements = list(requires_env.items())
    else:
        legacy_env_vars = list(requires_env)
        if len(legacy_env_vars) > 1:
            raise ValueError(
                "requires_env with multiple entries must be a mapping of "
                "env var name to query parameter name"
            )
        env_requirements = [(env_var, "api_key") for env_var in legacy_env_vars]

    seen_env_query_params: set[str] = set()
    for env_var, query_param in env_requirements:
        if query_param in seen_env_query_params:
            raise ValueError(
                f"Duplicate env-backed query parameter generated: {query_param!r}"
            )
        seen_env_query_params.add(query_param)
        env_lines.append(
            f'    query["{query_param}"] = _require_key({_py_literal(env_var)})'
        )

    body_parts = [url_line, "    query: dict = {}"]
    body_parts.extend(env_lines)
    body_parts.extend(query_assignments)
    body_parts.append(
        "    response = http_get(url, params=query or None, provider=PROVIDER_ID)"
    )

    if tool["response_format"] == "json":
        body_parts.append("    return response.json()")
        return_type = "Any"
    else:
        body_parts.append("    return response.text")
        return_type = "str"

    body = "\n".join(body_parts)
    return (
        f"def fetch_{snake}(params: {pascal}) -> {return_type}:\n"
        f'    """Fetch data for the {tool["name"]} tool."""\n'
        f"{body}"
    )


def _render_handler(tool: dict[str, Any]) -> str:
    snake = _kebab_to_snake(tool["name"])
    pascal = _kebab_to_pascal(tool["name"]) + "Params"
    shape = tool.get("response_shape", RESPONSE_SHAPE_NONE)

    adapter_lines: list[str] = []
    if tool["response_format"] == "json":
        data_line = f"        data = fetch_{snake}(params)"
        if shape != RESPONSE_SHAPE_NONE:
            # Provider-specific shape mapping varies too much to auto-generate;
            # surface a TODO so the author writes the adapter explicitly.
            adapter_lines.append(
                f"        # TODO: write a _{snake}_to_shape_payload(data) adapter\n"
                f"        # that maps the raw API response onto the "
                f"ui://meta-data-mcp/shape/{shape}/v1 contract."
            )
            binding = RESPONSE_SHAPE_BINDINGS[shape]
            serializer = binding["serializer_fn"]
            if binding["needs_max_chars"]:
                serializer_call = f"{serializer}(data, max_chars=MAX_RESPONSE_CHARS)"
            else:
                serializer_call = f"{serializer}(data)"
            payload_line = (
                f"        return [types.TextContent("
                f'type="text", text={serializer_call})]'
            )
        else:
            payload_line = (
                "        return [types.TextContent("
                'type="text", text=serialize_for_llm(data))]'
            )
    else:
        payload_line = (
            '        return [types.TextContent(type="text", text=(data or "")[:20000])]'
        )
        data_line = f"        data = fetch_{snake}(params)"

    body_lines = [data_line]
    body_lines.extend(adapter_lines)
    body_lines.append(payload_line)
    body = "\n".join(body_lines)

    return (
        f"async def handle_{snake}(\n"
        f"    arguments: dict[str, Any] | None = None,\n"
        f") -> Sequence[types.TextContent]:\n"
        f'    """Handle the {tool["name"]} tool call."""\n'
        f"    try:\n"
        f"        params = {pascal}(**(arguments or {{}}))\n"
        f"{body}\n"
        f"    except Exception as e:\n"
        f'        log.error(f"Error handling {tool["name"]}: {{e}}")\n'
        f"        raise"
    )


def _render_registration(tool: dict[str, Any]) -> str:
    snake = _kebab_to_snake(tool["name"])
    pascal = _kebab_to_pascal(tool["name"]) + "Params"
    shape = tool.get("response_shape", RESPONSE_SHAPE_NONE)
    meta_line = ""
    if shape != RESPONSE_SHAPE_NONE:
        uri_alias = RESPONSE_SHAPE_BINDINGS[shape]["uri_alias"]
        # The SDK's Tool model doesn't enable populate_by_name, so passing
        # ``meta=`` silently lands in extras and never reaches the wire as
        # ``_meta``. Always emit the alias keyword.
        meta_line = f'        _meta={{"ui": {{"resourceUri": {uri_alias}}}}},\n'
    return (
        "TOOLS.append(\n"
        "    types.Tool(\n"
        f"        name={_py_literal(tool['name'])},\n"
        f"        description={_py_literal(tool['description'])},\n"
        f"        inputSchema={pascal}.model_json_schema(),\n"
        f"{meta_line}"
        "    )\n"
        ")\n"
        f"TOOLS_HANDLERS[{_py_literal(tool['name'])}] = handle_{snake}"
    )


def _render_tool_block(tool: dict[str, Any], requires_env: list[str]) -> str:
    banner = f"###################\n# {tool['name']}\n###################"
    return "\n\n\n".join(
        [
            banner,
            _render_params_class(tool),
            _render_fetch_fn(tool, requires_env),
            _render_handler(tool),
            _render_registration(tool),
        ]
    )


def _render_env_helper(requires_env: list[str]) -> str:
    if not requires_env:
        return ""
    # No inline helper needed — generated code calls require_env_key() imported
    # from meta_data_mcp.provider_config, which keeps `os` out of generated modules.
    return (
        "def _require_key(var_name: str) -> str:\n"
        '    """Return the environment variable value via the trusted helper."""\n'
        "    return require_env_key(var_name)\n"
    )


def render_provider(spec: dict[str, Any]) -> str:
    """Return the full Python source for the provider module."""
    homepage = spec.get("homepage", "")
    description = (spec["description"] or "").strip()
    requires_env: list[str] = list(spec.get("requires_env") or [])

    docstring_lines = [f'"""{spec["server_name"]} provider.', ""]
    if description:
        docstring_lines.append(description)
        docstring_lines.append("")
    if homepage:
        docstring_lines.append(f"Homepage: {homepage}")
        docstring_lines.append("")
    if requires_env:
        docstring_lines.append("Required environment variables:")
        for v in requires_env:
            docstring_lines.append(f"- {v}")
        docstring_lines.append("")
    docstring_lines.append("Auto-generated by tools/generate_provider.py.")
    docstring_lines.append('"""')
    docstring = "\n".join(docstring_lines)

    # Note: we deliberately do NOT emit `from __future__ import annotations`
    # because Pydantic V2's model_json_schema() needs runtime-resolvable
    # types when the model is constructed at import time (the same reason
    # the canonical us_nasa.py provider avoids the future import).

    # MCP Apps bindings (Phase 6a) — collect every distinct shape used by
    # any tool so we emit the right URI imports + size-bounded serializers.
    used_shapes: list[str] = []
    for tool in spec["tools"]:
        shape = tool.get("response_shape", RESPONSE_SHAPE_NONE)
        if shape != RESPONSE_SHAPE_NONE and shape not in used_shapes:
            used_shapes.append(shape)
    has_unbound_json_tool = any(
        t.get("response_shape", RESPONSE_SHAPE_NONE) == RESPONSE_SHAPE_NONE
        and t.get("response_format", "json") == "json"
        for t in spec["tools"]
    )
    needs_max_chars = any(
        RESPONSE_SHAPE_BINDINGS[s].get("needs_max_chars") for s in used_shapes
    )

    import_lines = [
        "import logging",
    ]
    if requires_env:
        import_lines.append("from meta_data_mcp.provider_config import require_env_key")
    import_lines.extend(
        [
            "from typing import Any, List, Optional, Sequence",
            "",
            "import mcp.types as types",
            "from pydantic import BaseModel, Field",
            "",
        ]
    )

    # Shape-URI imports. One ``from ... import URI as <ALIAS>`` per shape,
    # in deterministic alphabetical order so the diff stays stable.
    for shape in sorted(used_shapes):
        binding = RESPONSE_SHAPE_BINDINGS[shape]
        import_lines.append(
            f"from {binding['uri_module']} import URI as {binding['uri_alias']}"
        )
    if used_shapes:
        import_lines.append("")

    util_imports = ["create_mcp_server", "http_get", "run_server"]
    if has_unbound_json_tool:
        util_imports.append("serialize_for_llm")
    if needs_max_chars:
        util_imports.append("MAX_RESPONSE_CHARS")
    for shape in sorted(used_shapes):
        util_imports.append(RESPONSE_SHAPE_BINDINGS[shape]["serializer_import"])
    # Dedupe while preserving order so MAX_RESPONSE_CHARS doesn't sort wrong.
    seen: set[str] = set()
    util_imports = [n for n in util_imports if not (n in seen or seen.add(n))]
    util_imports = sorted(util_imports)
    import_lines.append("from meta_data_mcp.utils import (")
    for name in util_imports:
        import_lines.append(f"    {name},")
    import_lines.append(")")

    header = (
        "log = logging.getLogger(__name__)\n\n"
        f"PROVIDER_ID = {_py_literal(spec['server_name'])}\n"
        f"BASE_URL = {_py_literal(spec['base_url'])}\n\n"
        "RESOURCES: List[Any] = []\n"
        "RESOURCES_HANDLERS: dict[str, Any] = {}\n"
        "TOOLS: List[types.Tool] = []\n"
        "TOOLS_HANDLERS: dict[str, Any] = {}"
    )

    env_helper = _render_env_helper(requires_env)
    tool_blocks = "\n\n\n".join(
        _render_tool_block(t, requires_env) for t in spec["tools"]
    )

    main_block = (
        'async def main(transport: str = "stdio", port: int = 8000, '
        'host: str = "127.0.0.1"):\n'
        "    server = create_mcp_server(\n"
        f"        {_py_literal(spec['server_name'])},\n"
        "        RESOURCES,\n"
        "        RESOURCES_HANDLERS,\n"
        "        TOOLS,\n"
        "        TOOLS_HANDLERS,\n"
        "    )\n"
        "    await run_server(server, transport, port, host)\n"
        "\n\n"
        'if __name__ == "__main__":\n'
        "    import anyio\n"
        "\n"
        "    anyio.run(main)"
    )

    sections = [
        docstring,
        "\n".join(import_lines),
        header,
    ]
    if env_helper:
        sections.append(env_helper)
    sections.extend([tool_blocks, main_block])
    return "\n\n\n".join(sections) + "\n"


# ---------------------------------------------------------------------------
# Rendering — test module
# ---------------------------------------------------------------------------


def _render_test_for_tool(tool: dict[str, Any], provider_id: str) -> str:
    snake = _kebab_to_snake(tool["name"])
    handler = f"handle_{snake}"

    # Build a minimal arguments dict with required params filled by placeholders.
    sample_args: dict[str, Any] = {}
    for p in tool["params"]:
        if not p["required"]:
            continue
        py_type = TYPE_MAP[p["type"]]
        sample_args[p["name"]] = {
            "str": "test",
            "int": 1,
            "float": 1.0,
            "bool": True,
        }[py_type]
    args_repr = _py_literal(sample_args) if sample_args else "{}"

    if tool["response_format"] == "json":
        mock_payload = '{"ok": true, "marker": "GENERATED_TEST_MARKER"}'
        mock_assignment = (
            "        mock_get.return_value.json.return_value = "
            '{"ok": True, "marker": "GENERATED_TEST_MARKER"}'
        )
        assertion = '        assert "GENERATED_TEST_MARKER" in result[0].text'
    else:
        mock_payload = "GENERATED_TEST_MARKER"
        mock_assignment = '        mock_get.return_value.text = "GENERATED_TEST_MARKER"'
        assertion = '        assert "GENERATED_TEST_MARKER" in result[0].text'

    _ = mock_payload  # currently unused outside of mock_assignment

    success = (
        f"@pytest.mark.anyio\n"
        f"async def test_{snake}_success():\n"
        f'    """Smoke test: {tool["name"]} returns success payload."""\n'
        f'    with patch("httpx.get") as mock_get:\n'
        f"{mock_assignment}\n"
        f"        mock_get.return_value.raise_for_status = Mock()\n"
        f"        mock_get.return_value.status_code = 200\n"
        f"        result = await {handler}({args_repr})\n"
        f"        assert len(result) == 1\n"
        f"{assertion}"
    )

    error = (
        f"@pytest.mark.anyio\n"
        f"async def test_{snake}_http_error():\n"
        f'    """{tool["name"]} propagates httpx errors."""\n'
        f'    with patch("httpx.get") as mock_get:\n'
        f'        mock_get.side_effect = httpx.HTTPError("Network down")\n'
        f"        with pytest.raises(httpx.HTTPError):\n"
        f"            await {handler}({args_repr})"
    )

    return success + "\n\n\n" + error


def render_tests(spec: dict[str, Any]) -> str:
    provider_id = spec["id"]
    handler_names = [f"handle_{_kebab_to_snake(t['name'])}" for t in spec["tools"]]

    import_block = (
        '"""Auto-generated tests for the '
        f"{spec['server_name']} provider.\n\n"
        "Generated by tools/generate_provider.py — feel free to extend with\n"
        "tool-specific assertions once the provider stabilizes.\n"
        '"""\n\n'
        "from unittest.mock import Mock, patch\n\n"
        "import httpx\n"
        "import pytest\n\n"
        f"from meta_data_mcp.providers.{provider_id} import (\n"
        + "".join(f"    {n},\n" for n in handler_names)
        + ")\n\n\n"
        "@pytest.fixture\n"
        "def anyio_backend():\n"
        '    return "asyncio"\n'
    )

    bodies = [_render_test_for_tool(t, provider_id) for t in spec["tools"]]
    return import_block + "\n\n" + "\n\n\n".join(bodies) + "\n"


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def _write_file(path: Path, contents: str, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(
            f"refusing to overwrite existing file: {path} (use --force to overwrite)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)


def generate(spec_path: Path, *, dry_run: bool, force: bool) -> dict[str, str]:
    """Render outputs and (optionally) write them. Returns the rendered text."""
    spec = load_spec(spec_path)
    provider_src = render_provider(spec)
    test_src = render_tests(spec)

    provider_path = PROVIDERS_DIR / f"{spec['id']}.py"
    test_path = TESTS_DIR / f"test_{spec['id']}.py"

    if dry_run:
        sys.stdout.write(f"# ---- WOULD WRITE: {provider_path} ----\n")
        sys.stdout.write(provider_src)
        sys.stdout.write(f"\n# ---- WOULD WRITE: {test_path} ----\n")
        sys.stdout.write(test_src)
    else:
        _write_file(provider_path, provider_src, force=force)
        _write_file(test_path, test_src, force=force)
        sys.stderr.write(f"wrote {provider_path}\n")
        sys.stderr.write(f"wrote {test_path}\n")

    return {"provider": provider_src, "tests": test_src}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an opendata-mcp provider module + tests from a YAML spec.",
    )
    parser.add_argument("spec", type=Path, help="Path to the YAML spec file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated code to stdout instead of writing files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing provider/test files if they already exist",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        generate(args.spec, dry_run=args.dry_run, force=args.force)
    except SpecError as e:
        sys.stderr.write(f"error: {e}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
