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


def _decision(vault: Path, tool: str, tool_input: dict, *, cwd: Path | None = None, env: dict = RUN):
    body = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or vault), "session_id": "s1"}
    return gate.run(stdin=io.StringIO(json.dumps(body)), env=env)


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
        ".claude/commands/x.md",
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
    assert "kb-read-gate" in entry["hooks"][0]["command"]


def test_every_run_write_reaches_python_and_fails_closed(vault: Path, tmp_path: Path) -> None:
    """No path words in the shell: a symlink can hide them, so Python decides every run write."""
    payload = _payload("Write", _input("Write", "notes/a.md"), vault)
    res = _sh(
        {**RUN, "SCOUT_DATA_DIR": str(vault)}, payload, vault, tmp_path / "missing", "Write|Edit|MultiEdit|NotebookEdit"
    )
    assert res.returncode == 2 and gate.MARK in res.stderr


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
