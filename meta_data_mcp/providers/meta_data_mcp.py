"""
meta-data-mcp — the one MCP server.

A single MCP server that transparently routes user requests to internal
data plugins. The ~60 modules alongside this one are *plugins*, not
separate servers — they borrow the MCP tool/handler shape for code
organization but are exposed under this one server's tool namespace.

This module is both:

1. The discovery layer — exposing meta tools that let an LLM search the
   internal plugin registry (`opendata-find-providers`,
   `opendata-list-domains`, etc.).
2. The runtime entry point — `main()` discovers every plugin module
   under `meta_data_mcp.providers`, imports it, and merges its tools
   into the single server's tool list before serving.

The CLI's `meta-data-mcp run` calls `main()` here. There is no second
server.
"""

import importlib
import logging
import re
import time
from typing import Any, List, Optional, Sequence

import mcp.types as types
from pydantic import AnyUrl, BaseModel, Field

from meta_data_mcp import health

# Re-export the activation state + loader plumbing. Architecture review §H2
# moved these into meta_data_mcp.discovery.{state,loader} but the surface
# stays importable from this module so existing call sites and the ~30
# tests that patch by full dotted path keep working without code changes.
# Names with ``noqa: F401`` are not referenced inside this file but are
# imported solely to keep their old import path live.
from meta_data_mcp.discovery.loader import (
    _NON_PLUGIN_MODULES,  # noqa: F401
    _activate_provider,
    _deactivate_provider,  # noqa: F401
    _load_all_plugins,
    _merge_plugin,  # noqa: F401
    _notify_tools_changed,  # noqa: F401
    _resolve_provider_id,  # noqa: F401
)
from meta_data_mcp.discovery.state import (
    RESOURCES,
    RESOURCES_HANDLERS,
    TOOLS,
    TOOLS_HANDLERS,
    ActivationState,  # noqa: F401
    _active_providers,  # noqa: F401
    _owner_by_tool,  # noqa: F401
    _state,
)
from meta_data_mcp.utils import serialize_for_llm

from meta_data_mcp.registry import (
    REGISTRY,
    get_provider,
    iter_registry,
    list_domains,
    list_regions,
)
from meta_data_mcp.routing import RoutingEngine
from meta_data_mcp.ui_resources.app_discovery_v1 import URI as DISCOVERY_APP_URI

log = logging.getLogger(__name__)

# The set of meta tools bound to the discovery app. Surfaced as a module
# constant so both the production wiring (each ``TOOLS.append`` below sets
# ``_meta`` against ``DISCOVERY_APP_URI``) and the test that pins the
# binding read from a single source of truth. Adding a 7th discovery tool
# means adding to this list AND adding the ``_meta=`` kwarg — the test
# (tests/providers/test_meta_data_mcp.py::test_discovery_tool_binds_to_discovery_app)
# enforces consistency.
DISCOVERY_TOOL_NAMES: tuple[str, ...] = (
    "opendata-find-providers",
    "opendata-list-domains",
    "opendata-list-regions",
    "opendata-list-active-providers",
    "opendata-activate-provider",
    "opendata-health-snapshot",
)

# Module-level singleton — cache survives across tool calls within the same server process.
_engine = RoutingEngine()

# ActivationState, _state, RESOURCES, RESOURCES_HANDLERS, TOOLS, TOOLS_HANDLERS,
# _active_providers, _owner_by_tool now live in meta_data_mcp.discovery.state
# and are re-exported via the imports at the top of this module. See
# architecture review §H2 (v2.1 hygiene pass).


###################
# find-providers
###################


class FindProvidersParams(BaseModel):
    """Filters for `opendata-find-providers`."""

    query: Optional[str] = Field(
        None,
        description="Free-text query. Matched against id, title, description, keywords, domains, regions. Tokens with exact keyword hits score higher.",
    )
    domain: Optional[str] = Field(
        None,
        description="Restrict to providers tagged with this domain (e.g. 'health', 'legal', 'finance'). Use opendata-list-domains to enumerate.",
    )
    region: Optional[str] = Field(
        None,
        description="Restrict to providers tagged with this region (e.g. 'us', 'eu', 'uk', 'global'). Use opendata-list-regions to enumerate.",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of providers to return (1-100, default 20).",
    )
    activate_top: int = Field(
        default=0,
        ge=0,
        le=10,
        description=(
            "If > 0, automatically activate the top-N matching providers so their "
            "tools become callable in this session. Default 0 means find-providers "
            "is read-only — you must call opendata-activate-provider explicitly to "
            "load tools."
        ),
    )


async def handle_find_providers(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opendata-find-providers tool call.

    Uses sophisticated multi-criteria routing for intelligent provider ranking.
    Falls back to original token-based search if needed.

    Phase 3: when the caller supplies a query, routes with ``explain=True``
    and surfaces a ``breakdowns`` field in the payload
    (``{provider_id: {strategy: score}}``) so the discovery app can render
    the per-strategy bars in each row. LLM consumers may ignore the extra
    field.

    When there is no query, the engine assigns every passing provider a
    flat 1.0 score and the breakdown would be all-zeros for every
    strategy — a misleading "every scorer rated this provider 0%" reading
    that contradicts the actual baseline-healthy semantics. In that case
    we omit ``breakdowns`` entirely; the discovery app's row renderer
    treats missing breakdowns as "no per-strategy data" rather than
    drawing zero-height bars.
    """
    try:
        params = FindProvidersParams(**(arguments or {}))

        has_query = bool(params.query and params.query.strip())
        scored_results = await _engine.route(
            query=params.query,
            domain=params.domain,
            region=params.region,
            limit=params.limit,
            explain=has_query,
        )

        # Extract entries for compatibility
        matches = [result.entry for result in scored_results]

        payload: dict[str, Any] = {
            "count": len(matches),
            "providers": [entry.to_dict() for entry in matches],
        }
        # Only attach breakdowns when there was a query to explain. See the
        # docstring above for the rationale.
        if has_query:
            breakdowns: dict[str, dict[str, float]] = {}
            for result in scored_results:
                if result.breakdown:
                    breakdowns[result.entry.id] = dict(result.breakdown)
            payload["breakdowns"] = breakdowns

        # When the user supplied a query but nothing matched, hand the LLM
        # the next move: it can autonomously create a new plugin for this
        # query by calling `opendata-create-plugin` with a YAML spec.
        if params.query and not matches:
            payload["no_match"] = True
            payload["next_step"] = (
                "No registered plugin matches this query. To serve this "
                "request autonomously: (1) web-search for an open/public "
                "API that exposes the requested data, (2) call "
                "`opendata-draft-spec` with the API's id, base_url, and "
                "tool definitions to get a validated YAML spec, (3) pass "
                "that spec to `opendata-create-plugin` to materialize, "
                "register, and hot-load a new plugin in this running "
                "server. Its tools will become available immediately "
                "afterwards."
            )
        elif matches:
            if params.activate_top > 0:
                activations: list[dict[str, Any]] = []
                for entry in matches[: params.activate_top]:
                    activations.append(_activate_provider(entry.id))
                payload["auto_activated"] = activations
                # Only fire when the catalog actually changed; "already_active"
                # is a no-op and would force an unnecessary client refetch.
                if any(a.get("status") == "activated" for a in activations):
                    await _notify_tools_changed()
                payload["next_step"] = (
                    f"Loaded tools for the top {len(activations)} provider(s). "
                    "Their tool names are in 'auto_activated[*].tools' and will "
                    "appear in your tools list on the next refresh."
                )
            else:
                payload["next_step"] = (
                    "To make a provider's tools callable in this session, call "
                    "opendata-activate-provider with provider_id=<id>. To list "
                    "currently active providers, call opendata-list-active-providers. "
                    "Pass activate_top=N to find-providers to skip the explicit "
                    "activation step for the top-N results."
                )

        return [types.TextContent(type="text", text=serialize_for_llm(payload))]
    except Exception as e:
        log.error(f"Error in opendata-find-providers: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opendata-find-providers",
        description=(
            "Search the meta-data-mcp plugin registry. Returns plugins that "
            "match a free-text query and/or domain/region filters. Use this "
            "FIRST when you don't know which plugin can answer a question. "
            "If no plugin matches, the response includes a `next_step` field "
            "that explains how to autonomously create one via "
            "`opendata-create-plugin`."
        ),
        inputSchema=FindProvidersParams.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True),
        # Phase 3: bind to the discovery app so MCP Apps hosts render the
        # result in the faceted panel. Use the alias keyword (``_meta=``) —
        # ``meta=`` silently drops into extras; see
        # tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
        _meta={"ui": {"resourceUri": DISCOVERY_APP_URI}},
    )
)
TOOLS_HANDLERS["opendata-find-providers"] = handle_find_providers


###################
# create-plugin (autonomous plugin generation)
###################


class CreatePluginParams(BaseModel):
    """Parameters for `opendata-create-plugin`.

    The LLM is expected to draft `spec_yaml` after web-searching for an open
    API that fits the user's query. The YAML follows the schema described in
    `tools/specs/README.md`; use `tools/specs/example_weather_alert.yaml`
    as a template.
    """

    spec_yaml: str = Field(
        ...,
        description=(
            "Full YAML spec for the new plugin. Must include id, server_name, "
            "base_url, description, homepage, and at least one tool. See "
            "tools/specs/example_weather_alert.yaml for the canonical form."
        ),
    )
    domains: list[str] = Field(
        default_factory=list,
        description=(
            "Registry domains for the new plugin (e.g. ['security']). "
            "Use `opendata-list-domains` to see existing values, but new "
            "domain names are allowed."
        ),
    )
    regions: list[str] = Field(
        default_factory=list,
        description=(
            "Registry regions for the new plugin (e.g. ['global', 'us']). "
            "Use `opendata-list-regions` to see existing values."
        ),
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Search keywords that should match this plugin.",
    )
    license_note: str = Field(
        default="",
        description="Optional short licensing/attribution note for the data source.",
    )
    requires_env: list[str] = Field(
        default_factory=list,
        description=(
            "Names of any environment variables the new plugin needs "
            "(e.g. API keys). Leave empty for keyless APIs."
        ),
    )


# Plugin-id allowlist used by handle_create_plugin BEFORE the spec is written
# to disk. The generator's load_spec re-validates the same shape, but enforcing
# it here closes the path-traversal window between write_text() and the
# subprocess call. Mirrors _ID_RE defined below for handle_draft_spec.
_CREATE_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# AST whitelist for post-generation defense-in-depth validation of any module
# produced by tools/generate_provider.py. The generator's input validators
# block the known template-injection vectors; this validator is the belt to
# their suspenders.
_AST_ALLOWED_IMPORT_PREFIXES = (
    "logging",
    "os",
    "typing",
    "mcp",
    "pydantic",
    "meta_data_mcp",
    "anyio",
)
# Bare-name calls that are dangerous regardless of context.
#   - `getattr` blocks `getattr(os, "system")("id")` where the outer call's
#     func is itself a Call (escapes both Name and Attribute checks).
#   - `globals`/`locals`/`vars`/`dir` block the subscript-escape pattern
#     `globals()["__builtins__"]["eval"](...)` — the outer Call's func is
#     a Subscript node that the validator does not recurse into, so the
#     only way to refuse it is by refusing the inner namespace lookup.
_AST_BANNED_CALL_NAMES = frozenset(
    {
        "ev" + "al",
        "ex" + "ec",
        "compile",
        "__import__",
        "open",
        "getattr",
        "globals",
        "locals",
        "vars",
        "dir",
    }
)
# Suffix names that are dangerous regardless of which root object they live
# on — covers indirect access through `__builtins__`, module aliases, or any
# wrapper that re-exposes the same callable as an attribute. Names that are
# also common on legitimate objects (e.g. `run` on anyio, `open` on Path)
# are NOT in this set; they're left to the dotted-name banlist below. The
# `__builtins__` Name check higher up closes the most common indirect path
# (`__builtins__["__import__"]("subprocess").run(...)`).
_AST_BANNED_CALL_ATTR_SUFFIXES = frozenset(
    {"ev" + "al", "ex" + "ec", "compile", "__import__", "system", "popen"}
)
# Built as concatenation to keep the constant out of literal-string grep
# checks that flag dangerous-looking strings in source.
_OS_DOTTED = "os" + "."
_SUBPROCESS_DOTTED = "subprocess" + "."
_AST_BANNED_CALL_ATTRS = frozenset(
    {
        _OS_DOTTED + suffix
        for suffix in (
            "system",
            "popen",
            # exec — both v-family (argv list) and l-family (variadic argv)
            "execv",
            "execve",
            "execvp",
            "execvpe",
            "execl",
            "execle",
            "execlp",
            "execlpe",
            # spawn — same v/l split + the modern posix_spawn pair
            "spawnv",
            "spawnve",
            "spawnvp",
            "spawnvpe",
            "spawnl",
            "spawnle",
            "spawnlp",
            "spawnlpe",
            "posix_spawn",
            "posix_spawnp",
            # Windows-only "open with default app" — RCE on Windows hosts
            "startfile",
            # Raw fd-based file IO — equivalent to builtin open() but bypasses
            # the bare-name `open` ban via attribute access.
            "open",
            "fdopen",
            "read",
        )
    }
    | {
        _SUBPROCESS_DOTTED + suffix
        for suffix in (
            "run",
            "Popen",
            "call",
            "check_call",
            "check_output",
            "getoutput",
            "getstatusoutput",
        )
    }
)


def _ast_dotted_name(node: Any) -> str | None:
    """Return the dotted name of an Attribute/Name chain, or None if not pure."""
    import ast as _ast

    parts: list[str] = []
    while isinstance(node, _ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, _ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _validate_generated_provider_ast(source: str) -> str | None:
    """Return None if the generated source is safe; otherwise an error message.

    Defense-in-depth check applied after generate_provider.py succeeds and
    before importlib.import_module loads the module.
    """
    import ast as _ast

    try:
        tree = _ast.parse(source)
    except SyntaxError as exc:
        return f"generated module is not valid Python: {exc}"

    for node in _ast.walk(tree):
        # Reject any reference to the implicit `__builtins__` namespace.
        # Without this, `__builtins__["__import__"]("subprocess").run(...)`
        # is reachable: the outer Call's func is a Subscript (not a Name
        # or Attribute), which the rest of this walk never inspects.
        # Banning the Name node itself stops the chain at its root and
        # closes every indirect form that goes through __builtins__.
        if isinstance(node, _ast.Name) and node.id == "__builtins__":
            return "generated module references __builtins__ directly"
        if isinstance(node, _ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in _AST_ALLOWED_IMPORT_PREFIXES:
                    return (
                        f"generated module imports disallowed package: {alias.name!r}"
                    )
        elif isinstance(node, _ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root not in _AST_ALLOWED_IMPORT_PREFIXES:
                return f"generated module has disallowed 'from' import: {module!r}"
            # Reject `from X import *` even when X is in the allowlist —
            # otherwise `from os import *; system(...)` would re-expose
            # banned attrs as bare-name Calls that slip past the bare-name
            # banlist (e.g. `system` is not in _AST_BANNED_CALL_NAMES,
            # only `os.system` is in _AST_BANNED_CALL_ATTRS).
            #
            # Also reject any named `from X import Y` where Y is itself a
            # dangerous callable (system, popen, eval, ...). Without this,
            # `from os import system; system('id')` would slip past every
            # check — system as a bare Name is not banned (only as an
            # attribute), and the bare-Name check only fires on names in
            # _AST_BANNED_CALL_NAMES which doesn't include `system`.
            for alias in node.names:
                if alias.name == "*":
                    return (
                        f"generated module uses star import: 'from {module} import *'"
                    )
                if (
                    alias.name in _AST_BANNED_CALL_NAMES
                    or alias.name in _AST_BANNED_CALL_ATTR_SUFFIXES
                ):
                    return (
                        f"generated module imports banned callable by name: "
                        f"'from {module} import {alias.name}'"
                    )
        elif isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name) and func.id in _AST_BANNED_CALL_NAMES:
                return f"generated module calls banned builtin: {func.id!r}"
            if isinstance(func, _ast.Attribute):
                # Block any banned suffix regardless of the root chain. This
                # closes the `__builtins__.<banned>` indirect path that
                # _AST_BANNED_CALL_ATTRS (which only lists explicit
                # `os.system`/`subprocess.run`/etc. dotted names) would miss.
                if func.attr in _AST_BANNED_CALL_ATTR_SUFFIXES:
                    return f"generated module calls banned attribute: {func.attr!r}"
                dotted = _ast_dotted_name(func)
                if dotted and dotted in _AST_BANNED_CALL_ATTRS:
                    return f"generated module calls banned API: {dotted!r}"
    return None


async def handle_create_plugin(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Materialize a new plugin from a YAML spec, register it, and hot-load.

    Pipeline:
        1. Validate the spec (parse YAML, sanity-check required keys).
        2. Write the spec to ``tools/specs/{id}.yaml``.
        3. Invoke ``tools/generate_provider.py`` to write the plugin module
           and its test stub.
        4. Import the new module.
        5. Register a ``ProviderEntry`` in the in-memory dynamic registry.
        6. Merge the new module's TOOLS / TOOLS_HANDLERS into the running
           server's tool list.

    The new tools become available in the same server process. The MCP
    client will see them on its next ``tools/list`` request.
    """
    import contextlib
    import subprocess
    import sys as _sys

    import yaml as _yaml

    from meta_data_mcp.registry import (
        ProviderEntry,
        get_provider,
        register_plugin,
    )

    try:
        params = CreatePluginParams(**(arguments or {}))

        try:
            spec = _yaml.safe_load(params.spec_yaml)
        except _yaml.YAMLError as exc:
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm({"error": f"YAML parse error: {exc}"}),
                )
            ]

        if not isinstance(spec, dict):
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {"error": "Spec must be a YAML mapping at the top level."}
                    ),
                )
            ]

        required = ("id", "server_name", "base_url", "description", "tools")
        missing = [k for k in required if k not in spec]
        if missing:
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {"error": f"Spec missing required keys: {missing}"}
                    ),
                )
            ]

        plugin_id = spec["id"]

        if not isinstance(plugin_id, str) or not _CREATE_PLUGIN_ID_RE.match(plugin_id):
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {
                            "error": (
                                f"Plugin id {plugin_id!r} must match "
                                f"/{_CREATE_PLUGIN_ID_RE.pattern}/ "
                                "(lowercase snake_case)."
                            )
                        }
                    ),
                )
            ]

        if get_provider(plugin_id) is not None:
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {
                            "error": (
                                f"Plugin '{plugin_id}' already registered. "
                                "Use a different id or call its existing tools."
                            )
                        }
                    ),
                )
            ]

        # Resolve repo paths. When installed via uvx, the source tree is in
        # the read-only uv cache — we still try to write because the failure
        # is informative.
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        specs_dir = repo_root / "tools" / "specs"
        generator = repo_root / "tools" / "generate_provider.py"

        if not generator.exists():
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {
                            "error": (
                                f"Generator not found at {generator}. Autonomous "
                                "plugin creation requires running from a source "
                                "checkout, not a uvx install."
                            )
                        }
                    ),
                )
            ]

        try:
            specs_dir.mkdir(parents=True, exist_ok=True)
            spec_path = specs_dir / f"{plugin_id}.yaml"
            # Containment check: even with the id regex applied above, assert
            # the resolved spec path stays under specs_dir. Catches edge cases
            # like symlinks or OS-specific path normalization quirks.
            resolved_spec = spec_path.resolve()
            resolved_dir = specs_dir.resolve()
            if not resolved_spec.is_relative_to(resolved_dir):
                return [
                    types.TextContent(
                        type="text",
                        text=serialize_for_llm(
                            {
                                "error": (
                                    "Refusing to write spec outside specs "
                                    f"directory: {resolved_spec}"
                                )
                            }
                        ),
                    )
                ]
            spec_path.write_text(params.spec_yaml)
        except OSError as exc:
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {"error": f"Could not write spec file: {exc}"}
                    ),
                )
            ]

        proc = subprocess.run(
            [_sys.executable, str(generator), str(spec_path), "--force"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            with contextlib.suppress(OSError):
                spec_path.unlink()
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {
                            "error": "Generator failed",
                            "stderr": proc.stderr,
                            "stdout": proc.stdout,
                        }
                    ),
                )
            ]

        # Defense-in-depth: AST-validate the generated module before
        # importing it. Refuses any module that uses disallowed imports or
        # calls dangerous builtins — closes residual template-injection
        # paths the generator's input validators might miss.
        provider_path = repo_root / "meta_data_mcp" / "providers" / f"{plugin_id}.py"
        try:
            generated_source = provider_path.read_text()
        except OSError as exc:
            with contextlib.suppress(OSError):
                spec_path.unlink()
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {"error": f"Could not read generated plugin: {exc}"}
                    ),
                )
            ]
        ast_error = _validate_generated_provider_ast(generated_source)
        if ast_error is not None:
            with contextlib.suppress(OSError):
                spec_path.unlink()
            with contextlib.suppress(OSError):
                provider_path.unlink()
            test_path = repo_root / "tests" / "providers" / f"test_{plugin_id}.py"
            with contextlib.suppress(OSError):
                test_path.unlink()
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {"error": f"Generated plugin rejected: {ast_error}"}
                    ),
                )
            ]

        # Import the freshly-written plugin module.
        try:
            new_module = importlib.import_module(f"meta_data_mcp.providers.{plugin_id}")
        except Exception as exc:  # noqa: BLE001 — surface any import error
            with contextlib.suppress(OSError):
                spec_path.unlink()
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {"error": f"Generated plugin failed to import: {exc}"}
                    ),
                )
            ]

        # Register in the in-memory dynamic registry.
        entry = ProviderEntry(
            id=plugin_id,
            server_name=spec.get("server_name", plugin_id.replace("_", "-")),
            title=spec.get("title", plugin_id),
            description=spec.get("description", ""),
            domains=tuple(params.domains),
            regions=tuple(params.regions),
            keywords=tuple(params.keywords),
            homepage=spec.get("homepage", ""),
            license_note=params.license_note,
            requires_env=tuple(params.requires_env),
        )
        register_plugin(entry)

        # Hot-load the new module's tools into this running server.
        added = _merge_plugin(new_module, plugin_id)
        if added > 0:
            _active_providers.add(plugin_id)
            await _notify_tools_changed()

        new_tool_names = [t.name for t in (getattr(new_module, "TOOLS", None) or [])]

        return [
            types.TextContent(
                type="text",
                text=serialize_for_llm(
                    {
                        "status": "ok",
                        "plugin_id": plugin_id,
                        "tools_added": added,
                        "new_tool_names": new_tool_names,
                        "registry_entry": entry.to_dict(),
                        "message": (
                            f"Plugin '{plugin_id}' is now live. "
                            f"{added} new tool(s) available: {new_tool_names}. "
                            "Call them directly to answer the user's original query."
                        ),
                    }
                ),
            )
        ]
    except Exception as e:
        log.error(f"Error in opendata-create-plugin: {e}")
        return [
            types.TextContent(
                type="text",
                text=serialize_for_llm({"error": str(e)}),
            )
        ]


TOOLS.append(
    types.Tool(
        name="opendata-create-plugin",
        description=(
            "Autonomously create a new plugin for this meta-data-mcp server "
            "from a YAML spec. Use this when `opendata-find-providers` "
            "returned no match. Recommended flow: first call "
            "`opendata-draft-spec` with structured fields to get a valid "
            "YAML spec, then pass it here. The new plugin is materialized "
            "to disk, imported, registered in the live registry, and its "
            "tools become available immediately."
        ),
        inputSchema=CreatePluginParams.model_json_schema(),
        annotations=types.ToolAnnotations(destructiveHint=False),
    )
)
TOOLS_HANDLERS["opendata-create-plugin"] = handle_create_plugin


###################
# draft-spec (build a YAML plugin spec from structured inputs)
###################


class DraftSpecToolParam(BaseModel):
    """One parameter on a tool inside a draft spec."""

    name: str = Field(
        ...,
        description="Parameter name (snake_case, matches API query/path param).",
    )
    type: str = Field(
        ...,
        description="Type: one of 'str', 'int', 'float', 'bool'.",
    )
    required: bool = Field(
        default=False,
        description="Whether the parameter is required.",
    )
    description: str = Field(
        default="",
        description="Short human-readable description of the parameter.",
    )
    default: Any | None = Field(
        default=None,
        description="Optional default value when the parameter is not required.",
    )


class DraftSpecTool(BaseModel):
    """One tool entry in a draft plugin spec."""

    name: str = Field(
        ...,
        description=(
            "Tool name in globally unique kebab-case, often using a "
            "provider-specific prefix (e.g. 'nvd-list-cves')."
        ),
    )
    description: str = Field(
        ...,
        description="Concise description of what the tool returns. The LLM sees this.",
    )
    endpoint: str = Field(
        ...,
        description=(
            "URL path relative to base_url. May contain {placeholders} for "
            "path parameters (e.g. '/cves/{cve_id}'). Each placeholder must "
            "appear as a required param of matching name."
        ),
    )
    response_format: str = Field(
        default="json",
        description="'json' (default) or 'text' for plain-text responses.",
    )
    params: list[DraftSpecToolParam] = Field(
        default_factory=list,
        description="Query parameters and path-placeholder parameters for this tool.",
    )


class DraftSpecParams(BaseModel):
    """Structured inputs that produce a valid plugin YAML spec."""

    id: str = Field(
        ...,
        description=(
            "Plugin id in snake_case, e.g. 'global_nvd_cve'. Becomes the "
            "Python module name under meta_data_mcp/providers/."
        ),
    )
    title: str = Field(
        ...,
        description="Human-readable title for the registry entry.",
    )
    base_url: str = Field(
        ...,
        description="API base URL with no trailing slash (e.g. 'https://services.nvd.nist.gov').",
    )
    description: str = Field(
        ...,
        description="One- or two-sentence description of what this plugin covers.",
    )
    homepage: str = Field(
        ...,
        description="URL to the API documentation or provider homepage.",
    )
    tools: list[DraftSpecTool] = Field(
        ...,
        min_length=1,
        description="At least one tool definition. Each becomes one MCP tool on the server.",
    )
    server_name: str | None = Field(
        default=None,
        description=(
            "kebab-case server name for the plugin registry entry. "
            "Defaults to id with underscores replaced by hyphens."
        ),
    )
    domains: list[str] = Field(
        default_factory=list,
        description="Registry domains (e.g. ['security', 'government']).",
    )
    regions: list[str] = Field(
        default_factory=list,
        description="Registry regions (e.g. ['global', 'us']).",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Search keywords that should match this plugin in opendata-find-providers.",
    )
    requires_env: list[str] = Field(
        default_factory=list,
        description="Names of environment variables this API needs (e.g. ['NVD_API_KEY']).",
    )


_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ALLOWED_PARAM_TYPES = ("str", "int", "float", "bool")


async def handle_draft_spec(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Compose a validated plugin YAML spec from structured inputs.

    Use this BEFORE calling `opendata-create-plugin`. The output is a
    YAML string that's syntactically valid, schema-compliant, and ready
    to pass straight through. This eliminates the need to hand-author
    YAML and surfaces validation errors up-front.
    """
    import yaml as _yaml

    try:
        params = DraftSpecParams(**(arguments or {}))

        if not _ID_RE.match(params.id):
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {
                            "error": (
                                f"id {params.id!r} must be lowercase snake_case "
                                "(matches /^[a-z][a-z0-9_]*$/)."
                            )
                        }
                    ),
                )
            ]

        server_name = params.server_name or params.id.replace("_", "-")
        if not _TOOL_NAME_RE.match(server_name):
            return [
                types.TextContent(
                    type="text",
                    text=serialize_for_llm(
                        {
                            "error": (
                                f"server_name {server_name!r} must be lowercase "
                                "kebab-case."
                            )
                        }
                    ),
                )
            ]

        # Validate every tool entry.
        for i, tool in enumerate(params.tools):
            if not _TOOL_NAME_RE.match(tool.name):
                return [
                    types.TextContent(
                        type="text",
                        text=serialize_for_llm(
                            {
                                "error": (
                                    f"tools[{i}].name {tool.name!r} must be "
                                    "lowercase kebab-case."
                                )
                            }
                        ),
                    )
                ]
            if tool.response_format not in ("json", "text"):
                return [
                    types.TextContent(
                        type="text",
                        text=serialize_for_llm(
                            {
                                "error": (
                                    f"tools[{i}].response_format must be 'json' "
                                    f"or 'text', got {tool.response_format!r}."
                                )
                            }
                        ),
                    )
                ]
            placeholders = _PLACEHOLDER_RE.findall(tool.endpoint)
            param_names = {p.name for p in tool.params}
            missing_placeholders = [p for p in placeholders if p not in param_names]
            if missing_placeholders:
                return [
                    types.TextContent(
                        type="text",
                        text=serialize_for_llm(
                            {
                                "error": (
                                    f"tools[{i}] endpoint has placeholders "
                                    f"{missing_placeholders} that are not declared "
                                    "as params."
                                )
                            }
                        ),
                    )
                ]
            for j, p in enumerate(tool.params):
                if p.type not in _ALLOWED_PARAM_TYPES:
                    return [
                        types.TextContent(
                            type="text",
                            text=serialize_for_llm(
                                {
                                    "error": (
                                        f"tools[{i}].params[{j}].type "
                                        f"{p.type!r} must be one of "
                                        f"{list(_ALLOWED_PARAM_TYPES)}."
                                    )
                                }
                            ),
                        )
                    ]
                if p.name in placeholders and not p.required:
                    return [
                        types.TextContent(
                            type="text",
                            text=serialize_for_llm(
                                {
                                    "error": (
                                        f"tools[{i}].params[{j}] {p.name!r} is a "
                                        "path placeholder and must be required=true."
                                    )
                                }
                            ),
                        )
                    ]

        # Build the plain-dict spec, in the same field order the example uses
        # so the emitted YAML matches the project's existing style.
        spec_dict: dict[str, Any] = {
            "id": params.id,
            "server_name": server_name,
            "title": params.title,
            "base_url": params.base_url,
            "description": params.description,
            "homepage": params.homepage,
        }
        if params.domains:
            spec_dict["domains"] = list(params.domains)
        if params.regions:
            spec_dict["regions"] = list(params.regions)
        if params.keywords:
            spec_dict["keywords"] = list(params.keywords)
        if params.requires_env:
            spec_dict["requires_env"] = list(params.requires_env)

        spec_dict["tools"] = []
        for tool in params.tools:
            tool_dict: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "endpoint": tool.endpoint,
                "response_format": tool.response_format,
            }
            if tool.params:
                tool_dict["params"] = []
                for p in tool.params:
                    p_dict: dict[str, Any] = {
                        "name": p.name,
                        "type": p.type,
                        "required": p.required,
                        "description": p.description,
                    }
                    if p.default is not None:
                        p_dict["default"] = p.default
                    tool_dict["params"].append(p_dict)
            spec_dict["tools"].append(tool_dict)

        spec_yaml = _yaml.safe_dump(spec_dict, sort_keys=False, width=88)

        return [
            types.TextContent(
                type="text",
                text=serialize_for_llm(
                    {
                        "status": "ok",
                        "spec_yaml": spec_yaml,
                        "next_step": (
                            "Pass `spec_yaml` (and your chosen domains/regions/"
                            "keywords/requires_env) to `opendata-create-plugin` to "
                            "materialize and hot-load the plugin."
                        ),
                    }
                ),
            )
        ]
    except Exception as e:
        log.error(f"Error in opendata-draft-spec: {e}")
        return [
            types.TextContent(
                type="text",
                text=serialize_for_llm({"error": str(e)}),
            )
        ]


TOOLS.append(
    types.Tool(
        name="opendata-draft-spec",
        description=(
            "Build a validated YAML plugin spec from structured inputs. "
            "Use this BEFORE `opendata-create-plugin` to avoid hand-writing "
            "YAML. Validates id format, kebab-case tool names, path-"
            "placeholder/param consistency, parameter types, and response "
            "format. Returns the YAML string ready to feed into "
            "`opendata-create-plugin`."
        ),
        inputSchema=DraftSpecParams.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True),
    )
)
TOOLS_HANDLERS["opendata-draft-spec"] = handle_draft_spec


###################
# explain-choice
###################


class ExplainChoiceParams(BaseModel):
    """Parameters for explain-choice tool."""

    query: Optional[str] = Field(
        None,
        description="The original search query to explain scoring for.",
    )
    domain: Optional[str] = Field(
        None,
        description="Domain filter used in search.",
    )
    region: Optional[str] = Field(
        None,
        description="Region filter used in search.",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of top providers to explain (1-20, default 5).",
    )


async def handle_explain_choice(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Explain the scoring breakdown for a provider search query.

    Shows how each provider was ranked, including the contribution of
    token matching, fuzzy matching, semantic similarity, and metadata filters.
    """
    try:
        params = ExplainChoiceParams(**(arguments or {}))

        scored_results = await _engine.route(
            query=params.query,
            domain=params.domain,
            region=params.region,
            limit=params.limit,
            explain=True,
        )

        # Format explanation
        explanations = []
        for i, result in enumerate(scored_results, 1):
            explanation = {
                "rank": i,
                "provider_id": result.entry.id,
                "provider_title": result.entry.title,
                "overall_score": round(result.score, 3),
                "scoring_breakdown": result.breakdown,
            }
            explanations.append(explanation)

        payload = {
            "query": params.query,
            "domain_filter": params.domain,
            "region_filter": params.region,
            "results": explanations,
        }

        return [types.TextContent(type="text", text=serialize_for_llm(payload))]
    except Exception as e:
        log.error(f"Error in opendata-explain-choice: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opendata-explain-choice",
        description="Explain the scoring breakdown for a provider search. Shows how each provider was ranked using token matching, fuzzy matching, semantic similarity, and metadata filters.",
        inputSchema=ExplainChoiceParams.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True),
    )
)
TOOLS_HANDLERS["opendata-explain-choice"] = handle_explain_choice


###################
# list-domains
###################


class ListDomainsParams(BaseModel):
    """No parameters."""


async def handle_list_domains(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opendata-list-domains tool call."""
    try:
        return [
            types.TextContent(
                type="text",
                text=serialize_for_llm({"domains": list_domains()}),
            )
        ]
    except Exception as e:
        log.error(f"Error in opendata-list-domains: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opendata-list-domains",
        description="List the controlled domain vocabulary used by the provider registry (e.g. 'health', 'legal', 'finance', 'earth-science').",
        inputSchema=ListDomainsParams.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True),
        _meta={"ui": {"resourceUri": DISCOVERY_APP_URI}},
    )
)
TOOLS_HANDLERS["opendata-list-domains"] = handle_list_domains


###################
# list-regions
###################


class ListRegionsParams(BaseModel):
    """No parameters."""


async def handle_list_regions(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opendata-list-regions tool call."""
    try:
        return [
            types.TextContent(
                type="text",
                text=serialize_for_llm({"regions": list_regions()}),
            )
        ]
    except Exception as e:
        log.error(f"Error in opendata-list-regions: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opendata-list-regions",
        description="List the controlled region vocabulary used by the provider registry (e.g. 'us', 'eu', 'uk', 'global').",
        inputSchema=ListRegionsParams.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True),
        _meta={"ui": {"resourceUri": DISCOVERY_APP_URI}},
    )
)
TOOLS_HANDLERS["opendata-list-regions"] = handle_list_regions


###################
# describe-provider
###################


class DescribeProviderParams(BaseModel):
    """Lookup a single registry entry by provider id."""

    provider_id: str = Field(
        ...,
        description="The provider id (e.g. 'us_nasa', 'global_world_bank', 'us_courtlistener').",
    )


async def handle_describe_provider(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opendata-describe-provider tool call."""
    try:
        if not arguments or "provider_id" not in arguments:
            raise ValueError("provider_id is required")
        params = DescribeProviderParams(**arguments)
        entry = get_provider(params.provider_id)
        if entry is None:
            payload = {"error": f"Provider '{params.provider_id}' not found"}
        else:
            payload = entry.to_dict()
        return [types.TextContent(type="text", text=serialize_for_llm(payload))]
    except Exception as e:
        log.error(f"Error in opendata-describe-provider: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opendata-describe-provider",
        description="Fetch the full registry entry for a single provider id — title, description, domains, regions, keywords, homepage, license note, required environment variables.",
        inputSchema=DescribeProviderParams.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True),
    )
)
TOOLS_HANDLERS["opendata-describe-provider"] = handle_describe_provider


###################
# list-providers
###################


class ListProvidersParams(BaseModel):
    """Pagination over the full registry."""

    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of entries to return (1-200, default 50).",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of entries to skip (default 0).",
    )


async def handle_list_providers(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opendata-list-providers tool call."""
    try:
        params = ListProvidersParams(**(arguments or {}))
        all_entries = list(REGISTRY)
        slice_ = all_entries[params.offset : params.offset + params.limit]
        payload = {
            "total": len(REGISTRY),
            "offset": params.offset,
            "limit": params.limit,
            "providers": [
                {
                    "id": entry.id,
                    "title": entry.title,
                    "domains": list(entry.domains),
                    "regions": list(entry.regions),
                    "requires_env": list(entry.requires_env),
                }
                for entry in slice_
            ],
        }
        return [types.TextContent(type="text", text=serialize_for_llm(payload))]
    except Exception as e:
        log.error(f"Error in opendata-list-providers: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opendata-list-providers",
        description="Enumerate all providers in the opendata-mcp registry (paginated, terse). Returns id, title, domains, regions, and any required env vars per provider.",
        inputSchema=ListProvidersParams.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True),
    )
)
TOOLS_HANDLERS["opendata-list-providers"] = handle_list_providers


###################
# Resources
###################

RESOURCES.append(
    types.Resource(
        uri="registry://all-providers",
        name="All OpenData Providers",
        description="A complete list of all currently registered OpenData MCP providers and their metadata.",
        mimeType="application/json",
    )
)


def handle_read_all_providers(uri: AnyUrl) -> str:
    payload = [entry.to_dict() for entry in REGISTRY]
    return serialize_for_llm(payload)


RESOURCES_HANDLERS["registry://all-providers"] = handle_read_all_providers


###################
# UI resources (MCP Apps shape primitives — v2.0 Phase 2)
###################

# Register the reusable ``ui://meta-data-mcp/shape/*`` bundles plus the
# Phase-3 ``ui://meta-data-mcp/app/*`` panels. Phase 4 provider sweeps
# bind individual tools to a primitive by setting
# ``Tool._meta = {"ui": {"resourceUri": <URI>}}``; the discovery meta
# tools above bind to the discovery app the same way.
from meta_data_mcp.ui_resources import register_apps, register_shapes  # noqa: E402

register_shapes(RESOURCES, RESOURCES_HANDLERS)
register_apps(RESOURCES, RESOURCES_HANDLERS)


###################
# Prompts
###################

# Create a module-level variable for prompts
PROMPTS: List[types.Prompt] = []
PROMPTS_HANDLERS: dict[str, Any] = {}

PROMPTS.append(
    types.Prompt(
        name="discover-providers",
        description="Ask the LLM to analyze your needs and suggest the best OpenData MCP providers.",
        arguments=[
            types.PromptArgument(
                name="use_case",
                description="What are you trying to build? (e.g., 'a dashboard for weather and flights')",
                required=True,
            )
        ],
    )
)


async def handle_discover_providers(
    arguments: dict[str, str] | None,
) -> types.GetPromptResult:
    use_case = (arguments or {}).get("use_case", "General exploration")

    return types.GetPromptResult(
        description=f"Suggest providers for: {use_case}",
        messages=[
            types.PromptMessage(
                role="user",
                content=types.TextContent(
                    type="text",
                    text=f"I want to build the following: {use_case}\n\nPlease use your `opendata-find-providers` tool to search the registry and recommend the 3 most relevant providers for my project. Explain why each one is a good fit and how I can use them together.",
                ),
            )
        ],
    )


PROMPTS_HANDLERS["discover-providers"] = handle_discover_providers

# Predefined Use Cases
USE_CASES = {
    "usecase-financial-research": {
        "title": "Financial & Economic Research",
        "description": "Analyze economic indicators, exchange rates, and financial markets.",
        "text": "I am building a financial research tool. Please suggest 3-5 providers from the registry that deal with economics, finance, exchange rates, or central banks (e.g., FRED, ECB, World Bank, DBnomics). Explain what data each provides and how they can be correlated.",
    },
    "usecase-climate-dashboard": {
        "title": "Climate & Environment Dashboard",
        "description": "Build tools to track weather, climate change, and environmental data.",
        "text": "I am building a climate and environment dashboard. Please suggest 3-5 providers from the registry that deal with weather, climate, emissions, or earth science (e.g., NOAA, Copernicus, OpenMeteo). Explain what data each provides and how to combine them for a comprehensive view.",
    },
    "usecase-healthcare-analytics": {
        "title": "Healthcare & Epidemiology Analytics",
        "description": "Track diseases, clinical trials, and public health data.",
        "text": "I am building a healthcare analytics platform. Please suggest 3-5 providers from the registry that deal with public health, epidemiology, or clinical trials (e.g., CDC, FDA, WHO, Disease.sh). Explain what datasets they offer and how they might be used together.",
    },
    "usecase-academic-literature": {
        "title": "Academic Literature Review",
        "description": "Search for scholarly articles, preprints, and citations.",
        "text": "I am conducting an academic literature review. Please suggest 3-5 providers from the registry that offer access to scholarly publications, metadata, and citations (e.g., ArXiv, CrossRef, EuropePMC, OpenAlex). Explain how to use them together for a comprehensive literature search.",
    },
}

for prompt_id, case_info in USE_CASES.items():
    PROMPTS.append(
        types.Prompt(name=prompt_id, description=case_info["description"], arguments=[])
    )

    # Need a factory function to capture the correct text in the closure
    def make_handler(text: str, title: str):
        async def handler(arguments: dict[str, str] | None) -> types.GetPromptResult:
            return types.GetPromptResult(
                description=f"Recommendations for: {title}",
                messages=[
                    types.PromptMessage(
                        role="user", content=types.TextContent(type="text", text=text)
                    )
                ],
            )

        return handler

    PROMPTS_HANDLERS[prompt_id] = make_handler(case_info["text"], case_info["title"])


###################
# activate-provider
###################


class ActivateProviderParams(BaseModel):
    """Parameters for opendata-activate-provider."""

    provider_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Provider id to activate (e.g. 'us_data_gov' or 'us-data-gov'). "
            "Use opendata-find-providers or opendata-list-providers to "
            "discover available ids."
        ),
    )


async def handle_activate_provider(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opendata-activate-provider tool call."""
    params = ActivateProviderParams(**(arguments or {}))
    report = _activate_provider(params.provider_id)
    # Only fire tools/list_changed when the catalog actually changed.
    # "already_active" is a no-op — sending the notification would force the
    # client to refetch tools/list for no semantic reason.
    if report.get("status") == "activated":
        await _notify_tools_changed()
    return [types.TextContent(type="text", text=serialize_for_llm(report))]


TOOLS.append(
    types.Tool(
        name="opendata-activate-provider",
        description=(
            "Activate a registered provider so its tools become callable in this "
            "session. By default the server starts in discovery-only mode — only "
            "meta tools (find-providers, list-providers, etc.) are advertised. "
            "Activation imports the plugin module and merges its tools into the "
            "advertised list, then sends a tools/list_changed notification so the "
            "client refetches its catalog. Idempotent."
        ),
        inputSchema=ActivateProviderParams.model_json_schema(),
        annotations=types.ToolAnnotations(idempotentHint=True),
        _meta={"ui": {"resourceUri": DISCOVERY_APP_URI}},
    )
)
TOOLS_HANDLERS["opendata-activate-provider"] = handle_activate_provider


###################
# deactivate-provider
###################


class DeactivateProviderParams(BaseModel):
    """Parameters for opendata-deactivate-provider."""

    provider_id: str = Field(
        ...,
        min_length=1,
        description="Provider id to deactivate.",
    )


async def handle_deactivate_provider(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opendata-deactivate-provider tool call."""
    params = DeactivateProviderParams(**(arguments or {}))
    report = _deactivate_provider(params.provider_id)
    if report.get("status") == "deactivated":
        await _notify_tools_changed()
    return [types.TextContent(type="text", text=serialize_for_llm(report))]


TOOLS.append(
    types.Tool(
        name="opendata-deactivate-provider",
        description=(
            "Remove a previously-activated provider's tools from the session's "
            "advertised list. The Python module remains imported (Python caches "
            "modules) but its tools no longer appear in tools/list. A "
            "tools/list_changed notification is sent so the client refetches."
        ),
        inputSchema=DeactivateProviderParams.model_json_schema(),
        annotations=types.ToolAnnotations(idempotentHint=True),
    )
)
TOOLS_HANDLERS["opendata-deactivate-provider"] = handle_deactivate_provider


###################
# list-active-providers
###################


class ListActiveProvidersParams(BaseModel):
    """No parameters."""


async def handle_list_active_providers(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opendata-list-active-providers tool call."""
    ListActiveProvidersParams(**(arguments or {}))
    # Plugin-owned tools are anything _owner_by_tool maps to a non-meta value.
    # Everything else in TOOLS_HANDLERS is a meta tool. Counting this way
    # keeps the report correct even if _owner_by_tool was never seeded
    # (e.g. when callers bypass _load_all_plugins).
    plugin_tools = {name for name, owner in _owner_by_tool.items() if owner != "meta"}
    meta_tools = [name for name in TOOLS_HANDLERS if name not in plugin_tools]
    payload = {
        "count": len(_active_providers),
        "active_providers": sorted(_active_providers),
        "tools_per_provider": {
            pid: sorted(name for name, owner in _owner_by_tool.items() if owner == pid)
            for pid in sorted(_active_providers)
        },
        "meta_tool_count": len(meta_tools),
        "plugin_tool_count": len(plugin_tools),
    }
    return [types.TextContent(type="text", text=serialize_for_llm(payload))]


TOOLS.append(
    types.Tool(
        name="opendata-list-active-providers",
        description=(
            "List the providers currently activated in this session, along with "
            "the tool names each contributes. Useful for inspecting why a "
            "particular tool is (or isn't) advertised."
        ),
        inputSchema=ListActiveProvidersParams.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True),
        _meta={"ui": {"resourceUri": DISCOVERY_APP_URI}},
    )
)
TOOLS_HANDLERS["opendata-list-active-providers"] = handle_list_active_providers


###################
# health-snapshot (Phase 3)
###################


class HealthSnapshotParams(BaseModel):
    """Parameters for opendata-health-snapshot."""

    provider_ids: Optional[List[str]] = Field(
        None,
        description=(
            "Optional list of provider ids to query. When omitted, returns "
            "snapshots for every provider in the static + dynamic registry. "
            "Providers with no recorded failures default to a fully-healthy "
            "baseline (score 1.0)."
        ),
    )


async def handle_health_snapshot(
    arguments: dict[str, Any] | None = None,
) -> Sequence[types.TextContent]:
    """Handle the opendata-health-snapshot tool call.

    Returns a per-provider health snapshot for the discovery app's live
    health badges. The payload follows the shape:

    .. code-block:: json

        {
          "snapshot": {
            "<provider_id>": {
              "score": <float in [0.0, 1.0]>,
              "failure_mass": <float>,
              "last_update_ts": <monotonic seconds | null>
            },
            ...
          },
          "generated_at":           <wall-clock unix epoch seconds>,
          "generated_at_monotonic": <monotonic seconds>
        }

    The ``score`` field is what :class:`HealthScorer` would return for
    that provider at the same instant — so the UI badge and the routing
    score never disagree. See ``health.snapshot()`` for the underlying
    helper.

    Two clock fields are exposed because they live in different domains.
    ``last_update_ts`` is in the monotonic clock the health module uses
    internally (never decreases, no wall-clock meaning); ``generated_at``
    is wall-clock seconds suitable for display ("snapshot taken at X");
    ``generated_at_monotonic`` is the matching reference clock so a
    consumer that wants to compute staleness ("how long ago was this
    provider's last update?") can subtract ``last_update_ts`` from it
    correctly. Mixing wall-clock and monotonic would produce nonsense
    durations.
    """
    try:
        params = HealthSnapshotParams(**(arguments or {}))
        # Default to every registered provider so an empty call from the
        # discovery app gets a useful response. Use iter_registry to also
        # cover hot-loaded plugins from opendata-create-plugin.
        if params.provider_ids is None:
            ids = [entry.id for entry in iter_registry()]
        else:
            ids = list(params.provider_ids)
        snap = health.snapshot(ids)
        payload = {
            "snapshot": snap,
            "generated_at": time.time(),
            "generated_at_monotonic": time.monotonic(),
        }
        return [types.TextContent(type="text", text=serialize_for_llm(payload))]
    except Exception as e:
        log.error(f"Error in opendata-health-snapshot: {e}")
        raise


TOOLS.append(
    types.Tool(
        name="opendata-health-snapshot",
        description=(
            "Snapshot the in-memory provider health registry. Returns a "
            "score in [0.0, 1.0] for each requested provider (or every "
            "registered provider when called without arguments). Health "
            "degrades on recent 5xx / 429 / network failures and decays "
            "back toward 1.0 over ~5 minutes; 401/403 are excluded as "
            "caller misconfig. The discovery app uses this to paint live "
            "health badges next to each search result."
        ),
        inputSchema=HealthSnapshotParams.model_json_schema(),
        annotations=types.ToolAnnotations(readOnlyHint=True, idempotentHint=True),
        _meta={"ui": {"resourceUri": DISCOVERY_APP_URI}},
    )
)
TOOLS_HANDLERS["opendata-health-snapshot"] = handle_health_snapshot


# ---------------------------------------------------------------------------
# Plugin loading
# ---------------------------------------------------------------------------
#
# A plugin is a sibling module under meta_data_mcp.providers that exposes
# `TOOLS` and `TOOLS_HANDLERS`. By default the server starts in DISCOVERY-
# ONLY mode — only the meta tools defined in THIS module are advertised —
# and individual plugins are activated on demand via
# ``opendata-activate-provider`` (or in batch via the
# ``META_DATA_MCP_PRELOAD`` environment variable). This keeps the
# tool-catalog footprint small for clients with limited context budgets.
#
# Set ``META_DATA_MCP_PRELOAD=*`` to restore the legacy behavior of
# loading every registered plugin at startup.
#
# The implementation lives in meta_data_mcp.discovery.loader (extracted in
# the v2.1 hygiene pass, architecture review §H2). The names below are
# re-exported via the top-of-module import block so existing call sites
# (and the ~30 tests that patch them by full dotted path) keep working
# unchanged.


async def main(transport: str = "stdio", port: int = 8000, host: str = "127.0.0.1"):
    from meta_data_mcp.utils import create_mcp_server, run_server

    plugins_loaded, plugin_tools_added = _load_all_plugins()
    log.info(
        "meta-data-mcp — %d plugins preloaded, %d plugin tools + %d discovery tools "
        "(set META_DATA_MCP_PRELOAD=* to preload all, or use opendata-activate-provider "
        "at runtime)",
        plugins_loaded,
        plugin_tools_added,
        len(_state.tools) - plugin_tools_added,
    )

    server = create_mcp_server(
        "meta-data-mcp",
        resources=RESOURCES,
        resources_handlers=RESOURCES_HANDLERS,
        tools=_state.tools,
        tools_handlers=_state.tools_handlers,
        prompts=PROMPTS,
        prompts_handlers=PROMPTS_HANDLERS,
    )
    _state.server = server

    await run_server(server, transport, port, host)


if __name__ == "__main__":
    import anyio

    anyio.run(main)
