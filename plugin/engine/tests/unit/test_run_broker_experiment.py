"""The run broker's ``experiment`` verb and ``scoutctl lab experiment run`` (lab Task 11).

The broker runs an experiment for a sandboxed scheduled run only when the experiment's note is committed, isn't
vetoed at HEAD, and was added longer ago than the owner's notice; it clamps the cap to the owner's maximum and runs
at most the owner's number of experiments a run. The runner itself is replaced here (Task 10 tests it): these tests
check what reaches it and what the run reads back.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.errors import LabError
from scout.lab import paths, runner
from scout.sandbox import broker

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
SLUG, EID = "monitors", "E2"
T = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)  # when the fixture thread is committed
AFTER_NOTICE = T + timedelta(hours=7)
ARGV = ["python3", "run.py"]  # a script committed in E2's folder (R26)
REQ = {"v": broker.PROTOCOL, "verb": "experiment", "slug": SLUG, "eid": EID, "argv": ARGV}
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


def _note(vault: Path, eid: str = EID) -> Path:
    note = paths.experiment_note(vault, SLUG, eid)
    assert note is not None
    return note


def _set(vault: Path, key: str, value: str) -> None:
    """Rewrite one frontmatter field of E2's note in the worktree."""
    note = _note(vault)
    text, n = re.subn(rf"^{key}: .*$", f"{key}: {value}", note.read_text(encoding="utf-8"), count=1, flags=re.M)
    assert n == 1
    note.write_text(text, encoding="utf-8")


def _commit(vault: Path, at: datetime, message: str = "lab: monitors") -> None:
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", message, at=at)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = (tmp_path / "vault").resolve()
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(v, SLUG))
    folder = paths.experiment_dir(_note(v))
    folder.mkdir()
    (folder / "run.py").write_text("print('hi')\n", encoding="utf-8")
    (folder / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    (folder / "run.sh").chmod(0o755)
    _git(v, "init", "-q", "-b", "main")
    _commit(v, T)
    return v


class FakeRun:
    """Stands in for :func:`scout.lab.runner.run_experiment`: records each call, returns a set outcome."""

    def __init__(self, outcome: runner.RunOutcome | None = None, error: LabError | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcome = outcome or runner.RunOutcome(0, "result line\n", "progress line\n", 0.0, False)
        self.error = error

    def __call__(self, vault: Path, slug: str, eid: str, argv: Sequence[str], **kw: Any) -> runner.RunOutcome:
        self.calls.append({"vault": vault, "slug": slug, "eid": eid, "argv": list(argv), **kw})
        if self.error is not None:
            raise self.error
        return self.outcome


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    fake = FakeRun()
    monkeypatch.setattr(runner, "run_experiment", fake)
    return fake


def _broker(vault: Path, now: datetime = AFTER_NOTICE, **kw: Any) -> broker.Broker:
    return broker.Broker.for_vault(vault, dict(os.environ), clock=lambda: now, **kw)


def _owner_config(data: Mapping[str, Any]) -> None:
    folder = broker.owner_dir(os.environ)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / broker.CONFIG_NAME).write_text(json.dumps(data), encoding="utf-8")


# -- notice, veto, HEAD ------------------------------------------------------------------------------------------


def test_refuses_note_committed_too_recently(vault: Path, fake_run: FakeRun) -> None:
    reply = _broker(vault, now=T + timedelta(hours=1)).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and reply.kind == broker.REFUSED
    assert "notice" in reply.stderr
    assert fake_run.calls == []


def test_runs_once_the_notice_has_passed(vault: Path, fake_run: FakeRun) -> None:
    reply = _broker(vault, now=AFTER_NOTICE).handle(dict(REQ))
    assert reply.exit == 0, reply.stderr
    assert (reply.stdout, reply.stderr) == ("result line\n", "progress line\n")
    assert [(c["slug"], c["eid"], c["argv"]) for c in fake_run.calls] == [(SLUG, EID, ARGV)]
    assert fake_run.calls[0]["vault"] == vault and fake_run.calls[0]["timeout_s"] == 1800


def test_a_later_commit_to_the_note_restarts_the_notice(vault: Path, fake_run: FakeRun) -> None:
    _set(vault, "cost_cap_usd", "0.50")
    edited = T + timedelta(hours=6, minutes=30)
    _commit(vault, edited, "lab: E2 cap")
    reply = _broker(vault, now=AFTER_NOTICE).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "notice" in reply.stderr
    assert _broker(vault, now=edited + timedelta(hours=6, minutes=1)).handle(dict(REQ)).exit == 0
    assert len(fake_run.calls) == 1 and fake_run.calls[0]["cap_usd"] == pytest.approx(0.5)


def test_refuses_vetoed_at_head_even_if_worktree_says_planned(vault: Path, fake_run: FakeRun) -> None:
    _set(vault, "status", "vetoed")
    _commit(vault, T + timedelta(minutes=5), "lab: veto E2")
    _set(vault, "status", "planned")  # the run's uncommitted rewrite
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "vetoed" in reply.stderr
    assert fake_run.calls == []


def test_a_veto_is_sticky_even_when_planned_is_committed_on_top(vault: Path, fake_run: FakeRun) -> None:
    _set(vault, "status", "vetoed")
    _commit(vault, T + timedelta(minutes=5), "lab: veto E2")
    _set(vault, "status", "planned")
    _commit(vault, T + timedelta(minutes=10), "lab: E2 planned again")
    reply = _broker(vault, now=T + timedelta(days=2)).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "vetoed" in reply.stderr
    assert fake_run.calls == []


def test_a_veto_survives_renaming_the_note(vault: Path, fake_run: FakeRun) -> None:
    _set(vault, "status", "vetoed")
    _commit(vault, T + timedelta(minutes=5), "lab: veto E2")
    old = _note(vault)
    _git(vault, "mv", str(old.relative_to(vault)), str(old.with_name(f"{SLUG}-{EID}-again.md").relative_to(vault)))
    _set(vault, "status", "planned")
    _commit(vault, T + timedelta(minutes=10), "lab: E2 again")
    assert _note(vault).name == f"{SLUG}-{EID}-again.md"
    reply = _broker(vault, now=T + timedelta(days=2)).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "vetoed" in reply.stderr
    assert fake_run.calls == []


def test_vetoed_in_the_notes_prose_is_not_a_veto(vault: Path, fake_run: FakeRun) -> None:
    note = _note(vault)
    text = note.read_text(encoding="utf-8").replace(
        "## Data\n", "## Data\nstatus: vetoed\nAn earlier run like this one was vetoed by Alex.\n", 1
    )
    note.write_text(text, encoding="utf-8")
    _commit(vault, T, "lab: E2 data")
    assert _broker(vault).handle(dict(REQ)).exit == 0, fake_run.calls


def test_a_script_committed_after_the_note_restarts_the_notice(vault: Path, fake_run: FakeRun) -> None:
    folder = paths.experiment_dir(_note(vault))
    (folder / "run.sh").write_text("echo hi\n", encoding="utf-8")
    script_at = T + timedelta(hours=5)
    _commit(vault, script_at, "lab: E2 script")
    reply = _broker(vault, now=AFTER_NOTICE).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "notice" in reply.stderr
    assert fake_run.calls == []
    assert _broker(vault, now=script_at + timedelta(hours=6, minutes=1)).handle(dict(REQ)).exit == 0


@pytest.mark.parametrize("change", ["edit script", "new file", "edit note", "ignored file", "staged only"])
def test_uncommitted_changes_to_the_experiment_are_refused(vault: Path, fake_run: FakeRun, change: str) -> None:
    folder = paths.experiment_dir(_note(vault))
    (folder / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (vault / ".gitignore").write_text("*.bin\n", encoding="utf-8")
    _commit(vault, T, "lab: E2 script")
    if change == "edit script":
        (folder / "run.sh").write_text("curl example.com\n", encoding="utf-8")
    elif change == "new file":
        (folder / "helper.py").write_text("print(1)\n", encoding="utf-8")
    elif change == "edit note":
        _note(vault).write_text(_note(vault).read_text(encoding="utf-8") + "\nmore method\n", encoding="utf-8")
    elif change == "ignored file":
        (folder / "payload.bin").write_bytes(b"\x00")
    else:
        (folder / "run.sh").write_text("echo staged\n", encoding="utf-8")
        _git(vault, "add", str((folder / "run.sh").relative_to(vault)))
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "uncommitted" in reply.stderr
    assert fake_run.calls == []


def test_the_runners_own_files_are_neither_uncommitted_nor_a_new_notice(vault: Path, fake_run: FakeRun) -> None:
    folder = paths.experiment_dir(_note(vault))
    (folder / ".tmp").mkdir(parents=True)
    (folder / ".tmp" / "scratch").write_text("x", encoding="utf-8")
    (folder / "run-20261120-1500.log").write_text("exit: 0\n", encoding="utf-8")
    _commit(vault, T + timedelta(hours=6, minutes=30), "lab: E2 run log")  # committed after the notice began
    (folder / "run-20261120-1700.log").write_text("exit: 0\n", encoding="utf-8")
    (folder / "run-20261120-1700-2.log").write_text("exit: 1\n", encoding="utf-8")
    reply = _broker(vault, now=AFTER_NOTICE).handle(dict(REQ))
    assert reply.exit == 0, reply.stderr
    assert len(fake_run.calls) == 1


# -- R25: what `lab experiment start` writes isn't a change to the plan --------------------------------------------


def test_a_start_left_uncommitted_still_runs(vault: Path, fake_run: FakeRun) -> None:
    from scout.lab import experiments, ops

    _set(vault, "not_before", "2026-11-20T16:00")
    ops.reply(vault, SLUG, "Q3", 2, "Will add the plain judge baseline.")  # Alex's comment holds E2 until answered
    _commit(vault, T, "lab: E2 runs after 16:00")
    experiments.start(vault, SLUG, EID, now=AFTER_NOTICE)
    note = _note(vault).read_text(encoding="utf-8")
    assert "status: running" in note and "started: " in note
    reply = _broker(vault, now=AFTER_NOTICE).handle(dict(REQ))
    assert reply.exit == 0, reply.stderr
    assert len(fake_run.calls) == 1


def test_a_commit_changing_only_status_and_started_keeps_the_notice(vault: Path, fake_run: FakeRun) -> None:
    _set(vault, "status", "running")
    note = _note(vault)
    note.write_text(note.read_text(encoding="utf-8").replace("status: running\n", "status: running\nstarted: "
                    "2026-11-20T15:00\n", 1), encoding="utf-8")  # fmt: skip
    _commit(vault, T + timedelta(hours=5), "lab: start E2")
    reply = _broker(vault, now=AFTER_NOTICE).handle(dict(REQ))
    assert reply.exit == 0, reply.stderr
    assert len(fake_run.calls) == 1


@pytest.mark.parametrize("change", ["cap", "method", "cap with status"])
def test_a_commit_changing_the_plan_restarts_the_notice(vault: Path, fake_run: FakeRun, change: str) -> None:
    if change in ("cap", "cap with status"):
        _set(vault, "cost_cap_usd", "0.90")
    if change == "cap with status":
        _set(vault, "status", "running")
    if change == "method":
        note = _note(vault)
        note.write_text(note.read_text(encoding="utf-8").replace("## Method\n", "## Method\nAlso fetch URLs.\n", 1))
    _commit(vault, T + timedelta(hours=5), "lab: E2 edit")
    reply = _broker(vault, now=AFTER_NOTICE).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "notice" in reply.stderr
    assert fake_run.calls == []


def test_an_uncommitted_lifecycle_edit_with_any_other_change_is_refused(vault: Path, fake_run: FakeRun) -> None:
    _set(vault, "status", "running")
    _set(vault, "cost_cap_usd", "0.90")
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "uncommitted" in reply.stderr
    assert fake_run.calls == []


@pytest.mark.parametrize("status", ["done", "abandoned", "vetoed-ish"])
def test_the_worktree_note_must_be_planned_or_running(vault: Path, fake_run: FakeRun, status: str) -> None:
    _set(vault, "status", status)  # a lifecycle-only edit: not "uncommitted", but not a runnable status either
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "only a planned or running one runs" in reply.stderr
    assert fake_run.calls == []


def test_same_plan_compares_only_outside_status_and_started() -> None:
    base = "---\nid: E2\nstatus: planned\ncost_cap_usd: 1.00\n---\n## Method\nstatus: x\n"
    assert broker._same_plan(base, base.replace("status: planned", "status: running\nstarted: 2026-11-20T15:00"))
    assert not broker._same_plan(base, base.replace("cost_cap_usd: 1.00", "cost_cap_usd: 2.00"))
    assert not broker._same_plan(base, base.replace("## Method\nstatus: x", "## Method\nstatus: y"))  # body
    assert not broker._same_plan(base, base.replace("id: E2\n", "id: E2\n  status: nested\n"))
    assert broker._same_plan("no frontmatter", "no frontmatter")
    assert not broker._same_plan("no frontmatter", "no frontmatter!")


@pytest.mark.parametrize(
    ("rel", "owned"),
    [
        (".tmp", True),
        (".tmp/a/b", True),
        ("run-20261120-1700.log", True),
        ("run-20261120-1700-12.log", True),
        ("run-latest.log", False),
        ("sub/run-20261120-1700.log", False),
        ("run.sh", False),
        (".tmpfile", False),
    ],
)
def test_what_the_runner_owns(rel: str, owned: bool) -> None:
    assert runner.runner_owned(rel) is owned


def test_refuses_a_note_that_isnt_committed(vault: Path, fake_run: FakeRun) -> None:
    note = _note(vault)
    text = note.read_text(encoding="utf-8").replace("id: E2", "id: E3", 1)
    (note.parent / f"{SLUG}-E3-fresh.md").write_text(text, encoding="utf-8")
    reply = _broker(vault).handle({**REQ, "eid": "E3"})
    assert reply.exit == broker.EXIT_REFUSED and "HEAD" in reply.stderr
    assert fake_run.calls == []


def test_refuses_an_experiment_without_a_note(vault: Path, fake_run: FakeRun) -> None:
    reply = _broker(vault).handle({**REQ, "eid": "E9"})
    assert reply.exit == broker.EXIT_REFUSED and "no experiment note" in reply.stderr
    assert fake_run.calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"slug": "../monitors"},
        {"slug": "Monitors"},
        {"slug": 7},
        {"eid": "E0"},
        {"eid": "E2/../E1"},
        {"argv": []},
        {"argv": "/bin/sh"},
        {"argv": ["/bin/sh", 3]},
        {"argv": ["/bin/sh", "a\x00b"]},
        {"argv": ["/bin/sh", "line\nbreak"]},
        {"argv": ["x" * 4097]},
        {"argv": ["/bin/true"] * 65},
    ],
)
def test_requests_outside_the_rules_are_refused(vault: Path, fake_run: FakeRun, change: dict[str, Any]) -> None:
    reply = _broker(vault).handle({**REQ, **change})
    assert reply.exit == broker.EXIT_REFUSED and reply.kind == broker.REFUSED
    assert reply.stderr.startswith("scoutctl lab: ")
    assert fake_run.calls == []


def test_the_longest_allowed_argv_passes(vault: Path, fake_run: FakeRun) -> None:
    argv = ["python3", "run.py", *(["y" * 4096] * 62)]
    assert _broker(vault).handle({**REQ, "argv": argv}).exit == 0
    assert fake_run.calls[0]["argv"] == argv


# -- I1: file names git would quote ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ['a"b.py', "back\\slash.py", "new\nline.py", "tab\there.py", "caf\u00e9.py"])
def test_a_file_with_a_name_git_quotes_still_restarts_the_notice(vault: Path, fake_run: FakeRun, name: str) -> None:
    (paths.experiment_dir(_note(vault)) / name).write_text("print(1)\n", encoding="utf-8")
    _commit(vault, T + timedelta(hours=6, minutes=59), "lab: E2 helper")
    reply = _broker(vault, now=AFTER_NOTICE).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "notice" in reply.stderr
    assert fake_run.calls == []


def test_running_a_quoted_script_committed_within_the_notice_is_refused(vault: Path, fake_run: FakeRun) -> None:
    (paths.experiment_dir(_note(vault)) / 'x"y.py').write_text("print(1)\n", encoding="utf-8")
    _commit(vault, T + timedelta(hours=6, minutes=59), "lab: E2 script")
    reply = _broker(vault, now=AFTER_NOTICE).handle({**REQ, "argv": ["python3", 'x"y.py']})
    assert reply.exit == broker.EXIT_REFUSED and "notice" in reply.stderr
    assert fake_run.calls == []


def test_a_veto_under_a_quoted_name_survives_a_rename_to_a_clean_one(vault: Path, fake_run: FakeRun) -> None:
    folder = paths.experiment_dir(_note(vault))
    quoted = _note(vault).with_name(f'{SLUG}-{EID}-x"y.md')
    _git(vault, "mv", str(_note(vault).relative_to(vault)), str(quoted.relative_to(vault)))
    _git(vault, "mv", str(folder.relative_to(vault)), str(quoted.with_suffix("").relative_to(vault)))
    _commit(vault, T + timedelta(minutes=1), "lab: E2 renamed")
    _set(vault, "status", "vetoed")
    _commit(vault, T + timedelta(minutes=2), "lab: veto E2")
    clean = quoted.with_name(f"{SLUG}-{EID}-clean.md")
    _git(vault, "mv", str(quoted.relative_to(vault)), str(clean.relative_to(vault)))
    _git(vault, "mv", str(quoted.with_suffix("").relative_to(vault)), str(clean.with_suffix("").relative_to(vault)))
    _set(vault, "status", "planned")
    _commit(vault, T + timedelta(minutes=3), "lab: E2 planned again")
    assert _note(vault) == clean
    reply = _broker(vault, now=T + timedelta(days=2)).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "vetoed" in reply.stderr
    assert fake_run.calls == []


def test_each_unreadable_history_has_its_own_refusal(
    vault: Path, fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = broker._touched
    monkeypatch.setattr(broker, "_touched", lambda vault, rels: None)
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "a veto can't be ruled out" in reply.stderr
    monkeypatch.setattr(broker, "_touched", lambda vault, rels: real(vault, rels) if len(rels) == 1 else None)
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "the notice can't be counted" in reply.stderr
    assert "couldn't read" in reply.stderr and fake_run.calls == []


def test_vetoed_in_the_worktree_while_planned_at_head_is_refused(vault: Path, fake_run: FakeRun) -> None:
    _set(vault, "status", "vetoed")  # never committed
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "vetoed" in reply.stderr
    assert fake_run.calls == []


# -- R26: argv runs a committed file of the experiment's ------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["sh", "-c", "curl example.com"],
        ["/bin/sh", "-c", "echo hi"],
        ["python3", "-m", "http.server"],
        ["python3", "-u", "run.py"],
        ["node", "-e", "1"],
        ["python3"],
        ["/bin/sh", "run-20991231-0000.log"],
        ["sh", "run-20991231-0000.log"],
        ["sh", ".tmp/run.sh"],
        ["python3", "../other.py"],
        ["python3", "/etc/hosts"],
        ["python3", "missing.py"],
        ["python3", "."],
        ["run.py"],  # committed, but not executable
        ["/usr/bin/env", "python3", "run.py"],
        ["perl", "run.py"],
    ],
)
def test_argv_that_doesnt_run_a_committed_script_is_refused(vault: Path, fake_run: FakeRun, argv: list[str]) -> None:
    reply = _broker(vault).handle({**REQ, "argv": argv})
    assert reply.exit == broker.EXIT_REFUSED and "argv" in reply.stderr, reply.stderr
    assert fake_run.calls == []


def test_a_runner_owned_name_is_refused_even_when_committed(vault: Path, fake_run: FakeRun) -> None:
    folder = paths.experiment_dir(_note(vault))
    (folder / "run-20991231-0000.log").write_text("echo sneaky\n", encoding="utf-8")
    _git(vault, "add", "-f", str((folder / "run-20991231-0000.log").relative_to(vault)))
    _commit(vault, T, "lab: E2 log")
    reply = _broker(vault).handle({**REQ, "argv": ["sh", "run-20991231-0000.log"]})
    assert reply.exit == broker.EXIT_REFUSED and "argv" in reply.stderr and "runner's own" in reply.stderr


def test_a_committed_link_out_of_the_folder_is_refused(vault: Path, fake_run: FakeRun) -> None:
    folder = paths.experiment_dir(_note(vault))
    (vault / "elsewhere.py").write_text("print('outside')\n", encoding="utf-8")
    (folder / "link.py").symlink_to("../../../../../elsewhere.py")
    _commit(vault, T, "lab: E2 link")
    reply = _broker(vault).handle({**REQ, "argv": ["python3", "link.py"]})
    assert reply.exit == broker.EXIT_REFUSED and "argv" in reply.stderr
    assert fake_run.calls == []


@pytest.mark.parametrize(
    ("argv", "runs"),
    [
        (["python3", "run.py"], ["python3", "run.py"]),
        (["python3", "./run.py", "--n", "3", "-c", "x"], ["python3", "run.py", "--n", "3", "-c", "x"]),
        (["sh", "run.sh"], ["sh", "run.sh"]),
        (["bash", "run.sh", "data"], ["bash", "run.sh", "data"]),
        (["run.sh"], ["./run.sh"]),
        (["./run.sh", "-c", "anything"], ["./run.sh", "-c", "anything"]),
    ],
)
def test_a_committed_script_runs(vault: Path, fake_run: FakeRun, argv: list[str], runs: list[str]) -> None:
    reply = _broker(vault).handle({**REQ, "argv": argv})
    assert reply.exit == 0, reply.stderr
    assert fake_run.calls[0]["argv"] == runs


def test_an_uncommitted_new_script_is_still_refused_as_uncommitted(vault: Path, fake_run: FakeRun) -> None:
    (paths.experiment_dir(_note(vault)) / "new.py").write_text("print(2)\n", encoding="utf-8")
    reply = _broker(vault).handle({**REQ, "argv": ["python3", "new.py"]})
    assert reply.exit == broker.EXIT_REFUSED and "uncommitted" in reply.stderr
    assert fake_run.calls == []


def test_a_huge_committed_cap_is_refused_not_a_crash(vault: Path, fake_run: FakeRun) -> None:
    _set(vault, "cost_cap_usd", "1" + "0" * 400)
    _commit(vault, T, "lab: E2 cap")
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "cost_cap_usd" in reply.stderr


# -- owner limits ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("note_cap", "expected"), [("5.00", 1.0), ("0.25", 0.25)])
def test_cap_clamped_to_owner_max(vault: Path, fake_run: FakeRun, note_cap: str, expected: float) -> None:
    _set(vault, "cost_cap_usd", note_cap)
    _commit(vault, T + timedelta(minutes=1), "lab: E2 cap")
    assert _broker(vault).handle(dict(REQ)).exit == 0
    assert fake_run.calls[0]["cap_usd"] == pytest.approx(expected)


def test_a_cap_raised_in_the_worktree_is_refused_as_uncommitted(vault: Path, fake_run: FakeRun) -> None:
    _owner_config({"lab": {"max_cost_cap_usd": 10.0}})
    _set(vault, "cost_cap_usd", "8.00")  # raised in the worktree, never committed
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "uncommitted" in reply.stderr
    assert fake_run.calls == []


def test_a_committed_note_without_a_usable_cap_is_refused(vault: Path, fake_run: FakeRun) -> None:
    _set(vault, "cost_cap_usd", "lots")
    _commit(vault, T + timedelta(minutes=1), "lab: E2 cap")
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "cost_cap_usd" in reply.stderr
    assert fake_run.calls == []


def test_second_experiment_in_one_run_hits_limit(vault: Path, fake_run: FakeRun) -> None:
    b = _broker(vault)
    assert b.handle(dict(REQ)).exit == 0
    reply = b.handle(dict(REQ))
    assert reply.exit == broker.EXIT_LIMIT and reply.kind == broker.RUN_LIMIT
    assert reply.stderr == "scoutctl lab: this run has run its 1 experiment(s)\n"
    assert len(fake_run.calls) == 1 and b.experiments_run == 1


def test_the_owner_can_allow_more_experiments_a_run(vault: Path, fake_run: FakeRun) -> None:
    _owner_config({"lab": {"experiments_per_run": 2, "timeout_s": 90}})
    b = _broker(vault)
    assert [b.handle(dict(REQ)).exit for _ in range(3)] == [0, 0, broker.EXIT_LIMIT]
    assert [c["timeout_s"] for c in fake_run.calls] == [90, 90]
    assert "its 2 experiment(s)" in b.handle(dict(REQ)).stderr


def test_a_runner_refusal_is_answered_and_still_uses_the_runs_allowance(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    why = "E2 is planned, not running; start it before running its script"
    monkeypatch.setattr(runner, "run_experiment", FakeRun(error=LabError(why)))
    b = _broker(vault)
    reply = b.handle(dict(REQ))
    assert (reply.exit, reply.stderr) == (2, f"scoutctl lab: {why}\n")
    assert b.experiments_run == 1


def test_the_runners_exit_and_output_come_back(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(runner.RunOutcome(3, "partial\n", "gave up\n", 0.75, True))
    monkeypatch.setattr(runner, "run_experiment", fake)
    reply = _broker(vault).handle(dict(REQ))
    assert (reply.exit, reply.stdout, reply.stderr) == (3, "partial\n", "gave up\n")


def test_lab_limits_come_from_the_owner_file_and_bad_values_mean_the_default(tmp_path: Path) -> None:
    env = {"HOME": str(tmp_path)}
    assert broker.load_lab_limits(env) == broker.LabLimits()
    assert broker.LabLimits() == broker.LabLimits(1, 1.0, 6, 1800)
    folder = broker.owner_dir(env)
    folder.mkdir(parents=True)
    (folder / broker.CONFIG_NAME).write_text(
        json.dumps({"lab": {"experiments_per_run": 3, "max_cost_cap_usd": 2.5, "notice_hours": 0, "timeout_s": 600}})
    )
    assert broker.load_lab_limits(env) == broker.LabLimits(3, 2.5, 0, 600)
    bad = {"experiments_per_run": 1.5, "max_cost_cap_usd": "1", "notice_hours": -1, "timeout_s": True}
    (folder / broker.CONFIG_NAME).write_text(json.dumps({"lab": bad}))
    assert broker.load_lab_limits(env) == broker.LabLimits()
    (folder / broker.CONFIG_NAME).write_text('{"lab": {"max_cost_cap_usd": 1e999, "notice_hours": NaN}}')
    assert broker.load_lab_limits(env) == broker.LabLimits()
    (folder / broker.CONFIG_NAME).write_text(
        '{"lab": {"timeout_s": 1' + "0" * 400 + ', "experiments_per_run": 1' + "0" * 400 + "}}"
    )
    assert broker.load_lab_limits(env) == broker.LabLimits()  # too big for a float: the default, not OverflowError
    (folder / broker.CONFIG_NAME).write_text("not json")
    assert broker.load_lab_limits(env) == broker.LabLimits()
    (folder / broker.CONFIG_NAME).write_text(json.dumps({"lab": {"timeout_s": 60}}))
    assert broker.Broker.for_vault(tmp_path, env).lab_limits == broker.LabLimits(timeout_s=60)


# -- the verb's place in the broker ------------------------------------------------------------------------------


def test_unknown_verb_still_refused(vault: Path, fake_run: FakeRun) -> None:
    reply = _broker(vault).handle({"v": broker.PROTOCOL, "verb": "experiments"})
    assert reply.exit == broker.EXIT_REFUSED and reply.kind == broker.REFUSED
    assert "unknown verb" in reply.stderr
    assert "experiment" in broker.VERBS and fake_run.calls == []


def test_experiment_requests_are_never_queued(vault: Path, fake_run: FakeRun) -> None:
    with pytest.raises(ValueError):
        broker.enqueue(vault, "experiment", dict(REQ))
    planted = vault / broker.QUEUE_DIR / "experiment"
    planted.mkdir(parents=True)
    (planted / "01PLANTED.json").write_text(json.dumps({"v": 1, **REQ, "queued_at": T.isoformat()}))
    assert broker.pending(vault) == 0
    result = broker.drain(vault, env=dict(os.environ), broker=_broker(vault))
    assert (result.done, result.failed) == (0, 0) and fake_run.calls == []


# -- the client --------------------------------------------------------------------------------------------------


def test_client_without_broker_in_scheduled_mode_returns_4(
    vault: Path, fake_run: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"SCOUT_MODE": "scheduled", "SCOUT_DATA_DIR": str(vault), "HOME": os.environ["HOME"]}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == 4
    assert "experiment not run: experiments need the run broker" in capsys.readouterr().err
    assert fake_run.calls == []


def test_client_whose_broker_doesnt_answer_returns_4(
    vault: Path, tmp_path: Path, fake_run: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {broker.ENV_SOCKET: str(tmp_path / "gone.sock"), "SCOUT_MODE": "scheduled", "SCOUT_DATA_DIR": str(vault)}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == 4
    assert "experiment not run: this run's broker didn't answer" in capsys.readouterr().err
    assert fake_run.calls == []


def test_client_waits_for_the_owners_timeout_plus_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    _owner_config({"lab": {"timeout_s": 100}})
    seen: dict[str, Any] = {}

    def fake_call(sock: str, req: Mapping[str, Any], *, timeout: float) -> broker.Reply:
        seen.update(sock=sock, req=dict(req), timeout=timeout)
        return broker.Reply(0, "", "done")

    monkeypatch.setattr(broker, "call", fake_call)
    env = {**os.environ, broker.ENV_SOCKET: "/tmp/scout-broker.x/b.sock"}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == 0
    assert seen["timeout"] == 160
    assert seen["req"] == REQ


def test_owner_session_runs_directly_with_owner_limits(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeRun(runner.RunOutcome(7, "direct out\n", "direct err\n", 0.0, False))
    monkeypatch.setattr(runner, "run_experiment", fake)
    _set(vault, "cost_cap_usd", "5.00")
    _commit(vault, T + timedelta(minutes=1), "lab: E2 cap")
    env = {"SCOUT_DATA_DIR": str(vault), "HOME": os.environ["HOME"], "PATH": os.environ.get("PATH", "")}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == 7
    out = capsys.readouterr()
    assert out.out == "direct out\n" and out.err == "direct err\n"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["vault"] == vault and call["cap_usd"] == pytest.approx(1.0) and call["timeout_s"] == 1800
    assert call["env"] == env


def test_owner_session_still_respects_a_veto(
    vault: Path, fake_run: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    _set(vault, "status", "vetoed")
    _commit(vault, T + timedelta(minutes=1), "lab: veto E2")
    env = {"SCOUT_DATA_DIR": str(vault), "HOME": os.environ["HOME"]}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == broker.EXIT_REFUSED
    assert "vetoed" in capsys.readouterr().err
    assert fake_run.calls == []


@pytest.fixture
def broker_dir() -> Iterator[Path]:
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="scout-broker.", dir="/tmp")).resolve()
    d.chmod(0o700)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def keep_signals() -> Iterator[None]:
    import signal

    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    yield
    for s, handler in saved.items():
        signal.signal(s, handler)


def test_the_client_runs_its_experiment_through_the_served_broker(
    vault: Path,
    broker_dir: Path,
    fake_run: FakeRun,
    keep_signals: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(broker, "PARENT_POLL_S", 0.2)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))  # serve() sets it; this puts it back afterwards
    parent = subprocess.Popen(["/bin/sleep", "60"])
    served = _broker(vault, run_id=broker_dir.name)
    sock = str(broker_dir / broker.SOCKET_NAME)
    env = {**os.environ, broker.ENV_SOCKET: sock, "SCOUT_MODE": "scheduled"}
    results: list[int] = []

    def client() -> None:
        try:
            assert broker.wait_for(Path(sock), timeout=20)
            results.append(broker.client_experiment(SLUG, EID, ARGV, env=env))
            results.append(broker.client_experiment(SLUG, EID, ARGV, env=env))
        finally:
            parent.kill()
            parent.wait()

    t = threading.Thread(target=client, daemon=True)
    t.start()
    assert broker.serve(vault, broker_dir, parent=parent.pid, broker=served) == 0
    t.join(30)
    assert results == [0, broker.EXIT_LIMIT]
    assert len(fake_run.calls) == 1
    out = capsys.readouterr()
    assert "result line" in out.out and "progress line" in out.err
    assert "this run has run its 1 experiment(s)" in out.err
    assert "broker: experiment -> exit 0" in out.out


# -- the CLI -----------------------------------------------------------------------------------------------------


def test_the_cli_hands_the_command_after_the_separator_to_the_client(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_client(slug: str, eid: str, argv: Sequence[str], env: Mapping[str, str] | None = None) -> int:
        seen.update(slug=slug, eid=eid, argv=list(argv), vault=(env or {}).get("SCOUT_DATA_DIR"))
        return 3

    monkeypatch.setattr(broker, "client_experiment", fake_client)
    result = CliRunner().invoke(
        cli.app, ["lab", "--vault", str(vault), "experiment", "run", SLUG, EID, "--", "/bin/sh", "-c", "echo --x"]
    )
    assert result.exit_code == 3, result.output
    assert seen == {"slug": SLUG, "eid": EID, "argv": ["/bin/sh", "-c", "echo --x"], "vault": str(vault)}
