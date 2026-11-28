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
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.errors import LabError
from scout.lab import paths, runner
from scout.lab.model import ModelRef
from scout.sandbox import broker, profile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "lab"
SLUG, EID = "summary-drift", "E2"
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


def _commit(vault: Path, at: datetime, message: str = "lab: summary-drift") -> None:
    _git(vault, "add", "-A")
    _git(vault, "commit", "-qm", message, at=at)


def _answer_the_hold(vault: Path) -> None:
    """Scout's reply to the fixture's unanswered alex comment on Q3, which holds E2 (W3: the broker checks holds)."""
    from scout.lab import ops

    ops.reply(vault, SLUG, "Q3", 2, "Will report the false alarms.", now=T)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = (tmp_path / "vault").resolve()
    shutil.copytree(FIXTURES / SLUG, paths.thread_dir(v, SLUG))
    folder = paths.experiment_dir(_note(v))
    folder.mkdir()
    (folder / "run.py").write_text("print('hi')\n", encoding="utf-8")
    (folder / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    (folder / "run.sh").chmod(0o755)
    _answer_the_hold(v)
    _git(v, "init", "-q", "-b", "main")
    _commit(v, T)
    _set(v, "status", "running")  # what `lab experiment start` writes, left uncommitted (R25, R31)
    return v


@pytest.fixture(autouse=True)
def experiments_on() -> None:
    """The owner's switch on, in the test's HOME: it is off by default (W3), and most tests here are about what
    happens once it's on. The switch's own tests (at the end) rewrite the config."""
    _owner_config({})


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


def _owner_config(data: Mapping[str, Any], env: Mapping[str, str] | None = None) -> None:
    """The owner's config.json in ``env``'s HOME (default the test's), with experiments switched on unless ``data``
    says otherwise."""
    folder = broker.owner_dir(os.environ if env is None else env)
    folder.mkdir(parents=True, exist_ok=True)
    lab = {"experiments_enabled": True, **data.get("lab", {})}
    (folder / broker.CONFIG_NAME).write_text(json.dumps({**data, "lab": lab}), encoding="utf-8")


# -- private threads fail closed ---------------------------------------------------------------------------------


def _flip_private(path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace("visibility: normal", "visibility: private"), "utf-8")


@pytest.mark.parametrize("where", ["head", "worktree"])
@pytest.mark.parametrize("what", ["note", "thread"])
def test_broker_refuses_a_private_note_or_thread(vault: Path, fake_run: FakeRun, what: str, where: str) -> None:
    target = _note(vault) if what == "note" else paths.front_page(vault, SLUG)
    original = target.read_text(encoding="utf-8")
    _flip_private(target)
    if where == "head":
        _commit(vault, T + timedelta(minutes=1), "lab: private")
        target.write_text(original, "utf-8")  # the worktree says normal again
    reply = _broker(vault).handle(dict(REQ))
    assert (reply.exit, reply.kind) == (broker.EXIT_REFUSED, broker.REFUSED)
    assert "private" in reply.stderr
    assert fake_run.calls == []


@pytest.mark.parametrize("value", ["Private", "PRIVATE", "secret"])
@pytest.mark.parametrize("what", ["note", "thread"])
def test_broker_refuses_private_spelled_otherwise(vault: Path, fake_run: FakeRun, what: str, value: str) -> None:
    """The privacy layer's parser reads the value: any visibility but normal is private (#761 review item 4)."""
    target = _note(vault) if what == "note" else paths.front_page(vault, SLUG)
    target.write_text(target.read_text(encoding="utf-8").replace("visibility: normal", f"visibility: {value}"), "utf-8")
    _commit(vault, T + timedelta(minutes=1), "lab: private")
    reply = _broker(vault).handle(dict(REQ))
    assert (reply.exit, reply.kind) == (broker.EXIT_REFUSED, broker.REFUSED)
    assert "private" in reply.stderr
    assert fake_run.calls == []


def test_broker_refuses_a_front_page_it_cannot_read(vault: Path, fake_run: FakeRun) -> None:
    front = paths.front_page(vault, SLUG)
    front.write_bytes(b"\xff\xfe not utf-8")  # unreadable in the worktree
    reply = _broker(vault).handle(dict(REQ))
    assert (reply.exit, reply.kind) == (broker.EXIT_REFUSED, broker.REFUSED)
    assert "private" in reply.stderr and "front page" in reply.stderr
    assert fake_run.calls == []


def test_broker_refuses_a_front_page_missing_at_the_commit(vault: Path, fake_run: FakeRun) -> None:
    front = paths.front_page(vault, SLUG)
    _git(vault, "rm", "-q", "--cached", front.relative_to(vault).as_posix())
    _git(vault, "commit", "-qm", "lab: drop the front page", at=T + timedelta(minutes=1))
    reply = _broker(vault).handle(dict(REQ))  # still on disk in the worktree
    assert (reply.exit, reply.kind) == (broker.EXIT_REFUSED, broker.REFUSED)
    assert "private" in reply.stderr and "front page at HEAD" in reply.stderr
    assert fake_run.calls == []


@pytest.mark.parametrize("where", ["head", "worktree"])
def test_broker_refuses_a_front_page_with_broken_frontmatter(vault: Path, fake_run: FakeRun, where: str) -> None:
    front = paths.front_page(vault, SLUG)
    original = front.read_text(encoding="utf-8")
    front.write_text(original.replace("visibility: normal", "visibility: [unclosed", 1), "utf-8")
    if where == "head":
        _commit(vault, T + timedelta(minutes=1), "lab: broken front page")
        front.write_text(original, "utf-8")
    reply = _broker(vault).handle(dict(REQ))
    assert (reply.exit, reply.kind) == (broker.EXIT_REFUSED, broker.REFUSED)
    assert "private" in reply.stderr
    assert fake_run.calls == []


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
    from scout.lab import experiments

    _set(vault, "status", "planned")  # not started yet: this test starts it
    _set(vault, "not_before", "2026-11-20T16:00")
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


@pytest.mark.parametrize("status", ["planned", "done", "abandoned", "vetoed-ish"])
def test_the_worktree_note_must_be_running(vault: Path, fake_run: FakeRun, status: str) -> None:
    """R31: the broker asks for what the runner asks for, so a refusal is the broker's, before anything runs."""
    _set(vault, "status", status)  # a lifecycle-only edit: not "uncommitted", but not a runnable status either
    b = _broker(vault)
    reply = b.handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and "only a running experiment runs" in reply.stderr
    assert f"scoutctl lab experiment start {SLUG} {EID}" in reply.stderr
    assert fake_run.calls == []
    assert b.experiments_run == 0  # a refusal keeps the run's allowance
    _set(vault, "status", "running")
    assert b.handle(dict(REQ)).exit == 0 and len(fake_run.calls) == 1


def test_the_validated_commit_is_what_the_runner_gets(vault: Path, fake_run: FakeRun) -> None:
    assert _broker(vault).handle(dict(REQ)).exit == 0
    head = _git(vault, "rev-parse", "HEAD").strip()
    assert fake_run.calls[0]["commit"] == head


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
        {"slug": "../summary-drift"},
        {"slug": "Summary-Drift"},
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
    assert reply.stderr.startswith("experiment refused: ")
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
    assert reply.stderr == "experiment refused: this run has run its 1 experiment\n"
    assert len(fake_run.calls) == 1 and b.experiments_run == 1


def test_the_owner_can_allow_more_experiments_a_run(vault: Path, fake_run: FakeRun) -> None:
    _owner_config({"lab": {"experiments_per_run": 2, "timeout_s": 90}})
    b = _broker(vault)
    assert [b.handle(dict(REQ)).exit for _ in range(3)] == [0, 0, broker.EXIT_LIMIT]
    assert [c["timeout_s"] for c in fake_run.calls] == [90, 90]
    assert "its 2 experiments" in b.handle(dict(REQ)).stderr


def test_a_runner_refusal_is_answered_and_still_uses_the_runs_allowance(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    why = "E2 is planned, not running; start it before running its script"
    monkeypatch.setattr(runner, "run_experiment", FakeRun(error=LabError(why)))
    b = _broker(vault)
    reply = b.handle(dict(REQ))
    assert (reply.exit, reply.stderr) == (2, f"experiment refused: {why}\n")
    assert b.experiments_run == 1


def test_the_runners_exit_and_output_come_back(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRun(runner.RunOutcome(3, "partial\n", "gave up\n", 0.75, True))
    monkeypatch.setattr(runner, "run_experiment", fake)
    reply = _broker(vault).handle(dict(REQ))
    # The outcome timed out, so the reply names that just before the status line (final review, item 10).
    assert (reply.exit, reply.stdout, reply.stderr) == (3, "partial\n", "gave up\nkilled: timed_out\n")


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


def test_the_owners_totals_default_and_bad_values_mean_the_default(tmp_path: Path) -> None:
    """W4: ``max_processes`` (default 64) and ``max_total_bytes`` (default 512 MiB) are whole numbers above 0."""
    env = {"HOME": str(tmp_path)}
    defaults = broker.LabLimits()
    assert (defaults.max_processes, defaults.max_total_bytes, defaults.max_open_files) == (64, 512 * 1024 * 1024, 1024)
    folder = broker.owner_dir(env)
    folder.mkdir(parents=True)
    totals = {"max_processes": 8, "max_total_bytes": 1 << 34, "max_open_files": 4096}
    (folder / broker.CONFIG_NAME).write_text(json.dumps({"lab": totals}))
    limits = broker.load_lab_limits(env)
    assert (limits.max_processes, limits.max_total_bytes, limits.max_open_files) == (8, 1 << 34, 4096)
    for bad in (0, -1, 1.5, True, "8", None, [8], 1 << 50):
        lab = {"max_processes": bad, "max_total_bytes": bad, "max_open_files": bad}
        (folder / broker.CONFIG_NAME).write_text(json.dumps({"lab": lab}))
        assert broker.load_lab_limits(env) == broker.LabLimits(), bad
    (folder / broker.CONFIG_NAME).write_text('{"lab": {"max_processes": 1' + "0" * 400 + "}}")
    assert broker.load_lab_limits(env) == broker.LabLimits()


def test_the_broker_passes_the_owners_totals_to_the_runner(vault: Path, fake_run: FakeRun) -> None:
    _owner_config({"lab": {"max_processes": 12, "max_total_bytes": 4096, "max_open_files": 512}})
    assert _broker(vault).handle(dict(REQ)).exit == 0
    call = fake_run.calls[0]
    assert (call["max_processes"], call["max_total_bytes"], call["max_open_files"]) == (12, 4096, 512)


def test_the_owners_own_session_passes_the_owners_totals_too(vault: Path, fake_run: FakeRun) -> None:
    _owner_config({"lab": {"max_processes": 5, "max_total_bytes": 2048, "max_open_files": 70000}})  # files: too many
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == 0
    call = fake_run.calls[0]
    assert (call["max_processes"], call["max_total_bytes"], call["max_open_files"]) == (5, 2048, 1024)


def test_an_over_cap_run_still_reports_ran_with_its_exit(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run killed for its bytes is a run: the status line is ``experiment ran: exit <n>``."""
    killed = runner.RunOutcome(-9, "", "", 0.0, False, over_bytes=True)
    monkeypatch.setattr(runner, "run_experiment", FakeRun(killed))
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == -9
    assert capsys.readouterr().err.splitlines()[-1] == "experiment ran: exit -9"


def _plan_budget(vault: Path, budget: int) -> None:
    """Commit a ``disk_budget_bytes`` into E2's note, at T so the notice has passed by AFTER_NOTICE."""
    note = _note(vault)
    text = note.read_text(encoding="utf-8").replace(
        "status: running\n", f"status: running\ndisk_budget_bytes: {budget}\n", 1
    )
    assert f"disk_budget_bytes: {budget}" in text
    note.write_text(text, encoding="utf-8")
    _commit(vault, T, "lab: a disk budget")


def test_broker_runs_with_the_plans_budget(vault: Path, fake_run: FakeRun) -> None:
    """The runner gets the plan's budget, clamped to the owner's maximum, never the owner's maximum itself."""
    _owner_config({"lab": {"max_total_bytes": 1 << 30, "disk_free_floor_bytes": 0, "max_memory_bytes": 123456}})
    _plan_budget(vault, 2 << 20)
    assert _broker(vault).handle(dict(REQ)).exit == 0
    call = fake_run.calls[0]
    assert call["max_total_bytes"] == 2 << 20
    assert (call["disk_free_floor_bytes"], call["max_memory_bytes"]) == (0, 123456)


@pytest.mark.parametrize("gpu", [True, False])
def test_the_owners_gpu_switch_reaches_the_runner_attached(vault: Path, fake_run: FakeRun, gpu: bool) -> None:
    """lab.gpu reaches the runner (and so the profile) from the run's broker verb and the owner's own session."""
    _owner_config({"lab": {"gpu": gpu, "disk_free_floor_bytes": 0}})
    assert _broker(vault).handle(dict(REQ)).exit == 0
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == 0
    assert [c["gpu"] for c in fake_run.calls] == [gpu, gpu]


def test_a_plan_budget_over_the_owners_maximum_is_clamped(vault: Path, fake_run: FakeRun) -> None:
    _owner_config({"lab": {"max_total_bytes": 4096, "disk_free_floor_bytes": 0}})
    _plan_budget(vault, 1 << 30)
    assert _broker(vault).handle(dict(REQ)).exit == 0
    assert fake_run.calls[0]["max_total_bytes"] == 4096


def test_the_owners_own_session_passes_the_plans_budget_and_floors(vault: Path, fake_run: FakeRun) -> None:
    _owner_config({"lab": {"disk_free_floor_bytes": 0, "max_memory_bytes": 99}})
    _plan_budget(vault, 3 << 20)
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == 0
    call = fake_run.calls[0]
    assert (call["max_total_bytes"], call["disk_free_floor_bytes"], call["max_memory_bytes"]) == (3 << 20, 0, 99)


def test_the_broker_refuses_to_start_below_the_floor_and_keeps_the_allowance(
    vault: Path, fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner_config({"lab": {"disk_free_floor_bytes": 1000}})
    _plan_budget(vault, 500)
    monkeypatch.setattr(runner, "free_bytes", lambda _vault: 1499)  # free - budget = 999 < floor
    b = _broker(vault)
    reply = b.handle(dict(REQ))
    assert (reply.exit, reply.stderr) == (
        broker.EXIT_NOT_RUN,
        "experiment not run: free disk would fall below the floor\n",
    )
    assert fake_run.calls == [] and b.experiments_run == 0
    monkeypatch.setattr(runner, "free_bytes", lambda _vault: 1500)  # exactly the floor is allowed
    assert b.handle(dict(REQ)).exit == 0 and b.experiments_run == 1


def test_a_floor_the_runner_hits_after_the_broker_check_is_not_run_either(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "run_experiment", FakeRun(error=runner.DiskFloorError()))
    b = _broker(vault)
    reply = b.handle(dict(REQ))
    assert (reply.exit, reply.stderr) == (
        broker.EXIT_NOT_RUN,
        "experiment not run: free disk would fall below the floor\n",
    )
    assert b.experiments_run == 0


def test_the_owners_own_session_below_the_floor_is_not_run(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner, "run_experiment", FakeRun(error=runner.DiskFloorError()))
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == broker.EXIT_NOT_RUN
    assert capsys.readouterr().err.splitlines()[-1] == "experiment not run: free disk would fall below the floor"


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
    assert out.out == "direct out\n" and out.err == "direct err\nexperiment ran: exit 7\n"
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
    assert "this run has run its 1 experiment" in out.err
    assert "broker: experiment -> exit 0" in out.out


# -- against the kernel: the real runner ---------------------------------------------------------------------------

kernel = pytest.mark.skipif(not profile.available(), reason="needs sandbox-exec")
# A committed script that says which copy of it runs and whether it can rewrite that copy.
PROBE_SCRIPT = (
    "import os\n"
    "print('committed copy')\n"
    "try:\n"
    "    open(__file__, 'a').write('# tampered\\n')\n"
    "    print('SNAPSHOT-WRITABLE')\n"
    "except OSError:\n"
    "    print('snapshot read-only')\n"
    "print('source dir', os.environ.get('SCOUT_LAB_SOURCE_DIR') == os.path.dirname(os.path.abspath(__file__)))\n"
    "open('out.txt', 'w').write('output in the experiment folder')\n"
)


def _fake_home(tmp_path: Path) -> dict[str, str]:
    """The broker's and runner's env: HOME in the test's temp dir, so the meter ledger never lands in the real home."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "en_US.UTF-8"}
    _owner_config({}, env)
    return env


def _commit_probe(vault: Path) -> Path:
    folder = paths.experiment_dir(_note(vault))
    (folder / "probe.py").write_text(PROBE_SCRIPT, encoding="utf-8")
    _set(vault, "status", "planned")
    _commit(vault, T, "lab: E2 probe script")
    _set(vault, "status", "running")
    return folder


@kernel
def test_a_script_rewritten_after_the_check_is_not_what_runs(vault: Path, tmp_path: Path) -> None:
    """I7 / R30: the broker validates the committed plan; a run rewriting the script in the worktree afterwards
    (it may write the experiment folder) changes nothing: the runner runs the committed copy, read-only."""
    folder = _commit_probe(vault)
    slug, eid, argv, cap, commit, _plan_fields = broker.validate_experiment(
        {**REQ, "argv": ["python3", "probe.py"]}, vault, broker.LabLimits(), AFTER_NOTICE
    )
    (folder / "probe.py").write_text("print('REWRITTEN COPY')\n", encoding="utf-8")  # after validation
    out = runner.run_experiment(vault, slug, eid, argv, cap_usd=cap, env=_fake_home(tmp_path), commit=commit)
    assert out.exit == 0, out.stderr_tail
    assert "committed copy" in out.stdout_tail and "REWRITTEN" not in out.stdout_tail
    assert "snapshot read-only" in out.stdout_tail and "source dir True" in out.stdout_tail
    assert (folder / "out.txt").read_text() == "output in the experiment folder"  # cwd is still the folder
    assert (folder / "probe.py").read_text() == "print('REWRITTEN COPY')\n"
    # Run directly too: the committed executable, not the worktree's.
    (folder / "run.sh").write_text("#!/bin/sh\necho REWRITTEN\n", encoding="utf-8")
    direct = runner.run_experiment(vault, SLUG, EID, ["./run.sh"], cap_usd=1, env=_fake_home(tmp_path), commit=commit)
    assert direct.exit == 0 and direct.stdout_tail.strip() == "hi", direct.stderr_tail


@kernel
def test_the_private_flag_needs_the_committed_note_too(vault: Path, tmp_path: Path) -> None:
    """R30: a worktree note flipped to private after the check doesn't open the private files to the script."""
    secret = vault / "knowledge-base" / "people" / "sam.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("---\nvisibility: private\n---\nCANARY-PRIV-BROKER\n", encoding="utf-8")
    (paths.experiment_dir(_note(vault)) / "cat.sh").write_text(f"#!/bin/sh\ncat '{secret}'\n", encoding="utf-8")
    (paths.experiment_dir(_note(vault)) / "cat.sh").chmod(0o755)
    _set(vault, "status", "planned")
    _commit(vault, T, "lab: E2 cat")
    commit = _git(vault, "rev-parse", "HEAD").strip()
    _set(vault, "status", "running")
    _set(vault, "visibility", "private")  # the run's rewrite, after the broker checked HEAD
    out = runner.run_experiment(vault, SLUG, EID, ["./cat.sh"], cap_usd=1, env=_fake_home(tmp_path), commit=commit)
    assert out.exit != 0 and "CANARY" not in out.stdout_tail and "not permitted" in out.stderr_tail
    _set(vault, "status", "planned")
    _commit(vault, T, "lab: E2 private")  # private in the commit too: now it reads private files
    _set(vault, "status", "running")
    commit = _git(vault, "rev-parse", "HEAD").strip()
    shown = runner.run_experiment(vault, SLUG, EID, ["./cat.sh"], cap_usd=1, env=_fake_home(tmp_path), commit=commit)
    assert shown.exit == 0 and "CANARY-PRIV-BROKER" in shown.stdout_tail, shown.stderr_tail


@kernel
def test_end_to_end_plan_notice_start_broker_run_finish(vault: Path, tmp_path: Path) -> None:
    """I8: the whole path with nothing replaced: a committed plan, the notice passing, ``start``, the broker's
    ``experiment`` verb running the real runner under the kernel's sandbox, then ``finish``."""
    from scout.lab import experiments

    folder = paths.experiment_dir(_note(vault))
    (folder / "probe.py").write_text(PROBE_SCRIPT, encoding="utf-8")
    _set(vault, "status", "planned")
    _set(vault, "not_before", "2026-11-20T16:00")
    _commit(vault, T, "lab: plan E2")
    env = _fake_home(tmp_path)
    b = broker.Broker.for_vault(vault, env, clock=lambda: AFTER_NOTICE)
    req = {**REQ, "argv": ["python3", "probe.py"]}
    early = b.handle(dict(req))  # planned, not started: refused, and the run keeps its allowance
    assert early.exit == broker.EXIT_REFUSED and "not running" in early.stderr and b.experiments_run == 0
    experiments.start(vault, SLUG, EID, now=AFTER_NOTICE)
    reply = b.handle(dict(req))
    assert reply.exit == 0, reply.stderr
    assert "committed copy" in reply.stdout and "snapshot read-only" in reply.stdout
    assert (folder / "out.txt").is_file() and len(list(folder.glob("run-*.log"))) == 1
    done = experiments.finish(vault, SLUG, EID, "It ran.", "Nothing yet.", "ran end to end", now=AFTER_NOTICE)
    assert done.status == "done"
    assert b.handle(dict(req)).exit == broker.EXIT_LIMIT  # one experiment a run


# -- the CLI -----------------------------------------------------------------------------------------------------


def test_the_cli_hands_the_command_after_the_separator_to_the_client(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_client(
        slug: str, eid: str, argv: Sequence[str], env: Mapping[str, str] | None = None, *, timeout_s: int | None = None
    ) -> int:
        seen.update(slug=slug, eid=eid, argv=list(argv), vault=(env or {}).get("SCOUT_DATA_DIR"), timeout_s=timeout_s)
        return 3

    monkeypatch.setattr(broker, "client_experiment", fake_client)
    result = CliRunner().invoke(
        cli.app, ["lab", "--vault", str(vault), "experiment", "run", SLUG, EID, "--", "/bin/sh", "-c", "echo --x"]
    )
    assert result.exit_code == 3, result.output
    assert seen == {
        "slug": SLUG,
        "eid": EID,
        "argv": ["/bin/sh", "-c", "echo --x"],
        "vault": str(vault),
        "timeout_s": None,
    }


# -- the status line, --timeout ----------------------------------------------------------------------------------


def _owner_env(vault: Path, tmp_path: Path) -> dict[str, str]:
    return {**_fake_home(tmp_path), "SCOUT_DATA_DIR": str(vault)}


def _commit_script(vault: Path, name: str, body: str) -> None:
    script = paths.experiment_dir(_note(vault)) / name
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    _set(vault, "status", "planned")
    _commit(vault, T, f"lab: E2 {name}")
    _set(vault, "status", "running")


@kernel
@pytest.mark.parametrize("code", [2, 4, 5])
def test_status_line_when_the_script_exits_2(
    vault: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int
) -> None:
    _commit_script(vault, "exits.sh", f"#!/bin/sh\nexit {code}\n")
    assert broker.client_experiment(SLUG, EID, ["./exits.sh"], env=_owner_env(vault, tmp_path)) == code
    assert capsys.readouterr().err.rstrip("\n").splitlines()[-1] == f"experiment ran: exit {code}"


def test_status_line_on_refusal(vault: Path, fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    _set(vault, "status", "vetoed")
    env = {"SCOUT_DATA_DIR": str(vault), "HOME": os.environ["HOME"], "PATH": os.environ.get("PATH", "")}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == 2
    assert capsys.readouterr().err.rstrip("\n").splitlines()[-1].startswith("experiment refused: ")
    assert fake_run.calls == []


def test_status_line_on_a_refusal_through_the_broker(
    vault: Path, fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    refused = broker.Reply(2, "experiment refused: vetoed\n")
    monkeypatch.setattr(broker, "call", lambda sock, req, *, timeout: refused)
    env = {**os.environ, broker.ENV_SOCKET: "/tmp/scout-broker.x/b.sock"}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == 2
    assert capsys.readouterr().err == "experiment refused: vetoed\n"


def test_status_line_without_broker_in_a_scheduled_run(
    vault: Path, fake_run: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {"SCOUT_MODE": "research", "SCOUT_DATA_DIR": str(vault), "HOME": os.environ["HOME"]}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == 4
    assert capsys.readouterr().err.rstrip("\n").splitlines()[-1].startswith("experiment not run: ")


@kernel
def test_timeout_only_lowers(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    _commit_script(vault, "slow.sh", "#!/bin/sh\nsleep 30\n")
    env = _owner_env(vault, tmp_path)
    started = time.monotonic()
    code = broker.client_experiment(SLUG, EID, ["./slow.sh"], env=env, timeout_s=5)
    assert time.monotonic() - started < 30
    assert code != 0 and capsys.readouterr().err.rstrip("\n").splitlines()[-1] == f"experiment ran: exit {code}"
    log = next(paths.experiment_dir(_note(vault)).glob("run-*.log")).read_text(encoding="utf-8")
    assert "timed_out: true" in log
    fake = FakeRun()
    monkeypatch.setattr(runner, "run_experiment", fake)
    assert broker.client_experiment(SLUG, EID, ["./slow.sh"], env=env, timeout_s=99999) == 0
    assert fake.calls[0]["timeout_s"] == 1800


def test_broker_applies_requested_timeout_min(vault: Path, fake_run: FakeRun) -> None:
    assert _broker(vault).handle({**REQ, "timeout_s": 60}).exit == 0
    assert fake_run.calls[0]["timeout_s"] == 60
    assert _broker(vault).handle({**REQ, "timeout_s": 99999}).exit == 0
    assert fake_run.calls[1]["timeout_s"] == 1800


@pytest.mark.parametrize("bad", [0, -3, "60", True, 1.5])
def test_broker_refuses_a_bad_timeout(vault: Path, fake_run: FakeRun, bad: object) -> None:
    reply = _broker(vault).handle({**REQ, "timeout_s": bad})
    assert reply.exit == broker.EXIT_REFUSED and reply.stderr.startswith("experiment refused: ")
    assert fake_run.calls == []


def test_the_cli_passes_timeout_to_the_client(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_client(
        slug: str, eid: str, argv: Sequence[str], env: Mapping[str, str] | None = None, *, timeout_s: int | None = None
    ) -> int:
        seen["timeout_s"] = timeout_s
        return 0

    monkeypatch.setattr(broker, "client_experiment", fake_client)
    args = ["lab", "--vault", str(vault), "experiment", "run", SLUG, EID, "--timeout", "7", "--", "./run.sh"]
    assert CliRunner().invoke(cli.app, args).exit_code == 0 and seen["timeout_s"] == 7
    assert CliRunner().invoke(cli.app, [*args[:-3], "0", "--", "./run.sh"]).exit_code != 0


def test_multi_line_reasons_stay_one_status_line(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner, "run_experiment", FakeRun(error=LabError("first part\nsecond part")))
    reply = _broker(vault).handle(dict(REQ))
    assert reply.kind == broker.REFUSED and reply.stderr == "experiment refused: first part second part\n"
    env = {"SCOUT_DATA_DIR": str(vault), "HOME": os.environ["HOME"], "PATH": os.environ.get("PATH", "")}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == 2
    assert capsys.readouterr().err.splitlines()[-1] == "experiment refused: first part second part"

    def refuse(*_: Any) -> Any:
        raise broker.Refused(2, "a\nb")

    monkeypatch.setattr(broker, "validate_experiment", refuse)
    assert _broker(vault).handle(dict(REQ)).stderr == "experiment refused: a b\n"
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == 2
    assert capsys.readouterr().err.splitlines()[-1] == "experiment refused: a b"


# -- W3: the broker enforces holds itself ----------------------------------------------------------------------------


def _commit_front(vault: Path, at: datetime, message: str) -> None:
    """Commit the front page alone (the worktree note keeps its uncommitted `status: running`)."""
    _git(vault, "add", str(paths.front_page(vault, SLUG).relative_to(vault)))
    _git(vault, "commit", "-qm", message, at=at)


def _unanswer_the_hold(vault: Path) -> None:
    """The fixture's front page as shipped: alex's second comment on Q3, which E2 lists, has no reply."""
    shutil.copy(FIXTURES / SLUG / f"{SLUG}.md", paths.front_page(vault, SLUG))


def _owner_session_env(vault: Path) -> dict[str, str]:
    return {"SCOUT_DATA_DIR": str(vault), "HOME": os.environ["HOME"], "PATH": os.environ.get("PATH", "")}


def test_an_unanswered_owner_comment_holds_the_experiment(vault: Path, fake_run: FakeRun) -> None:
    _unanswer_the_hold(vault)
    _commit_front(vault, T, "lab: the hold as shipped")
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and reply.kind == broker.REFUSED
    assert reply.stderr.startswith("experiment refused: held: ")
    assert "Q3 at HEAD" in reply.stderr and "Q3 in the worktree" in reply.stderr
    assert fake_run.calls == []


def test_a_hold_at_head_holds_even_when_the_worktree_answers_it(vault: Path, fake_run: FakeRun) -> None:
    _unanswer_the_hold(vault)
    _commit_front(vault, T, "lab: the hold as shipped")
    _answer_the_hold(vault)  # the run's answer, never committed: the front page isn't part of the plan check
    reply = _broker(vault).handle(dict(REQ))
    assert reply.stderr.startswith("experiment refused: held: ") and "Q3 at HEAD" in reply.stderr
    assert "in the worktree" not in reply.stderr and fake_run.calls == []


@pytest.mark.parametrize("target", ["E2", "Q3"])
def test_a_hold_only_in_the_worktree_holds(vault: Path, fake_run: FakeRun, target: str) -> None:
    from scout.lab import ops

    ops.comment(vault, SLUG, target, "alex", "wait for the larger sample", now=AFTER_NOTICE)  # uncommitted
    reply = _broker(vault).handle(dict(REQ))
    assert reply.stderr.startswith("experiment refused: held: ") and f"{target} in the worktree" in reply.stderr
    assert "at HEAD" not in reply.stderr and fake_run.calls == []


@pytest.mark.parametrize("target", ["Q5", "thread", "E1"])
def test_a_comment_elsewhere_doesnt_hold(vault: Path, fake_run: FakeRun, target: str) -> None:
    """Like ``summary.held``: only the experiment's own line and the questions its note lists hold it."""
    from scout.lab import ops

    ops.comment(vault, SLUG, target, "alex", "a thought for later", now=T)
    _commit_front(vault, T, f"lab: comment on {target}")
    assert _broker(vault).handle(dict(REQ)).exit == 0
    assert len(fake_run.calls) == 1


def test_the_owner_session_also_refuses_a_held_experiment(
    vault: Path, fake_run: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    _unanswer_the_hold(vault)
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == broker.EXIT_REFUSED
    assert capsys.readouterr().err.splitlines()[-1].startswith("experiment refused: held: ")
    assert fake_run.calls == []


# -- W3: vetoes in the owner's store -------------------------------------------------------------------------------


def _store() -> Path:
    return broker.owner_dir(os.environ) / broker.LAB_VETOES_NAME


def test_veto_writes_the_owners_store(vault: Path) -> None:
    from scout.lab import experiments

    _set(vault, "status", "planned")
    experiments.veto(vault, SLUG, EID, "alex", now=AFTER_NOTICE)
    rows = [json.loads(line) for line in _store().read_text(encoding="utf-8").splitlines()]
    assert rows == [{"slug": SLUG, "eid": EID, "at": AFTER_NOTICE.isoformat(), "vault": str(vault)}]
    assert _store().stat().st_mode & 0o777 == 0o600


def test_an_uncommitted_veto_set_back_to_running_is_still_refused(
    vault: Path, fake_run: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """The owner vetoes; before anything is committed a run sets the note back to ``running`` by hand. HEAD and
    the history never saw the veto, and the note differs from HEAD only in its status: only the store stops it."""
    from scout.lab import experiments

    _set(vault, "status", "planned")
    experiments.veto(vault, SLUG, EID, "alex", now=AFTER_NOTICE)
    _set(vault, "status", "running")
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and reply.kind == broker.REFUSED
    assert reply.stderr.startswith(f"experiment refused: vetoed: the owner vetoed {SLUG} {EID} at ")
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == broker.EXIT_REFUSED
    assert capsys.readouterr().err.splitlines()[-1].startswith("experiment refused: vetoed: ")
    assert fake_run.calls == []


def test_the_owner_can_veto_a_note_a_run_set_to_running_by_hand(vault: Path, fake_run: FakeRun) -> None:
    """A run hand-edits ``status: running`` (lifecycle-only, so not dirty): the owner's veto still lands, in
    the store first, and the broker refuses the experiment."""
    from scout.lab import experiments

    _set(vault, "status", "running")
    assert experiments.veto(vault, SLUG, EID, "alex", now=AFTER_NOTICE).status == "vetoed"
    assert [json.loads(line)["eid"] for line in _store().read_text(encoding="utf-8").splitlines()] == [EID]
    _set(vault, "status", "running")  # the run puts it back; the store still holds the veto
    reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED and reply.stderr.startswith("experiment refused: vetoed: ")
    assert fake_run.calls == []


def test_a_stored_veto_of_another_experiment_doesnt_refuse_this_one(vault: Path, fake_run: FakeRun) -> None:
    _store().parent.mkdir(parents=True, exist_ok=True)
    _store().write_text('not json\n{"slug": "x"\n', encoding="utf-8")  # a write cut short is skipped
    broker.record_veto(os.environ, vault, SLUG, "E1", T)
    broker.record_veto(os.environ, vault, "another-thread", EID, T)
    assert _broker(vault).handle(dict(REQ)).exit == 0
    broker.record_veto(os.environ, vault, SLUG, EID, T)
    assert "experiment refused: vetoed: " in _broker(vault).handle(dict(REQ)).stderr


def _row(slug: str, eid: str) -> str:
    return json.dumps({"slug": slug, "eid": eid, "at": T.isoformat(), "vault": "/v"})


def test_a_veto_recorded_after_a_torn_tail_is_still_found(vault: Path) -> None:
    """A store whose last line has no newline (a cut-short write, a hand edit): the next veto starts its own line."""
    _store().parent.mkdir(parents=True, exist_ok=True)
    _store().write_text('{"slug": "x", "eid": "E1"', encoding="utf-8")
    broker.record_veto(os.environ, vault, SLUG, EID, T)
    assert broker.owner_veto(os.environ, SLUG, EID) == T.isoformat()
    assert _store().read_text(encoding="utf-8").splitlines()[0] == '{"slug": "x", "eid": "E1"'
    _store().write_text(_row(SLUG, "E1"), encoding="utf-8")  # a whole row, its newline dropped by an editor
    broker.record_veto(os.environ, vault, SLUG, EID, T)
    assert broker.owner_veto(os.environ, SLUG, "E1") == broker.owner_veto(os.environ, SLUG, EID) == T.isoformat()


def test_a_damaged_store_still_yields_every_complete_row() -> None:
    _store().parent.mkdir(parents=True, exist_ok=True)
    joined = '{"slug": "x", "eid": "E1"' + _row(SLUG, "E1") + _row(SLUG, "E2")  # torn, then two on one line
    _store().write_text(f"{joined}\n\x00garbage {{not json\n{_row(SLUG, 'E3')}\n", encoding="utf-8")
    for eid in ("E1", "E2", "E3"):
        assert broker.owner_veto(os.environ, SLUG, eid) == T.isoformat(), eid
    assert broker.owner_veto(os.environ, SLUG, "E4") is None
    assert broker.owner_veto(os.environ, "x", "E1") is None  # the torn row itself is not a veto


def test_a_veto_under_the_account_home_holds_when_home_is_elsewhere(
    tmp_path: Path, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner vetoed with the account's HOME; a broker started with another HOME still finds the veto."""
    from scout.kb import hook_paths

    account = tmp_path / "account"
    monkeypatch.setattr(hook_paths, "account_home", lambda: account)
    broker.record_veto({**os.environ, "HOME": str(account)}, vault, SLUG, EID, T)
    elsewhere = {**os.environ, "HOME": str(tmp_path / "elsewhere")}
    assert broker.veto_stores(elsewhere) == [
        tmp_path / "elsewhere" / ".local/state/scout" / hook_paths.RUN_BROKERS_NAME / broker.LAB_VETOES_NAME,
        account / ".local/state/scout" / hook_paths.RUN_BROKERS_NAME / broker.LAB_VETOES_NAME,
    ]
    assert broker.owner_veto(elsewhere, SLUG, EID) == T.isoformat()
    assert broker.owner_veto(elsewhere, SLUG, "E9") is None


def test_no_home_cancels_another_homes_veto(tmp_path: Path, vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reading is additive: a store with other rows, or none for this experiment, never hides a veto in the other."""
    from scout.kb import hook_paths

    account, elsewhere = tmp_path / "account", tmp_path / "elsewhere"
    monkeypatch.setattr(hook_paths, "account_home", lambda: account)
    in_account, in_elsewhere = ({**os.environ, "HOME": str(home)} for home in (account, elsewhere))
    broker.record_veto(in_account, vault, SLUG, "E1", T)  # HOME is the account's: its store only
    home_store, account_store = broker.veto_stores(in_elsewhere)
    home_store.parent.mkdir(parents=True)
    home_store.write_text(_row(SLUG, "E2") + "\n", encoding="utf-8")  # a row only HOME's store holds
    # HOME elsewhere: both stores are read, each holding a row the other lacks.
    assert "E2" not in account_store.read_text(encoding="utf-8")
    assert broker.owner_veto(in_elsewhere, SLUG, "E1") == T.isoformat()  # only in the account's store
    assert broker.owner_veto(in_elsewhere, SLUG, "E2") == T.isoformat()  # only in HOME's store
    assert broker.owner_veto(in_elsewhere, SLUG, "E3") is None


def test_a_veto_from_another_home_reaches_a_broker_with_the_account_home(
    tmp_path: Path, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner vetoes from a session whose HOME isn't the account's; a scheduled run's broker (the account's
    HOME) still finds the veto, since it is written to both stores."""
    from scout.kb import hook_paths

    account, elsewhere = tmp_path / "account", tmp_path / "elsewhere"
    monkeypatch.setattr(hook_paths, "account_home", lambda: account)
    in_account, in_elsewhere = ({**os.environ, "HOME": str(home)} for home in (account, elsewhere))
    broker.record_veto(in_elsewhere, vault, SLUG, EID, T)
    for store in broker.veto_stores(in_elsewhere):
        assert EID in store.read_text(encoding="utf-8"), store
    assert broker.veto_stores(in_account) == [broker.veto_stores(in_elsewhere)[1]]
    assert broker.owner_veto(in_account, SLUG, EID) == T.isoformat()


def test_a_veto_whose_account_store_write_fails_changes_nothing(
    tmp_path: Path, vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HOME's store takes the row but the account's can't: the veto command fails loudly and no file changes."""
    from scout.kb import hook_paths
    from scout.lab import experiments

    account = tmp_path / "account"
    monkeypatch.setattr(hook_paths, "account_home", lambda: account)
    _set(vault, "status", "planned")
    elsewhere = {**os.environ, "HOME": str(tmp_path / "elsewhere")}
    account_store = broker.veto_stores(elsewhere)[1]
    account_store.mkdir(parents=True)  # the store's name is a folder: the append fails
    files = [_note(vault), paths.front_page(vault, SLUG), paths.timeline_path(vault, SLUG)]
    before = [p.read_bytes() for p in files]
    with pytest.raises(LabError, match="cannot record the veto of E2 in the owner's veto store"):
        experiments.veto(vault, SLUG, EID, "alex", now=AFTER_NOTICE, env=elsewhere)
    assert [p.read_bytes() for p in files] == before


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_an_unreadable_veto_store_refuses(vault: Path, fake_run: FakeRun) -> None:
    broker.record_veto(os.environ, vault, SLUG, "E1", T)
    _store().chmod(0)
    try:
        reply = _broker(vault).handle(dict(REQ))
    finally:
        _store().chmod(0o600)
    assert reply.exit == broker.EXIT_REFUSED and "veto store can't be read" in reply.stderr
    assert fake_run.calls == []


def test_a_veto_whose_store_write_fails_changes_nothing(vault: Path) -> None:
    from scout.lab import experiments

    _set(vault, "status", "planned")
    _store().mkdir(parents=True)  # the store's name is a folder: the append fails
    files = [_note(vault), paths.front_page(vault, SLUG), paths.timeline_path(vault, SLUG)]
    before = [p.read_bytes() for p in files]
    with pytest.raises(LabError, match="cannot record the veto of E2 in the owner's veto store"):
        experiments.veto(vault, SLUG, EID, "alex", now=AFTER_NOTICE)
    assert [p.read_bytes() for p in files] == before


# -- W3: the owner's switch ------------------------------------------------------------------------------------------

OFF = "experiment not run: experiments are off in the owner's config\n"


@pytest.mark.parametrize(
    "config",
    [
        None,
        "not json",
        "{}",
        '{"lab": {}}',
        '{"lab": {"experiments_enabled": false}}',
        '{"lab": {"experiments_enabled": "true"}}',
        '{"lab": {"experiments_enabled": 1}}',
        '{"lab": {"experiments_enabled": null}}',
        '{"lab": {"experiments_enabled": [true]}}',
    ],
)
def test_experiments_are_off_unless_the_owner_says_true(vault: Path, fake_run: FakeRun, config: str | None) -> None:
    path = broker.owner_dir(os.environ) / broker.CONFIG_NAME
    if config is None:
        path.unlink()
    else:
        path.write_text(config, encoding="utf-8")
    assert broker.load_lab_limits(os.environ).experiments_enabled is False
    b = _broker(vault)
    # Refused before anything is looked at: a malformed request gets the same answer as a valid one.
    malformed = b.handle({"v": broker.PROTOCOL, "verb": "experiment"})
    reply = b.handle(dict(REQ))
    assert (malformed.exit, malformed.stderr) == (reply.exit, reply.stderr) == (broker.EXIT_NOT_RUN, OFF)
    assert reply.ran is False and fake_run.calls == [] and b.experiments_run == 0


def test_experiments_run_once_the_owner_says_true(vault: Path, fake_run: FakeRun) -> None:
    _owner_config({"lab": {"experiments_enabled": True, "timeout_s": 90}})
    assert broker.load_lab_limits(os.environ) == broker.LabLimits(timeout_s=90, experiments_enabled=True)
    assert _broker(vault).handle(dict(REQ)).exit == 0 and len(fake_run.calls) == 1


def test_the_client_says_not_run_when_the_switch_is_off(
    vault: Path, fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _owner_config({"lab": {"experiments_enabled": False}})
    served = _broker(vault)

    def over_the_socket(sock: str, req: Mapping[str, Any], *, timeout: float) -> broker.Reply:
        return broker.Reply.from_dict(served.handle(dict(req)).to_dict())

    monkeypatch.setattr(broker, "call", over_the_socket)
    env = {**os.environ, broker.ENV_SOCKET: "/tmp/scout-broker.x/b.sock", "SCOUT_MODE": "research"}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env) == broker.EXIT_NOT_RUN
    assert capsys.readouterr().err == OFF
    assert fake_run.calls == []


def test_the_owners_own_session_isnt_gated_by_the_switch(
    vault: Path, fake_run: FakeRun, capsys: pytest.CaptureFixture[str]
) -> None:
    (broker.owner_dir(os.environ) / broker.CONFIG_NAME).unlink()  # the default: off
    assert broker.load_lab_limits(os.environ).experiments_enabled is False
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == 0
    assert capsys.readouterr().err.splitlines()[-1] == "experiment ran: exit 0"
    assert len(fake_run.calls) == 1


# -- ML experiments, part 1: owner limits and note fields (spec §6.4) ----------------------------------------------

SHA_A = "a" * 40


def test_lab_limits_read_the_ml_keys(tmp_path: Path) -> None:
    python = tmp_path / "py"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    env = {"HOME": str(tmp_path / "home")}
    folder = broker.owner_dir(env)
    folder.mkdir(parents=True)
    config = folder / broker.CONFIG_NAME
    ml = {
        "detached_max_hours": 12,
        "max_detached": 1,
        "gpu": True,
        "max_memory_bytes": 1 << 34,
        "disk_free_floor_bytes": 0,
        "weights_max_bytes": 40 << 30,
        "python": str(python),
    }
    config.write_text(json.dumps({"lab": ml}), encoding="utf-8")
    got = broker.load_lab_limits(env)
    assert (got.detached_max_hours, got.max_detached, got.gpu) == (12.0, 1, True)
    assert (got.max_memory_bytes, got.disk_free_floor_bytes, got.weights_max_bytes) == (1 << 34, 0, 40 << 30)
    assert got.python == str(python)
    bad = '{"lab": {"gpu": 1, "max_detached": -1, "python": "relative/py", "detached_max_hours": 1e400}}'
    config.write_text(bad, encoding="utf-8")
    assert broker.load_lab_limits(env) == broker.LabLimits()
    config.write_text(json.dumps({"lab": {"python": str(tmp_path / "missing")}}), encoding="utf-8")
    assert broker.load_lab_limits(env).python is None


@pytest.mark.parametrize(("hours", "expected"), [(72, 72.0), (71.5, 71.5), (72.5, 0.0), (168, 0.0)])
def test_detached_runs_are_capped_at_72_hours(hours: float, expected: float) -> None:
    """Only the probe file is refreshed while a detached run goes on, and macOS deletes /tmp files untouched for 3
    days: past 72 h the meter socket, the profile and the output files could go. More than 72 means the default."""
    _owner_config({"lab": {"detached_max_hours": hours}})
    assert broker.load_lab_limits(os.environ).detached_max_hours == expected


def _commit_fields(vault: Path, extra: str) -> None:
    """Commit E2's note with ``extra`` frontmatter lines added, then leave it ``running`` in the worktree."""
    note = _note(vault)
    text = note.read_text(encoding="utf-8").replace("visibility: normal\n", f"visibility: normal\n{extra}", 1)
    note.write_text(text, encoding="utf-8")
    _set(vault, "status", "planned")
    _commit(vault, T, "lab: E2 ML fields")
    _set(vault, "status", "running")


def _plan(vault: Path, limits: broker.LabLimits | None = None) -> broker.ExperimentPlan:
    return broker.validate_experiment(dict(REQ), vault, limits or broker.LabLimits(), AFTER_NOTICE)[5]


def test_broker_reads_the_plan_at_the_commit(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    models = f'models:\n  - repo: org/m\n    revision: "{SHA_A}"\n'
    _commit_fields(vault, f"timeout_hours: 6\ndisk_budget_bytes: 1000\n{models}")
    _set(vault, "disk_budget_bytes", "999999")  # after the commit: the worktree's copy must not count
    # The uncommitted-change refusal would stop the run first; with it out of the way, the plan's source is pinned.
    monkeypatch.setattr(broker, "_uncommitted", lambda *_a, **_k: [])
    assert _plan(vault) == broker.ExperimentPlan(6.0, (ModelRef("org/m", SHA_A),), 1000)


def test_disk_budget_is_clamped(vault: Path) -> None:
    _commit_fields(vault, f"disk_budget_bytes: {20 << 30}\n")
    assert _plan(vault, broker.LabLimits(max_total_bytes=10 << 30)).disk_budget_bytes == 10 << 30


@pytest.mark.parametrize(
    ("extra", "field"),
    [
        ("timeout_hours: 0\n", "timeout_hours"),
        ("disk_budget_bytes: -1\n", "disk_budget_bytes"),
        ('models:\n  - repo: org/m\n    revision: "main"\n', "models"),
    ],
)
def test_malformed_ml_fields_are_refused_not_defaulted(vault: Path, fake_run: FakeRun, extra: str, field: str) -> None:
    _commit_fields(vault, extra)
    reply = _broker(vault).experiment(REQ)
    assert reply.exit == broker.EXIT_REFUSED and reply.stderr.startswith("experiment refused:")
    assert field in reply.stderr
    assert fake_run.calls == []


def test_no_disk_budget_means_512_mib(vault: Path) -> None:
    assert _plan(vault, broker.LabLimits(max_total_bytes=10 << 30)) == broker.ExperimentPlan(None, (), 512 << 20)


# -- detached runs (spec §6.4) -------------------------------------------------------------------------------------

DETACH = {**REQ, "detach": True}
STATUS = {"v": broker.PROTOCOL, "verb": "experiment-status", "slug": SLUG, "eid": EID}


def _detached_on(hours: float = 12, most: int = 1) -> None:
    _owner_config({"lab": {"detached_max_hours": hours, "max_detached": most}})


class FakeStart:
    """Stands in for :func:`scout.lab.supervise.start`: records each call, or raises."""

    def __init__(self, error: LabError | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def __call__(self, vault: Path, slug: str, eid: str, argv: Sequence[str], **kw: Any) -> None:
        self.calls.append({"vault": vault, "slug": slug, "eid": eid, "argv": list(argv), **kw})
        if self.error is not None:
            raise self.error


@pytest.fixture
def fake_start(monkeypatch: pytest.MonkeyPatch) -> FakeStart:
    from scout.lab import supervise

    fake = FakeStart()
    monkeypatch.setattr(supervise, "start", fake)
    return fake


@pytest.fixture
def no_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.lab import supervise

    def fail(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the supervisor was started")

    monkeypatch.setattr(supervise, "start", fail)


def _record(vault: Path, eid: str = EID, *, pid: int | None = None, **kw: Any) -> Path:
    from scout.lab import runs

    path = runs.record_path(vault, SLUG, eid, os.environ)
    started, deadline = T, T + timedelta(hours=12)
    runs.write(path, runs.RunRecord(started.isoformat(), deadline.isoformat(), pid or os.getpid(), **kw))
    return path


def _dead_pid() -> int:
    """The pid of a process this test started and reaped."""
    proc = subprocess.Popen(["/usr/bin/true"])
    proc.wait()
    return proc.pid


@pytest.mark.parametrize("config", [{}, {"detached_max_hours": 12, "max_detached": 0}, {"max_detached": 1}])
def test_detach_refused_when_off(vault: Path, fake_run: FakeRun, no_start: None, config: dict[str, Any]) -> None:
    _owner_config({"lab": config})
    b = _broker(vault)
    reply = b.handle(dict(DETACH))
    # A config answer (ruling P2-R8), not a fault of the experiment's: not run, and the allowance is kept.
    assert reply.exit == broker.EXIT_NOT_RUN
    assert reply.stderr == "experiment not run: detached runs are off in the owner's config\n"
    assert b.experiments_run == 0 and fake_run.calls == []


def test_detach_refused_when_one_is_running(vault: Path, fake_run: FakeRun, no_start: None) -> None:
    _detached_on()
    _record(vault, "E1")  # another experiment's supervisor, alive: it holds E1's run lock (this test's process)
    with runner._run_lock(vault, SLUG, "E1", os.environ):
        b = _broker(vault)
        reply = b.handle(dict(DETACH))
    assert reply.exit == broker.EXIT_NOT_RUN  # a capacity answer (ruling P2-R8): the run may try again later
    assert reply.stderr == "experiment not run: a detached experiment is already running\n"
    assert b.experiments_run == 0 and fake_run.calls == []


def test_a_record_whose_supervisor_is_gone_doesnt_count_as_running(vault: Path, fake_start: FakeStart) -> None:
    _detached_on()
    _record(vault, "E1", pid=_dead_pid())  # no outcome, but nothing holds E1's run lock
    assert _broker(vault).handle(dict(DETACH)).exit == 0
    assert len(fake_start.calls) == 1


@pytest.mark.parametrize(
    ("fields", "requested", "expected"),
    [("timeout_hours: 24\n", None, 43200), ("timeout_hours: 2\n", None, 7200), ("", None, 43200), ("", 600, 600)],
)
def test_detach_deadline_is_clamped(
    vault: Path, fake_start: FakeStart, fields: str, requested: int | None, expected: int
) -> None:
    _detached_on(hours=12)
    if fields:
        _commit_fields(vault, fields)
    req = dict(DETACH) if requested is None else {**DETACH, "timeout_s": requested}
    reply = _broker(vault).handle(req)
    assert reply.exit == 0, reply.stderr
    assert fake_start.calls[0]["deadline_s"] == expected


def test_detached_start_replies_started(
    vault: Path, fake_run: FakeRun, fake_start: FakeStart, capsys: pytest.CaptureFixture[str]
) -> None:
    _detached_on()
    _commit_fields(vault, "disk_budget_bytes: 1000\n")
    b = _broker(vault)
    reply = b.handle(dict(DETACH))
    assert broker._emit_experiment(reply) == 0
    assert capsys.readouterr().err.rstrip("\n").splitlines()[-1] == "experiment started: detached"
    assert b.experiments_run == 1 and fake_run.calls == []  # the run's allowance is used; nothing ran attached
    [call] = fake_start.calls
    assert (call["slug"], call["eid"], call["argv"]) == (SLUG, EID, ARGV)
    assert call["commit"] == _git(vault, "rev-parse", "HEAD").strip() and call["cap_usd"] == 1.0
    assert call["limits"].max_total_bytes == 1000  # the plan's disk budget
    again = b.handle(dict(DETACH))
    assert again.exit == broker.EXIT_LIMIT and len(fake_start.calls) == 1


MODELS = f'models:\n  - repo: org/m\n    revision: "{"a" * 40}"\n'


def test_attached_run_with_models_is_refused(
    vault: Path, fake_run: FakeRun, no_start: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The weights are fetched when the experiment starts, before its gate, so one that names models runs detached
    (ruling P2-h); attached, it is refused and keeps the run's allowance."""
    _detached_on()
    _commit_fields(vault, MODELS)
    b = _broker(vault)
    reply = b.handle(dict(REQ))
    assert reply.exit == broker.EXIT_REFUSED
    assert reply.stderr == "experiment refused: an experiment that names models runs detached\n"
    assert b.experiments_run == 0 and fake_run.calls == []
    # The owner's own session, without a broker, answers the same.
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == broker.EXIT_REFUSED
    assert (
        capsys.readouterr().err.splitlines()[-1] == "experiment refused: an experiment that names models runs detached"
    )
    assert fake_run.calls == []


def test_a_detached_start_hands_the_supervisor_the_models(vault: Path, fake_start: FakeStart) -> None:
    _detached_on()
    _commit_fields(vault, MODELS)
    assert _broker(vault).handle(dict(DETACH)).exit == 0
    assert fake_start.calls[0]["models"] == (ModelRef("org/m", "a" * 40),)


def test_the_owners_python_reaches_the_runner(vault: Path, fake_run: FakeRun, tmp_path: Path) -> None:
    py = tmp_path / "venv" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    _owner_config({"lab": {"python": str(py)}})
    assert _broker(vault).handle(dict(REQ)).exit == 0
    assert fake_run.calls[0]["python"] == str(py) and fake_run.calls[0]["argv"] == ARGV  # validated as python3
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == 0
    assert fake_run.calls[1]["python"] == str(py)


def _last_two(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().err.rstrip("\n").splitlines()[-2:]


@pytest.mark.parametrize(
    ("flags", "line"),
    [
        ({"over_memory": True}, "killed: over_memory"),
        ({"over_disk_floor": True}, "killed: over_disk_floor"),
        ({"over_bytes": True}, "killed: over_bytes"),
        ({"over_processes": True}, "killed: over_processes"),
        ({"timed_out": True}, "killed: timed_out"),
        ({"stopped": "stopped by the owner"}, "killed: stopped"),
        ({"timed_out": True, "over_memory": True}, "killed: timed_out, over_memory"),
    ],
)
def test_an_attached_run_says_what_killed_it(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], flags: dict[str, Any], line: str
) -> None:
    """A limit or a stop that killed the run is one `killed: <flag>` line just before the status line, so the run
    can put it in the Result; it isn't left in the run log alone."""
    out = runner.RunOutcome(**{"exit": -9, "stdout_tail": "", "stderr_tail": "progress", "spent_usd": 0.0,
                               "timed_out": False, **flags})  # fmt: skip
    monkeypatch.setattr(runner, "run_experiment", FakeRun(out))
    reply = _broker(vault).handle(dict(REQ))
    assert broker._emit_experiment(reply) == -9
    assert _last_two(capsys) == [line, "experiment ran: exit -9"]


def test_a_clean_run_has_no_killed_line(vault: Path, fake_run: FakeRun, capsys: pytest.CaptureFixture[str]) -> None:
    broker._emit_experiment(_broker(vault).handle(dict(REQ)))
    assert _last_two(capsys) == ["progress line", "experiment ran: exit 0"]


@pytest.mark.parametrize(
    ("outcome", "lines"),
    [
        (
            {"status": "ran", "exit": -9, "stderr_tail": "progress\n", "over_memory": True, "stopped": None},
            ["killed: over_memory", "experiment ran: exit -9"],
        ),
        (
            {"status": "ran", "exit": -9, "stderr_tail": "progress", "timed_out": True, "over_disk_floor": True},
            ["killed: timed_out, over_disk_floor", "experiment ran: exit -9"],
        ),
        (
            {"status": "not run", "reason": "stopped by the owner", "stopped": "stopped by the owner"},
            ["killed: stopped", "experiment not run: stopped by the owner"],
        ),
        (
            {"status": "ran", "exit": 0, "stderr_tail": "progress\n", "timed_out": False, "over_bytes": False},
            ["progress", "experiment ran: exit 0"],
        ),
    ],
)
def test_a_collect_says_what_killed_it(
    vault: Path, capsys: pytest.CaptureFixture[str], outcome: dict[str, Any], lines: list[str]
) -> None:
    _record(vault, outcome=outcome)
    broker._emit_experiment(_broker(vault).handle(dict(STATUS)))
    assert _last_two(capsys) == lines


PYTHON_MISSING = "experiment not run: the owner's lab.python is missing"


def _missing_python(tmp_path: Path, how: str) -> str:
    """An owner's lab.python that was moved or deleted (``gone``), or is no longer executable (``not-executable``)."""
    py = tmp_path / "venv" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    if how == "not-executable":
        py.write_text("#!/bin/sh\n")
        py.chmod(0o644)
    return str(py)


@pytest.mark.parametrize("how", ["gone", "not-executable"])
def test_lab_limits_keep_a_set_python_that_is_missing(tmp_path: Path, how: str) -> None:
    py = _missing_python(tmp_path, how)
    _owner_config({"lab": {"python": py}})
    got = broker.load_lab_limits(os.environ)
    assert got.python is None and got.python_set == py  # set, but not usable: never the same as unset
    _owner_config({})
    assert broker.load_lab_limits(os.environ).python_set is None


@pytest.mark.parametrize("how", ["gone", "not-executable"])
@pytest.mark.parametrize("detach", [False, True])
def test_a_missing_owner_python_is_not_run_rather_than_another_interpreter(
    vault: Path, fake_run: FakeRun, no_start: None, tmp_path: Path, how: str, detach: bool
) -> None:
    """`python3` in the argv means the owner's lab.python once it is set; a moved or deleted one must not fall back to
    PATH's python3 (another interpreter, without the owner's packages). Not run, exit 4, the allowance kept."""
    _detached_on()
    _owner_config({"lab": {"python": _missing_python(tmp_path, how), "detached_max_hours": 12, "max_detached": 1}})
    b = _broker(vault)
    reply = b.handle(dict(DETACH if detach else REQ))
    assert reply.exit == broker.EXIT_NOT_RUN and reply.stderr == f"{PYTHON_MISSING}\n"
    assert b.experiments_run == 0 and fake_run.calls == []


def test_a_missing_owner_python_in_the_owners_own_session(
    vault: Path, fake_run: FakeRun, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _owner_config({"lab": {"python": _missing_python(tmp_path, "gone")}})
    assert broker.client_experiment(SLUG, EID, ARGV, env=_owner_session_env(vault)) == broker.EXIT_NOT_RUN
    assert capsys.readouterr().err.splitlines()[-1] == PYTHON_MISSING
    assert fake_run.calls == []


def test_a_missing_owner_python_doesnt_stop_a_script_that_doesnt_use_it(
    vault: Path, fake_run: FakeRun, tmp_path: Path
) -> None:
    _owner_config({"lab": {"python": _missing_python(tmp_path, "gone")}})
    assert _broker(vault).handle({**REQ, "argv": ["./run.sh"]}).exit == 0
    assert fake_run.calls[0]["argv"] == ["./run.sh"] and fake_run.calls[0]["python"] is None


@pytest.mark.parametrize(
    "why",
    [
        "cannot start the experiment's supervisor; not run",
        "the experiment's supervisor stopped before it started: no reason given",
        "the experiment's supervisor wrote no record within 30 s; not run",
    ],
)
def test_a_supervisor_that_doesnt_start_is_not_run_and_keeps_the_allowance(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], why: str
) -> None:
    """A transient start failure ran nothing and is no fault of the experiment's (as ruling P2-R8): `not run`, exit
    4, so the run retries it later instead of abandoning and re-planning it (P2-g)."""
    from scout.lab import supervise

    _detached_on()
    monkeypatch.setattr(supervise, "start", FakeStart(supervise.SupervisorNotStarted(why)))
    b = _broker(vault)
    reply = b.handle(dict(DETACH))
    assert reply.exit == broker.EXIT_NOT_RUN and reply.stderr == f"experiment not run: {why}\n"
    assert b.experiments_run == 0
    assert broker._emit_experiment(reply) == broker.EXIT_NOT_RUN
    assert capsys.readouterr().err.rstrip("\n").splitlines()[-1] == f"experiment not run: {why}"


def test_any_other_start_refusal_is_still_refused(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.lab import supervise

    _detached_on()
    monkeypatch.setattr(supervise, "start", FakeStart(LabError("experiment summary-drift E2 is already running")))
    b = _broker(vault)
    reply = b.handle(dict(DETACH))
    assert reply.exit == broker.EXIT_REFUSED and reply.stderr.startswith("experiment refused: experiment")
    assert b.experiments_run == 0


@pytest.mark.parametrize("value", [1, "yes", None])
def test_detach_must_be_a_boolean(vault: Path, fake_run: FakeRun, no_start: None, value: object) -> None:
    _detached_on()
    reply = _broker(vault).handle({**REQ, "detach": value})
    assert reply.exit == broker.EXIT_REFUSED and "detach must be true or false" in reply.stderr


def test_the_client_sends_detach(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[Mapping[str, Any]] = []

    def fake_call(sock: str, req: Mapping[str, Any], *, timeout: float) -> broker.Reply:
        sent.append(req)
        return broker.Reply(0, "experiment started: detached\n")

    monkeypatch.setattr(broker, "call", fake_call)
    env = {**os.environ, broker.ENV_SOCKET: "/tmp/scout-broker.x/b.sock"}
    assert broker.client_experiment(SLUG, EID, ARGV, env=env, detach=True) == 0
    assert sent[0]["detach"] is True and sent[0]["verb"] == "experiment"
    assert capsys.readouterr().err == "experiment started: detached\n"


def test_status_reports_running(vault: Path) -> None:
    _record(vault)
    with runner._run_lock(vault, SLUG, EID, os.environ):  # the supervisor (this test's process) holds the lock
        reply = _broker(vault, now=T + timedelta(hours=1, minutes=5)).handle(dict(STATUS))
    assert reply.exit == 0 and reply.stderr == "experiment running: 1h05m of 12h00m\n"
    assert not reply.ran


def test_status_reports_the_outcome(vault: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ran = {"status": "ran", "exit": 3, "stdout_tail": "result line\n", "stderr_tail": "progress line\n"}
    path = _record(vault, outcome=ran)
    b = _broker(vault)
    reply = b.handle(dict(STATUS))
    assert reply.ran and reply.exit == 3 and reply.stdout == "result line\n" and reply.stderr == "progress line\n"
    assert broker._emit_experiment(reply) == 3
    assert capsys.readouterr().err.rstrip("\n").splitlines()[-1] == "experiment ran: exit 3"
    from scout.lab import runs

    runs.write(path, runs.RunRecord(T.isoformat(), T.isoformat(), 1, outcome={"status": "not run", "reason": "x y"}))
    assert b.handle(dict(STATUS)).stderr == "experiment not run: x y\n"
    path.unlink()
    missing = b.handle(dict(STATUS))
    assert missing.exit == broker.EXIT_NOT_RUN
    assert missing.stderr == "experiment not run: no detached run of this experiment was started\n"
    assert b.experiments_run == 0  # a status never counts against the run's allowance


def test_status_refuses_a_bad_name(vault: Path) -> None:
    reply = _broker(vault).handle({**STATUS, "eid": "../E2"})
    assert reply.exit == broker.EXIT_REFUSED


def test_status_sweeps_a_dead_supervisor(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.lab import runs, sandbox

    swept: list[tuple[str, str]] = []

    def fake_sweep(_probe: object, marker: str, outside: str) -> bool:
        swept.append((marker, outside))
        return True

    monkeypatch.setattr(runner, "_sweep", fake_sweep)
    monkeypatch.setattr(sandbox, "SandboxProbe", lambda: object())
    marker, outside = _probe_files(tmp_path), str(paths.experiment_dir(_note(vault)).parent)
    path = _record(vault, pid=_dead_pid(), marker=marker, outside=outside)
    b = _broker(vault)
    first = b.handle(dict(STATUS))
    assert first.exit == broker.EXIT_NOT_RUN
    assert first.stderr == "experiment not run: the supervisor stopped without a result\n"
    assert swept == [(marker, outside)]
    rec = runs.read(path)
    assert rec is not None and rec.outcome is not None
    assert rec.outcome["reason"] == "the supervisor stopped without a result" and rec.outcome["swept"] == "clean"
    assert b.handle(dict(STATUS)).stderr == first.stderr and swept == [(marker, outside)]  # answered, not swept again


def _probe_files(folder: Path) -> str:
    """A sweep's probe file and its decoy, as the runner makes them (both plain files)."""
    marker = folder / "scout-lab-t" / "probe"
    marker.parent.mkdir()
    marker.write_text("", encoding="utf-8")
    Path(str(marker) + "-denied").write_text("", encoding="utf-8")
    return str(marker)


SURVIVED = "experiment not run: a process the experiment started survived the sweep; check ps\n"


@pytest.mark.parametrize("why", ["survivors", "no probe"])
def test_a_sweep_that_cant_confirm_clean_says_so_and_retries(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    """Survivors remain, or the kernel can't be asked: the outcome stays unwritten, so the next collect sweeps
    again, and the reply names the problem (as an attached run's survivors do)."""
    from scout.lab import runs, sandbox

    swept: list[str] = []

    def fake_sweep(_probe: object, marker: str, _outside: str) -> bool:
        swept.append(marker)
        return False

    def no_probe() -> object:
        raise OSError("sandbox_check unavailable")

    monkeypatch.setattr(runner, "_sweep", fake_sweep)
    monkeypatch.setattr(sandbox, "SandboxProbe", no_probe if why == "no probe" else (lambda: object()))
    path = _record(vault, pid=_dead_pid(), marker=_probe_files(tmp_path), outside=str(tmp_path))
    b = _broker(vault)
    for _ in range(2):
        reply = b.handle(dict(STATUS))
        assert reply.exit == broker.EXIT_NOT_RUN and reply.stderr == SURVIVED
        rec = runs.read(path)
        assert rec is not None and rec.outcome is None  # not recorded: the next collect sweeps again
    assert len(swept) == (2 if why == "survivors" else 0)
    monkeypatch.setattr(runner, "_sweep", lambda *_a: True)
    monkeypatch.setattr(sandbox, "SandboxProbe", lambda: object())
    assert b.handle(dict(STATUS)).stderr == "experiment not run: the supervisor stopped without a result\n"


def test_a_probe_file_gone_records_the_outcome(vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe file is gone (a restart cleared /tmp, and with it every process that held the profile): nothing
    can be swept, and nothing is left to sweep, so the outcome is recorded."""
    from scout.lab import runs, sandbox

    def fail(*_a: Any) -> bool:
        raise AssertionError("swept by a probe file that isn't there")

    monkeypatch.setattr(runner, "_sweep", fail)
    monkeypatch.setattr(sandbox, "SandboxProbe", lambda: object())
    path = _record(vault, pid=_dead_pid(), marker=str(tmp_path / "scout-lab-gone" / "probe"), outside=str(tmp_path))
    reply = _broker(vault).handle(dict(STATUS))
    assert reply.stderr == "experiment not run: the supervisor stopped without a result\n"
    rec = runs.read(path)
    assert rec is not None and rec.outcome is not None and rec.outcome["swept"] == "probe file gone"


def test_an_attached_run_over_an_uncollected_detached_record_is_not_run(vault: Path, fake_run: FakeRun) -> None:
    """A killed supervisor's script may still be running unpolled in the experiment's folder: an attached run must
    not start there (and clear its scratch dir) until a collect has swept it."""
    path = _record(vault, pid=_dead_pid())  # no outcome
    b = _broker(vault)
    reply = b.handle(dict(REQ))
    assert reply.exit == broker.EXIT_NOT_RUN
    assert (
        reply.stderr
        == f"experiment not run: an earlier detached run of {EID} hasn't been collected; collect it first\n"
    )
    assert fake_run.calls == [] and b.experiments_run == 0
    from scout.lab import runs

    rec = runs.read(path)
    assert rec is not None
    runs.write(path, runs.RunRecord(rec.started, rec.deadline, rec.pid, outcome={"status": "ran", "exit": 0}))
    assert b.handle(dict(REQ)).exit == 0 and len(fake_run.calls) == 1  # collected: it runs


class _HeldLock:
    """The experiment's run lock held through a descriptor of the test's own, which a fake SIGKILL closes."""

    def __init__(self, vault: Path) -> None:
        import fcntl

        self.fd: int | None = os.open(
            runner.run_lock_path(vault, SLUG, EID, os.environ), os.O_WRONLY | os.O_CREAT, 0o200
        )
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.signals: list[tuple[int, int]] = []

    def kill(self, pid: int, sig: int) -> None:
        self.signals.append((pid, sig))
        if sig == signal.SIGKILL and self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def test_a_supervisor_not_yet_past_its_grace_is_left_running(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.lab import runs

    fake_pid = 999_999_991
    path = _record(vault, pid=fake_pid)  # the deadline is T + 12 h
    held = _HeldLock(vault)
    monkeypatch.setattr(runs, "_kill", held.kill)
    try:
        reply = _broker(vault, now=T + timedelta(hours=12, minutes=5)).handle(dict(STATUS))
    finally:
        held.close()
    assert (fake_pid, signal.SIGKILL) not in held.signals
    assert reply.exit == 0 and reply.stderr == "experiment running: 12h05m of 12h00m\n"
    rec = runs.read(path)
    assert rec is not None and rec.outcome is None


_REAL_KILL = os.kill
needs_libproc = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS libproc")
# A process of the test's own that opens the experiment's run lock (argv 1), flocks it when argv 2 is "1", says it is
# ready (argv 3), and waits.
_HOLDER = (
    "import fcntl, os, sys, time\n"
    "fd = os.open(sys.argv[1], os.O_WRONLY)\n"
    "if sys.argv[2] == '1':\n"
    "    fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "open(sys.argv[3], 'w').write('ready')\n"
    "time.sleep(60)\n"
)


def _holder(vault: Path, tmp_path: Path, *, flock: bool) -> subprocess.Popen[bytes]:
    lock = runner.run_lock_path(vault, SLUG, EID, os.environ)
    if not lock.exists():
        os.close(os.open(lock, os.O_WRONLY | os.O_CREAT, 0o200))
    ready = tmp_path / f"ready-{flock}"
    proc = subprocess.Popen([sys.executable, "-I", "-c", _HOLDER, str(lock), "1" if flock else "0", str(ready)])
    deadline = time.monotonic() + 30
    while not ready.exists():
        assert proc.poll() is None and time.monotonic() < deadline, "the lock holder didn't start"
        time.sleep(0.05)
    return proc


def _overdue_record(vault: Path, pid: int, started: datetime) -> Path:
    from scout.lab import runs

    path = runs.record_path(vault, SLUG, EID, os.environ)
    runs.write(path, runs.RunRecord(started.isoformat(), (started + timedelta(hours=1)).isoformat(), pid))
    return path


def _no_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any signal but 0 (the existence probe) fails the test."""

    def kill(pid: int, sig: int) -> None:
        if sig != 0:
            raise AssertionError(f"signal {sig} sent to {pid}")
        _REAL_KILL(pid, 0)

    monkeypatch.setattr(os, "kill", kill)


@needs_libproc
def test_an_overrun_kill_spares_a_pid_that_doesnt_hold_the_lock_file(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record names a live process of this test's that doesn't have the lock file open (a reused pid), while
    something else holds the lock: it is never signalled, and the record is settled as a gone one's."""
    other = subprocess.Popen(["/bin/sleep", "60"])
    held = _HeldLock(vault)
    try:
        started = datetime.now(UTC) + timedelta(seconds=1)
        _overdue_record(vault, other.pid, started)
        _no_signals(monkeypatch)
        reply = _broker(vault, now=started + timedelta(hours=2)).handle(dict(STATUS))
        monkeypatch.undo()
        assert other.poll() is None  # untouched
        assert reply.exit == 0 and reply.stderr == f"experiment running: another run of {EID} holds it\n"
    finally:
        held.close()
        other.kill()
        other.wait()


@needs_libproc
def test_an_overrun_kill_spares_a_pid_started_after_the_record(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pid has the lock file open but started after the record was written: not the record's supervisor."""
    held = _HeldLock(vault)
    proc = _holder(vault, tmp_path, flock=False)
    try:
        started = datetime.now(UTC) - timedelta(hours=1)
        _overdue_record(vault, proc.pid, started)
        _no_signals(monkeypatch)
        reply = _broker(vault, now=started + timedelta(hours=2)).handle(dict(STATUS))
        monkeypatch.undo()
        assert proc.poll() is None
        assert reply.stderr == f"experiment running: another run of {EID} holds it\n"
    finally:
        held.close()
        proc.kill()
        proc.wait()


@needs_libproc
def test_an_overrun_supervisor_that_proves_itself_is_killed(vault: Path, tmp_path: Path) -> None:
    """A process of this test's that holds the lock file open and locked, started before the record: SIGKILLed,
    swept as a gone supervisor, and recorded ``not run``; it no longer holds a ``max_detached`` slot."""
    from scout.lab import runs

    proc = _holder(vault, tmp_path, flock=True)
    try:
        started = datetime.now(UTC) + timedelta(seconds=1)
        path = _overdue_record(vault, proc.pid, started)
        reply = _broker(vault, now=started + timedelta(hours=1, minutes=11)).handle(dict(STATUS))
        assert proc.wait(timeout=10) == -signal.SIGKILL
        assert reply.exit == broker.EXIT_NOT_RUN
        assert reply.stderr == "experiment not run: the supervisor ran past its deadline\n"
        rec = runs.read(path)
        assert (
            rec is not None
            and rec.outcome is not None
            and rec.outcome["reason"] == "the supervisor ran past its deadline"
        )
        assert runs.live(vault, os.environ) == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_a_second_start_of_a_running_detached_experiment_says_it_is_running(vault: Path, fake_run: FakeRun) -> None:
    _record(vault)  # no outcome, and its supervisor (this test's process) holds the lock
    with runner._run_lock(vault, SLUG, EID, os.environ):
        reply = _broker(vault).handle(dict(REQ))
    assert reply.exit == broker.EXIT_NOT_RUN
    assert reply.stderr == f"experiment not run: a detached run of {EID} is still running\n"
    assert fake_run.calls == []


def test_a_lock_busy_then_free_still_reads_as_another_run(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The run lock was busy when tried and free a moment later: the answer is still the busy one, never a
    contradictory "not run: … already running"."""
    import contextlib as _contextlib

    _record(vault, pid=_dead_pid())

    @_contextlib.contextmanager
    def busy(*_a: Any) -> Iterator[None]:
        raise runner.RunLockBusy(f"experiment {SLUG} {EID} is already running")
        yield

    monkeypatch.setattr(runner, "_run_lock", busy)
    reply = _broker(vault).handle(dict(STATUS))
    assert reply.exit == 0 and reply.stderr == f"experiment running: another run of {EID} holds it\n"


def test_status_when_another_run_holds_the_lock(vault: Path) -> None:
    """The record's supervisor is gone but an attached run of the experiment holds its lock: running, without the
    dead record's times."""
    _record(vault, pid=_dead_pid())
    with runner._run_lock(vault, SLUG, EID, os.environ):
        reply = _broker(vault).handle(dict(STATUS))
    assert reply.exit == 0 and reply.stderr == f"experiment running: another run of {EID} holds it\n"


def test_status_of_a_supervisor_gone_before_its_gate_sweeps_nothing(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_a: Any) -> bool:
        raise AssertionError("swept a run whose script never started")

    monkeypatch.setattr(runner, "_sweep", fail)
    _record(vault, pid=_dead_pid())  # no marker: the gate never opened
    reply = _broker(vault).handle(dict(STATUS))
    assert reply.stderr == "experiment not run: the supervisor stopped without a result\n"


def test_record_is_out_of_the_runs_reach(vault: Path) -> None:
    from scout.lab import runs

    env = dict(os.environ)
    rules = profile.build_rules(vault, env)
    for path in (runs.record_path(vault, SLUG, EID, env), runs.inputs_path(runs.record_path(vault, SLUG, EID, env))):
        assert profile.decide(rules, str(path)) == "deny"
        assert profile.decide(rules, str(path), "unlink") == "deny"
    assert runs.lock_path(vault, SLUG, EID, env) == runner.run_lock_path(vault, SLUG, EID, env)
