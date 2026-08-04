from unittest.mock import Mock, patch

import pytest

from meta_data_mcp.providers.nl_ndov import (
    handle_ndov_list_path,
    list_ndov_path,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_html_root():
    return """
    <html>
    <body>
    <h1>Index of /</h1>
    <ul>
    <li><a href="haltes/">haltes/</a></li>
    <li><a href="ns/">ns/</a></li>
    <li><a href="LICENTIE-CC0.TXT">LICENTIE-CC0.TXT</a></li>
    <li><a href="?order=N">Name</a></li>
    </ul>
    </body>
    </html>
    """


@pytest.fixture
def mock_html_haltes():
    return """
    <html>
    <body>
    <h1>Index of /haltes/</h1>
    <ul>
    <li><a href="../">Parent Directory</a></li>
    <li><a href="ExportCHB_2026-03-21.xml.gz">ExportCHB_2026-03-21.xml.gz</a></li>
    </ul>
    </body>
    </html>
    """


def test_list_ndov_root(mock_html_root):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = mock_html_root
        mock_get.return_value.raise_for_status = Mock()

        entries = list_ndov_path("/")
        assert len(entries) == 3
        assert entries[0]["name"] == "haltes"
        assert entries[0]["type"] == "directory"
        assert entries[0]["url"] == "https://data.ndovloket.nl/haltes/"
        assert entries[2]["name"] == "LICENTIE-CC0.TXT"
        assert entries[2]["type"] == "file"


def test_list_ndov_subpath(mock_html_haltes):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = mock_html_haltes
        mock_get.return_value.raise_for_status = Mock()

        entries = list_ndov_path("/haltes/")
        assert len(entries) == 1
        assert entries[0]["name"] == "ExportCHB_2026-03-21.xml.gz"
        assert entries[0]["type"] == "file"
        assert (
            entries[0]["url"]
            == "https://data.ndovloket.nl/haltes/ExportCHB_2026-03-21.xml.gz"
        )


@pytest.mark.anyio
async def test_handle_ndov_list_path(mock_html_root):
    with patch("httpx.get") as mock_get:
        mock_get.return_value.text = mock_html_root
        mock_get.return_value.raise_for_status = Mock()

        result = await handle_ndov_list_path({"path": "/"})
        assert len(result) == 1
        assert "haltes" in result[0].text
        assert "directory" in result[0].text
