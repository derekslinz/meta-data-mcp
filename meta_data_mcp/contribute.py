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
    val = os.getenv(_ENABLE_VAR)
    if val is None:
        return True  # unset means enabled
    return val.strip().lower() not in _FALSY


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
