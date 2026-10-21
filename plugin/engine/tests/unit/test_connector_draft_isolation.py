"""Drafting and probe sessions ignore the user's settings (#350, spec §4.2).

`claude -p` would otherwise load ~/.claude/settings.json: its global allow rules
could let the drafting model call a write tool, and its hooks would run. Both
sessions start with no setting sources and hooks off, and get back only what
the target server needs to be visible: its plugin's `enabledPlugins` key, or
its user-scope `mcpServers` entry through a private temp file.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from scout.scripts import connector_draft as cd
from scout.scripts.connector_detect import server_slug

PLUGIN = Path(__file__).parent.parent.parent.parent
SECRET = "sk-test-not-a-real-token-123"
DB_ENTRY = {"type": "stdio", "command": "example-db-mcp", "args": ["--ro"], "env": {"API_TOKEN": SECRET}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake Claude Code home: user settings with plugins, ~/.claude.json with user-scope servers."""
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    (h / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["mcp__example_db__drop_table"]},
                "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "true"}]}]},
                "enabledPlugins": {
                    "chat@market-a": True,
                    "chat@market-b": False,
                    "chat-extras@market-a": True,
                    "other@market-a": True,
                },
            }
        )
    )
    (h / ".claude.json").write_text(
        json.dumps({"mcpServers": {"example-db": DB_ENTRY, "other-db": {"command": "other"}}, "projects": {}})
    )
    return h


def _definition(slug: str) -> dict:
    t = f"mcp__{slug}__"
    return {
        "definitions": [
            {
                "key": "example_reader",
                "display_name": "Example reader",
                "probe": f"{t}list_items",
                "preset": "mail",
                "inbound": {"tools": [f"{t}search_items"]},
            }
        ],
        "summary": [{"key": "example_reader", "scans": "new items", "looks_up": "nothing"}],
    }


class Recorder:
    """Answers both calls; records argv and the --mcp-config file as it was during the call."""

    def __init__(self, slug: str, *, raise_on_call: BaseException | None = None, timeout: bool = False):
        self.slug = slug
        self.raise_on_call = raise_on_call
        self.timeout = timeout
        self.calls: list[list[str]] = []
        self.mcp_files: list[tuple[Path, dict, int, int]] = []

    def __call__(self, argv, stdin, timeout):
        self.calls.append(argv)
        if "--mcp-config" in argv:
            p = Path(argv[argv.index("--mcp-config") + 1])
            self.mcp_files.append(
                (p, json.loads(p.read_text()), stat.S_IMODE(p.stat().st_mode), stat.S_IMODE(p.parent.stat().st_mode))
            )
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if self.timeout:
            return None
        body = {"ok": True, "error": ""} if "haiku" in argv else _definition(self.slug)
        return cd.ClaudeResult(0, json.dumps({"is_error": False, "result": "", "structured_output": body}))


def _draft(server, rec, tmp_path):
    return cd.draft(server, plugin_root=PLUGIN, vault=tmp_path / "Scout", claude_bin="claude", runner=rec)


def _settings(argv: list[str]) -> dict:
    return json.loads(argv[argv.index("--settings") + 1])


# --- the exact argv --------------------------------------------------------


def test_draft_argv_is_pinned(tmp_path):
    schema = {"type": "object"}
    iso = cd.Isolation(settings={"disableAllHooks": True}, mcp_config=tmp_path / "mcp.json")
    assert cd.draft_argv("claude", "sonnet", schema, iso) == [
        "claude",
        "-p",
        "--model",
        "sonnet",
        "--tools",
        "ToolSearch",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "",
        "--settings",
        '{"disableAllHooks": true}',
        "--mcp-config",
        str(tmp_path / "mcp.json"),
        "--json-schema",
        '{"type": "object"}',
        "--max-budget-usd",
        "0.50",
        "--allowedTools",
        "ToolSearch",
    ]


def test_probe_argv_is_pinned():
    iso = cd.Isolation(settings={"disableAllHooks": True, "enabledPlugins": {"chat@market-a": True}}, mcp_config=None)
    assert cd.probe_argv("claude", "mcp__plugin_chat_chat__list_items", iso) == [
        "claude",
        "-p",
        "--model",
        "haiku",
        "--tools",
        "ToolSearch",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "",
        "--settings",
        '{"disableAllHooks": true, "enabledPlugins": {"chat@market-a": true}}',
        "--json-schema",
        json.dumps(cd.PROBE_SCHEMA),
        "--max-budget-usd",
        "0.20",
        "--allowedTools",
        "ToolSearch",
        "mcp__plugin_chat_chat__list_items",
    ]


def test_run_claude_env_turns_off_nonblocking_mcp_startup(monkeypatch):
    captured = {}

    class Done:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return Done()

    monkeypatch.setenv("MCP_CONNECTION_NONBLOCKING", "true")
    monkeypatch.setattr(cd.subprocess, "run", fake_run)
    cd.run_claude(["claude", "-p"], "x", 1.0)
    assert captured["env"]["MCP_CONNECTION_NONBLOCKING"] == "false"
    assert captured["env"]["PATH"].startswith(f"{Path.home()}/.local/bin:")  # still probe_env()
    assert captured["cwd"] == cd.tempfile.gettempdir()


# --- why a claude.ai connector's write tools stay out of reach ----------------
#
# Drafting a claude.ai connector needs those connectors visible, so neither call
# passes `--strict-mcp-config`, and every claude.ai connector on the account
# loads into the session (#374's spike: about 40, Slack, Gmail, Calendar, Drive
# and GitHub among them, write tools included). What keeps those tools from being
# called is `--permission-mode dontAsk` with an allowlist of ToolSearch plus, for
# the probe, one exact read tool: every other call is denied, not prompted.
# Admin-managed (policy) settings still apply and sit outside this; an allow
# rule there would widen it. Checked live on 2026-10-12 against Claude Code
# 2.1.278 (spec §4.2): an
# adversarial prompt made both sessions call `create_draft` on a connected
# claude.ai Gmail; each call was denied and listed in `permission_denials`, and
# no draft was created. These tests pin every input that decision depends on,
# so a change that widens it fails here first.

_BYPASS_FLAGS = (
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--permission-prompt-tool",
    "--permission-prompts",
    # The kebab aliases would add rules beside the pinned `--allowedTools`.
    "--allowed-tools",
    # A denylist would mean the allowlist stopped being the whole story.
    "--disallowedTools",
    "--disallowed-tools",
)
# Flags whose value the guarantee depends on: each must appear once, as two argv
# words, never as `--flag=value`, which `argv.index` would not see.
_GUARDED = ("--permission-mode", "--tools", "--allowedTools", "--settings", "--setting-sources")


def _values(argv: list[str], flag: str) -> list[str]:
    """Every word after ``flag`` up to the next flag (the CLI reads some as variadic)."""
    i = argv.index(flag) + 1
    j = next((k for k in range(i, len(argv)) if argv[k].startswith("--")), len(argv))
    return argv[i:j]


def _allowed_tools(argv: list[str]) -> list[str]:
    # Variadic in the CLI: it must be the last flag so nothing after it is read as a rule.
    assert _values(argv, "--allowedTools") == argv[argv.index("--allowedTools") + 1 :], argv
    return _values(argv, "--allowedTools")


@pytest.mark.parametrize("server", ["claude.ai Example Suite", "plugin:chat:chat", "example-db"])
def test_sessions_can_call_only_toolsearch_and_the_one_read_probe(home, tmp_path, server):
    rec = Recorder(server_slug(server))
    assert _draft(server, rec, tmp_path)["status"] == "drafted"
    draft_call, probe_call = rec.calls
    for argv in rec.calls:
        for flag in _GUARDED:
            assert argv.count(flag) == 1, flag
            assert not any(a.startswith(f"{flag}=") for a in argv), flag
        assert _values(argv, "--permission-mode") == ["dontAsk"], "anything else prompts or allows"
        assert _values(argv, "--tools") == ["ToolSearch"], "no built-in tool but ToolSearch"
        assert not any(a.split("=", 1)[0] in _BYPASS_FLAGS for a in argv), argv
        assert "permissions" not in _settings(argv), "--settings must never carry allow rules"
        assert argv[argv.index("--setting-sources") + 1] == "", "user allow rules stay out (#350)"
        assert not any("*" in rule or "," in rule or " " in rule for rule in _allowed_tools(argv))
    assert _allowed_tools(draft_call) == ["ToolSearch"]
    probe = _definition(server_slug(server))["definitions"][0]["probe"]
    assert _allowed_tools(probe_call) == ["ToolSearch", probe]


def test_claude_ai_connectors_stay_visible_on_purpose(home, tmp_path):
    """`--strict-mcp-config` would hide the connector being drafted. Leaving it
    off is deliberate; the allowlist above is what keeps the others' tools out of
    reach. If this flag is ever added, drafting a claude.ai connector breaks."""
    rec = Recorder(server_slug("claude.ai Example Suite"))
    _draft("claude.ai Example Suite", rec, tmp_path)
    for argv in rec.calls:
        assert "--strict-mcp-config" not in argv
        assert "--mcp-config" not in argv


def test_the_probe_allowlist_holds_one_exact_read_tool():
    """probe_argv refuses anything but one exact MCP tool name, so a probe can't
    smuggle a wildcard or a second rule into `--allowedTools`. Whether that one
    name reads is the gate's call (check_definitions, before any probe runs;
    test_connector_draft_gate.py); its classifier rejects the write tools the
    live check used."""
    for bad in ("mcp__claude_ai_Gmail__*", "mcp__claude_ai_Gmail__list_drafts,mcp__claude_ai_Gmail__create_draft"):
        with pytest.raises(ValueError):
            cd.probe_argv("claude", bad, cd.Isolation(settings={"disableAllHooks": True}, mcp_config=None))
    assert not cd.is_read_tool("mcp__claude_ai_Gmail__create_draft")
    assert not cd.is_read_tool("mcp__claude_ai_Slack__slack_send_message_draft")


# --- what each kind of server gets back --------------------------------------


def test_plugin_server_gets_only_its_own_enabled_plugin_keys(home, tmp_path):
    rec = Recorder("plugin_chat_chat")
    out = _draft("plugin:chat:chat", rec, tmp_path)
    assert out["status"] == "drafted", out
    assert len(rec.calls) == 2  # draft + probe
    for argv in rec.calls:
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert _settings(argv) == {"disableAllHooks": True, "enabledPlugins": {"chat@market-a": True}}
        assert "--mcp-config" not in argv


def test_user_scope_server_gets_only_its_entry_in_a_private_temp_file(home, tmp_path):
    rec = Recorder("example_db")
    out = _draft("example-db", rec, tmp_path)
    assert out["status"] == "drafted", out
    assert len(rec.mcp_files) == 2  # both calls
    for argv in rec.calls:
        assert _settings(argv) == {"disableAllHooks": True}
        assert not any(SECRET in a for a in argv)
    for path, content, file_mode, dir_mode in rec.mcp_files:
        assert content == {"mcpServers": {"example-db": DB_ENTRY}}
        assert file_mode == 0o600 and dir_mode == 0o700
        assert not path.exists() and not path.parent.exists()


LOCAL_ENTRY = {"type": "http", "url": "https://example.test/mcp", "headers": {"Authorization": f"Bearer {SECRET}"}}


def _add_local_scope(home: Path, project: Path, servers: dict) -> None:
    data = json.loads((home / ".claude.json").read_text())
    data["projects"][str(project)] = {"mcpServers": servers}
    (home / ".claude.json").write_text(json.dumps(data))


def test_vault_local_scope_server_is_found_for_that_vault_only(home, tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    _add_local_scope(home, vault, {"knowledge": LOCAL_ENTRY})
    assert cd.user_mcp_server("knowledge", vault) == LOCAL_ENTRY
    assert cd.user_mcp_server("knowledge") is None
    assert cd.user_mcp_server("knowledge", tmp_path / "elsewhere") is None


def test_vault_local_scope_is_matched_by_resolved_path(home, tmp_path):
    real = tmp_path / "real-vault"
    real.mkdir()
    link = tmp_path / "Scout"
    link.symlink_to(real)
    _add_local_scope(home, real.resolve(), {"knowledge": LOCAL_ENTRY})
    assert cd.user_mcp_server("knowledge", link) == LOCAL_ENTRY


def test_user_scope_wins_over_vault_local_scope(home, tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    _add_local_scope(home, vault, {"example-db": LOCAL_ENTRY})
    assert cd.user_mcp_server("example-db", vault) == DB_ENTRY


def test_draft_passes_a_vault_local_server_in_its_private_temp_file(home, tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    _add_local_scope(home, vault, {"knowledge": LOCAL_ENTRY})
    rec = Recorder("knowledge")
    out = _draft("knowledge", rec, tmp_path)
    assert out["status"] == "drafted", out
    assert len(rec.mcp_files) == 2
    for argv in rec.calls:
        assert not any(SECRET in a for a in argv)
    for _path, content, file_mode, _dir_mode in rec.mcp_files:
        assert content == {"mcpServers": {"knowledge": LOCAL_ENTRY}}
        assert file_mode == 0o600


@pytest.mark.parametrize("project", ["not a mapping", {"mcpServers": ["knowledge"]}, {"mcpServers": {"knowledge": 1}}])
def test_odd_vault_local_entries_resolve_to_nothing(home, tmp_path, project):
    vault = tmp_path / "Scout"
    data = json.loads((home / ".claude.json").read_text())
    data["projects"][str(vault)] = project
    (home / ".claude.json").write_text(json.dumps(data))
    assert cd.user_mcp_server("knowledge", vault) is None


def test_claude_config_dir_overrides_home(home, tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "settings.json").write_text(json.dumps({"enabledPlugins": {"chat@market-c": True}}))
    (cfg / ".claude.json").write_text(json.dumps({"mcpServers": {}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    expected = {"disableAllHooks": True, "enabledPlugins": {"chat@market-c": True}}
    assert cd.session_settings("plugin:chat:chat") == expected
    assert cd.user_mcp_server("example-db") is None


@pytest.mark.parametrize("server", ["claude.ai Example Suite", "plugin:unknown:thing", "not-configured"])
def test_unresolved_server_runs_with_neither(home, tmp_path, server):
    rec = Recorder(server_slug(server))
    _draft(server, rec, tmp_path)
    for argv in rec.calls:
        assert _settings(argv) == {"disableAllHooks": True}
        assert "--mcp-config" not in argv


@pytest.mark.parametrize(
    "settings_text, config_text",
    [
        ("not json", "not json"),
        ("[]", "[]"),
        ('{"enabledPlugins": ["chat@market-a"]}', '{"mcpServers": ["example-db"]}'),
        ('{"enabledPlugins": {"chat@market-a": "yes"}}', '{"mcpServers": {"example-db": "x"}}'),
    ],
)
def test_unreadable_or_odd_config_resolves_to_nothing(home, settings_text, config_text):
    (home / ".claude" / "settings.json").write_text(settings_text)
    (home / ".claude.json").write_text(config_text)
    assert cd.session_settings("plugin:chat:chat") == {"disableAllHooks": True}
    assert cd.user_mcp_server("example-db") is None


def test_missing_config_files_resolve_to_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "empty"))
    assert cd.session_settings("plugin:chat:chat") == {"disableAllHooks": True}
    assert cd.user_mcp_server("example-db") is None


# --- the temp file is gone on every exit path ---------------------------------


def test_temp_file_removed_after_a_timeout(home, tmp_path):
    rec = Recorder("example_db", timeout=True)
    assert _draft("example-db", rec, tmp_path)["status"] == "timeout"
    path = rec.mcp_files[0][0]
    assert not path.exists() and not path.parent.exists()


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), RuntimeError("boom")])
def test_temp_file_removed_when_the_call_raises(home, tmp_path, exc):
    rec = Recorder("example_db", raise_on_call=exc)
    with pytest.raises(type(exc)):
        _draft("example-db", rec, tmp_path)
    path = rec.mcp_files[0][0]
    assert not path.exists() and not path.parent.exists()
