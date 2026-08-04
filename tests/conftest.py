"""Root pytest configuration.

Applies safety defaults to every test in the suite.
"""

import pytest


@pytest.fixture(autouse=True)
def _disable_auto_contribute(monkeypatch):
    """Default auto-contribute OFF in tests so no test can push to a real remote.

    A test that specifically needs it ON (or needs the default-ON behavior
    with the var unset) can override after this fixture runs, e.g. with its
    own monkeypatch.setenv("META_DATA_MCP_AUTO_CONTRIBUTE", "1") or
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False).
    """
    monkeypatch.setenv("META_DATA_MCP_AUTO_CONTRIBUTE", "0")
