import pytest

from meta_data_mcp import contribute


def test_is_enabled_defaults_on(monkeypatch):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)
    assert contribute.is_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off", ""])
def test_is_enabled_falsy_disables(monkeypatch, val):
    monkeypatch.setenv("META_DATA_MCP_AUTO_CONTRIBUTE", val)
    assert contribute.is_enabled() is False


def test_is_enabled_truthy_stays_on(monkeypatch):
    monkeypatch.setenv("META_DATA_MCP_AUTO_CONTRIBUTE", "1")
    assert contribute.is_enabled() is True


def test_branch_name():
    assert contribute.branch_name("acme_weather") == "contribute/plugin-acme_weather"


def test_resolve_target_repo_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("META_DATA_MCP_CONTRIBUTE_REPO", "upstream/meta-data-mcp")
    assert contribute.resolve_target_repo(tmp_path) == "upstream/meta-data-mcp"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/derekslinz/meta-data-mcp.git\n",
        "git@github.com:derekslinz/meta-data-mcp.git\n",
        "https://github.com/derekslinz/meta-data-mcp\n",
    ],
)
def test_resolve_target_repo_parses_origin(monkeypatch, tmp_path, url):
    monkeypatch.delenv("META_DATA_MCP_CONTRIBUTE_REPO", raising=False)

    def fake_run(args, **kwargs):
        import subprocess

        return subprocess.CompletedProcess(args, 0, stdout=url, stderr="")

    monkeypatch.setattr(contribute.subprocess, "run", fake_run)
    assert contribute.resolve_target_repo(tmp_path) == "derekslinz/meta-data-mcp"


def test_result_to_dict_omits_none():
    r = contribute.ContributionResult(status="declined", message="user said no")
    assert r.to_dict() == {"status": "declined", "message": "user said no"}
