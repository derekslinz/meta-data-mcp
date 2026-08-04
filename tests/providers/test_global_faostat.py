"""Tests for the global-faostat provider."""

import json
from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.global_faostat import (
    TOOLS,
    FaostatDataParams,
    FaostatListDomainsParams,
    FaostatListItemsParams,
    _faostat_data_to_shape_payload,
    fetch_faostat_data,
    fetch_faostat_list_domains,
    fetch_faostat_list_items,
    handle_faostat_data,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


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


def test_list_domains():
    payload = {
        "data": [{"domain_code": "QCL", "domain_name": "Crops and livestock products"}],
    }
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        result = fetch_faostat_list_domains(FaostatListDomainsParams())
        assert result["data"][0]["domain_code"] == "QCL"
        assert "groupsanddomains" in mock_get.call_args[0][0]


def test_list_items_uses_domain_in_path():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"data": [{"item_code": "56", "item": "Maize"}]})
        result = fetch_faostat_list_items(FaostatListItemsParams(domain_code="QCL"))
        assert result["data"][0]["item"] == "Maize"
        assert "/dimensions/QCL/items" in mock_get.call_args[0][0]


def test_list_items_rejects_empty_domain():
    with pytest.raises(Exception):
        FaostatListItemsParams(domain_code="")


def test_data_endpoint_threads_filters():
    payload = {"data": [{"area": "USA", "item": "Maize", "year": 2022, "value": 1.0}]}
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        params = FaostatDataParams(
            domain_code="QCL",
            area_codes="231",
            item_codes="56",
            element_codes="5510",
            year="2020,2021,2022",
            limit=500,
        )
        result = fetch_faostat_data(params)
        assert result["data"][0]["area"] == "USA"
        url = mock_get.call_args[0][0]
        assert url.endswith("/data/QCL")
        sent = mock_get.call_args[1]["params"]
        assert sent["area"] == "231"
        assert sent["item"] == "56"
        assert sent["element"] == "5510"
        assert sent["year"] == "2020,2021,2022"
        assert sent["limit"] == 500


def test_data_endpoint_omits_unset_filters():
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok({"data": []})
        fetch_faostat_data(FaostatDataParams(domain_code="QCL"))
        sent = mock_get.call_args[1]["params"]
        assert "area" not in sent
        assert "item" not in sent
        assert "element" not in sent
        assert "year" not in sent


def test_data_limit_validation():
    with pytest.raises(Exception):
        FaostatDataParams(domain_code="QCL", limit=0)
    with pytest.raises(Exception):
        FaostatDataParams(domain_code="QCL", limit=10001)


@pytest.mark.anyio
async def test_handle_data():
    payload = {
        "data": [
            {
                "Area": "USA",
                "Item": "Maize",
                "Element": "Production",
                "Year": 2022,
                "Unit": "t",
                "Value": 100,
            },
        ],
    }
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        result = await handle_faostat_data({"domain_code": "QCL"})
        assert "USA" in result[0].text
        assert "points" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for faostat-data.
# ---------------------------------------------------------------------------


def test_faostat_adapter_flattens_rows_to_points():
    raw = {
        "data": [
            {
                "Area": "USA",
                "Item": "Maize",
                "Element": "Production",
                "Year": 2020,
                "Unit": "t",
                "Value": 100.0,
            },
            {
                "Area": "USA",
                "Item": "Maize",
                "Element": "Production",
                "Year": 2021,
                "Unit": "t",
                "Value": 110.0,
            },
        ],
    }
    payload = _faostat_data_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Year", "y": "t"}
    assert len(payload["points"]) == 2
    assert payload["points"][0] == {
        "date": "2020",
        "value": 100.0,
        "series": "USA · Maize · Production",
    }


def test_faostat_adapter_lowercase_fields_also_work():
    raw = {"data": [{"area": "BRA", "year": 2020, "value": 1.5}]}
    payload = _faostat_data_to_shape_payload(raw)
    assert payload["points"] == [{"date": "2020", "value": 1.5, "series": "BRA"}]


def test_faostat_adapter_empty():
    payload = _faostat_data_to_shape_payload({"data": []})
    assert payload["points"] == []
    assert payload["axes"]["x"] == "Year"


def test_faostat_adapter_handles_non_list():
    payload = _faostat_data_to_shape_payload({"data": None})
    assert payload["points"] == []


def test_faostat_adapter_skips_non_numeric():
    raw = {
        "data": [
            {"Area": "USA", "Year": 2020, "Value": "bad"},
            {"Area": "USA", "Year": 2021, "Value": 1.0},
        ],
    }
    payload = _faostat_data_to_shape_payload(raw)
    assert len(payload["points"]) == 1


def test_faostat_data_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "faostat-data")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_faostat_data_returns_shape_payload():
    payload = {
        "data": [
            {
                "Area": "USA",
                "Item": "Maize",
                "Element": "Production",
                "Year": 2020,
                "Unit": "t",
                "Value": 100.0,
            },
        ],
    }
    with patch("httpx.get") as mock_get:
        mock_get.return_value = _ok(payload)
        result = await handle_faostat_data({"domain_code": "QCL"})
        body = json.loads(result[0].text)
        assert body["axes"]["y"] == "t"
        assert body["points"][0]["date"] == "2020"
