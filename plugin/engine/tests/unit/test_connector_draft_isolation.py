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
