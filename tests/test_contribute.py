import subprocess as _sp

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


def _git(cwd, *args):
    return _sp.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


def _make_repo(tmp_path):
    """A repo whose 'origin' is a local bare remote, with a committed main."""
    remote = tmp_path / "remote.git"
    _sp.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Test")
    _git(work, "checkout", "-b", "main")
    (work / "README.md").write_text("# base\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "base")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")
    return work


def test_build_contribution_branch_isolates_working_tree(tmp_path):
    work = _make_repo(tmp_path)
    # A dirty, unrelated change in the working tree that MUST survive untouched.
    (work / "README.md").write_text("# base\nDIRTY LOCAL EDIT\n")
    # The three "generated" files.
    spec = work / "tools" / "specs" / "acme.yaml"
    prov = work / "meta_data_mcp" / "providers" / "acme.py"
    test = work / "tests" / "providers" / "test_acme.py"
    for p, body in [(spec, "id: acme\n"), (prov, "# provider\n"), (test, "# test\n")]:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)

    branch = contribute.build_contribution_branch(
        "acme", [spec, prov, test], repo_root=work, base="origin/main"
    )
    assert branch == "contribute/plugin-acme"

    # The branch tree = origin/main's README plus exactly the 3 new files.
    listing = _git(work, "ls-tree", "-r", "--name-only", branch).stdout.split()
    assert set(listing) == {
        "README.md",
        "tools/specs/acme.yaml",
        "meta_data_mcp/providers/acme.py",
        "tests/providers/test_acme.py",
    }
    # README in the branch is base content, NOT the dirty local edit.
    blob = _git(work, "show", f"{branch}:README.md").stdout
    assert blob == "# base\n"
    # The primary working tree still has the dirty edit and no branch switch.
    assert "DIRTY LOCAL EDIT" in (work / "README.md").read_text()
    assert _git(work, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
