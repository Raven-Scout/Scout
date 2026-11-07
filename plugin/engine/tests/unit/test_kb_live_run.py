"""A live scheduled run counts its commits as scheduled, whatever their environment says (#595).

The runner's session lock (``scripts/scout-lock.sh``) writes a run marker,
``.scout-logs/.scout-session.lock.run``, beside the lock for as long as the run
holds it. While the marker is there and the lock's PID is alive (and the marker
names no other PID), ``kb lint --staged`` checks a commit as a scheduled run's,
so ``env -u SCOUT_MODE git commit …`` or ``SCOUT_MODE= git commit …`` no longer
gets the owner's rules. The owner's commits are unchanged when no run is live.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from scout.kb import lint_privacy, outbound
from scout.kb.lint import run_hook
from scout.kb.live_run import RUN_MARKER, LiveRun, live_run, names_run_env
from scout.kb.ontology import clear_frontmatter_cache
from tests.unit.privacy_vault_helpers import build_privacy_vault, git

POSITIONS = "trading/positions.md"  # local-only through the fixture overlay's local_only_add
MOVED = "notes/positions.md"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    clear_frontmatter_cache()
    outbound.clear_cache()
    return build_privacy_vault(tmp_path)


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _hold(vault: Path, pid: int, marker: str | None) -> None:
    """The lock as a runner leaves it: the lock holds ``pid``; the marker (if any) is ``marker``."""
    logs = vault / ".scout-logs"
    logs.mkdir(exist_ok=True)
    (logs / ".scout-session.lock").write_text(f"{pid}\n", encoding="utf-8")
    if marker is not None:
        (logs / RUN_MARKER).write_text(marker, encoding="utf-8")


def _hook(vault: Path, env: dict[str, str]) -> tuple[int, str]:
    buf = io.StringIO()
    return run_hook(vault, env=env, out=buf), buf.getvalue()


def _events(vault: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((vault / ".scout-logs").glob("schedule-events-*.jsonl")):
        rows += [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return rows


def _move_out_of_local_only(vault: Path) -> None:
    (vault / MOVED).parent.mkdir(parents=True, exist_ok=True)
    git(vault, "mv", POSITIONS, MOVED)


# ----- live_run -------------------------------------------------------------------


def test_a_marker_naming_the_live_lock_holder_is_a_live_run(tmp_path: Path) -> None:
    _hold(tmp_path, os.getpid(), f"{os.getpid()} dreaming-evening\n")
    assert live_run(tmp_path) == LiveRun(pid=os.getpid(), mode="dreaming-evening")


def test_no_marker_is_no_run(tmp_path: Path) -> None:
    _hold(tmp_path, os.getpid(), None)  # the lock alone: scoutctl bootstrap holds it too
    assert live_run(tmp_path) is None
    assert live_run(tmp_path / "missing") is None


def test_a_dead_holder_is_no_run(tmp_path: Path) -> None:
    pid = _dead_pid()
    _hold(tmp_path, pid, f"{pid} morning-briefing\n")
    assert live_run(tmp_path) is None


def test_a_marker_left_by_another_holder_is_no_run(tmp_path: Path) -> None:
    """A run killed before its trap leaves its marker; the next lock holder (say, bootstrap) isn't that run."""
    _hold(tmp_path, os.getpid(), f"{_dead_pid()} morning-briefing\n")
    assert live_run(tmp_path) is None


@pytest.mark.parametrize("marker", ["", "garbage\n", "12x morning\n"])
def test_an_unreadable_marker_beside_a_live_lock_fails_closed(tmp_path: Path, marker: str) -> None:
    _hold(tmp_path, os.getpid(), marker)
    assert live_run(tmp_path) == LiveRun(pid=os.getpid(), mode="scheduled")


def test_a_mode_that_isnt_a_plain_slot_name_is_not_echoed(tmp_path: Path) -> None:
    _hold(tmp_path, os.getpid(), f"{os.getpid()} $(rm -rf ~)\n")
    assert live_run(tmp_path) == LiveRun(pid=os.getpid(), mode="scheduled")


def test_an_empty_lock_is_no_run(tmp_path: Path) -> None:
    _hold(tmp_path, os.getpid(), f"{os.getpid()} dreaming\n")
    (tmp_path / ".scout-logs" / ".scout-session.lock").write_text("", encoding="utf-8")
    assert live_run(tmp_path) is None


# ----- the commit hook ------------------------------------------------------------


@pytest.mark.parametrize(
    "env", [{}, {"SCOUT_MODE": ""}, {"SCOUT_READER": "owner"}, {"SCOUT_MODE": "", "SCOUT_READER": "owner"}]
)
def test_a_live_runs_commit_is_checked_as_scheduled_whatever_its_env(vault: Path, env: dict[str, str]) -> None:
    _hold(vault, os.getpid(), f"{os.getpid()} morning-briefing\n")
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, env)
    assert rc == 1 and "[privacy-widening]" in out and "local-only" in out
    assert "kb-lint: a scheduled run (morning-briefing) holds the session lock" in out
    (event,) = [e for e in _events(vault) if e["kind"] == "kb_lint.scheduled_by_lock"]
    assert event["payload"] == {"mode": "morning-briefing", "pid": os.getpid()}


def test_a_commit_already_marked_scheduled_leaves_no_trace(vault: Path) -> None:
    _hold(vault, os.getpid(), f"{os.getpid()} morning-briefing\n")
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, {"SCOUT_MODE": "morning-briefing"})
    assert rc == 1 and "[privacy-widening]" in out
    assert "holds the session lock" not in out
    assert [e for e in _events(vault) if e["kind"] == "kb_lint.scheduled_by_lock"] == []


def test_the_live_run_decides_the_privacy_check_failure_too(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(lint_privacy, "load_policy", boom)
    _hold(vault, os.getpid(), f"{os.getpid()} morning-briefing\n")
    path = vault / "knowledge-base/people/priya.md"
    path.write_text(path.read_text(encoding="utf-8") + "More notes.\n", encoding="utf-8")
    git(vault, "add", "--", "knowledge-base/people/priya.md")
    rc, out = _hook(vault, {})
    assert rc == 1 and "[privacy-check-failed]" in out and "WARNING" not in out


@pytest.mark.parametrize("holder", ["none", "dead", "bootstrap"])
def test_the_owners_commit_is_unchanged_when_no_run_is_live(vault: Path, holder: str) -> None:
    if holder == "dead":
        pid = _dead_pid()
        _hold(vault, pid, f"{pid} morning-briefing\n")
    elif holder == "bootstrap":
        _hold(vault, os.getpid(), None)
    _move_out_of_local_only(vault)
    rc, out = _hook(vault, {})
    assert rc == 0, out
    assert "holds the session lock" not in out


def test_a_commit_from_a_linked_worktree_finds_the_main_vaults_marker(vault: Path, tmp_path: Path) -> None:
    _hold(vault, os.getpid(), f"{os.getpid()} dreaming\n")
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
        "SCOUT_MODE= git commit -m x",
        "SCOUT_READER=owner scripts/git-safe-commit.sh 'x' notes/a.md",
        "unset SCOUT_MODE; git commit -m x",
        "unset -v SCOUT_READER && git commit -m x",
        "export SCOUT_MODE=",
        "export -n SCOUT_MODE",
        "declare +x SCOUT_READER",
        "env -i PATH=$PATH git commit -m x",
        "env --ignore-environment git commit -m x",
        "env - git commit -m x",
        "rm -f .scout-logs/.scout-session.lock.run",
        "printf '' > .scout-logs/.scout-session.lock",
    ],
)
def test_commands_that_change_the_run_env_or_its_marker_are_flagged(command: str) -> None:
    assert names_run_env(command)


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m 'nightly KB pass'",
        'echo "$SCOUT_MODE"',
        'echo "${SCOUT_READER:-autonomous}"',
        "scripts/git-safe-commit.sh 'notes' notes/a.md",
        "env | sort",
        "env LANG=C sort notes.txt",
        "scripts/scoutctl kb search environment",
    ],
)
def test_ordinary_commands_and_reads_of_the_run_env_are_not_flagged(command: str) -> None:
    assert not names_run_env(command)
