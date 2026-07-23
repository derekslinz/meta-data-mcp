# Auto-PR session-created plugins — design

- **Status:** approved design, pending implementation plan
- **Date:** 2026-07-23
- **Feature branch:** `feat/auto-contribute-plugins`

## Problem

`opendata_plugins_create` lets an LLM materialize a new open-data plugin
mid-session: it writes a spec, a provider module, and a test stub into the
working tree, hot-loads the tools, and answers the user's original query. That
plugin then dies with the checkout — the next user who needs the same source
starts from scratch. A plugin someone needed once is exactly the plugin someone
else will need next; today nothing carries it back to the catalogue.

## Vision

When a plugin is created in-session, the server contributes it back to the
project as a pull request — automatically, with the user's consent, and with
messaging clear enough that nothing feels like it happened behind their back.
The catalogue grows from real usage instead of only from maintainer effort.

## Decisions (settled during brainstorming)

| Fork | Choice | Rationale |
|---|---|---|
| Trigger | **Auto on create** (side effect of `opendata_plugins_create`) | No separate step; the plugin someone just needed becomes a PR immediately. |
| Verification gate | **None** | Create already AST-validates and hot-loads; CI on the PR is the real test surface. |
| Mechanism | **git + `gh` CLI** | The 3 files are already in the working tree; git commits them natively. Zero new secrets — no `GITHUB_TOKEN` handling surface. Correct authorship. |
| Enablement default | **ON**, opt-out via `META_DATA_MCP_AUTO_CONTRIBUTE=0` | Paired with clear messaging (below); transparency does the safety work. |
| Consent | **Yes/no, default yes**, via MCP elicitation | Native consent dialog when the client supports it; falls back to auto-proceed when it doesn't. |

## Out of scope

- A verification/test gate before PR (explicitly declined).
- A hosted/token PR path. `handle_create_plugin` only runs from a source
  checkout (it needs `tools/generate_provider.py` and writes into the package
  tree); it errors on a `uvx` install, so this code can never run headless.
- Auto-merge or CI orchestration. The PR is opened; a human merges it, per the
  project's review-before-merge rule.
- Editing/updating existing plugins via PR. New-plugin contribution only.

## Principles

- **Non-fatal, always.** The plugin is already live before contribution runs.
  No contribution failure — missing `gh`, no push access, offline, a declined
  consent — may change create's `status: ok`.
- **Never disturb the working tree.** The user may have unrelated uncommitted
  changes. Contribution commits *only* the three generated files and touches
  neither the primary index nor the working tree.
- **Deterministic core, capability-gated edges.** Git plumbing is deterministic;
  elicitation is used only when the client advertises the capability.
- **Transparency over surprise.** Default-ON is acceptable only because every
  path says plainly what happened and how to turn it off.

## Constraints

- `mcp >= 1.9.0` — elicitation is available (`ServerSession.elicit`,
  `types.ElicitResult`, `ClientCapabilities.elicitation`).
- Kernel handlers are sync `httpx`; git/`gh` are subprocesses. Run them without
  blocking the event loop (thread offload, matching how handlers already behave).
- `gh` and `git` are expected in a dev checkout but must be treated as optional.

## Architecture

### Module boundary

One new module: `meta_data_mcp/contribute.py`. Single responsibility — turn a
set of just-written files into a pull request. It knows nothing about plugin
generation; it takes paths and a plugin id.

```python
@dataclass
class ContributionResult:
    status: Literal[
        "opened", "skipped_exists", "declined",
        "degraded", "disabled", "error",
    ]
    pr_url: str | None = None
    branch: str | None = None
    message: str = ""

async def contribute_plugin(
    plugin_id: str,
    files: list[Path],
    *,
    repo_root: Path,
) -> ContributionResult: ...
```

Consent is resolved by the caller (see below); `contribute_plugin` is invoked
*only* on a proceed decision, so it carries no MCP-session or consent concerns.
The caller builds the `disabled` / `declined` results itself. `handle_create_plugin`
calls it after hot-load and the `plugin.created` event, **awaited but wrapped**
so any exception becomes `status="error"`. The result rides back in the create
response under a new `contribution` key.

### Consent (yes/no, default yes)

Resolved in `handle_create_plugin` *before* calling `contribute_plugin`, so the
contribute module stays free of MCP session concerns:

1. If `META_DATA_MCP_AUTO_CONTRIBUTE=0` → `disabled`, skip entirely (no elicitation,
   no PR). Reported in the response.
2. Otherwise read the active request context's session via the low-level SDK
   contextvar (`mcp.server.lowlevel.server.request_ctx` → `.session`) and check
   the negotiated client capabilities.
3. If the client advertises `elicitation`: call `session.elicit()` with the
   message *"Contribute '<id>' back to the meta-data-mcp project so others can
   use it? This opens a public pull request."* and a schema of one boolean
   **defaulting `true`** (the yes is preselected).
   - `accept` + true → proceed.
   - `decline` / `cancel` / false → `declined`; plugin stays live, no PR.
4. If the client does **not** advertise `elicitation`: proceed (default-ON). We
   cannot ask; the response messaging carries the disclosure instead.

### The git mechanism

The three files are new/untracked in the working tree, likely beside unrelated
changes. Commit only them, on a fresh branch, without touching the working tree
or index — temp-index plumbing (no branch switch, no worktree, no file copy):

```sh
tmp="$(git rev-parse --git-dir)/contribute-index-<id>"
GIT_INDEX_FILE="$tmp" git read-tree origin/main            # seed with main's tree
GIT_INDEX_FILE="$tmp" git add -- <spec> <provider> <test>  # stage only the 3 files
tree="$(GIT_INDEX_FILE="$tmp" git write-tree)"
commit="$(git commit-tree "$tree" -p origin/main -m "<msg>")"
git update-ref refs/heads/contribute/plugin-<id> "$commit"
git push origin contribute/plugin-<id>
gh pr create --head contribute/plugin-<id> --repo <target> \
  --title "<title>" --body-file <body> --label auto-contributed
rm -f "$tmp"
```

Result: a branch on top of `origin/main` containing exactly the three files;
the primary working tree and index untouched. `origin/main` is fetched first if
stale.

### Target repo & dedup

- **Target:** `git remote get-url origin`, parsed to `owner/repo`. Never
  hardcoded. Optional `META_DATA_MCP_CONTRIBUTE_REPO=owner/repo` overrides, so a fork
  checkout can target upstream.
- **Dedup:** branch `contribute/plugin-<id>`. If the branch already exists on
  origin, or `gh pr list --head contribute/plugin-<id>` returns an open PR →
  `skipped_exists` with the existing PR url. Repeated creates are idempotent.

### Degraded path

If `gh` is absent, or the push fails for lack of access, or the host is offline:
commit the local branch (steps through `update-ref`) and return `degraded` with
the branch name and the exact `gh pr create …` command to finish by hand. Never
raise.

## Clear messaging (load-bearing, since default is ON)

1. **Tool description** of `opendata_plugins_create` states up front: *"On
   success this also opens a public contribution PR of the generated plugin to
   the project. Set `META_DATA_MCP_AUTO_CONTRIBUTE=0` to disable."*
2. **Create response** carries `contribution: {status, pr_url?, branch?,
   message}`. On success the message reads plainly, e.g. *"Opened contribution
   PR <url> — thanks for growing the catalogue. Set
   `META_DATA_MCP_AUTO_CONTRIBUTE=0` to disable."*
3. **Startup log** emits one INFO line when auto-contribute is active:
   *"auto-contribute is ON — created plugins will open a PR to <target> (set
   META_DATA_MCP_AUTO_CONTRIBUTE=0 to disable)."*
4. **PR body** identifies it as auto-contributed from an in-session creation,
   includes the plugin description / base_url / homepage / domains / regions /
   keywords, and flags that the tests are generated stubs for maintainer review.
   Labeled `auto-contributed`.
5. **README** gains a short section documenting the behavior and the opt-out.

## Response shape

`opendata_plugins_create` success payload gains one key; everything else is
unchanged:

```jsonc
{
  "status": "ok",
  "plugin_id": "acme_weather",
  "tools_added": 2,
  "new_tool_names": ["acme-weather-forecast", "acme-weather-current"],
  "registry_entry": { /* ... */ },
  "message": "Plugin 'acme_weather' is now live. ...",
  "contribution": {
    "status": "opened",
    "pr_url": "https://github.com/derekslinz/meta-data-mcp/pull/NNN",
    "branch": "contribute/plugin-acme_weather",
    "message": "Opened contribution PR … — set META_DATA_MCP_AUTO_CONTRIBUTE=0 to disable."
  }
}
```

## Error handling

Every failure mode maps to a `ContributionResult.status`, never an exception out
of create:

| Situation | status | Response effect |
|---|---|---|
| `META_DATA_MCP_AUTO_CONTRIBUTE=0` | `disabled` | create ok; contribution notes disabled |
| User declines elicitation | `declined` | create ok; plugin live, no PR |
| Branch/PR already exists | `skipped_exists` | create ok; existing pr_url returned |
| `gh` missing / no push / offline | `degraded` | create ok; branch + manual command returned |
| Any unexpected exception | `error` | create ok; message describes the failure |

## Test strategy

- **Unit (`contribute.py`, subprocess mocked):** disabled-by-env → `disabled`;
  existing branch/PR → `skipped_exists`; `gh` missing → `degraded`; happy path
  asserts the exact git/`gh` command sequence and branch name.
- **Consent unit:** capability present + accept → proceeds; capability present +
  decline → `declined`; capability absent → proceeds (default-ON). Elicit schema
  boolean defaults `true`.
- **Integration (temp git repo fixture, local bare "remote", `gh` mocked):**
  run the temp-index flow; assert the branch's tree equals `main`'s tree **plus
  exactly** the three files, and that the primary working tree/index are
  unchanged (a pre-existing dirty file survives untouched).
- **Regression:** existing `opendata_plugins_create` tests pass unchanged with
  contribution disabled; with it enabled + `gh` mocked, they still return
  `status: ok` and gain a well-formed `contribution` block.

## Verification (done = all true)

- Creating a plugin with auto-contribute ON, on a checkout with push access,
  opens a PR whose diff is exactly the three generated files on top of `main`.
- The primary working tree and index are byte-for-byte unchanged afterward.
- A second create of the same id returns `skipped_exists` with the first PR url.
- With `META_DATA_MCP_AUTO_CONTRIBUTE=0`, no branch, no PR, no elicitation.
- A client that supports elicitation shows a yes/no dialog defaulting to yes; a
  client that does not still contributes and says so in the response.
- `gh` removed from PATH → create still returns `ok` with a `degraded`
  contribution carrying a runnable manual command.
