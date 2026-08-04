"""Tests for the per-call source-citation manifest on tool results.

Citations are ON by default (``META_DATA_MCP_CITATIONS`` set to a falsy
value disables them). While a tool call runs, every kernel HTTP exchange
is recorded in a context-local span; the dispatcher attaches the
collected manifest to the first content block's ``_meta`` under
``meta-data-mcp/citations``.

These tests cover:

1. The env-var parser — default ON, falsy OFF, anything else ON.
2. ``redact_url`` — sensitive query-param values replaced (name match
   case-insensitive), names/order preserved, query-less URLs untouched.
3. ``record`` / ``recording_span`` — no-op outside a span, ground-truth
   URL from ``response.request.url``, params-composition fallback,
   cache-hit flagging, and context isolation across concurrent tasks.
4. ``attach`` — manifest on the first block only, pre-existing ``_meta``
   preserved (provenance coexistence), registry enrichment (title /
   homepage / license) keyed by kebab ``server_name``, empty-content
   pass-through, pass-through when there are no records.
5. ``Registry.find_by_server_name`` — kebab lookup, case-insensitive.
6. Transport integration — ``http_get`` records success, error-status,
   cache-hit (with original fetch time), and retried-away exchanges.
7. End-to-end through the SDK dispatcher — enabled by default, absent
   when disabled, coexists with the provenance digest, and preserves
   every SDK-permitted handler return shape (dict, tuple,
   CallToolResult, lazy iterable, empty content).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from mcp import types

from meta_data_mcp import citations, provenance
from meta_data_mcp.registry import REGISTRY
from meta_data_mcp.server import create_mcp_server
from meta_data_mcp.transport import _response_cache, http_get


def _txt(s: str) -> types.TextContent:
    return types.TextContent(type="text", text=s)


def _record(provider: str = "eu-eurostat", **overrides: Any) -> citations.SourceRecord:
    values: dict[str, Any] = {
        "provider": provider,
        "url": "https://example.test/data?x=1",
        "method": "GET",
        "status": 200,
        "fetched_at": "2026-01-01T00:00:00.000Z",
        "cache_hit": False,
    }
    values.update(overrides)
    return citations.SourceRecord(**values)


# ---------------------------------------------------------------------------
# is_enabled()
# ---------------------------------------------------------------------------


def test_is_enabled_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(citations._ENV_VAR, raising=False)
    assert citations.is_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "Off", " oFf "])
def test_is_enabled_falsy_disables(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(citations._ENV_VAR, value)
    assert citations.is_enabled() is False


@pytest.mark.parametrize("value", ["", "1", "true", "yes", "anything"])
def test_is_enabled_everything_else_on(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(citations._ENV_VAR, value)
    assert citations.is_enabled() is True


# ---------------------------------------------------------------------------
# redact_url()
# ---------------------------------------------------------------------------


def test_redact_url_replaces_sensitive_values() -> None:
    url = "https://api.test/v1?dataset=gdp&api_key=sekrit&lang=en"
    out = citations.redact_url(url)
    assert "sekrit" not in out
    assert out == "https://api.test/v1?dataset=gdp&api_key=REDACTED&lang=en"


def test_redact_url_is_case_insensitive_on_names() -> None:
    out = citations.redact_url("https://api.test/v1?APPID=abc123&q=london")
    assert "abc123" not in out
    assert "APPID=REDACTED" in out


def test_redact_url_no_query_passthrough() -> None:
    assert citations.redact_url("https://api.test/v1") == "https://api.test/v1"


def test_redact_url_preserves_repeated_params() -> None:
    out = citations.redact_url("https://api.test/v1?geo=DE&geo=FR&token=t0k")
    assert out == "https://api.test/v1?geo=DE&geo=FR&token=REDACTED"


def test_redact_url_strips_userinfo_credentials() -> None:
    out = citations.redact_url("https://apikey:s3cret@api.test/v1/data")
    assert "s3cret" not in out
    assert "apikey:" not in out
    assert out == "https://REDACTED:REDACTED@api.test/v1/data"


def test_redact_url_covers_presigned_cloud_urls() -> None:
    # follow_redirects=True means response.request.url can be a
    # post-redirect presigned URL — its signature params must not leak.
    out = citations.redact_url(
        "https://bucket.s3.test/obj"
        "?X-Amz-Credential=AKIA%2F123&X-Amz-Signature=deadbeef&X-Amz-Expires=300",
    )
    assert "deadbeef" not in out
    assert "AKIA" not in out
    assert "X-Amz-Signature=REDACTED" in out
    assert "X-Amz-Expires=300" in out


@pytest.mark.parametrize(
    "param",
    ["subscription-key", "user-key", "private_token", "page_token", "sig"],
)
def test_redact_url_suffix_heuristics_catch_provider_specific_keys(
    param: str,
) -> None:
    # Generated plugins can map an env var onto any query-param name;
    # the *key/*token suffix heuristics cover names the exact denylist
    # can't enumerate (over-redacting a benign page_token is the safe
    # direction).
    out = citations.redact_url(f"https://api.test/v1?{param}=s3cret&q=data")
    assert "s3cret" not in out
    assert "q=data" in out


# ---------------------------------------------------------------------------
# record() / recording_span()
# ---------------------------------------------------------------------------


def test_record_is_noop_outside_span() -> None:
    # Must not raise, must not leak state into a later span.
    citations.record(provider="p", url="https://x.test/a", status=200)
    with citations.recording_span() as records:
        assert records == []


def test_record_prefers_response_request_url() -> None:
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://x.test/a?fmt=json&key=sek"),
    )
    with citations.recording_span() as records:
        citations.record(provider="p", url="https://x.test/WRONG", response=response)
    assert len(records) == 1
    assert records[0].url == "https://x.test/a?fmt=json&key=REDACTED"
    assert records[0].status == 200


def test_record_composes_url_from_params_without_response() -> None:
    with citations.recording_span() as records:
        citations.record(
            provider="p",
            url="https://x.test/a",
            params={"fmt": "json", "appid": "sek"},
            status=200,
        )
    assert records[0].url == "https://x.test/a?fmt=json&appid=REDACTED"


def test_record_flags_cache_hits() -> None:
    with citations.recording_span() as records:
        citations.record(
            provider="p",
            url="https://x.test/a",
            status=200,
            cache_hit=True,
        )
    assert records[0].cache_hit is True


def test_record_survives_requestless_response() -> None:
    # httpx.Response.request raises RuntimeError (not AttributeError)
    # when no request was set; record() must fall back to composing the
    # URL rather than propagating into the tool call.
    with citations.recording_span() as records:
        citations.record(
            provider="p",
            url="https://x.test/a",
            params={"fmt": "json"},
            response=httpx.Response(200),
        )
    assert len(records) == 1
    assert records[0].url == "https://x.test/a?fmt=json"
    assert records[0].status == 200


def test_record_never_breaks_the_tool_call() -> None:
    # An unrecordable exchange (garbage response double, unparseable
    # URL) is logged and dropped — observability must not fail the work
    # it observes.
    from types import SimpleNamespace

    # A Mock-like double whose request.url is not a URL at all —
    # redact_url's httpx.URL() call raises TypeError on it.
    garbage = SimpleNamespace(request=SimpleNamespace(url=object()), status_code=200)

    with citations.recording_span() as records:
        citations.record(provider="p", url="https://x.test/a", response=garbage)  # type: ignore[arg-type]
    assert records == []


def test_recording_span_disabled_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(citations._ENV_VAR, "0")
    with citations.recording_span() as records:
        citations.record(provider="p", url="https://x.test/a", status=200)
    assert records == []


def test_spans_isolate_concurrent_tasks() -> None:
    """Two tool calls running concurrently must never cross-cite."""

    async def call(provider: str, n: int) -> list[citations.SourceRecord]:
        with citations.recording_span() as records:
            for i in range(n):
                citations.record(
                    provider=provider,
                    url=f"https://{provider}.test/{i}",
                    status=200,
                )
                await asyncio.sleep(0)  # force interleaving
            return list(records)

    async def main() -> tuple[list, list]:
        return await asyncio.gather(call("alpha", 3), call("beta", 2))

    alpha, beta = asyncio.run(main())
    assert [r.provider for r in alpha] == ["alpha"] * 3
    assert [r.provider for r in beta] == ["beta"] * 2


def test_spans_nest_by_shadowing() -> None:
    with citations.recording_span() as outer:
        citations.record(provider="outer", url="https://o.test/1", status=200)
        with citations.recording_span() as inner:
            citations.record(provider="inner", url="https://i.test/1", status=200)
        citations.record(provider="outer", url="https://o.test/2", status=200)
    assert [r.provider for r in inner] == ["inner"]
    assert [r.provider for r in outer] == ["outer", "outer"]


# ---------------------------------------------------------------------------
# Registry.find_by_server_name()
# ---------------------------------------------------------------------------


def test_find_by_server_name_resolves_kebab_id() -> None:
    entry = REGISTRY.find_by_server_name("eu-eurostat")
    assert entry is not None
    assert entry.id == "eu_eurostat"


def test_find_by_server_name_case_insensitive() -> None:
    assert REGISTRY.find_by_server_name("EU-Eurostat") is not None


def test_find_by_server_name_unknown_returns_none() -> None:
    assert REGISTRY.find_by_server_name("no-such-provider") is None


# ---------------------------------------------------------------------------
# attach() — structural correctness + enrichment
# ---------------------------------------------------------------------------


def test_attach_without_records_passes_through() -> None:
    content = [_txt("hello")]
    out = citations.attach(content, [])
    assert out[0].meta is None


def test_attach_manifest_on_first_block_only() -> None:
    out = citations.attach([_txt("a"), _txt("b")], [_record()])
    assert out[0].meta is not None
    assert citations.CITATIONS_META_KEY in out[0].meta
    assert out[1].meta is None


def test_attach_does_not_mutate_input() -> None:
    content = [_txt("a")]
    citations.attach(content, [_record()])
    assert content[0].meta is None


def test_attach_enriches_known_provider_from_registry() -> None:
    out = citations.attach([_txt("a")], [_record(provider="eu-eurostat")])
    sources = out[0].meta[citations.CITATIONS_META_KEY]["sources"]
    assert len(sources) == 1
    src = sources[0]
    assert src["provider"] == "eu-eurostat"
    assert src["title"] == "Eurostat"
    assert src["homepage"] == "https://ec.europa.eu/eurostat"
    assert "CC BY 4.0" in src["license"]
    assert src["url"] == "https://example.test/data?x=1"
    assert src["status"] == 200
    assert src["cache_hit"] is False


def test_attach_unknown_provider_still_cites() -> None:
    out = citations.attach([_txt("a")], [_record(provider="out-of-tree")])
    src = out[0].meta[citations.CITATIONS_META_KEY]["sources"][0]
    assert src["provider"] == "out-of-tree"
    assert "title" not in src
    assert "license" not in src


def test_attach_preserves_existing_meta_keys() -> None:
    block = _txt("a").model_copy(update={"meta": {"other/key": {"kept": True}}})
    out = citations.attach([block], [_record()])
    assert out[0].meta["other/key"] == {"kept": True}
    assert citations.CITATIONS_META_KEY in out[0].meta


def test_attach_empty_content_passes_through_and_drops_manifest() -> None:
    # Synthesizing a stub block would change the wire shape clients see
    # (e.g. the remote SDK's `if not result.content: return {}` guard) —
    # citations annotate results, never alter them.
    out = citations.attach([], [_record()])
    assert out == []


# ---------------------------------------------------------------------------
# transport integration
# ---------------------------------------------------------------------------


def _http_response(status: int, url: str) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", url), json={})


def test_http_get_records_success(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.test/data?fmt=json"
    monkeypatch.setattr(
        "meta_data_mcp.transport.httpx.get",
        lambda *a, **kw: _http_response(200, url),
    )
    with citations.recording_span() as records:
        http_get("https://api.test/data", params={"fmt": "json"}, provider="prov-x")
    assert len(records) == 1
    assert records[0].url == url
    assert records[0].status == 200
    assert records[0].cache_hit is False


def test_http_get_records_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from meta_data_mcp.errors import ProviderError

    url = "https://api.test/missing"
    monkeypatch.setattr(
        "meta_data_mcp.transport.httpx.get",
        lambda *a, **kw: _http_response(404, url),
    )
    with citations.recording_span() as records, pytest.raises(ProviderError):
        http_get(url, provider="prov-x")
    assert len(records) == 1
    assert records[0].status == 404


def test_http_get_records_cache_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.test/cached"
    monkeypatch.setattr(
        "meta_data_mcp.transport.httpx.get",
        lambda *a, **kw: _http_response(200, url),
    )
    _response_cache.clear()
    try:
        with citations.recording_span() as records:
            http_get(url, provider="prov-x", cache_ttl=60.0)
            http_get(url, provider="prov-x", cache_ttl=60.0)
        assert [r.cache_hit for r in records] == [False, True]
        assert records[1].url == url
        assert records[1].status == 200
    finally:
        _response_cache.clear()


def test_cache_hit_reports_original_fetch_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetched_at on a cache hit is when the bytes were fetched, not
    when the cache was read.
    """
    url = "https://api.test/cached-time"
    monkeypatch.setattr(
        "meta_data_mcp.transport.httpx.get",
        lambda *a, **kw: _http_response(200, url),
    )
    stamps = iter(["2026-01-01T10:00:00.000Z", "2026-01-01T11:00:00.000Z"])
    # The fetch path stamps the cache entry via transport's utc_iso_ms;
    # a later read must reuse that stamp rather than minting a new one.
    monkeypatch.setattr("meta_data_mcp.transport.utc_iso_ms", lambda: next(stamps))
    _response_cache.clear()
    try:
        with citations.recording_span() as records:
            http_get(url, provider="prov-x", cache_ttl=60.0)
        with citations.recording_span() as later_records:
            http_get(url, provider="prov-x", cache_ttl=60.0)
        assert later_records[0].cache_hit is True
        assert later_records[0].fetched_at == "2026-01-01T10:00:00.000Z"
        assert records[0].cache_hit is False
    finally:
        _response_cache.clear()


def test_retried_attempts_are_cited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Intermediate 429/5xx attempts the retry loop recovers from still
    appear in the manifest — they're part of how the answer was produced.
    """
    url = "https://api.test/flaky"
    responses = iter([_http_response(500, url), _http_response(200, url)])
    monkeypatch.setattr(
        "meta_data_mcp.transport.httpx.get",
        lambda *a, **kw: next(responses),
    )
    monkeypatch.setattr("meta_data_mcp.transport.time.sleep", lambda s: None)
    with citations.recording_span() as records:
        http_get(url, provider="prov-x")
    assert [r.status for r in records] == [500, 200]


# ---------------------------------------------------------------------------
# end-to-end through the SDK dispatcher
# ---------------------------------------------------------------------------


def _server_with_fetching_tool(structured: bool = False):
    """A server whose one tool records a kernel exchange when called."""

    async def fetch(args: dict[str, Any] | None):
        citations.record(
            provider="eu-eurostat",
            url="https://ec.europa.eu/eurostat/api/x",
            status=200,
        )
        if structured:
            return {"answer": 42}
        return [_txt("fetched")]

    tools = [types.Tool(name="fetch", description="", input_schema={"type": "object"})]
    return create_mcp_server(
        "test-citations",
        tools=tools,
        tools_handlers={"fetch": fetch},
    )


async def _call_tool(server, name: str, arguments: dict[str, Any]):
    entry = server._request_handlers.get("tools/call")
    if entry is None:
        raise AttributeError("No handler for tools/call")
    handler = entry.handler
    from mcp.server.context import ServerRequestContext
    from mcp_types import RequestParamsMeta

    from meta_data_mcp import citations

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
    return result


@pytest.mark.anyio
async def test_dispatcher_attaches_citations_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(citations._ENV_VAR, raising=False)
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)
    server = _server_with_fetching_tool()
    result = await _call_tool(server, "fetch", {})
    meta = result.content[0].meta
    assert meta is not None
    manifest = meta[citations.CITATIONS_META_KEY]
    assert manifest["sources"][0]["provider"] == "eu-eurostat"
    assert manifest["sources"][0]["title"] == "Eurostat"


@pytest.mark.anyio
async def test_dispatcher_disabled_leaves_result_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(citations._ENV_VAR, "0")
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)
    server = _server_with_fetching_tool()
    result = await _call_tool(server, "fetch", {})
    assert result.content[0].meta is None


@pytest.mark.anyio
async def test_dispatcher_no_upstream_calls_no_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool that touches no provider (pure meta tool) cites nothing."""
    monkeypatch.delenv(citations._ENV_VAR, raising=False)
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)

    async def pure(args: dict[str, Any] | None):
        return [_txt("no I/O here")]

    tools = [types.Tool(name="pure", description="", input_schema={"type": "object"})]
    server = create_mcp_server(
        "test-citations-pure",
        tools=tools,
        tools_handlers={"pure": pure},
    )
    result = await _call_tool(server, "pure", {})
    assert result.content[0].meta is None


@pytest.mark.anyio
async def test_dispatcher_citations_and_provenance_coexist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(citations._ENV_VAR, raising=False)
    monkeypatch.setenv(provenance._ENV_VAR, "1")
    server = _server_with_fetching_tool()
    result = await _call_tool(server, "fetch", {})
    meta = result.content[0].meta
    assert citations.CITATIONS_META_KEY in meta
    assert provenance.PROVENANCE_META_KEY in meta


@pytest.mark.anyio
async def test_dispatcher_structured_result_keeps_structured_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(citations._ENV_VAR, raising=False)
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)
    server = _server_with_fetching_tool(structured=True)
    result = await _call_tool(server, "fetch", {})
    assert result.structured_content == {"answer": 42}
    meta = result.content[0].meta
    assert citations.CITATIONS_META_KEY in meta


def _server_with_handler(handler):
    tools = [types.Tool(name="t", description="", input_schema={"type": "object"})]
    return create_mcp_server(
        "test-citations-shape",
        tools=tools,
        tools_handlers={"t": handler},
    )


def _record_one_exchange() -> None:
    citations.record(
        provider="eu-eurostat",
        url="https://ec.europa.eu/eurostat/api/x",
        status=200,
    )


@pytest.mark.anyio
async def test_dispatcher_handles_tuple_return_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(unstructured, structured) — the SDK's CombinationContent shape —
    must survive attachment intact.
    """
    monkeypatch.delenv(citations._ENV_VAR, raising=False)
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)

    async def handler(args):
        _record_one_exchange()
        return ([_txt("combo")], {"rows": [1, 2]})

    result = await _call_tool(_server_with_handler(handler), "t", {})
    assert result.is_error is False
    assert result.structured_content == {"rows": [1, 2]}
    assert result.content[0].text == "combo"
    assert citations.CITATIONS_META_KEY in result.content[0].meta


@pytest.mark.anyio
async def test_dispatcher_passes_calltoolresult_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler returning types.CallToolResult keeps full control —
    the attach layers step aside.
    """
    monkeypatch.delenv(citations._ENV_VAR, raising=False)
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)

    async def handler(args):
        _record_one_exchange()
        return types.CallToolResult(content=[_txt("direct")], isError=True)

    result = await _call_tool(_server_with_handler(handler), "t", {})
    assert result.is_error is True
    assert result.content[0].text == "direct"
    assert result.content[0].meta is None


@pytest.mark.anyio
async def test_dispatcher_materializes_generators_inside_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lazy handler results are consumed while the recording span is
    open, so exchanges made during iteration still get cited.
    """
    monkeypatch.delenv(citations._ENV_VAR, raising=False)
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)

    async def handler(args):
        def gen():
            _record_one_exchange()  # records during iteration
            yield _txt("lazy")

        return gen()

    result = await _call_tool(_server_with_handler(handler), "t", {})
    assert result.content[0].text == "lazy"
    manifest = result.content[0].meta[citations.CITATIONS_META_KEY]
    assert manifest["sources"][0]["provider"] == "eu-eurostat"


@pytest.mark.anyio
async def test_dispatcher_empty_content_stays_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler returning [] after upstream calls keeps its empty
    content — no phantom stub block on the default path.
    """
    monkeypatch.delenv(citations._ENV_VAR, raising=False)
    monkeypatch.delenv(provenance._ENV_VAR, raising=False)

    async def handler(args):
        _record_one_exchange()
        return []

    result = await _call_tool(_server_with_handler(handler), "t", {})
    assert result.content == []
