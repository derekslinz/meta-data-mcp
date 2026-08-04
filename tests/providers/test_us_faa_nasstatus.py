from unittest.mock import Mock, patch

import httpx
import pytest

from meta_data_mcp.providers.us_faa_nasstatus import (
    handle_faa_airport_status,
    handle_faa_arrival_delays,
    handle_faa_departure_delays,
    handle_faa_ground_stops,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _mock_xml_response(mock_get, xml_text: str):
    """Configure mock_get to return an httpx-like response whose .text is xml_text."""
    response = Mock()
    response.text = xml_text
    response.raise_for_status = Mock()
    mock_get.return_value = response


@pytest.mark.anyio
async def test_faa_airport_status_success():
    sample_xml = (
        "<?xml version='1.0'?><AIRPORT_STATUS_INFORMATION>"
        "<Delay_type><Ground_Stop><Airport>ORD</Airport></Ground_Stop>"
        "</Delay_type></AIRPORT_STATUS_INFORMATION>"
    )
    with patch("httpx.get") as mock_get:
        _mock_xml_response(mock_get, sample_xml)

        result = await handle_faa_airport_status()
        assert "ORD" in result[0].text
        assert "Ground_Stop" in result[0].text


@pytest.mark.anyio
async def test_faa_airport_status_http_error():
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = httpx.HTTPError("FAA endpoint down")

        with pytest.raises(httpx.HTTPError):
            await handle_faa_airport_status()


@pytest.mark.anyio
async def test_faa_ground_stops_success():
    sample_xml = "<events><event type='GS'><airport>JFK</airport></event></events>"
    with patch("httpx.get") as mock_get:
        _mock_xml_response(mock_get, sample_xml)

        result = await handle_faa_ground_stops()
        assert "JFK" in result[0].text


@pytest.mark.anyio
async def test_faa_departure_delays_success():
    sample_xml = "<events><event type='GD'><airport>LAX</airport></event></events>"
    with patch("httpx.get") as mock_get:
        _mock_xml_response(mock_get, sample_xml)

        result = await handle_faa_departure_delays()
        assert "LAX" in result[0].text


@pytest.mark.anyio
async def test_faa_arrival_delays_success():
    sample_xml = "<events><event type='ARRDLY'><airport>ATL</airport></event></events>"
    with patch("httpx.get") as mock_get:
        _mock_xml_response(mock_get, sample_xml)

        result = await handle_faa_arrival_delays()
        assert "ATL" in result[0].text


@pytest.mark.anyio
async def test_faa_airport_status_truncates_long_xml():
    # text longer than 20000 chars should be truncated
    long_xml = "<x>" + ("a" * 25000) + "</x>"
    with patch("httpx.get") as mock_get:
        _mock_xml_response(mock_get, long_xml)

        result = await handle_faa_airport_status()
        assert len(result[0].text) <= 20000
