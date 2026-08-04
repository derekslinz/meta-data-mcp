import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_gbif import (
    TOOLS,
    _gbif_occurrences_to_shape_payload,
    handle_gbif_get_dataset,
    handle_gbif_get_occurrence,
    handle_gbif_get_occurrence_counts,
    handle_gbif_get_species,
    handle_gbif_get_species_name_suggest,
    handle_gbif_list_datasets,
    handle_gbif_search_occurrences,
    handle_gbif_search_species,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_gbif_search_occurrences_success():
    """Now returns the geofeatures shape payload — only records with
    usable Darwin Core decimalLatitude/decimalLongitude make it through.
    """
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "results": [
                {
                    "key": 1,
                    "scientificName": "Puma concolor",
                    "decimalLatitude": 37.7,
                    "decimalLongitude": -122.4,
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_gbif_search_occurrences(
            {"scientificName": "Puma concolor", "country": "US", "limit": 5},
        )
        assert "Puma concolor" in result[0].text


@pytest.mark.anyio
async def test_gbif_search_occurrences_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("GBIF unavailable")

        with pytest.raises(httpx.HTTPError):
            await handle_gbif_search_occurrences({"scientificName": "Puma concolor"})


@pytest.mark.anyio
async def test_gbif_get_occurrence_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "key": 9999,
            "scientificName": "Quercus alba",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_gbif_get_occurrence({"key": 9999})
        assert "Quercus alba" in result[0].text


@pytest.mark.anyio
async def test_gbif_search_species_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "results": [{"key": 2877951, "scientificName": "Quercus"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_gbif_search_species({"q": "Quercus"})
        assert "Quercus" in result[0].text


@pytest.mark.anyio
async def test_gbif_get_species_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "key": 2877951,
            "scientificName": "Quercus L.",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_gbif_get_species({"key": 2877951})
        assert "Quercus L." in result[0].text


@pytest.mark.anyio
async def test_gbif_get_species_name_suggest_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"key": 2877951, "scientificName": "Quercus"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_gbif_get_species_name_suggest({"q": "Querc"})
        assert "Quercus" in result[0].text


@pytest.mark.anyio
async def test_gbif_list_datasets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "results": [{"key": "abc-uuid", "title": "Test Dataset"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_gbif_list_datasets({"type": "OCCURRENCE", "limit": 5})
        assert "Test Dataset" in result[0].text


@pytest.mark.anyio
async def test_gbif_get_dataset_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "key": "abc-uuid",
            "title": "eBird Observation Dataset",
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_gbif_get_dataset({"key": "abc-uuid"})
        assert "eBird Observation Dataset" in result[0].text


@pytest.mark.anyio
async def test_gbif_get_occurrence_counts_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = 12345678
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_gbif_get_occurrence_counts({"country": "US"})
        assert "12345678" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for gbif-search-occurrences.
# ---------------------------------------------------------------------------


def test_adapter_maps_occurrences_to_features():
    raw = {
        "count": 2,
        "results": [
            {
                "key": 1,
                "scientificName": "Puma concolor",
                "decimalLatitude": 37.7,
                "decimalLongitude": -122.4,
                "country": "US",
            },
            {
                "key": 2,
                "scientificName": "Quercus alba",
                "decimalLatitude": 40.0,
                "decimalLongitude": -74.0,
            },
        ],
    }
    payload = _gbif_occurrences_to_shape_payload(raw)
    assert len(payload["features"]) == 2
    assert payload["features"][0]["lat"] == 37.7
    assert payload["features"][0]["lon"] == -122.4
    assert payload["features"][0]["attrs"]["scientificName"] == "Puma concolor"
    assert payload["features"][0]["attrs"]["country"] == "US"
    # Coordinate keys stripped from attrs (already promoted)
    assert "decimalLatitude" not in payload["features"][0]["attrs"]
    assert "decimalLongitude" not in payload["features"][0]["attrs"]


def test_adapter_handles_empty_results():
    payload = _gbif_occurrences_to_shape_payload({"count": 0, "results": []})
    assert payload == {"features": []}


def test_adapter_handles_non_dict_response():
    assert _gbif_occurrences_to_shape_payload([]) == {"features": []}
    assert _gbif_occurrences_to_shape_payload("error") == {"features": []}


def test_adapter_skips_occurrences_without_coords():
    """Many GBIF records (esp. herbarium specimens) lack decimal coords —
    those are dropped silently.
    """
    raw = {
        "results": [
            {"key": 1, "scientificName": "no coords"},
            {
                "key": 2,
                "decimalLatitude": "bad",
                "decimalLongitude": -74.0,
            },
            {
                "key": 3,
                "decimalLatitude": 200.0,  # out of range
                "decimalLongitude": -74.0,
            },
            {
                "key": 4,
                "decimalLatitude": 40.0,
                "decimalLongitude": -74.0,
            },
        ],
    }
    payload = _gbif_occurrences_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["key"] == 4


def test_search_occurrences_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "gbif-search-occurrences")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_gbif_search_occurrences_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "count": 1,
            "results": [
                {
                    "key": 1,
                    "scientificName": "Puma concolor",
                    "decimalLatitude": 37.7,
                    "decimalLongitude": -122.4,
                },
            ],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_gbif_search_occurrences(
            {"scientificName": "Puma concolor"},
        )
        body = json.loads(result[0].text)
        assert body["features"][0]["lat"] == 37.7
        assert body["features"][0]["lon"] == -122.4
        assert body["features"][0]["attrs"]["scientificName"] == "Puma concolor"
