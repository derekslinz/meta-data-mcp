import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_clinicaltrials import (
    TOOLS,
    _ctgov_search_to_shape_payload,
    handle_get_study,
    handle_list_stats,
    handle_search_by_condition,
    handle_search_by_intervention,
    handle_search_by_location,
    handle_search_studies,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_ctgov_search_studies_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "studies": [
                {"protocolSection": {"identificationModule": {"nctId": "NCT01234567"}}},
            ],
            "nextPageToken": "abc",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_studies({"query_term": "cancer"})
        assert len(result) == 1
        assert "NCT01234567" in result[0].text


@pytest.mark.anyio
async def test_ctgov_search_studies_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")
        with pytest.raises(httpx.HTTPError):
            await handle_search_studies({"query_term": "cancer"})


@pytest.mark.anyio
async def test_ctgov_get_study_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT00000001",
                    "briefTitle": "Sample Study",
                },
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_study({"nctId": "NCT00000001"})
        assert "NCT00000001" in result[0].text
        assert "Sample Study" in result[0].text


@pytest.mark.anyio
async def test_ctgov_get_study_requires_nctid():
    with pytest.raises(ValueError):
        await handle_get_study({})


@pytest.mark.anyio
async def test_ctgov_search_by_condition_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "studies": [{"condition": "diabetes"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_by_condition({"condition": "diabetes"})
        assert "diabetes" in result[0].text


@pytest.mark.anyio
async def test_ctgov_search_by_intervention_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "studies": [{"intervention": "metformin"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_by_intervention({"intervention": "metformin"})
        assert "metformin" in result[0].text


@pytest.mark.anyio
async def test_ctgov_search_by_location_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"studies": [{"location": "Boston"}]}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_by_location({"location": "Boston"})
        assert "Boston" in result[0].text


@pytest.mark.anyio
async def test_ctgov_list_stats_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {"totalStudies": 500000}
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_stats()
        assert "500000" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for ctgov-search-studies.
# ---------------------------------------------------------------------------


def test_ctgov_adapter_flattens_protocol_section_to_rows():
    raw = {
        "studies": [
            {
                "protocolSection": {
                    "identificationModule": {
                        "nctId": "NCT01234567",
                        "briefTitle": "Phase III trial",
                    },
                    "statusModule": {
                        "overallStatus": "RECRUITING",
                        "startDateStruct": {"date": "2024-01-01"},
                    },
                    "designModule": {
                        "studyType": "INTERVENTIONAL",
                        "phases": ["PHASE3"],
                    },
                    "sponsorCollaboratorsModule": {
                        "leadSponsor": {"name": "Acme Pharma"},
                    },
                    "conditionsModule": {"conditions": ["Lung Cancer"]},
                    "descriptionModule": {"briefSummary": "Summary here."},
                },
            },
        ],
        "nextPageToken": "abc",
    }
    payload = _ctgov_search_to_shape_payload(raw)
    assert payload["nextPageToken"] == "abc"
    row = payload["rows"][0]
    assert row["nctId"] == "NCT01234567"
    assert row["overallStatus"] == "RECRUITING"
    assert row["studyType"] == "INTERVENTIONAL"
    assert row["phase"] == "PHASE3"
    assert row["leadSponsor"] == "Acme Pharma"
    assert row["conditions"] == "Lung Cancer"
    assert payload["default_facets"] == ["overallStatus", "studyType", "phase"]


def test_ctgov_adapter_handles_missing_studies():
    assert _ctgov_search_to_shape_payload({})["rows"] == []


def test_ctgov_adapter_handles_partial_protocol_section():
    payload = _ctgov_search_to_shape_payload(
        {
            "studies": [
                {"protocolSection": {"identificationModule": {"nctId": "NCT99999999"}}},
            ],
        },
    )
    assert payload["rows"][0]["nctId"] == "NCT99999999"


def test_search_studies_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "ctgov-search-studies")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_ctgov_search_studies_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "studies": [
                {"protocolSection": {"identificationModule": {"nctId": "NCT01234567"}}},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_search_studies({"query_term": "cancer"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["nctId"] == "NCT01234567"
        assert "schema" in body
