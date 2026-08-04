import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_fda_openfda import (
    TOOLS,
    _openfda_drug_enforcement_to_shape_payload,
    handle_animal_veterinary_events,
    handle_device_510k,
    handle_device_events,
    handle_device_recalls,
    handle_drug_enforcement,
    handle_drug_events,
    handle_drug_labels,
    handle_food_enforcement,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_openfda_drug_events_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"safetyreportid": "1234567-1"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_drug_events(
            {"search": "patient.drug.medicinalproduct:aspirin", "limit": 1},
        )
        assert "1234567-1" in result[0].text


@pytest.mark.anyio
async def test_openfda_drug_labels_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"openfda": {"brand_name": ["TYLENOL"]}}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_drug_labels(
            {"search": "openfda.brand_name:tylenol", "limit": 1},
        )
        assert "TYLENOL" in result[0].text


@pytest.mark.anyio
async def test_openfda_drug_enforcement_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"classification": "Class I", "recall_number": "D-001"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_drug_enforcement(
            {"search": "classification:Class I", "limit": 1},
        )
        assert "Class I" in result[0].text


@pytest.mark.anyio
async def test_openfda_device_events_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"event_type": "Malfunction"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_device_events({"search": "device.brand_name:pacemaker"})
        assert "Malfunction" in result[0].text


@pytest.mark.anyio
async def test_openfda_device_recalls_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"recall_number": "Z-1234-2024"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_device_recalls({"search": "classification:Class I"})
        assert "Z-1234-2024" in result[0].text


@pytest.mark.anyio
async def test_openfda_device_510k_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"k_number": "K123456", "applicant": "Medtronic"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_device_510k({"search": "applicant:Medtronic"})
        assert "K123456" in result[0].text


@pytest.mark.anyio
async def test_openfda_food_enforcement_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"reason_for_recall": "Salmonella contamination"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_food_enforcement(
            {"search": "reason_for_recall:salmonella"},
        )
        assert "Salmonella" in result[0].text


@pytest.mark.anyio
async def test_openfda_animal_veterinary_events_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"animal": {"species": "Dog"}}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_animal_veterinary_events({"search": "animal.species:dog"})
        assert "Dog" in result[0].text


@pytest.mark.anyio
async def test_openfda_http_error_propagates():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Boom")
        with pytest.raises(httpx.HTTPError):
            await handle_drug_events({"search": "anything"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for openfda-drug-enforcement.
#
# We bind drug enforcement (recalls), not drug events: events are deeply
# nested FAERS reports (patient.drug[], patient.reaction[]) that would
# need row-explosion to fit the records contract; enforcement records
# are already flat (one row per recall).
# ---------------------------------------------------------------------------


def test_openfda_drug_enforcement_adapter_flattens_results_to_rows():
    raw = {
        "meta": {"results": {"total": 1}},
        "results": [
            {
                "recall_number": "D-001",
                "classification": "Class I",
                "status": "Ongoing",
                "recalling_firm": "Acme Pharma",
                "product_description": "Aspirin 100mg tablets",
                "voluntary_mandated": "Voluntary",
                "country": "United States",
                "state": "NY",
                "reason_for_recall": "Contamination.",
                "recall_initiation_date": "20240101",
            },
        ],
    }
    payload = _openfda_drug_enforcement_to_shape_payload(raw)
    assert payload["total"] == 1
    row = payload["rows"][0]
    assert row["classification"] == "Class I"
    assert row["recalling_firm"] == "Acme Pharma"
    assert payload["default_facets"] == ["classification", "status", "country"]


def test_openfda_drug_enforcement_adapter_handles_missing_results():
    assert _openfda_drug_enforcement_to_shape_payload({})["rows"] == []


def test_drug_enforcement_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "openfda-drug-enforcement")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_openfda_drug_enforcement_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "results": [{"classification": "Class I", "recall_number": "D-001"}],
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_drug_enforcement(
            {"search": "classification:Class I", "limit": 1},
        )
        body = json.loads(result[0].text)
        assert body["rows"][0]["classification"] == "Class I"
        assert "schema" in body
