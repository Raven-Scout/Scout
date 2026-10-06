"""templates/scripts/session-lane-liveness.py — is each session type still producing work?

``scripts/heartbeat.sh`` runs this once a day with ``--quiet --notify``. Connector
health watches tool calls inside a run and the run-outcome ledger watches one
run's exit, but neither notices a whole session type (a *lane*: every slot of one
type) going quiet for weeks. A lane is judged by committed output, not by
``last-fire.json``, which also advances when a run dies at the door.

The lanes are the slot types the vault's schedule declares. A commit is a lane's
output when its subject starts with the lane's name (the convention the dreaming
and research phases commit with), or when it lands inside a run of that lane
recorded in ``.scout-logs/run-outcomes.jsonl``. The script ships to vaults
verbatim, so it is driven here as a CLI over a temporary vault.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "templates" / "scripts" / "session-lane-liveness.py"
NOW = datetime.now(UTC)

SCHEDULE = """\
schema_version: 1

slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]

  evening-consolidation:   # weekday evenings
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "19:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]

  dreaming-nightly:
    type: dreaming
    runner: run-dreaming.sh
    fires_at_local: "22:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]

  ad-hoc:
    type: manual
    runner: run-scout.sh
    fires_at_local: "00:00"
    weekdays: [Mon]
"""


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("session_lane_liveness", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(vault: Path, *args: str, when: datetime | None = None) -> None:
    stamp = (when or NOW).isoformat()
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Scout Bot",
        "GIT_AUTHOR_EMAIL": "alex@example.com",
        "GIT_COMMITTER_NAME": "Scout Bot",
        "GIT_COMMITTER_EMAIL": "alex@example.com",
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    }
    subprocess.run(["git", "-C", str(vault), *args], env=env, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _fresh_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-anchor NOW when each test starts. The script measures ages from its own
    clock and rounds them to 2 decimals, so a NOW frozen at collection drifts the
    ages as the suite runs: ~3 minutes in, 0.4931 days rounds to 0.50 (#308)."""
    monkeypatch.setattr(sys.modules[__name__], "NOW", datetime.now(UTC))


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-state").mkdir(parents=True)
    (v / ".scout-logs").mkdir()
    (v / ".scout-state" / "schedule.yaml").write_text(SCHEDULE, encoding="utf-8")
    _git(v, "init", "-q")
    return v


def _commit(vault: Path, subject: str, *, days_ago: float) -> None:
    _git(vault, "commit", "-q", "--allow-empty", "-m", subject, when=NOW - timedelta(days=days_ago))


def _ledger(
    vault: Path, slot: str, *, days_ago: float, minutes: int = 30, exit_code: int = 0, log: str | None = None
) -> None:
    started = NOW - timedelta(days=days_ago)
    row = {
        "slot": slot,
        "mode": slot,
        "started_at": int(started.timestamp()),
        "finished_at": int((started + timedelta(minutes=minutes)).timestamp()),
        "exit_code": exit_code,
        "log": log,
    }
    with (vault / ".scout-logs" / "run-outcomes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _fired(vault: Path, **slots: float) -> None:
    index = {slot: (NOW - timedelta(days=d)).isoformat().replace("+00:00", "Z") for slot, d in slots.items()}
    (vault / ".scout-state" / "last-fire.json").write_text(json.dumps({"last_fire": index}), encoding="utf-8")


def _run(vault: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env={**os.environ, "SCOUT_DATA_DIR": str(vault), **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def _lanes(vault: Path) -> dict[str, dict]:
    result = _run(vault, "--json")
    return {lane["lane"]: lane for lane in json.loads(result.stdout)["lanes"]}


def _status(vault: Path, lane: str) -> str:
    return _lanes(vault)[lane]["status"]


def _healthy(vault: Path) -> None:
    """Every scheduled lane committed yesterday."""
    for subject in ("briefing [08:00]: done", "consolidation [19:00]: done", "dreaming [22:00]: done"):
        _commit(vault, subject, days_ago=1)


# ---------- which lanes ----------


def test_the_lanes_are_the_slot_types_the_schedule_declares(vault: Path) -> None:
    _healthy(vault)
    _commit(vault, "chores [09:00]: tidied", days_ago=1)  # not a scheduled lane here

    lanes = _lanes(vault)

    assert sorted(lanes) == ["briefing", "consolidation", "dreaming"]
    assert lanes["consolidation"]["slots"] == ["evening-consolidation"]


def test_a_vault_without_a_schedule_has_no_lanes_and_is_healthy(vault: Path) -> None:
    (vault / ".scout-state" / "schedule.yaml").unlink()

    result = _run(vault, "--json")

    assert result.returncode == 0
    assert json.loads(result.stdout)["lanes"] == []


def test_a_block_style_schedule_is_read_too(vault: Path) -> None:
    (vault / ".scout-state" / "schedule.yaml").write_text(
        "slots:\n"
        "    research:\n"
        "        type: 'research'\n"
        "        runner: run-research.sh\n"
        "        weekdays:\n"
        "        - Mon\n"
        "        - Thu\n",
        encoding="utf-8",
    )
    _commit(vault, "research [14:00]: findings", days_ago=1)

    (lane,) = _lanes(vault).values()

    assert (lane["lane"], lane["slots"], lane["status"]) == ("research", ["research"], "🟢 OK")


# ---------- what counts as a lane's output ----------


def test_a_commit_named_for_the_lane_is_its_output(vault: Path) -> None:
    _healthy(vault)

    assert {lane: info["status"] for lane, info in _lanes(vault).items()} == {
        "briefing": "🟢 OK",
        "consolidation": "🟢 OK",
        "dreaming": "🟢 OK",
    }


def test_a_commit_inside_a_recorded_run_is_that_lanes_output(vault: Path) -> None:
    """Briefing and consolidation runs have no subject convention; the run ledger
    says which lane was running when a commit landed."""
    _healthy(vault)
    _ledger(vault, "evening-consolidation", days_ago=0.5)
    _commit(vault, "Update action items", days_ago=0.5 - 10 / 1440)  # 10 minutes into the run

    lane = _lanes(vault)["consolidation"]

    assert lane["last_commit_subject"] == "Update action items"
    assert lane["last_commit_age_days"] == pytest.approx(0.49, abs=0.01)


def test_a_manual_run_of_a_lane_counts_for_that_lane(vault: Path) -> None:
    _ledger(vault, "dreaming-manual", days_ago=0.5)
    _commit(vault, "tidy the KB", days_ago=0.5 - 5 / 1440)

    assert _lanes(vault)["dreaming"]["last_commit_subject"] == "tidy the KB"


def test_a_commit_named_for_one_lane_is_not_another_lanes_output(vault: Path) -> None:
    """Runs can overlap a commit another lane made; the subject's lane wins."""
    _ledger(vault, "evening-consolidation", days_ago=0.5)
    _commit(vault, "dreaming [22:00]: tidy the KB", days_ago=0.5 - 10 / 1440)

    lanes = _lanes(vault)

    assert lanes["dreaming"]["last_commit_subject"] == "dreaming [22:00]: tidy the KB"
    assert lanes["consolidation"]["last_commit"] is None


def test_a_commit_outside_every_run_is_nobodys_output(vault: Path) -> None:
    _ledger(vault, "evening-consolidation", days_ago=10)
    _commit(vault, "Update action items", days_ago=1)

    assert _lanes(vault)["consolidation"]["last_commit"] is None


# ---------- verdicts ----------


def test_a_lane_past_its_warn_budget_is_degraded_but_not_alarming(vault: Path) -> None:
    _commit(vault, "briefing [08:00]: done", days_ago=1)
    _commit(vault, "consolidation [19:00]: done", days_ago=1)
    _commit(vault, "dreaming [22:00]: done", days_ago=5)  # warn 3d, dark 7d

    result = _run(vault, "--json")

    assert result.returncode == 0
    assert _status(vault, "dreaming") == "🟡 DEGRADED"


def test_a_lane_with_no_recent_output_and_no_recent_fire_is_dark(vault: Path) -> None:
    _commit(vault, "briefing [08:00]: done", days_ago=1)
    _commit(vault, "consolidation [19:00]: done", days_ago=1)
    _commit(vault, "dreaming [22:00]: done", days_ago=30)
    _fired(vault, **{"dreaming-nightly": 9})

    result = _run(vault, "--json")

    assert result.returncode == 2
    assert _status(vault, "dreaming") == "🔴 DARK"


def test_a_lane_that_fires_but_commits_nothing_is_firing_but_silent(vault: Path) -> None:
    _commit(vault, "briefing [08:00]: done", days_ago=1)
    _commit(vault, "consolidation [19:00]: done", days_ago=1)
    _commit(vault, "dreaming [22:00]: done", days_ago=30)
    _fired(vault, **{"dreaming-nightly": 0.4})

    result = _run(vault, "--json")

    assert result.returncode == 3
    assert _status(vault, "dreaming") == "🔴 FIRING-BUT-SILENT"


def test_a_lane_with_too_little_history_is_not_judged(vault: Path) -> None:
    """Right after an upgrade a lane with no subject convention has no recorded
    runs yet; calling it dark would alarm on day one."""
    _commit(vault, "briefing [08:00]: done", days_ago=1)
    _commit(vault, "dreaming [22:00]: done", days_ago=1)
    _ledger(vault, "evening-consolidation", days_ago=1, exit_code=1)
    _fired(vault, **{"evening-consolidation": 1})

    result = _run(vault, "--json")

    assert result.returncode == 0
    assert _status(vault, "consolidation") == "⚪ NO DATA YET"


def test_a_lane_that_ran_for_its_whole_dark_budget_without_output_is_judged(vault: Path) -> None:
    _commit(vault, "briefing [08:00]: done", days_ago=1)
    _commit(vault, "dreaming [22:00]: done", days_ago=1)
    for day in (9, 6, 3, 1):
        _ledger(vault, "evening-consolidation", days_ago=day, exit_code=1)
    _fired(vault, **{"evening-consolidation": 1})

    result = _run(vault, "--json")

    assert result.returncode == 3
    assert _status(vault, "consolidation") == "🔴 FIRING-BUT-SILENT"


# ---------- budgets ----------


@pytest.mark.parametrize(
    ("weekdays", "budgets"),
    [
        pytest.param(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], (3, 7), id="daily-keeps-the-base"),
        pytest.param(["Mon", "Tue", "Wed", "Thu", "Fri"], (3, 7), id="weekdays-gap-fits-the-base"),
        pytest.param(["Mon"], (7, 8), id="weekly-widens-to-the-gap"),
        pytest.param(["Mon", "Thu"], (4, 7), id="twice-weekly"),
        pytest.param([], (3, 7), id="no-weekdays-means-daily"),
    ],
)
def test_budgets_widen_to_fit_a_sparse_schedule(weekdays: list[str], budgets: tuple[int, int]) -> None:
    """A weekly lane cannot be expected to commit every three days."""
    assert _module().lane_budgets("research", weekdays) == budgets


def test_daily_lanes_keep_their_tighter_budgets() -> None:
    assert _module().lane_budgets("briefing", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) == (2, 4)


def test_an_unknown_lane_type_gets_the_default_budget() -> None:
    assert _module().lane_budgets("custom", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) == (3, 7)


# ---------- reporting ----------


def _dark_dreaming(vault: Path) -> None:
    _commit(vault, "briefing [08:00]: done", days_ago=1)
    _commit(vault, "consolidation [19:00]: done", days_ago=1)
    _commit(vault, "dreaming [22:00]: done", days_ago=30)
    _fired(vault, **{"dreaming-nightly": 0.4})


def test_quiet_prints_nothing_when_every_lane_is_healthy(vault: Path) -> None:
    _healthy(vault)

    result = _run(vault, "--quiet")

    assert (result.returncode, result.stdout) == (0, "")


def test_quiet_still_reports_a_dark_lane_with_its_newest_log(vault: Path) -> None:
    _dark_dreaming(vault)
    logs = vault / ".scout-logs"
    (logs / "dreaming-2026-01-04_22-00.log").write_text("(exit code: 0, duration: 900s)\n", encoding="utf-8")
    (logs / "dreaming-2026-01-05_22-00.log").write_text("Failed to authenticate\n", encoding="utf-8")
    (logs / "dreaming-notes.log").write_text("not a runner log\n", encoding="utf-8")

    out = _run(vault, "--quiet").stdout

    assert "## Lanes needing attention" in out
    assert "**dreaming**" in out
    assert "dreaming-2026-01-05_22-00.log: AUTH FAILURE" in out


@pytest.mark.parametrize(
    ("log_text", "verdict"),
    [
        ("=== Budget check: skipping this run ===\n", "budget-skip"),
        ("=== Another Scout session running (PID 42) — skipping ===\n", "lock-skip"),
        ("=== Scout run finished (exit code: 1, duration: 3s) ===\n", "exit 1"),
        ("starting...\n", "no completion line"),
    ],
)
def test_the_newest_log_explains_why_a_lane_is_silent(vault: Path, log_text: str, verdict: str) -> None:
    _dark_dreaming(vault)
    (vault / ".scout-logs" / "dreaming-2026-01-05_22-00.log").write_text(log_text, encoding="utf-8")

    assert _lanes(vault)["dreaming"]["newest_log"] == f"dreaming-2026-01-05_22-00.log: {verdict}"


def test_a_shared_runner_log_is_only_blamed_on_the_lane_that_ran_it(vault: Path) -> None:
    """Briefing and consolidation both run run-scout.sh, whose log names carry no
    slot; the newest scout log may be a healthy briefing. The ledger says which
    logs were consolidation's."""
    _commit(vault, "briefing [08:00]: done", days_ago=0.2)
    _commit(vault, "dreaming [22:00]: done", days_ago=1)
    logs = vault / ".scout-logs"
    for days_ago, name in ((9, "scout-2026-01-01_19-00.log"), (5, "scout-2026-01-05_19-00.log")):
        _ledger(vault, "evening-consolidation", days_ago=days_ago, exit_code=1, log=name)
        (logs / name).write_text("Failed to authenticate\n", encoding="utf-8")
    _ledger(vault, "morning-briefing", days_ago=0.2, log="scout-2026-01-09_08-00.log")
    (logs / "scout-2026-01-09_08-00.log").write_text("(exit code: 0, duration: 600s)\n", encoding="utf-8")
    (logs / "scout-2026-01-10_19-00.log").write_text("=== Budget check: skipping this run ===\n", encoding="utf-8")
    _fired(vault, **{"evening-consolidation": 1})

    lane = _lanes(vault)["consolidation"]

    assert lane["status"] == "🔴 FIRING-BUT-SILENT"
    assert lane["newest_log"] == "scout-2026-01-05_19-00.log: AUTH FAILURE"


def test_notify_sends_a_desktop_notification_naming_the_dark_lanes(vault: Path, tmp_path: Path) -> None:
    """Notifications go through osascript where it exists (macOS); elsewhere --notify is a no-op."""
    _dark_dreaming(vault)
    calls = tmp_path / "osascript.calls"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "osascript"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{calls}"\n', encoding="utf-8")
    stub.chmod(0o755)

    _run(vault, "--quiet", "--notify", PATH=f"{bindir}:{os.environ['PATH']}")

    argv = calls.read_text(encoding="utf-8").splitlines()
    assert argv[0] == "-"  # the script comes on stdin; names travel as argv, never as code
    assert "dreaming" in argv[-1]


def test_notify_is_silent_when_every_lane_is_healthy(vault: Path, tmp_path: Path) -> None:
    _healthy(vault)
    calls = tmp_path / "osascript.calls"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "osascript"
    stub.write_text(f'#!/bin/bash\ntouch "{calls}"\n', encoding="utf-8")
    stub.chmod(0o755)

    result = _run(vault, "--notify", PATH=f"{bindir}:{os.environ['PATH']}")

    assert result.returncode == 0, result.stderr
    assert "🟢 OK" in result.stdout
    assert not calls.exists()


def test_without_scout_data_dir_it_checks_the_vault_it_is_installed_in(vault: Path) -> None:
    _healthy(vault)
    installed = vault / "scripts" / "session-lane-liveness.py"
    installed.parent.mkdir()
    shutil.copy(SCRIPT, installed)
    env = {k: v for k, v in os.environ.items() if k != "SCOUT_DATA_DIR"}

    result = subprocess.run(
        [sys.executable, str(installed), "--json"], env=env, capture_output=True, text=True, check=True
    )

    assert sorted(lane["lane"] for lane in json.loads(result.stdout)["lanes"]) == [
        "briefing",
        "consolidation",
        "dreaming",
    ]
