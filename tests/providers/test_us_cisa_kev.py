"""Tests for the us-cisa-kev provider."""

from unittest.mock import Mock

import pytest

from meta_data_mcp import utils
from meta_data_mcp.providers.us_cisa_kev import (
    TOOLS,
    CisaKevGetParams,
    CisaKevListParams,
    fetch_cisa_kev_get,
    fetch_cisa_kev_list,
    handle_cisa_kev_get,
    handle_cisa_kev_list,
)
from meta_data_mcp.ui_resources.app_vulnerability_v1 import URI as VULN_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolate_cache():
    """The provider caches the catalog with cache_ttl=3600; reset between tests."""
    utils._response_cache.clear()
    yield
    utils._response_cache.clear()


@pytest.fixture
def mock_catalog():
    return {
        "catalogVersion": "2026.05.15",
        "dateReleased": "2026-05-15T00:00:00.000Z",
        "count": 3,
        "vulnerabilities": [
            {
                "cveID": "CVE-2021-44228",
                "vendorProject": "Apache",
                "product": "Log4j2",
                "vulnerabilityName": "Apache Log4j2 RCE",
                "dateAdded": "2021-12-10",
                "dueDate": "2021-12-24",
                "knownRansomwareCampaignUse": "Known",
                "requiredAction": "Apply updates per vendor instructions.",
            },
            {
                "cveID": "CVE-2024-3094",
                "vendorProject": "XZ",
                "product": "XZ Utils",
                "vulnerabilityName": "XZ Utils Embedded Malicious Code",
                "dateAdded": "2024-03-29",
                "dueDate": "2024-04-19",
                "knownRansomwareCampaignUse": "Unknown",
                "requiredAction": "Downgrade or apply patches.",
            },
            {
                "cveID": "CVE-2024-21413",
                "vendorProject": "Microsoft",
                "product": "Outlook",
                "vulnerabilityName": "Microsoft Outlook RCE",
                "dateAdded": "2024-02-13",
                "dueDate": "2024-03-05",
                "knownRansomwareCampaignUse": "Unknown",
                "requiredAction": "Apply updates per vendor instructions.",
            },
        ],
    }


def _stub_catalog_get(mock_catalog: dict) -> Mock:
    mock_get = Mock()
    mock_get.return_value.json.return_value = mock_catalog
    mock_get.return_value.raise_for_status = Mock()
    mock_get.return_value.status_code = 200
    mock_get.return_value.headers = {}
    return mock_get


def test_fetch_cisa_kev_list_filters_by_vendor(monkeypatch, mock_catalog):
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    params = CisaKevListParams(vendor="apache")
    result = fetch_cisa_kev_list(params)
    assert result["match_count"] == 1
    assert result["vulnerabilities"][0]["cveID"] == "CVE-2021-44228"


def test_fetch_cisa_kev_list_filters_by_ransomware(monkeypatch, mock_catalog):
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    params = CisaKevListParams(known_ransomware=True)
    result = fetch_cisa_kev_list(params)
    assert result["match_count"] == 1
    assert result["vulnerabilities"][0]["cveID"] == "CVE-2021-44228"


def test_fetch_cisa_kev_list_excludes_ransomware_when_false(monkeypatch, mock_catalog):
    """known_ransomware=False must EXCLUDE entries with knownRansomwareCampaignUse=='Known'."""
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    result = fetch_cisa_kev_list(CisaKevListParams(known_ransomware=False))
    cves = {v["cveID"] for v in result["vulnerabilities"]}
    # CVE-2021-44228 is the ransomware-known entry in the fixture; it must be excluded.
    assert "CVE-2021-44228" not in cves
    assert {"CVE-2024-3094", "CVE-2024-21413"}.issubset(cves)


def test_fetch_cisa_kev_list_date_added_after(monkeypatch, mock_catalog):
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    params = CisaKevListParams(date_added_after="2024-01-01")
    result = fetch_cisa_kev_list(params)
    cves = {v["cveID"] for v in result["vulnerabilities"]}
    assert cves == {"CVE-2024-3094", "CVE-2024-21413"}


def test_fetch_cisa_kev_list_pagination(monkeypatch, mock_catalog):
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    page1 = fetch_cisa_kev_list(CisaKevListParams(limit=2, offset=0))
    page2 = fetch_cisa_kev_list(CisaKevListParams(limit=2, offset=2))
    assert page1["returned"] == 2
    assert page2["returned"] == 1


def test_fetch_cisa_kev_get_returns_entry(monkeypatch, mock_catalog):
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    result = fetch_cisa_kev_get(CisaKevGetParams(cve_id="CVE-2021-44228"))
    assert result["vendorProject"] == "Apache"


def test_fetch_cisa_kev_get_case_insensitive(monkeypatch, mock_catalog):
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    result = fetch_cisa_kev_get(CisaKevGetParams(cve_id="cve-2021-44228"))
    assert result["vendorProject"] == "Apache"


def test_fetch_cisa_kev_get_returns_not_found_marker(monkeypatch, mock_catalog):
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    result = fetch_cisa_kev_get(CisaKevGetParams(cve_id="CVE-9999-99999"))
    assert result == {"cveID": "CVE-9999-99999", "in_kev": False}


def test_cisa_kev_list_validates_date_pattern():
    with pytest.raises(Exception):
        CisaKevListParams(date_added_after="not-a-date")


def test_cisa_kev_get_rejects_empty_cve():
    with pytest.raises(Exception):
        CisaKevGetParams(cve_id="")


@pytest.mark.anyio
async def test_handle_cisa_kev_list(monkeypatch, mock_catalog):
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    result = await handle_cisa_kev_list({"vendor": "microsoft"})
    assert len(result) == 1
    assert "CVE-2024-21413" in result[0].text


@pytest.mark.anyio
async def test_handle_cisa_kev_get(monkeypatch, mock_catalog):
    monkeypatch.setattr(utils.httpx, "get", _stub_catalog_get(mock_catalog))
    result = await handle_cisa_kev_get({"cve_id": "CVE-2024-3094"})
    assert "XZ Utils" in result[0].text


@pytest.mark.anyio
async def test_handle_cisa_kev_list_translates_upstream_503(monkeypatch):
    """Upstream 5xx with provider= must raise UpstreamError, not raw httpx."""
    import httpx

    from meta_data_mcp.errors import UpstreamError

    req = httpx.Request("GET", "https://www.cisa.gov/x")
    resp = httpx.Response(status_code=503, request=req)
    status_err = httpx.HTTPStatusError("down", request=req, response=resp)
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)

    mock_get = Mock()
    mock_get.return_value.raise_for_status = Mock(side_effect=status_err)
    mock_get.return_value.status_code = 503
    mock_get.return_value.headers = {}
    monkeypatch.setattr(utils.httpx, "get", mock_get)

    with pytest.raises(UpstreamError) as exc_info:
        await handle_cisa_kev_list({})
    assert exc_info.value.provider == "us-cisa-kev"


# ---------------------------------------------------------------------------
# Phase 5: MCP Apps shape primitive binding (vulnerability app).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["cisa-kev-list", "cisa-kev-get"])
def test_kev_tool_binds_to_vulnerability_app(tool_name):
    """KEV tools render through the Phase 5 vulnerability app. The
    binding's wire-level alias (``_meta``) is what the host reads.
    """
    tool = next(t for t in TOOLS if t.name == tool_name)
    assert tool.meta == {"ui": {"resourceUri": VULN_URI}}, (
        f"{tool_name} is not bound to {VULN_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == VULN_URI
