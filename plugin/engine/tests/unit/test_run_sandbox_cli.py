"""`scoutctl sandbox …`, the self-test's verdicts and the replay's edge cases, on any platform (spec 2026-11-15).

The kernel itself is exercised on macOS only (tests/integration/test_run_sandbox_kernel.py); here
sandbox-exec is replaced by a fake subprocess, so the decisions around it are covered on Linux too.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.kb import hook_paths
from scout.sandbox import launch, replay
from scout.sandbox import profile as sbx
from scout.scripts import bootstrap_doctor

runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    subprocess.run(["git", "init", "-q", str(v)], check=True)
    hook_paths._ROOTS_CACHE.clear()
    return v


# -- the CLI ------------------------------------------------------------------------------------------------------


def test_profile_prints_the_profile_and_exits_with_the_status(vault: Path, monkeypatch) -> None:
    monkeypatch.setattr(launch, "prepare", lambda _v, *_a, **_k: launch.Prepared("enforced", "ok", "(version 1)\n"))
    result = runner.invoke(app, ["sandbox", "profile", "--vault", str(vault)])
    assert result.exit_code == 0
    assert result.stdout.startswith("(version 1)")


@pytest.mark.parametrize(("status", "code"), [("failed", 3), ("unavailable", 6), ("off", 7), ("conflict", 8)])
def test_profile_prints_nothing_when_not_enforced(vault: Path, monkeypatch, status: str, code: int) -> None:
    monkeypatch.setattr(launch, "prepare", lambda _v, *_a, **_k: launch.Prepared(status, "why"))
    result = runner.invoke(app, ["sandbox", "profile", "--vault", str(vault)])
    assert result.exit_code == code
    assert "(version 1)" not in result.stdout
    assert f"[scout:sandbox] {status}: why" in result.output


def test_status_says_why(vault: Path, monkeypatch) -> None:
    monkeypatch.setattr(launch, "prepare", lambda _v, *_a, **_k: launch.Prepared("unavailable", "no sandbox"))
    assert runner.invoke(app, ["sandbox", "status", "--vault", str(vault)]).stdout.strip() == (
        "run sandbox: unavailable (no sandbox)"
    )
    out = runner.invoke(app, ["sandbox", "status", "--vault", str(vault), "--json"]).stdout
    assert json.loads(out) == {"status": "unavailable", "reason": "no sandbox"}


def test_off_and_on_write_the_owners_switch(monkeypatch) -> None:
    monkeypatch.delenv("SCOUT_RUN_SANDBOX", raising=False)
    assert "off" in runner.invoke(app, ["sandbox", "off"]).stdout
    assert launch.switch()[0] is False
    assert "on" in runner.invoke(app, ["sandbox", "on"]).stdout
    assert launch.switch()[0] is True


def test_replay_prints_counts(vault: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "s.jsonl").write_text(json.dumps({"entrypoint": "sdk-cli"}) + "\n")
    text = runner.invoke(app, ["sandbox", "replay", "--transcripts", str(project), "--vault", str(vault)]).stdout
    assert text.startswith("sessions: 1, tool calls: 0")
    raw = runner.invoke(app, ["sandbox", "replay", "--transcripts", str(project), "--vault", str(vault), "--json"])
    assert json.loads(raw.stdout)["sessions"] == 1


# -- the self-test, with sandbox-exec faked -----------------------------------------------------------------------


def _fake_sandbox_exec(monkeypatch, *, rc: int, write_canary: bool = False, write_ordinary: bool = True) -> None:
    def run(argv, **_kw):
        canary, ordinary = argv[-2], argv[-1]
        if write_canary:
            Path(canary).write_text("x")
        if write_ordinary:
            Path(ordinary).write_text("y")
        return subprocess.CompletedProcess(argv, rc, "", "boom")

    monkeypatch.setattr(sbx.subprocess, "run", run)


def test_self_test_passes_when_the_canary_is_refused(monkeypatch) -> None:
    _fake_sandbox_exec(monkeypatch, rc=0)
    assert sbx.self_test("(version 1)") is None


def test_self_test_fails_when_the_canary_gets_through(monkeypatch) -> None:
    _fake_sandbox_exec(monkeypatch, rc=9, write_canary=True)
    assert "let through" in (sbx.self_test("(version 1)") or "")


def test_self_test_fails_when_an_ordinary_write_is_refused(monkeypatch) -> None:
    _fake_sandbox_exec(monkeypatch, rc=8, write_ordinary=False)
    assert "ordinary" in (sbx.self_test("(version 1)") or "")


def test_self_test_fails_on_any_other_exit(monkeypatch) -> None:
    _fake_sandbox_exec(monkeypatch, rc=71)
    assert "exit 71" in (sbx.self_test("(version 1)") or "")


def test_self_test_fails_when_sandbox_exec_wont_run(monkeypatch) -> None:
    def run(*_a, **_k):
        raise OSError("no such file")

    monkeypatch.setattr(sbx.subprocess, "run", run)
    assert "didn't run" in (sbx.self_test("(version 1)") or "")


def test_available_is_false_off_macos(monkeypatch) -> None:
    monkeypatch.setattr(sbx.sys, "platform", "linux")
    assert sbx.available() is False


def test_unavailable_on_a_mac_without_sandbox_exec(vault: Path) -> None:
    env = {**os.environ}
    env.pop("SCOUT_RUN_SANDBOX", None)
    result = launch.prepare(vault, env, available=lambda: False, platform="darwin")
    assert (result.status, result.reason) == ("unavailable", "no sandbox-exec")


# -- profile edges -------------------------------------------------------------------------------------------------


def test_claude_config_dir_gets_its_own_rules(vault: Path, tmp_path: Path) -> None:
    other = tmp_path / "claude-config"
    env = {**os.environ, "HOME": str(tmp_path / "home"), "CLAUDE_CONFIG_DIR": str(other)}
    rules = sbx.build_rules(vault, env)
    assert sbx.decide(rules, str(os.path.realpath(other) + "/settings.json")) == "deny"
    assert sbx.decide(rules, str(os.path.realpath(other) + "/plugins/data/p/x")) == "allow"


def test_a_profile_without_a_vault_still_protects_home(tmp_path: Path) -> None:
    env = {**os.environ, "HOME": str(tmp_path)}
    rules = sbx.build_rules(None, env)
    assert sbx.decide(rules, os.path.realpath(tmp_path) + "/.claude/settings.json") == "deny"


def test_a_network_block_is_appended_last(vault: Path) -> None:
    text = sbx.build_profile(vault, network="(deny network-outbound)")
    assert text.rstrip().endswith("(deny network-outbound)")


def test_a_quote_in_a_git_dir_path_fails_the_build() -> None:
    with pytest.raises(sbx.ProfileError):
        sbx._rx_escape('/a/"b/.git')


def test_rules_serialise(vault: Path) -> None:
    data = json.loads(sbx.to_json(sbx.build_rules(vault)))
    assert {"action", "op", "kind", "path"} <= set(data[0])


def test_regex_rules_render_and_match(vault: Path) -> None:
    rules = sbx.build_rules(vault)
    rx = [r for r in rules if r.kind == "regex"]
    assert rx and all(sbx.render([r]).count('(regex #"^') == 1 for r in rx)
    git = os.path.realpath(vault / ".git")
    assert sbx._matches(rx[0], f"{git}/next-index-12.lock") or sbx._matches(rx[1], f"{git}/index.stash.12")


# -- hook_paths edges the profile depends on ------------------------------------------------------------------


def test_a_worktree_protects_its_gitdir_and_common_dir(tmp_path: Path) -> None:
    common = tmp_path / "main" / ".git"
    gitdir = common / "worktrees" / "w"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n")
    assert hook_paths.git_dirs(wt) == [gitdir, gitdir / "../.."]


def test_a_dot_git_file_that_isnt_a_gitdir_pointer_adds_nothing(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("nonsense\n")
    assert hook_paths.git_dirs(tmp_path) == []


def test_an_unreadable_pointer_adds_no_venv(tmp_path: Path) -> None:
    bad = tmp_path / "engine.json"
    bad.write_text("{not json")
    assert hook_paths._pointer_venv(bad) is None
    assert hook_paths._pointer_venv(tmp_path / "missing.json") is None
    bad.write_text(json.dumps({"python": "relative/bin/python"}))
    assert hook_paths._pointer_venv(bad) is None
    bad.write_text(json.dumps(["python"]))
    assert hook_paths._pointer_venv(bad) is None


def test_a_worktree_without_a_commondir_is_its_gitdir_only(tmp_path: Path) -> None:
    gitdir = tmp_path / "gd"
    gitdir.mkdir()
    (tmp_path / ".git").write_text("gitdir: gd\n")
    assert hook_paths.git_dirs(tmp_path) == [tmp_path / "gd"]


def test_an_empty_dot_git_file_adds_nothing(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("")
    assert hook_paths.git_dirs(tmp_path) == []


def test_no_git_means_no_hooks_dir(tmp_path: Path, monkeypatch) -> None:
    def run(*_a, **_k):
        raise OSError("no git")

    monkeypatch.setattr(hook_paths.subprocess, "run", run)
    assert hook_paths.git_hooks_dir(tmp_path) is None


# -- doctor ------------------------------------------------------------------------------------------------------


def test_doctor_survives_a_broken_check(tmp_path: Path, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.delenv("SCOUT_SANDBOX_STATUS", raising=False)
    monkeypatch.setattr(launch, "prepare", boom)
    warnings, notes = bootstrap_doctor._check_run_sandbox(vault=tmp_path)
    assert notes == [] and "couldn't check" in warnings[0]


# -- replay edges (synthetic transcripts only) ---------------------------------------------------------------


def _row(**kw: object) -> str:
    return json.dumps(kw)


def test_replay_reads_only_scheduled_sessions_up_to_the_limit(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    for i in range(3):
        (project / f"s{i}.jsonl").write_text(_row(entrypoint="sdk-cli") + "\n")
    (project / "noentry.jsonl").write_text("not json\n" + _row(type="user") + "\n")
    (project / "long.jsonl").write_text("\n".join(_row(type="user") for _ in range(60)) + "\n")
    assert len(replay.scheduled_sessions(project, 2)) == 2
    assert replay._entrypoint(project / "noentry.jsonl") is None
    assert replay._entrypoint(project / "long.jsonl") is None
    assert replay._entrypoint(project / "gone.jsonl") is None


def test_replay_skips_malformed_rows_and_blocks(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        "\n".join(
            [
                "garbage",
                _row(type="assistant", message={"content": "text only"}),
                _row(type="assistant", message={"content": ["str", {"type": "text"}]}),
                _row(type="assistant", message={"content": [{"type": "tool_use", "name": "Bash", "input": "x"}]}),
            ]
        )
        + "\n"
    )
    assert list(replay.tool_calls(path)) == [("Bash", {})]


def test_replay_resolves_home_and_leaves_run_time_paths_unresolved(tmp_path: Path) -> None:
    home = tmp_path / "h"
    assert replay._resolve("$HOME/x", tmp_path, home) == os.path.realpath(home / "x")
    assert replay._resolve("${HOME}/x", tmp_path, home) == os.path.realpath(home / "x")
    assert replay._resolve("~/x", tmp_path, home) == os.path.realpath(home / "x")
    assert replay._resolve("$T/x", tmp_path, home) is None
    assert replay._resolve("", tmp_path, home) is None


def test_replay_names_sandbox_only_forms(vault: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(vault.parent / "home"))
    hook_paths._ROOTS_CACHE.clear()
    rules = sbx.build_rules(vault)
    assert replay._form(os.path.realpath(vault), vault, rules) == "a folder holding a protected path"
    secret = os.path.realpath(vault.parent / "home" / ".scout-secrets") + "/k"
    assert replay._form(secret, vault, rules) == "Scout's secrets"
    assert replay._form("/usr/lib/cron/tabs/x", vault, rules) == "sandbox-only rule"


def test_replay_counts_unparsable_unresolved_and_kernel_only(vault: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    hook_paths._ROOTS_CACHE.clear()
    project = tmp_path / "p"
    project.mkdir()
    calls = [
        {"type": "tool_use", "name": "Bash", "input": {"command": "echo x > $UNKNOWN/f"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "echo x > /usr/lib/cron/tabs/me"}},
        {"type": "tool_use", "name": "Read", "input": {"file_path": "$NOPE/x"}},
        {"type": "tool_use", "name": "Write", "input": {"file_path": 7}},
    ]
    (project / "s.jsonl").write_text(
        _row(entrypoint="sdk-cli") + "\n" + _row(type="assistant", message={"content": calls}) + "\n"
    )

    def broken(_command: str) -> list[tuple[str, bool]]:
        raise ValueError("unparsable")

    report = replay.replay(project, vault)
    assert report["unresolved_paths"] >= 2
    assert report["denied_writes_the_hook_layer_allows"] == {"sandbox-only rule": 1}
    monkeypatch.setattr(replay, "_collect", broken)
    assert replay.replay(project, vault)["checked"].get("unparsed bash") == 2
