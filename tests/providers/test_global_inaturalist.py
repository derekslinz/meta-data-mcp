import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.global_inaturalist import (
    TOOLS,
    _inaturalist_observations_to_shape_payload,
    handle_inaturalist_get_observation,
    handle_inaturalist_get_place,
    handle_inaturalist_get_taxon,
    handle_inaturalist_get_user,
    handle_inaturalist_list_places,
    handle_inaturalist_list_projects,
    handle_inaturalist_search_observations,
    handle_inaturalist_search_taxa,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_inaturalist_search_observations_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total_results": 1,
            "results": [{"id": 42, "species_guess": "Western Bluebird"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_inaturalist_search_observations(
            {"taxon_name": "Sialia mexicana", "per_page": 5},
        )
        assert "Western Bluebird" in result[0].text


@pytest.mark.anyio
async def test_inaturalist_search_observations_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("iNaturalist unavailable")

        with pytest.raises(httpx.HTTPError):
            await handle_inaturalist_search_observations({"q": "owl"})


@pytest.mark.anyio
async def test_inaturalist_get_observation_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total_results": 1,
            "results": [{"id": 100, "species_guess": "Mountain Lion"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_inaturalist_get_observation({"id": 100})
        assert "Mountain Lion" in result[0].text


@pytest.mark.anyio
async def test_inaturalist_search_taxa_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total_results": 1,
            "results": [{"id": 1, "name": "Quercus alba", "rank": "species"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_inaturalist_search_taxa(
            {"q": "Quercus", "rank": "species"},
        )
        assert "Quercus alba" in result[0].text


@pytest.mark.anyio
async def test_inaturalist_get_taxon_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total_results": 1,
            "results": [{"id": 50, "name": "Felis catus"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_inaturalist_get_taxon({"id": 50})
        assert "Felis catus" in result[0].text


@pytest.mark.anyio
async def test_inaturalist_list_places_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total_results": 1,
            "results": [{"id": 7, "display_name": "California, US"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_inaturalist_list_places({"q": "Calif"})
        assert "California, US" in result[0].text


@pytest.mark.anyio
async def test_inaturalist_get_place_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total_results": 1,
            "results": [{"id": 14, "display_name": "Yosemite National Park"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_inaturalist_get_place({"id": 14})
        assert "Yosemite National Park" in result[0].text


@pytest.mark.anyio
async def test_inaturalist_list_projects_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total_results": 1,
            "results": [{"id": 99, "title": "City Nature Challenge"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_inaturalist_list_projects({"q": "City Nature"})
        assert "City Nature Challenge" in result[0].text


@pytest.mark.anyio
async def test_inaturalist_get_user_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total_results": 1,
            "results": [{"id": 1, "login": "kueda", "name": "Ken-ichi Ueda"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_inaturalist_get_user({"id": "kueda"})
        assert "Ken-ichi Ueda" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for inaturalist-search-observations.
# ---------------------------------------------------------------------------


def test_inaturalist_adapter_flattens_observations_to_rows():
    raw = {
        "total_results": 1,
        "page": 1,
        "per_page": 30,
        "results": [
            {
                "id": 42,
                "taxon": {
                    "name": "Quercus alba",
                    "preferred_common_name": "White Oak",
                    "rank": "species",
                    "iconic_taxon_name": "Plantae",
                },
                "place_guess": "North Carolina",
                "observed_on": "2024-04-01",
                "user": {"login": "naturalist1"},
                "quality_grade": "research",
                "license_code": "cc-by-nc",
                "uri": "https://inat.example/observations/42",
            },
        ],
    }
    payload = _inaturalist_observations_to_shape_payload(raw)
    assert payload["total_results"] == 1
    row = payload["rows"][0]
    assert row["scientific_name"] == "Quercus alba"
    assert row["common_name"] == "White Oak"
    assert row["user"] == "naturalist1"
    assert payload["default_facets"] == ["iconic_taxon", "quality_grade", "rank"]


def test_inaturalist_adapter_handles_missing_results():
    assert _inaturalist_observations_to_shape_payload({})["rows"] == []


def test_search_observations_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "inaturalist-search-observations")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_inaturalist_search_observations_returns_shape_payload():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "total_results": 1,
            "results": [
                {"id": 1, "taxon": {"name": "Quercus alba"}, "place_guess": "NC"},
            ],
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_inaturalist_search_observations({"q": "oak"})
        body = json.loads(result[0].text)
        assert body["rows"][0]["scientific_name"] == "Quercus alba"
        assert "schema" in body
