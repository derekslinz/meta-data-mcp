import json
from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.eu_eurostat import (
    TOOLS,
    EurostatDataParams,
    EurostatListDatasetsParams,
    EurostatMetadataParams,
    _eurostat_dataset_to_shape_payload,
    fetch_eurostat_data,
    fetch_eurostat_metadata,
    handle_eurostat_get_dataset,
    handle_eurostat_get_metadata,
    handle_eurostat_list_datasets,
    list_eurostat_datasets,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_eurostat_toc_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<nt:tree xmlns:nt="urn:eu.europa.ec.eurostat.navtree">
  <nt:leaf type="table">
    <code>NAMA_10_GDP</code>
    <nt:code>nama_10_gdp</nt:code>
    <nt:title language="en">GDP and main components</nt:title>
    <nt:lastUpdate>2023-01-01</nt:lastUpdate>
    <nt:lastDataUpdate>2023-01-02</nt:lastDataUpdate>
  </nt:leaf>
  <nt:leaf type="dataset">
    <nt:code>prc_hicp_manr</nt:code>
    <nt:title language="en">HICP - monthly data</nt:title>
    <nt:lastUpdate>2023-02-01</nt:lastUpdate>
  </nt:leaf>
</nt:tree>"""


@pytest.fixture
def mock_eurostat_data_response():
    """Minimal but valid JSON-stat 2.0: 1-D over time."""
    return {
        "version": "2.0",
        "class": "dataset",
        "label": "GDP and main components",
        "id": ["time"],
        "size": [2],
        "dimension": {
            "time": {
                "label": "Time period",
                "category": {"index": {"2020": 0, "2021": 1}},
            },
        },
        "value": [100.5, 102.3],
    }


@pytest.fixture
def mock_eurostat_2d_response():
    """2-D JSON-stat: geo × time, sparse value object."""
    return {
        "version": "2.0",
        "class": "dataset",
        "label": "GDP",
        "id": ["geo", "time"],
        "size": [2, 2],
        "dimension": {
            "geo": {
                "label": "Geo",
                "category": {"index": {"DE": 0, "FR": 1}},
            },
            "time": {
                "label": "Time",
                "category": {"index": {"2020": 0, "2021": 1}},
            },
        },
        # geo=DE, time=2020 -> idx 0; DE, 2021 -> 1; FR, 2020 -> 2; FR, 2021 -> 3
        "value": {"0": 1.0, "1": 1.1, "2": 2.0, "3": 2.1},
    }


@pytest.fixture
def mock_eurostat_metadata_response():
    return {
        "id": "nama_10_gdp",
        "label": "GDP and main components",
        "dimension": {
            "geo": {"label": "Geopolitical entity"},
            "time": {"label": "Time"},
        },
    }


def test_list_eurostat_datasets(mock_eurostat_toc_xml):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.content = mock_eurostat_toc_xml.encode("utf-8")
        mock_get.return_value.raise_for_status = Mock()

        # Test listing all
        params = EurostatListDatasetsParams(limit=10)
        datasets = list_eurostat_datasets(params)
        assert len(datasets) == 2
        assert datasets[0].code == "nama_10_gdp"
        assert datasets[1].code == "prc_hicp_manr"

        # Test search
        params = EurostatListDatasetsParams(search="GDP")
        datasets = list_eurostat_datasets(params)
        assert len(datasets) == 1
        assert datasets[0].code == "nama_10_gdp"


@pytest.mark.anyio
async def test_handle_eurostat_list_datasets(mock_eurostat_toc_xml):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.content = mock_eurostat_toc_xml.encode("utf-8")
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_eurostat_list_datasets({"search": "HICP"})
        assert len(result) == 1
        assert "prc_hicp_manr" in result[0].text


def test_fetch_eurostat_data(mock_eurostat_data_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_eurostat_data_response
        mock_get.return_value.raise_for_status = Mock()

        params = EurostatDataParams(dataset_code="nama_10_gdp")
        response = fetch_eurostat_data(params)
        assert response["version"] == "2.0"
        assert response["value"][0] == 100.5


@pytest.mark.anyio
async def test_handle_eurostat_get_dataset(mock_eurostat_data_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_eurostat_data_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_eurostat_get_dataset({"dataset_code": "nama_10_gdp"})
        assert len(result) == 1
        assert "100.5" in result[0].text
        assert "points" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for eurostat-get-dataset.
# ---------------------------------------------------------------------------


def test_eurostat_adapter_1d_time_series(mock_eurostat_data_response):
    payload = _eurostat_dataset_to_shape_payload(mock_eurostat_data_response)
    assert payload["axes"] == {"x": "Time period", "y": "GDP and main components"}
    assert payload["points"] == [
        {"date": "2020", "value": 100.5},
        {"date": "2021", "value": 102.3},
    ]


def test_eurostat_adapter_2d_with_geo_series(mock_eurostat_2d_response):
    payload = _eurostat_dataset_to_shape_payload(mock_eurostat_2d_response)
    assert payload["axes"] == {"x": "Time", "y": "GDP"}
    # 2 geos × 2 years = 4 points, all with non-empty 'series'.
    assert len(payload["points"]) == 4
    series = {p["series"] for p in payload["points"]}
    assert series == {"DE", "FR"}


def test_eurostat_adapter_empty():
    payload = _eurostat_dataset_to_shape_payload({})
    assert payload["points"] == []


def test_eurostat_adapter_no_time_dim_defers():
    payload = _eurostat_dataset_to_shape_payload(
        {
            "id": ["geo"],
            "size": [1],
            "dimension": {"geo": {"category": {"index": {"DE": 0}}}},
            "value": [1.0],
        },
    )
    assert payload["points"] == []


def test_eurostat_adapter_skips_non_numeric(mock_eurostat_data_response):
    raw = dict(mock_eurostat_data_response)
    raw["value"] = [None, "NA"]
    payload = _eurostat_dataset_to_shape_payload(raw)
    assert payload["points"] == []


def test_eurostat_get_dataset_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "eurostat-get-dataset")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_eurostat_get_dataset_returns_shape_payload(mock_eurostat_data_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_eurostat_data_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_eurostat_get_dataset({"dataset_code": "nama_10_gdp"})
        body = json.loads(result[0].text)
        assert "points" in body
        assert body["axes"]["x"] == "Time period"


def test_fetch_eurostat_metadata(mock_eurostat_metadata_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_eurostat_metadata_response
        mock_get.return_value.raise_for_status = Mock()

        params = EurostatMetadataParams(dataset_code="nama_10_gdp")
        response = fetch_eurostat_metadata(params)
        assert response["id"] == "nama_10_gdp"
        assert "geo" in response["dimension"]


@pytest.mark.anyio
async def test_handle_eurostat_get_metadata(mock_eurostat_metadata_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_eurostat_metadata_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_eurostat_get_metadata({"dataset_code": "nama_10_gdp"})
        assert len(result) == 1
        assert "Geopolitical entity" in result[0].text
