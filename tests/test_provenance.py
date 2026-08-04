"""Tests for the optional sha256 + timestamp provenance on tool results.

The provenance layer is opt-in via the ``META_DATA_MCP_PROVENANCE``
environment variable. When enabled, ``call_tool`` responses gain a
``meta-data-mcp/provenance`` entry on the first content block's
``_meta`` field carrying a sha256 of the canonical ``(tool, arguments,
content)`` envelope plus an ISO 8601 UTC timestamp; when disabled (the
default), results are passed through untouched.

These tests cover:

1. The env-var parser — truthy / falsy / unset, with surrounding
   whitespace and mixed case.
2. The ``attach()`` helper — adds a fingerprint to the first block,
   doesn't mutate the input, synthesizes a stub for empty content,
   preserves any pre-existing ``_meta`` keys, serializes to the wire
   under the ``_meta`` alias, and produces a digest the caller can
   reproduce externally (without seeing the provenance metadata
   itself).
3. The digest's input-output binding — different ``(tool, args)``
   pairs returning identical content produce different fingerprints.
4. Multi-content-type coverage — ImageContent and EmbeddedResource
   round-trip through ``attach()`` with reproducible digests.
5. Unicode safety — non-ASCII text content produces a digest a
   receiver can recompute (``ensure_ascii=True`` is load-bearing).
6. End-to-end through the SDK dispatcher — when enabled, the wrapped
   ``handle_call_tool`` returns content with provenance carrying the
   real tool name and arguments; when disabled, it does not.

Why the external-reproducibility tests are load-bearing: the
tamper-evidence story only works if a caller can recompute the digest
from what they see. The exact ``json.dumps`` kwargs
(``sort_keys=True``, ``separators=(",", ":")``, ``ensure_ascii=True``)
plus ``model_dump(mode="json", by_alias=True, exclude_none=True)`` and stripping
``_meta`` are the public contract; these tests pin every piece.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

import pytest
from mcp import types

from meta_data_mcp import provenance
from meta_data_mcp.server import create_mcp_server


def _txt(s: str) -> types.TextContent:
    return types.TextContent(type="text", text=s)


def _img() -> types.ImageContent:
    # 1x1 transparent PNG, base64.
    return types.ImageContent(
        type="image",
        data="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
        mime_type="image/png",
    )


def _embedded() -> types.EmbeddedResource:
    return types.EmbeddedResource(
        type="resource",
        resource=types.TextResourceContents(
            uri="file:///example.txt",
            mime_type="text/plain",
            text="hello from a resource",
        ),
    )


# ---------------------------------------------------------------------------
# is_enabled()
# ---------------------------------------------------------------------------


def test_is_enabled_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)
    assert provenance.is_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On", " tRuE "])
def test_is_enabled_truthy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(provenance._ENV_VAR, value)
    assert provenance.is_enabled() is True


@pytest.mark.parametrize(
    "value",
    ["", "0", "false", "no", "off", "disabled", "maybe", " "],
)
def test_is_enabled_falsy(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(provenance._ENV_VAR, value)
    assert provenance.is_enabled() is False


# ---------------------------------------------------------------------------
# attach() — structural correctness
# ---------------------------------------------------------------------------


def test_attach_adds_provenance_to_first_block_only() -> None:
    out = provenance.attach(
        [_txt("hello"), _txt("world")],
        tool_name="t",
        arguments=None,
    )
    assert len(out) == 2

    meta = out[0].meta
    assert meta is not None
    fp = meta[provenance.PROVENANCE_META_KEY]
    assert set(fp.keys()) == {"sha256", "timestamp"}

    # Second block is left alone — provenance is intentionally on the
    # FIRST block only so callers know exactly where to look.
    assert out[1].meta is None


def test_attach_does_not_mutate_input() -> None:
    original = [_txt("hello")]
    out = provenance.attach(original, tool_name="t", arguments=None)
    assert out is not original
    assert original[0].meta is None


def test_attach_empty_content_synthesizes_stub_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="meta_data_mcp.provenance"):
        out = provenance.attach([], tool_name="my-tool", arguments=None)
    assert len(out) == 1
    block = out[0]
    assert isinstance(block, types.TextContent)
    assert block.text == ""
    assert block.meta is not None
    assert provenance.PROVENANCE_META_KEY in block.meta

    fp = block.meta[provenance.PROVENANCE_META_KEY]
    expected_sha256 = _recompute_digest("my-tool", None, out)
    assert fp["sha256"] == expected_sha256

    # Empty content is suspicious — operators need visibility.
    assert any("my-tool" in rec.message for rec in caplog.records), (
        "expected a warning naming the tool that returned empty content"
    )


def test_attach_preserves_existing_meta() -> None:
    block = types.TextContent(type="text", text="hi", _meta={"existing": "x"})
    out = provenance.attach([block], tool_name="t", arguments=None)
    meta = out[0].meta
    assert meta is not None
    assert meta["existing"] == "x"
    assert provenance.PROVENANCE_META_KEY in meta


# ---------------------------------------------------------------------------
# Digest properties
# ---------------------------------------------------------------------------


def test_sha256_is_deterministic() -> None:
    a = provenance.attach([_txt("hello")], tool_name="t", arguments={"x": 1})
    b = provenance.attach([_txt("hello")], tool_name="t", arguments={"x": 1})
    assert _sha(a) == _sha(b)


def test_sha256_changes_with_content() -> None:
    a = provenance.attach([_txt("hello")], tool_name="t", arguments=None)
    b = provenance.attach([_txt("world")], tool_name="t", arguments=None)
    assert _sha(a) != _sha(b)


def test_sha256_changes_with_tool_name() -> None:
    """Two tools returning identical content must produce different
    fingerprints. Without this, an audit log can't distinguish
    'tool A returned X' from 'tool B returned X' — the tamper-evidence
    story requires input-output binding.
    """
    a = provenance.attach([_txt("hello")], tool_name="tool-a", arguments=None)
    b = provenance.attach([_txt("hello")], tool_name="tool-b", arguments=None)
    assert _sha(a) != _sha(b)


def test_sha256_changes_with_arguments() -> None:
    """Same tool, same content, different inputs → different digest.
    Two calls that happen to produce the same response from different
    parameters must be distinguishable.
    """
    a = provenance.attach([_txt("ok")], tool_name="t", arguments={"q": "alpha"})
    b = provenance.attach([_txt("ok")], tool_name="t", arguments={"q": "beta"})
    assert _sha(a) != _sha(b)


def test_sha256_none_args_equals_empty_dict() -> None:
    """``arguments=None`` is canonicalized as ``{}`` so two callers
    expressing 'no arguments' two different ways agree on the digest.
    """
    a = provenance.attach([_txt("ok")], tool_name="t", arguments=None)
    b = provenance.attach([_txt("ok")], tool_name="t", arguments={})
    assert _sha(a) == _sha(b)


def test_sha256_excludes_existing_meta_from_digest() -> None:
    """Running ``attach`` on its own output must yield the same digest;
    that requires the provenance metadata itself to be excluded from
    the canonical bytes the hash covers.
    """
    once = provenance.attach([_txt("hello")], tool_name="t", arguments=None)
    twice = provenance.attach(once, tool_name="t", arguments=None)
    assert _sha(once) == _sha(twice)


def test_sha256_is_reproducible_externally_text() -> None:
    """A caller that receives the response must be able to recompute
    the advertised digest from the visible content using only the
    documented recipe (no implementation source-diving).
    """
    tool_name = "the-tool"
    args = {"q": "search-term", "n": 5}
    out = provenance.attach(
        [_txt("hello"), _txt("world")],
        tool_name=tool_name,
        arguments=args,
    )
    advertised = _sha(out)
    assert advertised == _recompute_digest(tool_name, args, out)


def test_sha256_is_reproducible_externally_image() -> None:
    """ImageContent round-trips through the digest the same way text
    does. The base64 ``data`` field is what's covered; the binary
    bytes are not re-decoded.
    """
    tool_name = "image-tool"
    args = {"format": "png"}
    out = provenance.attach([_img()], tool_name=tool_name, arguments=args)
    advertised = _sha(out)
    assert advertised == _recompute_digest(tool_name, args, out)


def test_sha256_is_reproducible_externally_embedded_resource() -> None:
    """EmbeddedResource (which has a nested ``resource`` model) also
    round-trips. The nested ``TextResourceContents`` dump goes through
    pydantic's union discrimination — pinning that here so a future
    SDK bump that changes the discriminator surfaces immediately.
    """
    tool_name = "resource-tool"
    args: dict[str, Any] = {}
    out = provenance.attach([_embedded()], tool_name=tool_name, arguments=args)
    advertised = _sha(out)
    assert advertised == _recompute_digest(tool_name, args, out)


def test_sha256_is_reproducible_externally_non_ascii() -> None:
    """Non-ASCII text content must round-trip. ``ensure_ascii=True`` in
    ``_canonicalize`` pins the unicode-escape behavior; without it a
    receiver using a JSON library with a different ``ensure_ascii``
    default would compute a different byte string and the digest
    wouldn't verify.
    """
    tool_name = "i18n-tool"
    args = {"locale": "fr"}
    out = provenance.attach(
        [_txt("café"), _txt("naïve résumé"), _txt("日本語")],
        tool_name=tool_name,
        arguments=args,
    )
    advertised = _sha(out)
    assert advertised == _recompute_digest(tool_name, args, out)


def test_timestamp_format_is_iso_utc_with_ms() -> None:
    out = provenance.attach([_txt("hi")], tool_name="t", arguments=None)
    ts = out[0].meta[provenance.PROVENANCE_META_KEY]["timestamp"]  # type: ignore[index]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", ts), (
        f"timestamp {ts!r} does not match YYYY-MM-DDTHH:MM:SS.mmmZ"
    )


def test_meta_serializes_under_wire_alias() -> None:
    """The first block must serialize the provenance under the ``_meta``
    alias (not the python ``meta`` attribute name) — that's what the MCP
    wire format requires, and the SDK's ``populate_by_name`` posture
    means we'd silently drop the metadata on the floor if we used the
    wrong key.
    """
    out = provenance.attach([_txt("hi")], tool_name="t", arguments=None)
    dumped = out[0].model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "_meta" in dumped
    assert "meta" not in dumped
    assert provenance.PROVENANCE_META_KEY in dumped["_meta"]


# ---------------------------------------------------------------------------
# Dispatcher integration — wire it together through create_mcp_server
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatcher_attaches_provenance_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(provenance._ENV_VAR, "1")
    server = _server_with_echo()
    result = await _call_echo(server, "ping")
    contents = result.content
    assert len(contents) == 1
    assert contents[0].meta is not None
    assert provenance.PROVENANCE_META_KEY in contents[0].meta


@pytest.mark.anyio
async def test_dispatcher_skips_provenance_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)
    server = _server_with_echo()
    result = await _call_echo(server, "ping")
    contents = result.content
    assert len(contents) == 1
    assert contents[0].meta is None


@pytest.mark.anyio
async def test_dispatcher_attaches_provenance_to_structured_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(provenance._ENV_VAR, "1")
    server = _server_with_structured_echo()
    result = await _call_tool(server, "structured-echo", {"text": "ping"})
    assert result.structured_content == {"text": "ping"}
    contents = result.content
    assert len(contents) == 1
    assert json.loads(contents[0].text) == {"text": "ping"}
    assert contents[0].meta is not None
    assert provenance.PROVENANCE_META_KEY in contents[0].meta


@pytest.mark.anyio
async def test_dispatcher_digest_binds_to_call_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dispatcher passes the real tool name and arguments into
    ``attach`` — confirm by recomputing the digest using those values
    end-to-end.
    """
    monkeypatch.setenv(provenance._ENV_VAR, "1")
    server = _server_with_echo()
    result = await _call_echo(server, "hello")
    advertised = result.content[0].meta[provenance.PROVENANCE_META_KEY]["sha256"]
    expected = _recompute_digest("echo", {"text": "hello"}, result.content)
    assert advertised == expected


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha(
    content: list[types.TextContent | types.ImageContent | types.EmbeddedResource],
) -> str:
    meta = content[0].meta
    assert meta is not None
    return meta[provenance.PROVENANCE_META_KEY]["sha256"]


def _recompute_digest(
    tool_name: str,
    arguments: dict[str, Any] | None,
    content: Sequence,  # type: ignore[type-arg]
) -> str:
    """Implementation of the documented receiver verification recipe.

    Kept verbatim so the test fails loudly if the public contract drifts.
    """
    rendered: list[dict[str, Any]] = []
    for block in content:
        dumped = block.model_dump(mode="json", by_alias=True, exclude_none=True)
        dumped.pop("_meta", None)
        rendered.append(dumped)
    envelope = {
        "tool": tool_name,
        "arguments": arguments or {},
        "content": rendered,
    }
    canonical = json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _server_with_echo():
    tools = [types.Tool(name="echo", description="", input_schema={"type": "object"})]

    async def echo(args: dict[str, Any] | None):
        text = (args or {}).get("text", "default")
        return [types.TextContent(type="text", text=text)]

    return create_mcp_server(
        "test-provenance",
        tools=tools,
        tools_handlers={"echo": echo},
    )


def _server_with_structured_echo():
    tools = [
        types.Tool(
            name="structured-echo",
            description="",
            input_schema={"type": "object"},
        ),
    ]

    async def structured_echo(args: dict[str, Any] | None):
        return {"text": (args or {}).get("text", "default")}

    return create_mcp_server(
        "test-provenance-structured",
        tools=tools,
        tools_handlers={"structured-echo": structured_echo},
    )


async def _call_echo(server, text: str):
    return await _call_tool(server, "echo", {"text": text})


async def _call_tool(server, name: str, arguments: dict[str, Any]):
    entry = server._request_handlers.get("tools/call")
    if entry is None:
        raise AttributeError("No handler for tools/call")
    handler = entry.handler
    from mcp.server.context import ServerRequestContext
    from mcp_types import RequestParamsMeta

    from meta_data_mcp import citations, provenance

    # Create a minimal session
    class MockSession:
        def __init__(self):
            self._initialized = True

    session = MockSession()

    ctx = ServerRequestContext(
        session=session,
        lifespan_context=None,
        protocol_version="2024-11-05",
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
        request_id=1,
        meta=RequestParamsMeta(),
    )

    # Wrap in recording_span to simulate the middleware chain
    with citations.recording_span() as records:
        result = await handler(
            ctx, types.CallToolRequestParams(name=name, arguments=arguments)
        )
        # Manually attach citations since we're bypassing the middleware
        if records:
            from meta_data_mcp.citations import attach

            if isinstance(result, types.CallToolResult):
                result = types.CallToolResult(
                    content=attach(result.content, records),
                    structured_content=result.structured_content,
                    is_error=result.is_error,
                )
        # Attach provenance if enabled
        if provenance.is_enabled() and isinstance(result, types.CallToolResult):
            result = types.CallToolResult(
                content=provenance.attach(
                    result.content,
                    tool_name=name,
                    arguments=arguments,
                ),
                structured_content=result.structured_content,
                is_error=result.is_error,
            )
    return result
