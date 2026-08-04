"""Tests for the meta-data-mcp CLI.

The CLI exposes ONE server (``meta-data-mcp``). Commands like ``info``,
``setup``, ``remove``, and ``inspect`` therefore do not take a provider
argument; plugins are an internal concept.
"""

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from meta_data_mcp.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_run_starts_the_server(runner):
    """`run` boots the unified meta-data-mcp server."""

    async def mock_main(transport="stdio", port=8000, host="127.0.0.1"):
        return None

    with patch("meta_data_mcp.providers.meta_data_mcp.main", new=mock_main):
        with patch("meta_data_mcp.cli.anyio.run") as mock_run:
            result = runner.invoke(cli, ["run"])

    assert result.exit_code == 0, result.output
    mock_run.assert_called_once_with(mock_main, "sse", 8000, "127.0.0.1")


def test_run_rejects_positional_args(runner):
    """`run` takes no positional args (no per-provider server)."""
    result = runner.invoke(cli, ["run", "some_provider"])
    assert result.exit_code == 2


def test_list_shows_plugins(runner):
    plugin_names = ["us_nasa", "global_frankfurter"]
    with patch("pkgutil.iter_modules") as mock_iter:
        mock_iter.return_value = [(None, name, False) for name in plugin_names]
        result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0
    for name in plugin_names:
        assert name in result.output
    assert "plugins bundled in meta-data-mcp" in result.output


def test_list_no_plugins(runner):
    with patch("pkgutil.iter_modules") as mock_iter:
        mock_iter.return_value = []
        result = runner.invoke(cli, ["list"])

    assert result.exit_code == 0
    assert "No plugins found" in result.output


def test_info_default_shows_server_overview(runner):
    result = runner.invoke(cli, ["info"])
    assert result.exit_code == 0
    assert "meta-data-mcp" in result.output
    assert "single MCP server" in result.output


def test_info_plugin_flag_shows_plugin_doc(runner):
    mock_module = type("Module", (), {"__doc__": "Test plugin description"})
    with patch("importlib.import_module", return_value=mock_module):
        result = runner.invoke(cli, ["info", "--plugin", "test_plugin"])

    assert result.exit_code == 0
    assert "Plugin: test_plugin" in result.output
    assert "Test plugin description" in result.output


def test_info_unknown_plugin_errors(runner):
    result = runner.invoke(cli, ["info", "--plugin", "nonexistent"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_version_command(runner):
    with patch("meta_data_mcp.cli.__version__", "1.0.0"):
        result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "meta-data-mcp version: 1.0.0" in result.output


def test_short_help_flag_works(runner):
    """`-h` must be accepted as an alias for `--help` at the group level."""
    short = runner.invoke(cli, ["-h"])
    long = runner.invoke(cli, ["--help"])
    assert short.exit_code == 0
    assert long.exit_code == 0
    # Both produce the same usage banner + commands list.
    assert "Usage: cli [OPTIONS] COMMAND" in short.output
    assert "Commands:" in short.output
    # Help text must advertise both flags.
    assert "-h, --help" in short.output


def test_short_help_flag_works_on_subcommand(runner):
    """Subcommands inherit `help_option_names` from the parent group."""
    result = runner.invoke(cli, ["run", "-h"])
    assert result.exit_code == 0
    assert "Run the meta-data-mcp server." in result.output


def test_unknown_command_shows_full_help(runner):
    """An invalid command must exit non-zero AND emit the full help listing.

    Default Click shows only a one-line "Try `--help` for help." hint; we
    upgrade that to the full command listing so the user can self-correct
    without a second invocation.
    """
    result = runner.invoke(cli, ["bogus-nonsense"])
    assert result.exit_code != 0
    # Error message identifies the bad input.
    assert "No such command 'bogus-nonsense'" in result.output
    # Full help must appear — at minimum the "Commands:" header AND at
    # least one real command name.
    assert "Commands:" in result.output
    assert "version" in result.output
    assert "setup" in result.output


def test_setup_writes_one_server_key(runner, tmp_path):
    """`setup` registers exactly one key: ``meta-data-mcp``."""
    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"

    with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--force"])

    assert result.exit_code == 0, result.output

    config = json.loads(config_path.read_text())
    assert list(config["mcpServers"].keys()) == ["meta-data-mcp"]


def test_setup_migrates_legacy_entries(runner, tmp_path):
    """`setup` removes any legacy multi-server keys automatically."""
    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"
    config_path.write_text(
        '{"mcpServers": {'
        '  "opendata-mcp-us-nasa": {},'
        '  "opendata-mcp-meta": {},'
        '  "opendata-mcp-all": {},'
        '  "unrelated-server": {}'
        "}}",
    )

    with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--force"])

    assert result.exit_code == 0, result.output

    config = json.loads(config_path.read_text())
    assert "unrelated-server" in config["mcpServers"]
    assert "meta-data-mcp" in config["mcpServers"]
    # all legacy opendata-mcp-* entries were removed
    assert not any(k.startswith("opendata-mcp-") for k in config["mcpServers"])


def test_setup_creates_backup_before_writing(runner, tmp_path):
    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"
    original_content = '{"mcpServers": {}}'
    config_path.write_text(original_content)

    with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            runner.invoke(cli, ["setup", "--force"])

    backup = config_path.with_suffix(".json.bak")
    assert backup.exists()
    assert backup.read_text() == original_content


def test_setup_print_json_emits_snippet_without_writing(runner, tmp_path):
    """`setup --print-json` prints the snippet and does not touch disk."""
    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"

    with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--print-json"])

    assert result.exit_code == 0, result.output
    snippet = json.loads(result.output)
    assert list(snippet.keys()) == ["meta-data-mcp"]
    entry = snippet["meta-data-mcp"]
    assert "command" in entry
    assert "args" in entry
    # Did not create or modify the config file
    assert not config_path.exists()
    assert not config_path.with_suffix(".json.bak").exists()


def test_setup_print_json_works_on_unsupported_platforms(runner, tmp_path):
    """`setup --print-json` should not require macOS/Windows."""
    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--print-json"])

    assert result.exit_code == 0, result.output
    snippet = json.loads(result.output)
    assert "meta-data-mcp" in snippet


def test_setup_print_json_respects_local_flag(runner, tmp_path):
    """With --local, the printed entry uses 'uv --directory' invocation."""
    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--print-json", "--local"])

    assert result.exit_code == 0, result.output
    entry = json.loads(result.output)["meta-data-mcp"]
    assert entry["command"] == "uv"
    assert "--directory" in entry["args"]


def test_setup_print_json_omits_auth_when_token_unset(runner, tmp_path, monkeypatch):
    """No auth instructions when META_DATA_MCP_AUTH_TOKEN is not set."""
    monkeypatch.delenv("META_DATA_MCP_AUTH_TOKEN", raising=False)
    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--print-json"])

    assert result.exit_code == 0
    json.loads(result.stdout)  # stdout is parseable JSON
    assert "bearer" not in result.stderr.lower()


def test_setup_print_json_surfaces_auth_when_token_set(runner, tmp_path, monkeypatch):
    """With token set, stderr surfaces SSE client config instructions."""
    monkeypatch.setenv("META_DATA_MCP_AUTH_TOKEN", "test-token-abc123")
    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--print-json"])

    assert result.exit_code == 0
    # stdout remains a clean parseable snippet (pipeable to jq / >)
    snippet = json.loads(result.stdout)
    assert list(snippet.keys()) == ["meta-data-mcp"]
    assert "headers" not in snippet["meta-data-mcp"]
    # stderr carries the auth wiring instructions with the real token
    assert "META_DATA_MCP_AUTH_TOKEN" in result.stderr
    assert "Bearer test-token-abc123" in result.stderr
    assert "https://YOUR-HOST/sse" in result.stderr
    # The "type": "sse" discriminator must be present — Claude Code requires
    # it to recognise the entry as a remote SSE server (without it the entry
    # is treated as stdio and skipped with "command: expected string").
    assert '"type": "sse"' in result.stderr


def test_setup_writes_to_claude_code_when_detected(runner, tmp_path):
    """When ~/.claude.json exists, setup writes the meta-data-mcp entry there."""
    (tmp_path / ".claude.json").write_text('{"mcpServers": {}, "other": 1}')

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--force"])

    assert result.exit_code == 0, result.output
    config = json.loads((tmp_path / ".claude.json").read_text())
    assert "meta-data-mcp" in config["mcpServers"]
    # Pre-existing keys are preserved
    assert config["other"] == 1


def test_setup_writes_to_cursor_when_dir_exists(runner, tmp_path):
    """When ~/.cursor/ exists, setup creates ~/.cursor/mcp.json."""
    (tmp_path / ".cursor").mkdir()

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--force"])

    assert result.exit_code == 0, result.output
    cursor_config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "meta-data-mcp" in cursor_config["mcpServers"]


def test_setup_writes_to_all_detected_clients_in_one_run(runner, tmp_path):
    """A single `setup` updates every client whose detect path exists."""
    # Set up three clients: Claude Code (file), Cursor (dir), Gemini (file)
    (tmp_path / ".claude.json").write_text("{}")
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "settings.json").write_text(
        '{"general": {"sessionRetention": true}}',
    )

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--force"])

    assert result.exit_code == 0, result.output
    for relpath in (".claude.json", ".cursor/mcp.json", ".gemini/settings.json"):
        cfg = json.loads((tmp_path / relpath).read_text())
        assert "meta-data-mcp" in cfg["mcpServers"], relpath
    # Pre-existing Gemini keys preserved
    gemini = json.loads((tmp_path / ".gemini/settings.json").read_text())
    assert "general" in gemini


def test_setup_no_clients_detected_errors_with_helpful_hint(runner, tmp_path):
    """If no clients are installed, setup exits non-zero with guidance."""
    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--force"])

    assert result.exit_code != 0
    assert "No installed MCP clients" in result.output
    assert "--client" in result.output


def test_setup_client_flag_targets_one_explicit_client(runner, tmp_path):
    """`--client claude-code` writes only to ~/.claude.json, ignoring detection."""
    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--client", "claude-code", "--force"])

    assert result.exit_code == 0, result.output
    config = json.loads((tmp_path / ".claude.json").read_text())
    assert "meta-data-mcp" in config["mcpServers"]
    # Other client configs were not created
    assert not (tmp_path / ".cursor" / "mcp.json").exists()


def test_setup_client_all_writes_to_every_supported_client(runner, tmp_path):
    """`--client all` writes to every supported client, creating parent dirs."""
    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--client", "all", "--force"])

    assert result.exit_code == 0, result.output
    # Linux: claude-desktop is unsupported on this OS and should be skipped
    for relpath in (
        ".claude.json",
        ".cursor/mcp.json",
        ".codeium/windsurf/mcp_config.json",
        ".gemini/settings.json",
        ".cache/lm-studio/mcp.json",
    ):
        cfg = json.loads((tmp_path / relpath).read_text())
        assert "meta-data-mcp" in cfg["mcpServers"], relpath


def test_setup_client_unknown_key_errors(runner, tmp_path):
    with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
        result = runner.invoke(cli, ["setup", "--client", "not-a-client", "--force"])
    assert result.exit_code != 0
    assert "Unknown client" in result.output


def test_setup_skips_corrupt_json_but_continues_with_others(runner, tmp_path):
    """A single invalid-JSON client should not abort writes to siblings."""
    (tmp_path / ".claude.json").write_text("{ broken json")
    (tmp_path / ".cursor").mkdir()

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--force"])

    assert result.exit_code == 0, result.output
    # Cursor still got configured
    cursor_config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "meta-data-mcp" in cursor_config["mcpServers"]
    # Claude Code (corrupt) was skipped, content unchanged
    assert (tmp_path / ".claude.json").read_text() == "{ broken json"


def test_clients_command_lists_status(runner, tmp_path):
    """`meta-data-mcp clients` shows installed/configured status for each."""
    (tmp_path / ".claude.json").write_text(
        '{"mcpServers": {"meta-data-mcp": {"command": "x"}}}',
    )
    (tmp_path / ".cursor").mkdir()

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["clients"])

    assert result.exit_code == 0, result.output
    assert "Claude Code" in result.output
    assert "configured" in result.output  # claude-code has SERVER_KEY
    assert "Cursor" in result.output
    assert (
        "installed (not configured)" in result.output
    )  # cursor: dir exists, file doesn't


def test_setup_handles_null_mcpservers_value(runner, tmp_path):
    """A `null` mcpServers value should be replaced with an empty object, not crash."""
    (tmp_path / ".claude.json").write_text('{"mcpServers": null, "other": 1}')

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--force"])

    assert result.exit_code == 0, result.output
    config = json.loads((tmp_path / ".claude.json").read_text())
    assert isinstance(config["mcpServers"], dict)
    assert "meta-data-mcp" in config["mcpServers"]
    assert config["other"] == 1


def test_setup_skips_non_object_mcpservers_and_continues(runner, tmp_path):
    """A list/string mcpServers value should skip that client without aborting siblings."""
    # Claude Code has mcpServers as a list (malformed)
    (tmp_path / ".claude.json").write_text('{"mcpServers": ["foo"], "other": 1}')
    # Cursor is also installed and well-formed
    (tmp_path / ".cursor").mkdir()

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--force"])

    assert result.exit_code == 0, result.output
    # Claude Code config was NOT mutated (skipped due to malformed value)
    claude = json.loads((tmp_path / ".claude.json").read_text())
    assert claude["mcpServers"] == ["foo"]
    assert claude["other"] == 1
    # Helpful error mentioning the type was emitted
    assert "non-object" in result.output and "list" in result.output
    # Sibling (Cursor) was still configured
    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "meta-data-mcp" in cursor["mcpServers"]


def test_setup_skips_string_mcpservers_value(runner, tmp_path):
    """String mcpServers value: skip with type-named error, no crash, file untouched.

    Targeting only this one (malformed) client results in 0 successes, so
    `setup` exits non-zero — but the run completes cleanly without a
    TypeError on the string subscript.
    """
    (tmp_path / ".claude.json").write_text('{"mcpServers": "oops"}')

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--client", "claude-code", "--force"])

    # No-success exit + per-client skip message (proves no crash)
    assert result.exit_code != 0
    assert "non-object" in result.output and "str" in result.output
    # File untouched — preserves user data
    assert json.loads((tmp_path / ".claude.json").read_text()) == {"mcpServers": "oops"}


def test_setup_skips_top_level_non_object_config(runner, tmp_path):
    """If the whole file is a JSON array/string, skip cleanly without crashing."""
    (tmp_path / ".claude.json").write_text("[1, 2, 3]")

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["setup", "--client", "claude-code", "--force"])

    # No-success exit code, but the run completed without TypeError
    assert result.exit_code != 0
    assert "top-level" in result.output and "list" in result.output
    # File untouched
    assert json.loads((tmp_path / ".claude.json").read_text()) == [1, 2, 3]


def test_remove_does_not_crash_on_non_object_mcpservers(runner, tmp_path):
    """Remove must tolerate string/list mcpServers without raising TypeError."""
    # String containing SERVER_KEY substring — `in` would be true but `del` would fail
    (tmp_path / ".claude.json").write_text(
        '{"mcpServers": "prefix-meta-data-mcp-suffix"}',
    )
    # And a list
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text('{"mcpServers": ["meta-data-mcp"]}')

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["remove"])

    assert result.exit_code == 0, result.output
    # Neither file was mutated
    assert json.loads((tmp_path / ".claude.json").read_text()) == {
        "mcpServers": "prefix-meta-data-mcp-suffix",
    }
    assert json.loads((tmp_path / ".cursor" / "mcp.json").read_text()) == {
        "mcpServers": ["meta-data-mcp"],
    }


def test_remove_client_flag_targets_only_that_client(runner, tmp_path):
    """`remove --client cursor` strips only from Cursor even if Claude Code also has it."""
    (tmp_path / ".claude.json").write_text(
        '{"mcpServers": {"meta-data-mcp": {}, "other": {}}}',
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(
        '{"mcpServers": {"meta-data-mcp": {}}}',
    )

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["remove", "--client", "cursor"])

    assert result.exit_code == 0, result.output
    # Cursor: meta-data-mcp removed, mcpServers became empty and was dropped
    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "mcpServers" not in cursor
    # Claude Code: untouched (--client cursor only targeted Cursor)
    claude = json.loads((tmp_path / ".claude.json").read_text())
    assert claude["mcpServers"] == {"meta-data-mcp": {}, "other": {}}


def test_remove_client_unknown_key_errors(runner, tmp_path):
    """`remove --client not-a-client` exits non-zero with a supported-list hint."""
    with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
        result = runner.invoke(cli, ["remove", "--client", "not-a-client"])

    assert result.exit_code != 0
    assert "Unknown client" in result.output
    # Hint enumerates the supported keys
    assert "claude-desktop" in result.output


def test_remove_client_all_targets_every_supported_client(runner, tmp_path):
    """`remove --client all` strips meta-data-mcp from every client where it's set,
    regardless of whether the client is detected as installed.
    """
    # Claude Code: has SERVER_KEY
    (tmp_path / ".claude.json").write_text('{"mcpServers": {"meta-data-mcp": {}}}')
    # Cursor: has SERVER_KEY
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(
        '{"mcpServers": {"meta-data-mcp": {}, "keep": {}}}',
    )
    # Gemini: does NOT have SERVER_KEY (should be silently skipped)
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "settings.json").write_text(
        '{"mcpServers": {"unrelated": {}}}',
    )

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["remove", "--client", "all"])

    assert result.exit_code == 0, result.output
    assert "Claude Code" in result.output
    assert "Cursor" in result.output

    claude = json.loads((tmp_path / ".claude.json").read_text())
    assert (
        "mcpServers" not in claude
    )  # was {"meta-data-mcp": {}}; emptied → key dropped

    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "meta-data-mcp" not in cursor["mcpServers"]
    assert cursor["mcpServers"] == {"keep": {}}

    gemini = json.loads((tmp_path / ".gemini" / "settings.json").read_text())
    assert gemini == {"mcpServers": {"unrelated": {}}}  # untouched


def test_remove_multi_client(runner, tmp_path):
    """`remove` strips meta-data-mcp from every detected client."""
    (tmp_path / ".claude.json").write_text(
        '{"mcpServers": {"meta-data-mcp": {}, "other": {}}}',
    )
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "mcp.json").write_text(
        '{"mcpServers": {"meta-data-mcp": {}}}',
    )

    with patch("meta_data_mcp.cli.platform.system", return_value="Linux"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["remove"])

    assert result.exit_code == 0, result.output
    claude = json.loads((tmp_path / ".claude.json").read_text())
    assert "meta-data-mcp" not in claude.get("mcpServers", {})
    assert "other" in claude["mcpServers"]
    cursor = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    # Cursor had only meta-data-mcp; mcpServers should be removed when empty
    assert "mcpServers" not in cursor


def test_remove_removes_the_server_key(runner, tmp_path):
    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"
    config_path.write_text('{"mcpServers": {"meta-data-mcp": {}, "other-server": {}}}')

    with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["remove"])

    assert result.exit_code == 0

    config = json.loads(config_path.read_text())
    assert "meta-data-mcp" not in config["mcpServers"]
    assert "other-server" in config["mcpServers"]


def test_remove_creates_backup_before_writing(runner, tmp_path):
    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"
    original_content = '{"mcpServers": {"meta-data-mcp": {}}}'
    config_path.write_text(original_content)

    with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            runner.invoke(cli, ["remove"])

    backup = config_path.with_suffix(".json.bak")
    assert backup.exists()
    assert backup.read_text() == original_content


def test_cleanup_dry_run_lists_legacy_entries(runner, tmp_path):
    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"
    config_path.write_text(
        '{"mcpServers": {"opendata-mcp-us-nasa": {}, "opendata-mcp-meta": {}}}',
    )

    with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["cleanup"])

    assert result.exit_code == 0
    assert "opendata-mcp-us-nasa" in result.output
    assert "opendata-mcp-meta" in result.output
    assert "Dry run" in result.output
    # No write yet

    config = json.loads(config_path.read_text())
    assert "opendata-mcp-us-nasa" in config["mcpServers"]


def test_cleanup_apply_removes_legacy_entries(runner, tmp_path):
    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"
    config_path.write_text(
        '{"mcpServers": {"opendata-mcp-us-nasa": {}, "opendata-mcp-all": {}}}',
    )

    with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(cli, ["cleanup", "--apply"])

    assert result.exit_code == 0

    config = json.loads(config_path.read_text())
    assert not any(k.startswith("opendata-mcp-") for k in config.get("mcpServers", {}))


def test_cleanup_apply_creates_backup_before_writing(runner, tmp_path):
    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"
    original_content = '{"mcpServers": {"opendata-mcp-ch-sbb": {}}}'
    config_path.write_text(original_content)

    with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
        with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
            runner.invoke(cli, ["cleanup", "--apply"])

    backup = config_path.with_suffix(".json.bak")
    assert backup.exists()
    assert backup.read_text() == original_content


def test_strip_injected_server_keys_preserves_first_occurrence_only(tmp_path):
    """Injected server keys are stripped, but a real subcommand name is kept."""
    import sys as _sys

    from meta_data_mcp.cli import _strip_injected_server_keys

    claude_dir = tmp_path / "Library" / "Application Support" / "Claude"
    claude_dir.mkdir(parents=True)
    config_path = claude_dir / "claude_desktop_config.json"
    config_path.write_text('{"mcpServers": {"setup": {}, "meta-data-mcp": {}}}')

    original_argv = _sys.argv[:]
    try:
        _sys.argv = ["meta-data-mcp", "setup", "setup", "meta-data-mcp"]
        with patch("meta_data_mcp.cli.platform.system", return_value="Darwin"):
            with patch("meta_data_mcp.cli.Path.home", return_value=tmp_path):
                _strip_injected_server_keys()
        assert _sys.argv == ["meta-data-mcp", "setup"]
    finally:
        _sys.argv = original_argv
