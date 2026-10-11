from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

REAL_BIRTH = health.vault_birth if hasattr(health, "vault_birth") else None

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)  # a Sunday
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


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    (v / ".scout-logs").mkdir()
    (v / ".scout-state" / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    old = (NOW - timedelta(days=60)).timestamp()
    os.utime(v / ".scout-state" / "schedule.yaml", (old, old))
    monkeypatch.setattr(health, "vault_birth", lambda _v: NOW - timedelta(days=60))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: True, raising=False)
    _tick(v, NOW - timedelta(minutes=5))
    return v


def _fires(v: Path, **fires: datetime) -> None:
    (v / ".scout-state" / "last-fire.json").write_text(
        json.dumps({"schema_version": 1, "last_fire": {k.replace("_", "-"): health.iso_z(t) for k, t in fires.items()}})
    )


def _tick(v: Path, at: datetime) -> None:
    path = v / ".scout-logs" / f"schedule-events-{at.date().isoformat()}.jsonl"
    with path.open("a") as fh:
        fh.write(
            json.dumps(
                {
                    "id": "x",
                    "ts": at.isoformat().replace("+00:00", "Z"),
                    "kind": "schedule.tick.completed",
                    "source": "t",
                    "payload": {},
                }
            )
            + "\n"
        )


def _ledger(v: Path, *rows: tuple[str, datetime]) -> None:
    with (v / ".scout-logs" / "run-outcomes.jsonl").open("a") as fh:
        for slot, start in rows:
            fh.write(
                json.dumps(
                    {
                        "slot": slot,
                        "mode": "scout",
                        "started_at": int(start.timestamp()),
                        "finished_at": int(start.timestamp()) + 60,
                        "exit_code": 0,
                        "failure_class": "ok",
                        "log": None,
                    }
                )
                + "\n"
            )


def _ids(v: Path) -> dict[str, str]:
    return {f.id: f.severity for f in health.evaluate(v, now=NOW).findings}


def _h(hours: float) -> timedelta:
    return timedelta(hours=hours)


def _healthy(v: Path) -> None:
    b, r = NOW - _h(4), NOW - timedelta(days=4)
    _fires(v, morning_briefing=b, weekly_research=r)
    _ledger(v, ("weekly-research", r), ("morning-briefing", b))


def test_longest_gap_hours():
    assert health.longest_gap_hours(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")) == 24
    assert health.longest_gap_hours(("Wed",)) == 168
    assert health.longest_gap_hours(("Mon", "Tue", "Wed", "Thu", "Fri")) == 72
    assert health.longest_gap_hours(("Sat", "Sun")) == 144
    assert health.longest_gap_hours(()) == 24


def test_healthy_vault_is_green(vault):
    _healthy(vault)
    report = health.evaluate(vault, now=NOW)
    assert _ids(vault) == {}
    assert {s.key for s in report.slots} == {"morning-briefing", "weekly-research"}
    assert all(s.last_outcome is not None and s.last_fire is not None for s in report.slots)


def test_the_193_shape_a_slot_missing_from_last_fire_is_never_fired(vault):
    _fires(vault, morning_briefing=NOW - _h(4))
    _ledger(vault, ("morning-briefing", NOW - _h(4)))
    assert _ids(vault) == {"slot.never_fired:weekly-research": "yellow"}


def test_a_young_vault_does_not_report_never_fired(vault, monkeypatch):
    _fires(vault, morning_briefing=NOW - _h(4))
    _ledger(vault, ("morning-briefing", NOW - _h(4)))
    monkeypatch.setattr(health, "vault_birth", lambda _v: NOW - _h(100))
    assert "slot.never_fired:weekly-research" not in _ids(vault)
    monkeypatch.setattr(health, "vault_birth", lambda _v: NOW - _h(173))
    assert "slot.never_fired:weekly-research" in _ids(vault)


def test_a_recently_edited_schedule_resets_never_fired(vault):
    _fires(vault, morning_briefing=NOW - _h(4))
    _ledger(vault, ("morning-briefing", NOW - _h(4)))
    recent = (NOW - _h(1)).timestamp()
    os.utime(vault / ".scout-state" / "schedule.yaml", (recent, recent))
    assert "slot.never_fired:weekly-research" not in _ids(vault)


def test_unknown_vault_birth_skips_never_fired(vault, monkeypatch):
    _fires(vault, morning_briefing=NOW - _h(4))
    _ledger(vault, ("morning-briefing", NOW - _h(4)))
    monkeypatch.setattr(health, "vault_birth", lambda _v: None)
    assert _ids(vault) == {}


def test_stale_boundary_is_exactly_twice_the_gap_plus_window(vault):
    fresh = NOW - timedelta(days=1)
    _fires(vault, morning_briefing=NOW - _h(52), weekly_research=fresh)
    assert "slot.stale:morning-briefing" not in _ids(vault)
    _fires(vault, morning_briefing=NOW - _h(52) - timedelta(seconds=1), weekly_research=fresh)
    ids = _ids(vault)
    assert ids["slot.stale:morning-briefing"] == "yellow"
    assert "slot.stale:weekly-research" not in ids


def test_every_slot_stale_is_red(vault):
    _fires(vault, morning_briefing=NOW - timedelta(days=3), weekly_research=NOW - timedelta(days=30))
    ids = _ids(vault)
    assert ids["slot.stale:morning-briefing"] == "red"
    assert ids["slot.stale:weekly-research"] == "red"
    stale = {f.id: f for f in health.evaluate(vault, now=NOW).findings}
    assert stale["slot.stale:morning-briefing"].summary == "morning-briefing last fired 3 d ago."
    assert stale["slot.stale:morning-briefing"].since == NOW - timedelta(days=3)
    assert stale["slot.stale:morning-briefing"].source == "schedule"


def test_stale_hours_age_format(vault):
    _fires(vault, morning_briefing=NOW - _h(53), weekly_research=NOW - _h(2))
    f = {f.id: f for f in health.evaluate(vault, now=NOW).findings}
    assert f["slot.stale:morning-briefing"].summary == "morning-briefing last fired 53 h ago."


def test_the_233_shape_fresh_fires_keep_slots_green_while_auth_is_red(vault):
    b, r = NOW - _h(4), NOW - timedelta(days=4)
    _fires(vault, morning_briefing=b, weekly_research=r)
    with (vault / ".scout-logs" / "run-outcomes.jsonl").open("a") as fh:
        for slot, start in (("weekly-research", r), ("morning-briefing", b)):
            fh.write(
                json.dumps(
                    {
                        "slot": slot,
                        "mode": "scout",
                        "started_at": int(start.timestamp()),
                        "finished_at": int(start.timestamp()) + 60,
                        "exit_code": 1,
                        "failure_class": "oauth_expired",
                        "log": None,
                    }
                )
                + "\n"
            )
    assert _ids(vault) == {"auth.expired": "red"}


def test_fired_silent_after_the_window(vault):
    _fires(vault, morning_briefing=NOW - _h(5), weekly_research=NOW - timedelta(days=1))
    _ledger(vault, ("morning-briefing", NOW - _h(30)), ("weekly-research", NOW - timedelta(days=1)))
    f = {f.id: f for f in health.evaluate(vault, now=NOW).findings}
    assert f["slot.fired_silent:morning-briefing"].severity == "yellow"
    assert f["slot.fired_silent:morning-briefing"].source == "ledger"
    assert f["slot.fired_silent:morning-briefing"].since == NOW - _h(5)
    assert "no run outcome was recorded" in f["slot.fired_silent:morning-briefing"].summary
    _fires(vault, morning_briefing=NOW - _h(3), weekly_research=NOW - timedelta(days=1))
    assert "slot.fired_silent:morning-briefing" not in _ids(vault)


def test_fired_silent_is_not_judged_before_the_ledger_existed(vault):
    _fires(vault, morning_briefing=NOW - _h(5), weekly_research=NOW - timedelta(days=1))
    _ledger(vault, ("weekly-research", NOW - _h(1)))
    assert "slot.fired_silent:morning-briefing" not in _ids(vault)


def test_a_row_starting_just_before_the_fire_counts(vault):
    fire = NOW - _h(5)
    _fires(vault, morning_briefing=fire, weekly_research=NOW - timedelta(days=1))
    _ledger(vault, ("morning-briefing", fire - timedelta(days=1)), ("morning-briefing", fire - timedelta(seconds=60)))
    assert "slot.fired_silent:morning-briefing" not in _ids(vault)


def test_scheduler_stalled_after_two_hours(vault):
    _healthy(vault)
    for p in (vault / ".scout-logs").glob("schedule-events-*"):
        p.unlink()
    _tick(vault, NOW - timedelta(hours=2, seconds=1))
    assert _ids(vault) == {"scheduler.stalled": "red"}
    f = health.evaluate(vault, now=NOW).findings[0]
    assert f.source == "events"
    assert f.since == NOW - timedelta(hours=2, seconds=1)
    for p in (vault / ".scout-logs").glob("schedule-events-*"):
        p.unlink()
    _tick(vault, NOW - timedelta(hours=1, minutes=59))
    assert "scheduler.stalled" not in _ids(vault)


def test_scheduler_never_ticked(vault, monkeypatch):
    _healthy(vault)
    for p in (vault / ".scout-logs").glob("schedule-events-*"):
        p.unlink()
    assert _ids(vault) == {"scheduler.stalled": "red"}
    f = health.evaluate(vault, now=NOW).findings[0]
    assert "never recorded a tick" in f.summary
    assert f.since is None
    monkeypatch.setattr(health, "vault_birth", lambda _v: NOW - _h(1))
    assert "scheduler.stalled" not in _ids(vault)
    monkeypatch.setattr(health, "vault_birth", lambda _v: None)
    assert "scheduler.stalled" not in _ids(vault)


def test_tick_events_across_utc_midnight(vault):
    _healthy(vault)
    for p in (vault / ".scout-logs").glob("schedule-events-*"):
        p.unlink()
    yesterday = datetime(2026, 10, 10, 23, 30, tzinfo=UTC)
    _tick(vault, yesterday)
    today = vault / ".scout-logs" / "schedule-events-2026-10-11.jsonl"
    today.write_text(
        json.dumps({"id": "y", "ts": "2026-10-11T00:05:00Z", "kind": "schedule.fire.started"})
        + "\nnot json\n"
        + json.dumps({"id": "z", "ts": "garbage", "kind": "schedule.tick.completed"})
        + "\n"
        + json.dumps({"id": "n", "kind": "schedule.tick.completed"})
        + "\n"
    )
    assert health.last_tick_completed(vault) == yesterday


def test_last_tick_completed_none_without_logs(tmp_path):
    assert health.last_tick_completed(tmp_path) is None


@pytest.mark.parametrize(
    "body",
    [
        "schema_version: 1\nslots: {bad: {type: nope}}\n",
        "schema_version: 1\nslots: [1, 2]\n",
        "schema_version: 1\nslots: {a: hello}\n",
    ],
)
def test_unloadable_schedule_skips_slot_findings_only(vault, body):
    (vault / ".scout-state" / "schedule.yaml").write_text(body)
    _ledger_failing = vault / ".scout-logs" / "run-outcomes.jsonl"
    with _ledger_failing.open("a") as fh:
        for i in range(2):
            t = int((NOW - _h(10 - i)).timestamp())
            fh.write(
                json.dumps(
                    {
                        "slot": "morning-briefing",
                        "mode": "scout",
                        "started_at": t,
                        "finished_at": t + 5,
                        "exit_code": 1,
                        "failure_class": "crash",
                        "log": None,
                    }
                )
                + "\n"
            )
    report = health.evaluate(vault, now=NOW)
    ids = {f.id for f in report.findings}
    assert "runs.failing" in ids
    assert not any(i.startswith("slot.") for i in ids)
    assert report.slots == ()


def test_missing_schedule_file_is_not_a_crash(vault):
    (vault / ".scout-state" / "schedule.yaml").unlink()
    assert health.evaluate(vault, now=NOW).slots == ()


def test_corrupt_last_fire_reads_as_no_fires(vault):
    (vault / ".scout-state" / "last-fire.json").write_text("{")
    assert health.read_last_fires(vault) == {}
    ids = _ids(vault)
    assert "slot.never_fired:morning-briefing" in ids
    assert "slot.never_fired:weekly-research" in ids


def test_read_last_fires_skips_bad_entries(vault):
    (vault / ".scout-state" / "last-fire.json").write_text(
        json.dumps({"last_fire": {"a": "2026-10-11T08:00:00Z", "b": "nope", "c": 5}})
    )
    assert health.read_last_fires(vault) == {"a": datetime(2026, 10, 11, 8, 0, tzinfo=UTC)}
    (vault / ".scout-state" / "last-fire.json").write_text(json.dumps([1]))
    assert health.read_last_fires(vault) == {}
    (vault / ".scout-state" / "last-fire.json").write_text(json.dumps({"last_fire": []}))
    assert health.read_last_fires(vault) == {}


def test_remote_runtime_slots_are_not_judged(vault):
    (vault / ".scout-state" / "schedule.yaml").write_text(
        SCHEDULE.replace("    runner: run-research.sh\n", "    runner: run-research.sh\n    runtime: remote\n")
    )
    _fires(vault, morning_briefing=NOW - _h(4))
    _ledger(vault, ("morning-briefing", NOW - _h(4)))
    assert _ids(vault) == {}


def test_vault_birth_uses_the_root_commit(tmp_path):
    real = REAL_BIRTH
    v = tmp_path / "repo"
    v.mkdir()
    assert real(v) is None
    cfg = v / "scout-config.yaml"
    cfg.write_text("x: 1\n")
    os.utime(cfg, (1600000000, 1600000000))
    assert real(v) == datetime.fromtimestamp(1600000000, UTC)
    env = {
        **os.environ,
        "GIT_COMMITTER_DATE": "@1700000000 +0000",
        "GIT_AUTHOR_DATE": "@1700000000 +0000",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        ["git", "-c", "user.name=A", "-c", "user.email=a@example.com", "commit", "-q", "-m", "x"],
    ):
        subprocess.run(cmd, cwd=v, env=env, check=True)
    assert real(v) == datetime.fromtimestamp(1700000000, UTC)


def test_vault_birth_ignores_an_enclosing_repo(tmp_path):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, env=env, check=True)
    subprocess.run(
        ["git", "-c", "user.name=A", "-c", "user.email=a@example.com", "commit", "-q", "--allow-empty", "-m", "x"],
        cwd=tmp_path,
        env=env,
        check=True,
    )
    inner = tmp_path / "Scout"
    inner.mkdir()
    assert REAL_BIRTH(inner) is None


def test_vault_birth_survives_git_failures(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert REAL_BIRTH(tmp_path) is None


def test_vault_birth_empty_repo_falls_back_to_config(tmp_path):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, env=env, check=True)
    assert REAL_BIRTH(tmp_path) is None
    cfg = tmp_path / "scout-config.yaml"
    cfg.write_text("x: 1\n")
    os.utime(cfg, (1600000000, 1600000000))
    assert REAL_BIRTH(tmp_path) == datetime.fromtimestamp(1600000000, UTC)


# ----- P1 final review fixes -------------------------------------------------


def _row(v: Path, slot: str, start: datetime, finish: datetime, *, raw_start: int | None = None) -> None:
    with (v / ".scout-logs" / "run-outcomes.jsonl").open("a") as fh:
        fh.write(
            json.dumps(
                {
                    "slot": slot,
                    "mode": "scout",
                    "started_at": int(start.timestamp()) if raw_start is None else raw_start,
                    "finished_at": int(finish.timestamp()),
                    "exit_code": 0,
                    "failure_class": "ok",
                    "log": None,
                }
            )
            + "\n"
        )


def _no_ticks(v: Path) -> None:
    for p in (v / ".scout-logs").glob("schedule-events-*"):
        p.unlink()


def test_a_session_end_fire_is_covered_by_the_run_that_ended(vault):
    """F1: last-fire.json records the Stop hook's session-end row, ~2 s before finished_at."""
    start = NOW - _h(6)
    finish = start + timedelta(minutes=20)
    _row(vault, "weekly-research", NOW - timedelta(days=1), NOW - timedelta(days=1) + timedelta(minutes=5))
    _row(vault, "morning-briefing", start, finish)
    _fires(vault, morning_briefing=finish - timedelta(seconds=2), weekly_research=NOW - timedelta(days=1))
    assert "slot.fired_silent:morning-briefing" not in _ids(vault)


def test_a_row_that_finished_long_before_the_fire_does_not_cover_it(vault):
    _row(vault, "morning-briefing", NOW - _h(30), NOW - _h(29))
    _row(vault, "weekly-research", NOW - timedelta(days=1), NOW - timedelta(days=1) + timedelta(minutes=5))
    _fires(vault, morning_briefing=NOW - _h(5), weekly_research=NOW - timedelta(days=1))
    assert _ids(vault)["slot.fired_silent:morning-briefing"] == "yellow"


def _silent_shape(v: Path) -> None:
    _fires(v, morning_briefing=NOW - _h(5), weekly_research=NOW - timedelta(days=1))
    _ledger(v, ("morning-briefing", NOW - _h(30)), ("weekly-research", NOW - timedelta(days=1)))


def test_a_live_session_lock_defers_fired_silent(vault):
    """F2: a run longer than the missed window has no ledger row until it ends."""
    _silent_shape(vault)
    (vault / ".scout-logs" / ".scout-session.lock").write_text(f"{os.getpid()}\n")
    assert "slot.fired_silent:morning-briefing" not in _ids(vault)
    assert (vault / ".scout-logs" / ".scout-session.lock").read_text() == f"{os.getpid()}\n"  # read-only


def test_a_dead_session_lock_does_not_defer_fired_silent(vault):
    _silent_shape(vault)
    child = subprocess.Popen(["true"])
    child.wait()
    (vault / ".scout-logs" / ".scout-session.lock").write_text(f"{child.pid}\n")
    assert "slot.fired_silent:morning-briefing" in _ids(vault)
    assert (vault / ".scout-logs" / ".scout-session.lock").exists()


MANUAL_ONLY = """\
schema_version: 1
slots:
  ad-hoc:
    type: manual
    runner: run-scout.sh
    fires_at_local: "00:00"
    weekdays: [Mon]
    missed_window_hours: 1
    on_miss: skip
    cooldown_minutes: 0
"""


@pytest.mark.parametrize("body", [MANUAL_ONLY, "schema_version: 1\nslots: {}\n"])
def test_no_judged_slots_means_no_scheduler_verdict(vault, body):
    """F4: a manual-only (or empty) schedule has nothing the tick must fire."""
    (vault / ".scout-state" / "schedule.yaml").write_text(body)
    old = (NOW - timedelta(days=60)).timestamp()
    os.utime(vault / ".scout-state" / "schedule.yaml", (old, old))
    _no_ticks(vault)
    assert "scheduler.stalled" not in _ids(vault)
    _tick(vault, NOW - _h(5))
    assert "scheduler.stalled" not in _ids(vault)


def test_unloadable_schedule_means_no_scheduler_verdict(vault):
    (vault / ".scout-state" / "schedule.yaml").write_text("schema_version: 1\nslots: [1]\n")
    _no_ticks(vault)
    assert "scheduler.stalled" not in _ids(vault)


def test_never_ticked_without_a_tick_job_on_this_host_is_not_stalled(vault, monkeypatch):
    """F4: a second Mac syncing the vault, or a --no-jobs host."""
    _healthy(vault)
    _no_ticks(vault)
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: False)
    assert "scheduler.stalled" not in _ids(vault)


def test_never_ticked_with_a_recently_edited_schedule_is_not_stalled(vault):
    _healthy(vault)
    _no_ticks(vault)
    recent = (NOW - _h(1)).timestamp()
    os.utime(vault / ".scout-state" / "schedule.yaml", (recent, recent))
    assert "scheduler.stalled" not in _ids(vault)


def test_a_stopped_tick_is_red_even_without_a_tick_job(vault, monkeypatch):
    """F4: the has-ticked-before branch is #199 itself, never gated on the job check."""
    _healthy(vault)
    _no_ticks(vault)
    _tick(vault, NOW - _h(3))
    monkeypatch.setattr(health, "tick_job_installed", lambda home=None: False)
    assert _ids(vault) == {"scheduler.stalled": "red"}


THREE_SLOTS = (
    SCHEDULE
    + """\
  evening-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "18:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 2
    on_miss: skip
    cooldown_minutes: 0
"""
)


def test_stale_plus_never_fired_slots_are_red(vault):
    """M1: a never-fired slot is not running either."""
    (vault / ".scout-state" / "schedule.yaml").write_text(THREE_SLOTS)
    old = (NOW - timedelta(days=60)).timestamp()
    os.utime(vault / ".scout-state" / "schedule.yaml", (old, old))
    _fires(vault, morning_briefing=NOW - timedelta(days=3), weekly_research=NOW - timedelta(days=30))
    ids = _ids(vault)
    assert ids["slot.never_fired:evening-consolidation"] == "yellow"
    assert ids["slot.stale:morning-briefing"] == "red"
    assert ids["slot.stale:weekly-research"] == "red"


def test_stale_plus_a_running_slot_stays_yellow(vault):
    (vault / ".scout-state" / "schedule.yaml").write_text(THREE_SLOTS)
    old = (NOW - timedelta(days=60)).timestamp()
    os.utime(vault / ".scout-state" / "schedule.yaml", (old, old))
    _fires(
        vault,
        morning_briefing=NOW - timedelta(days=3),
        weekly_research=NOW - timedelta(days=30),
        evening_consolidation=NOW - _h(18),
    )
    _ledger(vault, ("evening-consolidation", NOW - _h(18)))
    ids = _ids(vault)
    assert ids["slot.stale:morning-briefing"] == "yellow"


def test_a_zero_started_at_row_does_not_open_the_fired_silent_window(vault):
    """M3: run-outcome.sh records started_at 0 for a bad start; it is not the ledger's first row."""
    _row(vault, "weekly-research", NOW, NOW - _h(2), raw_start=0)
    _row(vault, "weekly-research", NOW - _h(1), NOW - _h(1) + timedelta(minutes=5))
    _fires(vault, morning_briefing=NOW - _h(5), weekly_research=NOW - _h(1))
    assert "slot.fired_silent:morning-briefing" not in _ids(vault)


def _git_repo(path: Path, stamp: int) -> None:
    env = {
        **os.environ,
        "GIT_COMMITTER_DATE": f"@{stamp} +0000",
        "GIT_AUTHOR_DATE": f"@{stamp} +0000",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    path.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["git", "init", "-q"],
        ["git", "-c", "user.name=A", "-c", "user.email=a@example.com", "commit", "-q", "--allow-empty", "-m", "x"],
    ):
        subprocess.run(cmd, cwd=path, env=env, check=True)


def test_vault_birth_ignores_an_inherited_git_dir(tmp_path, monkeypatch):
    """M2: a GIT_DIR / GIT_WORK_TREE in the caller's env must not point git at another repo."""
    other = tmp_path / "other"
    _git_repo(other, 1700000000)
    vault = tmp_path / "Scout"
    vault.mkdir()
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(vault))
    assert REAL_BIRTH(vault) is None


def test_vault_birth_matches_a_differently_spelled_vault_path(tmp_path):
    """M2: macOS paths are case-insensitive; a symlinked path is the same vault too."""
    repo = tmp_path / "repo"
    _git_repo(repo, 1700000000)
    link = tmp_path / "link"
    link.symlink_to(repo)
    assert REAL_BIRTH(link) == datetime.fromtimestamp(1700000000, UTC)
    upper = tmp_path / "REPO"
    if not upper.exists():
        pytest.skip("case-sensitive filesystem")
    assert REAL_BIRTH(upper) == datetime.fromtimestamp(1700000000, UTC)


REAL_TICK_JOB = health.tick_job_installed if hasattr(health, "tick_job_installed") else None


def test_tick_job_installed_on_macos_is_the_launch_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(health.platform, "system", lambda: "Darwin")
    assert REAL_TICK_JOB(tmp_path) is False
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.scout.schedule-tick.plist").write_text("<plist/>")
    assert REAL_TICK_JOB(tmp_path) is True
    monkeypatch.setattr(health.Path, "home", classmethod(lambda cls: tmp_path))
    assert REAL_TICK_JOB() is True


_CRON = """\
MAILTO=""
# >>> scout-managed >>>
*/5 * * * * /home/alex/.venv/bin/scoutctl schedule tick
# <<< scout-managed <<<
"""


@pytest.mark.parametrize(
    ("stdout", "code", "expected"),
    [
        (_CRON, 0, True),
        (_CRON.replace("schedule tick", "heartbeat"), 0, False),
        ("*/5 * * * * scoutctl schedule tick\n# >>> scout-managed >>>\n# <<< scout-managed <<<\n", 0, False),
        (_CRON, 1, False),
    ],
)
def test_tick_job_installed_on_linux_reads_the_managed_cron_block(tmp_path, monkeypatch, stdout, code, expected):
    monkeypatch.setattr(health.platform, "system", lambda: "Linux")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["timeout"] = cmd, kw.get("timeout")
        return subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr="")

    monkeypatch.setattr(health.subprocess, "run", fake_run)
    assert REAL_TICK_JOB(tmp_path) is expected
    assert seen == {"cmd": ["crontab", "-l"], "timeout": 5}


@pytest.mark.parametrize("exc", [FileNotFoundError("crontab"), subprocess.TimeoutExpired("crontab", 5)])
def test_tick_job_installed_on_linux_survives_crontab_errors(tmp_path, monkeypatch, exc):
    monkeypatch.setattr(health.platform, "system", lambda: "Linux")

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(health.subprocess, "run", boom)
    assert REAL_TICK_JOB(tmp_path) is False


def test_tick_job_installed_elsewhere_is_false(tmp_path, monkeypatch):
    monkeypatch.setattr(health.platform, "system", lambda: "Windows")
    assert REAL_TICK_JOB(tmp_path) is False
