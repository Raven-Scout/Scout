"""The inbox step the schedule tick starts (#550 V4b1): pull, commit the ledgers, apply within a budget.

Built on the apply tests' fixture: the anonymised privacy vault with a real mirror for ``grok-personal`` and a
daily file. The inbox is a local bare repository (tests only: ``SCOUT_INBOX_ALLOW_LOCAL``), and the agent
proposes through a real clone of it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import delegate_apply as da
from scout import delegate_inbox as di
from scout import delegate_inbox_sync as dis
from scout.cli import app
from scout.repo_sync import RepoSyncError

from .privacy_vault_helpers import git
from .test_delegate_apply import DAILY, DATE, TASKS, _commits, _git_commit, mirrored  # noqa: F401 - fixtures
from .test_delegate_mirror import vault  # noqa: F401 - the fixture

D = "grok-personal"
runner = CliRunner()
PLUGIN = Path(__file__).resolve().parents[3]


@pytest.fixture
def linked(mirrored: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:  # noqa: F811
    """The mirrored vault with a local inbox for grok-personal, and the agent's clone of it."""
    monkeypatch.setenv(di.ALLOW_LOCAL_ENV, "1")
    bare = tmp_path / "inbox.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    di.link(mirrored, D, str(bare))
    clone = tmp_path / "agent-inbox"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True, capture_output=True)
    return mirrored, clone


def _propose(clone: Path, op: str = "mark-done", tag: str = "AB12") -> str:
    env = {"SCOUT_READER": f"delegate:{D}", "SCOUT_INBOX_DIR": str(clone)}
    body = {"op": op, "tag": tag, "date": DATE, "args": {}}
    return di.propose("action_item_op", body, push=True, env=env).id


def _subject(v: Path, rev: str = "HEAD") -> str:
    return git(v, "log", "-1", "--format=%s", rev).strip()


def test_sync_pulls_commits_the_ledger_and_applies(linked: tuple[Path, Path]) -> None:
    v, clone = linked
    ulid = _propose(clone)
    r = dis.sync(v, commit=_git_commit)
    assert r.skipped is None and r.pulled[0]["taken"] == [ulid]
    assert r.applied[0]["outcomes"] == {ulid: "applied"}
    assert _subject(v) == f"inbox: mark-done [#AB12] from delegate-{D} ({ulid})"
    assert _subject(v, "HEAD~1") == dis.LEDGER_MESSAGE and r.ledger_commit
    tracked = set(git(v, "ls-files", "--", ".scout-inbox/agents").split())
    assert tracked == {".scout-inbox/agents/.gitignore", f".scout-inbox/agents/{D}/taken.ledger"}  # never a copy
    assert git(v, "status", "--porcelain", "--", ".scout-inbox", "action-items").strip() == ""
    again = dis.sync(v, commit=_git_commit)
    assert again.ledger_commit is None and again.applied[0]["outcomes"] == {}


def test_a_live_run_makes_commits_and_apply_wait(linked: tuple[Path, Path]) -> None:
    from scout.action_items import retitle

    v, clone = linked
    ulid = _propose(clone)
    with retitle.session_lock(v):
        r = dis.sync(v, commit=_git_commit)
    assert r.skipped == "busy" and r.pulled[0]["taken"] == [ulid] and r.applied == []
    assert "a run is live" in r.notes[-1]
    later = dis.sync(v, commit=_git_commit)
    assert later.applied[0]["outcomes"] == {ulid: "applied"}


def test_pull_never_holds_the_session_lock(linked: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.action_items import retitle

    v, clone = linked
    _propose(clone)
    real = di.pull_all
    free: list[bool] = []

    def checking(w: Path, **kw: object) -> list:  # type: ignore[type-arg]
        with retitle.session_lock(w):  # would refuse at once if the sync held it
            free.append(True)
        return real(w, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(di, "pull_all", checking)
    dis.sync(v, commit=_git_commit)
    assert free == [True]


def test_a_run_firing_during_the_apply_phase_waits_and_then_runs(
    linked: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The coordinator's condition 1: a slot that fires while the sync applies is never skipped. The runner waits
    for the lock (SCOUT_LOCK_WAIT_SECS, 30 minutes by default); the apply phase holds it for at most its budget."""
    from scout.action_items import retitle
    from scout.scripts.bootstrap_lock import acquire_lock_with_wait, release_lock

    v, _clone = linked
    holding = threading.Event()

    def slow_apply_all(w: Path, **kw: object) -> list:  # type: ignore[type-arg]
        with da._session(w):
            holding.set()
            time.sleep(0.5)
        return []

    monkeypatch.setattr(da, "apply_all", slow_apply_all)
    t = threading.Thread(target=dis.sync, args=(v,), kwargs={"commit": _git_commit})
    t.start()
    assert holding.wait(10)
    lock = v / retitle.SESSION_LOCK
    started = time.monotonic()
    acquire_lock_with_wait(lock, timeout_s=10, poll_s=1)  # as the runner does, only quicker
    waited = time.monotonic() - started
    release_lock(lock)
    t.join(10)
    assert 0 < waited < 10 and not t.is_alive()
    template = (PLUGIN / "templates" / "scripts" / "scout-lock.sh.tmpl").read_text(encoding="utf-8")
    wait = int(re.search(r'LOCK_WAIT_SECS="\$\{SCOUT_LOCK_WAIT_SECS:-(\d+)\}"', template)[1])  # type: ignore[index]
    assert wait > dis.APPLY_BUDGET_S * 10


def test_an_agnes_lease_that_isnt_granted_commits_nothing(linked: tuple[Path, Path]) -> None:
    from contextlib import contextmanager

    from scout import repo_lease

    v, clone = linked
    ulid = _propose(clone)
    head = git(v, "rev-parse", "HEAD").strip()
    for code, why in ((repo_lease.HELD_CODE, "lease_held"), ("remote_unverified", "lease_unavailable")):

        @contextmanager
        def refused(w: Path, code: str = code) -> Iterator[list[str]]:
            raise RepoSyncError(code, "refused: no lease")
            yield []  # pragma: no cover

        r = dis.sync(v, commit=_git_commit, lease=refused)
        assert r.skipped == why and r.applied == [] and git(v, "rev-parse", "HEAD").strip() == head
    assert ulid in json.dumps(di.load_state(v).taken)  # the pull still ran


def test_a_granted_lease_spans_the_commits_and_is_always_given_back(linked: tuple[Path, Path]) -> None:
    from contextlib import contextmanager

    v, clone = linked
    ulid = _propose(clone)
    events: list[str] = []

    @contextmanager
    def granted(w: Path) -> Iterator[list[str]]:
        events.append("acquire")
        try:
            yield ["lease: held"]
        finally:
            events.append("release")

    r = dis.sync(v, commit=_git_commit, lease=granted)
    assert (
        events == ["acquire", "release"] and r.applied[0]["outcomes"] == {ulid: "applied"} and "lease: held" in r.notes
    )
    _propose(clone, "clear-block", "EF56")

    def fails(w: Path, message: str, rels: list[str]) -> str:
        raise RuntimeError("helper failed")

    with pytest.raises(RepoSyncError):
        dis.sync(v, commit=fails, lease=granted)
    assert events[-1] == "release"  # given back even when a commit fails


def test_the_real_lease_takes_its_own_holder_and_gives_it_back(
    mirrored: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from scout import repo_lease, repo_sync

    assert list(_drain(dis.agnes_lease(mirrored))) == [[]]  # not linked: nothing to take
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(repo_sync, "load_state", lambda w: SimpleNamespace(agnes_url="https://agnes.example"))
    monkeypatch.setattr(repo_sync, "load_token", lambda w: "token")
    monkeypatch.setattr(repo_sync, "AgnesHost", lambda url, token: ("host", url))

    def acquire(w: Path, host: object, **kw: object) -> tuple[object, list[str]]:
        calls.append(("acquire", kw))
        return object(), ["lease: cleared a stale holder"]

    def release(w: Path, host: object, run_id: str) -> bool:
        calls.append(("release", {"run_id": run_id}))
        raise RepoSyncError("unreachable", "refused: Agnes is down")  # it expires after its TTL: never raised

    monkeypatch.setattr(repo_lease, "acquire_for_run", acquire)
    monkeypatch.setattr(repo_lease, "release", release)
    assert list(_drain(dis.agnes_lease(mirrored))) == [["lease: cleared a stale holder"]]
    (_, got), (_, gave) = calls
    assert got["what"] == "inbox-sync" and got["wait_s"] == 0 and str(got["run_id"]).startswith("inbox-sync-")
    assert gave["run_id"] == got["run_id"]


def _drain(cm: object) -> Iterator[object]:
    with cm as value:  # type: ignore[attr-defined]
        yield value


def test_sync_is_the_owners_and_needs_an_inbox(mirrored: Path) -> None:  # noqa: F811
    assert dis.sync(mirrored).skipped == "no_inbox"
    with pytest.raises(RepoSyncError) as e:
        dis.sync(mirrored, env={"SCOUT_MODE": "briefing"})
    assert e.value.code == "headless_refused"


def test_a_failed_ledger_commit_is_unstaged_and_tried_again(linked: tuple[Path, Path]) -> None:
    v, clone = linked
    _propose(clone)
    di.pull_all(v)

    def stages_then_fails(w: Path, message: str, rels: list[str]) -> str:
        git(w, "add", "--", *rels)
        raise RuntimeError("the helper failed")

    with pytest.raises(RepoSyncError) as e:
        dis.commit_ledgers(v, stages_then_fails)
    assert e.value.code == "commit_failed" and git(v, "diff", "--cached", "--name-only").strip() == ""
    assert dis.commit_ledgers(v, _git_commit)
    assert dis.commit_ledgers(v, _git_commit) is None  # nothing new


def test_ledger_paths_take_plain_files_only(linked: tuple[Path, Path], tmp_path: Path) -> None:
    v, clone = linked
    _propose(clone)
    di.pull_all(v)
    agents = v.joinpath(*di.INBOX_DIR)
    (agents / "notafolder").write_text("x", encoding="utf-8")
    (agents / "linked").symlink_to(tmp_path)
    assert dis.ledger_paths(v) == [".scout-inbox/agents/.gitignore", f".scout-inbox/agents/{D}/taken.ledger"]


# ----- the tick ---------------------------------------------------------------------------------


def test_due_respects_the_interval_and_the_slot_guard(linked: tuple[Path, Path], tmp_path: Path) -> None:
    v, _clone = linked
    now = dt.datetime(2026, 11, 19, 9, 0, tzinfo=dt.UTC)
    assert not dis.due(tmp_path, now=now)  # no inbox
    assert dis.due(v, now=now)  # never started
    assert not dis.due(v, now=now, slot_times=[now + dt.timedelta(minutes=3)])  # a slot fires soon
    assert dis.due(v, now=now, slot_times=[now + dt.timedelta(minutes=30), now - dt.timedelta(minutes=1)])
    dis.mark_started(v)
    real_now = dt.datetime.now(dt.UTC)
    assert not dis.due(v, now=real_now)
    assert dis.due(v, now=real_now + dis.SYNC_INTERVAL + dt.timedelta(seconds=1))


def test_the_tick_starts_a_sync_as_the_owner(linked: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import schedule_tick as st

    v, _clone = linked
    log_dir = v / ".scout-logs"
    started: list[Path] = []
    now = dt.datetime.now(dt.UTC)
    st._sync_inboxes(vault=v, log_dir=log_dir, now=now, spawn=lambda w, logs: started.append(w) or 4242)
    assert started == [v]
    st._sync_inboxes(vault=v, log_dir=log_dir, now=now, spawn=lambda w, logs: started.append(w) or 1)
    assert started == [v]  # not again within the interval

    seen: dict[str, object] = {}

    class FakePopen:
        def __init__(self, argv: list[str], **kw: object) -> None:
            seen["argv"], seen["env"], seen["cwd"] = argv, kw["env"], kw["cwd"]
            self.pid = 7

    monkeypatch.setenv("SCOUT_MODE", "briefing")
    monkeypatch.setenv("SCOUT_READER", "autonomous")
    monkeypatch.setattr(st.subprocess, "Popen", FakePopen)
    assert st._spawn_inbox_sync(v, log_dir) == 7
    env = seen["env"]
    assert isinstance(env, dict) and "SCOUT_MODE" not in env and "SCOUT_READER" not in env
    assert env["SCOUT_DATA_DIR"] == str(v) and seen["cwd"] == str(Path.home())
    assert list(seen["argv"])[-2:] == ["inbox", "sync"]  # type: ignore[call-overload]


def test_the_tick_waits_when_a_slot_is_near(linked: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import schedule_tick as st

    v, _clone = linked

    class Soon:
        def target_today(self, *, now: dt.datetime) -> dt.datetime:
            return now + dt.timedelta(minutes=2)

    monkeypatch.setattr(st, "_load_or_default", lambda w: {"briefing": Soon()})
    started: list[Path] = []
    st._sync_inboxes(vault=v, log_dir=v / ".scout-logs", now=dt.datetime.now(dt.UTC),
                     spawn=lambda w, logs: started.append(w) or 1)  # fmt: skip
    assert started == []


def test_the_tick_never_raises_on_a_broken_inbox(linked: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import schedule_tick as st

    v, _clone = linked

    def broken(w: Path) -> object:
        raise ValueError("bad schedule")

    monkeypatch.setattr(st, "_load_or_default", broken)
    st._sync_inboxes(vault=v, log_dir=v / ".scout-logs", now=dt.datetime.now(dt.UTC))
    rows = [
        json.loads(x) for f in (v / ".scout-logs").glob("schedule-events-*.jsonl") for x in f.read_text().splitlines()
    ]
    assert any(r["kind"] == "delegate_inbox.spawn_failed" for r in rows)
    st._sync_inboxes(vault=v / "nowhere", log_dir=v / ".scout-logs", now=dt.datetime.now(dt.UTC))  # no inbox


# ----- health, the doctor, the runs' view --------------------------------------------------------


def test_health_and_the_doctor_name_what_needs_the_owner(linked: tuple[Path, Path]) -> None:
    from scout import health
    from scout.scripts import bootstrap_doctor as bd

    v, clone = linked
    assert health.delegate_inbox_findings(v) == []
    _, notes = bd._check_delegate_inboxes(vault=v, now=None)
    assert notes == [f"delegate inbox {D}: never pulled; 0 wait"]
    ulid = _propose(clone)
    dis.sync(v, commit=_git_commit)
    folder = v.joinpath(*di.INBOX_DIR, D)
    (folder / "01M7T0000000000000000000ZZ.json").write_text("{}", encoding="utf-8")  # planted
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=2)
    ids = {f.id for f in health.delegate_inbox_findings(v, now=later)}
    assert ids == {"delegate.inbox_stalled", "delegate.inbox_untaken"}
    warnings, notes = bd._check_delegate_inboxes(vault=v, now=later)
    assert any("not pulled for over a day" in w for w in warnings) and any("never taken" in w for w in warnings)
    assert "applied 1" in notes[0] and ulid not in " ".join(notes)  # counts, never ids or text
    state = di.load_state(v)
    state.inboxes[D].last_error = {"code": "inbox_rewritten", "at": "x"}
    di.save_state(v, state)
    assert {f.id for f in health.delegate_inbox_findings(v)} == {"delegate.inbox_failed"}
    assert any("inbox_rewritten" in w for w in bd._check_delegate_inboxes(vault=v, now=None)[0])
    state_file = di.state_path(v)
    state_file.write_text("{}", encoding="utf-8")
    assert [f.id for f in health.delegate_inbox_findings(v)] == ["delegate.inbox_state"]
    assert bd._check_delegate_inboxes(vault=v, now=None)[0][0].startswith("delegate inboxes: refused")


def test_runs_see_what_agents_changed_by_id_only(linked: tuple[Path, Path]) -> None:
    from scout.scripts import pre_session_data as psd

    v, clone = linked
    ulid = _propose(clone)
    dis.sync(v, commit=_git_commit)
    rows = psd.get_delegate_changes(v, dt.date.fromisoformat(DATE))
    assert rows == [
        {
            "delegate": D,
            "op": "mark-done",
            "tag": "AB12",
            "date": DATE,
            "commit": git(v, "rev-parse", "HEAD").strip(),
            "ulid": ulid,
            "at": rows[0]["at"],
        }
    ]
    assert psd.get_delegate_changes(v, dt.date(2026, 1, 1)) == []
    di.state_path(v).write_text("{}", encoding="utf-8")
    assert psd.get_delegate_changes(v, dt.date.fromisoformat(DATE)) == []  # unverified: no rows, never a guess


def test_cli_inbox_sync(linked: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    v, clone = linked
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    ulid = _propose(clone)
    r = runner.invoke(app, ["inbox", "sync", "--json"])
    assert r.exit_code == 0, r.output
    doc = json.loads(r.output)
    assert doc["applied"][0]["outcomes"] == {ulid: "applied"} and doc["ledger_commit"]
    _propose(clone, "clear-block", "EF56")
    r = runner.invoke(app, ["inbox", "sync"])
    assert r.exit_code == 0 and "committed the ledgers" in r.output

    def broken(*a: object, **k: object) -> object:
        raise OSError("disk went away")

    monkeypatch.setattr(dis, "sync", broken)
    r = runner.invoke(app, ["inbox", "sync", "--json"])
    assert r.exit_code == 2 and json.loads(r.output)["code"] == "sync_failed"
    os.environ.pop("SCOUT_MODE", None)


def test_rows_ledger_paths_and_a_commit_that_lands_elsewhere(linked: tuple[Path, Path], tmp_path: Path) -> None:
    v, clone = linked
    failure = RepoSyncError("inbox_rebound", "refused: x")
    failure.detail["delegate"] = D
    assert dis._row(failure) == {"ok": False, "delegate": D, "code": "inbox_rebound"}
    assert dis.ledger_paths(tmp_path) == []  # no copies folder
    agents = v.joinpath(*di.INBOX_DIR)
    (agents / "empty-delegate").mkdir(parents=True)  # a folder with no ledger: nothing to commit from it
    assert dis.ledger_paths(v) == []
    os.chmod(agents, 0o300)  # listable no more
    try:
        assert dis.ledger_paths(v) == []
    finally:
        os.chmod(agents, 0o755)
    _propose(clone)
    di.pull_all(v)

    def lands_elsewhere(w: Path, message: str, rels: list[str]) -> str:
        git(w, "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "--no-verify", "-m", "someone else")
        raise RuntimeError("the helper crashed")

    with pytest.raises(RepoSyncError) as e:
        dis.commit_ledgers(v, lands_elsewhere)
    assert e.value.code == "commit_failed"
