"""Lane findings in scout.health: a ported, read-only twin of session-lane-liveness.py.

A lane is every slot of one type (``manual`` excluded). It is judged by committed
output, not by last-fire.json: a commit counts when its subject names the lane or
when it landed inside a recorded run of that lane.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health
from scout.schedule import load_schedule

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

SCHEDULE = """\
schema_version: 1

slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 0

  evening-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "19:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 0

  dreaming-nightly:
    type: dreaming
    runner: run-dreaming.sh
    fires_at_local: "22:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 0

  weekly-research:
    type: research
    runner: run-research.sh
    fires_at_local: "02:00"
    weekdays: [Wed]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 0

  ad-hoc:
    type: manual
    runner: run-scout.sh
    fires_at_local: "00:00"
    weekdays: [Mon]
    missed_window_hours: 1
    on_miss: skip
    cooldown_minutes: 0
"""


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    (v / ".scout-logs").mkdir()
    (v / ".scout-state" / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    return v


@pytest.fixture
def schedule(vault: Path):
    return load_schedule(vault / ".scout-state" / "schedule.yaml")


def _row(slot: str, *, days_ago: float, minutes: int = 30, log: str | None = None) -> health.OutcomeRow:
    started = ago(days_ago)
    return health.OutcomeRow(
        slot=slot,
        mode=slot,
        started_at=started,
        finished_at=started + timedelta(minutes=minutes),
        exit_code=0,
        failure_class="",
        outcome="ok",
        log=log,
        commits_in_window=None,
        newest_commit=None,
    )


def _healthy() -> list[tuple[datetime, str]]:
    return [
        (ago(1), "briefing [08:00]: done"),
        (ago(1), "consolidation [19:00]: done"),
        (ago(1), "dreaming [22:00]: done"),
        (ago(1), "research [02:00]: done"),
    ]


def _lanes(schedule, vault, commits, *, rows=(), fires=None):
    found = health.lane_findings(vault, schedule, list(rows), fires or {}, now=NOW, commits=commits)
    return {f.id: f for f in found}


# ---------- verdicts ----------


def test_lanes_that_committed_recently_raise_nothing(vault, schedule):
    assert _lanes(schedule, vault, _healthy()) == {}


def test_a_lane_past_its_warn_budget_is_degraded_but_silent(vault, schedule):
    commits = [c for c in _healthy() if not c[1].startswith("dreaming")] + [(ago(5), "dreaming [22:00]: done")]

    assert _lanes(schedule, vault, commits) == {}


def test_a_dark_lane_is_red_and_says_how_long(vault, schedule):
    commits = [c for c in _healthy() if not c[1].startswith("dreaming")] + [(ago(8), "dreaming [22:00]: done")]

    found = _lanes(schedule, vault, commits, fires={"dreaming-nightly": ago(9)})

    f = found["lane.dark:dreaming"]
    assert (f.severity, f.source, f.since) == ("red", "git", ago(8))
    assert f.summary == "dreaming has produced no commits in 8 days"
    assert set(found) == {"lane.dark:dreaming"}


def test_a_lane_that_fires_but_commits_nothing_is_red(vault, schedule):
    """Past its dark budget yet still firing: a broken lane, as the script's exit 3 said."""
    commits = [c for c in _healthy() if not c[1].startswith("dreaming")] + [(ago(30), "dreaming [22:00]: done")]

    found = _lanes(schedule, vault, commits, fires={"dreaming-nightly": ago(0.4)})

    f = found["lane.silent:dreaming"]
    assert f.severity == "red"
    assert f.summary == "dreaming has produced no commits in 30 days"
    assert "firing" in f.fix and "budget" in f.fix


def test_a_lane_with_no_output_at_all_is_judged_only_once_observed_long_enough(vault, schedule):
    commits = [c for c in _healthy() if not c[1].startswith("consolidation")]
    young = [_row("evening-consolidation", days_ago=1)]
    assert _lanes(schedule, vault, commits, rows=young, fires={"evening-consolidation": ago(1)}) == {}
    # no ledger row for the lane at all: still nothing to judge by
    assert _lanes(schedule, vault, commits, fires={"evening-consolidation": ago(0.1)}) == {}

    old = [_row("evening-consolidation", days_ago=d) for d in (9, 6, 3, 1)]
    found = _lanes(schedule, vault, commits, rows=old, fires={"evening-consolidation": ago(1)})

    f = found["lane.silent:consolidation"]
    assert f.since is None
    assert f.summary == "consolidation has no commits on record"


def test_a_lane_that_stopped_firing_with_no_output_is_dark(vault, schedule):
    commits = [c for c in _healthy() if not c[1].startswith("consolidation")]
    old = [_row("evening-consolidation", days_ago=d) for d in (9, 6)]

    found = _lanes(schedule, vault, commits, rows=old, fires={"evening-consolidation": ago(5)})

    assert set(found) == {"lane.dark:consolidation"}


def test_a_weekly_lane_is_widened_by_its_gap(vault, schedule):
    others = [c for c in _healthy() if not c[1].startswith("research")]

    assert _lanes(schedule, vault, others + [(ago(7.5), "research [02:00]: done")]) == {}
    found = _lanes(schedule, vault, others + [(ago(9), "research [02:00]: done")])

    assert set(found) == {"lane.dark:research"}


@pytest.mark.parametrize(
    ("weekdays", "budgets"),
    [
        (("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), (3, 7)),
        (("Mon", "Tue", "Wed", "Thu", "Fri"), (3, 7)),
        (("Mon",), (7, 8)),
        (("Mon", "Thu"), (4, 7)),
        ((), (3, 7)),
    ],
)
def test_budgets_widen_to_fit_a_sparse_schedule(weekdays, budgets):
    assert health.lane_budgets("research", weekdays) == budgets
    assert health.lane_budgets("briefing", ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")) == (2, 4)
    assert health.lane_budgets("custom", ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")) == (3, 7)


def test_manual_slots_are_not_a_lane(vault, schedule):
    commits = _healthy() + [(ago(40), "manual: poked")]

    assert _lanes(schedule, vault, commits, fires={"ad-hoc": ago(0.1)}) == {}


# ---------- attribution ----------


def test_a_commit_inside_a_recorded_run_is_that_lanes_output(vault, schedule):
    commits = [c for c in _healthy() if not c[1].startswith("consolidation")]
    commits.append((ago(0.5) + timedelta(minutes=10), "Update action items"))
    rows = [_row("evening-consolidation", days_ago=0.5)]
    # stale enough to be dark if the unprefixed commit were not credited
    rows.append(_row("evening-consolidation", days_ago=12))

    assert _lanes(schedule, vault, commits, rows=rows, fires={"evening-consolidation": ago(5)}) == {}


def test_an_unscheduled_run_of_a_lane_counts_for_that_lane(vault, schedule):
    commits = [c for c in _healthy() if not c[1].startswith("dreaming")]
    commits.append((ago(0.5) + timedelta(minutes=5), "tidy the KB"))
    rows = [_row("dreaming-manual", days_ago=0.5), _row("dreaming-nightly", days_ago=12)]

    assert _lanes(schedule, vault, commits, rows=rows, fires={"dreaming-nightly": ago(5)}) == {}


def test_a_row_without_a_real_start_opens_no_window(vault, schedule):
    """run-outcome.sh writes started_at 0 when it can't read the start; such a row
    must not span 1970..now and credit every unnamed commit to its lane."""
    commits = [c for c in _healthy() if not c[1].startswith("consolidation")] + [(ago(1), "Update action items")]
    sentinel = health.OutcomeRow(
        slot="evening-consolidation",
        mode="evening-consolidation",
        started_at=datetime.fromtimestamp(0, UTC),
        finished_at=ago(0.5),
        exit_code=0,
        failure_class="",
        outcome="ok",
        log=None,
        commits_in_window=None,
        newest_commit=None,
    )
    rows = [sentinel, _row("evening-consolidation", days_ago=12)]

    found = _lanes(schedule, vault, commits, rows=rows, fires={"evening-consolidation": ago(5)})

    assert set(found) == {"lane.dark:consolidation"}


def test_a_run_of_no_known_lane_credits_nobody(vault, schedule):
    commits = [c for c in _healthy() if not c[1].startswith("dreaming")] + [(ago(0.5), "tidy the KB")]
    rows = [_row("chores", days_ago=0.5), _row("dreaming-nightly", days_ago=12)]

    found = _lanes(schedule, vault, commits, rows=rows, fires={"dreaming-nightly": ago(5)})

    assert set(found) == {"lane.dark:dreaming"}


def test_a_subject_naming_one_lane_beats_the_run_window_of_another(vault, schedule):
    commits = [c for c in _healthy() if not c[1].startswith("consolidation")]
    commits.append((ago(0.5) + timedelta(minutes=10), "dreaming [22:00]: tidy the KB"))
    rows = [_row("evening-consolidation", days_ago=0.5), _row("evening-consolidation", days_ago=12)]

    found = _lanes(schedule, vault, commits, rows=rows, fires={"evening-consolidation": ago(5)})

    assert set(found) == {"lane.dark:consolidation"}


def test_a_commit_outside_every_run_is_nobodys_output(vault, schedule):
    commits = [c for c in _healthy() if not c[1].startswith("consolidation")] + [(ago(1), "Update action items")]
    rows = [_row("evening-consolidation", days_ago=d) for d in (12, 10)]

    found = _lanes(schedule, vault, commits, rows=rows, fires={"evening-consolidation": ago(5)})

    assert set(found) == {"lane.dark:consolidation"}


def test_an_empty_subject_is_attributed_by_window_only(vault, schedule):
    commits = _healthy() + [(ago(2), "")]

    assert _lanes(schedule, vault, commits) == {}


# ---------- skipping ----------


def test_no_schedule_no_commits_no_findings(vault, schedule):
    assert health.lane_findings(vault, None, [], {}, now=NOW, commits=_healthy()) == []
    assert health.lane_findings(vault, schedule, [], {}, now=NOW, commits=[]) == []


# ---------- the newest runner log ----------


def _dark_dreaming(schedule, vault, **kw):
    commits = [c for c in _healthy() if not c[1].startswith("dreaming")] + [(ago(30), "dreaming [22:00]: done")]
    return _lanes(schedule, vault, commits, fires={"dreaming-nightly": ago(9)}, **kw)["lane.dark:dreaming"]


@pytest.mark.parametrize(
    ("log_text", "verdict"),
    [
        ("=== Budget check: skipping this run ===\n", "budget-skip"),
        ("Failed to authenticate\n", "AUTH FAILURE"),
        ("=== Another Scout session running (PID 42) — skipping ===\n", "lock-skip"),
        ("=== Scout run finished (exit code: 1, duration: 3s) ===\n", "exit 1"),
        ("starting...\n", "no completion line"),
    ],
)
def test_the_newest_runner_log_explains_a_silent_lane(vault, schedule, log_text, verdict):
    logs = vault / ".scout-logs"
    (logs / "dreaming-2026-01-04_22-00.log").write_text("(exit code: 0, duration: 900s)\n", encoding="utf-8")
    (logs / "dreaming-2026-01-05_22-00.log").write_text(log_text, encoding="utf-8")
    (logs / "dreaming-notes.log").write_text("not a runner log\n", encoding="utf-8")

    f = _dark_dreaming(schedule, vault)

    assert (
        f.summary
        == f"dreaming has produced no commits in 30 days; newest run log: dreaming-2026-01-05_22-00.log: {verdict}"
    )


def test_a_shared_runner_log_is_only_blamed_on_the_lane_that_ran_it(vault, schedule):
    logs = vault / ".scout-logs"
    commits = [c for c in _healthy() if not c[1].startswith("consolidation")]
    rows = []
    for days_ago, name in ((9, "scout-2026-01-01_19-00.log"), (5, "scout-2026-01-05_19-00.log")):
        rows.append(_row("evening-consolidation", days_ago=days_ago, log=name))
        (logs / name).write_text("Failed to authenticate\n", encoding="utf-8")
    (logs / "scout-2026-01-10_19-00.log").write_text("=== Budget check: skipping this run ===\n", encoding="utf-8")

    found = _lanes(schedule, vault, commits, rows=rows, fires={"evening-consolidation": ago(1)})

    assert found["lane.silent:consolidation"].summary.endswith(
        "newest run log: scout-2026-01-05_19-00.log: AUTH FAILURE"
    )


def test_an_unreadable_log_is_left_out_of_the_summary(vault, schedule):
    (vault / ".scout-logs" / "dreaming-2026-01-05_22-00.log").mkdir()  # reading a directory raises OSError

    assert _dark_dreaming(schedule, vault).summary == "dreaming has produced no commits in 30 days"


# ---------- recent_commits ----------


def _git(vault: Path, *args: str, when: datetime | None = None) -> None:
    stamp = (when or NOW).isoformat()
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_AUTHOR_NAME": "Scout Bot",
        "GIT_AUTHOR_EMAIL": "alex@example.com",
        "GIT_COMMITTER_NAME": "Scout Bot",
        "GIT_COMMITTER_EMAIL": "alex@example.com",
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    }
    subprocess.run(["git", "-C", str(vault), *args], env=env, check=True, capture_output=True)


def _commit(vault: Path, subject: str, *, days_ago: float) -> None:
    _git(vault, "commit", "-q", "--allow-empty", "-m", subject, when=ago(days_ago))


def test_recent_commits_reads_time_and_subject_newest_first(vault):
    _git(vault, "init", "-q")
    _commit(vault, "briefing [08:00]: one", days_ago=2)
    _commit(vault, "dreaming [22:00]: two", days_ago=1)

    got = health.recent_commits(vault)

    assert got == [(ago(1), "dreaming [22:00]: two"), (ago(2), "briefing [08:00]: one")]
    assert len(health.recent_commits(vault, limit=1)) == 1


def test_recent_commits_is_empty_without_a_repo(vault):
    assert health.recent_commits(vault) == []


def test_recent_commits_ignores_a_repo_the_vault_merely_sits_inside(tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "Scout"
    inner.mkdir(parents=True)
    _git(outer, "init", "-q")
    _commit(outer, "dreaming [22:00]: not ours", days_ago=1)

    assert health.recent_commits(inner) == []


def test_recent_commits_survives_a_missing_git(vault, monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(health.subprocess, "run", boom)

    assert health.recent_commits(vault) == []


def test_recent_commits_ignores_an_outer_git_dir(vault, monkeypatch):
    _git(vault, "init", "-q")
    _commit(vault, "dreaming [22:00]: ours", days_ago=1)
    other = vault.parent / "other"
    other.mkdir()
    _git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    assert [s for _, s in health.recent_commits(vault)] == ["dreaming [22:00]: ours"]


# ---------- through evaluate() ----------


@pytest.fixture
def live(vault, monkeypatch):
    monkeypatch.setattr(health, "vault_birth", lambda _v: ago(60))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: True)
    old = ago(60).timestamp()
    os.utime(vault / ".scout-state" / "schedule.yaml", (old, old))
    tick = vault / ".scout-logs" / f"schedule-events-{NOW.date().isoformat()}.jsonl"
    stamp = (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    tick.write_text(
        json.dumps({"id": "x", "ts": stamp, "kind": "schedule.tick.completed", "source": "t", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    _git(vault, "init", "-q")
    return vault


def test_evaluate_reports_a_dark_lane_from_real_git(live):
    for subject, days in (
        ("briefing [08:00]: done", 1),
        ("consolidation [19:00]: done", 1),
        ("research [02:00]: done", 1),
        ("dreaming [22:00]: done", 30),
    ):
        _commit(live, subject, days_ago=days)

    report = health.evaluate(live, now=NOW)

    lane = [f for f in report.findings if f.id.startswith("lane.")]
    assert [(f.id, f.severity) for f in lane] == [("lane.dark:dreaming", "red")]
    assert report.severity == "red"


def _paused_ledger(vault: Path, *, limit_hit_days_ago: float) -> None:
    """The ledger of a weekly usage limit: runs failed on it, then the gate held them."""
    hit = int(ago(limit_hit_days_ago).timestamp())
    resets = int((NOW + timedelta(days=2)).timestamp())
    base = {"slot": "morning-briefing", "exit_code": 1, "outcome": "failed", "failure_class": "rate_limited"}
    rows = [
        {**base, "started_at": hit - 60, "finished_at": hit},
        {**base, "started_at": hit + 3600, "finished_at": hit + 3660, "resets_at": resets},
    ]
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def test_a_lane_gone_dark_during_a_usage_limit_pause_is_held_back(live):
    """#424: a weekly limit pauses every lane for days. A lane that was inside its
    budget when the limit hit is dark because of the pause, which runs.paused names."""
    for subject in ("briefing [08:00]: done", "consolidation [19:00]: done", "research [02:00]: done"):
        _commit(live, subject, days_ago=6)
    _commit(live, "dreaming [22:00]: done", days_ago=6)
    _paused_ledger(live, limit_hit_days_ago=5)

    report = health.evaluate(live, now=NOW)

    assert not [f for f in report.findings if f.id.startswith("lane.")]
    assert {f.id: f.severity for f in report.findings if f.id.startswith("runs.")} == {"runs.paused": "yellow"}


def test_a_lane_already_dark_before_the_pause_is_still_reported(live):
    for subject in ("briefing [08:00]: done", "consolidation [19:00]: done", "research [02:00]: done"):
        _commit(live, subject, days_ago=6)
    _commit(live, "dreaming [22:00]: done", days_ago=30)
    _paused_ledger(live, limit_hit_days_ago=5)

    report = health.evaluate(live, now=NOW)

    lane = [f for f in report.findings if f.id.startswith("lane.")]
    assert [(f.id, f.severity) for f in lane] == [("lane.dark:dreaming", "red")]


def test_evaluate_runs_git_log_once_and_loads_the_schedule_once(live, monkeypatch):
    calls = {"commits": 0, "schedule": 0}
    real_commits, real_load = health.recent_commits, health.load_schedule

    def counting_commits(*a, **k):
        calls["commits"] += 1
        return real_commits(*a, **k)

    def counting_load(*a, **k):
        calls["schedule"] += 1
        return real_load(*a, **k)

    monkeypatch.setattr(health, "recent_commits", counting_commits)
    monkeypatch.setattr(health, "load_schedule", counting_load)

    health.evaluate(live, now=NOW)

    assert calls == {"commits": 1, "schedule": 1}


def test_evaluate_skips_lanes_when_the_vault_is_not_a_repo(live):
    subprocess.run(["rm", "-rf", str(live / ".git")], check=True)

    report = health.evaluate(live, now=NOW)

    assert not [f for f in report.findings if f.id.startswith("lane.")]


def test_evaluate_without_a_schedule_has_no_lane_findings(live):
    (live / ".scout-state" / "schedule.yaml").unlink()

    assert health.evaluate(live, now=NOW).findings == ()


def test_the_newest_commit_of_a_lane_sets_its_age(vault, schedule):
    commits = _healthy() + [(ago(40), "dreaming [22:00]: older")]

    assert _lanes(schedule, vault, commits) == {}


def test_recent_commits_skips_lines_it_cannot_parse(vault, monkeypatch):
    def fake_run(cmd, **_k):
        out = str(vault) + "\n" if "rev-parse" in cmd else "garbage line\n1700000000\x1fdreaming: ok\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(health.subprocess, "run", fake_run)

    assert health.recent_commits(vault) == [(datetime.fromtimestamp(1700000000, UTC), "dreaming: ok")]


def test_schedule_findings_still_works_standalone(vault):
    assert health.schedule_findings(vault, [], now=NOW)[1] != ()
