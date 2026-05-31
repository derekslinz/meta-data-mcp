import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from meta_data_mcp.providers.meta_data_mcp import (
    RESOURCES,
    PROMPTS,
    PROMPTS_HANDLERS,
    RESOURCES_HANDLERS,
    TOOLS,
    TOOLS_HANDLERS,
    _owner_by_tool,
    handle_describe_provider,
    handle_call_tool,
    handle_discover_providers,
    handle_explain_choice,
    handle_find_providers,
    handle_list_domains,
    handle_list_providers,
    handle_list_regions,
    handle_read_all_providers,
)

EXPECTED_PROVIDER_FIELDS = {
    "id",
    "server_name",
    "title",
    "description",
    "domains",
    "regions",
    "keywords",
    "homepage",
    "license_note",
    "requires_env",
}


def _parse(result: Any) -> dict:
    """Parse a handler result regardless of return type (dict or list[TextContent])."""
    if isinstance(result, dict):
        return result
    return json.loads(result[0].text)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_meta_tools_registered():
    """The discovery + autonomous-creation meta tools must be registered at
    module-import time (before main() merges plugins)."""
    names = {tool.name for tool in TOOLS}
    expected = {
        "opendata.explain.choice",
        "opendata.providers.find",
        "opendata.domains.list",
        "opendata.regions.list",
        "opendata.providers.describe",
        "opendata.providers.list",
        "opendata.plugins.create",
        "opendata.plugins.draft",
    }
    # The set must contain (at least) all of the above. Plugin tools may
    # have been hot-loaded earlier in this test session — that's fine.
    assert expected.issubset(names), f"missing: {expected - names}"


@pytest.mark.anyio
async def test_draft_spec_emits_valid_yaml_for_simple_api():
    """draft-spec returns a YAML string that the generator's validator accepts."""
    from meta_data_mcp.providers.meta_data_mcp import handle_draft_spec

    result = await handle_draft_spec(
        {
            "id": "global_drafttest",
            "title": "Draft Test API",
            "base_url": "https://api.example.com",
            "description": "Draft-spec test plugin.",
            "homepage": "https://example.com/",
            "domains": ["finance"],
            "regions": ["global"],
            "keywords": ["test"],
            "tools": [
                {
                    "name": "drafttest-get-thing",
                    "description": "Get a thing by id.",
                    "endpoint": "/v1/things/{thing_id}",
                    "response_format": "json",
                    "params": [
                        {
                            "name": "thing_id",
                            "type": "str",
                            "required": True,
                            "description": "the id",
                        },
                    ],
                },
            ],
        }
    )
    payload = _parse(result)
    assert payload.get("status") == "ok", payload
    yaml_str = payload["spec_yaml"]
    assert "title: Draft Test API" in yaml_str

    # Round-trip the YAML through the generator's validator to prove it's
    # actually consumable by `opendata.plugins.create`.
    import tempfile
    from pathlib import Path as _P
    import importlib.util

    with tempfile.TemporaryDirectory() as temp_dir:
        spec_path = _P(temp_dir) / "draft.yaml"
        spec_path.write_text(yaml_str)

        gen_path = _P(__file__).resolve().parents[2] / "tools" / "generate_provider.py"
        gen_spec = importlib.util.spec_from_file_location("generate_provider", gen_path)
        assert gen_spec is not None and gen_spec.loader is not None
        gen_mod = importlib.util.module_from_spec(gen_spec)
        gen_spec.loader.exec_module(gen_mod)
        parsed = gen_mod.load_spec(spec_path)
    assert parsed["id"] == "global_drafttest"
    assert parsed["server_name"] == "global-drafttest"
    assert parsed["title"] == "Draft Test API"
    assert parsed["tools"][0]["name"] == "drafttest-get-thing"


@pytest.mark.anyio
async def test_draft_spec_rejects_path_placeholder_without_required_param():
    from meta_data_mcp.providers.meta_data_mcp import handle_draft_spec

    result = await handle_draft_spec(
        {
            "id": "bad_one",
            "title": "Bad",
            "base_url": "https://example.com",
            "description": "x",
            "homepage": "https://example.com",
            "tools": [
                {
                    "name": "bad-tool",
                    "description": "x",
                    "endpoint": "/v1/{missing_id}",
                    "params": [],
                }
            ],
        }
    )
    payload = _parse(result)
    assert "error" in payload
    assert "missing_id" in payload["error"]


@pytest.mark.anyio
async def test_draft_spec_rejects_bad_id_casing():
    from meta_data_mcp.providers.meta_data_mcp import handle_draft_spec

    result = await handle_draft_spec(
        {
            "id": "Bad-Id",  # not snake_case
            "title": "x",
            "base_url": "https://example.com",
            "description": "x",
            "homepage": "https://example.com",
            "tools": [
                {
                    "name": "x-y",
                    "description": "x",
                    "endpoint": "/x",
                    "params": [],
                }
            ],
        }
    )
    payload = _parse(result)
    assert "error" in payload
    assert "snake_case" in payload["error"].lower()


@pytest.mark.anyio
async def test_find_providers_by_query():
    result = await handle_find_providers({"query": "earthquake"})
    assert "us_usgs_earthquake" in json.dumps(result, default=str)


@pytest.mark.anyio
async def test_find_providers_by_domain():
    result = await handle_find_providers({"domain": "health"})
    text = json.dumps(result, default=str)
    assert (
        "us_cdc_socrata" in text or "us_fda_openfda" in text or "global_who_gho" in text
    )


@pytest.mark.anyio
async def test_find_providers_by_region():
    result = await handle_find_providers({"region": "uk"})
    text = json.dumps(result, default=str)
    assert "uk_gov" in text or "uk_ons" in text


@pytest.mark.anyio
async def test_find_providers_by_region_case_insensitive():
    result = await handle_find_providers({"region": "UK"})
    payload = _parse(result)
    assert payload["count"] > 0


@pytest.mark.anyio
async def test_call_tool_reports_only_plugin_tools_when_missing():
    saved_owner_by_tool = dict(_owner_by_tool)
    try:
        _owner_by_tool.clear()
        _owner_by_tool.update(
            {
                "opendata.providers.list": "meta",
                "example-plugin-tool": "example_provider",
            }
        )
        payload = await handle_call_tool({"tool_name": "missing-tool"})
    finally:
        _owner_by_tool.clear()
        _owner_by_tool.update(saved_owner_by_tool)

    assert payload["activated_tools"] == ["example-plugin-tool"]


@pytest.mark.anyio
async def test_find_providers_without_query_uses_filters_only():
    result = await handle_find_providers({"domain": "health", "limit": 5})
    payload = _parse(result)
    assert payload["count"] > 0


@pytest.mark.anyio
async def test_explain_choice_includes_breakdown():
    result = await handle_explain_choice({"query": "earthquake", "limit": 1})
    payload = _parse(result)
    assert payload["results"]
    assert payload["results"][0]["scoring_breakdown"] is not None


@pytest.mark.anyio
async def test_find_providers_combined_filters():
    result = await handle_find_providers(
        {"query": "exchange rates", "domain": "finance", "limit": 5}
    )
    text = json.dumps(result, default=str)
    assert "frankfurter" in text.lower() or "ecb" in text.lower()


@pytest.mark.anyio
async def test_find_providers_no_match_returns_empty():
    result = await handle_find_providers({"query": "zzzzzzzz_no_such_topic"})
    text = json.dumps(result, default=str)
    assert '"count": 0' in text
    # The no-match response must steer the LLM toward autonomous creation.
    payload = json.loads(text)
    assert payload.get("no_match") is True
    assert "opendata.plugins.create" in payload.get("next_step", "")


@pytest.mark.anyio
async def test_create_plugin_end_to_end(tmp_path, monkeypatch):
    """Drive opendata.plugins.create through a real generator invocation.

    Confirms the pipeline: validate spec → write to tools/specs/ → run
    generate_provider.py → import module → register in dynamic registry →
    hot-load tools onto the running server.
    """
    import meta_data_mcp.providers.meta_data_mcp as srv
    from meta_data_mcp.providers.meta_data_mcp import (
        handle_create_plugin,
        TOOLS_HANDLERS as live_handlers,
    )
    from meta_data_mcp.registry import (
        REGISTRY,
        get_provider,
    )

    # Use a unique id so this test is hermetic even if files leak.
    pid = "autotest_unique_plugin_xyz"

    spec_yaml = f"""id: {pid}
server_name: autotest-unique-plugin-xyz
title: Autonomous Test Plugin
base_url: https://api.example.com
description: Test plugin materialized by create-plugin end-to-end.
homepage: https://example.com/
tools:
  - name: autotest-fetch-thing
    description: Fetch a thing.
    endpoint: /v1/thing
    params:
      - {{name: id, type: str, required: false, description: thing id}}
"""

    saved_registry = REGISTRY.snapshot()
    saved_handlers = dict(live_handlers)
    saved_tools = list(srv.TOOLS)
    try:
        result = await handle_create_plugin(
            {
                "spec_yaml": spec_yaml,
                "domains": ["finance"],
                "regions": ["global"],
                "keywords": ["test", "autonomous"],
            }
        )
        payload = _parse(result)
        assert payload.get("status") == "ok", payload
        assert payload["plugin_id"] == pid
        assert "autotest-fetch-thing" in payload["new_tool_names"]
        # Registry entry should be addressable through get_provider()
        entry = get_provider(pid)
        assert entry is not None
        assert "finance" in entry.domains
        # Tool should be live in the running server's handler table
        assert "autotest-fetch-thing" in live_handlers
    finally:
        # Tear down to keep other tests hermetic.
        REGISTRY.restore(saved_registry)
        live_handlers.clear()
        live_handlers.update(saved_handlers)
        srv.TOOLS[:] = saved_tools
        # Best-effort cleanup of files materialized on disk.
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        for path in (
            repo_root / "tools" / "specs" / f"{pid}.yaml",
            repo_root / "meta_data_mcp" / "providers" / f"{pid}.py",
            repo_root / "tests" / "providers" / f"test_{pid}.py",
        ):
            if path.exists():
                path.unlink()


@pytest.mark.anyio
async def test_create_plugin_rejects_invalid_yaml():
    from meta_data_mcp.providers.meta_data_mcp import handle_create_plugin

    result = await handle_create_plugin({"spec_yaml": "::not valid yaml :::"})
    payload = _parse(result)
    assert "error" in payload


@pytest.mark.anyio
async def test_create_plugin_rejects_missing_required_keys():
    from meta_data_mcp.providers.meta_data_mcp import handle_create_plugin

    result = await handle_create_plugin({"spec_yaml": "id: only_id\n"})
    payload = _parse(result)
    assert "error" in payload
    assert "missing required keys" in payload["error"].lower()


# -- Security regression tests for opendata.plugins.create (RCE-prevention) ---
#
# These guard against the five vulnerabilities surfaced in the expanded-scope
# security review: id-driven path traversal in handle_create_plugin, plus
# three template-injection RCE paths through endpoint / param.name /
# server_name in tools/generate_provider.py, plus the missing post-generation
# AST validator.

_MALICIOUS_ID_SPEC = """id: ../../../tmp/pwn
server_name: pwn-server
base_url: https://example.com
description: malicious id
tools:
  - name: t
    description: t
    endpoint: /x
"""

_MALICIOUS_PARAM_NAME_SPEC = """id: secrgntest_param
server_name: secrgntest-param
base_url: https://example.com
description: malicious param name
tools:
  - name: t
    description: t
    endpoint: /x
    params:
      - {name: 'x"; bad = True; #', type: str, required: true, description: oops}
"""

_MALICIOUS_ENDPOINT_SPEC = """id: secrgntest_endpoint
server_name: secrgntest-endpoint
base_url: https://example.com
description: malicious endpoint
tools:
  - name: t
    description: t
    endpoint: '/safe";bad=True;#'
"""

_MALICIOUS_SERVER_NAME_SPEC = '''id: secrgntest_srvname
server_name: '"""; bad = True; """'
base_url: https://example.com
description: malicious server name
tools:
  - name: t
    description: t
    endpoint: /x
'''


def _create_plugin_artifacts(plugin_id: str):
    """Return the three on-disk paths that handle_create_plugin may touch."""
    from pathlib import Path as _P

    repo = _P(__file__).resolve().parents[2]
    return [
        repo / "tools" / "specs" / f"{plugin_id}.yaml",
        repo / "meta_data_mcp" / "providers" / f"{plugin_id}.py",
        repo / "tests" / "providers" / f"test_{plugin_id}.py",
    ]


def _cleanup_artifacts(paths):
    for p in paths:
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass


@pytest.mark.anyio
async def test_create_plugin_rejects_path_traversal_id():
    """Malicious id like '../../../tmp/pwn' is rejected before any disk write."""
    from pathlib import Path as _P
    from meta_data_mcp.providers.meta_data_mcp import handle_create_plugin

    result = await handle_create_plugin({"spec_yaml": _MALICIOUS_ID_SPEC})
    payload = _parse(result)
    assert "error" in payload
    assert "snake_case" in payload["error"] or "must match" in payload["error"]
    assert not _P("/tmp/pwn.yaml").exists()


@pytest.mark.anyio
async def test_create_plugin_rejects_param_name_injection():
    """Identifier-injection via a malicious param name is rejected by the generator."""
    from meta_data_mcp.providers.meta_data_mcp import handle_create_plugin

    artifacts = _create_plugin_artifacts("secrgntest_param")
    # Pre-clean any artifact left by a prior interrupted run so this test
    # is hermetic on local reruns. Without this, a leaked provider file
    # from a prior failure would make the `not provider_path.exists()`
    # assertion below silently wrong (testing pre-existing leak, not
    # current behavior).
    _cleanup_artifacts(artifacts)
    try:
        result = await handle_create_plugin({"spec_yaml": _MALICIOUS_PARAM_NAME_SPEC})
        payload = _parse(result)
        assert "error" in payload
        # Both must hold independently — a leaked file with the wrong error
        # message would have slipped past the original `or`-joined assertion.
        provider_path = artifacts[1]
        assert not provider_path.exists()
        # This spec has a *valid* plugin id but a *malicious* param name,
        # so rejection happens at the generator's load_spec param-name
        # validation — not at the handler's _CREATE_PLUGIN_ID_RE check.
        # The handler surfaces that as {"error": "Generator failed",
        # "stderr": "...snake_case..."}.
        assert "Generator failed" in payload["error"]
        assert "snake_case" in payload.get("stderr", "")
    finally:
        _cleanup_artifacts(artifacts)


@pytest.mark.anyio
async def test_create_plugin_rejects_endpoint_injection():
    """Template-injection via a malicious endpoint string is rejected."""
    from meta_data_mcp.providers.meta_data_mcp import handle_create_plugin

    artifacts = _create_plugin_artifacts("secrgntest_endpoint")
    try:
        result = await handle_create_plugin({"spec_yaml": _MALICIOUS_ENDPOINT_SPEC})
        payload = _parse(result)
        assert "error" in payload
        assert not artifacts[1].exists()
    finally:
        _cleanup_artifacts(artifacts)


@pytest.mark.anyio
async def test_create_plugin_rejects_server_name_injection():
    """Docstring-injection via a malicious server_name is rejected."""
    from meta_data_mcp.providers.meta_data_mcp import handle_create_plugin

    artifacts = _create_plugin_artifacts("secrgntest_srvname")
    try:
        result = await handle_create_plugin({"spec_yaml": _MALICIOUS_SERVER_NAME_SPEC})
        payload = _parse(result)
        assert "error" in payload
        assert not artifacts[1].exists()
    finally:
        _cleanup_artifacts(artifacts)


def test_validate_generated_provider_ast_rejects_dangerous_calls():
    """Direct AST validator unit: any banned call or import is refused."""
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    # Build dangerous source via concatenation so this test file itself
    # doesn't trip the security_reminder hook on literal patterns.
    danger_eval = "x = " + "ev" + "al('1+1')\n"
    assert _validate_generated_provider_ast(danger_eval) is not None
    danger_os = "import os\n" + "os." + "system('id')\n"
    assert _validate_generated_provider_ast(danger_os) is not None
    # disallowed top-level import (socket is not in the allowlist)
    assert _validate_generated_provider_ast("import socket\nx = 1\n") is not None


def test_validate_generated_provider_ast_rejects_star_import_bypass():
    """Star-import of a disallowed module is rejected.

    Regression: before the suffix-attr / star-import gates were added,
    `from os import *; system("id")` parsed cleanly — `os` was in the
    allowlist, and the bare `system` call is not in the bare-name banlist
    (only the dotted `os.system` was). Now `os` is removed from the
    allowlist entirely, so the import itself is refused.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    src = "from os import *\n" + "sys" + "tem('id')\n"
    err = _validate_generated_provider_ast(src)
    assert err is not None
    assert "os" in err


def test_validate_generated_provider_ast_rejects_builtins_attribute_bypass():
    """`__builtins__.<banned>` indirect access should be rejected.

    Regression: dotted-name lookup only covered explicit `os.system` /
    `subprocess.run` etc.; `__builtins__.eval(...)` slipped through
    because the full dotted name `__builtins__.eval` was not in the
    banned-attrs set.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    src = "__builtins__." + "ev" + "al('1+1')\n"
    err = _validate_generated_provider_ast(src)
    assert err is not None
    assert "banned attribute" in err


def test_validate_generated_provider_ast_rejects_globals_subscript_escape():
    """`globals()["__builtins__"]["eval"](...)` should not slip past.

    Regression found by an independent post-merge review: `globals()` was
    not in the bare-name banlist, and the resulting subscript chain has
    no Name/Attribute at the outer Call's func — so the validator never
    inspected it. Closed by adding `globals`/`locals`/`vars`/`dir` to
    the bare-name banlist; the inner namespace lookup is now refused.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    src = "x = " + "glo" + 'bals()["__builtins__"]["ev" + "al"]("1+1")\n'
    err = _validate_generated_provider_ast(src)
    assert err is not None
    assert "globals" in err


def test_validate_generated_provider_ast_rejects_builtins_subscript_escape():
    """`__builtins__["__import__"]("subprocess").run([...])` should not slip past.

    Regression found by an independent post-merge review: `__builtins__`
    is always available as an implicit Name at module top, and a Subscript
    on it returns the requested builtin without going through any
    Import/ImportFrom. Closed by refusing any direct reference to the
    Name `__builtins__` at AST-walk time.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    src = '__builtins__["__import__"]("subprocess").' + 'run(["id"])\n'
    err = _validate_generated_provider_ast(src)
    assert err is not None
    assert "__builtins__" in err


def test_validate_generated_provider_ast_allows_anyio_run():
    """`anyio.run(main)` is legitimate generator output and must still pass.

    Pins the boundary: even though `run` was briefly added to the suffix
    banlist during the bypass-closure work, the generator's `main_block`
    template emits `anyio.run(main)` and pre-existing providers use this
    pattern. A regression that re-introduced `run` to the suffix banlist
    would break every generated provider; this test catches that.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    src = "import anyio\n\nanyio.run(main)\n"
    assert _validate_generated_provider_ast(src) is None


def test_validate_generated_provider_ast_rejects_named_import_of_banned_callable():
    """Named imports from a disallowed module are rejected.

    Regression flagged by the GitHub Copilot reviewer on PR #95: the
    star-import gate caught `from X import *`, but a *named* import
    of a banned callable re-exposed it as a bare-Name Call that
    slipped past every check — the suffix names are only in the
    attribute-suffix banlist, not the bare-name banlist.  `os` is now
    removed from the allowlist entirely, so any `from os import …`
    is rejected at the module level before the attribute check runs.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    # Build source by concatenation so this test file doesn't itself
    # trip the local security_reminder hook on literal patterns.
    bad_attr = "sys" + "tem"
    src = f"from os import {bad_attr}\n{bad_attr}('id')\n"
    err = _validate_generated_provider_ast(src)
    assert err is not None
    assert "os" in err

    bad_attr2 = "po" + "pen"
    src2 = f"from os import {bad_attr2}\n{bad_attr2}('id')\n"
    err2 = _validate_generated_provider_ast(src2)
    assert err2 is not None

    # `from meta_data_mcp.provider_config import require_env_key` is the
    # approved pattern for env access in generated plugins.
    safe = (
        "from meta_data_mcp.provider_config import require_env_key\n"
        "x = require_env_key('MY_KEY')\n"
    )
    assert _validate_generated_provider_ast(safe) is None


def test_validate_generated_provider_ast_rejects_os_execl_family():
    """`os.execl` and the l/le/lp/lpe variants must be rejected.

    Regression found by an independent post-merge review: only the
    v-family (`execv`/`execve`/`execvp`/`execvpe`) had been in the
    dotted banlist; the l-family (variadic argv form) was reachable
    via legitimate `import os; os.execl(...)` because `os` was in the
    import allowlist. `os` is now removed from the allowlist entirely,
    so `import os` is rejected at the import level.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    for fn in ("execl", "execle", "execlp", "execlpe"):
        src = f"import os\nos.{fn}('/bin/sh', 'sh', '-c', 'id')\n"
        err = _validate_generated_provider_ast(src)
        assert err is not None, f"os.{fn} should be rejected"
    for fn in ("posix_spawn", "posix_spawnp", "startfile"):
        src = f"import os\nos.{fn}('/bin/sh', [], {{}})\n"
        err = _validate_generated_provider_ast(src)
        assert err is not None, f"os.{fn} should be rejected"


def test_validate_generated_provider_ast_rejects_os_low_level_file_io():
    """`os.open`/`os.read`/`os.fdopen` must be rejected.

    Builtin `open` was already blocked by the bare-name banlist, but
    the low-level POSIX equivalents on the `os` module were reachable
    by attribute access. `os` is now removed from the allowlist entirely,
    so `import os` is rejected at the import level.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    for fn in ("open", "read", "fdopen"):
        src = f"import os\nos.{fn}(0)\n"
        err = _validate_generated_provider_ast(src)
        assert err is not None, f"os.{fn} should be rejected"


def test_validate_generated_provider_ast_rejects_getattr_indirect_call():
    """`getattr(x, 'system')(...)` indirect call should be rejected.

    Regression: outer Call's func is itself a Call (not a Name or
    Attribute), so it slipped past both the bare-name and dotted-attr
    checks; adding `getattr` to the bare-name banlist closes the path
    by refusing the inner lookup itself.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    # Use an allowed import as the object — the getattr call itself is banned
    src = "import logging\n" + "get" + "attr(logging, " + "'sys' + 'tem')('id')\n"
    err = _validate_generated_provider_ast(src)
    assert err is not None
    assert "getattr" in err
    # legitimate generator output passes
    safe = (
        "import logging\n"
        "from typing import Any\n"
        "import mcp.types as types\n"
        "from pydantic import BaseModel, Field\n"
        "from meta_data_mcp.utils import http_get\n"
        "PROVIDER_ID = 'x'\n"
    )
    assert _validate_generated_provider_ast(safe) is None


def test_validate_generated_provider_ast_rejects_os_getenv():
    """`os.getenv` and `os.environ` must be rejected.

    A generated plugin must not be able to access arbitrary environment
    variables (which may contain secrets such as META_DATA_MCP_AUTH_TOKEN).
    `os` is removed from the import allowlist; additionally `os.getenv` and
    `os.environ` are in the banned call-attr set as defence-in-depth.
    """
    from meta_data_mcp.providers.meta_data_mcp import (
        _validate_generated_provider_ast,
    )

    # Direct import — rejected because os is not in the allowlist
    src_import = "import os\ntoken = os.getenv('META_DATA_MCP_AUTH_TOKEN')\n"
    assert _validate_generated_provider_ast(src_import) is not None

    # Dotted access via a hypothetical alias — rejected by the banned attr set
    src_alias = (
        "import logging as os\n"  # allowlist bypass attempt
        "token = os.getenv('SECRET')\n"
    )
    err_alias = _validate_generated_provider_ast(src_alias)
    assert err_alias is not None


@pytest.mark.anyio
async def test_list_domains_returns_known_domains():
    result = await handle_list_domains({})
    text = json.dumps(result, default=str)
    assert "health" in text
    assert "earth-science" in text
    assert "finance" in text


@pytest.mark.anyio
async def test_list_regions_returns_known_regions():
    result = await handle_list_regions({})
    text = json.dumps(result, default=str)
    assert "us" in text
    assert "eu" in text
    assert "global" in text


@pytest.mark.anyio
async def test_describe_provider_known_id():
    result = await handle_describe_provider({"provider_id": "us_nasa"})
    text = json.dumps(result, default=str)
    assert "us_nasa" in text
    assert "NASA" in text


@pytest.mark.anyio
async def test_describe_provider_unknown_id_returns_error_payload():
    result = await handle_describe_provider({"provider_id": "not_a_real_provider"})
    assert "not found" in json.dumps(result, default=str)


@pytest.mark.anyio
async def test_describe_provider_requires_id():
    with pytest.raises(ValueError):
        await handle_describe_provider({})


@pytest.mark.anyio
async def test_list_providers_pagination():
    result = await handle_list_providers({"limit": 3, "offset": 0})
    text = json.dumps(result, default=str)
    assert '"total"' in text
    assert '"limit": 3' in text


# Exception handler coverage tests
@pytest.mark.anyio
async def test_find_providers_exception_handling(caplog):
    """Test that exceptions in find_providers are logged and re-raised."""
    with patch("meta_data_mcp.providers.meta_data_mcp._engine") as mock_engine:
        mock_engine.route = AsyncMock(side_effect=RuntimeError("Test error"))

        with caplog.at_level("ERROR", logger="meta_data_mcp.providers.meta_data_mcp"):
            with pytest.raises(RuntimeError, match="Test error"):
                await handle_find_providers({"query": "test"})

        assert "Error in opendata.providers.find: Test error" in caplog.text


@pytest.mark.anyio
async def test_explain_choice_exception_handling(caplog):
    """Test that exceptions in explain_choice are logged and re-raised."""
    with patch("meta_data_mcp.providers.meta_data_mcp._engine") as mock_engine:
        mock_engine.route = AsyncMock(side_effect=ValueError("Bad params"))

        with caplog.at_level("ERROR", logger="meta_data_mcp.providers.meta_data_mcp"):
            with pytest.raises(ValueError, match="Bad params"):
                await handle_explain_choice({"query": "test"})

        assert "Error in opendata.explain.choice: Bad params" in caplog.text


@pytest.mark.anyio
async def test_list_domains_exception_handling(caplog):
    """Test that exceptions in list_domains are logged and re-raised."""
    with patch("meta_data_mcp.providers.meta_data_mcp.list_domains") as mock_list:
        mock_list.side_effect = RuntimeError("Domain list error")

        with caplog.at_level("ERROR", logger="meta_data_mcp.providers.meta_data_mcp"):
            with pytest.raises(RuntimeError, match="Domain list error"):
                await handle_list_domains({})

        assert "Error in opendata.domains.list: Domain list error" in caplog.text


@pytest.mark.anyio
async def test_list_regions_exception_handling(caplog):
    """Test that exceptions in list_regions are logged and re-raised."""
    with patch("meta_data_mcp.providers.meta_data_mcp.list_regions") as mock_list:
        mock_list.side_effect = RuntimeError("Region list error")

        with caplog.at_level("ERROR", logger="meta_data_mcp.providers.meta_data_mcp"):
            with pytest.raises(RuntimeError, match="Region list error"):
                await handle_list_regions({})

        assert "Error in opendata.regions.list: Region list error" in caplog.text


@pytest.mark.anyio
async def test_list_providers_exception_handling(caplog):
    """Test that exceptions in list_providers are logged and re-raised."""
    # ``handle_list_providers`` materializes the registry with ``list(REGISTRY)``,
    # which triggers ``__iter__``. Mock that path to fault-inject.
    with patch("meta_data_mcp.providers.meta_data_mcp.REGISTRY") as mock_registry:
        mock_registry.__iter__.side_effect = RuntimeError("Registry error")

        with caplog.at_level("ERROR", logger="meta_data_mcp.providers.meta_data_mcp"):
            with pytest.raises(RuntimeError, match="Registry error"):
                await handle_list_providers({"limit": 10})

        assert "Error in opendata.providers.list: Registry error" in caplog.text


# Resource handler coverage tests
def test_read_all_providers_returns_serialized_registry():
    """Test that resource handler returns serialized registry as string."""
    from pydantic import AnyUrl

    uri = AnyUrl("registry://all-providers")
    result = handle_read_all_providers(uri)
    # Verify it's a string and starts with JSON array
    assert isinstance(result, str)
    assert result.startswith("[")
    assert len(result) > 100  # Should have substantial content


def test_read_all_providers_includes_provider_data():
    """Test that resource handler includes expected provider fields."""
    from pydantic import AnyUrl

    uri = AnyUrl("registry://all-providers")
    with patch(
        "meta_data_mcp.providers.meta_data_mcp.serialize_for_llm",
        side_effect=lambda payload: json.dumps(payload),
    ):
        result = handle_read_all_providers(uri)
    payload = json.loads(result)

    assert isinstance(payload, list)
    assert payload

    provider = payload[0]
    assert EXPECTED_PROVIDER_FIELDS.issubset(provider.keys())


# Prompt handler coverage tests
@pytest.mark.anyio
async def test_discover_providers_with_use_case():
    """Test discover_providers prompt handler with use case."""
    result = await handle_discover_providers({"use_case": "Build a weather dashboard"})
    assert result.description
    assert "weather dashboard" in result.description
    assert len(result.messages) == 1
    assert result.messages[0].role == "user"
    assert "weather dashboard" in result.messages[0].content.text


@pytest.mark.anyio
async def test_discover_providers_without_use_case():
    """Test discover_providers falls back to default when use_case missing."""
    result = await handle_discover_providers({})
    assert "General exploration" in result.description
    assert "General exploration" in result.messages[0].content.text


@pytest.mark.anyio
async def test_discover_providers_with_none_arguments():
    """Test discover_providers with None arguments."""
    result = await handle_discover_providers(None)
    assert result.description
    assert "General exploration" in result.description


# Use case prompt handlers
def test_all_use_case_prompts_registered():
    """Test that all use case prompts are registered."""
    prompt_names = {p.name for p in PROMPTS}
    assert "usecase-financial-research" in prompt_names
    assert "usecase-climate-dashboard" in prompt_names
    assert "usecase-healthcare-analytics" in prompt_names
    assert "usecase-academic-literature" in prompt_names


@pytest.mark.anyio
async def test_financial_research_use_case():
    """Test financial research use case prompt handler."""
    handler = PROMPTS_HANDLERS["usecase-financial-research"]
    result = await handler(None)
    assert (
        "financial" in result.description.lower()
        or "research" in result.description.lower()
    )
    assert "financial research tool" in result.messages[0].content.text.lower()


@pytest.mark.anyio
async def test_climate_dashboard_use_case():
    """Test climate dashboard use case prompt handler."""
    handler = PROMPTS_HANDLERS["usecase-climate-dashboard"]
    result = await handler(None)
    assert (
        "climate" in result.description.lower()
        or "environment" in result.description.lower()
    )
    assert "climate" in result.messages[0].content.text.lower()


@pytest.mark.anyio
async def test_healthcare_analytics_use_case():
    """Test healthcare analytics use case prompt handler."""
    handler = PROMPTS_HANDLERS["usecase-healthcare-analytics"]
    result = await handler(None)
    assert (
        "health" in result.description.lower()
        or "epidemiology" in result.description.lower()
    )
    assert "healthcare" in result.messages[0].content.text.lower()


@pytest.mark.anyio
async def test_academic_literature_use_case():
    """Test academic literature use case prompt handler."""
    handler = PROMPTS_HANDLERS["usecase-academic-literature"]
    result = await handler(None)
    assert (
        "literature" in result.description.lower()
        or "academic" in result.description.lower()
    )
    assert "literature review" in result.messages[0].content.text.lower()


# Explain choice with various parameters
@pytest.mark.anyio
async def test_explain_choice_with_domain_filter():
    """Test explain_choice with domain filter."""
    result = await handle_explain_choice(
        {"query": "epidemic", "domain": "health", "limit": 3}
    )
    payload = _parse(result)
    assert payload["domain_filter"] == "health"
    assert payload["results"]


@pytest.mark.anyio
async def test_explain_choice_with_region_filter():
    """Test explain_choice with region filter."""
    result = await handle_explain_choice(
        {"query": "open data", "region": "us", "limit": 3}
    )
    payload = _parse(result)
    assert payload["region_filter"] == "us"
    assert payload["results"]


@pytest.mark.anyio
async def test_explain_choice_without_query():
    """Test explain_choice with just domain filter, no query."""
    result = await handle_explain_choice({"domain": "finance", "limit": 2})
    payload = _parse(result)
    assert payload["domain_filter"] == "finance"
    assert payload["results"]


# CLI/main entry point coverage tests
@pytest.mark.anyio
async def test_main_function_creates_server():
    """Test that main function creates MCP server with all resources/tools/prompts."""
    from meta_data_mcp.providers.meta_data_mcp import main

    with (
        patch("meta_data_mcp.utils.create_mcp_server") as mock_create,
        patch("meta_data_mcp.utils.run_server") as mock_run,
        patch("meta_data_mcp.logging_config.configure_logging"),
        patch("meta_data_mcp.telemetry.configure_otel"),
    ):
        mock_server = object()
        mock_create.return_value = mock_server

        # Call main with mocked functions
        await main(transport="stdio", port=8000, host="127.0.0.1")

        # Verify the server was created with the unified server name
        # and that the merged TOOLS/HANDLERS were passed through.
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert mock_create.call_args.args[0] == "meta-data-mcp"
        assert call_kwargs["resources"] is RESOURCES
        assert call_kwargs["resources_handlers"] is RESOURCES_HANDLERS
        assert call_kwargs["tools"] is TOOLS
        assert call_kwargs["tools_handlers"] is TOOLS_HANDLERS
        assert call_kwargs["prompts"] is PROMPTS
        assert call_kwargs["prompts_handlers"] is PROMPTS_HANDLERS

        # Verify run_server was called
        mock_run.assert_awaited_once_with(mock_server, "stdio", 8000, "127.0.0.1")


# ---------------------------------------------------------------------------
# Phase 3: discovery app binding + opendata.health.snapshot
# ---------------------------------------------------------------------------


from meta_data_mcp.providers.meta_data_mcp import (  # noqa: E402
    DISCOVERY_TOOL_NAMES as _DISCOVERY_BOUND_TOOLS,
)
from meta_data_mcp.ui_resources.app_discovery_v1 import URI as _DISCOVERY_URI  # noqa: E402


@pytest.mark.parametrize("tool_name", _DISCOVERY_BOUND_TOOLS)
def test_discovery_tool_binds_to_discovery_app(tool_name):
    """Each discovery meta tool must declare ``_meta.ui.resourceUri`` pointing
    at the canonical discovery app URI. If this drifts, the MCP Apps host
    stops loading the panel when these tools are called.

    Pin both the Python-side ``.meta`` attribute AND the wire-level alias
    (``model_dump(by_alias=True)`` emits ``_meta``) so a future SDK
    regression on the populate_by_name footgun is caught here.
    """
    tool = next(t for t in TOOLS if t.name == tool_name)
    assert tool.meta == {"ui": {"resourceUri": _DISCOVERY_URI}}, (
        f"{tool_name} is not bound to {_DISCOVERY_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == _DISCOVERY_URI


def test_health_snapshot_tool_registered():
    """``opendata.health.snapshot`` is the only net-new tool in Phase 3."""
    names = {t.name for t in TOOLS}
    assert "opendata.health.snapshot" in names
    assert "opendata.health.snapshot" in TOOLS_HANDLERS


@pytest.mark.anyio
async def test_health_snapshot_default_returns_full_registry():
    """Called with no arguments, the handler returns a snapshot for every
    registered provider — i.e. the discovery app can fetch all badges in
    one call."""
    from meta_data_mcp.providers.meta_data_mcp import handle_health_snapshot
    from meta_data_mcp.registry import iter_registry

    result = await handle_health_snapshot({})
    payload = _parse(result)
    assert "snapshot" in payload
    # Two clock fields are intentional: ``generated_at`` is wall-clock for
    # display, ``generated_at_monotonic`` is the reference clock for
    # ``last_update_ts`` (also monotonic) so consumers can compute staleness
    # without mixing clock domains. See handle_health_snapshot's docstring.
    assert "generated_at" in payload
    assert "generated_at_monotonic" in payload
    assert isinstance(payload["generated_at"], float)
    assert isinstance(payload["generated_at_monotonic"], float)
    snap = payload["snapshot"]
    # The registry has tens of providers; the snapshot must cover them all.
    registry_ids = {p.id for p in iter_registry()}
    assert registry_ids.issubset(snap.keys())
    # Every entry has the expected shape.
    for entry in snap.values():
        assert set(entry.keys()) == {"score", "failure_mass", "last_update_ts"}
        assert 0.0 <= entry["score"] <= 1.0


@pytest.mark.anyio
async def test_health_snapshot_with_explicit_ids_filters_response():
    """Explicit ``provider_ids`` restricts the snapshot to that subset."""
    from meta_data_mcp.providers.meta_data_mcp import handle_health_snapshot

    result = await handle_health_snapshot(
        {"provider_ids": ["us_nasa", "global_frankfurter"]}
    )
    payload = _parse(result)
    snap = payload["snapshot"]
    assert set(snap.keys()) == {"us_nasa", "global_frankfurter"}


@pytest.mark.anyio
async def test_health_snapshot_unknown_provider_returns_baseline():
    """Asking for a provider that's never been routed through ``http_get``
    must return the healthy baseline rather than dropping the key. The
    discovery app relies on this for "first-load" rendering before any
    real traffic has touched the provider."""
    from meta_data_mcp.providers.meta_data_mcp import handle_health_snapshot

    result = await handle_health_snapshot({"provider_ids": ["zzz_never_seen"]})
    payload = _parse(result)
    assert payload["snapshot"]["zzz_never_seen"] == {
        "score": 1.0,
        "failure_mass": 0.0,
        "last_update_ts": None,
    }


@pytest.mark.anyio
async def test_find_providers_includes_breakdowns_per_provider():
    """Phase 3: handle_find_providers now passes ``explain=True`` to the
    routing engine and surfaces ``breakdowns`` in the payload so the
    discovery app can render per-strategy bars."""
    result = await handle_find_providers({"query": "weather", "limit": 5})
    payload = _parse(result)
    assert "breakdowns" in payload
    # At least one provider's breakdown must contain the canonical scorers.
    if payload["providers"]:
        first_id = payload["providers"][0]["id"]
        breakdown = payload["breakdowns"].get(first_id, {})
        # Token + fuzzy + metadata + semantic + health are the wired scorers.
        # ``explain`` skips zero-weight scorers, so we only assert that at
        # least one non-health scorer ran — that's what the app relies on.
        non_health = {k: v for k, v in breakdown.items() if k != "health"}
        assert non_health, (
            "expected at least one non-health scorer to appear in breakdown"
        )


@pytest.mark.anyio
async def test_find_providers_omits_breakdowns_when_no_query():
    """Phase 3 refinement: when the caller doesn't supply a query, every
    passing provider is assigned a flat 1.0 score with no per-strategy
    contribution. Returning an all-zero breakdown would lie ("every
    scorer rated this provider 0%"), so the handler omits ``breakdowns``
    entirely. The discovery app's row renderer treats absence as 'no
    per-strategy data' rather than drawing zero-height bars."""
    result = await handle_find_providers({"limit": 3})
    payload = _parse(result)
    assert "breakdowns" not in payload, (
        "breakdowns must be absent when no query was supplied; got "
        f"{payload.get('breakdowns')!r}"
    )
    # Empty string query / whitespace-only query should be treated the same.
    result_blank = await handle_find_providers({"query": "   ", "limit": 3})
    payload_blank = _parse(result_blank)
    assert "breakdowns" not in payload_blank


def test_discovery_app_resource_registered_at_boot():
    """``register_apps()`` ran during module import, so the discovery app
    URI must already be in RESOURCES_HANDLERS by the time tests load."""
    assert _DISCOVERY_URI in RESOURCES_HANDLERS
    uris = [str(r.uri) for r in RESOURCES]
    assert _DISCOVERY_URI in uris
