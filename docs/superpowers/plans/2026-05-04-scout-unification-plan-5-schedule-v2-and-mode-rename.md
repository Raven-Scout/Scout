# Scout Engine Plan 5: Schedule v2 + mode rename

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Schedule v2 — engine-canonical schedule (vault YAML) + TZ-aware dispatcher (`scoutctl schedule tick`) + sleep-aware catch-up + mode rename to TZ-neutral semantic slot names + scout-app refactor from in-app dispatcher to read-only UI mirror. Replaces today's 7 per-slot launchd plists with one 5-min `com.scout.schedule-tick.plist`.

**Architecture:** Two-level slot vocabulary (user-chosen slot keys + fixed plugin slot types: `briefing | consolidation | dreaming | research | manual`). Schedule lives at `~/Scout/.scout-state/schedule.yaml`, validated by `scout.schedule`. Dispatcher fires every 5 min via launchd, evaluates due slots against `usage-tracker.jsonl` last-fire timestamps, applies per-slot `on_miss` policy (`fire`/`skip`/`collapse`), pre-spawn network probe, single-fire-per-tick with priority `briefing > consolidation > dreaming > research > manual`. Slot wall-clock times interpreted in system local TZ (TZ-aware by construction); optional per-slot `tz:` pinning available. `connectors.yaml`'s `required_in` field migrates to `required_in_types` so connector roster references the stable type vocabulary, not user-chosen slot keys.

**Tech Stack:** Python 3.11+, Typer, PyYAML (existing runtime dep), python-ulid (existing), `zoneinfo` stdlib (existing pattern from Plan 4 `_et_date`). Pytest + ruff + mypy. Bash for the legacy runner shim deletion. Swift for scout-app `ScheduleService` + `PowerStateService`.

**Position in plan sequence:** Plan 5. Plan 4 (connector subsystem migration) merged 2026-05-01. The plan-queue reorganization documented in `2026-05-04-schedule-v2-design.md §2` shifts existing Plan 5 (KB ontology cache) to Plan 7 and existing Plan 7 (personal-data scrub) to Plan 8. Plan 4-supplement scope shrinks (heartbeat redesigned, not just ported — see design doc §10). Plan 6 (scout-app refactor) tightens (loses the schedule pieces that land here).

---

## Context for the implementer

**Working directories:**
- **scout-plugin** at `plugin/`. Fresh branch off the merged Plan 4 tip:
  ```bash
  cd plugin
  git checkout main
  git pull --ff-only
  git checkout -b plan-5-schedule-v2
  cd engine && ../.venv/bin/pytest tests/ -q       # green expected (301 unit + 9 skipped)
  ```
- **scout-app** at `apps/macos/`. Branch off main (the spec branch `plan-5-schedule-v2-design` already merged or about to). Use a fresh branch `plan-5-scout-app` for the Swift work.
- **Vault** at `/Users/jordanburger/Scout/`. Local-only repo (no remote). Skill/doc updates and old plist deletions commit here.

**Reference docs (READ BEFORE STARTING):**
- `docs/superpowers/specs/2026-05-04-schedule-v2-design.md` — the design spec for this plan. Sections §3 (slot semantics), §4 (dispatcher), §5 (sleep handling), §8 (mode rename), §9 (event taxonomy) are the implementation contract.
- `docs/superpowers/specs/2026-04-24-scout-unification-design.md` §6 (Layout — amended to include schedule.yaml) and §11 (new "Schedule definition lives in the vault" sub-section).
- `docs/superpowers/specs/2026-04-25-scout-event-architecture-design.md` Core Concepts → Schedule events sub-section, and Connector Taxonomy + Async-first user comms (`(slot_type, tier)` routing).
- `docs/superpowers/plans/2026-04-28-scout-unification-plan-4-connector-subsystem-and-hooks-port.md` — pattern to mirror (single source of truth YAML, `scoutctl <subapp>` CLI, parity test pattern, snapshot sync).
- `~/Scout/run-scout.sh` (lines 60–80 — the HOUR-based mode case statement that gets deleted in Task 7) and `~/Scout/run-dreaming.sh` (similar shape).

**What this plan does NOT touch:**
- The 6 remaining Plan 4-supplement scripts (`budget-check`, `rate-limit-detect`, `collect-events`, `pre-session-data`, `cc-session-cache`, `write-session-cost`) — Plan 4-supplement.
- The KB ontology pre-computed `kb_summary.json` cache (formerly Plan 5 — now Plan 7).
- Personal-data scrub of SKILL/DREAMING/RESEARCH (formerly Plan 7 — now Plan 8). Plan 5 *does* update SKILL.md/DREAMING.md/RESEARCH.md/CLAUDE.md for the mode rename, but only the rename — no other content edits.
- Scout-app first-run wizard, `ScoutEnvironment`, `EngineClient` abstraction (Plan 6 — minus the schedule pieces that land here).
- Bidirectional Telegram return-bridge (v0.7+).

**Bash heartbeat preserved.** `~/Scout/scripts/heartbeat.sh` and `com.scout.heartbeat.plist` stay in place — Plan 5 only handles scheduled-dispatch (the role taken over by `scoutctl schedule tick`). Heartbeat's *opportunistic-dispatch* role (firing dreaming/research when conditions allow) stays for Plan 4-supplement to redesign per the design doc §10 split. The two ticks coexist on different cadences (schedule-tick = 5 min; heartbeat = 30 min).

**Old launchd plists.** The 7 existing Scout plists (`com.scout.briefing.plist`, `briefing-weekend.plist`, `consolidation-7pm.plist`, `dreaming.plist`, `dreaming-nightly-10pm.plist`, `dreaming-weekend-{6am,7am}.plist`, `research.plist`) get uninstalled in Task 11. Heartbeat plist stays. The new `com.scout.schedule-tick.plist` from Task 4 takes their role.

## File structure

```
scout-plugin/
├── engine/
│   ├── pyproject.toml                                MODIFIED — Task 1 (no new runtime deps; tests get tzdata)
│   └── scout/
│       ├── schedule.py                               NEW — Task 1 (schema, loader, slot dataclasses, ScheduleRegistry)
│       ├── defaults/
│       │   └── schedule.yaml                         NEW — Task 1 (plugin defaults; copied to vault by `scoutctl schedule init`)
│       ├── cli.py                                    MODIFIED — Tasks 2, 3, 5, 8 (registers `scoutctl schedule` sub-app)
│       ├── connectors.py                             MODIFIED — Task 6 (Connector schema gains required_in_types, deprecates required_in with one-version migration)
│       ├── connectors.yaml                           MODIFIED — Task 6 (rewrite required_in lists as required_in_types)
│       ├── connectors.snapshot.json                  MODIFIED — Task 6 (regenerated)
│       ├── scripts/
│       │   ├── schedule_tick.py                      NEW — Task 3 (the dispatcher)
│       │   ├── schedule_snapshot.py                  NEW — Task 8 (parallel to connectors_snapshot)
│       │   └── connector_health_report.py            MODIFIED — Task 6 (use required_in_types)
│       ├── connectors_runtime.py                     (none — connector logic stays in connectors.py)
│       ├── manifest.py                               MODIFIED — Task 12 (adds `schedule_v2` feature flag)
├── tools/
│   ├── migrate-mode-names.py                         NEW — Task 7 (one-shot JSONL rewriter)
│   └── regenerate-connector-health.py                NEW — Task 7 (one-shot doc regen helper)
├── plugin.json                                       MODIFIED — Task 4 (no hooks change; only manifest version bump)
├── hooks/
│   └── hooks.json                                    UNCHANGED (the 3 Plan 4 hooks stay)
├── tests/
│   ├── unit/
│   │   ├── test_schedule_loader.py                   NEW — Task 1
│   │   ├── test_schedule_tick.py                     NEW — Task 3
│   │   ├── test_schedule_snapshot.py                 NEW — Task 8
│   │   ├── test_scripts_connector_health_required_in_types.py  NEW — Task 6
│   │   └── test_manifest.py                          MODIFIED — Task 12 (asserts schedule_v2 flag flipped)
│   ├── integration/
│   │   └── test_schedule_tick_e2e.py                 NEW — Task 3 (end-to-end against a tmp_path vault + fake clock)
│   ├── parity/
│   │   └── test_schedule_tick_parity.bats            NEW — Task 3 (transitional bats test against scout-app heartbeat output for fixed schedule + clock; removed once Plan 5 ships)
│   └── fixtures/
│       ├── schedule-default.yaml                     NEW — Task 1 (test fixture)
│       ├── schedule-edge-cases.yaml                  NEW — Task 1 (overlay test fixture)
│       ├── usage-tracker-fresh.jsonl                 NEW — Task 3 (no prior fires; fresh-install-like)
│       ├── usage-tracker-overslept.jsonl             NEW — Task 3 (last fire 26h ago — wake-from-sleep scenario)
│       └── connector-calls-pre-rename.jsonl          NEW — Task 7 (sample old-mode-name JSONL for migration test)
└── .github/workflows/test.yml                         MODIFIED — Tasks 6, 8 (CI drift check for schedule snapshot)

scout-app/
├── Scout/
│   ├── Services/
│   │   ├── ScheduleService.swift                     NEW — Task 9 (calls scoutctl schedule list-upcoming; publishes [UpcomingRun])
│   │   ├── PowerStateService.swift                   NEW — Task 9 (polls pmset -g batt; publishes .onAC / .onBattery)
│   │   ├── LaunchdScheduleService.swift              REMOVED — Task 9 (replaced by ScheduleService)
│   │   └── RunnerService.swift                       REMOVED — Task 9 (engine owns dispatch)
│   ├── Models/
│   │   └── RunType.swift                             MODIFIED — Task 9 (rename cases; init(from slotKey:) added)
│   ├── ControlCenter/
│   │   ├── NowStripView.swift                        MODIFIED — Task 9 (Run-now button calls scoutctl schedule fire-now)
│   │   ├── UpcomingStripView.swift                   MODIFIED — Task 9 (consume ScheduleService instead of LaunchdScheduleService)
│   │   └── PowerStateBanner.swift                    NEW — Task 9 (yellow banner above schedule strip when on battery)
│   └── Shell/
│       ├── AppState.swift                            MODIFIED — Task 9 (wire ScheduleService + PowerStateService)
│       └── MenuBarExtraContent.swift                 MODIFIED — Task 9 (delete RunnerService refs; add "Install wake-schedule…" item)
└── ScoutTests/
    ├── Services/
    │   ├── ScheduleServiceTests.swift                NEW — Task 9
    │   └── PowerStateServiceTests.swift              NEW — Task 9
    ├── Fixtures/
    │   └── schedule.snapshot.json                    NEW — Task 8 (committed snapshot for tests)
    └── Models/
        └── RunTypeTests.swift                        NEW — Task 9 (init from slot key)

~/Scout/ (vault, local-only)
├── .scout-state/
│   └── schedule.yaml                                 NEW — Task 12 (seeded by `scoutctl schedule init`)
├── run-scout.sh                                      MODIFIED — Task 4 (delete HOUR case; read SCOUT_FORCE_MODE only)
├── SKILL.md, DREAMING.md, RESEARCH.md                MODIFIED — Task 10 (mode-name rename only)
└── CLAUDE.md                                         MODIFIED — Task 10 (schedule description rename)
```

---

## Task 1: `scout.schedule` module — schema, loader, slot dataclasses

**Files:**
- Create: `engine/scout/schedule.py`
- Create: `engine/scout/defaults/schedule.yaml`
- Create: `engine/tests/unit/test_schedule_loader.py`
- Create: `engine/tests/fixtures/schedule-default.yaml`
- Create: `engine/tests/fixtures/schedule-edge-cases.yaml`

**What this builds:** The vault-YAML schema + typed loader. Mirrors the `scout.connectors` shape from Plan 4 — frozen dataclasses, schema validation at load time via `ConfigError`, no runtime mutation. Slot keys are user-chosen kebab-case identifiers; slot types come from the fixed `SlotType` enum (`briefing | consolidation | dreaming | research | manual`).

The loader resolves slot wall-clock times to a `local_now`-aware `next_fire_at` datetime when asked. Per-slot `tz` field optional; absent → system local; present → IANA zone overrides for that slot only.

- [ ] **Step 1: Write failing tests for schema + loader**

Create `engine/tests/unit/test_schedule_loader.py`:

```python
"""Unit tests for scout.schedule — YAML loader + slot semantics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout.errors import ConfigError
from scout.schedule import (
    OnMissPolicy,
    Slot,
    SlotType,
    Schedule,
    SlotPriority,
    load_schedule,
    load_default_schedule,
)


FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_load_default_schedule_returns_jordan_default_slots():
    sched = load_default_schedule()
    keys = set(sched.keys())
    # The 10 slot keys shipped in engine/scout/defaults/schedule.yaml.
    assert keys >= {
        "morning-briefing",
        "weekend-briefing",
        "morning-consolidation",
        "midday-consolidation",
        "afternoon-consolidation",
        "evening-consolidation",
        "dreaming-evening",
        "dreaming-nightly",
        "dreaming-weekend-morning",
        "research",
    }


def test_slot_dataclass_has_typed_fields():
    sched = load_default_schedule()
    morning = sched["morning-briefing"]
    assert isinstance(morning, Slot)
    assert morning.type == SlotType.BRIEFING
    assert morning.fires_at_local == "08:00"
    assert "Mon" in morning.weekdays
    assert morning.on_miss == OnMissPolicy.FIRE
    assert morning.cooldown_minutes == 60
    assert morning.runner == "run-scout.sh"
    assert morning.budget_usd is None       # optional; absent in default
    assert morning.tz is None               # absent → system local


def test_slot_priority_order_is_briefing_consolidation_dreaming_research_manual():
    assert SlotPriority.BRIEFING.value > SlotPriority.CONSOLIDATION.value
    assert SlotPriority.CONSOLIDATION.value > SlotPriority.DREAMING.value
    assert SlotPriority.DREAMING.value > SlotPriority.RESEARCH.value
    assert SlotPriority.RESEARCH.value > SlotPriority.MANUAL.value


def test_load_schedule_from_explicit_path():
    sched = load_schedule(FIXTURES / "schedule-default.yaml")
    assert "morning-briefing" in sched


def test_unknown_slot_type_raises():
    overlay = FIXTURES / "schedule-edge-cases.yaml"
    # File has slot with `type: not-real-type`.
    with pytest.raises(ConfigError, match="not-real-type"):
        load_schedule(overlay)


def test_invalid_fires_at_local_format_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  bad-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '25:99'\n"          # invalid hour
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    with pytest.raises(ConfigError, match="fires_at_local"):
        load_schedule(bad)


def test_invalid_weekday_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  bad-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Funday]\n"                # not a valid weekday
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    with pytest.raises(ConfigError, match="weekday"):
        load_schedule(bad)


def test_empty_weekdays_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  bad-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: []\n"                      # empty
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    with pytest.raises(ConfigError, match="weekday"):
        load_schedule(bad)


def test_slot_target_today_in_system_local_tz_when_no_override():
    sched = load_default_schedule()
    morning = sched["morning-briefing"]
    # 'Now' set to a specific datetime in local tz; target_today should match.
    fake_now = datetime(2026, 5, 11, 6, 0, tzinfo=ZoneInfo("America/New_York"))  # Mon 6am EDT
    target = morning.target_today(now=fake_now)
    assert target.tzinfo is not None
    assert target.hour == 8
    assert target.minute == 0
    assert target.weekday() == 0   # Monday


def test_slot_target_today_honors_per_slot_tz_override(tmp_path):
    bad_or_explicit = tmp_path / "with-tz.yaml"
    bad_or_explicit.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  pacific-standup:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
        "    tz: America/Los_Angeles\n"
    )
    sched = load_schedule(bad_or_explicit)
    pac = sched["pacific-standup"]
    fake_now = datetime(2026, 5, 11, 14, 0, tzinfo=ZoneInfo("Europe/Prague"))   # 2pm Prague
    target = pac.target_today(now=fake_now)
    # Same wall-clock day; 8am Pacific = different absolute time than 8am Prague.
    assert target.tzinfo == ZoneInfo("America/Los_Angeles")
    assert target.hour == 8


def test_slot_target_today_returns_none_when_weekday_doesnt_match():
    sched = load_default_schedule()
    weekend = sched["weekend-briefing"]
    fake_now = datetime(2026, 5, 11, 6, 0, tzinfo=ZoneInfo("America/New_York"))  # Monday
    assert weekend.target_today(now=fake_now) is None


def test_schedule_keys_iter_lookup_contains():
    sched = load_default_schedule()
    keys = list(sched.keys())
    assert "morning-briefing" in keys
    assert "morning-briefing" in sched
    assert sched["morning-briefing"].type == SlotType.BRIEFING


def test_schedule_get_priority_for_slot_type():
    sched = load_default_schedule()
    morning = sched["morning-briefing"]
    consolidation = sched["morning-consolidation"]
    assert morning.priority > consolidation.priority


def test_overlay_path_layered_on_seed_when_present(tmp_path, monkeypatch):
    """If <vault>/.scout-state/schedule.local.yaml exists, layer on top of the canonical."""
    canonical = tmp_path / "schedule.yaml"
    canonical.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  morning-briefing:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    overlay = tmp_path / "schedule.local.yaml"
    overlay.write_text(
        "slots:\n"
        "  morning-briefing:\n"
        "    fires_at_local: '07:00'\n"          # override only this field
    )
    sched = load_schedule(canonical, overlay=overlay)
    assert sched["morning-briefing"].fires_at_local == "07:00"
    assert sched["morning-briefing"].on_miss == OnMissPolicy.FIRE   # inherited
```

Create `engine/tests/fixtures/schedule-default.yaml`:

```yaml
schema_version: 1

slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60
```

Create `engine/tests/fixtures/schedule-edge-cases.yaml`:

```yaml
schema_version: 1

slots:
  bad-slot:
    type: not-real-type        # Should raise ConfigError on load
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60
```

- [ ] **Step 2: Run tests, confirm RED**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_schedule_loader.py -v
```

Expected: `ModuleNotFoundError: No module named 'scout.schedule'` (all 12 tests fail at import).

- [ ] **Step 3: Write `engine/scout/defaults/schedule.yaml` (the plugin defaults)**

```yaml
# engine/scout/defaults/schedule.yaml — Scout default schedule.
#
# This file is the plugin's seed schedule. `scoutctl schedule init`
# copies it to `~/Scout/.scout-state/schedule.yaml` on first run.
# Users edit the vault copy after that; this file is not modified.
#
# Slot keys are user-renameable. Slot types are a fixed plugin
# vocabulary (briefing | consolidation | dreaming | research | manual)
# that aggregation surfaces (connectors.yaml `required_in_types`,
# alert routing) reference. See:
#   docs/superpowers/specs/2026-05-04-schedule-v2-design.md

schema_version: 1

slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60

  weekend-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:30"
    weekdays: [Sat, Sun]
    missed_window_hours: 6
    on_miss: fire
    cooldown_minutes: 60

  morning-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "11:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 2
    on_miss: collapse
    cooldown_minutes: 90

  midday-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "13:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 2
    on_miss: collapse
    cooldown_minutes: 90

  afternoon-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "17:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 3
    on_miss: collapse
    cooldown_minutes: 90

  evening-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "19:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: collapse
    cooldown_minutes: 90

  dreaming-evening:
    type: dreaming
    runner: run-dreaming.sh
    fires_at_local: "18:30"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 120

  dreaming-nightly:
    type: dreaming
    runner: run-dreaming.sh
    fires_at_local: "22:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]
    missed_window_hours: 6
    on_miss: skip
    cooldown_minutes: 120

  dreaming-weekend-morning:
    type: dreaming
    runner: run-dreaming.sh
    fires_at_local: "07:00"
    weekdays: [Sat, Sun]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 120

  research:
    type: research
    runner: run-research.sh
    fires_at_local: "14:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: skip
    cooldown_minutes: 240
```

- [ ] **Step 4: Implement `engine/scout/schedule.py`**

```python
"""Schedule registry: vault-canonical schedule.yaml loader + slot semantics.

Schedule definition lives at `~/Scout/.scout-state/schedule.yaml`; the engine
ships defaults at `engine/scout/defaults/schedule.yaml` that `scoutctl schedule
init` copies on first run. The vault file is the single source of truth at
runtime.

Slot wall-clock times are interpreted in the system's current local timezone
by default (TZ-aware by construction — travel ET → CEST and the schedule moves
with you). Optional per-slot `tz: <iana-zone>` field pins a slot to a fixed
zone if needed.

See docs/superpowers/specs/2026-05-04-schedule-v2-design.md.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from scout.errors import ConfigError


class SlotType(enum.Enum):
    BRIEFING = "briefing"
    CONSOLIDATION = "consolidation"
    DREAMING = "dreaming"
    RESEARCH = "research"
    MANUAL = "manual"


class OnMissPolicy(enum.Enum):
    FIRE = "fire"
    SKIP = "skip"
    COLLAPSE = "collapse"


class SlotPriority(enum.IntEnum):
    """Priority order for single-fire-per-tick selection.

    Higher integer = fires first when multiple slots are eligible at the
    same tick. Hardcoded; not user-configurable. See design doc §4 step 6.
    """

    BRIEFING = 50
    CONSOLIDATION = 40
    DREAMING = 30
    RESEARCH = 20
    MANUAL = 10


_VALID_WEEKDAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
# Python datetime.weekday() returns Mon=0 ... Sun=6.
_WEEKDAY_INDEX = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


@dataclass(frozen=True)
class Slot:
    """One scheduled slot. Frozen — load_schedule rebuilds; never mutated."""

    key: str                         # user-chosen kebab-case identifier
    type: SlotType                   # fixed plugin vocabulary
    runner: str                      # script name relative to vault root (run-scout.sh, run-dreaming.sh, ...)
    fires_at_local: str              # "HH:MM" 24-hour
    weekdays: tuple[str, ...]        # subset of {Mon,Tue,Wed,Thu,Fri,Sat,Sun}
    missed_window_hours: int
    on_miss: OnMissPolicy
    cooldown_minutes: int
    budget_usd: float | None = None  # optional; not load-bearing in v0.5
    tz: str | None = None            # optional IANA zone; absent → system local

    @property
    def priority(self) -> SlotPriority:
        """Map slot type to its priority for single-fire-per-tick selection."""
        return _PRIORITY_BY_TYPE[self.type]

    def target_today(self, *, now: datetime) -> datetime | None:
        """Return today's target datetime for this slot, or None if today's weekday is excluded.

        `now` must be tz-aware. Slot's tz override (or system local) is honored.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        slot_tz = ZoneInfo(self.tz) if self.tz else now.tzinfo
        local_today = now.astimezone(slot_tz)
        weekday_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][local_today.weekday()]
        if weekday_name not in self.weekdays:
            return None
        hh, mm = self.fires_at_local.split(":")
        return local_today.replace(
            hour=int(hh), minute=int(mm), second=0, microsecond=0
        )


_PRIORITY_BY_TYPE: dict[SlotType, SlotPriority] = {
    SlotType.BRIEFING: SlotPriority.BRIEFING,
    SlotType.CONSOLIDATION: SlotPriority.CONSOLIDATION,
    SlotType.DREAMING: SlotPriority.DREAMING,
    SlotType.RESEARCH: SlotPriority.RESEARCH,
    SlotType.MANUAL: SlotPriority.MANUAL,
}


class Schedule:
    """Indexed view over loaded slots. Use load_schedule() / load_default_schedule()."""

    def __init__(self, slots: dict[str, Slot]) -> None:
        self._slots = slots

    def __contains__(self, key: str) -> bool:
        return key in self._slots

    def __getitem__(self, key: str) -> Slot:
        return self._slots[key]

    def __iter__(self):
        return iter(self._slots)

    def keys(self):
        return self._slots.keys()

    def values(self):
        return self._slots.values()

    def items(self):
        return self._slots.items()

    def by_type(self, slot_type: SlotType) -> list[Slot]:
        return [s for s in self._slots.values() if s.type == slot_type]


def load_default_schedule() -> Schedule:
    """Load the plugin-shipped default schedule.

    Used by tests, `scoutctl schedule init` (to seed the vault), and
    fallback loaders when the vault file is absent.
    """
    return load_schedule(Path(__file__).parent / "defaults" / "schedule.yaml")


def load_schedule(
    canonical_path: Path,
    *,
    overlay: Path | None = None,
) -> Schedule:
    """Load a schedule.yaml. Optionally layer an overlay file on top.

    The overlay is shallow-merged into each slot key (matches Plan 4's
    connectors overlay pattern). Validation runs after the merge.
    """
    seed = _load_yaml(canonical_path)
    merged: dict[str, Any] = dict(seed.get("slots", {}))
    if overlay is not None and overlay.exists():
        ov = _load_yaml(overlay)
        for key, override in ov.get("slots", {}).items():
            if key in merged:
                merged[key] = {**merged[key], **override}
            else:
                merged[key] = override
    slots: dict[str, Slot] = {}
    for key, raw in merged.items():
        slots[key] = _build_slot(key, raw)
    return Schedule(slots)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"schedule yaml at {path} not found") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"schedule yaml at {path} is malformed: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"schedule yaml at {path} is not a mapping")
    return data


def _build_slot(key: str, raw: dict[str, Any]) -> Slot:
    try:
        slot_type = SlotType(raw["type"])
        weekdays_raw = raw.get("weekdays", [])
        if not weekdays_raw:
            raise ConfigError(f"slot {key}: weekdays must be a non-empty list")
        for wd in weekdays_raw:
            if wd not in _VALID_WEEKDAYS:
                raise ConfigError(
                    f"slot {key}: invalid weekday {wd!r}; must be one of {sorted(_VALID_WEEKDAYS)}"
                )
        fires_at_raw = str(raw.get("fires_at_local", ""))
        try:
            hh, mm = fires_at_raw.split(":")
            time(int(hh), int(mm))   # validate via stdlib
        except (ValueError, AttributeError) as e:
            raise ConfigError(
                f"slot {key}: fires_at_local {fires_at_raw!r} is not HH:MM 24-hour"
            ) from e
        on_miss = OnMissPolicy(raw["on_miss"])
        tz = raw.get("tz")
        if tz is not None:
            try:
                ZoneInfo(tz)
            except ZoneInfoNotFoundError as e:
                raise ConfigError(f"slot {key}: unknown tz {tz!r}") from e
        return Slot(
            key=key,
            type=slot_type,
            runner=raw["runner"],
            fires_at_local=fires_at_raw,
            weekdays=tuple(weekdays_raw),
            missed_window_hours=int(raw["missed_window_hours"]),
            on_miss=on_miss,
            cooldown_minutes=int(raw["cooldown_minutes"]),
            budget_usd=raw.get("budget_usd"),   # may be None
            tz=tz,
        )
    except (KeyError, ValueError) as e:
        raise ConfigError(f"slot {key}: malformed entry: {e}") from e
```

- [ ] **Step 5: Re-run tests, confirm GREEN**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_schedule_loader.py -v
```

Expected: 12 passed.

- [ ] **Step 6: Run the full suite to confirm no regression**

```bash
cd plugin/engine
../.venv/bin/pytest tests/ -q
```

Expected: 313 passed (was 301), 9 skipped.

- [ ] **Step 7: Lint**

```bash
cd plugin/engine
../.venv/bin/ruff check scout tests
../.venv/bin/ruff format --check scout tests
../.venv/bin/mypy scout
```

All three should be clean.

- [ ] **Step 8: Commit**

```bash
cd plugin
git add engine/scout/schedule.py engine/scout/defaults/schedule.yaml \
        engine/tests/unit/test_schedule_loader.py \
        engine/tests/fixtures/schedule-default.yaml \
        engine/tests/fixtures/schedule-edge-cases.yaml
git commit -m "feat(engine): scout.schedule — schema validator + loader for vault schedule.yaml"
```

---

## Task 2: `scoutctl schedule {list,show,validate,init,reload}` sub-app

**Files:**
- Modify: `engine/scout/cli.py`
- Create: `engine/tests/unit/test_cli_schedule_subapp.py`

**What this builds:** Read-side CLI surface. Mirrors the `connectors` sub-app from Plan 4 — `list` enumerates slot keys + types, `show` dumps one slot's full record, `validate` re-loads and asserts the schema is clean, `init` seeds the vault from plugin defaults, `reload` is a no-op CLI signal (loader has no module-level cache; the command exists for forward-compat with future caching).

The dispatcher's `tick` and `fire-now` commands land in Task 3.

- [ ] **Step 1: Write failing tests**

Create `engine/tests/unit/test_cli_schedule_subapp.py`:

```python
"""CLI smoke tests for `scoutctl schedule {list,show,validate,init,reload}`."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from scout.cli import app


runner = CliRunner(mix_stderr=False)


def test_schedule_list_shows_all_default_slots():
    result = runner.invoke(app, ["schedule", "list"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "morning-briefing" in result.stdout
    assert "morning-consolidation" in result.stdout
    assert "dreaming-evening" in result.stdout
    assert "research" in result.stdout


def test_schedule_show_single_slot_returns_full_record():
    result = runner.invoke(app, ["schedule", "show", "morning-briefing"])
    assert result.exit_code == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)
    assert record["key"] == "morning-briefing"
    assert record["type"] == "briefing"
    assert record["fires_at_local"] == "08:00"
    assert record["on_miss"] == "fire"


def test_schedule_show_unknown_slot_exits_nonzero():
    result = runner.invoke(app, ["schedule", "show", "no-such-slot"])
    assert result.exit_code != 0
    assert "no-such-slot" in (result.stdout + result.stderr)


def test_schedule_validate_returns_zero_on_default():
    result = runner.invoke(app, ["schedule", "validate"])
    assert result.exit_code == 0, result.stdout + result.stderr


def test_schedule_init_writes_vault_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["schedule", "init"])
    assert result.exit_code == 0, result.stdout + result.stderr
    written = tmp_path / ".scout-state" / "schedule.yaml"
    assert written.exists()
    assert "morning-briefing" in written.read_text()


def test_schedule_init_refuses_to_overwrite_existing_without_force(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    target = tmp_path / ".scout-state" / "schedule.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# existing user content\n")
    result = runner.invoke(app, ["schedule", "init"])
    assert result.exit_code != 0
    assert "exists" in (result.stdout + result.stderr).lower()
    # Existing content preserved.
    assert target.read_text() == "# existing user content\n"


def test_schedule_init_force_overwrites_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    target = tmp_path / ".scout-state" / "schedule.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# old content\n")
    result = runner.invoke(app, ["schedule", "init", "--force"])
    assert result.exit_code == 0
    assert "morning-briefing" in target.read_text()


def test_schedule_reload_succeeds():
    result = runner.invoke(app, ["schedule", "reload"])
    assert result.exit_code == 0
    assert "reloaded" in result.stdout.lower()
```

- [ ] **Step 2: Run, confirm RED**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_cli_schedule_subapp.py -v
```

Expected: 8 failures (no `schedule` sub-app registered).

- [ ] **Step 3: Wire `scoutctl schedule` sub-app in `engine/scout/cli.py`**

Open `engine/scout/cli.py`. Find the `_register_connectors()` function (added in Plan 4) and add `_register_schedule()` immediately after it. Then call `_register_schedule()` at module level alongside the existing `_register_connectors()` invocation.

```python
def _register_schedule() -> None:
    """scoutctl schedule {list,show,validate,init,reload} — vault schedule operations."""

    schedule_app = typer.Typer(help="Schedule operations (vault schedule.yaml).")
    app.add_typer(schedule_app, name="schedule")

    @schedule_app.command("list")
    def cli_schedule_list() -> None:
        """List the registered schedule slots."""
        from scout.schedule import load_default_schedule, load_schedule
        from scout import paths as _paths

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = (
            load_schedule(vault_path)
            if vault_path.exists()
            else load_default_schedule()
        )
        for key in sorted(sched.keys()):
            slot = sched[key]
            typer.echo(
                f"{key}\t{slot.type.value}\t{slot.fires_at_local}\t"
                f"{','.join(slot.weekdays)}\t{slot.on_miss.value}"
            )

    @schedule_app.command("show")
    def cli_schedule_show(key: str) -> None:
        """Show one slot's full record as JSON."""
        from scout.schedule import load_default_schedule, load_schedule
        from scout import paths as _paths
        import json as _json

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = (
            load_schedule(vault_path)
            if vault_path.exists()
            else load_default_schedule()
        )
        if key not in sched:
            typer.echo(f"unknown slot: {key}", err=True)
            raise typer.Exit(code=1)
        slot = sched[key]
        record = {
            "key": slot.key,
            "type": slot.type.value,
            "runner": slot.runner,
            "fires_at_local": slot.fires_at_local,
            "weekdays": list(slot.weekdays),
            "missed_window_hours": slot.missed_window_hours,
            "on_miss": slot.on_miss.value,
            "cooldown_minutes": slot.cooldown_minutes,
            "budget_usd": slot.budget_usd,
            "tz": slot.tz,
        }
        typer.echo(_json.dumps(record, indent=2))

    @schedule_app.command("validate")
    def cli_schedule_validate() -> None:
        """Re-load the schedule (canonical + overlay if present); exit 0 on success."""
        from scout.schedule import load_default_schedule, load_schedule
        from scout import paths as _paths

        vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        if vault_path.exists():
            load_schedule(vault_path)
            typer.echo(f"schedule OK: {vault_path}")
        else:
            load_default_schedule()
            typer.echo("schedule OK: (no vault file; using plugin defaults)")

    @schedule_app.command("init")
    def cli_schedule_init(
        force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing vault file."),
    ) -> None:
        """Seed the vault schedule.yaml from plugin defaults."""
        from scout import paths as _paths
        import shutil

        target = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        if target.exists() and not force:
            typer.echo(
                f"{target} exists; refusing to overwrite. Use --force to replace.",
                err=True,
            )
            raise typer.Exit(code=1)
        target.parent.mkdir(parents=True, exist_ok=True)
        source = Path(__file__).parent / "defaults" / "schedule.yaml"
        shutil.copy2(source, target)
        typer.echo(f"wrote: {target}")

    @schedule_app.command("reload")
    def cli_schedule_reload() -> None:
        """Force-reload the schedule (forward-compat signal; loader has no cache in v0.5)."""
        from scout.schedule import load_default_schedule

        load_default_schedule()
        typer.echo("reloaded")


_register_schedule()
```

(Keep `_register_connectors()` and the existing hook/script registrations untouched.)

Make sure to add `from pathlib import Path` at the top of `cli.py` if not present.

- [ ] **Step 4: Re-run, confirm GREEN**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_cli_schedule_subapp.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Smoke-check the CLI manually**

```bash
plugin/.venv/bin/scoutctl schedule list 2>&1 | head -10
plugin/.venv/bin/scoutctl schedule show morning-briefing
plugin/.venv/bin/scoutctl schedule validate
```

Expected: list prints 10 rows; show prints JSON; validate prints `schedule OK: ...`.

- [ ] **Step 6: Lint and run full suite**

```bash
cd plugin/engine
../.venv/bin/ruff check scout tests
../.venv/bin/ruff format --check scout tests
../.venv/bin/mypy scout
../.venv/bin/pytest tests/ -q
```

All clean; full suite 321 passed (was 313), 9 skipped.

- [ ] **Step 7: Commit**

```bash
cd plugin
git add engine/scout/cli.py engine/tests/unit/test_cli_schedule_subapp.py
git commit -m "feat(engine): scoutctl schedule {list,show,validate,init,reload} sub-app"
```

---

## Task 3: `scoutctl schedule tick` dispatcher

**Files:**
- Create: `engine/scout/scripts/schedule_tick.py`
- Modify: `engine/scout/cli.py` (registers `tick` and `fire-now` commands under the existing `schedule` sub-app)
- Modify: `engine/scout/events.py` (no — Event already has the right shape; no edits needed)
- Create: `engine/tests/unit/test_schedule_tick.py`
- Create: `engine/tests/integration/test_schedule_tick_e2e.py`
- Create: `engine/tests/parity/test_schedule_tick_parity.bats`
- Create: `engine/tests/fixtures/usage-tracker-fresh.jsonl`
- Create: `engine/tests/fixtures/usage-tracker-overslept.jsonl`

**What this builds:** The brain. Single tick algorithm per design doc §4: load schedule + tracker → compute due slots → apply on_miss policy → pre-spawn network probe → single-fire-per-tick by priority → spawn runner with `SCOUT_FORCE_MODE` → emit events. Concurrency-safe via fcntl flock on `.scout-state/.schedule-tick.lock`. Events emit through the same JSONL writer the Plan 4 hooks use.

`fire-now <slot-key>` is a manual fire that bypasses the dispatcher's policy logic — used by scout-app's "Run now" buttons.

- [ ] **Step 1: Write the unit tests**

Create `engine/tests/unit/test_schedule_tick.py`:

```python
"""Unit tests for scout.scripts.schedule_tick — the dispatcher brain."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from scout.events import Event
from scout.schedule import (
    OnMissPolicy,
    Slot,
    SlotPriority,
    SlotType,
    load_default_schedule,
)
from scout.scripts.schedule_tick import (
    Decision,
    SlotCandidate,
    _apply_miss_rules,
    _compute_due_slots,
    _filter_winner_by_priority,
    _network_ready,
    _read_last_fire_index,
    run as tick_run,
)


# Helpers used by multiple tests below.
def _slot(
    key: str,
    *,
    type_: SlotType = SlotType.CONSOLIDATION,
    fires_at: str = "11:00",
    weekdays: tuple = ("Mon", "Tue", "Wed", "Thu", "Fri"),
    missed_window_hours: int = 2,
    on_miss: OnMissPolicy = OnMissPolicy.COLLAPSE,
    cooldown_minutes: int = 90,
) -> Slot:
    return Slot(
        key=key,
        type=type_,
        runner="run-scout.sh",
        fires_at_local=fires_at,
        weekdays=weekdays,
        missed_window_hours=missed_window_hours,
        on_miss=on_miss,
        cooldown_minutes=cooldown_minutes,
    )


# 1. compute_due_slots: only slots whose target time has passed and last fire is older than today's target.

def test_compute_due_slots_includes_slot_past_target_with_no_prior_fire():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 11, 30, tzinfo=et)   # Mon 11:30 EDT
    last_fire = {}                                    # no prior fires
    candidates = _compute_due_slots(sched, last_fire, now)
    keys = {c.slot_key for c in candidates}
    assert "morning-briefing" in keys
    assert "morning-consolidation" in keys
    assert "midday-consolidation" not in keys        # 13:00 hasn't passed yet


def test_compute_due_slots_skips_slot_within_cooldown():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 11, 30, tzinfo=et)
    last_fire = {"morning-consolidation": now - timedelta(minutes=30)}  # cooldown_minutes=90 → still cooling down
    candidates = _compute_due_slots(sched, last_fire, now)
    assert "morning-consolidation" not in {c.slot_key for c in candidates}


def test_compute_due_slots_excludes_slot_with_today_fire():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 11, 30, tzinfo=et)
    last_fire = {
        "morning-consolidation": datetime(2026, 5, 11, 11, 5, tzinfo=et)  # already fired today
    }
    candidates = _compute_due_slots(sched, last_fire, now)
    assert "morning-consolidation" not in {c.slot_key for c in candidates}


# 2. apply_miss_rules: on_miss policy + missed_window + collapse-within-type semantics.

def test_apply_miss_rules_fire_within_window():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 11, 30, tzinfo=et)
    candidate = SlotCandidate(
        slot_key="morning-briefing",
        slot=sched["morning-briefing"],          # on_miss=fire, missed_window=4h
        target=datetime(2026, 5, 11, 8, 0, tzinfo=et),  # 3.5h ago — within window
        last_fire=None,
    )
    decisions = _apply_miss_rules([candidate], now=now)
    assert decisions["morning-briefing"].action == "fire"


def test_apply_miss_rules_fire_outside_window_skips():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 14, 0, tzinfo=et)
    candidate = SlotCandidate(
        slot_key="morning-briefing",
        slot=sched["morning-briefing"],          # on_miss=fire, missed_window=4h
        target=datetime(2026, 5, 11, 8, 0, tzinfo=et),  # 6h ago — outside window
        last_fire=None,
    )
    decisions = _apply_miss_rules([candidate], now=now)
    assert decisions["morning-briefing"].action == "skip"
    assert "stale" in decisions["morning-briefing"].reason


def test_apply_miss_rules_skip_policy_always_skips():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 14, 30, tzinfo=et)
    candidate = SlotCandidate(
        slot_key="research",
        slot=sched["research"],                  # on_miss=skip
        target=datetime(2026, 5, 11, 14, 0, tzinfo=et),
        last_fire=None,
    )
    decisions = _apply_miss_rules([candidate], now=now)
    assert decisions["research"].action == "skip"
    assert "on_miss=skip" in decisions["research"].reason


def test_apply_miss_rules_collapse_within_type_fires_only_latest():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 17, 30, tzinfo=et)   # past 17:00 slot
    morning = SlotCandidate(
        "morning-consolidation", sched["morning-consolidation"],
        target=datetime(2026, 5, 11, 11, 0, tzinfo=et),
        last_fire=None,
    )
    midday = SlotCandidate(
        "midday-consolidation", sched["midday-consolidation"],
        target=datetime(2026, 5, 11, 13, 0, tzinfo=et),
        last_fire=None,
    )
    afternoon = SlotCandidate(
        "afternoon-consolidation", sched["afternoon-consolidation"],
        target=datetime(2026, 5, 11, 17, 0, tzinfo=et),
        last_fire=None,
    )
    decisions = _apply_miss_rules([morning, midday, afternoon], now=now)
    # Only the latest collapses-into-winner; morning + midday get skipped.
    assert decisions["afternoon-consolidation"].action == "fire"
    assert decisions["morning-consolidation"].action == "skip"
    assert "collapsed-into=afternoon-consolidation" in decisions["morning-consolidation"].reason
    assert decisions["midday-consolidation"].action == "skip"


def test_apply_miss_rules_collapse_respects_window_for_oldest():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 19, 30, tzinfo=et)
    morning = SlotCandidate(
        "morning-consolidation", sched["morning-consolidation"],
        target=datetime(2026, 5, 11, 11, 0, tzinfo=et),  # 8.5h ago — outside 2h window even before collapse
        last_fire=None,
    )
    evening = SlotCandidate(
        "evening-consolidation", sched["evening-consolidation"],
        target=datetime(2026, 5, 11, 19, 0, tzinfo=et),
        last_fire=None,
    )
    decisions = _apply_miss_rules([morning, evening], now=now)
    # Both stale or collapsed; only evening fires (within its 4h window).
    assert decisions["evening-consolidation"].action == "fire"
    assert decisions["morning-consolidation"].action == "skip"


# 3. priority filter: at most one fire per tick.

def test_filter_winner_by_priority_picks_briefing_over_consolidation():
    sched = load_default_schedule()
    decisions = {
        "morning-briefing": Decision(action="fire"),
        "afternoon-consolidation": Decision(action="fire"),
    }
    winner = _filter_winner_by_priority(sched, decisions)
    # Briefing has higher priority; consolidation deferred to next tick.
    assert winner == "morning-briefing"


def test_filter_winner_by_priority_picks_consolidation_when_no_briefing():
    sched = load_default_schedule()
    decisions = {
        "afternoon-consolidation": Decision(action="fire"),
        "dreaming-evening": Decision(action="fire"),
    }
    winner = _filter_winner_by_priority(sched, decisions)
    assert winner == "afternoon-consolidation"


def test_filter_winner_returns_none_when_no_fire_decisions():
    sched = load_default_schedule()
    decisions = {
        "morning-briefing": Decision(action="skip", reason="stale"),
    }
    assert _filter_winner_by_priority(sched, decisions) is None


# 4. network probe: tick early-exits when api.anthropic.com unreachable.

def test_network_ready_returns_true_when_probe_succeeds():
    with patch("scout.scripts.schedule_tick.socket.create_connection") as mock_conn:
        mock_conn.return_value.__enter__ = lambda s: None
        mock_conn.return_value.__exit__ = lambda *args: None
        assert _network_ready(retries=1, sleep_seconds=0) is True


def test_network_ready_returns_false_after_exhausting_retries():
    with patch("scout.scripts.schedule_tick.socket.create_connection", side_effect=OSError("dns")):
        assert _network_ready(retries=2, sleep_seconds=0) is False


# 5. tracker reading.

def test_read_last_fire_index_extracts_per_slot_last_ts(tmp_path):
    log_dir = tmp_path / ".scout-logs"
    log_dir.mkdir()
    tracker = log_dir / "usage-tracker.jsonl"
    tracker.write_text(
        '{"ts":"2026-05-11T12:00:00Z","type":"briefing","scout_mode":"morning-briefing"}\n'
        '{"ts":"2026-05-11T15:00:00Z","type":"consolidation","scout_mode":"morning-consolidation"}\n'
        '{"ts":"2026-05-11T17:00:00Z","type":"consolidation","scout_mode":"afternoon-consolidation"}\n'
    )
    index = _read_last_fire_index(tracker)
    assert "morning-briefing" in index
    assert "afternoon-consolidation" in index
    assert index["morning-briefing"] < index["afternoon-consolidation"]


def test_read_last_fire_index_is_empty_when_tracker_missing(tmp_path):
    assert _read_last_fire_index(tmp_path / "no.jsonl") == {}


# 6. End-to-end: run() emits Event and writes JSONL row.

def test_run_emits_schedule_tick_completed_event(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    # Write a minimal vault schedule.yaml with one slot whose target is in the past.
    sched_path = tmp_path / ".scout-state" / "schedule.yaml"
    sched_path.write_text(
        "schema_version: 1\nslots:\n  smoke-slot:\n"
        "    type: manual\n    runner: run-scout.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: skip\n    cooldown_minutes: 5\n"
    )
    # Mock subprocess.Popen so no real runner spawns; mock _network_ready True.
    with patch("scout.scripts.schedule_tick._network_ready", return_value=True), \
         patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 99999
        ev = tick_run()
    assert isinstance(ev, Event)
    assert ev.kind == "schedule.tick.completed"


def test_run_skips_when_network_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched_path = tmp_path / ".scout-state" / "schedule.yaml"
    sched_path.write_text(
        "schema_version: 1\nslots:\n  smoke-slot:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: fire\n    cooldown_minutes: 5\n"
    )
    with patch("scout.scripts.schedule_tick._network_ready", return_value=False), \
         patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen:
        ev = tick_run()
    # Network offline → no spawn; tick still emits a completed event.
    mock_popen.assert_not_called()
    assert ev.kind == "schedule.tick.completed"
    # The skipped reason should appear in the JSONL records written this tick.
    skipped_log = next((tmp_path / ".scout-logs").glob("schedule-events-*.jsonl"), None)
    assert skipped_log is not None
    events = [json.loads(l) for l in skipped_log.read_text().splitlines()]
    skip_kinds = [e for e in events if e["kind"] == "slot.skipped"]
    assert any("network-offline" in (e.get("payload") or {}).get("reason", "") for e in skip_kinds)


def test_run_lock_held_returns_quickly(tmp_path, monkeypatch):
    """Concurrency guard: a held flock causes the second tick to early-exit."""
    import fcntl

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    lock_path = tmp_path / ".scout-state" / ".schedule-tick.lock"
    lock_path.touch()
    with open(lock_path, "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ev = tick_run()
    assert ev.kind == "schedule.tick.skipped"
    assert ev.payload.get("reason") == "lock_held"
```

Create `engine/tests/integration/test_schedule_tick_e2e.py`:

```python
"""Integration test: full tick against a tmp_path vault + fake clock."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scout.scripts.schedule_tick import run as tick_run


def test_e2e_tick_fires_briefing_on_monday_morning(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    state = tmp_path / ".scout-state"
    state.mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched = state / "schedule.yaml"
    sched.write_text(
        "schema_version: 1\nslots:\n  morning-briefing:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '08:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 4\n    on_miss: fire\n    cooldown_minutes: 60\n"
    )
    et = ZoneInfo("America/New_York")
    fake_now = datetime(2026, 5, 11, 8, 5, tzinfo=et)   # Mon 8:05 EDT
    with patch("scout.scripts.schedule_tick._now", return_value=fake_now), \
         patch("scout.scripts.schedule_tick._network_ready", return_value=True), \
         patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 12345
        ev = tick_run()
    # Spawned the briefing runner with SCOUT_FORCE_MODE.
    args, kwargs = mock_popen.call_args
    env = kwargs["env"]
    assert env["SCOUT_FORCE_MODE"] == "morning-briefing"
    assert ev.kind == "schedule.tick.completed"
    assert "morning-briefing" in (ev.payload or {}).get("fired", [])


def test_e2e_tick_handles_wake_from_sleep_with_priority_winner(tmp_path, monkeypatch):
    """Wake at 3pm Mon after closed-laptop morning. Briefing wins on priority."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    state = tmp_path / ".scout-state"
    state.mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched = state / "schedule.yaml"
    sched.write_text(
        "schema_version: 1\nslots:\n"
        "  morning-briefing:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '08:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 8\n    on_miss: fire\n    cooldown_minutes: 60\n"
        "  morning-consolidation:\n"
        "    type: consolidation\n    runner: run-scout.sh\n    fires_at_local: '11:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 8\n    on_miss: collapse\n    cooldown_minutes: 90\n"
    )
    et = ZoneInfo("America/New_York")
    fake_now = datetime(2026, 5, 11, 15, 0, tzinfo=et)   # Mon 3pm — both slots stale-but-still-in-window
    with patch("scout.scripts.schedule_tick._now", return_value=fake_now), \
         patch("scout.scripts.schedule_tick._network_ready", return_value=True), \
         patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 99
        ev = tick_run()
    # Single fire this tick; briefing wins on priority.
    assert mock_popen.call_count == 1
    args, kwargs = mock_popen.call_args
    assert kwargs["env"]["SCOUT_FORCE_MODE"] == "morning-briefing"
    assert "morning-briefing" in (ev.payload or {}).get("fired", [])
    # Consolidation deferred (not skipped, not fired).
    assert "morning-consolidation" not in (ev.payload or {}).get("fired", [])
    assert "morning-consolidation" not in (ev.payload or {}).get("skipped", [])
```

- [ ] **Step 2: Run, confirm RED**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_schedule_tick.py tests/integration/test_schedule_tick_e2e.py -v
```

Expected: ~14 tests fail with `ModuleNotFoundError: No module named 'scout.scripts.schedule_tick'`.

- [ ] **Step 3: Implement `engine/scout/scripts/schedule_tick.py`**

```python
"""Schedule tick — engine-canonical schedule dispatcher.

Runs every 5 min via launchd (`com.scout.schedule-tick.plist`). Idempotent.
Concurrency-safe via fcntl flock. See:
  docs/superpowers/specs/2026-05-04-schedule-v2-design.md §4
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import socket
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scout import paths as _paths
from scout.events import Event, now_iso
from scout.ids import new_ulid
from scout.schedule import (
    OnMissPolicy,
    Schedule,
    Slot,
    SlotPriority,
    SlotType,
    load_default_schedule,
    load_schedule,
)


# Tunables (hardcoded; see design doc §14 open question).
NETWORK_PROBE_HOST = "api.anthropic.com"
NETWORK_PROBE_PORT = 443
NETWORK_PROBE_TIMEOUT_SECONDS = 3
NETWORK_PROBE_RETRIES = 6
NETWORK_PROBE_SLEEP_SECONDS = 5

LOCK_FILENAME = ".schedule-tick.lock"
TRACKER_FILENAME = "usage-tracker.jsonl"
EVENT_LOG_PREFIX = "schedule-events-"


@dataclass(frozen=True)
class SlotCandidate:
    """A slot whose target time has passed and that hasn't fired today yet."""

    slot_key: str
    slot: Slot
    target: datetime
    last_fire: datetime | None


@dataclass(frozen=True)
class Decision:
    action: str        # "fire" | "skip"
    reason: str = ""


def _now() -> datetime:
    """Indirection so tests can monkeypatch the clock."""
    return datetime.now(ZoneInfo(_local_tz_name()))


def _local_tz_name() -> str:
    """Resolve the system's local timezone name; falls back to UTC."""
    # tzlocal would be cleaner but it's an extra dep; use /etc/localtime symlink target.
    try:
        target = os.readlink("/etc/localtime")
        # /etc/localtime → /usr/share/zoneinfo/America/New_York etc.
        idx = target.find("zoneinfo/")
        if idx >= 0:
            return target[idx + len("zoneinfo/"):]
    except OSError:
        pass
    return "UTC"


def run(*, now: datetime | None = None) -> Event:
    """Single tick. Idempotent. Safe to call from launchd or manually."""
    now = now or _now()
    data_dir = _paths.data_dir()
    state_dir = data_dir / ".scout-state"
    log_dir = data_dir / ".scout-logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    lock_path = state_dir / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Concurrency guard: non-blocking exclusive lock.
    try:
        with _try_lock(lock_path) as acquired:
            if not acquired:
                return _emit_event(
                    log_dir,
                    kind="schedule.tick.skipped",
                    source="cli:schedule_tick",
                    payload={"reason": "lock_held"},
                )

            return _do_tick(data_dir=data_dir, log_dir=log_dir, now=now)
    except OSError as e:
        return _emit_event(
            log_dir,
            kind="schedule.tick.failed",
            source="cli:schedule_tick",
            payload={"reason": f"lock_error: {e}"},
        )


def fire_now(slot_key: str) -> Event:
    """Manually fire a slot, bypassing the dispatcher's policy logic.

    Used by scout-app's "Run now" buttons. Acquires the same lock so a
    concurrent `tick` won't double-spawn for the same slot.
    """
    data_dir = _paths.data_dir()
    log_dir = data_dir / ".scout-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    state_dir = data_dir / ".scout-state"
    state_dir.mkdir(parents=True, exist_ok=True)

    sched = _load_active_schedule(data_dir)
    if slot_key not in sched:
        return _emit_event(
            log_dir,
            kind="slot.fire_failed",
            source="cli:schedule_fire_now",
            payload={"slot_key": slot_key, "error": "unknown slot"},
        )
    slot = sched[slot_key]

    lock_path = state_dir / LOCK_FILENAME
    with _try_lock(lock_path) as acquired:
        if not acquired:
            return _emit_event(
                log_dir,
                kind="slot.fire_failed",
                source="cli:schedule_fire_now",
                payload={"slot_key": slot_key, "error": "lock_held"},
            )
        try:
            pid = _spawn_runner(slot_key, slot)
            _record_fire(data_dir / ".scout-logs" / TRACKER_FILENAME, slot_key, slot, _now())
            return _emit_event(
                log_dir,
                kind="slot.fired",
                source="cli:schedule_fire_now",
                payload={
                    "slot_key": slot_key,
                    "slot_type": slot.type.value,
                    "runner": slot.runner,
                    "pid_spawned": pid,
                    "manual": True,
                },
            )
        except Exception as e:
            return _emit_event(
                log_dir,
                kind="slot.fire_failed",
                source="cli:schedule_fire_now",
                payload={"slot_key": slot_key, "error": str(e)},
            )


# ---- internals ---------------------------------------------------------------


def _do_tick(*, data_dir: Path, log_dir: Path, now: datetime) -> Event:
    sched = _load_active_schedule(data_dir)
    tracker_path = log_dir / TRACKER_FILENAME
    last_fire = _read_last_fire_index(tracker_path)

    candidates = _compute_due_slots(sched, last_fire, now)
    decisions = _apply_miss_rules(candidates, now=now)
    skipped: list[str] = []
    fired: list[str] = []

    if any(d.action == "fire" for d in decisions.values()):
        ready = _network_ready(
            retries=NETWORK_PROBE_RETRIES,
            sleep_seconds=NETWORK_PROBE_SLEEP_SECONDS,
        )
        if not ready:
            for key, dec in decisions.items():
                if dec.action == "fire":
                    _emit_event(
                        log_dir,
                        kind="slot.skipped",
                        source="cli:schedule_tick",
                        payload={
                            "slot_key": key,
                            "slot_type": sched[key].type.value,
                            "target_local": _iso_local(candidates_by_key(candidates)[key].target),
                            "reason": "network-offline",
                        },
                    )
                    skipped.append(key)
            for key, dec in decisions.items():
                if dec.action == "skip":
                    _emit_event(
                        log_dir,
                        kind="slot.skipped",
                        source="cli:schedule_tick",
                        payload={
                            "slot_key": key,
                            "slot_type": sched[key].type.value,
                            "target_local": _iso_local(candidates_by_key(candidates)[key].target),
                            "reason": dec.reason,
                        },
                    )
                    skipped.append(key)
            return _emit_event(
                log_dir,
                kind="schedule.tick.completed",
                source="cli:schedule_tick",
                payload={"fired": fired, "skipped": skipped},
            )

    # Single-fire-per-tick by priority.
    winner = _filter_winner_by_priority(sched, decisions)
    for key, dec in decisions.items():
        if dec.action == "skip":
            _emit_event(
                log_dir,
                kind="slot.skipped",
                source="cli:schedule_tick",
                payload={
                    "slot_key": key,
                    "slot_type": sched[key].type.value,
                    "target_local": _iso_local(candidates_by_key(candidates)[key].target),
                    "reason": dec.reason,
                },
            )
            skipped.append(key)
    if winner is not None:
        slot = sched[winner]
        target = candidates_by_key(candidates)[winner].target
        try:
            pid = _spawn_runner(winner, slot)
            _record_fire(tracker_path, winner, slot, now)
            _emit_event(
                log_dir,
                kind="slot.fired",
                source="cli:schedule_tick",
                payload={
                    "slot_key": winner,
                    "slot_type": slot.type.value,
                    "target_local": _iso_local(target),
                    "runner": slot.runner,
                    "pid_spawned": pid,
                },
            )
            fired.append(winner)
        except Exception as e:
            _emit_event(
                log_dir,
                kind="slot.fire_failed",
                source="cli:schedule_tick",
                payload={
                    "slot_key": winner,
                    "slot_type": slot.type.value,
                    "target_local": _iso_local(target),
                    "error": str(e),
                },
            )

    return _emit_event(
        log_dir,
        kind="schedule.tick.completed",
        source="cli:schedule_tick",
        payload={"fired": fired, "skipped": skipped},
    )


def _load_active_schedule(data_dir: Path) -> Schedule:
    """Vault if present; else plugin defaults."""
    vault = data_dir / ".scout-state" / "schedule.yaml"
    if vault.exists():
        return load_schedule(vault)
    return load_default_schedule()


def _compute_due_slots(
    sched: Schedule, last_fire: dict[str, datetime], now: datetime
) -> list[SlotCandidate]:
    out: list[SlotCandidate] = []
    for key in sched.keys():
        slot = sched[key]
        target = slot.target_today(now=now)
        if target is None:
            continue
        if now < target:
            continue
        last = last_fire.get(key)
        if last is not None and last >= target:
            continue                                  # already fired today
        if last is not None and (now - last) < timedelta(minutes=slot.cooldown_minutes):
            continue                                  # within cooldown
        out.append(SlotCandidate(slot_key=key, slot=slot, target=target, last_fire=last))
    return out


def _apply_miss_rules(
    candidates: list[SlotCandidate], *, now: datetime
) -> dict[str, Decision]:
    decisions: dict[str, Decision] = {}
    by_type: dict[SlotType, list[SlotCandidate]] = defaultdict(list)
    for c in candidates:
        by_type[c.slot.type].append(c)

    for slot_type, group in by_type.items():
        group.sort(key=lambda c: c.target)        # earliest first; latest is group[-1]
        latest = group[-1] if group else None
        for c in group:
            staleness_h = (now - c.target).total_seconds() / 3600
            if staleness_h > c.slot.missed_window_hours:
                decisions[c.slot_key] = Decision(
                    action="skip",
                    reason=f"stale-after-window: {staleness_h:.1f}h > {c.slot.missed_window_hours}h",
                )
                continue
            if c.slot.on_miss == OnMissPolicy.SKIP:
                decisions[c.slot_key] = Decision(action="skip", reason="on_miss=skip")
            elif c.slot.on_miss == OnMissPolicy.COLLAPSE:
                if c is latest:
                    decisions[c.slot_key] = Decision(action="fire")
                else:
                    decisions[c.slot_key] = Decision(
                        action="skip",
                        reason=f"collapsed-into={latest.slot_key}",
                    )
            else:                                    # OnMissPolicy.FIRE
                decisions[c.slot_key] = Decision(action="fire")
    return decisions


def _filter_winner_by_priority(
    sched: Schedule, decisions: dict[str, Decision]
) -> str | None:
    fire_keys = [k for k, d in decisions.items() if d.action == "fire"]
    if not fire_keys:
        return None
    return max(fire_keys, key=lambda k: int(sched[k].priority))


def _network_ready(
    *, retries: int = NETWORK_PROBE_RETRIES, sleep_seconds: int = NETWORK_PROBE_SLEEP_SECONDS
) -> bool:
    for attempt in range(retries):
        try:
            with socket.create_connection(
                (NETWORK_PROBE_HOST, NETWORK_PROBE_PORT),
                timeout=NETWORK_PROBE_TIMEOUT_SECONDS,
            ):
                return True
        except OSError:
            if attempt + 1 < retries:
                time.sleep(sleep_seconds)
    return False


def _read_last_fire_index(tracker_path: Path) -> dict[str, datetime]:
    """Parse usage-tracker.jsonl into {slot_key: latest_ts}."""
    if not tracker_path.exists():
        return {}
    out: dict[str, datetime] = {}
    try:
        with tracker_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = rec.get("scout_mode") or rec.get("slot_key")
                ts_str = rec.get("ts")
                if not key or not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if key not in out or ts > out[key]:
                    out[key] = ts
    except OSError:
        return {}
    return out


def _spawn_runner(slot_key: str, slot: Slot) -> int:
    """Spawn the runner subprocess; return its PID."""
    runner_path = _paths.data_dir() / slot.runner
    if not runner_path.exists():
        raise FileNotFoundError(f"runner script not found: {runner_path}")
    env = os.environ.copy()
    env["SCOUT_FORCE_MODE"] = slot_key
    env["SCOUT_DATA_DIR"] = str(_paths.data_dir())
    proc = subprocess.Popen(
        ["/bin/bash", str(runner_path)],
        env=env,
        cwd=str(_paths.data_dir()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def _record_fire(tracker_path: Path, slot_key: str, slot: Slot, now: datetime) -> None:
    rec = {
        "ts": now.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "type": slot.type.value,
        "scout_mode": slot_key,
    }
    try:
        with tracker_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _emit_event(log_dir: Path, *, kind: str, source: str, payload: dict[str, Any]) -> Event:
    ev = Event(id=new_ulid(), ts=now_iso(), kind=kind, source=source, payload=payload)
    rec = {
        "id": ev.id, "ts": ev.ts, "kind": ev.kind, "source": ev.source, "payload": ev.payload
    }
    today = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d")
    out_path = log_dir / f"{EVENT_LOG_PREFIX}{today}.jsonl"
    try:
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except OSError:
        pass
    return ev


def _iso_local(dt: datetime) -> str:
    return dt.isoformat()


@contextlib.contextmanager
def _try_lock(lock_path: Path):
    """Yield True if the lock was acquired; False if already held."""
    f = open(lock_path, "w")
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield True
        except BlockingIOError:
            yield False
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()


def candidates_by_key(candidates: list[SlotCandidate]) -> dict[str, SlotCandidate]:
    return {c.slot_key: c for c in candidates}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — `scoutctl schedule tick` calls this."""
    try:
        run()
    except Exception as e:
        print(f"schedule_tick: unhandled error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Wire `tick` and `fire-now` into the `schedule` Typer sub-app**

In `engine/scout/cli.py`, inside `_register_schedule()`, add:

```python
    @schedule_app.command("tick")
    def cli_schedule_tick() -> None:
        """Run a single dispatch tick. Invoked by com.scout.schedule-tick.plist every 5 min."""
        from scout.scripts.schedule_tick import main as _main
        raise typer.Exit(code=_main())

    @schedule_app.command("fire-now")
    def cli_schedule_fire_now(slot_key: str) -> None:
        """Manually fire a slot, bypassing the dispatcher's policy logic."""
        from scout.scripts.schedule_tick import fire_now as _fire_now
        ev = _fire_now(slot_key)
        if ev.kind == "slot.fire_failed":
            typer.echo(f"failed: {(ev.payload or {}).get('error', 'unknown')}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"fired: {slot_key}")
```

- [ ] **Step 5: Re-run, confirm GREEN**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_schedule_tick.py tests/integration/test_schedule_tick_e2e.py -v
```

Expected: ~14 tests pass.

- [ ] **Step 6: Bats parity test (transitional)**

Create `engine/tests/parity/test_schedule_tick_parity.bats`:

```bash
#!/usr/bin/env bats
# Transitional bats parity test: assert `scoutctl schedule tick` makes the
# same fire decision as scout-app's heartbeat dispatcher would for a fixed
# schedule + fixed clock + fixed tracker. Skip cleanly if scout-app's CLI
# inspection isn't reachable. Removed once Plan 5 lands and the in-app
# dispatcher is gone (Task 9).

setup() {
    SCOUT_DATA_DIR=$(mktemp -d)
    export SCOUT_DATA_DIR
    PYTHON_TICK="$HOME/scout-plugin/.venv/bin/scoutctl"
    if [ ! -x "$PYTHON_TICK" ]; then
        skip "scoutctl not at expected path"
    fi
    mkdir -p "$SCOUT_DATA_DIR/.scout-state" "$SCOUT_DATA_DIR/.scout-logs"
    cat > "$SCOUT_DATA_DIR/.scout-state/schedule.yaml" <<EOF
schema_version: 1
slots:
  morning-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 4
    on_miss: fire
    cooldown_minutes: 60
EOF
    # No-op runner so the tick doesn't actually try to invoke claude.
    cat > "$SCOUT_DATA_DIR/run-scout.sh" <<EOF
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$SCOUT_DATA_DIR/run-scout.sh"
}

teardown() {
    rm -rf "$SCOUT_DATA_DIR"
}

@test "tick fires briefing when schedule says it should and tracker is empty" {
    # Stub the network probe by setting an env var the dispatcher honors.
    SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE=1 \
        "$PYTHON_TICK" schedule tick

    # The schedule-events JSONL should contain a slot.fired event for morning-briefing.
    grep -q '"kind":"slot.fired"' "$SCOUT_DATA_DIR/.scout-logs/schedule-events-"*.jsonl
    grep -q '"slot_key":"morning-briefing"' "$SCOUT_DATA_DIR/.scout-logs/schedule-events-"*.jsonl
}
```

The bats test references `SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE=1` — add that escape hatch in `_network_ready`:

```python
def _network_ready(
    *, retries: int = NETWORK_PROBE_RETRIES, sleep_seconds: int = NETWORK_PROBE_SLEEP_SECONDS
) -> bool:
    if os.environ.get("SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE") == "1":
        return True
    # ... rest unchanged
```

- [ ] **Step 7: Lint and run full suite**

```bash
cd plugin/engine
../.venv/bin/ruff check scout tests
../.venv/bin/ruff format --check scout tests
../.venv/bin/mypy scout
../.venv/bin/pytest tests/ -q
bats engine/tests/parity/test_schedule_tick_parity.bats
```

All clean; full suite ~335 passed (was 321), 9 skipped; bats 1/1 pass.

- [ ] **Step 8: Commit**

```bash
cd plugin
git add engine/scout/scripts/schedule_tick.py engine/scout/cli.py \
        engine/tests/unit/test_schedule_tick.py \
        engine/tests/integration/test_schedule_tick_e2e.py \
        engine/tests/parity/test_schedule_tick_parity.bats
git commit -m "feat(engine): scoutctl schedule tick — engine-canonical TZ-aware dispatcher with catch-up"
```

---

## Task 4: Install `com.scout.schedule-tick.plist` + simplify `run-scout.sh`

**Files:**
- Create: `engine/scout/defaults/com.scout.schedule-tick.plist` (template; uses `__USER_HOME__` placeholder filled at install time)
- Create: `engine/scout/scripts/install_schedule_plist.py` (helper invoked by `scoutctl schedule install-plist`)
- Modify: `engine/scout/cli.py` (registers `schedule install-plist` / `--uninstall-plist`)
- Modify: `~/Scout/run-scout.sh` (delete HOUR-based mode case; read SCOUT_FORCE_MODE only)
- Create: `engine/tests/unit/test_install_schedule_plist.py`

**What this builds:** The launchd plist that drives the dispatcher every 5 min, plus the run-scout.sh simplification (the engine no longer cares what hour it is — the dispatcher tells it via `SCOUT_FORCE_MODE`). `install-plist` is a separate command from `install-wake-schedule` (Task 5) — this one is the always-required core; that one is the optional pmset add-on.

- [ ] **Step 1: Write the plist template**

Create `engine/scout/defaults/com.scout.schedule-tick.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<!--
  com.scout.schedule-tick.plist — runs `scoutctl schedule tick` every 5 min.

  Installed by `scoutctl schedule install-plist`, which fills __USER_HOME__
  at install time. Replaces the 7 per-slot legacy plists deleted in Task 11.
-->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.scout.schedule-tick</string>
    <key>ProgramArguments</key>
    <array>
        <string>__USER_HOME__/scout-plugin/.venv/bin/scoutctl</string>
        <string>schedule</string>
        <string>tick</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>__USER_HOME__/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>__USER_HOME__</string>
    </dict>
    <key>StandardOutPath</key>
    <string>__USER_HOME__/Scout/.scout-logs/launchd-schedule-tick-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>__USER_HOME__/Scout/.scout-logs/launchd-schedule-tick-stderr.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Write failing tests**

Create `engine/tests/unit/test_install_schedule_plist.py`:

```python
"""Unit tests for engine/scout/scripts/install_schedule_plist.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.install_schedule_plist import install_plist, uninstall_plist


def test_install_plist_writes_filled_template(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    install_plist(home=tmp_path, agents_dir=target_dir)
    written = target_dir / "com.scout.schedule-tick.plist"
    assert written.exists()
    content = written.read_text()
    assert "__USER_HOME__" not in content                 # placeholders filled
    assert str(tmp_path) in content
    assert "<integer>300</integer>" in content


def test_install_plist_refuses_to_overwrite_without_force(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.schedule-tick.plist"
    plist.write_text("# existing\n")
    with pytest.raises(FileExistsError):
        install_plist(home=tmp_path, agents_dir=target_dir, force=False)
    assert plist.read_text() == "# existing\n"


def test_install_plist_force_overwrites(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.schedule-tick.plist"
    plist.write_text("# old\n")
    install_plist(home=tmp_path, agents_dir=target_dir, force=True)
    assert "<integer>300</integer>" in plist.read_text()


def test_uninstall_plist_removes_file(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    plist = target_dir / "com.scout.schedule-tick.plist"
    plist.write_text("dummy\n")
    uninstall_plist(agents_dir=target_dir)
    assert not plist.exists()


def test_uninstall_plist_silent_when_missing(tmp_path):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    # No exception when target plist doesn't exist.
    uninstall_plist(agents_dir=target_dir)
```

- [ ] **Step 3: Run tests, confirm RED**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_install_schedule_plist.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 4: Implement `engine/scout/scripts/install_schedule_plist.py`**

```python
"""Helper for `scoutctl schedule install-plist [--uninstall] [--force]`.

Filling __USER_HOME__ in the template at install time; not at runtime, because
launchd's plist parser doesn't expand env vars in <string> values.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

PLIST_NAME = "com.scout.schedule-tick.plist"
TEMPLATE = Path(__file__).parent.parent / "defaults" / PLIST_NAME


def install_plist(
    *,
    home: Path,
    agents_dir: Path | None = None,
    force: bool = False,
    bootstrap: bool = False,
) -> Path:
    """Render the template into ~/Library/LaunchAgents/."""
    agents_dir = agents_dir or (home / "Library" / "LaunchAgents")
    agents_dir.mkdir(parents=True, exist_ok=True)
    target = agents_dir / PLIST_NAME
    if target.exists() and not force:
        raise FileExistsError(target)
    rendered = TEMPLATE.read_text().replace("__USER_HOME__", str(home))
    target.write_text(rendered)
    if bootstrap:
        # `launchctl bootstrap gui/$UID <plist>` loads the job. Best-effort.
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(target)],
            check=False,
        )
    return target


def uninstall_plist(
    *, agents_dir: Path | None = None, bootout: bool = False
) -> None:
    """Remove the plist (and optionally bootout the job from launchd)."""
    agents_dir = agents_dir or (Path.home() / "Library" / "LaunchAgents")
    target = agents_dir / PLIST_NAME
    if bootout:
        uid = os.getuid()
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/com.scout.schedule-tick"],
            check=False,
        )
    if target.exists():
        target.unlink()
```

- [ ] **Step 5: Wire into `scoutctl schedule install-plist`**

In `engine/scout/cli.py`, inside `_register_schedule()`, add:

```python
    @schedule_app.command("install-plist")
    def cli_schedule_install_plist(
        force: bool = typer.Option(False, "--force", "-f"),
        bootstrap: bool = typer.Option(True, "--bootstrap/--no-bootstrap",
                                        help="Run launchctl bootstrap to load the job after writing the plist."),
        uninstall: bool = typer.Option(False, "--uninstall",
                                        help="Remove the plist (and bootout the job) instead of installing."),
    ) -> None:
        """Install or remove com.scout.schedule-tick.plist in ~/Library/LaunchAgents/."""
        from scout.scripts.install_schedule_plist import install_plist as _i, uninstall_plist as _u
        from pathlib import Path as _Path

        if uninstall:
            _u(bootout=bootstrap)
            typer.echo("uninstalled com.scout.schedule-tick.plist")
            return
        try:
            target = _i(home=_Path.home(), force=force, bootstrap=bootstrap)
            typer.echo(f"installed: {target}")
        except FileExistsError as e:
            typer.echo(f"plist already exists at {e}; use --force to overwrite", err=True)
            raise typer.Exit(code=1) from e
```

- [ ] **Step 6: Re-run, confirm GREEN**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_install_schedule_plist.py -v
```

Expected: 5 passed.

- [ ] **Step 7: Simplify `~/Scout/run-scout.sh`**

In `~/Scout/run-scout.sh`, locate the block (around lines 60–80):

```bash
# Determine mode label for session name (must happen before pre-session hooks that use $MODE)
DAY_OF_WEEK=$(TZ=America/New_York date '+%u')
HOUR=$(date +%H)
if [ "$DAY_OF_WEEK" -ge 6 ]; then
    case $HOUR in
        08) MODE="weekend-briefing" ;;
        *)  MODE="weekend-manual" ;;
    esac
else
    case $HOUR in
        08) MODE="morning-briefing" ;;
        11) MODE="consolidation-11am" ;;
        13) MODE="consolidation-1pm" ;;
        17) MODE="consolidation-5pm" ;;
        19) MODE="consolidation-7pm" ;;
        *)  MODE="manual" ;;
    esac
fi

# SCOUT_FORCE_MODE overrides the hour-derived mode (used by Scout.app's
# "Run now" buttons). Take whatever the caller specified.
if [ -n "${SCOUT_FORCE_MODE:-}" ]; then
    MODE="$SCOUT_FORCE_MODE"
fi
```

Replace it with:

```bash
# Mode is set by the dispatcher (SCOUT_FORCE_MODE). For manual invocations
# without SCOUT_FORCE_MODE set, default to "manual" — operators can still
# launch the runner directly for ad-hoc work; the dispatcher is the
# canonical caller.
MODE="${SCOUT_FORCE_MODE:-manual}"
```

Save the file. Verify it parses with:

```bash
bash -n ~/Scout/run-scout.sh
```

(Apply the same simplification to `~/Scout/run-dreaming.sh` and `~/Scout/run-research.sh` if they have similar HOUR-based blocks.)

- [ ] **Step 8: Smoke-install the plist (real machine)**

```bash
plugin/.venv/bin/scoutctl schedule install-plist --force --bootstrap
launchctl list | grep com.scout.schedule-tick     # should show the new job
```

Verify the dispatcher fires (wait up to 5 min, then check):

```bash
ls -lat ~/Scout/.scout-logs/schedule-events-*.jsonl | head -1
tail -5 $(ls -t ~/Scout/.scout-logs/schedule-events-*.jsonl | head -1)
```

You should see at least one `schedule.tick.completed` event in the latest log file.

- [ ] **Step 9: Lint, run suite, commit**

```bash
cd plugin/engine
../.venv/bin/ruff check scout tests
../.venv/bin/ruff format --check scout tests
../.venv/bin/mypy scout
../.venv/bin/pytest tests/ -q
```

All clean; ~340 passed, 9 skipped.

```bash
cd plugin
git add engine/scout/scripts/install_schedule_plist.py engine/scout/cli.py \
        engine/scout/defaults/com.scout.schedule-tick.plist \
        engine/tests/unit/test_install_schedule_plist.py
git commit -m "feat(engine): scoutctl schedule install-plist + com.scout.schedule-tick.plist (5-min dispatcher)"
```

```bash
cd ~/Scout
git add run-scout.sh run-dreaming.sh run-research.sh
git commit -m "scout: simplify run-*.sh — mode comes from SCOUT_FORCE_MODE only (Plan 5)"
```

---

## Task 5: `scoutctl schedule install-wake-schedule` (opt-in pmset)

**Files:**
- Modify: `engine/scout/cli.py` (registers `schedule install-wake-schedule [--uninstall]`)
- Create: `engine/scout/scripts/install_wake_schedule.py`
- Create: `engine/tests/unit/test_install_wake_schedule.py`

**What this builds:** Opt-in `pmset repeat wakeorpoweron` rule for live firing on AC. Documented as AC-only per design doc §5; the command prints the caveat at install time.

- [ ] **Step 1: Write failing tests**

Create `engine/tests/unit/test_install_wake_schedule.py`:

```python
"""Unit tests for install_wake_schedule.py."""

from __future__ import annotations

from datetime import time
from unittest.mock import patch

import pytest

from scout.scripts.install_wake_schedule import (
    compute_earliest_weekday_slot,
    install_wake_schedule,
    uninstall_wake_schedule,
)
from scout.schedule import load_default_schedule


def test_compute_earliest_weekday_slot_returns_morning_briefing():
    sched = load_default_schedule()
    slot = compute_earliest_weekday_slot(sched)
    assert slot is not None
    assert slot.fires_at_local == "07:00"             # dreaming-weekend-morning is earliest, but it's weekend-only
    # Wait — dreaming-weekend-morning is Sat/Sun only. Earliest weekday is morning-briefing 08:00.
    # Adjust: function should filter to slots with at least one weekday in {Mon..Fri}.
    weekdays = set(slot.weekdays)
    assert weekdays.intersection({"Mon", "Tue", "Wed", "Thu", "Fri"})


def test_install_wake_schedule_invokes_pmset_repeat(tmp_path):
    sched = load_default_schedule()
    with patch("scout.scripts.install_wake_schedule.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        install_wake_schedule(sched, dry_run=False)
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "pmset"
    assert "repeat" in cmd
    assert "wakeorpoweron" in cmd
    assert "MTWRF" in cmd                              # weekday set


def test_install_wake_schedule_dry_run_doesnt_invoke_pmset():
    sched = load_default_schedule()
    with patch("scout.scripts.install_wake_schedule.subprocess.run") as mock_run:
        install_wake_schedule(sched, dry_run=True)
    mock_run.assert_not_called()


def test_uninstall_wake_schedule_invokes_pmset_repeat_cancel():
    with patch("scout.scripts.install_wake_schedule.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        uninstall_wake_schedule()
    args, kwargs = mock_run.call_args
    cmd = args[0]
    assert cmd[0] == "pmset"
    assert "repeat" in cmd
    assert "cancel" in cmd
```

- [ ] **Step 2: Run, confirm RED**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_install_wake_schedule.py -v
```

Expected: 4 failures (no module).

- [ ] **Step 3: Implement `engine/scout/scripts/install_wake_schedule.py`**

```python
"""scoutctl schedule install-wake-schedule [--uninstall].

Wraps `pmset repeat wakeorpoweron <DAYS> <HH:MM:SS>` to wake the Mac for the
earliest scheduled weekday slot. Documented limitation: only reliable on AC
power; on battery + lid-closed, Apple Silicon laptops enter standby with wake
timers suppressed.
"""

from __future__ import annotations

import subprocess

from scout.schedule import Schedule, Slot


_WEEKDAY_LETTER = {"Mon": "M", "Tue": "T", "Wed": "W", "Thu": "R", "Fri": "F", "Sat": "S", "Sun": "U"}


def compute_earliest_weekday_slot(sched: Schedule) -> Slot | None:
    """Return the slot with the earliest fires_at_local that has at least one weekday."""
    candidates = [
        s for s in sched.values()
        if any(d in {"Mon", "Tue", "Wed", "Thu", "Fri"} for d in s.weekdays)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda s: s.fires_at_local)


def install_wake_schedule(sched: Schedule, *, dry_run: bool = False) -> str:
    """Install the pmset repeat rule. Returns the command summary string."""
    slot = compute_earliest_weekday_slot(sched)
    if slot is None:
        raise ValueError("no weekday slot found in schedule; cannot compute wake time")
    days = "".join(_WEEKDAY_LETTER[d] for d in slot.weekdays if d in _WEEKDAY_LETTER)
    if not days:
        raise ValueError(f"slot {slot.key} has no recognizable weekdays")
    hhmm = slot.fires_at_local
    cmd = ["pmset", "repeat", "wakeorpoweron", days, f"{hhmm}:00"]
    if dry_run:
        return f"[dry-run] would run: {' '.join(cmd)}"
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pmset failed: {result.stderr.strip()}")
    return f"installed: {' '.join(cmd)}"


def uninstall_wake_schedule(*, dry_run: bool = False) -> str:
    cmd = ["pmset", "repeat", "cancel"]
    if dry_run:
        return f"[dry-run] would run: {' '.join(cmd)}"
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pmset failed: {result.stderr.strip()}")
    return "uninstalled"
```

- [ ] **Step 4: Wire into the CLI**

In `engine/scout/cli.py`, inside `_register_schedule()`, add:

```python
    @schedule_app.command("install-wake-schedule")
    def cli_schedule_install_wake_schedule(
        uninstall: bool = typer.Option(False, "--uninstall"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ) -> None:
        """Install (or remove) a pmset repeat rule that wakes the Mac for the earliest weekday slot.

        AC-only: macOS standby suppresses wake timers when on battery + lid closed.
        Keep the laptop plugged in if you need guaranteed live firing.
        """
        from scout.schedule import load_default_schedule, load_schedule
        from scout.scripts.install_wake_schedule import (
            install_wake_schedule as _i, uninstall_wake_schedule as _u,
        )
        from scout import paths as _paths

        if uninstall:
            typer.echo(_u(dry_run=dry_run))
            return
        vault = _paths.data_dir() / ".scout-state" / "schedule.yaml"
        sched = load_schedule(vault) if vault.exists() else load_default_schedule()
        typer.echo(
            "Note: pmset wake-schedule is AC-only. On battery + lid closed, "
            "Apple Silicon laptops enter standby and ignore wake timers. "
            "Keep the laptop plugged in if you need guaranteed live firing."
        )
        typer.echo(_i(sched, dry_run=dry_run))
```

- [ ] **Step 5: Re-run, confirm GREEN, lint, commit**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_install_wake_schedule.py -v
../.venv/bin/ruff check scout tests
../.venv/bin/ruff format --check scout tests
../.venv/bin/mypy scout
../.venv/bin/pytest tests/ -q
```

All green; ~344 passed, 9 skipped.

```bash
cd plugin
git add engine/scout/scripts/install_wake_schedule.py engine/scout/cli.py \
        engine/tests/unit/test_install_wake_schedule.py
git commit -m "feat(engine): scoutctl schedule install-wake-schedule (opt-in pmset; AC-only caveat documented)"
```

---

## Task 6: Mode rename — `connectors.yaml` `required_in` → `required_in_types`; connector_health_report consumes types

**Files:**
- Modify: `engine/scout/connectors.py` (Connector dataclass gains `required_in_types: tuple[SlotType, ...]`; `required_in` deprecated for one minor version with conversion)
- Modify: `engine/scout/connectors.yaml` (rewrite all 10 entries)
- Modify: `engine/scout/scripts/connector_health_report.py` (use `required_in_types` for the chronic-skip rule)
- Modify: `engine/scout/connectors.snapshot.json` (regenerated)
- Modify: `engine/tests/unit/test_connectors_yaml.py` (assert `required_in_types`)
- Create: `engine/tests/unit/test_scripts_connector_health_required_in_types.py` (rule-level test)

**What this builds:** Connectors now reference slot TYPES (the fixed plugin vocabulary), not slot KEY names. So Slack's "required everywhere" stays the same; Granola/Drive's "required only on briefings + consolidations, not weekend dreaming" expresses cleanly via `[briefing, consolidation]`.

- [ ] **Step 1: Update `engine/scout/connectors.py`**

Open `engine/scout/connectors.py`. Add `required_in_types: tuple[SlotType, ...]` field to the `Connector` dataclass; keep `required_in` for one transitional version with a deprecation warning. Add a new helper `required_in_type(slot_type: SlotType) -> bool`.

```python
# At the top, add:
from scout.schedule import SlotType

# In the Connector dataclass:
@dataclass(frozen=True)
class Connector:
    key: str
    display_name: str
    tier: Tier
    capabilities: tuple[Capability, ...]
    required_in: tuple[str, ...] | str       # DEPRECATED: kept for one version
    required_in_types: tuple[SlotType, ...]  # NEW: source of truth in v0.5+
    remediation: Remediation
    notes: str = ""

    def required_in_mode(self, mode: str) -> bool:
        """Backwards-compat: was used by the old mode-name-based rule. Prefer required_in_type."""
        if self.required_in == "all":
            return True
        return mode in self.required_in

    def required_in_type(self, slot_type: SlotType) -> bool:
        """v0.5+ canonical: is this connector required in any slot of the given type?"""
        if self.required_in_types == ():
            return False                                 # outbound-only, e.g.
        return slot_type in self.required_in_types
```

Update `_build_connector` to read `required_in_types` from the YAML and parse it; if absent, derive heuristically from `required_in` (transitional fallback).

```python
def _build_connector(key: str, raw: dict[str, Any]) -> Connector:
    try:
        tier = Tier(raw.get("tier", "official"))
        capabilities = tuple(Capability(c) for c in raw.get("capabilities", []))
        # required_in (deprecated path)
        required_in_raw = raw.get("required_in", [])
        required_in: tuple[str, ...] | str
        if required_in_raw == "all":
            required_in = "all"
        else:
            required_in = tuple(required_in_raw)
        # required_in_types (v0.5+ canonical)
        rit_raw = raw.get("required_in_types")
        if rit_raw is None:
            # Transitional fallback: nothing → empty tuple (outbound).
            required_in_types: tuple[SlotType, ...] = ()
        else:
            required_in_types = tuple(SlotType(t) for t in rit_raw)
        rem_raw = raw.get("remediation", {})
        remediation = Remediation(
            first_fix=rem_raw.get("first_fix", ""),
            detail=rem_raw.get("detail", ""),
        )
        return Connector(
            key=key,
            display_name=raw["display_name"],
            tier=tier,
            capabilities=capabilities,
            required_in=required_in,
            required_in_types=required_in_types,
            remediation=remediation,
            notes=raw.get("notes", "") or "",
        )
    except (KeyError, ValueError) as e:
        raise ConfigError(f"connector {key} entry is malformed: {e}") from e
```

- [ ] **Step 2: Rewrite `engine/scout/connectors.yaml`**

For each connector entry, replace `required_in: [...]` with `required_in_types: [...]`. The mapping:

| Old `required_in` | New `required_in_types` |
|---|---|
| `all` | `[briefing, consolidation, dreaming, research]` |
| `[morning-briefing, weekend-briefing, consolidation-11am, ...]` | `[briefing, consolidation]` |
| `[]` (outbound-only) | `[]` |

Apply to all 10 connectors. Example for Slack:

```yaml
mcp:claude_ai_Slack:
  display_name: Slack
  tier: official
  capabilities: [inbound, outbound]
  required_in_types: [briefing, consolidation]
  remediation:
    first_fix: "..."
    detail: |
      ...
```

Drop the old `required_in:` field everywhere. Drop the `notes:` block on Chrome that explained the now-irrelevant 2026-04-25 promotion (replace with a comment noting the type-based contract).

- [ ] **Step 3: Update unit tests**

In `engine/tests/unit/test_connectors_yaml.py`, replace every assertion on `required_in` / `required_in_mode` with the type-based equivalent.

Replace:

```python
def test_required_in_all_means_every_mode_is_required():
    reg = load_registry()
    slack = reg["mcp:claude_ai_Slack"]
    assert slack.required_in_mode("morning-briefing")
    assert slack.required_in_mode("manual")
```

With:

```python
def test_slack_is_required_for_briefing_and_consolidation_types():
    reg = load_registry()
    slack = reg["mcp:claude_ai_Slack"]
    assert slack.required_in_type(SlotType.BRIEFING)
    assert slack.required_in_type(SlotType.CONSOLIDATION)
    assert not slack.required_in_type(SlotType.RESEARCH)
```

Update other relevant tests similarly. Run:

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_connectors_yaml.py -v
```

All should pass after the rewrite.

- [ ] **Step 4: Update `connector_health_report.py`**

Find the chronic-skip rule's `mode_required` check (around line 280 in the current Plan 4 implementation). It looks like:

```python
required_modes = REQUIRED_IN.get(c, set())   # legacy
mode_required = "all" in required_modes or current_mode in required_modes
```

Update to:

```python
# Plan 5: query connector by SLOT TYPE, not slot key.
slot_type = current_slot_type(current_mode, sched)   # mode → slot_type lookup
mode_required = registry[c].required_in_type(slot_type)
```

Add a small helper `current_slot_type(mode: str, sched: Schedule) -> SlotType` that looks up the current slot key and returns its type, defaulting to `SlotType.MANUAL` if the mode string isn't a recognized slot key.

- [ ] **Step 5: Add a rule-level test for required_in_types**

Create `engine/tests/unit/test_scripts_connector_health_required_in_types.py`:

```python
"""Verify the chronic-skip rule keys on slot type, not slot key."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from scout.scripts.connector_health_report import compute_critical_alerts


def _make_call(ts, sid, mode, connector, error=False, err=""):
    rec = {"ts": ts, "session_id": sid, "mode": mode, "tool": "Bash",
           "connector": connector, "error": error}
    if err:
        rec["err"] = err
    return rec


def test_chronic_skip_alert_fires_only_when_slot_type_requires_connector(tmp_path):
    """gh CLI dark in 3 weekday-briefing-mode runs (which require gh) → CRITICAL.
    Same gh CLI dark in 3 weekend-research-mode runs (which don't require gh) → no alert.
    """
    # Implementation depends on the test contract for compute_critical_alerts.
    # Build two test datasets:
    #   A: 3 morning-briefing sessions, each with 0 gh calls, 3+ Slack calls.
    #   B: 3 research sessions, each with 0 gh calls, 3+ Slack calls.
    # Assert: A produces a CRITICAL alert for github; B does not.
    # ... (full fixture construction inline)
```

(Fixture construction follows the pattern from `test_scripts_connector_health.py` from Plan 4 — `_seed_session` helper, etc. Reuse those if possible.)

- [ ] **Step 6: Regenerate snapshot, run tests, lint, commit**

```bash
cd plugin/engine
../.venv/bin/scoutctl connectors snapshot                     # regenerates engine/scout/connectors.snapshot.json + scout-app fixture
../.venv/bin/pytest tests/ -q
../.venv/bin/ruff check scout tests
../.venv/bin/ruff format --check scout tests
../.venv/bin/mypy scout
```

All clean.

```bash
cd plugin
git add engine/scout/connectors.py engine/scout/connectors.yaml \
        engine/scout/connectors.snapshot.json \
        engine/scout/scripts/connector_health_report.py \
        engine/tests/unit/test_connectors_yaml.py \
        engine/tests/unit/test_scripts_connector_health_required_in_types.py
git commit -m "feat(engine): connectors.yaml gains required_in_types (slot-type vocabulary); connector_health_report keys on type"
```

---

## Task 7: One-shot migration tools (`tools/migrate-mode-names.py` + `tools/regenerate-connector-health.py`)

**Files:**
- Create: `tools/migrate-mode-names.py`
- Create: `tools/regenerate-connector-health.py`
- Create: `engine/tests/unit/test_migrate_mode_names.py`
- Create: `engine/tests/fixtures/connector-calls-pre-rename.jsonl`

**What this builds:** Big-bang rename for historical JSONL. Walks the user's vault `.scout-logs/connector-calls-*.jsonl` and `session-tokens.jsonl`, rewrites the `mode` / `scout_mode` field per the rename map, backs up originals to `.scout-logs/.pre-plan-5-backup/`. Idempotent. Then regenerates `connector-health.md` from the renamed logs.

The rename map is defined in one place (`MODE_RENAME_MAP` in `migrate-mode-names.py`) and consumed by both tools.

- [ ] **Step 1: Write a test fixture with old mode names**

Create `engine/tests/fixtures/connector-calls-pre-rename.jsonl`:

```json
{"ts":"2026-04-30T15:03:01Z","session_id":"a1","mode":"consolidation-11am","tool":"Bash","connector":"github","error":false}
{"ts":"2026-04-30T17:03:02Z","session_id":"a2","mode":"consolidation-1pm","tool":"Bash","connector":"github","error":false}
{"ts":"2026-04-30T21:03:03Z","session_id":"a3","mode":"consolidation-5pm","tool":"Bash","connector":"github","error":false}
{"ts":"2026-04-30T23:00:04Z","session_id":"a4","mode":"consolidation-7pm","tool":"Bash","connector":"github","error":false}
{"ts":"2026-04-30T12:00:05Z","session_id":"a5","mode":"morning-briefing","tool":"Bash","connector":"github","error":false}
```

- [ ] **Step 2: Write failing tests**

Create `engine/tests/unit/test_migrate_mode_names.py`:

```python
"""Unit tests for tools/migrate-mode-names.py."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

# Tools directory is outside engine package; add to sys.path for import.
TOOLS_DIR = Path(__file__).parent.parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))
from migrate_mode_names import MODE_RENAME_MAP, migrate_jsonl_file, migrate_data_dir  # noqa: E402


FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_mode_rename_map_covers_all_old_names():
    expected = {
        "consolidation-11am": "morning-consolidation",
        "consolidation-1pm": "midday-consolidation",
        "consolidation-5pm": "afternoon-consolidation",
        "consolidation-7pm": "evening-consolidation",
        "dreaming-nightly-10pm": "dreaming-nightly",
        "dreaming-weekend-6am": "dreaming-weekend-morning",
        "dreaming-weekend-7am": "dreaming-weekend-morning",
        # Unchanged: morning-briefing, weekend-briefing, manual.
    }
    for old, new in expected.items():
        assert MODE_RENAME_MAP[old] == new


def test_migrate_jsonl_rewrites_mode_field(tmp_path):
    src = tmp_path / "connector-calls-2026-04-30.jsonl"
    shutil.copy(FIXTURES / "connector-calls-pre-rename.jsonl", src)
    n_changed = migrate_jsonl_file(src, mode_field="mode")
    assert n_changed == 4                                  # 4 of 5 lines had old names
    rows = [json.loads(l) for l in src.read_text().splitlines()]
    modes = [r["mode"] for r in rows]
    assert "consolidation-11am" not in modes
    assert "morning-consolidation" in modes
    assert "morning-briefing" in modes                     # unchanged


def test_migrate_jsonl_is_idempotent(tmp_path):
    src = tmp_path / "connector-calls-2026-04-30.jsonl"
    shutil.copy(FIXTURES / "connector-calls-pre-rename.jsonl", src)
    migrate_jsonl_file(src, mode_field="mode")
    n_changed_second_pass = migrate_jsonl_file(src, mode_field="mode")
    assert n_changed_second_pass == 0


def test_migrate_data_dir_creates_backup(tmp_path):
    log_dir = tmp_path / ".scout-logs"
    log_dir.mkdir()
    shutil.copy(
        FIXTURES / "connector-calls-pre-rename.jsonl",
        log_dir / "connector-calls-2026-04-30.jsonl",
    )
    migrate_data_dir(tmp_path)
    backup = log_dir / ".pre-plan-5-backup" / "connector-calls-2026-04-30.jsonl"
    assert backup.exists()
    # Backup contains the OLD names.
    backup_rows = [json.loads(l) for l in backup.read_text().splitlines()]
    assert any(r["mode"] == "consolidation-11am" for r in backup_rows)
```

- [ ] **Step 3: Run, confirm RED**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_migrate_mode_names.py -v
```

Expected: ImportError or 4 failures.

- [ ] **Step 4: Implement `tools/migrate-mode-names.py`**

```python
#!/usr/bin/env python3
"""tools/migrate-mode-names.py — one-shot Plan 5 migration.

Walks ~/Scout/.scout-logs/connector-calls-*.jsonl and session-tokens.jsonl,
rewrites the mode / scout_mode field per MODE_RENAME_MAP. Backs up originals
to .scout-logs/.pre-plan-5-backup/. Idempotent — re-runnable.

Usage:
    python3 tools/migrate-mode-names.py [--data-dir ~/Scout]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


MODE_RENAME_MAP: dict[str, str] = {
    "consolidation-11am": "morning-consolidation",
    "consolidation-1pm": "midday-consolidation",
    "consolidation-5pm": "afternoon-consolidation",
    "consolidation-7pm": "evening-consolidation",
    "dreaming-nightly-10pm": "dreaming-nightly",
    "dreaming-weekend-6am": "dreaming-weekend-morning",
    "dreaming-weekend-7am": "dreaming-weekend-morning",
    # morning-briefing, weekend-briefing, manual unchanged.
}


def migrate_jsonl_file(path: Path, *, mode_field: str = "mode") -> int:
    """Rewrite the given JSONL file in place. Returns count of lines changed."""
    n_changed = 0
    new_lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            new_lines.append(raw_line)
            continue
        try:
            rec = json.loads(raw_line)
        except json.JSONDecodeError:
            new_lines.append(raw_line)                       # leave malformed lines alone
            continue
        old = rec.get(mode_field)
        if old in MODE_RENAME_MAP:
            rec[mode_field] = MODE_RENAME_MAP[old]
            new_lines.append(json.dumps(rec, separators=(",", ":")))
            n_changed += 1
        else:
            new_lines.append(raw_line)
    path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""))
    return n_changed


def migrate_data_dir(data_dir: Path) -> dict[str, int]:
    """Migrate all JSONL files under data_dir/.scout-logs/. Returns per-file change counts."""
    log_dir = data_dir / ".scout-logs"
    backup_dir = log_dir / ".pre-plan-5-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    changes: dict[str, int] = {}

    for jsonl in sorted(log_dir.glob("connector-calls-*.jsonl")):
        backup_target = backup_dir / jsonl.name
        if not backup_target.exists():
            shutil.copy2(jsonl, backup_target)
        changes[jsonl.name] = migrate_jsonl_file(jsonl, mode_field="mode")

    session_tokens = log_dir / "session-tokens.jsonl"
    if session_tokens.exists():
        backup_target = backup_dir / session_tokens.name
        if not backup_target.exists():
            shutil.copy2(session_tokens, backup_target)
        changes[session_tokens.name] = migrate_jsonl_file(session_tokens, mode_field="scout_mode")

    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path.home() / "Scout",
        help="Path to the Scout data dir (default: ~/Scout)",
    )
    args = parser.parse_args(argv)

    if not args.data_dir.exists():
        print(f"data dir not found: {args.data_dir}", file=sys.stderr)
        return 1

    changes = migrate_data_dir(args.data_dir)
    total = sum(changes.values())
    print(f"migrated {total} rows across {len(changes)} files")
    for name, n in sorted(changes.items()):
        print(f"  {name}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Implement `tools/regenerate-connector-health.py`**

```python
#!/usr/bin/env python3
"""tools/regenerate-connector-health.py — one-shot Plan 5 doc regen.

After running migrate-mode-names.py on the JSONL logs, regenerate
~/Scout/knowledge-base/connector-health.md so the matrix headers and
alerting rollup match the new mode names.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path.home() / "Scout",
        help="Path to the Scout data dir (default: ~/Scout)",
    )
    args = parser.parse_args(argv)

    # Defer to scoutctl connector-health-report, which already does the
    # right thing: load registry, roll up logs, render the matrix.
    import os, subprocess
    env = os.environ.copy()
    env["SCOUT_DATA_DIR"] = str(args.data_dir)
    result = subprocess.run(
        [str(Path.home() / "scout-plugin" / ".venv" / "bin" / "scoutctl"),
         "connector-health-report"],
        env=env,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run tests, lint, commit**

```bash
cd plugin/engine
../.venv/bin/pytest tests/unit/test_migrate_mode_names.py -v
../.venv/bin/ruff check scout tests
../.venv/bin/ruff format --check scout tests
../.venv/bin/mypy scout
../.venv/bin/pytest tests/ -q
```

All green; ~352 passed, 9 skipped.

```bash
cd plugin
chmod +x tools/migrate-mode-names.py tools/regenerate-connector-health.py
git add tools/migrate-mode-names.py tools/regenerate-connector-health.py \
        engine/tests/unit/test_migrate_mode_names.py \
        engine/tests/fixtures/connector-calls-pre-rename.jsonl
git commit -m "feat(tools): one-shot migrate-mode-names.py + regenerate-connector-health.py for Plan 5"
```

- [ ] **Step 7: Run the migration on the live vault**

```bash
python3 plugin/tools/migrate-mode-names.py --data-dir ~/Scout
python3 plugin/tools/regenerate-connector-health.py --data-dir ~/Scout
```

Verify:

```bash
grep -c "consolidation-11am" ~/Scout/.scout-logs/connector-calls-*.jsonl 2>&1 | head     # should be 0
grep -c "morning-consolidation" ~/Scout/.scout-logs/connector-calls-*.jsonl 2>&1 | head  # should be > 0
head -20 ~/Scout/knowledge-base/connector-health.md                                       # matrix headers should use new names
```

Commit the regenerated KB doc to the vault:

```bash
cd ~/Scout
git add knowledge-base/connector-health.md
git commit -m "scout: regenerate connector-health.md after Plan 5 mode rename"
```

---

## Task 8: `scoutctl schedule snapshot` + cross-repo sync (Plan 4 pattern)

**Files:**
- Create: `engine/scout/scripts/schedule_snapshot.py`
- Create: `engine/scout/schedule.snapshot.json` (canonical snapshot, regenerated by the command)
- Modify: `engine/scout/cli.py` (registers `schedule snapshot [--target] [--check]`)
- Create: `engine/tests/unit/test_schedule_snapshot.py`
- Modify: `.github/workflows/test.yml` (CI drift check for schedule snapshot)
- Create: `apps/macos/ScoutTests/Fixtures/schedule.snapshot.json` (test target fixture)

**What this builds:** Mirrors Plan 4 Task 8 pattern. `scoutctl schedule snapshot` writes a JSON projection of `schedule.yaml` (slot keys + types + fires_at_local + on_miss) to the canonical engine path AND, by default, dual-writes to `apps/macos/ScoutTests/Fixtures/schedule.snapshot.json`. CI drift-checks the canonical against the seeded vault default. `--check` mode strips `generated_from` SHA before comparing (so committed snapshots don't always look stale).

(Implementation closely follows `engine/scout/scripts/connectors_snapshot.py` from Plan 4 — refer to that file for the exact pattern. Tests follow the same shape as `test_scripts_connectors_snapshot.py`.)

- [ ] **Step 1–8: Mirror Plan 4 Task 8 implementation**

Refer to:
- `engine/scout/scripts/connectors_snapshot.py` (existing) for the `build_snapshot` / `serialize` / `write_snapshot` / `check_snapshot` shape.
- `engine/tests/unit/test_scripts_connectors_snapshot.py` for the 16-test pattern.
- The existing `.github/workflows/test.yml` for the CI drift-check step.

The snapshot data shape:

```json
{
  "schema_version": 1,
  "generated_from": "scout-plugin@<short-sha>",
  "slots": [
    {
      "key": "morning-briefing",
      "type": "briefing",
      "runner": "run-scout.sh",
      "fires_at_local": "08:00",
      "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"],
      "on_miss": "fire"
    },
    ...
  ]
}
```

Default `--target` is `engine/scout/schedule.snapshot.json` (canonical). The `--also-write-app-fixture` flag (default ON) dual-writes to `apps/macos/ScoutTests/Fixtures/schedule.snapshot.json`; skip with a warning if the path doesn't exist.

After implementing, run:

```bash
plugin/.venv/bin/scoutctl schedule snapshot
diff <(jq -S . plugin/engine/scout/schedule.snapshot.json) \
     <(jq -S . apps/macos/ScoutTests/Fixtures/schedule.snapshot.json)
# Should be empty.
```

Add the CI step:

```yaml
# In .github/workflows/test.yml after the connectors-snapshot drift check:
- name: Verify schedule.snapshot.json drift
  run: |
    .venv/bin/python -m scout.scripts.schedule_snapshot --check --target scout/schedule.snapshot.json
```

Commit:

```bash
cd plugin
git add engine/scout/scripts/schedule_snapshot.py engine/scout/cli.py \
        engine/scout/schedule.snapshot.json \
        engine/tests/unit/test_schedule_snapshot.py \
        .github/workflows/test.yml
git commit -m "feat(engine): scoutctl schedule snapshot + canonical engine/scout/schedule.snapshot.json + CI drift check"
```

---

## Task 9: Scout-app — `ScheduleService` + `PowerStateService` + `RunnerService` deletion + UI rewiring

**Files:**
- Create: `Scout/Services/ScheduleService.swift`
- Create: `Scout/Services/PowerStateService.swift`
- Create: `Scout/ControlCenter/PowerStateBanner.swift`
- Modify: `Scout/Models/RunType.swift` (rename cases; add `init?(slotKey:)`)
- Modify: `Scout/ControlCenter/UpcomingStripView.swift` (consume ScheduleService)
- Modify: `Scout/ControlCenter/NowStripView.swift` (Run-now button calls scoutctl schedule fire-now)
- Modify: `Scout/Shell/AppState.swift` (wire ScheduleService + PowerStateService)
- Modify: `Scout/Shell/MenuBarExtraContent.swift` (delete RunnerService refs; add Install wake-schedule item)
- Delete: `Scout/Services/RunnerService.swift`
- Delete: `Scout/Services/LaunchdScheduleService.swift`
- Create: `ScoutTests/Services/ScheduleServiceTests.swift`
- Create: `ScoutTests/Services/PowerStateServiceTests.swift`
- Modify: `ScoutTests/Models/RunTypeTests.swift` (or create if doesn't exist)
- Create: `ScoutTests/Fixtures/schedule.snapshot.json` (Task 8 already created this; verify present)

**What this builds:** Scout-app stops being a dispatcher. Becomes a UI mirror that consults `scoutctl schedule list-upcoming --json` (a new sub-command added in this task) every 60s and renders the result.

- [ ] **Step 1: Add `scoutctl schedule list-upcoming` sub-command (engine, scout-plugin side)**

Wire a new Typer command into `_register_schedule()`:

```python
    @schedule_app.command("list-upcoming")
    def cli_schedule_list_upcoming(
        window_hours: int = typer.Option(24, "--window", help="Hours into the future"),
        as_json: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        """List upcoming slot fires within the given window."""
        # ... walk schedule + tracker, compute next fires per slot, return JSON.
```

(Implementation closely mirrors `LaunchdScheduleService.nextFires` from scout-app — but in Python, against the schedule.yaml + tracker.)

Add a small test in `engine/tests/unit/test_cli_schedule_subapp.py` that runs `list-upcoming --window 24h --json` and asserts JSON shape includes `[{"slot_key": ..., "slot_type": ..., "scheduled_at_local": ..., "scheduled_at_utc": ...}, ...]`.

Commit on scout-plugin side:

```bash
cd plugin
git add engine/scout/cli.py engine/scout/scripts/schedule_tick.py \
        engine/tests/unit/test_cli_schedule_subapp.py
git commit -m "feat(engine): scoutctl schedule list-upcoming for scout-app consumption"
```

- [ ] **Step 2: Branch scout-app + write the Swift services**

Switch to scout-app:

```bash
cd apps/macos
git checkout main
git pull --ff-only
git checkout -b plan-5-scout-app
```

Create `Scout/Services/ScheduleService.swift`:

```swift
import Foundation
import Combine

@MainActor
final class ScheduleService: ObservableObject {
    @Published private(set) var upcoming: [UpcomingRun] = []

    private let runner: any ProcessRunner
    private let scoutctl: URL
    private var pollTimer: Timer?

    init(scoutctl: URL, runner: any ProcessRunner) {
        self.scoutctl = scoutctl
        self.runner = runner
    }

    func start() {
        Task { await self.refresh() }
        pollTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }

    func stop() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    private func refresh() async {
        do {
            let output = try await runner.run(
                executable: scoutctl,
                arguments: ["schedule", "list-upcoming", "--window", "24", "--json"],
                environment: [:],
                workingDirectory: nil
            )
            let data = output.stdout.data(using: .utf8) ?? Data()
            let parsed = try JSONDecoder().decode([RawUpcomingRun].self, from: data)
            self.upcoming = parsed.compactMap { UpcomingRun(from: $0) }
        } catch {
            // Swallow; next tick will retry. Surfacing this as a UI banner is a Plan 6+ task.
            return
        }
    }

    private struct RawUpcomingRun: Decodable {
        let slot_key: String
        let slot_type: String
        let scheduled_at_local: String
        let scheduled_at_utc: String
    }
}
```

Create `Scout/Services/PowerStateService.swift`:

```swift
import Foundation
import Combine

enum PowerState: Equatable {
    case onAC
    case onBattery(level: Double)
    case unknown
}

@MainActor
final class PowerStateService: ObservableObject {
    @Published private(set) var state: PowerState = .unknown

    private let runner: any ProcessRunner
    private var pollTimer: Timer?

    init(runner: any ProcessRunner) {
        self.runner = runner
    }

    func start() {
        Task { await self.refresh() }
        pollTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { await self?.refresh() }
        }
    }

    func stop() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    private func refresh() async {
        do {
            let output = try await runner.run(
                executable: URL(fileURLWithPath: "/usr/bin/pmset"),
                arguments: ["-g", "batt"],
                environment: [:],
                workingDirectory: nil
            )
            self.state = parsePmsetOutput(output.stdout) ?? .unknown
        } catch {
            self.state = .unknown
        }
    }

    nonisolated static func parsePmsetOutput(_ stdout: String) -> PowerState? {
        // Sample lines:
        //   Now drawing from 'AC Power'
        //   Now drawing from 'Battery Power'
        //    -InternalBattery-0 (id=...)    73%; discharging; 4:12 remaining present: true
        if stdout.contains("AC Power") {
            return .onAC
        }
        if stdout.contains("Battery Power") {
            // Extract percentage like "73%".
            let range = stdout.range(of: #"(\d+)%"#, options: .regularExpression)
            if let r = range, let pctStr = Int(stdout[r].dropLast()) {
                return .onBattery(level: Double(pctStr) / 100.0)
            }
            return .onBattery(level: 0)
        }
        return nil
    }
}
```

Create `Scout/ControlCenter/PowerStateBanner.swift`:

```swift
import SwiftUI

struct PowerStateBanner: View {
    @ObservedObject var service: PowerStateService

    var body: some View {
        if case .onBattery = service.state {
            HStack {
                Image(systemName: "bolt.slash.fill")
                Text("On battery — runs may be missed if the lid closes. Plug in for guaranteed firing.")
                    .font(.callout)
            }
            .padding(8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.yellow.opacity(0.15))
            .foregroundColor(.yellow)
            .cornerRadius(6)
        }
    }
}
```

- [ ] **Step 3: Update `RunType.swift` to add `init?(slotKey:)`**

```swift
extension RunType {
    /// Map a slot key (from scoutctl schedule list-upcoming) to a RunType.
    /// Returns nil for unknown keys.
    init?(slotKey: String) {
        switch slotKey {
        case "morning-briefing": self = .morningBriefing
        case "weekend-briefing": self = .weekendBriefing
        case "morning-consolidation", "midday-consolidation",
             "afternoon-consolidation", "evening-consolidation": self = .consolidation
        case "dreaming-evening", "dreaming-nightly", "dreaming-weekend-morning": self = .dreaming
        case "research": self = .research
        default: return nil
        }
    }
}
```

(Adjust enum cases to whatever's currently defined in `RunType.swift`.)

- [ ] **Step 4: Delete `RunnerService.swift` and `LaunchdScheduleService.swift`**

Find every reference in the codebase, replace with calls into `ScheduleService` (for upcoming runs) or shell-out to `scoutctl schedule fire-now <slot-key>` (for "Run now" buttons). Delete the two files.

- [ ] **Step 5: Wire into `AppState.swift` + UI**

In `AppState.swift`, replace the `runnerService` / `LaunchdScheduleService` with `ScheduleService` + `PowerStateService`. In `UpcomingStripView.swift`, consume `state.scheduleService.upcoming`. In `NowStripView.swift`, replace any `Run-now` button action with a shell-out to `scoutctl schedule fire-now <slot-key>`.

In `MenuBarExtraContent.swift`, delete the `runnerService` references and add a "Install wake-schedule…" button that runs `scoutctl schedule install-wake-schedule` interactively.

Add `PowerStateBanner` to the top of the schedule strip in `ControlCenterView.swift` (above `UpcomingStripView`).

- [ ] **Step 6: Add Swift tests**

Create `ScoutTests/Services/ScheduleServiceTests.swift` and `ScoutTests/Services/PowerStateServiceTests.swift`. Tests:
- `ScheduleService.refresh` parses JSON output correctly.
- `PowerStateService.parsePmsetOutput` returns `.onAC` / `.onBattery(level:)` / `.unknown` for canonical inputs.
- `RunType(slotKey:)` returns expected mappings.

- [ ] **Step 7: Build + test the app**

```bash
cd apps/macos
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -20
xcodebuild test -project Scout.xcodeproj -scheme Scout 2>&1 | tail -20
```

Both should succeed.

- [ ] **Step 8: Commit on scout-app branch**

```bash
cd apps/macos
git add Scout/Services/ScheduleService.swift Scout/Services/PowerStateService.swift \
        Scout/ControlCenter/PowerStateBanner.swift Scout/Models/RunType.swift \
        Scout/ControlCenter/UpcomingStripView.swift Scout/ControlCenter/NowStripView.swift \
        Scout/Shell/AppState.swift Scout/Shell/MenuBarExtraContent.swift \
        ScoutTests/Services/ScheduleServiceTests.swift \
        ScoutTests/Services/PowerStateServiceTests.swift
git rm Scout/Services/RunnerService.swift Scout/Services/LaunchdScheduleService.swift
git commit -m "feat(app): ScheduleService + PowerStateService; remove in-app dispatcher (Plan 5)"
```

---

## Task 10: SKILL.md / DREAMING.md / RESEARCH.md / CLAUDE.md mode-rename updates

**Files (vault):**
- Modify: `~/Scout/SKILL.md`
- Modify: `~/Scout/DREAMING.md`
- Modify: `~/Scout/RESEARCH.md`
- Modify: `~/Scout/CLAUDE.md`

**What this builds:** Search-and-replace pass for old mode names. Keeps the rest of these files untouched (the personal-data scrub is Plan 8).

- [ ] **Step 1: Find all old-mode-name references**

```bash
grep -rn "consolidation-11am\|consolidation-1pm\|consolidation-5pm\|consolidation-7pm\|dreaming-nightly-10pm\|dreaming-weekend-6am\|dreaming-weekend-7am" ~/Scout/SKILL.md ~/Scout/DREAMING.md ~/Scout/RESEARCH.md ~/Scout/CLAUDE.md
```

- [ ] **Step 2: Apply the rename map per the table in design doc §8**

Edit each file. For each old name, replace with the new name. Do NOT touch any other content (no scrub-related edits).

- [ ] **Step 3: Verify zero old-name references remain**

```bash
grep -c "consolidation-11am\|consolidation-1pm\|consolidation-5pm\|consolidation-7pm\|dreaming-nightly-10pm\|dreaming-weekend-6am\|dreaming-weekend-7am" ~/Scout/SKILL.md ~/Scout/DREAMING.md ~/Scout/RESEARCH.md ~/Scout/CLAUDE.md
```

All counts should be 0.

- [ ] **Step 4: Commit in vault**

```bash
cd ~/Scout
git add SKILL.md DREAMING.md RESEARCH.md CLAUDE.md
git commit -m "scout: mode-name rename to Plan 5 semantic vocabulary (no other content edits)"
```

---

## Task 11: Old launchd plists uninstall

**What this does:** Uninstalls the 7 legacy plists from `~/Library/LaunchAgents/` (and their symlinks/files in `~/Scout/launchd/`). Keeps `com.scout.heartbeat.plist` (heartbeat redesign is Plan 4-supplement, not Plan 5).

- [ ] **Step 1: Bootout old jobs from launchd**

```bash
for label in com.scout.briefing com.scout.briefing-weekend com.scout.consolidation-7pm com.scout.dreaming com.scout.dreaming-nightly-10pm com.scout.dreaming-weekend-6am com.scout.dreaming-weekend-7am com.scout.research; do
    launchctl bootout gui/$UID/$label 2>/dev/null || true
done
launchctl list | grep com.scout    # heartbeat + the new schedule-tick should remain
```

- [ ] **Step 2: Remove the plist files**

```bash
rm -f ~/Library/LaunchAgents/com.scout.briefing.plist
rm -f ~/Library/LaunchAgents/com.scout.briefing-weekend.plist
rm -f ~/Library/LaunchAgents/com.scout.consolidation-7pm.plist
rm -f ~/Library/LaunchAgents/com.scout.dreaming.plist
rm -f ~/Library/LaunchAgents/com.scout.dreaming-nightly-10pm.plist
rm -f ~/Library/LaunchAgents/com.scout.dreaming-weekend-6am.plist
rm -f ~/Library/LaunchAgents/com.scout.dreaming-weekend-7am.plist
rm -f ~/Library/LaunchAgents/com.scout.research.plist
```

- [ ] **Step 3: Remove the master copies in vault `~/Scout/launchd/`**

```bash
cd ~/Scout
git rm launchd/com.scout.briefing-weekend.plist \
       launchd/com.scout.consolidation-7pm.plist \
       launchd/com.scout.dreaming-nightly-10pm.plist \
       launchd/com.scout.dreaming-weekend-6am.plist \
       launchd/com.scout.dreaming-weekend-7am.plist \
       launchd/com.scout.research.plist
git commit -m "scout: remove legacy per-slot launchd plists (Plan 5 schedule-tick.plist takes their role)"
```

- [ ] **Step 4: Verify only com.scout.heartbeat + com.scout.schedule-tick are loaded**

```bash
launchctl list | grep com.scout
# Expected: com.scout.heartbeat (interval 30 min) + com.scout.schedule-tick (interval 5 min)
ls ~/Library/LaunchAgents/ | grep com.scout
# Expected: com.scout.heartbeat.plist + com.scout.schedule-tick.plist
```

---

## Task 12: Manifest flag flip + final lint + verify suite

**Files:**
- Modify: `engine/scout/manifest.py` (add `schedule_v2: True`)
- Modify: `engine/tests/unit/test_manifest.py`

- [ ] **Step 1: Flip manifest flag**

In `engine/scout/manifest.py`, add to the `features` dict in `build_manifest()`:

```python
"schedule_v2": True,    # Plan 5
```

In `engine/tests/unit/test_manifest.py`, add the corresponding assertion.

- [ ] **Step 2: Run full suite + lint sweep**

```bash
cd plugin/engine
../.venv/bin/pytest tests/ -q
../.venv/bin/ruff check scout tests
../.venv/bin/ruff format --check scout tests
../.venv/bin/mypy scout
bats engine/tests/parity/                                    # all parity tests still skip cleanly (Plan 4 ones) or pass (schedule tick)
```

All clean.

- [ ] **Step 3: Smoke the live system**

```bash
launchctl list | grep com.scout                              # 2 jobs (heartbeat + schedule-tick)
plugin/.venv/bin/scoutctl schedule list              # 10 slots
plugin/.venv/bin/scoutctl schedule validate          # OK
plugin/.venv/bin/scoutctl manifest show 2>&1 | grep schedule_v2   # true

# Wait 5 minutes for the next tick, then:
ls -lat ~/Scout/.scout-logs/schedule-events-*.jsonl | head -1
tail -5 $(ls -t ~/Scout/.scout-logs/schedule-events-*.jsonl | head -1)
```

You should see at least one `schedule.tick.completed` event since the install.

- [ ] **Step 4: Commit final manifest flip**

```bash
cd plugin
git add engine/scout/manifest.py engine/tests/unit/test_manifest.py
git commit -m "feat(engine): Plan 5 manifest flag flip — schedule_v2: true"
```

---

## Task 13: Verify, push, open PR

- [ ] **Step 1: Final scout-plugin sanity sweep**

```bash
cd plugin
git log main..HEAD --oneline | wc -l                       # ~13–15 commits
cd engine && ../.venv/bin/pytest tests/ -q && cd ..
.venv/bin/scoutctl schedule list
launchctl list | grep com.scout
```

- [ ] **Step 2: Push scout-plugin branch**

```bash
cd plugin
git push -u origin plan-5-schedule-v2
```

- [ ] **Step 3: Open scout-plugin PR**

```bash
cd plugin
gh pr create --title "Plan 5: Schedule v2 + mode rename" --body "$(cat <<'EOF'
## Summary

Implements Schedule v2 — the next subsystem on the v0.4 unification arc. Same pattern as Plan 4 (connectors): vault YAML as source of truth, engine-canonical CLI + dispatcher, scout-app as a read-only UI mirror.

- **`scout.schedule`** module — vault `schedule.yaml` schema + typed loader, slot keys (user-chosen) vs slot types (fixed plugin vocabulary).
- **`scoutctl schedule {list,show,validate,init,reload,tick,fire-now,install-plist,install-wake-schedule,snapshot,list-upcoming}`** sub-app.
- **5-min `com.scout.schedule-tick.plist`** replaces the 7 legacy per-slot plists. Single dispatcher, idempotent, network-aware, single-fire-per-tick by priority.
- **Mode rename:** `consolidation-11am/1pm/5pm/7pm` → `morning/midday/afternoon/evening-consolidation`; `dreaming-nightly-10pm` → `dreaming-nightly`; `dreaming-weekend-{6am,7am}` → `dreaming-weekend-morning`. Connectors.yaml's `required_in` migrates to `required_in_types` (slot-type vocabulary).
- **One-shot migration tools** at `tools/migrate-mode-names.py` + `tools/regenerate-connector-health.py`.
- **CI drift check** for both connectors.snapshot.json (existing, Plan 4) and schedule.snapshot.json (new).

## Spec references

- Plan 5 design: `docs/superpowers/specs/2026-05-04-schedule-v2-design.md`
- v0.4 unification spec amendments: §6 Layout adds schedule.yaml; new §11 sub-section "Schedule definition lives in the vault"
- v0.5+ event-architecture spec amendments: `(mode, tier)` → `(slot_type, tier)`; new "Schedule events" sub-section adding `slot.fired`, `slot.skipped`, `slot.fire_failed`, `schedule.tick.completed`

## Test plan

- [x] Full pytest suite green (~360 passed + 9 skipped after Plan 5 vs 301+9 baseline)
- [x] `ruff check`, `ruff format --check`, `mypy scout` clean
- [x] `bats engine/tests/parity/` — schedule-tick parity test passes; legacy Plan 4 parity tests skip cleanly
- [x] `scoutctl schedule list` returns the 10 slots; `scoutctl schedule validate` is OK
- [x] `launchctl list | grep com.scout` shows exactly 2 jobs (heartbeat, schedule-tick)
- [x] One-shot mode-rename migration applied to live vault; `grep` confirms 0 old-name occurrences
- [ ] (post-merge) Manual smoke: next scheduled morning briefing fires from the new dispatcher
- [ ] (post-merge) Manual smoke: scout-app schedule strip renders 10 upcoming slots from `scoutctl schedule list-upcoming`
- [ ] (post-merge) Manual smoke: power-state banner appears when on battery

## Follow-up tasks

- Plan 4-supplement (now 6 ports + redesigned heartbeat per design doc §10): port the remaining bash scripts + redesign heartbeat for opportunistic-only (the scheduled-dispatch role landed here).
- Plan 6: scout-app refactor (ScoutEnvironment + EngineClient + first-run wizard) — the schedule pieces landed here, freeing Plan 6 to focus on environment / wizard.
- Plan 7: KB ontology cache (renumbered from old Plan 5).
- Plan 8: personal-data scrub (renumbered from old Plan 7).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Push scout-app branch + open separate PR**

```bash
cd apps/macos
git push -u origin plan-5-scout-app
gh pr create --title "Plan 5 (app): ScheduleService + PowerStateService; remove in-app dispatcher" --body "..."
```

(Body cross-references the scout-plugin PR + the spec doc.)

- [ ] **Step 5: Done**

Both PRs open. Plan 5 ready to merge after CI green and review.

---

## Self-review checklist

After completing all 13 tasks, verify:

1. **Spec coverage** — every section of `2026-05-04-schedule-v2-design.md` is implemented:
   - §3 (slot semantics) → Task 1
   - §4 (dispatcher) → Task 3
   - §5 (sleep handling) → Tasks 5, 9 (banner)
   - §6 (scout-app changes) → Task 9
   - §7 (snapshot sync) → Task 8
   - §8 (mode rename + migration) → Tasks 6, 7, 10
   - §9 (event taxonomy) → Task 3 emits all 4 kinds
   - §10 (heartbeat split) → out of scope; tracked for Plan 4-supplement
   - §11 (testing) → unit + integration + parity + Swift tests across tasks
   - §12 (spec amendments) → already shipped on the spec branch
2. **Type consistency** — `Slot`, `SlotType`, `OnMissPolicy`, `Decision`, `SlotCandidate` names used consistently across Tasks 1, 3, 8, 9.
3. **No placeholders** — every `tools/migrate-mode-names.py`, every test fixture, every CLI command, every plist key has concrete content.
4. **Commits** — each task ends in a commit; ~13 commits total in scout-plugin, ~2 in scout-app, ~3 in vault.

If any gap surfaces, add the missing task before opening PRs.
