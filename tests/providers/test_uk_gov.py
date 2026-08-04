import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.uk_gov import (
    TOOLS,
    _ckan_package_search_to_shape_payload,
    handle_uk_gov_get_dataset,
    handle_uk_gov_get_organization,
    handle_uk_gov_list_groups,
    handle_uk_gov_list_organizations,
    handle_uk_gov_list_recently_changed,
    handle_uk_gov_list_tags,
    handle_uk_gov_search_datasets,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_uk_gov_search_datasets_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {
                "count": 1,
                "results": [{"name": "uk-house-prices", "title": "UK House Prices"}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_gov_search_datasets({"q": "house prices", "rows": 5})
        assert len(result) == 1
        assert "UK House Prices" in result[0].text


@pytest.mark.anyio
async def test_uk_gov_search_datasets_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network down")

        with pytest.raises(httpx.HTTPError):
            await handle_uk_gov_search_datasets({"q": "anything"})


@pytest.mark.anyio
async def test_uk_gov_get_dataset_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {
                "name": "uk-house-prices",
                "title": "UK House Prices",
                "resources": [{"format": "CSV"}],
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_gov_get_dataset({"id": "uk-house-prices"})
        assert "UK House Prices" in result[0].text


@pytest.mark.anyio
async def test_uk_gov_list_organizations_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": [{"name": "office-for-national-statistics"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_gov_list_organizations({"limit": 10})
        assert "office-for-national-statistics" in result[0].text


@pytest.mark.anyio
async def test_uk_gov_get_organization_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {
                "name": "office-for-national-statistics",
                "title": "Office for National Statistics",
            },
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_gov_get_organization(
            {"id": "office-for-national-statistics"},
        )
        assert "Office for National Statistics" in result[0].text


@pytest.mark.anyio
async def test_uk_gov_list_groups_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": [{"name": "health", "display_name": "Health"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_gov_list_groups({})
        assert "Health" in result[0].text


@pytest.mark.anyio
async def test_uk_gov_list_tags_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": ["transport", "transport-policy"],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_gov_list_tags({"query": "trans"})
        assert "transport" in result[0].text


@pytest.mark.anyio
async def test_uk_gov_list_recently_changed_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": [{"activity_type": "changed package"}],
        }
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_uk_gov_list_recently_changed({"limit": 5})
        assert "changed package" in result[0].text


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for uk-gov-search-datasets.
#
# - Adapter flattens CKAN package_search's nested {result: {results: [...]}}
#   to the records shape primitive's {rows, schema, default_facets}.
# - Tool's _meta wires it to ui://meta-data-mcp/shape/records/v1 so the
#   MCP Apps host renders the dataset list inline.
# ---------------------------------------------------------------------------


def test_adapter_flattens_ckan_package_search_to_rows():
    raw = {
        "success": True,
        "result": {
            "count": 1,
            "results": [
                {
                    "name": "uk-house-prices",
                    "title": "UK House Prices",
                    "notes": "Monthly average house prices.",
                    "organization": {"title": "Office for National Statistics"},
                    "tags": [{"display_name": "Housing"}, {"name": "prices"}],
                    "groups": [{"title": "Economy"}],
                    "license_title": "Open Government Licence",
                    "num_resources": 2,
                    "resources": [{"format": "csv"}, {"format": "json"}],
                    "metadata_created": "2020-01-01T00:00:00",
                    "metadata_modified": "2024-06-01T00:00:00",
                },
            ],
        },
    }
    payload = _ckan_package_search_to_shape_payload(raw)
    assert payload["count"] == 1
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["name"] == "uk-house-prices"
    assert row["title"] == "UK House Prices"
    assert row["organization"] == "Office for National Statistics"
    assert row["license"] == "Open Government Licence"
    assert "Housing" in row["tags"]
    assert row["groups"] == "Economy"
    assert row["num_resources"] == 2
    assert "CSV" in row["formats"] and "JSON" in row["formats"]
    assert payload["default_facets"] == ["organization", "license", "formats"]
    col_names = [c["name"] for c in payload["schema"]["columns"]]
    assert "title" in col_names and "organization" in col_names


def test_adapter_handles_empty_results():
    payload = _ckan_package_search_to_shape_payload(
        {"success": True, "result": {"count": 0, "results": []}},
    )
    assert payload["rows"] == []
    assert payload["count"] == 0


def test_adapter_handles_missing_result_key():
    payload = _ckan_package_search_to_shape_payload({})
    assert payload["rows"] == []
    assert "schema" in payload


def test_adapter_truncates_long_notes():
    raw = {
        "result": {
            "results": [
                {"name": "x", "title": "X", "notes": "z" * 1000},
            ],
        },
    }
    payload = _ckan_package_search_to_shape_payload(raw)
    notes = payload["rows"][0]["notes"]
    # 500 chars + ellipsis (chars stripped/added defensively).
    assert len(notes) <= 501
    assert notes.endswith("…")


def test_search_datasets_tool_binds_to_records_shape_primitive():
    """uk-gov-search-datasets must point at the canonical records shape URI."""
    tool = next(t for t in TOOLS if t.name == "uk-gov-search-datasets")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}, (
        f"uk-gov-search-datasets is not bound to {RECORDS_URI}"
    )
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI


@pytest.mark.anyio
async def test_uk_gov_search_datasets_returns_shape_payload():
    """Handler now returns the records shape primitive's payload format."""
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = {
            "success": True,
            "result": {
                "count": 1,
                "results": [
                    {
                        "name": "uk-house-prices",
                        "title": "UK House Prices",
                        "organization": {"title": "ONS"},
                        "license_title": "OGL",
                    },
                ],
            },
        }
        mock_get.return_value.raise_for_status = Mock()
        result = await handle_uk_gov_search_datasets({"q": "house prices"})
        body = json.loads(result[0].text)
        assert "rows" in body and "schema" in body
        assert body["rows"][0]["title"] == "UK House Prices"
        assert body["rows"][0]["organization"] == "ONS"
