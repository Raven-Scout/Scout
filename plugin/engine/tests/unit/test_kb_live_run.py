"""A scheduled run's commit is the run's, whatever its environment says (#595).

The commit hook used to read ``SCOUT_MODE``/``SCOUT_READER`` only, so a run
could commit with ``env -u SCOUT_MODE`` and get the owner's rules. Now
``scout.kb.live_run`` reads the process table. A run of this vault is live while
its runner holds the session lock, its ``claude-with-retry.sh`` or ``claude -p``
session is alive, or its holder died leaving lock and marker behind. A commit is
the run's when any process in its ancestry is one of those. While a run is live
it is also the run's unless its ancestry reaches an interactive host: a process
whose real executable (never its argv) is a terminal, IDE or Claude app, sshd,
tmux and so on, and which started before the run did. The owner's commits from
their own terminal or app keep the owner's rules, run or no run.
"""

from __future__ import annotations

import ctypes
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
from scout.kb.live_run import (
    Proc,
    commit_source,
    commit_source_now,
    exe_path,
    is_host_exe,
    parse_etime,
    run_escape,
    run_processes,
)
from scout.kb.ontology import clear_frontmatter_cache
from tests.unit.privacy_vault_helpers import build_privacy_vault, git

POSITIONS = "trading/positions.md"  # local-only through the fixture overlay's local_only_add
MOVED = "notes/positions.md"
V = Path("/Users/alex/Scout")

BASH = "/bin/bash"
GIT = "/opt/homebrew/bin/git"
XCODE_GIT = "/Applications/Xcode.app/Contents/Developer/usr/bin/git"
VENV_PY = "/opt/scout/.venv/bin/python"
APP_PY = "Resources/Python.app/Contents/MacOS/Python"
FRAMEWORK_PY = f"/opt/homebrew/Cellar/python@3.13/3.13.1/Frameworks/Python.framework/Versions/3.13/{APP_PY}"
XCODE_PY = f"/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/{APP_PY}"
VSCODE_HELPER = "/Applications/VS Code.app/Contents/Frameworks/Code Helper.app/Contents/MacOS/Code Helper"
TERMINAL_EXE = "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"
LONG_AGO, RUN_AGE, JUST_NOW = 100_000, 1_000, 1  # seconds since each process started


Row = tuple[int, int, str, str, int]  # pid, ppid, command, real executable, elapsed


def _world(*rows: Row, zombies: tuple[int, ...] = ()) -> tuple[dict[int, Proc], dict[int, str]]:
    """A process table, and the real executable of each process (what ``exe_path`` reads from the kernel)."""
    table = {1: Proc(1, 0, "Ss", "/sbin/launchd", 500_000)}
    exes = {1: "/sbin/launchd"}
    for pid, ppid, command, exe, elapsed in rows:
        table[pid] = Proc(pid, ppid, "Z" if pid in zombies else "S", command, elapsed)
        exes[pid] = exe
    return table, exes


def _source(vault: Path, rows: tuple[Row, ...], *, start: int = 902, holder: int | None = 100):
    table, exes = _world(*rows)
    return commit_source(vault, table, start=start, holder=holder, exe=exes.get)


def _lock(vault: Path, pid: int | None, marker: str | None = None, *, age: int | None = None) -> None:
    logs = vault / ".scout-logs"
    logs.mkdir(parents=True, exist_ok=True)
    if pid is not None:
        path = logs / ".scout-session.lock"
        path.write_text(f"{pid}\n", encoding="utf-8")
        if age is not None:
            then = time.time() - age
            os.utime(path, (then, then))
    if marker is not None:
        (logs / ".scout-session.lock.run").write_text(marker, encoding="utf-8")


RUNNER: Row = (100, 1, f"/bin/bash {V}/run-scout.sh", BASH, RUN_AGE)
WRAPPER: Row = (
    110,
    100,
    f"/bin/bash {V}/scripts/claude-with-retry.sh {V}/.scout-logs/x.log claude --permission-mode auto",
    BASH,
    RUN_AGE - 1,
)
CLAUDE: Row = (
    120,
    110,
    "claude --permission-mode auto --model opus --max-budget-usd 25 --name scout-morning-briefing-20261107-0700 "
    f"-p You are Scout. Your working directory is {V}.",
    "/Users/alex/.local/share/claude/versions/2.1.318",
    RUN_AGE - 2,
)
RUN: tuple[Row, ...] = (RUNNER, WRAPPER, CLAUDE)
TERMINAL: Row = (500, 1, TERMINAL_EXE, TERMINAL_EXE, LONG_AGO)
LOGIN: Row = (510, 500, "login -pf alex", "/usr/bin/login", LONG_AGO - 1)
SHELL: Row = (520, 510, "-zsh", "/bin/zsh", LONG_AGO - 2)
OWNER_TERMINAL = (TERMINAL, LOGIN, SHELL)


def _commit(top_ppid: int, *, git_exe: str = GIT, hook_exe: str = VENV_PY, message: str = "move") -> tuple[Row, ...]:
    """A ``git commit`` and its pre-commit hook, under ``top_ppid``, all started just now."""
    return (
        (900, top_ppid, f"git commit -q -m {message}", git_exe, JUST_NOW),
        (901, 900, "/bin/sh .git/hooks/pre-commit", "/bin/sh", JUST_NOW),
        (902, 901, f"{hook_exe} scoutctl kb lint --staged", hook_exe, JUST_NOW),
    )


# ----- which processes are the run's -------------------------------------------


def test_the_runner_holding_the_lock_its_wrapper_and_its_claude_are_the_run() -> None:
    table, _ = _world(*RUN)
    assert run_processes(V, table, holder=100) == {100, 110, 120}
    # With no lock at all, the wrapper and claude still mark the run.
    assert run_processes(V, table, holder=None) == {110, 120}


@pytest.mark.parametrize(
    "command",
    [
        f"{V}/scripts/claude-with-retry.sh {V}/.scout-logs/x.log claude",
        f"/bin/bash {V}/scripts/claude-with-retry.sh {V}/.scout-logs/x.log claude",
        f"bash {V}/scripts/claude-with-retry.sh",
    ],
)
def test_the_wrapper_is_the_wrapper_when_it_is_what_runs(command: str) -> None:
    table, _ = _world((110, 1, command, BASH, RUN_AGE))
    assert run_processes(V, table, holder=None) == {110}


@pytest.mark.parametrize(
    "command",
    [
        f"/bin/zsh -c cat {V}/scripts/claude-with-retry.sh; git commit -m x",
        f"less {V}/scripts/claude-with-retry.sh",
        f"/bin/bash -x {V}/scripts/claude-with-retry.sh",
        f"/bin/bash {V}/scripts/claude-with-retry.sh.bak",
        f"vim {V}/scripts/claude-with-retry.sh",
    ],
)
def test_a_command_that_only_names_the_wrapper_is_no_run(command: str) -> None:
    table, _ = _world((110, 1, command, BASH, RUN_AGE))
    assert run_processes(V, table, holder=None) == set()


def test_a_vault_path_with_a_space_still_finds_its_wrapper() -> None:
    vault = Path("/Users/alex/My Vault")
    table, _ = _world((110, 1, f"/bin/bash {vault}/scripts/claude-with-retry.sh x", BASH, RUN_AGE))
    assert run_processes(vault, table, holder=None) == {110}


def test_a_killed_runner_leaves_its_wrapper_and_claude_marking_the_run() -> None:
    wrapper: Row = (110, 1, *WRAPPER[2:])  # re-parented to launchd
    table, _ = _world(wrapper, CLAUDE)
    assert run_processes(V, table, holder=100) == {110, 120}


@pytest.mark.parametrize(
    "holder_command",
    [
        "/opt/scout/engine/.venv/bin/python /opt/scout/engine/.venv/bin/scoutctl bootstrap upgrade",  # /scout-update
        "/usr/bin/some-unrelated-daemon",  # a PID reused after a reboot
    ],
)
def test_a_lock_holder_that_isnt_a_runner_is_no_run(holder_command: str) -> None:
    table, _ = _world((100, 1, holder_command, "/usr/bin/x", 5))
    assert run_processes(V, table, holder=100) == set()


def test_a_zombie_runner_is_no_run() -> None:
    table, _ = _world(RUNNER, zombies=(100,))
    assert run_processes(V, table, holder=100) == set()


def test_another_vaults_run_is_not_this_vaults() -> None:
    table, _ = _world(*RUN)
    assert run_processes(Path("/Users/alex/Other"), table, holder=None) == set()


# ----- which executables are hosts --------------------------------------------------


@pytest.mark.parametrize(
    "exe",
    [
        TERMINAL_EXE,
        "/Applications/iTerm.app/Contents/MacOS/iTerm2",
        "/Applications/Claude.app/Contents/MacOS/Claude",
        VSCODE_HELPER,
        "/Users/alex/Applications/Ghostty.app/Contents/MacOS/ghostty",
        "/usr/sbin/sshd",
        "/usr/libexec/sshd-session",
        "/usr/bin/login",
        "/opt/homebrew/bin/tmux",
        "/opt/homebrew/Cellar/tmux/3.5a/bin/tmux",
        "/usr/bin/screen",
        "/usr/libexec/gnome-terminal-server",
        "/usr/bin/konsole",
    ],
)
def test_real_host_executables(exe: str) -> None:
    assert is_host_exe(exe)


@pytest.mark.parametrize(
    "exe",
    [
        None,
        "",
        BASH,
        GIT,
        XCODE_GIT,
        VENV_PY,
        FRAMEWORK_PY,
        XCODE_PY,
        "/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python",
        "/tmp/x/tmux",  # a copied binary
        "/Users/alex/Scout/x/sshd",
        "/tmp/Foo.app/Contents/MacOS/Foo",  # an app bundle made in a temp folder
        "/Users/alex/Scout/Foo.app/Contents/MacOS/Foo",
        "/Users/alex/.local/share/claude/versions/2.1.318",
    ],
)
def test_other_executables_are_not_hosts(exe: str | None) -> None:
    assert not is_host_exe(exe)


# ----- whose commit is it ---------------------------------------------------------


def test_a_commit_below_the_runs_claude_is_the_runs() -> None:
    shell: Row = (130, 120, "/bin/zsh -c env -u SCOUT_MODE git commit", "/bin/zsh", 5)
    src = _source(V, (*RUN, shell, *_commit(130)))
    assert src is not None and src.runs and src.by == "ancestry"


@pytest.mark.parametrize(
    "commit",
    [
        _commit(130, hook_exe=FRAMEWORK_PY),
        _commit(130, git_exe=XCODE_GIT),
        _commit(130, hook_exe=XCODE_PY, git_exe=XCODE_GIT),
    ],
    ids=["framework-python", "xcode-git", "xcode-both"],
)
def test_app_shaped_tools_below_the_run_change_nothing(commit: tuple[Row, ...]) -> None:
    """A venv on a framework Python shows the hook as ``…/Python.app/Contents/MacOS/Python``; it's no host."""
    shell: Row = (130, 120, "/bin/zsh -c git commit", "/bin/zsh", 5)
    src = _source(V, (*RUN, shell, *commit))
    assert src is not None and src.runs and src.by == "ancestry"


def test_a_run_process_above_a_real_host_still_makes_it_the_runs() -> None:
    """A run process anywhere in the chain wins: nothing the owner runs passes through a run."""
    host_below: Row = (130, 120, "/usr/bin/tmux", "/usr/bin/tmux", LONG_AGO)
    src = _source(V, (*RUN, host_below, *_commit(130)))
    assert src is not None and src.runs and src.by == "ancestry"


def test_a_detached_commit_during_a_run_is_the_runs() -> None:
    """``( git commit & )``: git is re-parented to launchd and reaches no host."""
    orphan: Row = (130, 1, "/bin/zsh", "/bin/zsh", 5)
    src = _source(V, (*RUN, orphan, *_commit(130)))
    assert src is not None and src.runs and src.by == "detached"


@pytest.mark.parametrize(
    "commit",
    [
        _commit(130, hook_exe=FRAMEWORK_PY),
        _commit(130, git_exe=XCODE_GIT),
        _commit(130, message="'see Foo.app/Contents/ for details'"),
    ],
    ids=["framework-python", "xcode-git", "message-naming-a-bundle"],
)
def test_a_detached_commit_with_app_shaped_tools_is_the_runs(commit: tuple[Row, ...]) -> None:
    orphan: Row = (130, 1, "/bin/zsh", "/bin/zsh", 5)
    src = _source(V, (*RUN, orphan, *commit))
    assert src is not None and src.runs and src.by == "detached"


@pytest.mark.parametrize(
    "fake",
    [
        (130, 1, "sshd", BASH, 5),  # exec -a sshd bash
        (130, 1, "x/tmux -c git commit", BASH, 5),  # a symlink named tmux: the kernel reports the target
        (130, 1, "login -c git commit", BASH, 5),  # python double-fork, execv as login
        (130, 1, "/tmp/x/tmux -c git commit", "/tmp/x/tmux", 5),  # a copied binary
        (130, 1, "/opt/homebrew/bin/tmux -c git", "/opt/homebrew/bin/tmux", 5),  # a real tmux started by the run
        (130, 1, "open-terminal", TERMINAL_EXE, 5),  # a real app, started after the run
    ],
    ids=["exec-a-sshd", "symlink-tmux", "execv-login", "copied-tmux", "fresh-tmux", "fresh-terminal"],
)
def test_a_fake_or_freshly_started_host_is_no_host(fake: Row) -> None:
    src = _source(V, (*RUN, fake, *_commit(130)))
    assert src is not None and src.runs and src.by == "detached"


def test_the_owners_commit_from_a_terminal_during_a_run_is_the_owners() -> None:
    src = _source(V, (*RUN, *OWNER_TERMINAL, *_commit(520)))
    assert src is not None and not src.runs and src.by == "host"


@pytest.mark.parametrize(
    "commit", [_commit(520, hook_exe=FRAMEWORK_PY), _commit(520, git_exe=XCODE_GIT)], ids=["framework", "xcode"]
)
def test_the_owner_keeps_their_rules_with_app_shaped_tools_too(commit: tuple[Row, ...]) -> None:
    src = _source(V, (*RUN, *OWNER_TERMINAL, *commit))
    assert src is not None and not src.runs


@pytest.mark.parametrize(
    "host",
    [
        "/Applications/Claude.app/Contents/MacOS/Claude",
        VSCODE_HELPER,
        "/usr/sbin/sshd",
        "/usr/libexec/sshd-session",
        "/opt/homebrew/bin/tmux",
        "/usr/libexec/gnome-terminal-server",
    ],
)
def test_interactive_hosts_are_the_owners(host: str) -> None:
    rows: tuple[Row, ...] = ((600, 1, host, host, LONG_AGO), (610, 600, "/bin/zsh -l", "/bin/zsh", LONG_AGO - 1))
    src = _source(V, (*RUN, *rows, *_commit(610)))
    assert src is not None and not src.runs


def test_a_host_that_started_with_the_run_fails_closed() -> None:
    host: Row = (600, 1, TERMINAL_EXE, TERMINAL_EXE, RUN_AGE)  # the same second as the runner: can't say it's older
    src = _source(V, (*RUN, host, *_commit(600)))
    assert src is not None and src.runs


def test_a_runner_started_from_the_owners_app_is_still_the_runs() -> None:
    """``/scout-dream`` from a Claude session: the run's ancestry reaches the app, but it passes the runner."""
    app_exe = "/Applications/Claude.app/Contents/MacOS/Claude"
    app: Row = (600, 1, app_exe, app_exe, LONG_AGO)
    runner: Row = (100, 600, *RUNNER[2:])
    shell: Row = (130, 120, "/bin/zsh -c git commit", "/bin/zsh", 5)
    src = _source(V, (app, runner, WRAPPER, CLAUDE, shell, *_commit(130)))
    assert src is not None and src.runs and src.by == "ancestry"


def test_no_run_live_is_no_verdict() -> None:
    orphan: Row = (130, 1, "/bin/zsh", "/bin/zsh", 5)
    assert _source(V, (orphan, *_commit(130)), holder=None) is None


def test_an_unknown_executable_is_no_host() -> None:
    table, exes = _world(*RUN, *OWNER_TERMINAL, *_commit(520))
    src = commit_source(V, table, start=902, holder=100, exe=lambda _pid: None)
    assert src is not None and src.runs and exes  # can't read it: fail closed


# ----- a holder that died without releasing (#595, re-review) ------------------------


def test_a_dead_holder_with_its_marker_keeps_the_run_live(tmp_path: Path) -> None:
    """A run that kills its wrapper and runner: claude shows only ``claude``, so lock + marker must carry it."""
    _lock(tmp_path, 100, "100 morning-briefing\n", age=RUN_AGE)
    title_only: Row = (120, 1, "claude", "/Users/alex/.local/share/claude/versions/2.1.318", RUN_AGE - 2)
    shell: Row = (130, 120, "/bin/zsh -c git commit", "/bin/zsh", 5)
    table, exes = _world(title_only, shell, *_commit(130))
    src = commit_source(tmp_path, table, start=902, exe=exes.get)
    assert src is not None and src.runs and src.by == "stale-lock" and src.mode == "morning-briefing"


def test_a_dead_holders_marker_leaves_the_owners_old_terminal_alone(tmp_path: Path) -> None:
    _lock(tmp_path, 100, "100 morning-briefing\n", age=RUN_AGE)
    table, exes = _world(*OWNER_TERMINAL, *_commit(520))
    src = commit_source(tmp_path, table, start=902, exe=exes.get)
    assert src is not None and not src.runs and src.dead_holder


def test_a_live_run_has_no_dead_holder() -> None:
    src = _source(V, (*RUN, *OWNER_TERMINAL, *_commit(520)))
    assert src is not None and not src.dead_holder


def test_a_lock_and_marker_from_before_the_last_boot_are_no_run(tmp_path: Path) -> None:
    _lock(tmp_path, 100, "100 morning-briefing\n", age=600_000)  # launchd in _world has been up 500_000 s
    orphan: Row = (130, 1, "/bin/zsh", "/bin/zsh", 5)
    table, exes = _world(orphan, *_commit(130))
    assert commit_source(tmp_path, table, start=902, exe=exes.get) is None


@pytest.mark.parametrize("marker", [None, "999 dreaming\n"], ids=["no-marker", "another-holders-marker"])
def test_a_dead_holder_without_its_marker_is_no_run(tmp_path: Path, marker: str | None) -> None:
    _lock(tmp_path, 100, marker, age=RUN_AGE)
    orphan: Row = (130, 1, "/bin/zsh", "/bin/zsh", 5)
    table, exes = _world(orphan, *_commit(130))
    assert commit_source(tmp_path, table, start=902, exe=exes.get) is None


def test_a_live_holder_that_isnt_a_runner_ignores_a_stale_marker(tmp_path: Path) -> None:
    """Bootstrap took the lock after a crash; the dead run's marker names another PID."""
    _lock(tmp_path, 200, "100 morning-briefing\n", age=5)
    bootstrap: Row = (200, 1, "/opt/scout/.venv/bin/python scoutctl bootstrap upgrade", VENV_PY, 5)
    table, exes = _world(bootstrap, *_commit(200))
    assert commit_source(tmp_path, table, start=902, exe=exes.get) is None


# ----- the mode label --------------------------------------------------------------


def test_the_mode_comes_from_the_marker_naming_the_runner(tmp_path: Path) -> None:
    _lock(tmp_path, 100, "100 dreaming-evening\n")
    runner: Row = (100, 1, f"/bin/bash {tmp_path}/run-dreaming.sh", BASH, RUN_AGE)
    shell: Row = (130, 100, "/bin/zsh", "/bin/zsh", 5)
    table, exes = _world(runner, shell, *_commit(130))
    src = commit_source(tmp_path, table, start=902, exe=exes.get)
    assert src is not None and src.runs and src.mode == "dreaming-evening"


@pytest.mark.parametrize("marker", [None, "", "garbage\n", "999 dreaming\n", "100 $(rm -rf ~)\n"])
def test_a_missing_or_unusable_marker_still_counts_the_run(tmp_path: Path, marker: str | None) -> None:
    _lock(tmp_path, 100, marker)
    runner: Row = (100, 1, f"/bin/bash {tmp_path}/run-scout.sh", BASH, RUN_AGE)
    shell: Row = (130, 100, "/bin/zsh", "/bin/zsh", 5)
    table, exes = _world(runner, shell, *_commit(130))
    src = commit_source(tmp_path, table, start=902, exe=exes.get)
    assert src is not None and src.runs and src.mode == "scheduled"


# ----- the real process table -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("00:05", 5), ("01:02", 62), ("01:02:03", 3723), ("2-01:02:03", 176523), ("garbage", 0), ("", 0)],
)
def test_parse_etime(text: str, seconds: int) -> None:
    assert parse_etime(text) == seconds


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
    while "run-scout.sh" not in live_run.process_table().get(fake_runner.pid, Proc(0, 0, "", "", 0)).command:
        assert time.monotonic() < deadline, "the runner never showed its argv"
        time.sleep(0.05)
    table = live_run.process_table()
    assert fake_runner.pid in run_processes(tmp_path, table, holder=fake_runner.pid)
    assert table[1].elapsed > 0


def test_a_continuation_line_cant_rewrite_a_process_already_read(monkeypatch: pytest.MonkeyPatch) -> None:
    out = (
        "  10   1 S    00:05 /bin/bash\n"
        "  20  10 S    00:03 x --prompt line one\n"
        "  10   1 S 99:00 /Applications/T.app/Contents/MacOS/T\n"
    )

    def fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, out, "")

    monkeypatch.setattr(live_run.subprocess, "run", fake_run)
    table = live_run.process_table()
    assert table[10].command == "/bin/bash" and table[10].elapsed == 5
    assert "/Applications/T.app" in table[20].command


def test_the_real_executable_ignores_a_spoofed_argv0(tmp_path: Path) -> None:
    link = tmp_path / "tmux"
    link.symlink_to("/bin/bash")
    # "; :" keeps bash from exec-ing sleep in its place.
    for argv0 in ("sshd", str(link)):
        proc = subprocess.Popen([argv0, "-c", "sleep 30; :"], executable=str(link))
        try:
            time.sleep(0.2)
            exe = live_run.exe_path(proc.pid)
            assert exe is not None and Path(exe).name == "bash", exe
            assert not is_host_exe(exe)
        finally:
            proc.kill()
            proc.wait()


def _ps_prints(monkeypatch: pytest.MonkeyPatch, out: str) -> None:
    def fake_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, out, "")

    monkeypatch.setattr(live_run.subprocess, "run", fake_run)


def test_a_line_before_any_process_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _ps_prints(monkeypatch, "not a row\n  10   1 S    00:05 /bin/bash\n")
    assert list(live_run.process_table()) == [10]


def test_a_ps_that_lists_nothing_is_a_ps_that_cant_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _ps_prints(monkeypatch, "")
    with pytest.raises(OSError, match="no processes"):
        live_run.process_table()


class _FakeLibproc:
    """``proc_pidpath``: writes the path into the buffer and returns its length, or 0 when it can't."""

    def __init__(self, path: bytes) -> None:
        self.path = path

    def proc_pidpath(self, _pid: int, buf: ctypes.Array[ctypes.c_char], _size: int) -> int:
        buf.value = self.path
        return len(self.path)


def test_on_macos_the_executable_comes_from_libproc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_run.sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda _name: _FakeLibproc(b"/bin/bash"))
    assert exe_path(42) == "/bin/bash"


def test_on_macos_a_process_libproc_cant_read_has_no_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_run.sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", lambda _name: _FakeLibproc(b""))
    assert exe_path(42) is None


def test_on_macos_a_missing_libproc_means_no_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_library(_name: str) -> object:
        raise OSError("no such library")

    monkeypatch.setattr(live_run.sys, "platform", "darwin")
    monkeypatch.setattr(ctypes, "CDLL", no_library)
    assert exe_path(42) is None


def test_on_linux_the_executable_comes_from_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def readlink(path: str) -> str:
        seen.append(path)
        return "/usr/bin/bash"

    monkeypatch.setattr(live_run.sys, "platform", "linux")
    monkeypatch.setattr(live_run.os, "readlink", readlink)
    assert exe_path(42) == "/usr/bin/bash"
    assert seen == ["/proc/42/exe"]


def test_on_linux_a_process_proc_wont_show_has_no_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(_path: str) -> str:
        raise PermissionError("another user's process")

    monkeypatch.setattr(live_run.sys, "platform", "linux")
    monkeypatch.setattr(live_run.os, "readlink", denied)
    assert exe_path(42) is None


def test_a_vault_path_that_wont_resolve_still_matches_as_written(monkeypatch: pytest.MonkeyPatch) -> None:
    def cant_resolve(_self: Path) -> Path:
        raise OSError("gone")

    monkeypatch.setattr(Path, "resolve", cant_resolve)
    table, _ = _world(WRAPPER)
    assert run_processes(V, table, holder=None) == {110}


def test_a_reused_pid_named_by_a_stale_marker_is_no_run(tmp_path: Path) -> None:
    """The holder died, and its PID now belongs to something else that is alive and isn't a runner."""
    _lock(tmp_path, 200, "200 morning-briefing\n", age=RUN_AGE)
    other: Row = (200, 1, "/usr/bin/some-unrelated-daemon", "/usr/bin/some-unrelated-daemon", 5)
    orphan: Row = (130, 1, "/bin/zsh", "/bin/zsh", 5)
    table, exes = _world(other, orphan, *_commit(130))
    assert commit_source(tmp_path, table, start=902, exe=exes.get) is None


def test_a_lock_that_vanished_between_reads_is_no_run(tmp_path: Path) -> None:
    _lock(tmp_path, None, "100 morning-briefing\n")  # the marker is there; the lock file isn't
    orphan: Row = (130, 1, "/bin/zsh", "/bin/zsh", 5)
    table, exes = _world(orphan, *_commit(130))
    assert commit_source(tmp_path, table, start=902, holder=100, exe=exes.get) is None


def _no_ps_now() -> dict[int, Proc]:
    raise OSError("ps")


def test_without_ps_a_marker_naming_a_dead_holder_is_no_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = subprocess.Popen(["true"])
    proc.wait()
    _lock(tmp_path, proc.pid, f"{proc.pid} morning-briefing\n")
    monkeypatch.setattr(live_run, "process_table", _no_ps_now)
    assert commit_source_now(tmp_path) is None


def test_without_ps_a_holder_owned_by_another_user_is_still_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_yours(_pid: int, _sig: int) -> None:
        raise PermissionError("another user's process")

    _lock(tmp_path, 100, "100 morning-briefing\n")
    monkeypatch.setattr(live_run, "process_table", _no_ps_now)
    monkeypatch.setattr(live_run.os, "kill", not_yours)
    src = commit_source_now(tmp_path)
    assert src is not None and src.runs and src.by == "no-ps" and src.mode == "morning-briefing"


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
    parent = {"claude": 120, "detached": 1, "terminal": 520}[under]
    runner: Row = (100, 1, f"/bin/bash {vault}/run-scout.sh", BASH, RUN_AGE)
    claude: Row = (
        120,
        100,
        f"claude --max-budget-usd 25 --name scout-x -p Your working directory is {vault}.",
        "/Users/alex/.local/share/claude/versions/2.1.318",
        RUN_AGE - 2,
    )
    hook: Row = (me, parent, "python scoutctl kb lint --staged", VENV_PY, JUST_NOW)
    table, exes = _world(runner, claude, *OWNER_TERMINAL, hook)
    monkeypatch.setattr(live_run, "process_table", lambda: table)
    monkeypatch.setattr(live_run, "exe_path", exes.get)
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


def _dead_holder(monkeypatch: pytest.MonkeyPatch, vault: Path, *, under_terminal: bool) -> None:
    """The hook's commit after a run died and left lock and marker, from an old terminal or a new shell."""
    me = os.getpid()
    hook: Row = (me, 520 if under_terminal else 1, "python scoutctl kb lint --staged", VENV_PY, JUST_NOW)
    table, exes = _world(*OWNER_TERMINAL, hook)
    monkeypatch.setattr(live_run, "process_table", lambda: table)
    monkeypatch.setattr(live_run, "exe_path", exes.get)
    _lock(vault, 100, "100 morning-briefing\n", age=RUN_AGE)


def test_a_dead_holder_tells_the_owner_what_happened_and_how_to_clear_it(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dead_holder(monkeypatch, vault, under_terminal=False)
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, {})
    assert rc == 1 and "[privacy-widening]" in out
    (line,) = [ln for ln in out.splitlines() if "died" in ln]
    assert line.startswith("kb-lint: a scheduled run (morning-briefing) died and left its session lock")
    assert "the next run clears it" in line and "delete .scout-logs/.scout-session.lock" in line
    assert "comes from a scheduled run" not in out
    (event,) = _events(vault)
    assert event["payload"] == {"mode": "morning-briefing", "by": "stale-lock"}


def test_a_dead_holder_notice_in_the_owners_old_terminal_says_no_run_is_in_progress(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dead_holder(monkeypatch, vault, under_terminal=True)
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, {})
    assert rc == 0, out
    assert "in progress" not in out
    (line,) = [ln for ln in out.splitlines() if "died" in ln]
    assert "your rules apply" in line and "delete .scout-logs/.scout-session.lock" in line
    assert _events(vault) == []


def test_clearing_a_dead_holders_lock_as_the_message_says_gives_the_owner_their_rules(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dead_holder(monkeypatch, vault, under_terminal=False)
    _move_out_of_local_only(vault)
    assert _hook(vault, {})[0] == 1
    (vault / ".scout-logs" / ".scout-session.lock").unlink()
    rc, out = _hook(vault, {})
    assert rc == 0, out
    assert "scheduled run" not in out


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
        "cat <<'EOF' > /tmp/x\nbody\nEOF\nenv -u SCOUT_MODE git commit -m x",
    ],
)
def test_commands_that_change_the_run_env_are_flagged(command: str) -> None:
    assert run_escape(command) == "env"


@pytest.mark.parametrize(
    "command",
    [
        "env SCOUT_MODE=x git commit -m y",
        "env -u OTHER -u SCOUT_MODE git commit",
        "env -C notes -u SCOUT_MODE git commit",
        "env -0 -u SCOUT_READER git commit",
        "FOO=1 SCOUT_MODE= git commit",
        "sudo unset SCOUT_MODE",
        "command env -u SCOUT_MODE git commit",
    ],
)
def test_env_changes_behind_other_options_and_prefixes_are_flagged(command: str) -> None:
    assert run_escape(command) == "env"


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'about <<EOF here'\nnohup git push",  # a quoted "<<" starts no here-document
        "cat <<EOF\nno closing line\nnohup git push",  # an unterminated one keeps the rest scanned
    ],
)
def test_text_that_only_looks_like_a_heredoc_is_still_scanned(command: str) -> None:
    assert run_escape(command) == "detach"


@pytest.mark.parametrize(
    "command",
    [
        "FOO=1",
        "FOO=1 git commit -m x",
        "sudo git status",
        "env -u OTHER git status",
        "env -S 'git status'",
        "env -0 git status",
        'git commit -m "unterminated',
        "git commit -m 'about <<EOF here'",
    ],
)
def test_other_options_prefixes_and_broken_quotes_are_not_flagged(command: str) -> None:
    assert run_escape(command) is None


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
        "cat <<'EOF' > /tmp/x\nbody\nEOF\nnohup git commit -m x",
        "cat <<'EOF' > /tmp/x\nbody\nEOF\ngit commit -m x &",
    ],
)
def test_commands_that_detach_are_flagged(command: str) -> None:
    assert run_escape(command) == "detach"


TELEGRAM_WRAP = """scoutctl notify telegram --tier info --parse-mode html --body-file - <<'SCOUT_WRAP'
<b>Scout consolidation complete.</b>
• Watching: 4 PRs (open since Monday)
• Standup (at 10:00), R&amp;D review, P&L
script review; nohup is not a command here
at noon
SCOUT_WRAP"""


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
        'gh api "repos/example-org/x/pulls?state=open&per_page=5"',
        "git log --format=%at -1",
        "git commit -m 'watching 4 PRs (open since Monday)'",
        'git commit -m "standup (at 10:00)"',
        TELEGRAM_WRAP,
        "git commit -F - <<'EOF'\nQ3 P&L review; (at 10:00)\nnohup notes\nEOF",
        "git commit -F - <<EOF\nP&L\nEOF",
        "cat <<-'EOF' > notes/a.md\n\tR&D (open items)\n\tEOF",
    ],
)
def test_ordinary_commands_and_reads_are_not_flagged(command: str) -> None:
    assert run_escape(command) is None
