"""The run broker's experiment checks where git or the files fail, and the client's refusals before any broker.

Each failure is refused (exit 2) or not run (exit 4), never run: the broker can't tell, so it doesn't. No sandbox.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.lab import paths, runner
from scout.sandbox import broker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
SLUG, EID = "summary-drift", "E2"
T = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
AFTER_NOTICE = T + timedelta(hours=7)
REQ = {"slug": SLUG, "eid": EID, "argv": ["python3", "run.py"]}
GIT_ID = {
    "GIT_AUTHOR_NAME": "Alex",
    "GIT_AUTHOR_EMAIL": "alex@example.com",
    "GIT_COMMITTER_NAME": "Alex",
    "GIT_COMMITTER_EMAIL": "alex@example.com",
}


def _git(vault: Path, *args: str, at: datetime | None = None) -> str:
    env = {**os.environ, **GIT_ID, "GIT_CONFIG_NOSYSTEM": "1"}
    if at is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = at.isoformat()
    return subprocess.run(["git", "-C", str(vault), *args], env=env, check=True, capture_output=True, text=True).stdout


def _note(vault: Path) -> Path:
    note = paths.experiment_note(vault, SLUG, EID)
    assert note is not None
    return note


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """The fixture thread committed at T, the hold on Q3 answered, E2 running in the worktree: it validates."""
    from scout.lab import ops

    v = (tmp_path / "vault").resolve()
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(v, SLUG))
    folder = paths.experiment_dir(_note(v))
    folder.mkdir()
    (folder / "run.py").write_text("print('hi')\n", encoding="utf-8")
    ops.reply(v, SLUG, "Q3", 2, "Will report the false alarms.", now=T)
    _git(v, "init", "-q", "-b", "main")
    _git(v, "add", "-A")
    _git(v, "commit", "-qm", "lab: summary-drift", at=T)
    note = _note(v)
    note.write_text(note.read_text("utf-8").replace("status: planned", "status: running", 1), "utf-8")
    return v


def _validate(vault: Path) -> tuple[str, str, list[str], float, str, broker.ExperimentPlan]:
    return broker.validate_experiment(dict(REQ), vault, broker.LabLimits(), AFTER_NOTICE, dict(os.environ))


def test_the_fixture_validates(vault: Path) -> None:
    slug, eid, argv, cap, _commit, _plan = _validate(vault)
    assert (slug, eid, argv, cap) == (SLUG, EID, ["python3", "run.py"], 1.0)


def _refused(vault: Path, match: str) -> None:
    with pytest.raises(broker.Refused, match=match) as info:
        _validate(vault)
    assert info.value.exit_code == broker.EXIT_REFUSED


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_a_note_it_cant_read_is_refused(vault: Path) -> None:
    note = _note(vault)
    note.chmod(0o000)
    try:
        _refused(vault, "cannot read")
    finally:
        note.chmod(0o644)


def test_git_status_failing_is_refused(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(broker, "_uncommitted", lambda *_a: None)
    _refused(vault, "git status failed")


def test_no_commit_holding_the_note_is_refused(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = broker._touched
    calls: list[int] = []

    def empty_second(v: Path, rels: list[str]) -> object:
        calls.append(1)
        return real(v, rels) if len(calls) == 1 else []  # the veto history reads; the notice finds nothing

    monkeypatch.setattr(broker, "_touched", empty_second)
    _refused(vault, "no commit holds")


def test_history_git_cant_read_is_refused(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = broker._touched
    calls: list[int] = []

    def none_second(v: Path, rels: list[str]) -> object:
        calls.append(1)
        return real(v, rels) if len(calls) == 1 else None

    monkeypatch.setattr(broker, "_touched", none_second)
    _refused(vault, "history; the notice can't be counted")


def test_a_committed_script_whose_worktree_copy_leads_out_is_refused(vault: Path, tmp_path: Path) -> None:
    script = paths.experiment_dir(_note(vault)) / "run.py"
    outside = tmp_path / "elsewhere.py"
    outside.write_text("print('elsewhere')\n", encoding="utf-8")
    script.unlink()
    script.symlink_to(outside)  # uncommitted, but a link: R26 checks where it leads
    with pytest.raises(broker.Refused):
        _validate(vault)
    exp_rel = paths.experiment_dir(_note(vault)).relative_to(vault).as_posix()
    with pytest.raises(broker.Refused, match="leads out of the experiment's folder"):
        broker._committed_command(vault, exp_rel, ["python3", "run.py"], runner.runner_owned)


def _scripted(outputs: dict[str, str | None]) -> Callable[..., str | None]:
    """A stand-in ``_vault_git`` answering by the git subcommand."""

    def git(_vault: Path, *args: str) -> str | None:
        return outputs.get(args[0])

    return git


def test_touched_fails_closed_on_any_unexpected_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sha = "a" * 40
    for outputs in (
        {"log": None},
        {"log": "not-a-time " + sha + "\n"},
        {"log": "1700000000 " + sha + "\n", "diff-tree": None},
    ):
        monkeypatch.setattr(broker, "_vault_git", _scripted(outputs))
        assert broker._touched(tmp_path, ["x"]) is None, outputs
    monkeypatch.setattr(broker, "_vault_git", _scripted({"log": f"1700000000 {sha}\n", "diff-tree": "a.md\0b.md\0"}))
    assert broker._touched(tmp_path, ["x"]) == [(1700000000, sha, ["a.md", "b.md"])]


def test_uncommitted_reads_renames_and_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(broker, "_vault_git", _scripted({"rev-parse": None, "status": ""}))
    assert broker._uncommitted(tmp_path, ["x"]) is None
    status = "R  sub/new.md\0sub/old.md\0 M sub/edit.md\0?? sub/dir/\0x\0"
    monkeypatch.setattr(broker, "_vault_git", _scripted({"rev-parse": "sub/\n", "status": status}))
    assert broker._uncommitted(tmp_path, ["x"]) == ["old.md", "new.md", "edit.md", "dir"]


def test_vault_git_that_cant_start_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_a: object, **_k: object) -> object:
        raise OSError("no git")

    monkeypatch.setattr(broker.subprocess, "run", broken)
    assert broker._vault_git(tmp_path, "status") is None


def test_same_plan_without_a_closed_frontmatter_compares_exactly() -> None:
    assert broker._same_plan("---\nstatus: a\n", "---\nstatus: a\n") is True
    assert broker._same_plan("---\nstatus: a\n", "---\nstatus: b\n") is False
    assert broker._same_plan("---\nstatus: a\n---\nx\n", "---\nstatus: b\nstarted: y\n---\nx\n") is True


def test_a_note_without_frontmatter_counts_as_private(vault: Path) -> None:
    front, note = paths.front_page(vault, SLUG), _note(vault)
    text = front.read_text("utf-8")
    assert broker._private_at(SLUG, front, note, text, "# no frontmatter\n") == (
        "the experiment note at HEAD is private or unparseable; it"
    )


def test_held_skips_a_page_it_couldnt_read() -> None:
    assert broker._held(SLUG, EID, [("at HEAD", None)], []) is None


def test_the_client_refuses_a_timeout_under_a_second(capsys: pytest.CaptureFixture[str]) -> None:
    assert broker.client_experiment(SLUG, EID, ["sh", "x.sh"], env={"HOME": "/nonexistent"}, timeout_s=0) == 2
    assert capsys.readouterr().err.strip() == "experiment refused: the timeout must be at least 1 second"


def test_a_broker_that_doesnt_answer_runs_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env = {"HOME": str(tmp_path), broker.ENV_SOCKET: str(tmp_path / "no-broker.sock")}
    assert broker.client_experiment(SLUG, EID, ["sh", "x.sh"], env=env, timeout_s=5) == broker.EXIT_NOT_RUN
    assert capsys.readouterr().err.strip().splitlines()[-1] == "experiment not run: this run's broker didn't answer"
