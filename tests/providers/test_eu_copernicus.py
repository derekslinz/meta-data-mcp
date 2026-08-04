import json
from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.eu_copernicus import (
    TOOLS,
    ListCollectionsParams,
    ProductMetadataParams,
    SearchProductsParams,
    _copernicus_search_results_to_shape_payload,
    fetch_product_metadata,
    handle_get_product_metadata,
    handle_list_collections,
    handle_search_products,
    list_copernicus_collections,
    search_copernicus_products,
)
from meta_data_mcp.ui_resources.shape_geofeatures_v1 import URI as GEOFEATURES_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_stac_collections():
    return {
        "collections": [
            {
                "id": "sentinel-2a-l2a",
                "title": "Sentinel-2A L2A",
                "description": "Global land surface",
            },
            {
                "id": "sentinel-1-grd",
                "title": "Sentinel-1 GRD",
                "description": "Radar imaging",
            },
        ],
    }


@pytest.fixture
def mock_stac_search_results():
    return {
        "features": [
            {
                "id": "S2A_MSIL2A_20230101",
                "bbox": [0, 0, 1, 1],
                "properties": {
                    "datetime": "2023-01-01T12:00:00Z",
                    "eo:cloud_cover": 10.5,
                },
            },
        ],
    }


@pytest.fixture
def mock_odata_product():
    return {
        "Id": "uuid-1234",
        "Name": "S2A_MSIL2A_20230101",
        "ContentDate": {"Start": "2023-01-01T12:00:00Z", "End": "2023-01-01T12:00:05Z"},
    }


def test_list_copernicus_collections(mock_stac_collections):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_stac_collections
        mock_get.return_value.raise_for_status = Mock()

        params = ListCollectionsParams(limit=10)
        collections = list_copernicus_collections(params)
        assert len(collections) == 2
        assert collections[0].id == "sentinel-2a-l2a"


@pytest.mark.anyio
async def test_handle_list_collections(mock_stac_collections):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_stac_collections
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_list_collections({})
        assert len(result) == 1
        assert "sentinel-2a-l2a" in result[0].text


def test_search_copernicus_products(mock_stac_search_results):
    with patch("httpx.post") as mock_post:
        mock_post.return_value.json.return_value = mock_stac_search_results
        mock_post.return_value.raise_for_status = Mock()

        params = SearchProductsParams(collection="sentinel-2a-l2a", limit=5)
        response = search_copernicus_products(params)
        assert len(response["features"]) == 1
        assert response["features"][0]["id"] == "S2A_MSIL2A_20230101"


@pytest.mark.anyio
async def test_handle_search_products(mock_stac_search_results):
    with patch("httpx.post") as mock_post:
        mock_post.return_value.json.return_value = mock_stac_search_results
        mock_post.return_value.raise_for_status = Mock()

        result = await handle_search_products({"collection": "sentinel-2a-l2a"})
        assert len(result) == 1
        assert "S2A_MSIL2A_20230101" in result[0].text
        assert "10.5" in result[0].text


def test_fetch_product_metadata(mock_odata_product):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_odata_product
        mock_get.return_value.raise_for_status = Mock()

        params = ProductMetadataParams(product_id="uuid-1234")
        response = fetch_product_metadata(params)
        assert response["Id"] == "uuid-1234"
        assert response["Name"] == "S2A_MSIL2A_20230101"


@pytest.mark.anyio
async def test_handle_get_product_metadata(mock_odata_product):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_odata_product
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_get_product_metadata({"product_id": "uuid-1234"})
        assert len(result) == 1
        assert "uuid-1234" in result[0].text
        assert "S2A_MSIL2A_20230101" in result[0].text


@pytest.mark.anyio
async def test_handle_list_collections_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("STAC API is down")
        with pytest.raises(httpx.HTTPError):
            await handle_list_collections({})


@pytest.mark.anyio
async def test_handle_search_products_error():
    with patch("httpx.post") as mock_post:
        mock_post.side_effect = httpx.HTTPError("STAC Search failed")
        with pytest.raises(httpx.HTTPError):
            await handle_search_products({"collection": "sentinel-2a-l2a"})


@pytest.mark.anyio
async def test_handle_get_product_metadata_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("OData timeout")
        with pytest.raises(httpx.HTTPError):
            await handle_get_product_metadata({"product_id": "uuid-1234"})


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for copernicus-search-products.
# ---------------------------------------------------------------------------


def test_adapter_collapses_bbox_to_centroid():
    raw = {
        "features": [
            {
                "id": "S2A_MSIL2A_20230101",
                "bbox": [0.0, 10.0, 2.0, 14.0],
                "properties": {
                    "datetime": "2023-01-01T12:00:00Z",
                    "eo:cloud_cover": 10.5,
                },
            },
        ],
    }
    payload = _copernicus_search_results_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    feature = payload["features"][0]
    # Centroid of bbox = ((0+2)/2, (10+14)/2)
    assert feature["lon"] == 1.0
    assert feature["lat"] == 12.0
    assert feature["attrs"]["id"] == "S2A_MSIL2A_20230101"
    assert feature["attrs"]["bbox"] == [0.0, 10.0, 2.0, 14.0]
    assert feature["attrs"]["cloud_cover"] == 10.5
    assert feature["attrs"]["datetime"] == "2023-01-01T12:00:00Z"


def test_adapter_handles_empty_features():
    payload = _copernicus_search_results_to_shape_payload({"features": []})
    assert payload == {"features": []}


def test_adapter_handles_missing_structure():
    assert _copernicus_search_results_to_shape_payload({}) == {"features": []}
    assert _copernicus_search_results_to_shape_payload("err") == {"features": []}


def test_adapter_skips_features_without_valid_bbox():
    raw = {
        "features": [
            {"id": "no-bbox", "properties": {}},
            {"id": "short-bbox", "bbox": [0, 0]},
            {"id": "bad-bbox", "bbox": ["x", 0, 1, 1]},
            {"id": "out-of-range", "bbox": [0, 200, 1, 300]},
            {"id": "ok", "bbox": [0.0, 0.0, 1.0, 1.0]},
        ],
    }
    payload = _copernicus_search_results_to_shape_payload(raw)
    assert len(payload["features"]) == 1
    assert payload["features"][0]["attrs"]["id"] == "ok"


def test_search_products_tool_binds_to_geofeatures_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "copernicus-search-products")
    assert tool.meta == {"ui": {"resourceUri": GEOFEATURES_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == GEOFEATURES_URI


@pytest.mark.anyio
async def test_handle_search_products_returns_shape_payload(
    mock_stac_search_results,
):
    with patch("httpx.post") as mock_post:
        mock_post.return_value.json.return_value = mock_stac_search_results
        mock_post.return_value.raise_for_status = Mock()

        result = await handle_search_products({"collection": "sentinel-2a-l2a"})
        body = json.loads(result[0].text)
        assert body["features"][0]["lat"] == 0.5
        assert body["features"][0]["lon"] == 0.5
        assert body["features"][0]["attrs"]["id"] == "S2A_MSIL2A_20230101"
        assert body["features"][0]["attrs"]["cloud_cover"] == 10.5
