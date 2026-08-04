"""Live integration test configuration.

These tests make real HTTP calls against external APIs. They are skipped by
default in CI and during normal ``pytest tests/`` runs. Run them explicitly:

    pytest tests/live/ -m live -v

Or a single provider:

    pytest tests/live/test_live_weather.py -m live -v

All tests in this directory are tagged ``@pytest.mark.live``.
"""

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if "live" in str(item.fspath):
            item.add_marker(pytest.mark.live)
