"""Tests for the global-gdelt provider."""

import re
from importlib.resources import files
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_gdelt import (
    TOOLS,
    GdeltArticleSearchParams,
    GdeltVolumeTimelineParams,
    fetch_gdelt_article_search,
    fetch_gdelt_volume_timeline,
    handle_gdelt_article_search,
)
from meta_data_mcp.ui_resources.app_news_tone_v1 import URI as NEWS_TONE_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _ok(payload: dict) -> Mock:
    r = Mock()
    r.json.return_value = payload
    r.raise_for_status = Mock()
    r.status_code = 200
    r.headers = {}
    return r


def test_article_search_sends_required_params():
    payload = {"articles": [{"url": "https://x.com/a", "title": "T"}]}
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        params = GdeltArticleSearchParams(
            query='"supply chain" sourcelang:eng',
            timespan="7d",
            sort="DateDesc",
        )
        result = fetch_gdelt_article_search(params)
        assert len(result["articles"]) == 1
        sent = mock_get.call_args[1]["params"]
        assert sent["query"] == '"supply chain" sourcelang:eng'
        assert sent["mode"] == "ArtList"
        assert sent["format"] == "json"
        assert sent["timespan"] == "7d"
        assert sent["sort"] == "DateDesc"


def test_article_search_clamps_maxrecords():
    with pytest.raises(Exception):
        GdeltArticleSearchParams(query="x", maxrecords=0)
    with pytest.raises(Exception):
        GdeltArticleSearchParams(query="x", maxrecords=251)


def test_article_search_rejects_empty_query():
    with pytest.raises(Exception):
        GdeltArticleSearchParams(query="")


def test_volume_timeline_default_mode():
    payload = {"timeline": [{"date": "20260515", "value": 1.2}]}
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        fetch_gdelt_volume_timeline(GdeltVolumeTimelineParams(query="floods"))
        sent = mock_get.call_args[1]["params"]
        assert sent["mode"] == "TimelineVol"


def test_volume_timeline_custom_mode():
    payload = {"timeline": []}
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        fetch_gdelt_volume_timeline(
            GdeltVolumeTimelineParams(query="floods", timeline_mode="TimelineTone"),
        )
        assert mock_get.call_args[1]["params"]["mode"] == "TimelineTone"


@pytest.mark.anyio
async def test_handle_article_search():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"articles": [{"title": "Hello GDELT"}]})
        result = await handle_gdelt_article_search({"query": "test"})
        assert "Hello GDELT" in result[0].text


@pytest.mark.anyio
async def test_handle_translates_503():
    from meta_data_mcp import utils
    from meta_data_mcp.errors import UpstreamError

    req = httpx.Request("GET", "https://api.gdeltproject.org/api/v2/doc/doc")
    resp = httpx.Response(status_code=503, request=req)
    status_err = httpx.HTTPStatusError("down", request=req, response=resp)

    with patch("httpx.get") as mock_get:
        mock_get.return_value.raise_for_status = Mock(side_effect=status_err)
        mock_get.return_value.status_code = 503
        mock_get.return_value.headers = {}
        with patch.object(utils.time, "sleep", lambda s: None):
            with pytest.raises(UpstreamError) as exc_info:
                await handle_gdelt_article_search({"query": "x"})
        assert exc_info.value.provider == "global-gdelt"


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps news-tone binding.
# ---------------------------------------------------------------------------


def _load_news_tone_bundle() -> str:
    return (files("meta_data_mcp.ui_resources") / "app_news_tone_v1.html").read_text(
        encoding="utf-8",
    )


@pytest.mark.parametrize("tool_name", ["gdelt-article-search", "gdelt-volume-timeline"])
def test_gdelt_tool_binds_to_news_tone_app(tool_name):
    """Each GDELT tool must declare ``_meta.ui.resourceUri`` pointing at
    the canonical news-tone app URI. Pin both the Python-side ``.meta``
    attribute AND the wire-level alias (``model_dump(by_alias=True)``
    emits ``_meta``) so a future SDK regression on the populate_by_name
    footgun is caught here — see
    tests/test_ui_resource.py::test_tool_meta_constructor_kwarg_does_not_reach_wire.
    """
    tool = next(t for t in TOOLS if t.name == tool_name)
    assert tool.meta == {"ui": {"resourceUri": NEWS_TONE_URI}}, (
        f"{tool_name} is not bound to {NEWS_TONE_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == NEWS_TONE_URI


# ---------------------------------------------------------------------------
# news-tone bundle invariants (Phase 5).
# ---------------------------------------------------------------------------


def test_news_tone_bundle_is_non_empty():
    assert len(_load_news_tone_bundle()) > 0


def test_news_tone_bundle_contains_script_tag():
    assert "<script" in _load_news_tone_bundle().lower()


def test_news_tone_bundle_has_query_input():
    """The GDELT-query input is the primary user input. Pin its
    existence so a redesign that drops the input has to update tests
    and provider expectations together.
    """
    html = _load_news_tone_bundle().lower()
    assert 'id="query-input"' in html


def test_news_tone_bundle_advertises_tool_call_envelope():
    """The news-tone app reuses the Phase 3 ``tool_call`` envelope.
    If a refactor switches envelope shapes, both the Phase 3 discovery
    app's matching test AND this one will fail — pin one in each app
    so divergence between them is impossible silently.
    """
    html = _load_news_tone_bundle()
    assert "type: 'tool_call'" in html or 'type: "tool_call"' in html, (
        "bundle has no tool_call envelope construction"
    )


def test_news_tone_bundle_listens_for_tool_result_from_parent():
    """The app must accept ``tool_result`` / ``render`` messages and
    must guard ``ev.source === window.parent`` to refuse cross-frame
    spoof traffic. Both invariants together — without the source guard
    a malicious nested iframe could feed bogus tool_results.
    """
    html = _load_news_tone_bundle()
    assert "'tool_result'" in html or '"tool_result"' in html
    assert "window.parent" in html, "bundle doesn't guard ev.source vs window.parent"


def test_news_tone_bundle_has_inline_svg_visualization():
    """The plan promises a timeline + tone overlay + country chord.
    We don't pin specific SVG shape choices, but we DO pin that
    visualizations are inline SVG (the dependency-free stance —
    no Plotly, no D3, no CDN). If a refactor switches to canvas /
    WebGL / a charting lib the matching test on no-external-scripts
    would also fail; together they enforce the design choice.
    """
    html = _load_news_tone_bundle().lower()
    assert "<svg" in html or "createelementns" in html, (
        "bundle has no inline SVG visualization mount"
    )


def test_news_tone_bundle_size_under_100kb():
    """Phase 6b bundle-budget enforcement. The news-tone app's two
    SVG primitives (timeline + chord) plus the GDELT adapter live
    comfortably under the 100KB budget.
    """
    html = _load_news_tone_bundle()
    size_kb = len(html.encode("utf-8")) / 1024
    assert size_kb < 100, f"news-tone bundle is {size_kb:.1f}KB (budget: <100KB)"


def test_news_tone_bundle_has_no_external_script_sources():
    """Dependency-free by design — see the plan §5 visualisations
    note. Timeline + chord are achievable in pure SVG, no chart library
    required. CDN imports would also fail the headless smoke (file://
    origin can't resolve them).
    """
    html = _load_news_tone_bundle()
    pattern = re.compile(
        r"<script\b[^>]*\bsrc\s*=\s*[\"']https?://",
        flags=re.IGNORECASE,
    )
    matches = pattern.findall(html)
    assert not matches, (
        f"bundle smuggled in external <script src> tag(s): {matches!r}. "
        "News-tone app is dependency-free by design."
    )


def test_news_tone_bundle_does_not_use_dangerous_html_assignment():
    """Untrusted upstream JSON (GDELT article titles, source-country
    strings) flows through this bundle. No assignment to the DOM
    property whose name is ``"inner"`` + ``"HTML"`` anywhere — all DOM
    mutation has to go through ``textContent`` / ``replaceChildren`` /
    explicit element creation so markup injection is impossible by
    construction.
    """
    html = _load_news_tone_bundle()
    forbidden = "." + "inner" + "HTML"
    pattern = re.compile(re.escape(forbidden) + r"\s*[+]?=")
    matches = pattern.findall(html)
    assert not matches, (
        f"bundle assigns to {forbidden} ({matches!r}); use textContent / "
        "replaceChildren instead."
    )


def test_news_tone_bundle_documents_payload_contract():
    """The unified payload envelope (events / country_pairs / facets)
    is the contract the bundle promises to consume. Document it inline
    so a host integrator can wire app↔host without reading the bundle
    source. Pin the three top-level keys so a refactor that quietly
    renames them surfaces here.
    """
    html = _load_news_tone_bundle()
    for key in ("events", "country_pairs", "facets"):
        assert key in html, f"bundle does not document payload key: {key}"
