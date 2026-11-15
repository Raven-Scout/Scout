"""kb-read-gate: a scheduled run can't write the files that control git's hooks or the agent's hooks.

#607 denied a run's *Bash* write into ``.git/hooks`` or ``.git/config``. A run's Write,
Edit, MultiEdit or NotebookEdit call went past it, and so did any write to the agent's
own settings, where ``"disableAllHooks": true`` turns every Scout gate off for later
runs (spec 2026-11-13 §8.5, plan Phase 1b). Owner sessions stay ungated.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from scout.hooks import kb_read_gate as gate
from scout.kb.hook_paths import protected_write

PLUGIN = Path(__file__).parent.parent.parent.parent
RUN = {"SCOUT_MODE": "morning-briefing", "SCOUT_READER": "autonomous"}
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    (h / ".claude" / "plugins").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    for var in ("XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
    return h.resolve()


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path) -> Path:
    v = tmp_path / "vault"
    (v / "knowledge-base").mkdir(parents=True)
    (v / "notes").mkdir()
    (v / ".git" / "hooks").mkdir(parents=True)
    (v / ".git" / "info").mkdir()
    (v / ".git" / "config").write_text("[core]\n")
    (v / ".claude").mkdir()
    (v / "scout-config.yaml").write_text("user: {}\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v.resolve()


def _input(tool: str, path: str) -> dict:
    if tool == "NotebookEdit":
        return {"notebook_path": path, "new_source": "x"}
    if tool == "Write":
        return {"file_path": path, "content": "x"}
    if tool == "MultiEdit":
        return {"file_path": path, "edits": [{"old_string": "a", "new_string": "b"}]}
    return {"file_path": path, "old_string": "a", "new_string": "b"}


def _decision(vault: Path, tool: str, tool_input: dict, *, cwd: Path | None = None, env: dict | None = None):
    """The hook's decision for a run (or ``env``): the process env as the hook sees it, plus the run's variables."""
    body = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or vault), "session_id": "s1"}
    base = {k: v for k, v in os.environ.items() if k not in ("SCOUT_MODE", "SCOUT_READER")}
    return gate.run(stdin=io.StringIO(json.dumps(body)), env={**base, **(RUN if env is None else env)})


def _denied(out) -> str:
    assert out is not None, "expected a deny"
    spec = out["hookSpecificOutput"]
    assert spec["permissionDecision"] == "deny"
    assert spec["permissionDecisionReason"].startswith(gate.MARK)
    return spec["permissionDecisionReason"]


def _events(vault: Path) -> list[dict]:
    rows: list[dict] = []
    for path in sorted((vault / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(line) for line in path.read_text().splitlines()]
    return rows


# --- what is protected ----------------------------------------------------------------


@pytest.mark.parametrize("tool", WRITE_TOOLS)
@pytest.mark.parametrize(
    ("path", "form"),
    [
        (".git/hooks/pre-commit", ".git/hooks"),
        (".git/hooks/new-hook", ".git/hooks"),
        (".git/config", ".git/config"),
        (".git/config.worktree", ".git/config"),
        (".git/info/attributes", ".git"),
        (".git/info/exclude", ".git"),
        (".git/HEAD", ".git"),
        (".claude/settings.json", "agent settings"),
        (".claude/settings.local.json", "agent settings"),
        (".codex/hooks.json", "agent settings"),
        (".gemini/settings.json", "agent settings"),
        (".agents/hooks.json", "agent settings"),
        (".grok/hooks/gate.json", "agent settings"),
        (".cursor/hooks.json", "agent settings"),
    ],
)
def test_a_run_cant_write_hook_or_agent_settings_in_the_vault(vault: Path, tool: str, path: str, form: str) -> None:
    reason = _denied(_decision(vault, tool, _input(tool, path)))
    assert form in reason
    # git's hooks: commit the normal way; the agent's settings: leave them for the owner.
    assert ("git-safe-commit.sh" if form.startswith(".git") else "run notes for the owner") in reason
    (row,) = _events(vault)
    assert row["kind"] == "kb_read_gate.hook_skip"
    assert row["payload"] == {"form": form, "mode": RUN["SCOUT_MODE"]}


@pytest.mark.parametrize(
    ("rel", "form"),
    [
        (".gitconfig", "git config"),
        (".config/git/config", "git config"),
        (".config/git/attributes", "git config"),
        (".claude/settings.json", "agent settings"),
        (".claude/settings.local.json", "agent settings"),
        (".claude/plugins/installed_plugins.json", "agent settings"),
        (".codex/config.toml", "agent settings"),
        (".gemini/settings.json", "agent settings"),
    ],
)
def test_a_run_cant_write_the_users_git_or_agent_settings(vault: Path, home: Path, rel: str, form: str) -> None:
    for path in (f"~/{rel}", str(home / rel)):
        assert form in _denied(_decision(vault, "Write", _input("Write", path)))


def test_global_config_locations_from_the_environment(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gc"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert "git config" in _denied(_decision(vault, "Write", _input("Write", str(tmp_path / "gc"))))
    assert "git config" in _denied(_decision(vault, "Write", _input("Write", str(tmp_path / "xdg/git/config"))))
    assert "agent settings" in _denied(_decision(vault, "Edit", _input("Edit", str(tmp_path / "cc/settings.json"))))
    assert "agent settings" in _denied(_decision(vault, "Edit", _input("Edit", str(tmp_path / "cc/plugins/x.json"))))


# --- spelling: relative, absolute, .., case, symlinks ----------------------------------


@pytest.mark.parametrize(
    "spelling",
    [
        "notes/../.git/hooks/pre-commit",
        "./.git/hooks/pre-commit",
        ".git//hooks/pre-commit",
        "{vault}/.git/hooks/pre-commit",
        "{vault}/notes/../.git/hooks/pre-commit",
        ".GIT/hooks/pre-commit",
        ".git/HOOKS/pre-commit",
        ".Claude/Settings.json",
    ],
)
def test_every_spelling_of_a_protected_path_is_denied(vault: Path, spelling: str) -> None:
    _denied(_decision(vault, "Write", _input("Write", spelling.format(vault=vault))))


def test_a_relative_path_is_read_against_the_sessions_cwd(vault: Path) -> None:
    _denied(_decision(vault, "Write", _input("Write", "../.git/hooks/pre-commit"), cwd=vault / "notes"))


def test_a_symlinked_folder_into_git_hooks_is_denied(vault: Path) -> None:
    (vault / "notes" / "link").symlink_to(vault / ".git" / "hooks")
    assert ".git/hooks" in _denied(_decision(vault, "Write", _input("Write", "notes/link/pre-commit")))


def test_a_symlinked_file_onto_a_hook_is_denied(vault: Path) -> None:
    (vault / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n")
    (vault / "notes" / "harmless.md").symlink_to(vault / ".git" / "hooks" / "pre-commit")
    assert ".git/hooks" in _denied(_decision(vault, "Edit", _input("Edit", "notes/harmless.md")))


def test_a_symlinked_file_onto_claude_settings_is_denied(vault: Path) -> None:
    (vault / ".claude" / "settings.local.json").write_text("{}\n")
    (vault / "notes" / "cfg.json").symlink_to(vault / ".claude" / "settings.local.json")
    assert "agent settings" in _denied(_decision(vault, "Write", _input("Write", "notes/cfg.json")))


def test_a_worktree_vaults_real_git_dir_is_protected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    common = tmp_path / "main-repo" / ".git"
    gitdir = common / "worktrees" / "vault"
    (gitdir).mkdir(parents=True)
    (common / "hooks").mkdir()
    (gitdir / "commondir").write_text("../..\n")
    v = tmp_path / "wt-vault"
    v.mkdir()
    (v / ".git").write_text(f"gitdir: {gitdir}\n")
    (v / "scout-config.yaml").write_text("user: {}\n")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    assert ".git/hooks" in _denied(_decision(v, "Write", _input("Write", str(common / "hooks" / "pre-commit"))))
    assert ".git/config" in _denied(_decision(v, "Write", _input("Write", str(gitdir / "config.worktree"))))
    assert ".git" in _denied(_decision(v, "Write", _input("Write", ".git")))  # repointing the gitdir file


# --- what stays allowed ----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "notes/a.md",
        "knowledge-base/people.md",
        "action-items/action-items-2026-11-13.md",
        "drafts/reply.md",
        ".github/workflows/ci.yml",
        "notes/my.gitconfig.md",
        "notes/.git-notes.md",
        "~/.claude/projects/x/notes.md",
        "/tmp/scratch.txt",
    ],
)
def test_ordinary_writes_pass(vault: Path, path: str) -> None:
    assert _decision(vault, "Write", _input("Write", path)) is None
    assert _events(vault) == []


def test_the_owners_session_is_never_gated(vault: Path) -> None:
    for env in ({}, {"SCOUT_READER": "owner"}):
        assert _decision(vault, "Write", _input("Write", ".git/hooks/pre-commit"), env=env) is None


def test_a_delegate_session_is_gated(vault: Path) -> None:
    _denied(_decision(vault, "Edit", _input("Edit", ".claude/settings.json"), env={"SCOUT_READER": "delegate:duet"}))


def test_a_write_without_a_path_fails_closed(vault: Path) -> None:
    assert "could not check" in _denied(_decision(vault, "Write", {"content": "x"}))


def test_protected_write_outside_any_vault_still_guards_the_user_config(home: Path) -> None:
    assert protected_write(home / ".gitconfig", vault=None) == "git config"
    assert protected_write(home / "notes.md", vault=None) is None


# --- Bash writes to the agent's settings (the #607 Bash rule, extended) ----------------


@pytest.mark.parametrize(
    ("command", "form"),
    [
        ("echo '{\"disableAllHooks\": true}' > .claude/settings.local.json", "agent settings"),
        ("cp /tmp/x .claude/settings.json", "agent settings"),
        ("rm ~/.claude/settings.json", "agent settings"),
        ("tee ~/.gitconfig < /tmp/x", "git config"),
        ("echo x >> ~/.config/git/config", "git config"),
        ("mv /tmp/h .codex/hooks.json", "agent settings"),
    ],
)
def test_a_run_cant_write_agent_settings_from_the_shell(vault: Path, command: str, form: str) -> None:
    assert form in _denied(_decision(vault, "Bash", {"command": command}))


@pytest.mark.parametrize(
    "command",
    ["cat .claude/settings.json", "grep hooks ~/.claude/settings.json", "git config --get user.name", "ls ~/.codex"],
)
def test_reading_agent_settings_from_the_shell_passes(vault: Path, command: str) -> None:
    assert _decision(vault, "Bash", {"command": command}) is None


# --- hooks.json -------------------------------------------------------------------------


def _entry(matcher: str) -> dict:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    (entry,) = [e for e in hooks if e["matcher"] == matcher]
    return entry


def _sh(env: dict, payload: str, cwd: Path, root: Path, matcher: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if k not in ("SCOUT_MODE", "SCOUT_READER", "SCOUT_DATA_DIR")}
    cmd = _entry(matcher)["hooks"][0]["command"]
    return subprocess.run(
        ["sh", "-c", cmd],
        env={**clean, **env, "CLAUDE_PLUGIN_ROOT": str(root)},
        cwd=cwd,
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _payload(tool: str, tool_input: dict, cwd: Path) -> str:
    return json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd), "session_id": "s1"})


def test_write_tools_have_their_own_exact_matcher() -> None:
    entry = _entry("Write|Edit|MultiEdit|NotebookEdit")
    assert "hook kb-write-gate" in entry["hooks"][0]["command"]


def test_every_run_write_reaches_python_and_fails_closed(vault: Path, tmp_path: Path) -> None:
    """No path words in the shell: a symlink can hide them, so Python decides every run write."""
    payload = _payload("Write", _input("Write", "notes/a.md"), vault)
    res = _sh(
        {**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, tmp_path / "missing", "Write|Edit|MultiEdit|NotebookEdit"
    )
    assert res.returncode == 2 and "[scout:kb-write-gate]" in res.stderr


def test_an_owner_write_never_starts_python(vault: Path, tmp_path: Path) -> None:
    payload = _payload("Write", _input("Write", ".git/hooks/pre-commit"), vault)
    res = _sh({}, payload, vault, tmp_path / "missing", "Write|Edit|MultiEdit|NotebookEdit")
    assert res.returncode == 0


def test_the_real_hook_denies_a_run_write_into_git_hooks(vault: Path) -> None:
    payload = _payload("Write", _input("Write", ".git/hooks/pre-commit"), vault)
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, PLUGIN, "Write|Edit|MultiEdit|NotebookEdit")
    assert res.returncode == 0
    assert json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    ["echo x > .claude/settings.local.json", "cp x ~/.gitconfig", "rm ~/.config/git/config", "mv a .codex/hooks.json"],
)
def test_a_shell_write_to_agent_settings_reaches_python(vault: Path, tmp_path: Path, command: str) -> None:
    payload = _payload("Bash", {"command": command}, vault)
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, tmp_path / "missing", "Grep|Glob|Bash")
    assert res.returncode == 2, command


# --- review of #637 ----------------------------------------------------------------------------------


def _bash(vault: Path, command: str, *, cwd: Path | None = None):
    return _decision(vault, "Bash", {"command": command}, cwd=cwd)


def test_a_fifo_where_the_audit_log_belongs_cant_hold_the_deny_back(vault: Path) -> None:
    """Must-fix 1: the deny goes out first, and the audit never blocks (a hook that times out is an allow)."""
    import datetime as dt
    import sys
    import time

    logs = vault / ".scout-logs"
    logs.mkdir(exist_ok=True)
    os.mkfifo(logs / f"schedule-events-{dt.datetime.now(dt.UTC).date().isoformat()}.jsonl")
    payload = _payload("Write", _input("Write", ".claude/settings.local.json"), vault)
    env = {**os.environ, **RUN, "SCOUT_DATA_DIR": str(vault), "HOME": os.environ["HOME"]}
    started = time.monotonic()
    out = subprocess.run(
        [sys.executable, "-m", "scout.hooks", "kb-write-gate"],
        input=payload, capture_output=True, text=True, env=env, timeout=20,
    )  # fmt: skip
    assert time.monotonic() - started < 10
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_event_writer_refuses_a_fifo_without_blocking(tmp_path: Path) -> None:
    from scout.events import append_event

    log = tmp_path / "logs"
    log.mkdir()
    import datetime as dt

    os.mkfifo(log / f"schedule-events-{dt.datetime.now(dt.UTC).date().isoformat()}.jsonl")
    with pytest.raises(OSError):
        append_event(log, kind="k", source="s", payload={})


def test_the_engine_the_hooks_run_is_protected(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Must-fix 2: the interpreter cache, the engine's own code, its venv, the pointer, $CLAUDE_PLUGIN_ROOT."""
    import sys

    import scout
    from scout.hooks import kb_read_gate

    plugin_root = tmp_path / "installed-plugin"
    (plugin_root / "engine").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
    engine_plugin = Path(scout.__file__).resolve().parents[2]
    for path in (
        plugin_root / ".scoutctl-py-cache",
        engine_plugin / ".scoutctl-py-cache",
        Path(kb_read_gate.__file__),
        Path(scout.__file__).resolve().parent / "kb" / "hook_paths.py",
        engine_plugin / "hooks" / "hooks.json",
        Path(sys.prefix) / "bin" / "python",
        Path(os.environ["HOME"]) / ".local" / "state" / "scout" / "engine.json",
    ):
        assert "Scout's engine" in _denied(_decision(vault, "Write", _input("Write", str(path)))), path


def test_the_plugin_the_pre_commit_hook_calls_is_protected(vault: Path, tmp_path: Path) -> None:
    other = tmp_path / "other-plugin"
    (vault / ".git" / "hooks" / "pre-commit").write_text(f"#!/bin/sh\nSCOUTCTL={other}/engine/bin/scoutctl\n")
    assert "Scout's engine" in _denied(
        _decision(vault, "Edit", _input("Edit", str(other / "engine" / "scout" / "x.py")))
    )


@pytest.mark.parametrize(
    "rel", ["scripts/claude-with-retry.sh", "run-scout.sh", "scripts/scoutctl", "scripts/git-safe-commit.sh"]
)
def test_the_runner_scripts_are_protected(vault: Path, rel: str) -> None:
    """Must-fix 3: `unset SCOUT_MODE` in a runner script would make the next run an owner session."""
    assert "runner scripts" in _denied(_decision(vault, "Edit", _input("Edit", rel)))
    assert "runner scripts" in _denied(_bash(vault, f"sed -i '' 's/x/y/' {rel}"))


def test_files_runs_may_grow_stay_writable(vault: Path) -> None:
    assert _decision(vault, "Edit", _input("Edit", "knowledge-base/ontology/parser.py")) is None
    assert _decision(vault, "Write", _input("Write", "scripts/my-own-helper.py")) is None


def test_the_runner_file_list_matches_bootstrap() -> None:
    from scout.kb.hook_paths import RUNNER_FILES
    from scout.scripts import bootstrap

    expected = {dest for dest, _ in bootstrap._CAT1_TEMPLATES if dest not in bootstrap._CAT1_APPEND_ONLY}
    expected |= {dest for dest, _ in bootstrap._CAT1B_RUNNERS}
    expected |= {dest for dest in bootstrap._CAT1_FILES_FROM_PLUGIN if dest.startswith("scripts/")}
    assert set(RUNNER_FILES) == expected


@pytest.mark.parametrize(
    "command",
    [
        'echo x > .claude/"settings.local.json"',
        "echo x > .claude/set''tings.local.json",
        "echo x >> ~/.git''config",
        "cp a .Claude/Settings.json",
    ],
)
def test_quoting_cant_hide_a_write_from_the_shell_prefilter(vault: Path, tmp_path: Path, command: str) -> None:
    payload = _payload("Bash", {"command": command}, vault)
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, tmp_path / "missing", "Grep|Glob|Bash")
    assert res.returncode == 2, command
    assert _bash(vault, command) is not None, command


def test_symlink_then_write_is_denied_at_both_steps(vault: Path) -> None:
    """Should-fix 5a: `ln -s` a protected file, then write through the link in another command."""
    assert "agent settings" in _denied(_bash(vault, "ln -s ../.claude/settings.local.json notes/s.json"))
    (vault / ".claude" / "settings.local.json").write_text("{}\n")
    (vault / "notes" / "s.json").symlink_to(vault / ".claude" / "settings.local.json")
    assert "agent settings" in _denied(_bash(vault, "echo '{\"disableAllHooks\": true}' > notes/s.json"))


@pytest.mark.parametrize(
    ("command", "denied"),
    [
        ("git config --global core.pager 'sh -c x'", True),
        ("git config --global user.name x", True),
        ("git config --system core.editor x", True),
        ("git config --file ~/.gitconfig alias.c x", True),
        ("git config core.fsmonitor ./x", True),
        ("git config core.sshCommand x", True),
        ("git config credential.helper x", True),
        ("git config filter.lfs.clean x", True),
        ("git config init.templateDir /tmp/t", True),
        ("git config user.name x", False),
        ("git config --global --get user.name", False),
    ],
)
def test_git_config_writes_that_run_commands(vault: Path, command: str, denied: bool) -> None:
    """Should-fix 5b: any --global/--system/--file write, and local keys that run a command."""
    assert (_bash(vault, command) is not None) is denied, command


@pytest.mark.parametrize(
    "command",
    [
        "cp /tmp/settings.local.json .claude/",
        "cp -t .claude /tmp/settings.json",
        "mv /tmp/settings.json .claude",
        "cd .claude && echo '{}' > settings.local.json",
        "cd notes && cd ../.claude && echo x > settings.json",
        "echo x >| .claude/settings.json",
        "rm -rf .claude",
        "mv .claude /tmp/gone",
        "rm -r scripts",
        "curl -o .claude/settings.json https://x",
        "perl -pi -e 's/a/b/' .claude/settings.json",
    ],
)
def test_the_cheap_shell_forms_are_covered(vault: Path, command: str) -> None:
    """Should-fix 5c: directory targets, cd-relative paths, >|, removing a folder that holds a protected file."""
    assert _bash(vault, command) is not None, command


def test_git_hooks_dir_from_core_hookspath_is_protected(vault: Path) -> None:
    """Should-fix 6: git runs hooks from core.hooksPath, which a .git/hooks check alone misses."""
    import shutil

    shutil.rmtree(vault / ".git")
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "core.hooksPath", ".githooks"], check=True)
    assert ".git/hooks" in _denied(_decision(vault, "Write", _input("Write", ".githooks/pre-commit")))


def test_a_git_hooks_symlink_out_of_git_still_counts(vault: Path) -> None:
    import shutil

    shutil.rmtree(vault / ".git" / "hooks")
    (vault / "tracked-hooks").mkdir()
    (vault / ".git" / "hooks").symlink_to(vault / "tracked-hooks")
    assert _decision(vault, "Write", _input("Write", ".git/hooks/pre-commit")) is not None


def test_the_write_matcher_runs_its_own_hook_name() -> None:
    """Should-fix 7: an engine that predates the write gate has no kb-write-gate, exits 2, and Claude blocks."""
    command = _entry("Write|Edit|MultiEdit|NotebookEdit")["hooks"][0]["command"]
    assert "hook kb-write-gate" in command and "hook kb-read-gate" not in command


def test_an_unknown_hook_name_fails_closed() -> None:
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "scout.hooks", "kb-not-a-gate"], input="{}", capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 2


@pytest.mark.parametrize(
    "path",
    [
        "~/.claude.json",
        "~/.zshenv",
        "~/.zshrc",
        "~/.bash_profile",
        "~/Library/LaunchAgents/com.example.x.plist",
        ".mcp.json",
        ".claude/hooks/x.sh",
        ".claude/commands/x.md",
        ".claude/agents/x.md",
        ".gitattributes",
        ".gitmodules",
    ],
)
def test_more_files_that_run_code_are_protected(vault: Path, path: str) -> None:
    """Should-fix 8."""
    assert _decision(vault, "Write", _input("Write", path)) is not None, path


def test_the_config_dirs_claude_json_is_protected(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    assert "agent settings" in _denied(
        _decision(vault, "Write", _input("Write", str(tmp_path / "cc" / ".claude.json")))
    )


def test_unicode_spellings_are_one_path(vault: Path) -> None:
    """Nit 9: NFC-normalised, so a decomposed spelling of a protected name is the same name."""
    from scout.kb.hook_paths import canon

    assert canon("/tmp/café") == canon("/tmp/café")


def test_a_hard_link_to_a_protected_file_is_denied(vault: Path) -> None:
    (vault / ".claude" / "settings.local.json").write_text("{}\n")
    os.link(vault / ".claude" / "settings.local.json", vault / "notes" / "h.json")
    assert _decision(vault, "Write", _input("Write", "notes/h.json")) is not None


# --- re-review of #637 --------------------------------------------------------------------------------


def test_the_watchdog_fires_before_every_gate_matchers_timeout() -> None:
    """Must-fix 1(b): a hook that times out is an allow, so the gate must give up (deny) first."""
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
    timeouts = [
        h["timeout"]
        for e in hooks
        for h in e["hooks"]
        if "kb-read-gate" in h["command"] or "kb-write-gate" in h["command"]
    ]
    assert len(timeouts) == 3
    assert gate.WATCHDOG_S < min(timeouts)


def test_main_disarms_its_watchdog_on_the_way_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller in the same process (a test) must not be killed seconds later by a leftover alarm."""
    import signal

    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    gate.main()
    assert signal.alarm(0) == 0


def test_a_gate_that_hangs_is_cut_off_with_a_block(vault: Path) -> None:
    import sys
    import time

    script = (
        "import time, scout.hooks.kb_read_gate as g\ng.WATCHDOG_S = 1\ng.run = lambda **k: time.sleep(30)\ng.main()\n"
    )
    started = time.monotonic()
    out = subprocess.run([sys.executable, "-c", script], input="{}", capture_output=True, text=True, timeout=20)
    assert time.monotonic() - started < 5
    assert out.returncode == 2 and gate.MARK in out.stderr


def test_hundreds_of_paths_and_a_protected_write_is_denied_fast(vault: Path) -> None:
    """Must-fix 1(a)/(c): the reviewer's 450-path rm then a disableAllHooks write took 17.6 s and was allowed."""
    import time

    many = " ".join(f"notes/f{i}" for i in range(450))
    started = time.monotonic()
    out = _bash(vault, f"rm -f {many}; echo '{{\"disableAllHooks\":true}}' > .claude/settings.local.json")
    assert out is not None
    assert time.monotonic() - started < 5


def test_a_command_under_the_cap_is_checked_path_by_path_and_quickly(vault: Path) -> None:
    import time

    some = " ".join(f"notes/f{i}" for i in range(200))
    started = time.monotonic()
    assert _bash(vault, f"rm -f {some}") is None
    assert "agent settings" in _denied(_bash(vault, f"rm -f {some} .claude/settings.json"))
    assert time.monotonic() - started < 5


def test_a_command_over_the_cap_is_denied_as_too_complex(vault: Path) -> None:
    from scout.kb.live_run import MAX_CHECKED_PATHS

    many = " ".join(f"notes/f{i}" for i in range(MAX_CHECKED_PATHS + 5))
    assert "too many paths" in _denied(_bash(vault, f"rm -f {many}"))


def test_an_inline_program_naming_the_engine_cache_is_denied(vault: Path) -> None:
    """Must-fix 2(b): `python3 -c "open('<plugin>/.scoutctl-py-cache','w')…"` poisoned every gate."""
    import scout

    cache = Path(scout.__file__).resolve().parents[2] / ".scoutctl-py-cache"
    command = f"""python3 -c "open('{cache}','w').write('/usr/bin/true\\\\n/etc/hosts\\\\n')\""""
    assert "Scout's engine" in _denied(_bash(vault, command))


@pytest.mark.parametrize(
    "command",
    [
        "python3 - <<'EOF'\nopen('.claude/settings.local.json', 'w').write('{}')\nEOF",
        'perl -e \'open(F, ">", "$ENV{HOME}/.gitconfig")\' ; perl -e \'open(F, ">", ".git/hooks/pre-commit")\'',
        "node -e \"require('fs').writeFileSync('.claude/settings.json', '{}')\"",
        "ruby -e \"File.write('scripts/claude-with-retry.sh', 'x')\"",
    ],
)
def test_inline_programs_that_name_a_protected_path_are_denied(vault: Path, command: str) -> None:
    assert _bash(vault, command) is not None, command


@pytest.mark.parametrize(
    "command",
    [
        "python3 - <<'EOF'\nimport json\nprint(json.load(open('.scout-cache/session-context.json')))\nEOF",
        "python3 -c \"print(open('notes/a.md').read())\"",
        "python3 scripts/my-own-helper.py notes/a.md",
    ],
)
def test_ordinary_inline_programs_pass(vault: Path, command: str) -> None:
    assert _bash(vault, command) is None, command


def test_a_glob_is_judged_by_what_it_matches(vault: Path) -> None:
    """Should-fix 3: `> .claude/settings.local.j*` wrote the file."""
    (vault / ".claude" / "settings.local.json").write_text("{}\n")
    assert "agent settings" in _denied(_bash(vault, "echo '{\"disableAllHooks\":true}' > .claude/settings.local.j*"))


@pytest.mark.parametrize(
    "command",
    [
        "d=.claude; echo x > $d/settings.local.json",
        "d=.claude && echo x > ${d}/settings.local.json",
        "echo x > $SOMEWHERE/settings.local.json",
        "echo x >> $HOME/.gitconfig",
    ],
)
def test_variables_in_a_written_path_are_resolved_or_fail_closed(vault: Path, command: str) -> None:
    assert _bash(vault, command) is not None, command


def test_a_variable_path_that_cant_name_a_protected_file_passes(vault: Path) -> None:
    assert _bash(vault, "echo x > $TMPDIR/scout-out.txt") is None


def test_the_write_hooks_fallback_names_its_own_gate() -> None:
    command = _entry("Write|Edit|MultiEdit|NotebookEdit")["hooks"][0]["command"]
    assert "[scout:kb-write-gate]" in command


@pytest.mark.parametrize(
    "command", ["python3 -c \"open('x').read()\"", "node -e '1'", "python3 - <<'EOF'\nprint(1)\nEOF"]
)
def test_inline_programs_reach_python_from_the_shell(vault: Path, tmp_path: Path, command: str) -> None:
    payload = _payload("Bash", {"command": command}, vault)
    res = _sh({**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, tmp_path / "missing", "Grep|Glob|Bash")
    assert res.returncode == 2, command
