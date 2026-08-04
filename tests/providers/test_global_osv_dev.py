"""Tests for the global-osv-dev provider."""

from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_osv_dev import (
    TOOLS,
    OsvGetVulnParams,
    OsvQueryPackageParams,
    fetch_osv_get_vuln,
    fetch_osv_query_package,
    handle_osv_get_vuln,
    handle_osv_query_package,
)
from meta_data_mcp.ui_resources.app_vulnerability_v1 import URI as VULN_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_vuln():
    return {
        "id": "GHSA-jfh8-c2jp-5v3q",
        "summary": "Remote code execution in Apache Log4j",
        "details": "...",
        "aliases": ["CVE-2021-44228"],
        "affected": [{"package": {"name": "log4j-core", "ecosystem": "Maven"}}],
    }


@pytest.fixture
def mock_query_response():
    return {
        "vulns": [
            {"id": "GHSA-jfh8-c2jp-5v3q", "summary": "Apache Log4j RCE"},
            {"id": "GHSA-7rjr-3q55-vv33", "summary": "Log4j 1.x SocketServer"},
        ],
    }


def test_fetch_osv_get_vuln_uses_path_param(mock_vuln):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_vuln
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200

        params = OsvGetVulnParams(vuln_id="GHSA-jfh8-c2jp-5v3q")
        result = fetch_osv_get_vuln(params)

        assert result["id"] == "GHSA-jfh8-c2jp-5v3q"
        # Path-parameter URL — the id must be in the URL itself, not a query.
        called_url = mock_get.call_args[0][0]
        assert called_url == "https://api.osv.dev/v1/vulns/GHSA-jfh8-c2jp-5v3q"


def test_fetch_osv_query_package_sends_post_body(mock_query_response):
    with patch("httpx.post") as mock_post:
        mock_post.return_value.json.return_value = mock_query_response
        mock_post.return_value.raise_for_status = Mock()
        mock_post.return_value.status_code = 200

        params = OsvQueryPackageParams(
            name="log4j-core",
            ecosystem="Maven",
            version="2.14.1",
        )
        result = fetch_osv_query_package(params)

        assert len(result["vulns"]) == 2
        sent_body = mock_post.call_args[1]["json"]
        assert sent_body["package"] == {"name": "log4j-core", "ecosystem": "Maven"}
        assert sent_body["version"] == "2.14.1"


def test_fetch_osv_query_package_omits_version_when_not_set(mock_query_response):
    with patch("httpx.post") as mock_post:
        mock_post.return_value.json.return_value = mock_query_response
        mock_post.return_value.raise_for_status = Mock()
        mock_post.return_value.status_code = 200

        params = OsvQueryPackageParams(name="requests", ecosystem="PyPI")
        fetch_osv_query_package(params)
        body = mock_post.call_args[1]["json"]
        assert "version" not in body


def test_osv_get_vuln_rejects_empty_id():
    with pytest.raises(Exception):
        OsvGetVulnParams(vuln_id="")


def test_osv_get_vuln_rejects_path_traversal():
    """vuln_id must not allow slashes or other URL-structural characters."""
    for bad in ("../query", "CVE-2021-44228/extra", "a b", "id?query=x"):
        with pytest.raises(Exception):
            OsvGetVulnParams(vuln_id=bad)


def test_osv_get_vuln_accepts_real_ids():
    """Real id formats must continue to validate."""
    for good in (
        "CVE-2021-44228",
        "GHSA-jfh8-c2jp-5v3q",
        "PYSEC-2023-12",
        "GO-2024-1234",
        "RUSTSEC-2024-0001",
        "OSV-2024-0123",
    ):
        OsvGetVulnParams(vuln_id=good)


def test_osv_query_package_requires_name_and_ecosystem():
    with pytest.raises(Exception):
        OsvQueryPackageParams(name="", ecosystem="PyPI")
    with pytest.raises(Exception):
        OsvQueryPackageParams(name="requests", ecosystem="")


@pytest.mark.anyio
async def test_handle_osv_get_vuln(mock_vuln):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_vuln
        mock_get.return_value.raise_for_status = Mock()
        mock_get.return_value.status_code = 200

        result = await handle_osv_get_vuln({"vuln_id": "CVE-2021-44228"})
        assert "Log4j" in result[0].text


@pytest.mark.anyio
async def test_handle_osv_query_package(mock_query_response):
    with patch("httpx.post") as mock_post:
        mock_post.return_value.json.return_value = mock_query_response
        mock_post.return_value.raise_for_status = Mock()
        mock_post.return_value.status_code = 200

        result = await handle_osv_query_package(
            {"name": "log4j-core", "ecosystem": "Maven"},
        )
        assert "GHSA-jfh8-c2jp-5v3q" in result[0].text


@pytest.mark.anyio
async def test_handle_osv_get_vuln_translates_404():
    """Path-param 404 with provider= must surface as NotFoundError."""
    from meta_data_mcp.errors import NotFoundError

    req = httpx.Request("GET", "https://api.osv.dev/v1/vulns/MISSING-1")
    resp = httpx.Response(status_code=404, request=req)
    status_err = httpx.HTTPStatusError("not found", request=req, response=resp)

    with patch("httpx.get") as mock_get:
        mock_get.return_value.raise_for_status = Mock(side_effect=status_err)
        mock_get.return_value.status_code = 404

        with pytest.raises(NotFoundError) as exc_info:
            await handle_osv_get_vuln({"vuln_id": "MISSING-1"})
        assert exc_info.value.provider == "global-osv-dev"
        assert "api.osv.dev" not in str(exc_info.value)


@pytest.mark.anyio
async def test_handle_osv_query_package_translates_503():
    """POST with provider= must translate upstream 5xx to UpstreamError."""
    from meta_data_mcp.errors import UpstreamError

    req = httpx.Request("POST", "https://api.osv.dev/v1/query")
    resp = httpx.Response(status_code=503, request=req)
    status_err = httpx.HTTPStatusError("down", request=req, response=resp)

    with patch("httpx.post") as mock_post:
        mock_post.return_value.raise_for_status = Mock(side_effect=status_err)
        mock_post.return_value.status_code = 503
        mock_post.return_value.headers = {}
        # Patch sleep to keep the retry loop fast.
        with patch("meta_data_mcp.utils.time.sleep", lambda s: None):
            with pytest.raises(UpstreamError) as exc_info:
                await handle_osv_query_package(
                    {"name": "requests", "ecosystem": "PyPI"},
                )
        assert exc_info.value.provider == "global-osv-dev"


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps shape primitive binding (vulnerability app).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["osv-get-vulnerability", "osv-query-package"])
def test_osv_tool_binds_to_vulnerability_app(tool_name):
    """OSV tools render through the Phase 5 vulnerability app."""
    tool = next(t for t in TOOLS if t.name == tool_name)
    assert tool.meta == {"ui": {"resourceUri": VULN_URI}}, (
        f"{tool_name} is not bound to {VULN_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == VULN_URI
