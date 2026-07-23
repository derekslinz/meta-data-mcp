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


def test_render_pr_title():
    assert contribute.render_pr_title("acme_weather") == (
        "feat(plugins): add acme_weather (auto-contributed)"
    )


def test_render_pr_body_contains_key_facts():
    body = contribute.render_pr_body(
        "acme_weather",
        {
            "description": "ACME weather API",
            "base_url": "https://api.acme.test",
            "homepage": "https://acme.test",
            "domains": ["weather"],
            "regions": ["global"],
            "keywords": ["forecast"],
            "new_tool_names": ["acme-weather-forecast"],
        },
    )
    assert "acme_weather" in body
    assert "ACME weather API" in body
    assert "https://api.acme.test" in body
    assert "acme-weather-forecast" in body
    # Provenance + reviewer guidance + opt-out are mandatory.
    assert "auto-generated" in body.lower()
    assert "stub" in body.lower()
    assert "META_DATA_MCP_AUTO_CONTRIBUTE=0" in body


def test_render_pr_body_tolerates_missing_meta():
    body = contribute.render_pr_body("acme", {})
    assert "acme" in body
    assert "META_DATA_MCP_AUTO_CONTRIBUTE=0" in body


@pytest.fixture
def fake_repo(monkeypatch, tmp_path):
    """Force resolve_target_repo + gh presence deterministic."""
    monkeypatch.setenv("META_DATA_MCP_CONTRIBUTE_REPO", "derekslinz/meta-data-mcp")
    monkeypatch.setattr(contribute.shutil, "which", lambda _: "/usr/bin/gh")
    return tmp_path


@pytest.mark.asyncio
async def test_contribute_plugin_gh_missing_degrades(monkeypatch, tmp_path):
    monkeypatch.setattr(contribute.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        contribute,
        "build_contribution_branch",
        lambda *a, **k: "contribute/plugin-acme",
    )
    res = await contribute.contribute_plugin("acme", [], repo_root=tmp_path)
    assert res.status == "degraded"
    assert "gh" in res.message
    assert res.branch == "contribute/plugin-acme"


@pytest.mark.asyncio
async def test_contribute_plugin_skips_when_pr_exists(monkeypatch, fake_repo):
    calls = {}

    def fake_gh(args):
        if args[:2] == ["pr", "list"]:
            return '[{"url": "https://github.com/x/y/pull/7"}]'
        calls["created"] = True
        return ""

    monkeypatch.setattr(contribute, "_gh", lambda *a, **k: fake_gh(list(a)))
    res = await contribute.contribute_plugin("acme", [], repo_root=fake_repo)
    assert res.status == "skipped_exists"
    assert res.pr_url == "https://github.com/x/y/pull/7"
    assert "created" not in calls


@pytest.mark.asyncio
async def test_contribute_plugin_happy_path_opens_pr(monkeypatch, fake_repo):
    monkeypatch.setattr(
        contribute,
        "build_contribution_branch",
        lambda *a, **k: "contribute/plugin-acme",
    )
    monkeypatch.setattr(contribute, "_git_push", lambda *a, **k: None)

    def fake_gh(*args, **kwargs):
        if args[0] == "pr" and args[1] == "list":
            return "[]"
        if args[0] == "pr" and args[1] == "create":
            return "https://github.com/derekslinz/meta-data-mcp/pull/42\n"
        return ""

    monkeypatch.setattr(contribute, "_gh", fake_gh)
    res = await contribute.contribute_plugin(
        "acme", [], repo_root=fake_repo, meta={"description": "d"}
    )
    assert res.status == "opened"
    assert res.pr_url == "https://github.com/derekslinz/meta-data-mcp/pull/42"
    assert res.branch == "contribute/plugin-acme"


def test_startup_notice_when_enabled(monkeypatch, tmp_path):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)
    monkeypatch.setenv("META_DATA_MCP_CONTRIBUTE_REPO", "derekslinz/meta-data-mcp")
    msg = contribute.startup_notice(tmp_path)
    assert msg is not None
    assert "derekslinz/meta-data-mcp" in msg
    assert "META_DATA_MCP_AUTO_CONTRIBUTE=0" in msg


def test_startup_notice_none_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("META_DATA_MCP_AUTO_CONTRIBUTE", "0")
    assert contribute.startup_notice(tmp_path) is None


@pytest.mark.asyncio
async def test_contribute_plugin_existing_remote_branch_self_heals(
    monkeypatch, fake_repo
):
    """Branch exists on origin but no open PR: open a PR from it, never re-push."""
    called = {"build": False, "push": False, "create": False}

    def fake_build(*a, **k):
        called["build"] = True
        return "contribute/plugin-acme"

    def fake_push(*a, **k):
        called["push"] = True

    def fake_run_git(repo_root, *args, **kwargs):
        if args[:1] == ("ls-remote",):
            # Non-empty stdout => branch exists remotely.
            return "deadbeef\trefs/heads/contribute/plugin-acme\n"
        raise contribute.ContributionGitError(f"unexpected git {args}")

    def fake_gh(*args, **kwargs):
        if args[0] == "pr" and args[1] == "list":
            return "[]"
        if args[0] == "pr" and args[1] == "create":
            called["create"] = True
            return "https://github.com/derekslinz/meta-data-mcp/pull/55\n"
        return ""  # pr edit (best-effort label)

    monkeypatch.setattr(contribute, "build_contribution_branch", fake_build)
    monkeypatch.setattr(contribute, "_git_push", fake_push)
    monkeypatch.setattr(contribute, "_run_git", fake_run_git)
    monkeypatch.setattr(contribute, "_gh", fake_gh)

    res = await contribute.contribute_plugin(
        "acme", [], repo_root=fake_repo, meta={"description": "d"}
    )
    assert res.status == "opened"
    assert res.pr_url == "https://github.com/derekslinz/meta-data-mcp/pull/55"
    assert res.branch == "contribute/plugin-acme"
    assert called["create"] is True
    # The self-heal path must NOT rebuild or re-push the existing branch.
    assert called["build"] is False
    assert called["push"] is False


@pytest.mark.asyncio
async def test_contribute_plugin_label_failure_still_opens(monkeypatch, fake_repo):
    """gh pr create succeeds but the best-effort label edit fails: still opened."""
    monkeypatch.setattr(
        contribute,
        "build_contribution_branch",
        lambda *a, **k: "contribute/plugin-acme",
    )
    monkeypatch.setattr(contribute, "_git_push", lambda *a, **k: None)

    def fake_gh(*args, **kwargs):
        if args[0] == "pr" and args[1] == "list":
            return "[]"
        if args[0] == "pr" and args[1] == "create":
            return "https://github.com/derekslinz/meta-data-mcp/pull/77\n"
        if args[0] == "pr" and args[1] == "edit":
            raise contribute.ContributionGitError(
                "could not add label: 'auto-contributed' not found"
            )
        return ""

    monkeypatch.setattr(contribute, "_gh", fake_gh)
    res = await contribute.contribute_plugin(
        "acme", [], repo_root=fake_repo, meta={"description": "d"}
    )
    assert res.status == "opened"
    assert res.pr_url == "https://github.com/derekslinz/meta-data-mcp/pull/77"
    assert res.branch == "contribute/plugin-acme"


@pytest.mark.asyncio
async def test_contribute_plugin_push_failure_degrades(monkeypatch, fake_repo):
    monkeypatch.setattr(
        contribute,
        "build_contribution_branch",
        lambda *a, **k: "contribute/plugin-acme",
    )

    def boom(*a, **k):
        raise contribute.ContributionGitError("no push access")

    monkeypatch.setattr(contribute, "_gh", lambda *a, **k: "[]")
    monkeypatch.setattr(contribute, "_git_push", boom)
    res = await contribute.contribute_plugin("acme", [], repo_root=fake_repo)
    assert res.status == "degraded"
    assert "gh pr create" in res.message  # runnable manual command


def test_run_git_timeout_maps_to_contribution_error(monkeypatch, tmp_path):
    # A hanging git call (e.g. a credential prompt) must surface as a
    # ContributionGitError so the orchestrator degrades instead of hanging.
    def timeout(*a, **k):
        raise _sp.TimeoutExpired(cmd="git", timeout=60)

    monkeypatch.setattr(contribute.subprocess, "run", timeout)
    with pytest.raises(contribute.ContributionGitError):
        contribute._run_git(tmp_path, "status")


def test_run_git_closes_stdin_and_disables_prompt(monkeypatch, tmp_path):
    captured = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return _sp.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(contribute.subprocess, "run", fake_run)
    contribute._run_git(tmp_path, "status")
    assert captured["stdin"] is _sp.DEVNULL
    assert captured["timeout"] == contribute._SUBPROCESS_TIMEOUT
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_gh_timeout_maps_to_contribution_error(monkeypatch):
    def timeout(*a, **k):
        raise _sp.TimeoutExpired(cmd="gh", timeout=60)

    monkeypatch.setattr(contribute.subprocess, "run", timeout)
    with pytest.raises(contribute.ContributionGitError):
        contribute._gh("pr", "list")
