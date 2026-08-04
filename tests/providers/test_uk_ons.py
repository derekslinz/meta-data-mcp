import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.uk_ons import (
    TOOLS,
    _ons_get_observations_to_shape_payload,
    handle_get_codelist,
    handle_get_dataset,
    handle_get_edition,
    handle_get_observations,
    handle_list_codelists,
    handle_list_datasets,
    handle_list_editions,
    handle_list_versions,
)
from meta_data_mcp.ui_resources.shape_timeseries_v1 import URI as TIMESERIES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_ons_list_datasets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "items": [{"id": "cpih01", "title": "Consumer Prices Index incl. housing"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_datasets()
        assert "cpih01" in result[0].text


@pytest.mark.anyio
async def test_ons_list_datasets_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_list_datasets()


@pytest.mark.anyio
async def test_ons_get_dataset_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "id": "cpih01",
            "title": "CPIH",
            "description": "Consumer Prices Index incl. owner occupiers' housing costs",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_dataset({"id": "cpih01"})
        assert "CPIH" in result[0].text


@pytest.mark.anyio
async def test_ons_get_dataset_missing_arg():
    with pytest.raises(ValueError):
        await handle_get_dataset({})


@pytest.mark.anyio
async def test_ons_list_editions_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "items": [{"edition": "time-series", "state": "published"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_editions({"id": "cpih01"})
        assert "time-series" in result[0].text


@pytest.mark.anyio
async def test_ons_get_edition_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "edition": "time-series",
            "state": "published",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_edition({"id": "cpih01", "edition": "time-series"})
        assert "time-series" in result[0].text


@pytest.mark.anyio
async def test_ons_get_edition_missing_args():
    with pytest.raises(ValueError):
        await handle_get_edition({"id": "cpih01"})


@pytest.mark.anyio
async def test_ons_list_versions_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "items": [{"version": 1, "state": "published"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_versions({"id": "cpih01", "edition": "time-series"})
        assert "version" in result[0].text


@pytest.mark.anyio
async def test_ons_get_observations_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "observations": [
                {"observation": "123.4", "dimensions": {"time": "2023-Jan"}},
            ],
            "total_observations": 1,
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_observations(
            {
                "id": "cpih01",
                "edition": "time-series",
                "version": "1",
                "time": "2023-Jan",
                "geography": "K02000001",
            },
        )
        assert "123.4" in result[0].text
        assert "points" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: shape primitive binding for uk-ons-get-observations.
# ---------------------------------------------------------------------------


def test_ons_adapter_flattens_observations_with_dict_dims():
    raw = {
        "observations": [
            {
                "observation": "1.5",
                "dimensions": {
                    "time": {"id": "2023-01", "label": "January 2023"},
                    "geography": {"id": "K02000001", "label": "UK"},
                },
            },
            {
                "observation": "1.6",
                "dimensions": {
                    "time": {"id": "2023-02"},
                    "geography": {"id": "K02000001", "label": "UK"},
                },
            },
        ],
    }
    payload = _ons_get_observations_to_shape_payload(raw)
    assert payload["axes"] == {"x": "Time", "y": "Observation"}
    assert len(payload["points"]) == 2
    assert payload["points"][0]["series"] == "UK"


def test_ons_adapter_handles_string_dims():
    raw = {"observations": [{"observation": "1.5", "dimensions": {"time": "2023-Jan"}}]}
    payload = _ons_get_observations_to_shape_payload(raw)
    assert payload["points"] == [{"date": "2023-Jan", "value": 1.5}]


def test_ons_adapter_empty():
    payload = _ons_get_observations_to_shape_payload({})
    assert payload["points"] == []


def test_ons_adapter_skips_non_numeric():
    raw = {
        "observations": [
            {"observation": "bad", "dimensions": {"time": "2023-01"}},
            {"observation": "2.0", "dimensions": {"time": "2023-02"}},
        ],
    }
    payload = _ons_get_observations_to_shape_payload(raw)
    assert len(payload["points"]) == 1


def test_ons_get_observations_tool_bound_to_timeseries_shape():
    tool = next(t for t in TOOLS if t.name == "uk-ons-get-observations")
    assert tool.meta == {"ui": {"resourceUri": TIMESERIES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == TIMESERIES_URI


@pytest.mark.anyio
async def test_ons_get_observations_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "observations": [
                {"observation": "1.5", "dimensions": {"time": "2023-Jan"}}
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_observations(
            {"id": "x", "edition": "y", "version": "1"},
        )
        body = json.loads(result[0].text)
        assert body["points"][0]["value"] == 1.5


@pytest.mark.anyio
async def test_ons_get_observations_missing_args():
    with pytest.raises(ValueError):
        await handle_get_observations({"id": "cpih01", "edition": "time-series"})


@pytest.mark.anyio
async def test_ons_list_codelists_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "items": [{"id": "uk-only", "name": "UK"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_codelists()
        assert "uk-only" in result[0].text


@pytest.mark.anyio
async def test_ons_get_codelist_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "id": "uk-only",
            "name": "UK",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_codelist({"id": "uk-only"})
        assert "uk-only" in result[0].text
