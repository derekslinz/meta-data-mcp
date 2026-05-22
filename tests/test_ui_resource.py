"""Tests for the MCP Apps (`ui://`) resource foundation.

Covers the v2.0 Phase 1 surface:

- ``ui://`` scheme validation through ``pydantic.AnyUrl`` (Gotcha G8).
- ``register_ui_resource()`` end-to-end: appends to ``RESOURCES``, wires a
  handler that returns the HTML, idempotency, collision detection.
- ``mcp.types.Tool``'s ``_meta`` field round-trips correctly to the wire.
  The MCP Apps spec ships UI hints under ``_meta.ui.resourceUri``. In the
  installed SDK the field is named ``meta`` with alias ``_meta`` but
  ``populate_by_name`` is NOT enabled — so the *only* construct-time way
  to set it is to pass the alias (``_meta=``) or assign after
  construction. Passing ``meta=`` silently lands in extras and never
  reaches the host. These tests lock that behavior down so we catch any
  SDK regression that breaks it.

Together these cover the kernel-side foundation; per-shape primitive bundles
and provider wiring land in later phases.
"""

from __future__ import annotations

import pytest
from mcp import types
from pydantic import AnyUrl

from meta_data_mcp.utils import create_mcp_server, register_ui_resource


# ---------------------------------------------------------------------------
# Gotcha G8: ui:// must validate as a pydantic AnyUrl
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri,expected_host,expected_path",
    [
        ("ui://meta-data-mcp/discovery", "meta-data-mcp", "/discovery"),
        (
            "ui://meta-data-mcp/shape/timeseries/v1",
            "meta-data-mcp",
            "/shape/timeseries/v1",
        ),
        ("ui://discovery", "discovery", None),
    ],
)
def test_ui_scheme_validates_as_anyurl(uri: str, expected_host: str, expected_path):
    parsed = AnyUrl(uri)
    assert parsed.scheme == "ui"
    assert parsed.host == expected_host
    assert parsed.path == expected_path


# ---------------------------------------------------------------------------
# register_ui_resource()
# ---------------------------------------------------------------------------


def _fresh_state():
    """Return clean (resources_list, resources_handlers_dict) like a server boot."""
    return [], {}


def test_register_ui_resource_appends_resource_and_handler():
    resources, handlers = _fresh_state()
    uri = register_ui_resource(
        name="discovery",
        html="<html><body><h1>discovery</h1></body></html>",
        description="Faceted provider discovery UI.",
        resources=resources,
        resources_handlers=handlers,
    )
    assert uri == "ui://meta-data-mcp/discovery"
    assert len(resources) == 1
    res = resources[0]
    assert str(res.uri) == uri
    assert res.name == "discovery"
    assert res.description.startswith("Faceted")
    # Default MIME is the MCP Apps profile string — hosts reject plain
    # ``text/html`` with "Unsupported UI resource content format".
    assert res.mimeType == "text/html;profile=mcp-app"
    # Handler is registered under the string URI form (matches what
    # create_mcp_server's resource handler key-lookup uses, see utils.py).
    assert uri in handlers


def test_register_ui_resource_returns_html_via_handler():
    resources, handlers = _fresh_state()
    html = "<!doctype html><h1>hello</h1>"
    uri = register_ui_resource(
        name="hello",
        html=html,
        description="smoke",
        resources=resources,
        resources_handlers=handlers,
    )
    assert handlers[uri](AnyUrl(uri)) == html


def test_register_ui_resource_with_path_segments():
    """Shape primitives use slash-segmented names (e.g. 'shape/timeseries/v1')."""
    resources, handlers = _fresh_state()
    uri = register_ui_resource(
        name="shape/timeseries/v1",
        html="<html></html>",
        description="shape",
        resources=resources,
        resources_handlers=handlers,
    )
    assert uri == "ui://meta-data-mcp/shape/timeseries/v1"
    # AnyUrl must still parse it cleanly so the kernel's resource lookup works.
    parsed = AnyUrl(uri)
    assert parsed.path == "/shape/timeseries/v1"


def test_register_ui_resource_strips_leading_slash_in_name():
    resources, handlers = _fresh_state()
    uri = register_ui_resource(
        name="/discovery",
        html="<html></html>",
        description="x",
        resources=resources,
        resources_handlers=handlers,
    )
    assert uri == "ui://meta-data-mcp/discovery"


def test_register_ui_resource_rejects_empty_name():
    resources, handlers = _fresh_state()
    with pytest.raises(ValueError, match="non-empty"):
        register_ui_resource(
            name="",
            html="<html/>",
            description="x",
            resources=resources,
            resources_handlers=handlers,
        )


def test_register_ui_resource_rejects_collision():
    resources, handlers = _fresh_state()
    register_ui_resource(
        name="discovery",
        html="<html>v1</html>",
        description="first",
        resources=resources,
        resources_handlers=handlers,
    )
    with pytest.raises(ValueError, match="already registered"):
        register_ui_resource(
            name="discovery",
            html="<html>v2</html>",
            description="second",
            resources=resources,
            resources_handlers=handlers,
        )


def test_register_ui_resource_custom_server_and_mime():
    resources, handlers = _fresh_state()
    uri = register_ui_resource(
        name="probe",
        html="<svg/>",
        description="x",
        resources=resources,
        resources_handlers=handlers,
        server_name="alt-server",
        mime="image/svg+xml",
    )
    assert uri == "ui://alt-server/probe"
    assert resources[0].mimeType == "image/svg+xml"


# ---------------------------------------------------------------------------
# Tool's _meta field round-trips correctly to the wire (the MCP Apps contract).
#
# The Tool model has a `meta` field aliased to `_meta` on the wire. Crucially,
# `populate_by_name` is NOT enabled, so callers MUST pass the alias
# (`_meta={...}`) at construction time — passing `meta={...}` silently lands
# in extras (Tool.model_config["extra"] == "allow") and never reaches the
# host as `_meta`. These tests pin both ends of the contract.
# ---------------------------------------------------------------------------


def test_tool_underscore_meta_round_trips_to_wire():
    """Constructing with the alias keyword (`_meta=`) serializes to `_meta`."""
    app_uri = "ui://meta-data-mcp/discovery"
    tool = types.Tool(
        name="opendata.providers.find",
        description="Find providers.",
        inputSchema={"type": "object"},
        _meta={"ui": {"resourceUri": app_uri}},
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert "_meta" in wire, (
        "Tool's _meta did not appear on the wire. The MCP Apps extension "
        "relies on this — if pydantic's by_alias behavior changed, the "
        "tool's UI metadata stops reaching the host."
    )
    assert wire["_meta"]["ui"]["resourceUri"] == app_uri
    # The Python attribute name (`meta`) must NOT leak into the wire payload.
    assert "meta" not in wire


def test_tool_meta_via_field_assignment_round_trips_to_wire():
    """Assigning `tool.meta = ...` after construction also serializes to
    `_meta`. This is the alternative wiring pattern providers may use."""
    app_uri = "ui://meta-data-mcp/discovery"
    tool = types.Tool(
        name="opendata.providers.find",
        description="Find providers.",
        inputSchema={"type": "object"},
    )
    tool.meta = {"ui": {"resourceUri": app_uri}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert "_meta" in wire
    assert wire["_meta"]["ui"]["resourceUri"] == app_uri
    assert "meta" not in wire


def test_tool_meta_constructor_kwarg_does_not_reach_wire():
    """REGRESSION: passing `meta={...}` to the constructor silently drops
    the value into extras (Tool.model_config['extra'] == 'allow') and
    NEVER reaches the wire as `_meta`. The SDK currently doesn't enable
    `populate_by_name`, so `meta=` is a footgun. If this test ever fails
    — i.e. `_meta` appears in the wire from a `meta=` kwarg — the SDK has
    enabled `populate_by_name` and our docs / provider template should
    switch to the more natural `meta=` pattern."""
    app_uri = "ui://meta-data-mcp/discovery"
    tool = types.Tool(
        name="x",
        description="y",
        inputSchema={"type": "object"},
        meta={"ui": {"resourceUri": app_uri}},  # type: ignore[call-arg]
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert "_meta" not in wire, (
        "Tool now accepts `meta=` as a constructor kwarg and serializes "
        "it to `_meta` — the SDK has enabled populate_by_name. Update "
        "the v2.0 docs / provider template to use the simpler `meta=` "
        "pattern."
    )


def test_tool_without_meta_omits_underscore_meta():
    """Tools that don't declare meta should not emit a stray `_meta` key."""
    tool = types.Tool(
        name="plain-tool",
        description="No UI.",
        inputSchema={"type": "object"},
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert "_meta" not in wire
    assert "meta" not in wire


# ---------------------------------------------------------------------------
# End-to-end: a ui:// resource registered via register_ui_resource() is
# served by the same create_mcp_server() resource handler we already test
# elsewhere. No new plumbing required.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ui_resource_is_served_by_create_mcp_server():
    """A registered ui:// resource is reachable via the server's
    read_resource dispatch. Smoke test that the existing
    ``create_mcp_server`` plumbing handles ``ui://`` URIs identically to
    the pre-existing ``registry://`` scheme."""
    resources, handlers = _fresh_state()
    html = "<!doctype html><body>discovery-app</body>"
    uri = register_ui_resource(
        name="discovery",
        html=html,
        description="x",
        resources=resources,
        resources_handlers=handlers,
    )

    server = create_mcp_server(
        "test-ui-resource",
        resources=resources,
        resources_handlers=handlers,
    )

    # Reach into the server's registered handlers the same way the existing
    # provider tests do (e.g. test_meta_data_mcp.py uses the live
    # handler dict).
    # The handler is stored under the string form of the URI.
    served = handlers[uri](AnyUrl(uri))
    assert served == html
    # Sanity: the resource is in the server's catalog.
    assert any(str(r.uri) == uri for r in resources)
    # Server boot didn't blow up.
    assert server.name == "test-ui-resource"


# ---------------------------------------------------------------------------
# Regression: the parallel Phase 2 PR merges (#61/#62/#63) hit a conflict
# resolution that silently dropped the timeseries import from
# ``ui_resources/__init__.py``. The function body still referenced
# ``_register_timeseries``, so ``register_shapes()`` crashed with a
# NameError at server boot — and v1.1.0 shipped from broken main.
#
# This test pins the contract that ``register_shapes()`` actually registers
# every shape in ``__init__.py``'s body, not just the ones whose imports
# happen to be present.
# ---------------------------------------------------------------------------


def test_register_shapes_registers_every_shape_named_in_its_body():
    """If a future merge drops a ``_register_*`` import while leaving the
    call site in the function body, calling ``register_shapes`` raises
    NameError — and this test catches it BEFORE the discovery provider
    import does at server boot."""
    from meta_data_mcp.ui_resources import register_shapes

    resources, handlers = _fresh_state()
    result = register_shapes(resources, handlers)

    # Today's shapes: timeseries, geofeatures, records. If a new shape
    # primitive lands without updating this assertion, that's a signal to
    # add it explicitly here so the regression check stays honest.
    expected = {
        "timeseries/v1": "ui://meta-data-mcp/shape/timeseries/v1",
        "geofeatures/v1": "ui://meta-data-mcp/shape/geofeatures/v1",
        "records/v1": "ui://meta-data-mcp/shape/records/v1",
    }
    assert set(result) == set(expected), (
        f"register_shapes returned {sorted(result)}, expected {sorted(expected)}"
    )
    for name, uri in expected.items():
        assert result[name] == uri, f"{name} URI drifted: {result[name]} != {uri}"
        assert uri in handlers, f"{name} handler missing from catalog"


def test_discovery_provider_module_imports_without_crashing():
    """The discovery provider calls ``register_shapes`` at module import.
    If any shape's import is missing or a call signature drifts, this
    import raises and the entire server fails to boot.

    Importing the module here exercises the same code path CI exercises
    on a fresh process — equivalent to the smoke that every other
    ``providers/test_*.py`` indirectly does."""
    import importlib

    # importlib.import_module re-evaluates if not already cached; the
    # first import in the suite would catch this anyway, but the
    # explicit assertion documents the contract.
    mod = importlib.import_module("meta_data_mcp.providers.meta_data_mcp")
    # Sanity: the module exposes the catalog the shape primitives
    # landed in.
    assert any(
        str(r.uri).startswith("ui://meta-data-mcp/shape/") for r in mod.RESOURCES
    ), "discovery provider did not register any ui://meta-data-mcp/shape/* resources"


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Regression: ``read_resource`` MUST propagate the registered Resource's
# mimeType to the wire-level content envelope.
#
# The MCP SDK's ``@server.read_resource()`` decorator wraps a bare ``str`` /
# ``bytes`` return into a ``TextResourceContents`` / ``BlobResourceContents``
# envelope, but it defaults the envelope's ``mimeType`` to ``"text/plain"`` /
# ``"application/octet-stream"`` — *independent* of whatever ``Resource``
# was declared in the catalog. A host reads the envelope's mimeType (not
# the catalog entry's) when deciding how to render. Phase 5 surfaced this
# in the wild: ``ui://`` resources catalogued as ``text/html;profile=mcp-app``
# were being served as ``text/plain``, and the host refused to mount them
# as iframes. A follow-up bug surfaced once the MIME propagated: hosts only
# mount resources whose MIME carries the explicit ``;profile=mcp-app``
# parameter, rejecting plain ``text/html`` with the host-side error
# ``"Unsupported UI resource content format"``. This test pins both fixes.
#
# ``create_mcp_server`` now returns ``Iterable[ReadResourceContents]`` with
# the per-resource ``mime_type`` plumbed through. This test pins that.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_resource_returns_text_html_mime_for_ui_resource():
    """A ``ui://`` resource MUST serve as ``text/html;profile=mcp-app`` on
    the wire. If this drifts back to ``text/plain`` (SDK default) or to
    plain ``text/html`` (no profile parameter), MCP Apps hosts silently
    refuse to render the bundle — the latter fails with
    ``"Unsupported UI resource content format"``."""
    import asyncio  # noqa: F401 — anyio_backend uses asyncio

    resources, handlers = [], {}
    uri = register_ui_resource(
        name="phase5/regression",
        html="<!doctype html><body>regression</body>",
        description="x",
        resources=resources,
        resources_handlers=handlers,
    )
    server = create_mcp_server(
        "test-mime-regression",
        resources=resources,
        resources_handlers=handlers,
        tools=[],
        tools_handlers={},
    )

    req_type = types.ReadResourceRequest
    handler = server.request_handlers[req_type]
    req = req_type(
        method="resources/read",
        params=types.ReadResourceRequestParams(uri=AnyUrl(uri)),
    )
    server_result = await handler(req)

    # server_result is wrapped in ServerResult — peel one layer.
    inner = server_result.root
    assert isinstance(inner, types.ReadResourceResult)
    assert len(inner.contents) == 1, (
        f"expected exactly one content envelope, got {len(inner.contents)}"
    )

    content = inner.contents[0]
    assert isinstance(content, types.TextResourceContents)
    assert content.mimeType == "text/html;profile=mcp-app", (
        f"ui:// resource served as {content.mimeType!r}, expected "
        "'text/html;profile=mcp-app'. The MCP Apps host reads this MIME "
        "(not the catalog entry's) when deciding whether to mount the "
        "bundle in an iframe, and rejects anything without the explicit "
        "'profile=mcp-app' parameter with 'Unsupported UI resource "
        "content format'."
    )
    assert content.text == "<!doctype html><body>regression</body>"


@pytest.mark.anyio
async def test_read_resource_propagates_explicit_non_html_mime():
    """A resource registered with a non-default MIME (e.g. application/json
    for the registry resource) is served with that MIME, not silently
    downgraded to text/plain. Counterpart to the text/html regression —
    ensures the fix didn't hardwire text/html for all UI resources."""
    resources, handlers = [], {}
    json_uri = "registry://test/all"
    resources.append(
        types.Resource(
            uri=AnyUrl(json_uri),
            name="all",
            description="json catalog",
            mimeType="application/json",
        )
    )
    handlers[json_uri] = lambda _u: '{"hello": "world"}'

    server = create_mcp_server(
        "test-non-html-mime",
        resources=resources,
        resources_handlers=handlers,
        tools=[],
        tools_handlers={},
    )
    handler = server.request_handlers[types.ReadResourceRequest]
    req = types.ReadResourceRequest(
        method="resources/read",
        params=types.ReadResourceRequestParams(uri=AnyUrl(json_uri)),
    )
    server_result = await handler(req)
    content = server_result.root.contents[0]
    assert content.mimeType == "application/json"
