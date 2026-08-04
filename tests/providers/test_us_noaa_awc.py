from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.us_noaa_awc import (
    handle_awc_metar,
    handle_awc_station,
    handle_awc_taf,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_awc_metar_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"icaoId": "KJFK", "rawOb": "KJFK 111851Z ..."},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_awc_metar({"ids": "KJFK"})
        assert len(result) == 1
        assert "KJFK" in result[0].text


@pytest.mark.anyio
async def test_awc_taf_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"icaoId": "KJFK", "rawTaf": "KJFK 111720Z ..."},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_awc_taf({"ids": "KJFK"})
        assert len(result) == 1
        assert "KJFK" in result[0].text


@pytest.mark.anyio
async def test_awc_station_success():
    with patch("httpx.get") as mock_get:
        mock_get.return_value.json.return_value = [
            {"icaoId": "KJFK", "name": "NEW YORK/JOHN F KENNEDY INTL"},
        ]
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_awc_station({"ids": "KJFK"})
        assert len(result) == 1
        assert "KENNEDY" in result[0].text
