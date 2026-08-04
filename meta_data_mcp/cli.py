"""CLI entry point for meta-data-mcp.

There is ONE MCP server: ``meta-data-mcp``. The ~60 modules under
``meta_data_mcp/providers/`` are internal *plugins* of this server, not
separate servers. The CLI therefore does not take a "provider" argument —
all commands operate on the one server.

Commands:

    run       — start the server
    setup     — register the server in every installed MCP client (auto-detect)
    remove    — unregister the server from every detected MCP client
    clients   — list supported MCP clients and their installed status
    cleanup   — remove legacy individual-provider entries (pre-unified setups)
    inspect   — launch MCP Inspector against the server
    list      — informational: list internal plugins
    info      — informational: show server / plugin description
    version   — show the package version
"""

from __future__ import annotations

import json
import os
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Add src to sys.path so this works when run from a source checkout.
_src_path = str(Path(__file__).parent.parent)
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

import anyio  # noqa: E402
import click  # noqa: E402

from meta_data_mcp import __version__  # noqa: E402

# The package name on PyPI and the CLI entry-point are identical.
LIB_NAME = "meta-data-mcp"

# The single Claude Desktop ``mcpServers`` key for the unified server.
SERVER_KEY = "meta-data-mcp"

# Legacy keys produced by earlier multi-server CLI versions. The cleanup
# logic removes/migrates these whenever setup or cleanup runs.
_LEGACY_PREFIX = "opendata-mcp-"
_LEGACY_DOUBLE_META = "opendata-mcp-meta-data-mcp"
_LEGACY_DOUBLE_ALL = "opendata-mcp-opendata-mcp-all"


# ---------------------------------------------------------------------------
# Supported MCP clients
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientSpec:
    """A supported MCP client and where its config lives.

    All Phase-1 clients share the same JSON ``mcpServers`` schema:

        {"mcpServers": {<server-key>: {"command": str, "args": [str, ...],
                                       "env"?: {str: str}}}}

    ``detect_fn`` returns the path that must EXIST for the client to count
    as installed. ``config_path_fn`` returns the file to write (may equal
    detect_fn). Both may return None on an unsupported platform.
    """

    key: str
    label: str
    detect_fn: Callable[[], Path | None]
    config_path_fn: Callable[[], Path | None]


def _claude_desktop_config_path() -> Path | None:
    system = platform.system()
    if system == "Darwin":
        return (
            Path.home()
            / "Library/Application Support/Claude/claude_desktop_config.json"
        )
    if system == "Windows":
        return Path(os.getenv("APPDATA") or "") / "Claude/claude_desktop_config.json"
    return None


def _claude_desktop_detect() -> Path | None:
    p = _claude_desktop_config_path()
    return p.parent if p is not None else None


def _claude_code_path() -> Path:
    return Path.home() / ".claude.json"


def _cursor_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _windsurf_path() -> Path:
    return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"


def _gemini_path() -> Path:
    return Path.home() / ".gemini" / "settings.json"


def _lm_studio_path() -> Path:
    return Path.home() / ".cache" / "lm-studio" / "mcp.json"


CLIENTS: dict[str, ClientSpec] = {
    "claude-desktop": ClientSpec(
        key="claude-desktop",
        label="Claude Desktop",
        detect_fn=_claude_desktop_detect,
        config_path_fn=_claude_desktop_config_path,
    ),
    "claude-code": ClientSpec(
        key="claude-code",
        label="Claude Code",
        detect_fn=_claude_code_path,
        config_path_fn=_claude_code_path,
    ),
    "cursor": ClientSpec(
        key="cursor",
        label="Cursor",
        detect_fn=lambda: _cursor_path().parent,
        config_path_fn=_cursor_path,
    ),
    "windsurf": ClientSpec(
        key="windsurf",
        label="Windsurf",
        detect_fn=lambda: _windsurf_path().parent,
        config_path_fn=_windsurf_path,
    ),
    "gemini": ClientSpec(
        key="gemini",
        label="Gemini CLI",
        detect_fn=_gemini_path,
        config_path_fn=_gemini_path,
    ),
    "lm-studio": ClientSpec(
        key="lm-studio",
        label="LM Studio",
        detect_fn=lambda: _lm_studio_path().parent,
        config_path_fn=_lm_studio_path,
    ),
}


def _detect_installed_clients() -> list[ClientSpec]:
    """Return the subset of CLIENTS whose detection path exists on disk."""
    out: list[ClientSpec] = []
    for spec in CLIENTS.values():
        detect = spec.detect_fn()
        if detect is not None and detect.exists():
            out.append(spec)
    return out


def _config_path() -> Path:
    """Back-compat alias for the Claude Desktop config path.

    Retained because the ``cleanup`` command still operates Claude-Desktop-only
    (the legacy ``opendata-mcp-*`` entries were only ever written there).
    """
    p = _claude_desktop_config_path()
    if p is not None:
        return p
    # Fall back to the Windows-style path; preserves prior behaviour on
    # unsupported OSes (callers handle non-existence).
    return Path(os.getenv("APPDATA") or "") / "Claude/claude_desktop_config.json"


def _backup_config(config_path: Path) -> None:
    if config_path.exists():
        config_path.with_suffix(".json.bak").write_text(config_path.read_text())


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text())
    except json.JSONDecodeError:
        click.echo(
            f"Error: {config_path} contains invalid JSON. Please fix it manually.",
            err=True,
        )
        sys.exit(1)


def _load_config_safe(config_path: Path) -> dict | None:
    """Variant of ``_load_config`` that returns ``None`` on invalid JSON.

    Used by the multi-client setup loop so a single corrupted config does
    not abort writes to the other detected clients.
    """
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text())
    except json.JSONDecodeError:
        return None


def _server_entry(use_local: bool, repo_root: Path) -> dict:
    """Build the Claude Desktop ``mcpServers`` entry for the one server."""
    if use_local:
        return {
            "command": "uv",
            "args": [
                "--directory",
                str(repo_root),
                "run",
                LIB_NAME,
                "run",
                "--transport",
                "stdio",
            ],
            "env": {"OTEL_SDK_DISABLED": "true"},
        }
    return {
        "command": "uvx",
        "args": [LIB_NAME, "run", "--transport", "stdio"],
    }


def _migrate_legacy_entries(config: dict) -> list[str]:
    """Remove every legacy multi-server entry from *config* (in place).

    Returns the sorted list of keys that were removed. Legacy keys are:

    - Any key starting with ``opendata-mcp-`` (individual providers, plus
      the old ``opendata-mcp-meta`` and ``opendata-mcp-all`` two-server
      install).
    - The double-prefixed mistakes from earlier CLI versions.
    """
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return []

    to_remove = sorted(
        k
        for k in servers
        if k.startswith(_LEGACY_PREFIX)
        or k
        in {
            _LEGACY_DOUBLE_META,
            _LEGACY_DOUBLE_ALL,
        }
    )
    for key in to_remove:
        del servers[key]
    return to_remove


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _HelpOnErrorGroup(click.Group):
    """Click group that prints full help (not just a hint) on unknown commands.

    Default Click behavior on `meta-data-mcp bogus`:
        Usage: meta-data-mcp [OPTIONS] COMMAND [ARGS]...
        Try 'meta-data-mcp --help' for help.
        Error: No such command 'bogus'.

    With this subclass:
        Error: No such command 'bogus'.
        <full help output, listing all real commands>

    The user can immediately see what they should have typed instead of
    needing to re-run with --help.
    """

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as exc:
            click.echo(f"Error: {exc.format_message()}", err=True)
            click.echo(err=True)
            click.echo(ctx.get_help(), err=True)
            ctx.exit(2)


@click.group(
    cls=_HelpOnErrorGroup,
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        # Accept both `-h` and `--help`. Inherited by every subcommand via
        # Click's context propagation, so `meta-data-mcp run -h` also works.
        "help_option_names": ["-h", "--help"],
    },
)
def cli() -> None:
    """meta-data-mcp — the single MCP server for ~60 open-data plugins."""


@cli.command()
@click.option(
    "--transport",
    default="sse",
    show_default=True,
    type=click.Choice(["stdio", "sse"]),
    help="Transport protocol (sse for interactive, stdio for Claude Desktop).",
)
@click.option(
    "--port",
    default=8000,
    show_default=True,
    type=int,
    help="Port for SSE transport.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host for SSE transport.",
)
def run(transport: str, port: int, host: str) -> None:
    """Run the meta-data-mcp server."""
    try:
        from meta_data_mcp.providers import meta_data_mcp as server_module

        anyio.run(server_module.main, transport, port, host)
    except Exception as e:
        import traceback

        traceback.print_exc()
        click.echo(f"Error running server: {e}", err=True)
        sys.exit(1)


@cli.command()
def version() -> None:
    """Show the package version."""
    click.echo(f"{LIB_NAME} version: {__version__}")


@cli.command(name="list")
def list_plugins() -> None:
    """List the internal plugins bundled with this server (informational).

    Plugins are not separately installable or addressable; they are loaded
    automatically when the server starts.
    """
    import pkgutil

    import meta_data_mcp.providers as providers_pkg

    plugins = sorted(
        name
        for _, name, _ in pkgutil.iter_modules(providers_pkg.__path__)
        if name not in ("__template__", "__init__", "utils", "meta_data_mcp")
    )

    if not plugins:
        click.echo("No plugins found.")
        return

    click.echo(f"{len(plugins)} plugins bundled in {LIB_NAME}:")
    for name in plugins:
        click.echo(f"  - {name}")
    click.echo("\nAll plugin tools are exposed through the one `meta-data-mcp` server.")


@cli.command()
@click.option(
    "--plugin",
    default=None,
    help=(
        "Optional: show the docstring for a specific internal plugin. "
        "Without --plugin, shows the meta-server overview."
    ),
)
def info(plugin: str | None) -> None:
    """Show information about the server (or a specific internal plugin)."""
    if plugin is None:
        click.echo(f"{LIB_NAME} — single MCP server, version {__version__}.")
        click.echo(
            "Exposes discovery tools plus the tools of every bundled plugin "
            "under one tool namespace.",
        )
        click.echo("\nUse `meta-data-mcp list` to enumerate the plugins.")
        return

    try:
        import importlib

        module = importlib.import_module(f"meta_data_mcp.providers.{plugin}")
    except ImportError as e:
        click.echo(f"Plugin '{plugin}' not found: {e}", err=True)
        sys.exit(1)

    click.echo(f"Plugin: {plugin}")
    doc = getattr(module, "__doc__", None)
    if doc:
        click.echo(f"Description: {doc.strip()}")


@cli.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("_extra", nargs=-1)
@click.option(
    "--local",
    is_flag=True,
    help="Force local development mode using absolute paths.",
)
@click.option("--force", is_flag=True, help="Overwrite an existing configuration.")
@click.option(
    "--print-json",
    "print_json",
    is_flag=True,
    help=(
        "Print the server's JSON configuration snippet to stdout and exit "
        "without touching any config file. Useful for piping into other "
        "MCP clients."
    ),
)
@click.option(
    "--client",
    "client",
    type=str,
    default=None,
    help=(
        "Target a specific MCP client (claude-desktop, claude-code, cursor, "
        "windsurf, gemini, lm-studio) or 'all'. Default: every client "
        "detected as installed on this machine. Run `meta-data-mcp clients` "
        "to list."
    ),
)
def setup(
    _extra: tuple,
    local: bool,
    force: bool,
    print_json: bool,
    client: str | None,
) -> None:
    """Register the one meta-data-mcp server in every installed MCP client.

    By default, scans candidate config paths for known MCP clients and
    writes to each one whose parent directory exists on disk. Pass
    ``--client KEY`` to target one explicitly, or ``--client all`` to
    write to every supported client whether or not it's detected.

    Any legacy multi-server entries from older CLI versions are removed
    automatically.
    """
    repo_root = Path(__file__).parent.parent.resolve()
    is_local_repo = (repo_root / "pyproject.toml").exists()
    use_local = is_local_repo or local
    entry = _server_entry(use_local, repo_root)

    if print_json:
        snippet = {SERVER_KEY: entry}
        click.echo(json.dumps(snippet, indent=2))

        auth_token = os.getenv("META_DATA_MCP_AUTH_TOKEN")
        if auth_token:
            remote_snippet = {
                SERVER_KEY: {
                    "type": "sse",
                    "url": "https://YOUR-HOST/sse",
                    "headers": {"Authorization": f"Bearer {auth_token}"},
                },
            }
            click.echo(
                "\n# SSE bearer auth is enabled (META_DATA_MCP_AUTH_TOKEN is set).\n"
                "# The snippet above launches the server locally via stdio and does\n"
                "# NOT need auth. To wire a REMOTE MCP client to this server over\n"
                "# SSE, use the following client config (replace YOUR-HOST with\n"
                "# the public hostname, e.g. linzalytics.com):\n"
                f"#\n# {json.dumps(remote_snippet, indent=2).replace(chr(10), chr(10) + '# ')}",
                err=True,
            )
        return

    # Resolve target clients.
    if client == "all":
        targets = list(CLIENTS.values())
    elif client is not None:
        if client not in CLIENTS:
            click.echo(
                f"Unknown client '{client}'. Supported keys: "
                f"{', '.join(CLIENTS)}. Use 'all' to target every client.",
                err=True,
            )
            sys.exit(1)
        targets = [CLIENTS[client]]
    else:
        targets = _detect_installed_clients()
        if not targets:
            click.echo(
                "No installed MCP clients detected. Pass --client KEY to "
                "target one explicitly, --client all to write to every "
                "supported client, or run `meta-data-mcp clients` for the "
                "list of supported keys.",
                err=True,
            )
            sys.exit(1)

    mode_label = (
        f"LOCAL mode pointing to {repo_root}"
        if use_local
        else f"GLOBAL mode using 'uvx {LIB_NAME}'"
    )
    click.echo(f"Configuring '{SERVER_KEY}' in {mode_label}.")

    successes = 0
    for spec in targets:
        config_path = spec.config_path_fn()
        if config_path is None:
            click.echo(f"  - {spec.label}: skipped (unsupported on this OS)", err=True)
            continue
        if _write_server_to_client(spec, config_path, entry, force=force):
            successes += 1

    if successes:
        click.echo(
            f"\nConfigured '{SERVER_KEY}' in {successes} client(s). "
            "Restart the affected MCP client(s) to apply.",
        )
    else:
        click.echo(
            f"No requested MCP clients were configured for '{SERVER_KEY}'.",
            err=True,
        )
        sys.exit(1)


def _write_server_to_client(
    spec: ClientSpec,
    config_path: Path,
    entry: dict,
    *,
    force: bool,
) -> bool:
    """Write the SERVER_KEY entry into a single client config. Returns True on write."""
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    config = _load_config_safe(config_path)
    if config is None:
        click.echo(
            f"  ✗ {spec.label}: skipped ({config_path} contains invalid JSON)",
            err=True,
        )
        return False
    if not isinstance(config, dict):
        click.echo(
            f"  ✗ {spec.label}: skipped ({config_path} top-level is "
            f"{type(config).__name__}, not an object)",
            err=True,
        )
        return False

    servers = config.get("mcpServers")
    if servers is None:
        # Missing or explicit `null` — initialise.
        config["mcpServers"] = {}
    elif not isinstance(servers, dict):
        click.echo(
            f"  ✗ {spec.label}: skipped ({config_path} has a non-object "
            f"`mcpServers` value of type {type(servers).__name__}; "
            "fix it manually).",
            err=True,
        )
        return False

    removed = _migrate_legacy_entries(config)
    if removed:
        click.echo(f"  - {spec.label}: removed {len(removed)} legacy entry/entries.")

    if (
        SERVER_KEY in config["mcpServers"]
        and not force
        and not click.confirm(
            f"  {spec.label}: '{SERVER_KEY}' already present at {config_path}. "
            "Overwrite?",
            default=False,
        )
    ):
        click.echo(f"  - {spec.label}: skipped.")
        return False

    config["mcpServers"][SERVER_KEY] = entry
    _backup_config(config_path)
    config_path.write_text(json.dumps(config, indent=2))
    click.echo(f"  ✓ {spec.label}: {config_path}")
    return True


@cli.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("_extra", nargs=-1)
@click.option(
    "--client",
    "client",
    type=str,
    default=None,
    help=(
        "Target a specific MCP client or 'all'. Default: every client where "
        f"'{SERVER_KEY}' is currently registered."
    ),
)
def remove(_extra: tuple, client: str | None) -> None:
    """Unregister meta-data-mcp from every installed MCP client where it's set."""
    if client == "all":
        targets = list(CLIENTS.values())
    elif client is not None:
        if client not in CLIENTS:
            click.echo(
                f"Unknown client '{client}'. Supported: {', '.join(CLIENTS)}.",
                err=True,
            )
            sys.exit(1)
        targets = [CLIENTS[client]]
    else:
        targets = _detect_installed_clients()

    if not targets:
        click.echo("No MCP clients detected — nothing to remove.")
        return

    removed_count = 0
    for spec in targets:
        config_path = spec.config_path_fn()
        if config_path is None or not config_path.exists():
            continue
        config = _load_config_safe(config_path)
        if config is None:
            click.echo(
                f"  ✗ {spec.label}: skipped ({config_path} contains invalid JSON)",
                err=True,
            )
            continue
        if not isinstance(config, dict):
            click.echo(
                f"  ✗ {spec.label}: skipped ({config_path} top-level is "
                f"{type(config).__name__}, not an object)",
                err=True,
            )
            continue
        servers = config.get("mcpServers")
        if not isinstance(servers, dict):
            # Missing, null, or malformed — nothing for us to remove.
            continue
        if SERVER_KEY not in servers:
            continue
        del servers[SERVER_KEY]
        if not servers:
            config.pop("mcpServers", None)
        _backup_config(config_path)
        config_path.write_text(json.dumps(config, indent=2))
        click.echo(f"  ✓ {spec.label}: removed from {config_path}")
        removed_count += 1

    if removed_count == 0:
        click.echo(f"'{SERVER_KEY}' is not configured in any detected client.")
    else:
        click.echo(
            f"\nRemoved '{SERVER_KEY}' from {removed_count} client(s). "
            "Restart the affected MCP client(s) to apply.",
        )


@cli.command(name="clients")
def list_clients_command() -> None:
    """List supported MCP clients and their installed/configured status on this machine."""
    detected_keys = {s.key for s in _detect_installed_clients()}
    click.echo("Supported MCP clients:")
    for key, spec in CLIENTS.items():
        config_path = spec.config_path_fn()
        if config_path is None:
            status = "  unsupported on this OS"
            path_str = "—"
        else:
            installed = key in detected_keys
            configured = False
            if installed and config_path.exists():
                cfg = _load_config_safe(config_path)
                if isinstance(cfg, dict):
                    servers = cfg.get("mcpServers")
                    if isinstance(servers, dict):
                        configured = SERVER_KEY in servers
            if configured:
                status = "  ✓ configured"
            elif installed:
                status = "  • installed (not configured)"
            else:
                status = "    not detected"
            path_str = str(config_path)
        click.echo(f"  {status:<32}  {spec.label:<18}  {path_str}")


@cli.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("_extra", nargs=-1)
@click.option(
    "--apply",
    is_flag=True,
    help="Remove detected legacy entries (defaults to dry-run preview).",
)
def cleanup(_extra: tuple, apply: bool) -> None:
    """Detect and remove legacy multi-server entries from Claude Desktop config.

    Older versions of this CLI registered one MCP server per provider plus
    separate ``opendata-mcp-meta`` and ``opendata-mcp-all`` aggregators.
    The current architecture uses ONE server: ``meta-data-mcp``. This
    command finds and removes the obsolete entries.
    """
    config_path = _config_path()
    if not config_path.exists():
        click.echo("No Claude Desktop config — nothing to clean up.")
        return

    config = _load_config(config_path)
    servers = config.get("mcpServers", {})

    legacy = sorted(
        k
        for k in servers
        if k.startswith(_LEGACY_PREFIX)
        or k
        in {
            _LEGACY_DOUBLE_META,
            _LEGACY_DOUBLE_ALL,
        }
    )
    if not legacy:
        click.echo("No legacy entries found.")
        if SERVER_KEY in servers:
            click.echo(f"✓ '{SERVER_KEY}' is registered.")
        else:
            click.echo(f"Tip: run `{LIB_NAME} setup` to register the server.")
        return

    click.echo(f"Found {len(legacy)} legacy entry/entries:")
    for key in legacy:
        click.echo(f"  - {key}")

    if not apply:
        click.echo("\nDry run — no changes made. Re-run with --apply to remove.")
        return

    removed = _migrate_legacy_entries(config)
    _backup_config(config_path)
    config_path.write_text(json.dumps(config, indent=2))
    click.echo(f"\nRemoved {len(removed)} legacy entry/entries.")
    if SERVER_KEY not in config.get("mcpServers", {}):
        click.echo(f"Tip: run `{LIB_NAME} setup` to register the server.")


@cli.command(
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("_extra", nargs=-1)
@click.option(
    "--local",
    is_flag=True,
    help="Force local development mode using absolute paths.",
)
def inspect(_extra: tuple, local: bool) -> None:
    """Launch MCP Inspector against the meta-data-mcp server.

    Requires Node.js / npx. Opens http://localhost:5173 where you can
    browse the server's tools and try them out.
    """
    import shutil
    import subprocess

    if shutil.which("npx") is None:
        click.echo(
            "npx not found. Install Node.js (https://nodejs.org/) to use mcp-inspector.",
            err=True,
        )
        sys.exit(1)

    repo_root = Path(__file__).parent.parent.resolve()
    is_local_repo = (repo_root / "pyproject.toml").exists()
    use_local = is_local_repo or local

    if use_local:
        server_cmd = [
            "uv",
            "--directory",
            str(repo_root),
            "run",
            LIB_NAME,
            "run",
            "--transport",
            "stdio",
        ]
    else:
        server_cmd = ["uvx", LIB_NAME, "run", "--transport", "stdio"]

    click.echo(f"Starting MCP Inspector against {LIB_NAME}…")
    click.echo("Open http://localhost:5173 in your browser (Ctrl-C to stop).")

    try:
        subprocess.run(
            ["npx", "-y", "@modelcontextprotocol/inspector"] + server_cmd,
            check=True,
        )
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as e:
        click.echo(f"Inspector exited with code {e.returncode}.", err=True)
        sys.exit(e.returncode)


# ---------------------------------------------------------------------------
# Defensive: strip Claude Desktop injected mcpServers keys from sys.argv
# ---------------------------------------------------------------------------


def _strip_injected_server_keys() -> None:
    """Strip Claude Desktop ``mcpServers`` keys that get appended to sys.argv.

    When Claude Desktop restarts an MCP server, it sometimes appends the
    server's own config key as a positional arg. We strip those (but never
    a real subcommand name).
    """
    try:
        config_path = _config_path()
        if not config_path.exists():
            return
        config = json.loads(config_path.read_text())
        server_keys = set(config.get("mcpServers", {}).keys())
        if not server_keys:
            return
        known_commands = set(cli.commands.keys())
        already_preserved: set[str] = set()
        clean: list[str] = []
        for arg in sys.argv[1:]:
            if arg not in server_keys:
                clean.append(arg)
            elif arg in known_commands and arg not in already_preserved:
                clean.append(arg)
                already_preserved.add(arg)
            # else: drop the injected server key
        sys.argv[1:] = clean
    except Exception:
        pass  # never break the CLI if config can't be read


def main() -> None:
    _strip_injected_server_keys()
    cli()


if __name__ == "__main__":
    main()
