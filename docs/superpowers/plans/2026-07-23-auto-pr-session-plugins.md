# Auto-PR Session-Created Plugins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `opendata_plugins_create` materializes a new plugin, automatically open a contribution PR of the three generated files back to the project, with user consent and clear messaging.

**Architecture:** A new self-contained module `meta_data_mcp/contribute.py` turns a set of just-written files into a PR via git plumbing (temp index → `commit-tree` → branch → push) and `gh pr create`, never touching the working tree. `handle_create_plugin` resolves consent (MCP elicitation, default-yes) then calls it, attaching a `contribution` block to its response. Every failure is non-fatal — the plugin is already live.

**Tech Stack:** Python 3.12, `mcp>=1.9.0` (elicitation), `git` + `gh` CLIs via `subprocess`, `pytest` + `pytest-asyncio`, `pyyaml`.

## Global Constraints

- Env-flag naming convention: `META_DATA_MCP_*` (matches `citations`, `provenance`).
- Enablement default **ON**: `META_DATA_MCP_AUTO_CONTRIBUTE` disables only when set to a falsy value (`0`, `false`, `no`, `off`, `""`-not-unset → still enabled). Mirror `citations.is_enabled` exactly: `os.getenv(VAR, "").strip().lower() not in _FALSY`.
- Target repo derived from `git remote get-url origin`; never hardcoded. `META_DATA_MCP_CONTRIBUTE_REPO=owner/repo` overrides.
- Branch name is always `contribute/plugin-<plugin_id>`.
- Contribution NEVER raises out of create; every failure maps to a `ContributionResult.status`. Create's `status: "ok"` is unchanged in all cases.
- Contribution commits ONLY the three passed files; the primary index and working tree stay byte-for-byte unchanged.
- No new dependencies. Use `subprocess`, `os`, `shutil.which`, `pathlib` from stdlib.
- `bun`/TypeScript rules in the global CLAUDE.md do NOT apply — this is a Python repo; use `uv`/`pytest`.
- Run subprocesses off the event loop with `anyio.to_thread.run_sync` (or `asyncio.to_thread`), matching how sync work is offloaded elsewhere.

---

### Task 1: `contribute.py` foundation — result type, env gate, target-repo resolution

**Files:**
- Create: `meta_data_mcp/contribute.py`
- Test: `tests/test_contribute.py`

**Interfaces:**
- Consumes: nothing (new module).
- Produces:
  - `class ContributionResult` — dataclass with fields `status: str`, `pr_url: str | None = None`, `branch: str | None = None`, `message: str = ""`, and `def to_dict(self) -> dict[str, Any]`.
  - `def is_enabled() -> bool`
  - `def resolve_target_repo(repo_root: Path) -> str | None` — returns `"owner/repo"` or `None` if it cannot be determined.
  - `def branch_name(plugin_id: str) -> str` — returns `f"contribute/plugin-{plugin_id}"`.
  - Module constant `_FALSY = frozenset({"0", "false", "no", "off", ""})`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_contribute.py
from pathlib import Path

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_contribute.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'meta_data_mcp.contribute'`

- [ ] **Step 3: Write the module**

```python
# meta_data_mcp/contribute.py
"""Contribute session-created plugins back to the project as a pull request.

Single responsibility: given a plugin id and the set of files that
``opendata_plugins_create`` just wrote, open a PR on the project repo. Uses git
plumbing (a temporary index so the primary working tree is never touched) plus
the ``gh`` CLI. Every failure is non-fatal and reported via ``ContributionResult``.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_ENABLE_VAR = "META_DATA_MCP_AUTO_CONTRIBUTE"
_REPO_VAR = "META_DATA_MCP_CONTRIBUTE_REPO"
_FALSY = frozenset({"0", "false", "no", "off", ""})

# github.com/<owner>/<repo>(.git) from https or ssh remotes.
_ORIGIN_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.\n]+)")


@dataclass
class ContributionResult:
    status: str  # opened | skipped_exists | declined | degraded | disabled | error
    pr_url: str | None = None
    branch: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status}
        if self.pr_url is not None:
            out["pr_url"] = self.pr_url
        if self.branch is not None:
            out["branch"] = self.branch
        if self.message:
            out["message"] = self.message
        return out


def is_enabled() -> bool:
    """True unless ``META_DATA_MCP_AUTO_CONTRIBUTE`` is set to a falsy value.

    Unset means enabled — auto-contribute is the default.
    """
    return os.getenv(_ENABLE_VAR, "").strip().lower() not in _FALSY


def branch_name(plugin_id: str) -> str:
    return f"contribute/plugin-{plugin_id}"


def resolve_target_repo(repo_root: Path) -> str | None:
    """Return ``owner/repo`` for the PR target, or None if undeterminable."""
    override = os.getenv(_REPO_VAR, "").strip()
    if override:
        return override
    try:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    m = _ORIGIN_RE.search(proc.stdout)
    if not m:
        return None
    return f"{m.group('owner')}/{m.group('repo')}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_contribute.py -v`
Expected: PASS (7 tests / parametrized cases)

- [ ] **Step 5: Commit**

```bash
git add meta_data_mcp/contribute.py tests/test_contribute.py
git commit -m "feat(contribute): result type, env gate, target-repo resolution"
```

---

### Task 2: git branch build via temporary index

**Files:**
- Modify: `meta_data_mcp/contribute.py`
- Test: `tests/test_contribute.py`

**Interfaces:**
- Consumes: `branch_name` (Task 1).
- Produces:
  - `def build_contribution_branch(plugin_id: str, files: list[Path], *, repo_root: Path, base: str = "origin/main") -> str` — creates local ref `refs/heads/contribute/plugin-<id>` = a commit whose tree is `base`'s tree plus exactly `files`, parented on `base`. Returns the branch name. Raises `ContributionGitError` (new exception subclassing `RuntimeError`) on any git failure.

This is the core mechanism. It must never touch the primary index/working tree — it seeds a scratch index file with `read-tree`, `git add`s only the passed files into it, writes a tree, and `commit-tree`s it.

- [ ] **Step 1: Write the failing integration test**

```python
# add to tests/test_contribute.py
import subprocess as _sp


def _git(cwd, *args):
    return _sp.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_contribute.py::test_build_contribution_branch_isolates_working_tree -v`
Expected: FAIL — `AttributeError: module 'meta_data_mcp.contribute' has no attribute 'build_contribution_branch'`

- [ ] **Step 3: Implement `build_contribution_branch`**

Add to `meta_data_mcp/contribute.py`:

```python
class ContributionGitError(RuntimeError):
    """A git plumbing step failed during contribution."""


def _run_git(repo_root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise ContributionGitError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def build_contribution_branch(
    plugin_id: str,
    files: list[Path],
    *,
    repo_root: Path,
    base: str = "origin/main",
) -> str:
    """Create a local branch = base tree + exactly ``files``, parented on base.

    Uses a scratch index so the primary index/working tree are never touched.
    """
    branch = branch_name(plugin_id)
    git_dir = _run_git(repo_root, "rev-parse", "--git-dir").strip()
    scratch = str((repo_root / git_dir / f"contribute-index-{plugin_id}").resolve())

    child_env = {**os.environ, "GIT_INDEX_FILE": scratch}
    try:
        # Seed the scratch index with base's full tree.
        _run_git(repo_root, "read-tree", base, env=child_env)
        # Stage ONLY the generated files (paths relative to repo_root).
        rel = [str(p.resolve().relative_to(repo_root.resolve())) for p in files]
        _run_git(repo_root, "add", "--", *rel, env=child_env)
        tree = _run_git(repo_root, "write-tree", env=child_env).strip()
        parent = _run_git(repo_root, "rev-parse", base).strip()
        msg = f"feat(plugins): add {plugin_id} (auto-contributed)"
        commit = _run_git(
            repo_root, "commit-tree", tree, "-p", parent, "-m", msg
        ).strip()
        _run_git(repo_root, "update-ref", f"refs/heads/{branch}", commit)
    finally:
        try:
            os.remove(scratch)
        except OSError:
            pass
    return branch
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_contribute.py::test_build_contribution_branch_isolates_working_tree -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meta_data_mcp/contribute.py tests/test_contribute.py
git commit -m "feat(contribute): build branch via scratch index without touching working tree"
```

---

### Task 3: PR body + title rendering

**Files:**
- Modify: `meta_data_mcp/contribute.py`
- Test: `tests/test_contribute.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `def render_pr_title(plugin_id: str) -> str`
  - `def render_pr_body(plugin_id: str, meta: dict[str, Any]) -> str` — `meta` may contain `description`, `base_url`, `homepage`, `domains`, `regions`, `keywords`, `new_tool_names`. Missing keys render gracefully. Body MUST state it was auto-generated in-session and that tests are stubs for maintainer review, and MUST include the opt-out env var.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_contribute.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_contribute.py -k render_pr -v`
Expected: FAIL — `AttributeError: ... has no attribute 'render_pr_title'`

- [ ] **Step 3: Implement the renderers**

Add to `meta_data_mcp/contribute.py`:

```python
def render_pr_title(plugin_id: str) -> str:
    return f"feat(plugins): add {plugin_id} (auto-contributed)"


def render_pr_body(plugin_id: str, meta: dict[str, Any]) -> str:
    def _fmt_list(key: str) -> str:
        vals = meta.get(key) or []
        return ", ".join(str(v) for v in vals) if vals else "—"

    lines = [
        f"## New plugin: `{plugin_id}`",
        "",
        "> Auto-generated in-session by `opendata_plugins_create` and "
        "contributed back automatically.",
        "",
        f"**Description:** {meta.get('description') or '—'}",
        f"**Base URL:** {meta.get('base_url') or '—'}",
        f"**Homepage:** {meta.get('homepage') or '—'}",
        f"**Domains:** {_fmt_list('domains')}",
        f"**Regions:** {_fmt_list('regions')}",
        f"**Keywords:** {_fmt_list('keywords')}",
        f"**Tools added:** {_fmt_list('new_tool_names')}",
        "",
        "### Files",
        f"- `tools/specs/{plugin_id}.yaml`",
        f"- `meta_data_mcp/providers/{plugin_id}.py`",
        f"- `tests/providers/test_{plugin_id}.py`",
        "",
        "### Maintainer notes",
        "- The tests are generated **stubs** — please flesh them out before merge.",
        "- The provider passed AST validation and hot-loaded live before this PR.",
        "",
        "---",
        "_Opened automatically. Set `META_DATA_MCP_AUTO_CONTRIBUTE=0` to disable._",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_contribute.py -k render_pr -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meta_data_mcp/contribute.py tests/test_contribute.py
git commit -m "feat(contribute): PR title and body renderers"
```

---

### Task 4: Orchestrator `contribute_plugin` — dedup, push, gh pr create, degraded/error

**Files:**
- Modify: `meta_data_mcp/contribute.py`
- Test: `tests/test_contribute.py`

**Interfaces:**
- Consumes: `is_enabled` (not called here — caller gates), `resolve_target_repo`, `branch_name`, `build_contribution_branch`, `render_pr_title`, `render_pr_body`, `ContributionResult`.
- Produces:
  - `async def contribute_plugin(plugin_id: str, files: list[Path], *, repo_root: Path, meta: dict[str, Any] | None = None) -> ContributionResult`

Behavior (all subprocess work offloaded via `anyio.to_thread.run_sync`):
1. If `shutil.which("gh")` is None → build local branch, return `degraded` with a runnable manual command.
2. Resolve target repo; None → `degraded` (cannot determine remote).
3. Dedup: `gh pr list --repo <target> --head contribute/plugin-<id> --state open --json url` — if non-empty → `skipped_exists` with existing url.
4. `git fetch origin main` (best-effort), `build_contribution_branch`, `git push origin <branch>`.
5. `gh pr create --repo <target> --head <branch> --title ... --body-file <tmp> --label auto-contributed` → parse the printed URL → `opened`.
6. Any `ContributionGitError` / push failure / gh failure → `degraded` (branch may exist locally) or `error`; never raise.

- [ ] **Step 1: Write the failing tests (subprocess + gh fully mocked)**

```python
# add to tests/test_contribute.py
import shutil
import types as _types


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
        contribute, "build_contribution_branch", lambda *a, **k: "contribute/plugin-acme"
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
        contribute, "build_contribution_branch", lambda *a, **k: "contribute/plugin-acme"
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


@pytest.mark.asyncio
async def test_contribute_plugin_push_failure_degrades(monkeypatch, fake_repo):
    monkeypatch.setattr(
        contribute, "build_contribution_branch", lambda *a, **k: "contribute/plugin-acme"
    )

    def boom(*a, **k):
        raise contribute.ContributionGitError("no push access")

    monkeypatch.setattr(contribute, "_gh", lambda *a, **k: "[]")
    monkeypatch.setattr(contribute, "_git_push", boom)
    res = await contribute.contribute_plugin("acme", [], repo_root=fake_repo)
    assert res.status == "degraded"
    assert "gh pr create" in res.message  # runnable manual command
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_contribute.py -k contribute_plugin -v`
Expected: FAIL — `AttributeError: ... has no attribute 'contribute_plugin'`

- [ ] **Step 3: Implement the orchestrator + helpers**

Add these imports at the top of `meta_data_mcp/contribute.py` (with the existing imports):

```python
import contextlib
import json
import shutil
import tempfile

import anyio
```

Add to `meta_data_mcp/contribute.py`:

```python
def _gh(*args: str, repo_root: Path | None = None) -> str:
    """Run a gh command, returning stdout. Raises ContributionGitError on failure."""
    proc = subprocess.run(
        ["gh", *args],
        cwd=str(repo_root) if repo_root else None,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ContributionGitError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _git_push(repo_root: Path, branch: str) -> None:
    _run_git(repo_root, "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")


def _manual_command(target: str, branch: str) -> str:
    return (
        f"gh pr create --repo {target} --head {branch} "
        f"--title '<title>' --body '<body>' --label auto-contributed"
    )


def _contribute_sync(
    plugin_id: str,
    files: list[Path],
    *,
    repo_root: Path,
    meta: dict[str, Any],
) -> ContributionResult:
    branch = branch_name(plugin_id)

    if shutil.which("gh") is None:
        try:
            build_contribution_branch(plugin_id, files, repo_root=repo_root)
        except ContributionGitError as exc:
            return ContributionResult(status="error", message=str(exc))
        return ContributionResult(
            status="degraded",
            branch=branch,
            message=(
                "gh CLI not found; branch committed locally. Finish with: "
                f"git push origin {branch} && "
                f"gh pr create --head {branch} --label auto-contributed"
            ),
        )

    target = resolve_target_repo(repo_root)
    if not target:
        return ContributionResult(
            status="degraded",
            message="Could not determine target repo from origin remote.",
        )

    # Dedup on the head branch.
    try:
        existing = _gh(
            "pr", "list", "--repo", target, "--head", branch,
            "--state", "open", "--json", "url", repo_root=repo_root,
        )
        rows = json.loads(existing or "[]")
        if rows:
            return ContributionResult(
                status="skipped_exists",
                branch=branch,
                pr_url=rows[0].get("url"),
                message="A contribution PR for this plugin is already open.",
            )
    except (ContributionGitError, json.JSONDecodeError) as exc:
        log.warning("dedup check failed, continuing: %s", exc)

    # Build + push + create.
    try:
        # Best-effort refresh of base; ignore fetch failures (offline).
        try:
            _run_git(repo_root, "fetch", "origin", "main")
        except ContributionGitError as exc:
            log.warning("git fetch failed, using local origin/main: %s", exc)
        build_contribution_branch(plugin_id, files, repo_root=repo_root)
        _git_push(repo_root, branch)
    except ContributionGitError as exc:
        return ContributionResult(
            status="degraded",
            branch=branch,
            message=f"{exc} — finish manually: {_manual_command(target, branch)}",
        )

    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(render_pr_body(plugin_id, meta))
            body_path = fh.name
        try:
            out = _gh(
                "pr", "create", "--repo", target, "--head", branch,
                "--title", render_pr_title(plugin_id),
                "--body-file", body_path,
                "--label", "auto-contributed",
                repo_root=repo_root,
            )
        finally:
            with contextlib.suppress(OSError):
                os.remove(body_path)
    except ContributionGitError as exc:
        return ContributionResult(
            status="degraded",
            branch=branch,
            message=f"{exc} — finish manually: {_manual_command(target, branch)}",
        )

    pr_url = out.strip().splitlines()[-1].strip() if out.strip() else None
    return ContributionResult(
        status="opened",
        branch=branch,
        pr_url=pr_url,
        message=(
            f"Opened contribution PR {pr_url} — thanks for growing the catalogue. "
            "Set META_DATA_MCP_AUTO_CONTRIBUTE=0 to disable."
        ),
    )


async def contribute_plugin(
    plugin_id: str,
    files: list[Path],
    *,
    repo_root: Path,
    meta: dict[str, Any] | None = None,
) -> ContributionResult:
    """Open a contribution PR for a freshly-created plugin. Never raises."""
    try:
        return await anyio.to_thread.run_sync(
            lambda: _contribute_sync(
                plugin_id, files, repo_root=repo_root, meta=meta or {}
            )
        )
    except Exception as exc:  # noqa: BLE001 — contribution is always non-fatal
        log.error("contribution failed unexpectedly: %s", exc)
        return ContributionResult(status="error", message=str(exc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_contribute.py -v`
Expected: PASS (all Task 1–4 tests)

- [ ] **Step 5: Commit**

```bash
git add meta_data_mcp/contribute.py tests/test_contribute.py
git commit -m "feat(contribute): orchestrator with dedup, push, gh pr create, degraded paths"
```

---

### Task 5: Consent resolution (MCP elicitation, default-yes)

**Files:**
- Create: `meta_data_mcp/contribute_consent.py`
- Test: `tests/test_contribute_consent.py`

**Interfaces:**
- Consumes: `contribute.is_enabled` (Task 1).
- Produces:
  - `async def resolve_consent(plugin_id: str) -> str` — returns one of `"proceed"`, `"disabled"`, `"declined"`.

Logic:
1. `if not contribute.is_enabled(): return "disabled"`.
2. Get the active session from the low-level SDK contextvar; on any failure (no context, in tests) → `"proceed"` (default-ON, cannot ask).
3. If the client does not advertise the `elicitation` capability → `"proceed"`.
4. Call `session.elicit(...)` with a one-boolean schema defaulting `true`. `action == "accept"` and the boolean truthy → `"proceed"`; otherwise → `"declined"`.

Keeping this in its own module keeps `contribute.py` free of MCP-session concerns (spec principle: deterministic core, capability-gated edges).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_contribute_consent.py
import pytest

from meta_data_mcp import contribute_consent


@pytest.mark.asyncio
async def test_disabled_when_env_off(monkeypatch):
    monkeypatch.setenv("META_DATA_MCP_AUTO_CONTRIBUTE", "0")
    assert await contribute_consent.resolve_consent("acme") == "disabled"


@pytest.mark.asyncio
async def test_proceed_when_no_request_context(monkeypatch):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)
    # No active MCP request context in a plain test → cannot ask → proceed.
    monkeypatch.setattr(contribute_consent, "_current_session", lambda: None)
    assert await contribute_consent.resolve_consent("acme") == "proceed"


@pytest.mark.asyncio
async def test_proceed_when_client_lacks_elicitation(monkeypatch):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)

    class Sess:
        async def check_client_capability(self, cap):
            return False

    monkeypatch.setattr(contribute_consent, "_current_session", lambda: Sess())
    assert await contribute_consent.resolve_consent("acme") == "proceed"


@pytest.mark.asyncio
async def test_accept_elicitation_proceeds(monkeypatch):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)

    class Result:
        action = "accept"
        content = {"contribute": True}

    class Sess:
        async def check_client_capability(self, cap):
            return True

        async def elicit(self, message, requestedSchema, related_request_id=None):
            return Result()

    monkeypatch.setattr(contribute_consent, "_current_session", lambda: Sess())
    assert await contribute_consent.resolve_consent("acme") == "proceed"


@pytest.mark.asyncio
async def test_decline_elicitation_declines(monkeypatch):
    monkeypatch.delenv("META_DATA_MCP_AUTO_CONTRIBUTE", raising=False)

    class Result:
        action = "decline"
        content = None

    class Sess:
        async def check_client_capability(self, cap):
            return True

        async def elicit(self, message, requestedSchema, related_request_id=None):
            return Result()

    monkeypatch.setattr(contribute_consent, "_current_session", lambda: Sess())
    assert await contribute_consent.resolve_consent("acme") == "declined"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_contribute_consent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'meta_data_mcp.contribute_consent'`

- [ ] **Step 3: Implement the consent resolver**

```python
# meta_data_mcp/contribute_consent.py
"""Resolve user consent for auto-contribution via MCP elicitation.

Kept separate from ``contribute.py`` so the git/PR mechanism has no dependency
on the MCP session. Default is to proceed (auto-contribute is ON); elicitation
only downgrades to 'declined' when the user actively says no.
"""

from __future__ import annotations

import logging
from typing import Any

from meta_data_mcp import contribute

log = logging.getLogger(__name__)

_ELICIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contribute": {
            "type": "boolean",
            "title": "Contribute this plugin",
            "description": "Open a public PR so others can use it.",
            "default": True,
        }
    },
    "required": ["contribute"],
}


def _current_session() -> Any | None:
    """Return the active MCP ServerSession, or None outside a request."""
    try:
        from mcp.server.lowlevel.server import request_ctx

        return request_ctx.get().session
    except (LookupError, ImportError, AttributeError):
        return None


async def resolve_consent(plugin_id: str) -> str:
    """Return 'proceed', 'disabled', or 'declined'."""
    if not contribute.is_enabled():
        return "disabled"

    session = _current_session()
    if session is None:
        return "proceed"

    # Capability-gated: only elicit if the client supports it.
    try:
        from mcp import types as mcp_types

        cap = mcp_types.ClientCapabilities(
            elicitation=mcp_types.ElicitationCapability()
        )
        if not await session.check_client_capability(cap):
            return "proceed"
    except Exception as exc:  # noqa: BLE001 — capability probing is best-effort
        log.warning("capability probe failed, proceeding: %s", exc)
        return "proceed"

    try:
        result = await session.elicit(
            message=(
                f"Contribute '{plugin_id}' back to the meta-data-mcp project "
                "so others can use it? This opens a public pull request."
            ),
            requestedSchema=_ELICIT_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 — never let elicitation break create
        log.warning("elicitation failed, proceeding: %s", exc)
        return "proceed"

    if getattr(result, "action", None) == "accept":
        content = getattr(result, "content", None) or {}
        return "proceed" if content.get("contribute", True) else "declined"
    return "declined"
```

Note: verify `ElicitationCapability` is the correct type name in the installed SDK before relying on it — run `uv run python -c "from mcp import types; print(types.ElicitationCapability)"`. If the class differs, adjust; the `except Exception → proceed` guard keeps a wrong name from breaking create, but the capability probe should work so real clients are actually asked.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_contribute_consent.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add meta_data_mcp/contribute_consent.py tests/test_contribute_consent.py
git commit -m "feat(contribute): consent resolver via MCP elicitation, default-yes"
```

---

### Task 6: Wire contribution into `handle_create_plugin` + tool description

**Files:**
- Modify: `meta_data_mcp/providers/meta_data_mcp.py:808-826` (success response block) and `:539-568` (handler imports)
- Modify: `meta_data_mcp/providers/meta_data_mcp.py:841-849` (tool description)
- Test: `tests/test_plugins_create_contribution.py`

**Interfaces:**
- Consumes: `contribute.contribute_plugin`, `contribute.ContributionResult`, `contribute_consent.resolve_consent`.
- Produces: `handle_create_plugin` success payload gains a `contribution` dict.

The success path currently returns (meta_data_mcp.py:808-826) a dict with `status/plugin_id/tools_added/new_tool_names/registry_entry/message`. Add a `contribution` key built from the consent decision and (on proceed) `contribute_plugin`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plugins_create_contribution.py
"""The contribution block attached to opendata_plugins_create responses.

We drive handle_create_plugin's *response assembly* with contribution paths
mocked, rather than re-running the full generator, by patching the two seams.
"""
import json

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plugins_create_contribution.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_run_contribution'`

- [ ] **Step 3: Add the `_run_contribution` helper and imports**

At the top of `meta_data_mcp/providers/meta_data_mcp.py` (module-level imports, after `from meta_data_mcp import federate, health` on line 32), add:

```python
from meta_data_mcp.contribute import ContributionResult, contribute_plugin
from meta_data_mcp.contribute_consent import resolve_consent
```

Add this module-level helper (near the other helpers, before `handle_create_plugin`):

```python
async def _run_contribution(plugin_id: str, *, meta: dict[str, Any]) -> dict[str, Any]:
    """Resolve consent and (on proceed) open a contribution PR. Never raises.

    Returns the dict placed under the create response's ``contribution`` key.
    """
    try:
        decision = await resolve_consent(plugin_id)
    except Exception as exc:  # noqa: BLE001 — non-fatal
        log.warning("consent resolution failed: %s", exc)
        decision = "proceed"

    if decision == "disabled":
        return {"status": "disabled"}
    if decision == "declined":
        return {
            "status": "declined",
            "message": "Not contributed (declined). The plugin is still live locally.",
        }

    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    files = [
        repo_root / "tools" / "specs" / f"{plugin_id}.yaml",
        repo_root / "meta_data_mcp" / "providers" / f"{plugin_id}.py",
        repo_root / "tests" / "providers" / f"test_{plugin_id}.py",
    ]
    result: ContributionResult = await contribute_plugin(
        plugin_id, files, repo_root=repo_root, meta=meta
    )
    return result.to_dict()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plugins_create_contribution.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Call `_run_contribution` from the success path**

In `handle_create_plugin`, replace the success `return` block (meta_data_mcp.py:808-826) so it computes the contribution block and includes it. The new block:

```python
        contribution = await _run_contribution(
            plugin_id,
            meta={
                "description": spec.get("description", ""),
                "base_url": spec.get("base_url", ""),
                "homepage": spec.get("homepage", ""),
                "domains": list(params.domains),
                "regions": list(params.regions),
                "keywords": list(params.keywords),
                "new_tool_names": new_tool_names,
            },
        )
        return [
            types.TextContent(
                type="text",
                text=serialize_for_llm(
                    {
                        "status": "ok",
                        "plugin_id": plugin_id,
                        "tools_added": added,
                        "new_tool_names": new_tool_names,
                        "registry_entry": entry.to_dict(),
                        "message": (
                            f"Plugin '{plugin_id}' is now live. "
                            f"{added} new tool(s) available: {new_tool_names}. "
                            "Call them directly to answer the user's original query."
                        ),
                        "contribution": contribution,
                    }
                ),
            )
        ]
```

- [ ] **Step 6: Update the tool description**

In the `TOOLS.append(types.Tool(...))` for `opendata_plugins_create` (meta_data_mcp.py:841-849), append one sentence to the `description` string:

```python
            "tools become available immediately. On success this also opens a "
            "public contribution PR of the generated plugin to the project so "
            "others can use it; set META_DATA_MCP_AUTO_CONTRIBUTE=0 to disable."
```

- [ ] **Step 7: Run the full create + contribution suites**

Run: `uv run pytest tests/test_plugins_create_contribution.py tests/test_contribute.py tests/test_contribute_consent.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add meta_data_mcp/providers/meta_data_mcp.py tests/test_plugins_create_contribution.py
git commit -m "feat(plugins): attach contribution PR to opendata_plugins_create response"
```

---

### Task 7: Startup log line + README docs + full regression

**Files:**
- Modify: `meta_data_mcp/server.py` (startup, near `server = Server(...)` at :173 or the run/serve entrypoint)
- Modify: `README.md`
- Test: `tests/test_contribute.py` (log helper), existing suite (regression)

**Interfaces:**
- Consumes: `contribute.is_enabled`, `contribute.resolve_target_repo`.
- Produces: `def startup_notice(repo_root: Path) -> str | None` in `contribute.py` — the one-line message to log when enabled, else None.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_contribute.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_contribute.py -k startup_notice -v`
Expected: FAIL — `AttributeError: ... has no attribute 'startup_notice'`

- [ ] **Step 3: Implement `startup_notice`**

Add to `meta_data_mcp/contribute.py`:

```python
def startup_notice(repo_root: Path) -> str | None:
    """One-line notice to log at startup when auto-contribute is active."""
    if not is_enabled():
        return None
    target = resolve_target_repo(repo_root) or "<origin>"
    return (
        f"auto-contribute is ON — created plugins will open a PR to {target} "
        "(set META_DATA_MCP_AUTO_CONTRIBUTE=0 to disable)."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_contribute.py -k startup_notice -v`
Expected: PASS

- [ ] **Step 5: Emit the notice at startup**

In `meta_data_mcp/server.py`, just after the server object is created (`server = Server(server_name, version=__version__)` at :173), add:

```python
    from pathlib import Path

    from meta_data_mcp import contribute as _contribute

    _notice = _contribute.startup_notice(Path(__file__).resolve().parents[1])
    if _notice:
        log.info(_notice)
```

(If `log` is not already bound in that scope, use the module logger already defined at the top of `server.py`.)

- [ ] **Step 6: Document in README**

Add a subsection under the plugin-creation area of `README.md`:

```markdown
### Auto-contribution of created plugins

When `opendata_plugins_create` builds a new plugin, `meta-data-mcp` opens a
pull request contributing it back to the project so others can use it — the
catalogue grows from real usage.

- **Consent:** if your MCP client supports elicitation, you'll get a yes/no
  prompt (default yes) before the PR is opened.
- **What's shared:** only the three generated files (spec, provider module,
  test stub) on a `contribute/plugin-<id>` branch. Your working tree is never
  touched.
- **Opt out:** set `META_DATA_MCP_AUTO_CONTRIBUTE=0`.
- **Target repo:** derived from your `origin` remote; override with
  `META_DATA_MCP_CONTRIBUTE_REPO=owner/repo`.
- Requires the `gh` CLI authenticated with push access. Without it, the branch
  is committed locally and the response tells you how to finish the PR.
```

- [ ] **Step 7: Full regression + lint**

Run: `uv run pytest -q`
Expected: PASS — full suite green, including pre-existing `test_repo_invariants.py`.

Run: `uv run pre-commit run --files meta_data_mcp/contribute.py meta_data_mcp/contribute_consent.py meta_data_mcp/providers/meta_data_mcp.py meta_data_mcp/server.py README.md`
Expected: ruff + format + doc-sync all pass.

- [ ] **Step 8: Commit**

```bash
git add meta_data_mcp/contribute.py meta_data_mcp/server.py README.md tests/test_contribute.py
git commit -m "feat(contribute): startup notice + README docs"
```

---

## Post-Implementation

- [ ] Push the branch and open the PR:

```bash
git push -u origin feat/auto-contribute-plugins
gh pr create --title "feat: auto-PR session-created plugins" \
  --body "Implements docs/superpowers/specs/2026-07-23-auto-pr-session-plugins-design.md"
```

- [ ] Before merge: run the code-review skill on the diff, then enumerate and resolve `gh api .../pulls/<n>/comments` (per project rules), then merge.
- [ ] Sanity note: a naive end-to-end test that actually runs `opendata_plugins_create` will, with auto-contribute ON, attempt a real PR. Existing create tests should set `META_DATA_MCP_AUTO_CONTRIBUTE=0` in their environment (or the `_run_contribution` seam should be patched) to stay hermetic — confirm during regression and add the env guard to any create test that exercises the full handler.
