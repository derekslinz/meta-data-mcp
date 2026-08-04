import json
from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.nl_tweedekamer import (
    TOOLS,
    TkQueryEntityParams,
    _tk_odata_to_shape_payload,
    handle_tk_query,
    list_tk_entities,
    query_tk_entity,
)
from meta_data_mcp.ui_resources.shape_records_v1 import URI as RECORDS_URI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_odata_response():
    return {
        "value": [
            {
                "Id": "fab499e2-93b6-4bba-8266-00014175f6a6",
                "Voornamen": "Jan",
                "Achternaam": "de Vries",
            },
        ],
    }


def test_list_tk_entities():
    entities = list_tk_entities()
    assert "Persoon" in entities
    assert "Fractie" in entities
    assert len(entities) > 10


def test_query_tk_entity(mock_odata_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_odata_response
        mock_get.return_value.raise_for_status = Mock()

        params = TkQueryEntityParams(
            entity="Persoon",
            filter="Achternaam eq 'de Vries'",
            select="Id,Voornamen,Achternaam",
            top=1,
        )
        result = query_tk_entity(params)

        # Verify URL construction
        args, kwargs = mock_get.call_args
        assert args[0] == "https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0/Persoon"
        assert kwargs["params"]["$filter"] == "Achternaam eq 'de Vries'"
        assert kwargs["params"]["$select"] == "Id,Voornamen,Achternaam"

        assert result["value"][0]["Voornamen"] == "Jan"


@pytest.mark.anyio
async def test_handle_tk_query(mock_odata_response):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = mock_odata_response
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_tk_query({"entity": "Persoon", "top": 1})
        assert len(result) == 1
        body = json.loads(result[0].text)
        assert body["rows"][0]["Voornamen"] == "Jan"
        assert body["rows"][0]["Achternaam"] == "de Vries"


# ---------------------------------------------------------------------------
# Phase 4: MCP Apps shape primitive binding for tk-query.
# ---------------------------------------------------------------------------


def test_tk_adapter_passes_value_array_through_as_rows():
    raw = {
        "value": [{"Id": "abc", "Voornamen": "Jan", "Achternaam": "de Vries"}],
        "@odata.count": 1,
    }
    payload = _tk_odata_to_shape_payload(raw)
    assert payload["count"] == 1
    assert payload["rows"][0]["Voornamen"] == "Jan"


def test_tk_adapter_handles_missing_value():
    assert _tk_odata_to_shape_payload({})["rows"] == []


def test_tk_query_tool_binds_to_records_shape_primitive():
    tool = next(t for t in TOOLS if t.name == "tk-query")
    assert tool.meta == {"ui": {"resourceUri": RECORDS_URI}}
    wire = tool.model_dump(by_alias=True, exclude_none=True)
    assert wire.get("_meta", {}).get("ui", {}).get("resourceUri") == RECORDS_URI
