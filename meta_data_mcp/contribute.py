"""Contribute session-created plugins back to the project as a pull request.

Single responsibility: given a plugin id and the set of files that
``opendata_plugins_create`` just wrote, open a PR on the project repo. Uses git
plumbing (a temporary index so the primary working tree is never touched) plus
the ``gh`` CLI. Every failure is non-fatal and reported via ``ContributionResult``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio

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
    val = os.getenv(_ENABLE_VAR)
    if val is None:
        return True  # unset means enabled
    return val.strip().lower() not in _FALSY


def branch_name(plugin_id: str) -> str:
    return f"contribute/plugin-{plugin_id}"


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


def startup_notice(repo_root: Path) -> str | None:
    """One-line notice to log at startup when auto-contribute is active."""
    if not is_enabled():
        return None
    target = resolve_target_repo(repo_root) or "<origin>"
    return (
        f"auto-contribute is ON — created plugins will open a PR to {target} "
        "(set META_DATA_MCP_AUTO_CONTRIBUTE=0 to disable)."
    )


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
            branch=branch,
            message="Could not determine target repo from origin remote.",
        )

    # Dedup on the head branch.
    try:
        existing = _gh(
            "pr",
            "list",
            "--repo",
            target,
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url",
            repo_root=repo_root,
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

    def _open_pr() -> ContributionResult:
        """Create the PR (no label), best-effort label it, and report the url.

        Shared by the existing-branch (self-heal) and fresh-branch paths so the
        create/label/parse logic lives in exactly one place. Labeling failures
        are swallowed — a missing ``auto-contributed`` label must never block a
        PR that is already open.
        """
        try:
            with tempfile.NamedTemporaryFile(
                "w", suffix=".md", delete=False, encoding="utf-8"
            ) as fh:
                fh.write(render_pr_body(plugin_id, meta))
                body_path = fh.name
            try:
                out = _gh(
                    "pr",
                    "create",
                    "--repo",
                    target,
                    "--head",
                    branch,
                    "--title",
                    render_pr_title(plugin_id),
                    "--body-file",
                    body_path,
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

        # Best-effort labeling AFTER the PR exists. Any failure (label absent on
        # the repo, gh error, etc.) is logged and ignored — the PR still opened.
        if pr_url:
            try:
                _gh(
                    "pr",
                    "edit",
                    pr_url,
                    "--repo",
                    target,
                    "--add-label",
                    "auto-contributed",
                    repo_root=repo_root,
                )
            except ContributionGitError as exc:
                log.warning("labeling PR failed, ignoring: %s", exc)

        return ContributionResult(
            status="opened",
            branch=branch,
            pr_url=pr_url,
            message=(
                f"Opened contribution PR {pr_url} — thanks for growing the catalogue. "
                "Set META_DATA_MCP_AUTO_CONTRIBUTE=0 to disable."
            ),
        )

    # Does the branch already exist on origin? (Step 3 already ruled out an open
    # PR.) If so, self-heal: open a PR from the existing branch WITHOUT rebuilding
    # or re-pushing — a non-force push would be rejected non-fast-forward, which
    # is exactly how a pushed-but-no-PR branch gets stranded.
    remote_branch_exists = False
    try:
        ls = _run_git(repo_root, "ls-remote", "--heads", "origin", branch)
        remote_branch_exists = bool(ls.strip())
    except ContributionGitError as exc:
        log.warning("ls-remote check failed, assuming branch absent: %s", exc)

    if remote_branch_exists:
        return _open_pr()

    # Fresh path: build a local branch, push it, then open the PR.
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

    return _open_pr()


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
