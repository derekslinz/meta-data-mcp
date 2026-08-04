import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.cern_opendata import (
    TOOLS,
    _cern_search_to_shape_payload,
    handle_get_record,
    handle_list_collections,
    handle_search_by_experiment,
    handle_search_records,
    handle_search_software,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_cern_search_records_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hits": {"hits": [{"metadata": {"title": "Higgs boson dataset"}}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_records({"q": "higgs", "size": 5})
        assert len(result) == 1
        assert "Higgs" in result[0].text


@pytest.mark.anyio
async def test_cern_get_record_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "metadata": {"recid": 12345, "title": "CMS run 2011"},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_record({"record_id": 12345})
        assert "CMS run 2011" in result[0].text


@pytest.mark.anyio
async def test_cern_get_record_missing_id():
    with pytest.raises(ValueError, match="record_id is required"):
        await handle_get_record({})


@pytest.mark.anyio
async def test_cern_list_collections_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hits": {"hits": [{"metadata": {"title": "Dataset A"}}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_collections({"size": 10})
        assert "Dataset A" in result[0].text


@pytest.mark.anyio
async def test_cern_search_by_experiment_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hits": {
                "hits": [{"metadata": {"experiment": "CMS", "title": "CMS analysis"}}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_by_experiment(
            {"experiment": "CMS", "q": "jets", "size": 5},
        )
        assert "CMS analysis" in result[0].text


@pytest.mark.anyio
async def test_cern_search_by_experiment_missing_experiment():
    with pytest.raises(ValueError, match="experiment is required"):
        await handle_search_by_experiment({})


@pytest.mark.anyio
async def test_cern_search_software_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hits": {"hits": [{"metadata": {"title": "CMSSW Release"}}]},
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_search_software({"q": "cmssw", "size": 5})
        assert "CMSSW" in result[0].text


@pytest.mark.anyio
async def test_cern_http_error_propagates():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Boom")
        with pytest.raises(httpx.HTTPError):
            await handle_search_records({"q": "anything"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for cern-search-records.
# ---------------------------------------------------------------------------


def test_cern_adapter_flattens_invenio_hits_to_rows():
    raw = {
        "hits": {
            "hits": [
                {
                    "id": 14000,
                    "metadata": {
                        "title": "Higgs boson dataset",
                        "experiment": "CMS",
                        "type": {"primary": "Dataset"},
                        "collections": ["CMS-Primary-Datasets"],
                        "accelerator": "LHC",
                        "collision_energy": "13 TeV",
                        "year": 2015,
                        "publication_date": "2024-04-01",
                    },
                },
            ],
            "total": 1,
        },
    }
    payload = _cern_search_to_shape_payload(raw)
    assert payload["total"] == 1
    row = payload["rows"][0]
    assert row["title"] == "Higgs boson dataset"
    assert row["experiment"] == "CMS"
    assert row["type"] == "Dataset"
    assert "CMS-Primary-Datasets" in row["collections"]
    assert payload["default_facets"] == ["experiment", "type", "accelerator"]


def test_cern_adapter_handles_missing_hits():
    assert _cern_search_to_shape_payload({})["rows"] == []
    assert _cern_search_to_shape_payload({"hits": {}})["rows"] == []


def test_cern_adapter_handles_list_experiment():
    raw = {
        "hits": {
            "hits": [{"metadata": {"title": "Multi", "experiment": ["CMS", "ATLAS"]}}],
        },
    }
    payload = _cern_search_to_shape_payload(raw)
    assert "CMS" in payload["rows"][0]["experiment"]
    assert "ATLAS" in payload["rows"][0]["experiment"]


def test_search_records_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "cern-search-records")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_cern_search_records_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "hits": {"hits": [{"metadata": {"title": "Higgs boson dataset"}}]},
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_search_records({"q": "higgs"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["title"] == "Higgs boson dataset"
        assert "schema" in body
