# Run health P1–P3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One run-health evaluator (`scout/health.py`) with its CLI and doctor block (P1), one notifier that replaces the bash streak pushes and the lane script's `--notify` (P2), and the consumers: `/scout-status`, the session wrap and Scout.app (P3).

**Architecture:** `health.evaluate(vault, now=…)` is a pure read over the on-disk ledgers (`run-outcomes.jsonl`, `schedule.yaml`, `last-fire.json`, `schedule-events-*.jsonl`, `git log`). It returns a `RunHealth` of findings with stable ids. Every surface calls it: `scoutctl health [--json]`, `bootstrap doctor` (an additive `run_health` block; install severity and exit code unchanged), `scoutctl health notify` (a de-duplicating push state machine with fan-out channels), and through doctor JSON, the app.

**Tech Stack:** Python 3.12, Typer, PyYAML, pytest (engine at `plugin/engine`); bash templates rendered by `scout.scripts.phase_assembly.render_template`; Swift / SwiftUI with Swift Testing (`apps/macos`, Xcode 26.6).

**Spec:** `docs/superpowers/specs/2026-10-11-run-health-and-headless-updates-design.md` (§3 D1–D5, §4, §8, §9 P1–P3, §11, §12). Read it before your task.

**Coordinator rulings (2026-10-11), binding on this plan:**
- **Q-A:** push channels **fan out**. Every configured channel gets each push, in the order desktop → Telegram → `notify.command`, and each channel's result is recorded. One channel failing never stops the others.
- **Q-B:** the session-wrap consumer is a new `plugin/phases/core/run-health.md` with `mode: [briefing, consolidation, dreaming, research]`.
- **Q-C:** `runs.failing` is suppressed when `auth.expired` covers the same trailing failures. `auth.expired`'s summary carries the failed-run count and the last-success time.
- **Q-D:** `slot.never_fired` measures age from the later of the vault's first commit and the `schedule.yaml` mtime. `slot.fired_silent` is judged only for fires after the ledger's first row. `lock.skips` is in P1. `scoutctl health` is a Typer group that exits 0/1/2.
- **Merge order:** P1, P2 and P3 merge together, back to back, after P3 is approved. Each PR's description says so.

## Global Constraints

- Three PRs, each stacked on the one before: P1 = branch `run-health/p1` from `origin/main`; P2 = `run-health/p2` from `run-health/p1`; P3 = `run-health/p3` from `run-health/p2`. P3 lands as **ONE commit** across `plugin/` and `apps/` (repo-root `CLAUDE.md`, "Cross-cutting changes"); squash its task commits before opening the PR.
- Engine commands run with `plugin/engine` as cwd: `.venv/bin/python -m pytest …`, `.venv/bin/ruff check .` and `.venv/bin/ruff format --check .`. **Never run ruff on absolute paths.**
- CI enforces a coverage floor of 98.00%. Locally the number reads about 1.4 points low without `textual`, so compare missed-line counts with `main`: new code must be fully covered.
- Fixtures are anonymised: people `Alex` / `Priya` / `Sam`, vault `example_suite` or `tmp_path / "Scout"`. Nothing from `~/Scout` goes into the repo. Never write to `~/Scout` or `~/scout-plugin`.
- CHANGELOG bullets go under `## [Unreleased]` in `plugin/CHANGELOG.md`.
- Everything the plugin needs at runtime stays under `plugin/`.
- Contract artifacts are generated, never hand-edited. The P3 fixture has one canonical copy that a test generates, plus a byte copy in `apps/macos/ScoutTests/Fixtures/`, compared by `contract.yml`.
- Measured claims only (§4.5): text about a run's output says "N commits in this run's window (newest `abc1234`)" or "no commits in this run's window", and says nothing when the value is `null`. Never "by this run".
- Pushes are best-effort: `scoutctl health notify` exits 0 even when every channel fails. Doctor's exit code is install-only (D2).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Review Focus

1. **A ledger with junk lines, a non-dict JSON line, missing keys or non-int epochs** (half-written by a killed run). The evaluator skips those rows; it never raises. Pinned in Task 1 (`test_malformed_ledger_rows_are_skipped`).
2. **A `schedule.yaml` that fails to load** (`ConfigError`) or a corrupt `last-fire.json`. Slot findings are skipped, and the other findings still evaluate; doctor still returns. Pinned in Task 2 (`test_unloadable_schedule_skips_slot_findings_only`).
3. **The evaluator itself raising inside doctor.** Doctor must still return the install report, with `run_health: null` and one note saying why. Pinned in Task 4 (`test_doctor_survives_an_evaluator_crash`).
4. **A `notify.command` that hangs, exits non-zero or isn't found.** It is timed out at 30 s, its result is recorded, the other channels still fire, and the exit code stays 0. Pinned in Task 6 (`test_command_channel_*`).
5. **A corrupt or missing `health-notify.json`.** It is treated as empty state, which can mean one extra push but never a crash. Pinned in Task 8 (`test_corrupt_state_is_treated_as_empty`).

---

# PR P1 — health core (branch `run-health/p1`)

### Task 1: `scout/health.py`: model, ledger reader, ledger findings

**Files:**
- Create: `plugin/engine/scout/health.py`
- Create: `plugin/engine/tests/unit/test_health_ledger.py`

**Interfaces:**
- Produces (later tasks rely on these exact names):
  ```python
  # scout/health.py
  SEVERITY_ORDER = {"note": 0, "yellow": 1, "red": 2}
  CLI_AUTH_FAILURE_ERE: str          # moved verbatim from bootstrap_doctor (Task 4 re-exports it there)
  SKIP_OUTCOMES = frozenset({"skipped_lock", "skipped_budget", "skipped_redundant"})

  @dataclass(frozen=True)
  class Finding:
      id: str            # stable, e.g. "auth.expired", "slot.stale:morning-briefing"
      severity: str      # "yellow" | "red" | "note"
      since: datetime | None
      summary: str
      fix: str
      source: str        # "ledger" | "log" | "schedule" | "events" | "git"
      def to_dict(self) -> dict  # since → iso_z(since) or None

  @dataclass(frozen=True)
  class SlotStatus:
      key: str; type: str
      last_fire: datetime | None
      last_outcome: OutcomeRow | None
      def to_dict(self) -> dict  # {"key","type","last_fire","last_outcome": {"outcome","finished_at","failure_class"}|None}

  @dataclass(frozen=True)
  class RunHealth:
      severity: str                      # "green" | "yellow" | "red"
      findings: tuple[Finding, ...]      # sorted: red, yellow, note; then by id
      evaluated_at: datetime
      last_ok_at: datetime | None
      slots: tuple[SlotStatus, ...] = ()
      @property
      def exit_code(self) -> int         # green 0, yellow 1, red 2
      def to_dict(self) -> dict          # {"severity","evaluated_at","last_ok_at","findings":[...],"slots":[...]}

  @dataclass(frozen=True)
  class OutcomeRow:
      slot: str; mode: str
      started_at: datetime; finished_at: datetime
      exit_code: int; failure_class: str; outcome: str   # outcome normalised (see below)
      log: str | None
      commits_in_window: int | None; newest_commit: str | None

  def iso_z(dt: datetime) -> str                                    # "2026-10-11T08:00:00Z", seconds precision, UTC
  def read_outcomes(vault: Path) -> list[OutcomeRow]                # file order; bad rows skipped
  def ledger_findings(rows: list[OutcomeRow], *, vault: Path, now: datetime) -> list[Finding]
  def build_report(findings: list[Finding], *, now: datetime, last_ok_at: datetime | None,
                   slots: tuple[SlotStatus, ...] = ()) -> RunHealth
  def evaluate(vault: Path, *, now: datetime | None = None) -> RunHealth   # Task 2 extends it
  ```
- `outcome` normalisation: use the row's `outcome` when it is one of `ok`, `failed` or a `SKIP_OUTCOMES` member. Otherwise `"ok"` if `exit_code == 0`, else `"failed"`.

**Rules (spec §4.2, ruling Q-C):**
- Non-skip rows = rows whose outcome is not in `SKIP_OUTCOMES`. `last_ok_at` = `finished_at` of the newest row with outcome `ok`.
- `auth.expired` (red, source `ledger`): the newest non-skip row has `failure_class == "oauth_expired"`. `since` = `last_ok_at` (None if never). Summary: `f"Scout can't run: your Claude login expired. {n} failed run(s) since the last success ({when})."`, where `n` = trailing consecutive failed non-skip rows and `when` = `iso_z(last_ok_at)` or `"never"`. Fix: `"Open Terminal and run: claude auth login (or claude setup-token for unattended hosts)"`.
- **Fallback** (source `log`), only when `read_outcomes` returns no rows at all: today's doctor newest-log scan, moved here. It globs `.scout-logs/` for `scout-*.log`, `dreaming-*.log` and `research-*.log`, takes the newest by mtime, reads its last 64 KiB, and searches it with `_AUTH_FAILURE_RE` (the wrapper banner or `CLI_AUTH_FAILURE_ERE`, with `re.MULTILINE`). Then `since=None`, and the summary names the log file.
- `runs.failing`: trailing consecutive failed non-skip rows `n ≥ 1` → yellow when `n == 1`, red when `n ≥ 2`. `since` = `last_ok_at`. Summary: `f"The last {n} scheduled run(s) failed ({newest failure_class})."`. Fix: `"Run scoutctl bootstrap doctor and read the newest log: .scout-logs/<log>"`. **Not emitted when `auth.expired` is emitted** (Q-C).
- `lock.skips` (yellow, source `ledger`): ≥3 rows with outcome `skipped_lock` whose `finished_at` falls within the last 24 h. Summary: `f"{k} runs skipped in the last 24 h because another session held the vault lock."`. Fix: `"See #218; a long-running session is blocking scheduled ones."`.
- Severity: the worst finding (`note` never raises it). With no yellow or red findings, severity is `green`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_health_ledger.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


def _row(at: datetime, *, code: int = 0, klass: str = "ok", slot: str = "morning-briefing", **extra) -> dict:
    started = int((at - timedelta(minutes=5)).timestamp())
    return {"slot": slot, "mode": "scout", "started_at": started, "finished_at": int(at.timestamp()),
            "duration_s": 300, "exit_code": code, "failure_class": klass, "log": f"scout-{started}.log", **extra}


def _ledger(vault: Path, rows: list[dict | str]) -> None:
    lines = [r if isinstance(r, str) else json.dumps(r) for r in rows]
    (vault / ".scout-logs" / "run-outcomes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ids(report: health.RunHealth) -> dict[str, str]:
    return {f.id: f.severity for f in report.findings}


def test_no_ledger_and_no_logs_is_green(tmp_path):
    report = health.evaluate(_vault(tmp_path), now=NOW)
    assert report.severity == "green" and report.findings == () and report.last_ok_at is None


def test_one_failure_is_yellow_two_are_red(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=5)), _row(NOW - timedelta(hours=2), code=1, klass="network")])
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "yellow"}
    _ledger(v, [_row(NOW - timedelta(hours=5)), _row(NOW - timedelta(hours=3), code=1, klass="network"),
                _row(NOW - timedelta(hours=2), code=1, klass="unknown")])
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"runs.failing": "red"}
    assert report.findings[0].since == NOW - timedelta(hours=5)
    assert "last 2 scheduled run(s) failed (unknown)" in report.findings[0].summary


def test_a_success_ends_the_streak(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=3), code=1), _row(NOW - timedelta(hours=1))])
    assert health.evaluate(v, now=NOW).severity == "green"


def test_skips_neither_break_nor_extend_the_streak(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=4), code=1), _row(NOW - timedelta(hours=3), outcome="skipped_lock"),
                _row(NOW - timedelta(hours=2), code=1)])
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "red"}


def test_the_233_shape_is_one_auth_finding_with_the_count(tmp_path):
    """72 oauth_expired rows: one red finding (Q-C), carrying the count and last success."""
    v = _vault(tmp_path)
    ok_at = NOW - timedelta(days=21)
    rows = [_row(ok_at)] + [_row(ok_at + timedelta(hours=7 * (i + 1)), code=1, klass="oauth_expired") for i in range(72)]
    _ledger(v, rows)
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"auth.expired": "red"}
    f = report.findings[0]
    assert f.since == ok_at and "72 failed run(s)" in f.summary and health.iso_z(ok_at) in f.summary


def test_auth_is_judged_on_the_newest_non_skip_row(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=3), code=1, klass="oauth_expired"),
                _row(NOW - timedelta(hours=2), code=1, klass="network")])
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "red"}


def test_log_fallback_only_without_a_ledger(tmp_path):
    v = _vault(tmp_path)
    (v / ".scout-logs" / "scout-2026-10-11_08-00.log").write_text(
        "...\nFailed to authenticate. API Error: 401\n", encoding="utf-8")
    report = health.evaluate(v, now=NOW)
    assert _ids(report) == {"auth.expired": "red"} and report.findings[0].source == "log"
    _ledger(v, [_row(NOW - timedelta(hours=1))])
    assert health.evaluate(v, now=NOW).severity == "green"


@pytest.mark.parametrize("marker", [
    "=== Authentication failure (HTTP 401/403)",
    "Failed to authenticate. API Error: 403",
    "Invalid authentication credentials",
    "Failed to authenticate: OAuth session expired and could not be refreshed",
])
def test_log_fallback_markers(tmp_path, marker):
    v = _vault(tmp_path)
    (v / ".scout-logs" / "dreaming-2026-10-11_22-00.log").write_text(f"x\n{marker}\n", encoding="utf-8")
    assert "auth.expired" in _ids(health.evaluate(v, now=NOW))


def test_a_session_quoting_an_error_mid_line_is_not_an_auth_failure(tmp_path):
    v = _vault(tmp_path)
    (v / ".scout-logs" / "scout-2026-10-11_08-00.log").write_text(
        "I saw 'Failed to authenticate' in a ticket\n", encoding="utf-8")
    assert health.evaluate(v, now=NOW).severity == "green"


def test_three_lock_skips_in_a_day_are_yellow(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=h), outcome="skipped_lock") for h in (20, 10, 1)])
    assert _ids(health.evaluate(v, now=NOW)) == {"lock.skips": "yellow"}


def test_two_lock_skips_are_fine(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=h), outcome="skipped_lock") for h in (30, 10, 1)])
    assert health.evaluate(v, now=NOW).severity == "green"


def test_malformed_ledger_rows_are_skipped(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, ["not json", "[1, 2]", json.dumps({"slot": "x"}), json.dumps({**_row(NOW), "started_at": "soon"}),
                _row(NOW - timedelta(hours=2), code=1), _row(NOW - timedelta(hours=1), code=1)])
    rows = health.read_outcomes(v)
    assert len(rows) == 2
    assert _ids(health.evaluate(v, now=NOW)) == {"runs.failing": "red"}


def test_outcome_field_is_normalised_from_exit_code_when_absent_or_unknown(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW, code=0), _row(NOW, code=3), _row(NOW, code=0, outcome="bogus")])
    assert [r.outcome for r in health.read_outcomes(v)] == ["ok", "failed", "ok"]


def test_report_to_dict_shape(tmp_path):
    v = _vault(tmp_path)
    _ledger(v, [_row(NOW - timedelta(hours=1), code=1, klass="network")])
    d = health.evaluate(v, now=NOW).to_dict()
    assert d["severity"] == "yellow" and d["evaluated_at"] == "2026-10-11T12:00:00Z" and d["last_ok_at"] is None
    assert d["findings"] == [{"id": "runs.failing", "severity": "yellow", "since": None,
                              "summary": "The last 1 scheduled run(s) failed (network).",
                              "fix": d["findings"][0]["fix"], "source": "ledger"}]
    assert set(d) == {"severity", "evaluated_at", "last_ok_at", "findings", "slots"}


def test_notes_never_raise_severity():
    note = health.Finding(id="update.behind", severity="note", since=None, summary="s", fix="f", source="x")
    assert health.build_report([note], now=NOW, last_ok_at=None).severity == "green"
    assert health.build_report([note], now=NOW, last_ok_at=None).exit_code == 0
```

  `test_two_lock_skips_are_fine` pins the 24 h boundary: the 30 h row falls outside it.

- [ ] **Step 2: Run, expect failure** — `.venv/bin/python -m pytest tests/unit/test_health_ledger.py -q` → `ModuleNotFoundError: scout.health`.
- [ ] **Step 3: Implement `scout/health.py`** following the Interfaces and Rules above. Module docstring: what the module is (D1, "one evaluator") and that it is read-only. Use `datetime.fromtimestamp(int(x), UTC)`. A row needs `started_at`, `finished_at` (int-coercible) and `exit_code` (int-coercible), plus a `slot` or a `mode`; otherwise skip it. `slot` falls back to `mode`. `evaluate()` in this task is `rows = read_outcomes(vault); return build_report(ledger_findings(rows, vault=vault, now=now), now=now, last_ok_at=...)`, with `now` defaulting to `datetime.now(UTC)`.
- [ ] **Step 4: Run, expect pass**, then `.venv/bin/ruff check scout/health.py tests/unit/test_health_ledger.py && .venv/bin/ruff format scout/health.py tests/unit/test_health_ledger.py` (cwd `plugin/engine`).
- [ ] **Step 5: Commit** `feat(health): run-health evaluator core — ledger findings (#233)`.

### Task 2: schedule, fire and scheduler findings + per-slot status

**Files:**
- Modify: `plugin/engine/scout/health.py`
- Create: `plugin/engine/tests/unit/test_health_schedule.py`

**Interfaces:**
- Consumes: Task 1's `Finding`, `OutcomeRow`, `SlotStatus`, `read_outcomes`, `build_report`, `iso_z`.
- Consumes from the engine: `scout.schedule.load_schedule(path) -> Schedule` (raises `scout.errors.ConfigError`), `Slot.key/.type/.weekdays/.missed_window_hours/.runtime`, `SlotType.MANUAL`, `SlotRuntime.LOCAL`.
- Produces:
  ```python
  def longest_gap_hours(weekdays: tuple[str, ...]) -> int     # 24 × longest gap in days between scheduled weekdays (daily → 24, single day → 168)
  def read_last_fires(vault: Path) -> dict[str, datetime]     # .scout-state/last-fire.json {"last_fire": {key: iso}}; {} on any error
  def last_tick_completed(vault: Path) -> datetime | None     # newest "schedule.tick.completed" ts in the two newest .scout-logs/schedule-events-*.jsonl
  def vault_birth(vault: Path) -> datetime | None             # root-commit time (git log --max-parents=0 --format=%ct HEAD, timeout 10 s); else scout-config.yaml mtime; else None
  def schedule_findings(vault: Path, rows: list[OutcomeRow], *, now: datetime) -> tuple[list[Finding], tuple[SlotStatus, ...]]
  ```
  `evaluate()` now concatenates `ledger_findings` and `schedule_findings`, and passes `slots` to `build_report`.

**Rules (spec §4.2, ruling Q-D):** judge only slots whose `type` is not `manual` and whose `runtime` is `local`. With `gap = longest_gap_hours(slot.weekdays)` and `mw = slot.missed_window_hours`:
- `slot.never_fired:<key>` (yellow, source `schedule`): there is no last-fire entry, and `now - ref > gap + mw` hours, where `ref = max(vault_birth, schedule.yaml mtime)`. When `vault_birth` is `None`, the finding is not judged.
- `slot.stale:<key>` (yellow, source `schedule`): last fire is older than `2 × gap + mw` hours. `since` = the last fire. **Every** judged slot is stale (and there is at least one) → each stale finding is red.
- `slot.fired_silent:<key>` (yellow, source `ledger`): the slot fired at least `mw` hours ago, and there is no ledger row with `slot == key` and `started_at ≥ fire − 120 s`. Judged only when the ledger has rows and the fire is after the first row's `started_at` (Q-D). `since` = the fire.
- `scheduler.stalled` (red, source `events`): the newest tick-completed event is more than 2 h old. When no tick event was ever recorded, it fires only if `vault_birth` is known and more than 2 h ago, with the summary "The scheduler has never recorded a tick." `since` = the last tick or `None`.
- A `ConfigError` or `OSError` loading `schedule.yaml` → no slot findings and no slot statuses. `scheduler.stalled` is still judged.
- `SlotStatus` per judged slot: `last_fire` from last-fire, and `last_outcome` = the newest ledger row with `slot == key`.
- Summaries: never_fired `f"{key} has never fired."`; stale `f"{key} last fired {age} ago."` (age as `"3 d"` / `"5 h"`); fired_silent `f"{key} fired at {iso} but no run outcome was recorded."`; stalled `f"The scheduler has not completed a tick since {iso}."`. Fixes: stale/never_fired `"Check that the Mac was awake and the schedule-tick job is loaded: scoutctl bootstrap doctor"`; fired_silent `"The run may have been skipped (lock or budget) or killed; see .scout-logs/failures.log"`; stalled `"Reinstall the scheduler job: scoutctl schedule install-plist --force (macOS) or scoutctl schedule install-cron (Linux)"`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_health_schedule.py`. Build the fixture vault with this helper (it monkeypatches `health.vault_birth` so no git repo is needed):

```python
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout import health

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)   # a Sunday
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
    _tick(v, NOW - timedelta(minutes=5))
    return v


def _fires(v: Path, **fires: datetime) -> None:
    (v / ".scout-state" / "last-fire.json").write_text(json.dumps(
        {"schema_version": 1, "last_fire": {k.replace("_", "-"): health.iso_z(t) for k, t in fires.items()}}))


def _tick(v: Path, at: datetime) -> None:
    path = v / ".scout-logs" / f"schedule-events-{at.date().isoformat()}.jsonl"
    with path.open("a") as fh:
        fh.write(json.dumps({"id": "x", "ts": at.isoformat().replace("+00:00", "Z"),
                             "kind": "schedule.tick.completed", "source": "t", "payload": {}}) + "\n")


def _ledger(v: Path, *rows: tuple[str, datetime]) -> None:
    with (v / ".scout-logs" / "run-outcomes.jsonl").open("a") as fh:
        for slot, start in rows:
            fh.write(json.dumps({"slot": slot, "mode": "scout", "started_at": int(start.timestamp()),
                                 "finished_at": int(start.timestamp()) + 60, "exit_code": 0,
                                 "failure_class": "ok", "log": None}) + "\n")


def _ids(v: Path) -> dict[str, str]:
    return {f.id: f.severity for f in health.evaluate(v, now=NOW).findings}
```

  Tests to write. Each one is a separate function, set up with `_fires`, `_ledger` and `_tick`:
  - `test_longest_gap_hours`: `(("Mon","Tue","Wed","Thu","Fri","Sat","Sun"),) → 24`, `("Wed",) → 168`, `("Mon","Tue","Wed","Thu","Fri") → 72`, `("Sat","Sun") → 144`.
  - `test_healthy_vault_is_green`: both slots fired recently (briefing 4 h ago, research 4 d ago), with ledger rows after each fire → `{}`, and `slots` has 2 entries (manual excluded) whose `last_outcome` is set.
  - `test_the_193_shape_a_slot_missing_from_last_fire_is_never_fired`: only briefing in last-fire → `{"slot.never_fired:weekly-research": "yellow"}`.
  - `test_a_young_vault_does_not_report_never_fired`: set `vault_birth` to `NOW - 100 h` (research needs 168 + 4) → no `never_fired`. Then `NOW - 173 h` → present.
  - `test_a_recently_edited_schedule_resets_never_fired`: `os.utime` schedule.yaml to `NOW - 1 h` → no `never_fired`.
  - `test_unknown_vault_birth_skips_never_fired`: `vault_birth` → `None`.
  - `test_stale_boundary_is_exactly_twice_the_gap_plus_window`: briefing fired at `NOW - 52 h` (2×24+4) → not stale; at `NOW - 52 h - 1 s` → `"slot.stale:morning-briefing": "yellow"` (research fresh).
  - `test_every_slot_stale_is_red`: briefing 3 d ago, research 30 d ago → both stale, both `red`.
  - `test_the_233_shape_fresh_fires_keep_slots_green_while_auth_is_red`: fires fresh, plus ledger rows after the fires with `exit_code: 1, failure_class: oauth_expired` (write them inline) → `{"auth.expired": "red"}` only.
  - `test_fired_silent_after_the_window`: briefing fired `NOW - 5 h` (mw 4); the ledger has an older row (`NOW - 30 h`) but none since the fire → `"slot.fired_silent:morning-briefing": "yellow"`. Fired `NOW - 3 h` → absent.
  - `test_fired_silent_is_not_judged_before_the_ledger_existed`: fire at `NOW - 5 h`, first ledger row at `NOW - 1 h` for another slot → absent.
  - `test_a_row_starting_just_before_the_fire_counts`: row `started_at = fire - 60 s` → absent.
  - `test_scheduler_stalled_after_two_hours`: remove the events, `_tick(v, NOW - timedelta(hours=2, seconds=1))` → `"scheduler.stalled": "red"`; `NOW - 1 h 59 m` → absent.
  - `test_scheduler_never_ticked`: delete all `schedule-events-*` files → red, summary contains "never recorded a tick". With `vault_birth` → `NOW - 1 h` → absent.
  - `test_tick_events_across_utc_midnight`: newest tick in yesterday's file, a newer non-tick event in today's file → uses yesterday's tick time.
  - `test_unloadable_schedule_skips_slot_findings_only`: write `slots: {bad: {type: nope}}` and a red ledger → `runs.failing` present, no `slot.*`, `slots == ()`. Same for a corrupt `last-fire.json` (`"{"`): it reads as no fires, so the result is `never_fired` (not a crash).
  - `test_vault_birth_uses_the_root_commit` (real git): `git init`, then commit with `GIT_COMMITTER_DATE="@1700000000 +0000"`, so `health.vault_birth(v) == datetime.fromtimestamp(1700000000, UTC)`. No `.git` and no config → `None`. Config present → its mtime. (Use a fresh `tmp_path`, not the fixture, since the fixture monkeypatches `vault_birth`; call the real function saved as `REAL_BIRTH = health.vault_birth` at import time.)
- [ ] **Step 2: Run, expect failure.**
- [ ] **Step 3: Implement** per Rules. Reuse the lane script's weekday-gap math (`templates/scripts/session-lane-liveness.py::longest_gap_days`), in hours. `last_tick_completed` sorts `schedule-events-*.jsonl` by name, reads the newest two, and parses `ts` with `datetime.fromisoformat(ts.replace("Z", "+00:00"))`, skipping bad lines.
- [ ] **Step 4: Run** the new file and `tests/unit/test_health_ledger.py`; ruff (cwd `plugin/engine`).
- [ ] **Step 5: Commit** `feat(health): slot, fire and scheduler liveness findings (#199)`.

### Task 3: `scoutctl health [--json]`

**Files:**
- Modify: `plugin/engine/scout/cli.py` (add `_register_health()` next to `_register_notify()`)
- Create: `plugin/engine/tests/unit/test_cli_health.py`
- Modify: `plugin/engine/tests/unit/test_cli_surface.py` if it pins the top-level command list (run it to find out)

**Interfaces:**
- Produces: `health_app = typer.Typer(help="Run health: are scheduled runs happening and succeeding?", invoke_without_command=True)`, registered as `health`. Its `@health_app.callback()` takes `ctx: typer.Context` and `json_out: bool = typer.Option(False, "--json")`. When `ctx.invoked_subcommand is None`, it evaluates `health.evaluate(paths.data_dir())`. P2 adds `@health_app.command("notify")` to this same `health_app`, so keep it a module-level object returned by, or reachable from, `_register_health` (store it as `cli.health_app`).
- Output: `--json` prints `json.dumps(report.to_dict())`. Text prints `run health: <severity>`, then per finding `"<severity>  <id>: <summary>"` and `"    fix: <fix>"`. Both exit `report.exit_code`.

- [ ] **Step 1: Failing tests** with `typer.testing.CliRunner` and `SCOUT_DATA_DIR` pointing at a tmp vault (see how `test_cli_schedule_subapp.py` sets the vault): green vault → exit 0 and `"run health: green"`; two failed ledger rows → exit 2, JSON parses with `severity == "red"`, text contains `"red  runs.failing:"` and `"fix:"`.
- [ ] **Step 2: Run, expect failure** (`No such command 'health'`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests plus `tests/unit/test_cli_surface.py`; ruff.
- [ ] **Step 5: Commit** `feat(cli): scoutctl health [--json]`.

### Task 4: doctor's `run_health` block; the auth check leaves install severity (D2)

**Files:**
- Modify: `plugin/engine/scout/scripts/bootstrap_doctor.py`: delete `_RUN_LOG_GLOBS`, `_AUTH_FAILURE_RE`, `_tail_text` and `_check_recent_auth_failure`, plus their call in `run_doctor`. Keep the name `CLI_AUTH_FAILURE_ERE` importable: `from scout.health import CLI_AUTH_FAILURE_ERE  # noqa: F401  (re-export; test_claude_with_retry pins it)`. Add `run_health: RunHealth | None = None` to `DoctorReport`. At the end of `run_doctor` (vault exists), compute `health.evaluate(vault)` inside `try/except Exception as e` → on failure `run_health=None` plus the note `f"run health could not be evaluated: {e}"`. Pass it to every returned `DoctorReport`. The missing-vault early return keeps `run_health=None`.
- Modify: `plugin/engine/scout/scripts/bootstrap_auto.py::doctor_dict`: add `"run_health": rh.to_dict() if (rh := getattr(report, "run_health", None)) else None`.
- Modify: `plugin/engine/scout/cli.py::cli_bootstrap_doctor` text branch: after the notes, when `report.run_health` is set, print `run health: <sev>` and one `"run health <sev>: <id>: <summary>"` line per finding. **Never** start these lines with `warning:` / `error:` / `note:`: the app's legacy text parser reads those prefixes.
- Move tests: the `_check_recent_auth_failure` tests in `tests/unit/test_bootstrap_doctor_checks.py` (lines ~60–160) and the auth tests in `tests/unit/test_bootstrap_doctor.py` (~170–235) become assertions on `report.run_health`. Delete the ones Task 1 already covers. Keep the `_tail_text` test only if the helper survives (it moves to `health.py` as `_tail_text`; move the test with it).
- Modify: `plugin/CHANGELOG.md` under `## [Unreleased]`.

- [ ] **Step 1: Failing tests** (add to `tests/unit/test_bootstrap_doctor.py`, reusing its existing healthy-vault builder):
  - `test_expired_login_is_green_install_red_run_health`: a healthy vault plus a ledger whose newest row is `oauth_expired` → `report.severity is Severity.GREEN`, `report.exit_code == 0`, `report.run_health.severity == "red"`, and `"auth.expired"` is among the finding ids. This is the D2 contract.
  - `test_doctor_json_carries_run_health`: `doctor_dict(report)["run_health"]["findings"][0]["id"] == "auth.expired"`, and the key set is `{"severity","errors","warnings","notes","run_health"}`.
  - `test_missing_vault_has_null_run_health`.
  - `test_doctor_survives_an_evaluator_crash`: `monkeypatch.setattr(health, "evaluate", boom)` → install severity is unchanged, `run_health is None`, and a note contains `"run health could not be evaluated"`.
  - CLI text test (`tests/unit/test_cli_bootstrap_subapp.py` or wherever `bootstrap doctor` text is tested): the output contains `"run health: red"`, and no line starting `error:` mentions authentication.
- [ ] **Step 2: Run, expect failure.**
- [ ] **Step 3: Implement.** Import `scout.health` lazily inside `run_doctor` (`from scout import health`) so the monkeypatch in the crash test hits it.
- [ ] **Step 4: Run** `tests/unit/test_bootstrap_doctor.py tests/unit/test_bootstrap_doctor_checks.py tests/integration/test_claude_with_retry.py tests/unit/test_bootstrap_auto.py tests/unit/test_cli_bootstrap_subapp.py`, then the full suite with coverage: `.venv/bin/python -m pytest -q --cov=scout --cov-report=term-missing:skip-covered`. Compare the missed lines in `scout/health.py` and `scout/scripts/bootstrap_doctor.py` against `main` (health.py must be 100%). Then ruff check and format `--check` on `.`.
- [ ] **Step 5: CHANGELOG** (under `## [Unreleased]`):
  - `### Added` — "`scoutctl health [--json]`: one run-health verdict (failed runs, an expired Claude login, slots that never fire or went stale, a fired run with no recorded outcome, a stalled scheduler, repeated lock skips) computed from the run ledger, the schedule and the scheduler's events (#233, #199)."
  - `### Changed` — "`bootstrap doctor` reports run health separately: `--json` gains a `run_health` block and the text output a "run health" section. An expired Claude login is now a red run-health finding instead of a red install error, so it no longer fails an engine upgrade's post-upgrade check, and the doctor's exit code reflects install health only (D2)."
- [ ] **Step 6: Commit** `feat(doctor): run_health block; auth leaves install severity (D2)`.

**P1 PR:** before opening, run the whole-branch review (see "Per-PR finish" below). Title: `feat(health): run-health core — evaluator, scoutctl health, doctor block (P1)`. Body: summary; "Closes nothing alone; advances #233 (auth/runs findings) and #199 (slot/scheduler findings)"; the merge-order note ("Merges together with P2 and P3, back to back, after P3 is approved: P1 alone would leave Scout.app's sidebar dot dark for an expired login until P3 decodes `run_health`; #362 means anything on main ships to new installs immediately"); test plan.

---

# PR P2 — notifier (branch `run-health/p2`, from `run-health/p1`)

### Task 5: lane findings ported into `health.py`

**Files:**
- Modify: `plugin/engine/scout/health.py`
- Create: `plugin/engine/tests/unit/test_health_lanes.py`
- Unchanged: `plugin/templates/scripts/session-lane-liveness.py` (it stays as a stdlib-only manual instrument)

**Interfaces:**
- Produces: `def lane_findings(vault: Path, schedule: Schedule | None, rows: list[OutcomeRow], fires: dict[str, datetime], *, now: datetime, commits: list[tuple[datetime, str]] | None = None) -> list[Finding]`, plus `def recent_commits(vault: Path, limit: int = 4000) -> list[tuple[datetime, str]]` (the lane script's `git log -n 4000 --format=%ct%x1f%s`, timeout 60 s, `[]` on error). `evaluate()` calls it with `commits=None`, which means call `recent_commits`. `schedule_findings` must expose the loaded `Schedule` and fires to `evaluate()`. Refactor it to take them as arguments, or add a private `_load_schedule(vault) -> Schedule | None`. Keep `schedule_findings`' public signature from Task 2 working.

**Rules:** port `assess()` from `session-lane-liveness.py` **unchanged in behaviour**:
- `LANE_BUDGETS_DAYS`, `DEFAULT_BUDGET`, `RECENT_FIRE_DAYS = 1.5`, `lane_budgets` widening by the longest gap.
- Lanes = slot types except `manual`.
- Commit attribution: a subject whose first word (minus `:`, lowercased) names a lane → that lane; otherwise every lane with a ledger window `[started, finished]` containing the commit.
- `NO DATA YET` = no output and `observed` (age of the lane's first ledger row) is None or `< dark`.
- Findings: DARK → `lane.dark:<type>` red. FIRING-BUT-SILENT → `lane.silent:<type>` yellow. OK, DEGRADED and NO DATA → no finding. `since` = the last commit or None. Summary `f"{type} has produced no commits in {age}"`, with the newest log outcome appended when known (port `newest_log_outcome`). The fix text keeps the script's SILENT hint.

- [ ] **Step 1: Failing tests.** Port the behaviour cases of `tests/unit/test_session_lane_liveness.py` (read it first) that judge `assess()`: an OK lane, DARK after budget, SILENT with a recent fire, NO DATA on a young ledger, a weekly lane widened by its gap, prefix attribution beating window attribution, `manual` excluded. Drive them through `health.lane_findings(..., commits=[...])` with injected commits, plus one real-git test through `evaluate()`. Use the `SCHEDULE` text from that file with the `missed_window_hours/on_miss/cooldown_minutes` keys added, because `load_schedule` requires them.
- [ ] **Step 2–4:** fail, implement, pass; ruff.
- [ ] **Step 5: Commit** `feat(health): lane findings ported from session-lane-liveness (#199)`.

### Task 6: push channels: `scout/notify_desktop.py` and the command channel

**Files:**
- Create: `plugin/engine/scout/notify_desktop.py`
- Create: `plugin/engine/scout/health_notify.py` (channel functions in this task; state machine in Task 8)
- Create: `plugin/engine/tests/unit/test_notify_desktop.py`, `plugin/engine/tests/unit/test_health_notify_channels.py`

**Interfaces:**
- Produces:
  ```python
  # scout/notify_desktop.py
  def send(title: str, body: str, *, run=subprocess.run, which=shutil.which, env: Mapping[str,str] | None = None) -> bool
  #   osascript (argv-passed title/body, the same AppleScript as the wrapper's notify_auth_failure) → else
  #   notify-send --urgency=critical, with DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/<uid>/bus when unset and the socket exists.
  #   Returns True on exit 0; False when neither binary exists or on OSError/SubprocessError/timeout (10 s).

  # scout/health_notify.py
  @dataclass(frozen=True)
  class Push:
      finding_id: str        # or "recovered"
      kind: str              # "new" | "reminder" | "recovered" | "info"
      severity: str          # "red" | "green" | "yellow"
      title: str
      body: str

  ChannelResult = dict      # {"channel": str, "ok": bool, "detail": str}

  def desktop_channel(push: Push, *, vault: Path, now: datetime, send=notify_desktop.send) -> ChannelResult | None
  #   None (skipped, not recorded) when push.finding_id == "auth.expired" and
  #   <vault>/.scout-logs/.auth-alert-sent holds an epoch (or has an mtime) less than 24 h before now.
  def telegram_channel(push: Push, *, send=notify_telegram.send) -> ChannelResult | None
  #   tier = "action_required" for red new/reminder, else "info"; ConfigError → None (not configured).
  #   Any other exception → {"ok": False, "detail": type(e).__name__}  (never str(e): HTTPError strings embed the bot token).
  def command_channel(push: Push, *, vault: Path, run=subprocess.run) -> ChannelResult | None
  #   reads scout-config.yaml `notify: {command: "<shell string>"}`; absent/blank → None.
  #   runs ["/bin/sh", "-c", command] with env + SCOUT_NOTIFY_TITLE/BODY/SEVERITY/FINDING, timeout 30 s, stdin DEVNULL, output captured.
  #   ok = returncode == 0; timeout → {"ok": False, "detail": "timeout"}; OSError → {"ok": False, "detail": "oserror"}.
  def fan_out(push: Push, *, vault: Path, now: datetime, channels=None) -> list[ChannelResult]
  #   channels default = [desktop_channel, telegram_channel, command_channel] (each wrapped with functools.partial for its kwargs);
  #   calls every channel in order (Q-A), drops None, catches any exception per channel as {"ok": False, "detail": "error"}.
  ```
- [ ] **Step 1: Failing tests:**
  - desktop: osascript is present → `run` is called with `[osascript, "-", title, body]` and the script in `input=`. Only notify-send → the urgency flag is passed and DBUS is set when a fake socket path exists (inject it via `env` and a monkeypatched `Path.exists`, or make the socket path a parameter `bus_path`). Neither → False. A timeout → False.
  - `test_desktop_skips_auth_when_wrapper_alerted_recently`: marker = `str(int(now.timestamp()) - 3600)` → None. 25 h old → sent.
  - telegram: `ConfigError` → None. Red new → `action_required`. Recovered → `info`. `requests.HTTPError` → `ok False` and a detail without the URL.
  - `test_command_channel_gets_env`: command `env > "$OUT"` (with `OUT` passed through `os.environ` via monkeypatch) → the file contains `SCOUT_NOTIFY_FINDING=runs.failing`.
  - `test_command_channel_timeout_is_recorded`: inject a `run` that raises `subprocess.TimeoutExpired` → `{"ok": False, "detail": "timeout"}`. Also check that the real call uses `timeout=30` (assert on the kwargs of a fake `run`).
  - `test_command_channel_failure_is_recorded`: `exit 3` → `ok False`.
  - `test_command_channel_absent_is_skipped`: no `notify:` key → None. Malformed yaml → None.
  - `test_fan_out_calls_every_channel_even_after_one_fails`: three fakes, the first raises → three calls, results `[{"ok": False...}, ...]`.
- [ ] **Step 2–4:** fail, implement, pass; ruff.
- [ ] **Step 5: Commit** `feat(notify): desktop and notify.command channels, fan-out (Q4, Q-A)`.

### Task 7: Telegram send: a duplicate guard and a readable receipt (#241)

Folded into P2 at the coordinator's request (2026-10-11). #241: a scheduled session re-sent a byte-identical 4 055-character wrap because it filtered `scoutctl notify telegram` output with `tail -6`, and the receipt `id` is on line 2 of the indented JSON. A successful send also has no duplicate guard. The guard belongs in `notify_telegram.send`, which both the session wrap (through the CLI) and Task 6's `telegram_channel` call, so one guard covers every Telegram push.

**Files:**
- Modify: `plugin/engine/scout/scripts/notify_telegram.py`
- Modify: `plugin/engine/scout/cli.py` (`cli_notify_telegram`)
- Modify: `plugin/engine/scout/health_notify.py` (`telegram_channel` maps the new exception)
- Modify: `plugin/engine/tests/unit/test_scripts_notify_telegram.py`, `plugin/engine/tests/unit/test_scripts_notify_telegram_main.py`, `plugin/engine/tests/unit/test_health_notify_channels.py`
- Modify: `plugin/CHANGELOG.md`

**Interfaces:**
- Produces:
  ```python
  # scout/scripts/notify_telegram.py
  DUPLICATE_WINDOW = timedelta(minutes=10)
  SENT_LOG = "telegram-sent.jsonl"          # under paths.state_dir(paths.data_dir()); one row per real (non-dry-run) send
  class DuplicateSendError(ScoutError):     # exit_code = 3
      original_id: str; sent_at: str        # the earlier send's Event id and ts
  def secrets_dir() -> Path                 # Path.home() / ".scout-secrets", resolved per call (replaces the import-time SECRETS_DIR)
  def send(tier, body, *, dry_run=False, timeout=DEFAULT_TIMEOUT, allow_duplicate=False, now=None) -> Event
  ```
- The idempotency key is `sha256(chat_id + "\x00" + body)`, hex. Before posting, read `SENT_LOG`, keep the rows younger than `DUPLICATE_WINDOW`, and if one has the same key, raise `DuplicateSendError(original_id, sent_at)` without posting, unless `allow_duplicate`. After a successful real send, append `{"key", "id", "ts"}` and rewrite the file with only the rows still inside the window (atomic tmp + `os.replace`; an `OSError` is swallowed, because the guard must never block a send). An unreadable or corrupt log reads as empty. Dry runs neither check nor record. When the vault can't be resolved, skip the guard; a send never fails for want of a log.
- CLI `scoutctl notify telegram`: adds `--allow-duplicate`. It prints `id=<ulid>` as the **first line on stderr** for every send, dry runs included; stdout stays the unchanged pure JSON. On `DuplicateSendError` it prints `id=<original_id>` first on stderr, then `scoutctl notify telegram: refused — byte-identical message sent at <sent_at> (id <original_id>); pass --allow-duplicate to send it again`, and exits 3.
- `telegram_channel`: `DuplicateSendError` → `{"channel": "telegram", "ok": True, "detail": "duplicate"}`, a recorded no-op, because the message already reached the chat.

- [ ] **Step 1: Failing tests:**
  - `test_identical_body_within_window_is_refused`: two sends with a patched `requests.post` → the first posts, and the second raises `DuplicateSendError` whose `original_id` equals the first Event's id. `post` is called once.
  - `test_different_body_within_window_is_sent` (the negative control #241 asks for).
  - `test_identical_body_after_window_is_sent` (`now` = first + 10 min 1 s).
  - `test_allow_duplicate_sends_again`.
  - `test_dry_run_neither_checks_nor_records`.
  - `test_corrupt_sent_log_reads_as_empty`.
  - `test_secrets_dir_follows_home_per_call`: monkeypatch `HOME` after import → `secrets_dir()` follows it (the import-time-binding bug #241 reports).
  - CLI: a send → `json.loads(stdout)` parses, and `stderr.splitlines()[0] == f"id={json.loads(stdout)['id']}"`. A refused duplicate → exit 3, the stderr first line is `id=<original>`, and stdout is empty. Use `CliRunner(mix_stderr=False)`, or whatever the existing CLI tests use to split streams.
  - `telegram_channel` with a `send` raising `DuplicateSendError` → `ok True, detail "duplicate"`.
- [ ] **Step 2–4:** fail, implement, pass. Run every test that imports `notify_telegram` (`grep -rln notify_telegram tests`); ruff.
- [ ] **Step 5: CHANGELOG** `### Fixed` under `## [Unreleased]`: "`scoutctl notify telegram` refuses to re-send a byte-identical message to the same chat within 10 minutes (`--allow-duplicate` overrides), and prints the receipt as `id=<id>` on the first line of stderr, so a caller never re-sends to find the id. The secrets directory is resolved per call, not at import (#241)."
- [ ] **Step 6: Commit** `fix(notify): duplicate guard and stderr receipt for telegram sends (#241)`.

### Task 8: notifier state machine and `scoutctl health notify`

**Files:**
- Modify: `plugin/engine/scout/health_notify.py`
- Modify: `plugin/engine/scout/cli.py` (`@health_app.command("notify")`)
- Create: `plugin/engine/tests/unit/test_health_notify.py`

**Interfaces:**
- Produces:
  ```python
  STATE_FILE = ".scout-state/health-notify.json"
  REMIND_AFTER = timedelta(hours=24)
  def load_state(vault: Path) -> dict          # {} on missing/corrupt/non-dict
  def save_state(vault: Path, state: dict) -> None   # atomic (tmp + os.replace); OSError swallowed
  def plan(report: RunHealth, state: dict, *, now: datetime) -> tuple[list[Push], dict]
  def notify(vault: Path, *, now: datetime | None = None, channels=None, report: RunHealth | None = None) -> list[tuple[Push, list[ChannelResult]]]
  ```
- State shape: `{"findings": {id: {"first_seen": iso, "last_pushed": iso|None, "pushes": int, "last_results": [ChannelResult...]}}, "last_overall": "green|yellow|red"}`.
- `plan` rules (spec §4.4):
  - For each **red** finding: if it is not in state, or it is in state with `last_pushed is None` → a `new` push, then record it with `pushes += 1` and `last_pushed = now`. If `now - last_pushed ≥ 24 h` → a `reminder` push whose body names the elapsed time since `first_seen` (e.g. "for 3 days"). Otherwise nothing.
  - Yellow and note findings are recorded with `first_seen` only, never pushed.
  - Ids no longer present are dropped from state.
  - When `last_overall == "red"` and the new severity is not red → one `recovered` push (severity green or yellow, body "Scout runs are healthy again." / "…; remaining warnings: N"). Clear the red ids.
  - Set `last_overall` to the new severity.
- Push text: title `"Scout: <summary up to 60 chars>"`; body `summary + "\n" + "Fix: " + fix`. Reminder body: `f"Still failing after {elapsed}: {summary}\nFix: {fix}"`.
- `notify()`: evaluate unless `report` is given, `plan`, `fan_out` each push, store each push's results under `last_results` (the recovered push stores under `state["last_recovered"]`), save. Never raises: wrap the body in `try/except Exception` that returns `[]`.
- CLI: `scoutctl health notify [--quiet]` → calls `notify(paths.data_dir())`. It prints one line per push (`pushed <kind> <id>: <ok channels>`) unless `--quiet` and no push. **Always exit 0.**

- [ ] **Step 1: Failing tests:** a table-driven test with `@pytest.mark.parametrize` over these cases, using `health.build_report` to make reports and a fixed `NOW`:
  1. new red → one `new` push, `pushes == 1`.
  2. the same red 23 h later → no push.
  3. the same red 25 h later → one `reminder`, body contains "1 day" (format elapsed as `"N day(s)"` / `"N hour(s)"`).
  4. red → green → one `recovered`, state has no red ids, `last_overall == "green"`.
  5. red → yellow → one `recovered` (severity yellow).
  6. yellow only → no push, the finding is in state with `last_pushed None`.
  7. two reds → two `new` pushes.
  8. a red id disappears while another red stays → no recovered push, the gone id is dropped.

  Plus: `test_corrupt_state_is_treated_as_empty` (write `"{"` → one `new` push, no exception); `test_notify_records_channel_results` (fake channels → state's `last_results` has their dicts); `test_notify_never_raises` (an evaluate that raises → `[]`); and a CLI test: `health notify --quiet` with a red vault and fake channels (monkeypatch `health_notify.fan_out`) → exit 0, and with a failing fan-out it is still exit 0.
  The desktop `.auth-alert-sent` skip is already covered in Task 6.
- [ ] **Step 2–4:** fail, implement, pass; ruff.
- [ ] **Step 5: Commit** `feat(health): scoutctl health notify — red/reminder/recovered state machine (D4)`.

### Task 9: run-outcome.sh and heartbeat.sh switch to the notifier, with the bash floor (D5)

**Files:**
- Modify: `plugin/templates/scripts/run-outcome.sh.tmpl`
- Modify: `plugin/templates/scripts/heartbeat.sh.tmpl`
- Modify: `plugin/engine/tests/integration/test_run_outcome.py`
- Modify or create: a heartbeat template test (`grep -rn "heartbeat.sh.tmpl" plugin/engine/tests` to find the existing one; add to it)
- Modify: `plugin/CHANGELOG.md`

**What changes:**
- `run-outcome.sh` `cmd_record`: keep the ledger write and `classify_failure`. Delete the streak Python, the `tier` / `hint` case blocks and the Telegram call. After the ledger write, on **every** run (success too, so recovery is noticed):
  ```bash
  if [ -n "$SCOUTCTL" ] && [ -x "$SCOUTCTL" ] \
      && "$SCOUTCTL" health notify --quiet >>"$LOG_DIR/health-notify.log" 2>&1; then
      :
  else
      notify_engine_unreachable
  fi
  return 0
  ```
  The spec's sketch appends to the run's `$log`. Use `$LOG_DIR/health-notify.log` when `$log` is empty or missing, and the run's `$log` otherwise, so the output lands next to the run.
- `notify_engine_unreachable` (new bash function, D5): the marker is `$LOG_DIR/.engine-alert-sent` and holds an epoch. Return early if it is less than `${SCOUT_ENGINE_ALERT_INTERVAL_S:-86400}` old (a non-numeric interval falls back to 86400, like the wrapper). Otherwise send a desktop notification via the same osascript/notify-send block as `claude-with-retry.sh.tmpl::notify_auth_failure` (title `"{{INSTANCE_NAME}} engine is broken"`, body `"scoutctl could not run, so Scout cannot report run health. Reinstall: curl -fsSL https://raw.githubusercontent.com/Raven-Scout/Scout/main/install.sh | bash, or open Scout.app."`), and write the marker only when a notification was sent. A successful `health notify` removes the marker (`rm -f`), so the next breakage alerts at once.
- Update the header comment: the watcher is still the wrapper, and the channels are now `scoutctl health notify` plus the local floor.
- `heartbeat.sh`: replace the lane-liveness block with a daily sweep. Same tz and day logic, marker `health-sweep-${day}.done`, runs `"$SCOUTCTL_BIN" health notify --quiet >>"$SCOUT_DATA_DIR/.scout-logs/health-notify.log" 2>&1 || true`, and prunes old markers with the same `find -mtime +7`. Update the comment: the sweep catches slots that never fire and so never call `run-outcome.sh`.

- [ ] **Step 1: Update/failing tests** in `test_run_outcome.py`:
  - Replace `test_the_first_failure_is_silent_the_second_loud_then_quiet`, `test_a_success_resets_the_failure_streak` and `test_the_notice_names_the_run_the_class_and_the_log` with `test_every_record_calls_health_notify` (stub calls file gets `["health","notify","--quiet"]` once per record, on success and failure) and `test_no_telegram_call_from_bash` (no call starts with `notify`).
  - `test_an_unreachable_scoutctl_raises_one_floor_alert`: scoutctl stub exits 1. Put fake `osascript` and `notify-send` executables on a `PATH` dir that record calls (copy the `_notifier_bin` pattern from `tests/integration/test_claude_with_retry.py`). Two records → exactly one notification, and the marker exists.
  - `test_the_floor_alerts_again_after_the_interval` with `SCOUT_ENGINE_ALERT_INTERVAL_S=0`.
  - `test_a_working_engine_clears_the_floor_marker`.
  - `test_a_missing_scoutctl_raises_the_floor` (`SCOUTCTL_BIN` → a nonexistent path, and no scoutctl on PATH).
  - Keep `test_a_broken_scoutctl_never_fails_the_record` (exit 0 always).
  - Heartbeat: rendered with a stub scoutctl that records argv. A first run → `health notify --quiet` is called, then `heartbeat run`. A second run the same day → only `heartbeat run`. `session-lane-liveness.py` is never invoked (assert on the stub, or that `lane-liveness.log` is absent).
- [ ] **Step 2–4:** fail, implement, pass. Also run `tests/integration/test_claude_with_retry.py` (the three-way AUTH regex test) and every test that renders cat-1 templates: `grep -rln "templates/scripts" tests`. Then the full suite with coverage, compared against P1's missed-line count; ruff.
- [ ] **Step 5: CHANGELOG** under `## [Unreleased]`:
  - `### Added`: "`scoutctl health notify`: one notifier for run health. It pushes when a finding turns red, reminds every 24 h while it stays red, and sends one "recovered" notice. Pushes go to every configured channel: a desktop notification, Telegram, and a new optional `notify.command` in `scout-config.yaml` (run with `SCOUT_NOTIFY_TITLE`/`BODY`/`SEVERITY`/`FINDING`), for headless hosts. Yellow findings never push (#233)."
  - `### Changed`: "`run-outcome.sh` no longer sends its own Telegram streak notices (once at the first failure, again at the second, then silence). It calls `scoutctl health notify` after every run instead, and raises a local desktop alert, at most once a day, when `scoutctl` itself can't run. `heartbeat.sh` runs a daily health sweep in place of `session-lane-liveness.py --notify`; the script stays as a manual instrument. Vaults pick this up on the next `/scout-update`."
- [ ] **Step 6: Commit** `feat(runner): run-outcome and heartbeat call scoutctl health notify; engine-unreachable floor (D4, D5)`.

**P2 PR:** base `run-health/p1`. Title `feat(health): one notifier — scoutctl health notify, desktop/Telegram/notify.command (P2)`. Body: closes #233 (with P1, once all three merge, the permanent-failure notifier with reminders and the engine floor), closes #241 (Telegram duplicate guard + stderr receipt), advances #199 (lane findings); the merge-order note; test plan.

---

# PR P3 — consumers (branch `run-health/p3`, from `run-health/p2`; ONE commit)

### Task 10: shared fixture, `/scout-status`, session wrap phase

**Files:**
- Create: `plugin/engine/tests/fixtures/contract/doctor-run-health.json` (generated; see below)
- Create: `plugin/engine/tests/unit/test_doctor_run_health_contract.py`
- Copy: `apps/macos/ScoutTests/Fixtures/doctor-run-health.json` (byte copy)
- Modify: `.github/workflows/contract.yml`: add a byte-compare step for the new fixture, modelled on step 3 (parser corpus): it `cmp`s each `apps/*/[Ss]cout*Tests/Fixtures/doctor-run-health.json` against the canonical file, fails if none is found, and fails on any tracked copy it didn't check. The path filter already covers `plugin/engine/tests/fixtures/contract/` and `apps/.*/Fixtures/`.
- Modify: `plugin/commands/scout-status.md` (3a's per-type lines, 3g, Step 4's headline and the "Last 10 Runs" highlights)
- Create: `plugin/phases/core/run-health.md`
- Modify: `plugin/CHANGELOG.md`

**The fixture** is the `doctor_dict()` of a deterministic fixture vault: install yellow with one warning, run health red with `auth.expired`, one yellow `slot.stale:…`, and two `slots` entries. The test builds the vault in `tmp_path` with a fixed `now` and a monkeypatched `vault_birth`, calls `run_doctor(vault=…, check_jobs=False)` → `doctor_dict`, replaces `tmp_path` strings with `/Users/alex/Scout`, and compares with `json.dumps(d, indent=2, sort_keys=True) + "\n"`. Set `SCOUT_REGEN_CONTRACT=1` to rewrite the canonical file instead of failing. Assertion message on mismatch: "regenerate with SCOUT_REGEN_CONTRACT=1, then cp it to apps/macos/ScoutTests/Fixtures/doctor-run-health.json". The `evaluate` call inside `run_doctor` uses the real clock, so add a `now: datetime | None = None` keyword to `run_doctor` that it passes through to `health.evaluate`.

**`/scout-status`:**
- Replace 3g's inline Python with `scoutctl health --json` (exit codes 0/1/2 are all fine; anything else, or unparseable output → `RUN_HEALTH_UNAVAILABLE`).
- The headline comes from the worst finding: `auth.expired` keeps today's ❌ wording (with the finding's count and last-success time), any other red → ⚠️ plus the summary and fix, yellow → a one-line "⚠️ N warnings" plus the first summary, green → `✅ Last successful run: <last_ok_at as days ago>`.
- 3a keeps the `git log -10` list for the "Last 10 Runs" listing. The per-type "Last briefing / consolidation / dreaming / research" lines now come from `slots[]` (newest `last_fire` per type, plus that slot's `last_outcome.outcome` and `finished_at`); work-session and meta-review lines keep the git match (#199 §3).

**`phases/core/run-health.md`** (frontmatter copied from `git-setup.md`'s shape, with `phase: core`, `name: run-health`, `slot: run-health`, `mode: [briefing, consolidation, dreaming, research]`, `requires: null`). Body:
- **At the start of the run**: `scoutctl health --json > .scout-cache/run-health.json 2>/dev/null || true`.
- **At wrap**: when the file holds red findings, the wrap notification leads with them (summary + fix, one line each). Then, when yellow findings exist, one line `"⚠️ N run-health warnings — first: <summary>"`. Green says nothing.
- The measured-claims rule from §4.5 verbatim in substance: speak about a run's output only as "N commits in this run's window (newest `abc1234`)" or "no commits in this run's window", and never "by this run".

  Check how `phase_assembly` orders `core` sections (`grep -n "slot\|order" plugin/engine/scout/scripts/phase_assembly.py`). If a slot needs registering in an ordering list or an assembly fixture, do it, and run `tests/unit -k "phase or assembly or backport"`.

- [ ] **Step 1: Failing tests:** the contract test (fails with no canonical file); a test that `phases/core/run-health.md` parses with `parse_phase_file` and has the four modes; an assembly test that SKILL/DREAMING/RESEARCH each contain `scoutctl health --json` (find the existing assembly test that asserts section presence and extend it).
- [ ] **Step 2: Generate** the fixture with `SCOUT_REGEN_CONTRACT=1`, review it by eye (the anonymised path, no real names), and `cp` it to the app path. Then run without the env var → pass.
- [ ] **Step 3: Docs edits** (`scout-status.md`, phase file).
- [ ] **Step 4: Run** the engine suite + ruff; run the contract step's shell logic locally: `cmp plugin/engine/tests/fixtures/contract/doctor-run-health.json apps/macos/ScoutTests/Fixtures/doctor-run-health.json`.
- [ ] **Step 5: CHANGELOG** under `## [Unreleased]`:
  - `### Changed`: "`/scout-status` reads `scoutctl health --json`. Its headline is the worst run-health finding, and its per-type last-run lines come from the scheduler's fires and the run ledger instead of commit messages (#199)."
  - `### Added`: "A run-health phase: every scheduled session writes `scoutctl health --json` to `.scout-cache/run-health.json` at start, and its wrap notification leads with red findings."
  - `### Upgrade notes`: "The new run-health phase reaches existing vaults through `bootstrap upgrade` as a change to SKILL.md / DREAMING.md / RESEARCH.md. A vault with local edits to those files gets a brain-file merge or sidecar: resolve it with `scoutctl bootstrap resolve`."
- [ ] **Step 6: Commit (WIP)**, which is squashed with Task 11 into the single P3 commit.

### Task 11: Scout.app decodes `run_health`; Settings ▸ Engine "Runs" row; the sidebar dot

**Files:**
- Modify: `apps/macos/Scout/Engine/DoctorReport.swift`
- Modify: `apps/macos/Scout/Engine/EngineHealthService.swift` (`needsAttention`)
- Modify: `apps/macos/Scout/Engine/EngineSettingsModel.swift` (`runsLabel`, `runsHelp`, `runsIsOK`)
- Modify: `apps/macos/Scout/Shell/EngineSettingsSection.swift` (a "Runs" `SettingsRow` under "Health")
- Modify: `apps/macos/ScoutTests/Engine/DoctorReportTests.swift`, `apps/macos/ScoutTests/Engine/EngineSettingsModelTests.swift`, and a test for `needsAttention` (find the existing one: `grep -rn needsAttention apps/macos/ScoutTests`)
- The fixture from Task 10 must be in the test bundle. Check how `connectors.snapshot.json` in `ScoutTests/Fixtures` is included (a folder reference in `Scout.xcodeproj` means new files are picked up automatically; otherwise add a file reference to the test target's resources).

**Interfaces:**
```swift
nonisolated struct RunHealth: Equatable, Sendable, Decodable {
    nonisolated struct Finding: Equatable, Sendable, Decodable {
        let id: String; let severity: String; let since: String?; let summary: String; let fix: String; let source: String
    }
    nonisolated struct SlotStatus: Equatable, Sendable, Decodable { let key: String; let type: String; let lastFire: String? }   // CodingKeys: last_fire
    let severity: DoctorReport.Severity
    let evaluatedAt: String          // evaluated_at
    let lastOkAt: String?            // last_ok_at
    let findings: [Finding]
    let slots: [SlotStatus]
    var firstActionable: Finding? { findings.first { $0.severity != "note" } }
}
// DoctorReport gains:  let runHealth: RunHealth?   (CodingKeys run_health; decodeIfPresent; legacy text → nil)
// Keep a memberwise-compatible init: init(severity:errors:warnings:runHealth: = nil) so existing call sites and tests compile.
```
- `needsAttention`: in addition to the current rules, `doctor?.runHealth?.severity == .red` → true. Install severity red still counts as it does today.
- `EngineSettingsModel`: `runsLabel` is "Healthy" / "Warnings" / "Failing" / "Unknown" (no run health); `runsHelp` is `firstActionable.summary` or `"Last successful run <relative last_ok_at>"` or `"No runs recorded yet"`; `runsIsOK` is true when the severity is green.
- The Settings row shows only when `doctor?.runHealth != nil`, mirroring the "Health" row's styling (same `SettingsRow` and status dot / label pattern in the file).

- [ ] **Step 1: Failing Swift tests:**
  - `DoctorReportTests.decodesRunHealthFromSharedFixture`: load `doctor-run-health.json` from the test bundle the way `ClaudePluginsRegistryTests` loads fixtures, then `#expect(r?.runHealth?.severity == .red)`, the first finding id is `auth.expired`, and `slots.count == 2`.
  - `decodesWithoutRunHealth`: the existing JSON test → `runHealth == nil`.
  - `legacyTextHasNoRunHealth`.
  - `EngineSettingsModelTests`: runs label/help for red, green and nil.
  - `needsAttention` true for install green + run health red.
- [ ] **Step 2: Run, expect failure:** `xcodebuild test -project apps/macos/Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/DoctorReportTests` (check `apps/macos/CLAUDE.md` or the CI workflow `.github/workflows/*.yml` for the exact scheme and flags used in CI, and use those).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full `ScoutTests` target (CI's invocation) → pass.
- [ ] **Step 5: Squash** Tasks 10 and 11 into one commit: `git reset --soft run-health/p2 && git commit`, titled `feat(health): run-health consumers — /scout-status, wrap phase, Scout.app Runs row (P3)`, with a body that lists both subtrees and the contract fixture.

**P3 PR:** base `run-health/p2`. Title as the commit. Body: closes #199 (with P1/P2: slot/scheduler/lane findings in doctor, status and the app; status re-sourced from last-fire + ledger; the `update.behind` part of the #199 comment lands with P7), advances #233 (app surface); the merge-order note ("merge P1 → P2 → P3 back to back once this is approved"); test plan with the xcodebuild line.

---

## Per-PR finish (every PR)

1. Full engine suite + coverage, missed lines compared against the base branch; `ruff check .` and `ruff format --check .` from `plugin/engine`.
2. P3 only: the full `ScoutTests` run.
3. A whole-branch review on the most capable model (superpowers:requesting-code-review), against the spec sections this PR implements. Fix what it finds.
4. Push the branch, then `gh pr create --base <base> --head <branch>`. The body maps issues as above and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
5. Message the coordinator with the PR number. Never merge.
