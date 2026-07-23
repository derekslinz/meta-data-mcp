"""The contribution block attached to opendata_plugins_create responses.

We drive handle_create_plugin's *response assembly* with contribution paths
mocked, rather than re-running the full generator, by patching the two seams.
"""

import pytest

from meta_data_mcp.providers import meta_data_mcp as mod


@pytest.mark.asyncio
async def test_disabled_consent_marks_contribution_disabled(monkeypatch):
    async def fake_consent(_pid):
        return "disabled"

    monkeypatch.setattr(mod, "resolve_consent", fake_consent)
    block = await mod._run_contribution("acme", meta={})
    assert block == {"status": "disabled"}


@pytest.mark.asyncio
async def test_declined_consent_skips_pr(monkeypatch):
    async def fake_consent(_pid):
        return "declined"

    called = {"pr": False}

    async def fake_contribute(*a, **k):
        called["pr"] = True

    monkeypatch.setattr(mod, "resolve_consent", fake_consent)
    monkeypatch.setattr(mod, "contribute_plugin", fake_contribute)
    block = await mod._run_contribution("acme", meta={})
    assert block["status"] == "declined"
    assert called["pr"] is False


@pytest.mark.asyncio
async def test_proceed_consent_opens_pr(monkeypatch):
    from meta_data_mcp.contribute import ContributionResult

    async def fake_consent(_pid):
        return "proceed"

    async def fake_contribute(plugin_id, files, *, repo_root, meta=None):
        return ContributionResult(
            status="opened", pr_url="https://x/pull/1", branch="contribute/plugin-acme"
        )

    monkeypatch.setattr(mod, "resolve_consent", fake_consent)
    monkeypatch.setattr(mod, "contribute_plugin", fake_contribute)
    block = await mod._run_contribution("acme", meta={"description": "d"})
    assert block["status"] == "opened"
    assert block["pr_url"] == "https://x/pull/1"
