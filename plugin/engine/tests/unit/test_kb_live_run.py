"""A scheduled run's commit is the run's, whatever its environment says (#595).

The commit hook used to read ``SCOUT_MODE``/``SCOUT_READER`` only, so a run
could commit with ``env -u SCOUT_MODE`` and get the owner's rules. Now
``scout.kb.live_run`` reads the process table: a run of this vault is live
while its runner holds the session lock, or its ``claude-with-retry.sh`` or its
``claude -p`` session is alive. A commit is the run's when its process ancestry
passes through one of those, or, while a run is live, when the ancestry
reaches no interactive host (a terminal, an IDE or Claude app, sshd, tmux): a
double-forked or orphaned ``git``. The owner's commits from their own terminal
or app keep the owner's rules, run or no run.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from scout.kb import lint_privacy, live_run, outbound
from scout.kb.lint import run_hook
from scout.kb.live_run import Proc, commit_source, run_escape, run_processes
from scout.kb.ontology import clear_frontmatter_cache
from tests.unit.privacy_vault_helpers import build_privacy_vault, git

POSITIONS = "trading/positions.md"  # local-only through the fixture overlay's local_only_add
MOVED = "notes/positions.md"
V = Path("/Users/alex/Scout")


def _table(*rows: tuple[int, int, str], zombies: tuple[int, ...] = ()) -> dict[int, Proc]:
    procs = {1: Proc(1, 0, "Ss", "/sbin/launchd")}
    for pid, ppid, command in rows:
        procs[pid] = Proc(pid, ppid, "Z" if pid in zombies else "S", command)
    return procs


def _lock(vault: Path, pid: int | None, marker: str | None = None) -> None:
    logs = vault / ".scout-logs"
    logs.mkdir(parents=True, exist_ok=True)
    if pid is not None:
        (logs / ".scout-session.lock").write_text(f"{pid}\n", encoding="utf-8")
    if marker is not None:
        (logs / ".scout-session.lock.run").write_text(marker, encoding="utf-8")


RUNNER = (100, 1, f"/bin/bash {V}/run-scout.sh")
WRAPPER = (110, 100, f"/bin/bash {V}/scripts/claude-with-retry.sh {V}/.scout-logs/x.log claude --permission-mode auto")
CLAUDE = (
    120,
    110,
    "claude --permission-mode auto --model opus --max-budget-usd 25 --name scout-morning-briefing-20261107-0700 "
    f"-p You are Scout. Your working directory is {V}.",
)
TERMINAL = (500, 1, "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal")
LOGIN = (510, 500, "login -pf alex")
SHELL = (520, 510, "-zsh")


# ----- which processes are the run's -------------------------------------------


def test_the_runner_holding_the_lock_its_wrapper_and_its_claude_are_the_run() -> None:
    assert run_processes(V, _table(RUNNER, WRAPPER, CLAUDE), holder=100) == {100, 110, 120}
    # With no lock at all, the wrapper and claude still mark the run.
    assert run_processes(V, _table(RUNNER, WRAPPER, CLAUDE), holder=None) == {110, 120}


def test_a_killed_runner_leaves_its_wrapper_and_claude_marking_the_run() -> None:
    wrapper = (110, 1, WRAPPER[2])  # re-parented to launchd
    assert run_processes(V, _table(wrapper, CLAUDE), holder=100) == {110, 120}


@pytest.mark.parametrize(
    "holder_command",
    [
        "/opt/scout/engine/.venv/bin/python /opt/scout/engine/.venv/bin/scoutctl bootstrap upgrade",  # /scout-update
        "/usr/bin/some-unrelated-daemon",  # a PID reused after a reboot
    ],
)
def test_a_lock_holder_that_isnt_a_runner_is_no_run(holder_command: str) -> None:
    assert run_processes(V, _table((100, 1, holder_command)), holder=100) == set()


def test_a_zombie_runner_is_no_run() -> None:
    assert run_processes(V, _table(RUNNER, zombies=(100,)), holder=100) == set()


def test_another_vaults_run_is_not_this_vaults() -> None:
    other = Path("/Users/alex/Other")
    assert run_processes(other, _table(RUNNER, WRAPPER, CLAUDE), holder=None) == set()


# ----- whose commit is it ---------------------------------------------------------


def _commit_chain(top_ppid: int, base: int = 900) -> list[tuple[int, int, str]]:
    """A ``git commit`` and its pre-commit hook, under ``top_ppid``."""
    return [
        (base, top_ppid, "git commit -q -m move"),
        (base + 1, base, "/bin/sh .git/hooks/pre-commit"),
        (base + 2, base + 1, "/opt/scout/engine/.venv/bin/python scoutctl kb lint --staged"),
    ]


def test_a_commit_below_the_runs_claude_is_the_runs() -> None:
    table = _table(RUNNER, WRAPPER, CLAUDE, (130, 120, "/bin/zsh -c env -u SCOUT_MODE git commit"), *_commit_chain(130))
    src = commit_source(V, table, start=902, holder=100)
    assert src is not None and src.runs and src.by == "ancestry"


def test_a_detached_commit_during_a_run_is_the_runs() -> None:
    """``( git commit & )``: git is re-parented to launchd and reaches no interactive host."""
    table = _table(RUNNER, WRAPPER, CLAUDE, (130, 1, "/bin/zsh"), *_commit_chain(130))
    src = commit_source(V, table, start=902, holder=100)
    assert src is not None and src.runs and src.by == "detached"


def test_the_owners_commit_from_a_terminal_during_a_run_is_the_owners() -> None:
    table = _table(RUNNER, WRAPPER, CLAUDE, TERMINAL, LOGIN, SHELL, *_commit_chain(520))
    src = commit_source(V, table, start=902, holder=100)
    assert src is not None and not src.runs


@pytest.mark.parametrize(
    "host",
    [
        "/Applications/Claude.app/Contents/MacOS/Claude",
        "/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Plugin).app/Contents/MacOS/Code Helper",
        "/usr/sbin/sshd -D",
        "tmux",
        "tmux: server",
        "/usr/libexec/gnome-terminal-server",
    ],
)
def test_interactive_hosts_are_the_owners(host: str) -> None:
    table = _table(RUNNER, WRAPPER, CLAUDE, (600, 1, host), (610, 600, "/bin/zsh -l"), *_commit_chain(610))
    src = commit_source(V, table, start=902, holder=100)
    assert src is not None and not src.runs


def test_a_runner_started_from_the_owners_app_is_still_the_runs() -> None:
    """``/scout-dream`` from a Claude session: the run's ancestry reaches the app, but passes the runner first."""
    app = (600, 1, "/Applications/Claude.app/Contents/MacOS/Claude")
    runner = (100, 600, RUNNER[2])
    table = _table(app, runner, WRAPPER, CLAUDE, (130, 120, "/bin/zsh -c git commit"), *_commit_chain(130))
    src = commit_source(V, table, start=902, holder=100)
    assert src is not None and src.runs and src.by == "ancestry"


def test_no_run_live_is_no_verdict() -> None:
    table = _table((130, 1, "/bin/zsh"), *_commit_chain(130))
    assert commit_source(V, table, start=902, holder=None) is None


def test_the_mode_comes_from_the_marker_naming_the_runner(tmp_path: Path) -> None:
    _lock(tmp_path, 100, "100 dreaming-evening\n")
    runner = (100, 1, f"/bin/bash {tmp_path}/run-dreaming.sh")
    table = _table(runner, (130, 100, "/bin/zsh"), *_commit_chain(130))
    src = commit_source(tmp_path, table, start=902)
    assert src is not None and src.runs and src.mode == "dreaming-evening"


@pytest.mark.parametrize("marker", [None, "", "garbage\n", "999 dreaming\n", "100 $(rm -rf ~)\n"])
def test_a_missing_or_unusable_marker_still_counts_the_run(tmp_path: Path, marker: str | None) -> None:
    _lock(tmp_path, 100, marker)
    runner = (100, 1, f"/bin/bash {tmp_path}/run-scout.sh")
    table = _table(runner, (130, 100, "/bin/zsh"), *_commit_chain(130))
    src = commit_source(tmp_path, table, start=902)
    assert src is not None and src.runs and src.mode == "scheduled"


# ----- the real process table -------------------------------------------------------


@pytest.fixture
def fake_runner(tmp_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    """A live process whose argv is ``<vault>/run-scout.sh``, holding the vault's lock."""
    script = tmp_path / "run-scout.sh"
    script.write_text("#!/bin/bash\nsleep 60\n", encoding="utf-8")
    script.chmod(0o755)
    proc = subprocess.Popen([str(script)])
    _lock(tmp_path, proc.pid, f"{proc.pid} morning-briefing\n")
    yield proc
    proc.kill()
    proc.wait()


def test_the_real_process_table_finds_a_live_runner(tmp_path: Path, fake_runner: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 5
    while "run-scout.sh" not in live_run.process_table().get(fake_runner.pid, Proc(0, 0, "", "")).command:
        assert time.monotonic() < deadline, "the runner never showed its argv"
        time.sleep(0.05)
    assert fake_runner.pid in run_processes(tmp_path, live_run.process_table(), holder=fake_runner.pid)


# ----- the commit hook ------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    return build_privacy_vault(tmp_path)


@pytest.fixture
def live_lock_in_vault(vault: Path) -> Iterator[int]:
    """A live process holding the vault's lock, with a marker naming it (what ps can't tell apart)."""
    proc = subprocess.Popen(["sleep", "60"])
    _lock(vault, proc.pid, f"{proc.pid} morning-briefing\n")
    yield proc.pid
    proc.kill()
    proc.wait()


def _hook(vault: Path, env: dict[str, str]) -> tuple[int, str]:
    buf = io.StringIO()
    return run_hook(vault, env=env, out=buf), buf.getvalue()


def _events(vault: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((vault / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [r for r in rows if r["kind"] == "kb_lint.scheduled_by_process"]


def _move_out_of_local_only(vault: Path) -> None:
    (vault / MOVED).parent.mkdir(parents=True, exist_ok=True)
    git(vault, "mv", POSITIONS, MOVED)


def _as(monkeypatch: pytest.MonkeyPatch, vault: Path, *, under: str) -> None:
    """Make this process (the hook) a commit under the run's claude, detached, or the owner's terminal."""
    me = os.getpid()
    runner = (100, 1, f"/bin/bash {vault}/run-scout.sh")
    parent = {"claude": 120, "detached": 1, "terminal": 520}[under]
    claude = (120, 100, f"claude --max-budget-usd 25 --name scout-x -p Your working directory is {vault}.")
    table = _table(runner, claude, TERMINAL, LOGIN, SHELL, (me, parent, "python scoutctl kb lint --staged"))
    monkeypatch.setattr(live_run, "process_table", lambda: table)
    _lock(vault, 100, "100 morning-briefing\n")


@pytest.mark.parametrize("env", [{}, {"SCOUT_MODE": ""}, {"SCOUT_READER": "owner"}])
@pytest.mark.parametrize("under", ["claude", "detached"])
def test_a_runs_commit_is_checked_as_scheduled_whatever_its_env(
    vault: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], under: str
) -> None:
    _as(monkeypatch, vault, under=under)
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, env)
    assert rc == 1 and "[privacy-widening]" in out and "local-only" in out
    (line,) = [ln for ln in out.splitlines() if "comes from a scheduled run" in ln]
    assert line.startswith("kb-lint: this commit comes from a scheduled run (morning-briefing)")
    assert "PID" not in line and "lock" not in line and "process" not in line, "the run isn't told what marks it"
    (event,) = _events(vault)
    assert event["payload"] == {"mode": "morning-briefing", "by": {"claude": "ancestry", "detached": "detached"}[under]}


def test_the_owners_commit_during_a_run_keeps_the_owners_rules(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _as(monkeypatch, vault, under="terminal")
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, {})
    assert rc == 0, out
    assert "kb-lint: a scheduled run is in progress; this commit comes from your own session" in out
    assert _events(vault) == []


def test_a_commit_already_marked_scheduled_leaves_no_trace(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _as(monkeypatch, vault, under="claude")
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, {"SCOUT_MODE": "morning-briefing"})
    assert rc == 1 and "[privacy-widening]" in out
    assert "comes from" not in out and _events(vault) == []


def test_the_run_decides_the_privacy_check_failure_too(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(lint_privacy, "load_policy", boom)
    _as(monkeypatch, vault, under="claude")
    path = vault / "knowledge-base/people/priya.md"
    path.write_text(path.read_text(encoding="utf-8") + "More notes.\n", encoding="utf-8")
    git(vault, "add", "--", "knowledge-base/people/priya.md")
    rc, out = _hook(vault, {})
    assert rc == 1 and "[privacy-check-failed]" in out and "WARNING" not in out


def test_the_owners_commit_is_unchanged_when_no_run_is_live(vault: Path) -> None:
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, {})
    assert rc == 0, out
    assert "scheduled run" not in out


def _no_ps() -> dict[int, Proc]:
    raise OSError("ps")


def test_without_a_process_table_a_live_looking_lock_fails_closed(
    vault: Path, monkeypatch: pytest.MonkeyPatch, live_lock_in_vault: int
) -> None:
    monkeypatch.setattr(live_run, "process_table", _no_ps)
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, {})
    assert rc == 1 and "[privacy-widening]" in out


def test_without_a_process_table_and_no_live_lock_the_owner_commits(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_run, "process_table", _no_ps)
    _move_out_of_local_only(vault)
    assert _hook(vault, {})[0] == 0


def test_a_commit_from_a_linked_worktree_finds_the_main_vaults_run(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _as(monkeypatch, vault, under="claude")
    wt = tmp_path / "wt"
    git(vault, "worktree", "add", "-q", str(wt))
    (wt / MOVED).parent.mkdir(parents=True, exist_ok=True)
    git(wt, "mv", POSITIONS, MOVED)
    rc, out = _hook(wt, {})
    assert rc == 1 and "[privacy-widening]" in out


# ----- the PreToolUse classifier ----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "env -u SCOUT_MODE git commit -m x",
        "env -uSCOUT_READER git commit -m x",
        "env --unset=SCOUT_MODE git commit -m x",
        "env --unset SCOUT_MODE git commit -m x",
        'env -u "SCOUT_MODE" git commit -m x',
        "env LANG=C env -u SCOUT_MODE git commit",
        "SCOUT_MODE= git commit -m x",
        "SCOUT_READER=owner scripts/git-safe-commit.sh 'x' notes/a.md",
        "cd notes && SCOUT_MODE='' git commit -m x",
        "unset SCOUT_MODE; git commit -m x",
        "unset -v SCOUT_READER && git commit -m x",
        "export SCOUT_MODE=",
        "export -n SCOUT_MODE",
        "declare +x SCOUT_READER",
        "env -i PATH=/usr/bin git commit -m x",
        "env --ignore-environment git commit -m x",
        "env - git commit -m x",
        "bash -c 'env -u SCOUT_MODE git commit -m x'",
        "eval 'unset SCOUT_MODE'",
    ],
)
def test_commands_that_change_the_run_env_are_flagged(command: str) -> None:
    assert run_escape(command) == "env"


@pytest.mark.parametrize(
    "command",
    [
        "nohup git commit -m x",
        "setsid git commit -m x",
        "git commit -m x & disown",
        "git commit -m x &",
        "(git commit -m x &)",
        "sleep 1 & git commit -m x",
        "launchctl submit -l x -- git commit",
        'osascript -e \'tell app "Terminal" to do script "git commit"\'',
        "open -a Terminal notes/commit.command",
        "tmux new -d 'git commit -m x'",
        "screen -dm git commit -m x",
        "script -q /dev/null git commit -m x",
        "echo 'git commit' | at now",
        "echo 'git commit' | batch",
        "ssh localhost git commit",
        "bash -c 'git commit -m x &'",
    ],
)
def test_commands_that_detach_are_flagged(command: str) -> None:
    assert run_escape(command) == "detach"


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'nightly KB pass'",
        'echo "$SCOUT_MODE"',
        'echo "${SCOUT_READER:-autonomous}"',
        "echo ${#SCOUT_MODE}",
        "printenv SCOUT_MODE",
        "env | grep SCOUT_MODE",
        "env LC_ALL=C grep -ri plan notes/",
        "/usr/bin/env python3 x.py -i",
        "scripts/git-safe-commit.sh 'kb: note SCOUT_MODE= handling' notes/a.md",
        "git commit -m 'unset SCOUT_MODE in the docs'",
        "scripts/scoutctl kb search SCOUT_READER",
        'grep -rn "SCOUT_MODE=" scripts/',
        "export SCOUT_MODE",
        "git add notes/a.md && git commit -m x 2>&1",
        "scripts/git-safe-commit.sh 'look at this' notes/a.md &>/dev/null",
        "ls scripts/ >&2",
        "scripts/scoutctl kb read notes/script.md",
        "git commit -m 'run it & see'",
    ],
)
def test_ordinary_commands_and_reads_are_not_flagged(command: str) -> None:
    assert run_escape(command) is None
