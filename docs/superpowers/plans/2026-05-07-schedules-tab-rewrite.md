# Schedules Tab Rewrite Implementation Plan (Plan 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the post-Plan-5 placeholder Schedules tab with a real `~/Scout/.scout-state/schedule.yaml` editor — full CRUD, atomic saves with mtime stale-check, header-comment preservation, and a forward-compat `runtime` field reserved for Plan 7.

**Architecture:** UI-only rebuild on top of Plan 5's engine. Two PRs land in sequence: a tiny additive engine PR (`Slot.runtime` field + dispatcher guard + `scoutctl schedule validate --target` flag), then the scout-app PR (`Slot` Swift model, `ScheduleEditService`, single-column inline-expand view tree, deletion of legacy `ScheduleEditorService` and friends). The engine remains canonical; Swift never parses YAML for business logic.

**Tech Stack:** Python 3.12 + Typer + PyYAML (engine); Swift 5.9 + SwiftUI + `@MainActor` services + Yams (scout-app); pytest (engine tests); XCTest + a hand-rolled `MockProcessRunner` queue stub (scout-app tests).

**Reference spec:** `docs/superpowers/specs/2026-05-07-schedules-tab-rewrite-design.md` (commit `1cbfadc`).

---

## File Structure

### scout-plugin (engine PR — `plan-6-engine`)

| File | Responsibility | Action |
|---|---|---|
| `engine/scout/schedule.py` | `Slot` dataclass, `SlotRuntime` enum, `_build_slot` parsing | Modify |
| `engine/scout/scripts/schedule_tick.py` | Dispatcher; raise `ConfigError` on `runtime: remote` | Modify |
| `engine/scout/cli.py::cli_schedule_validate` | `--target` path arg | Modify |
| `engine/tests/unit/test_schedule_loader.py` | New tests for `runtime` parsing + defaults | Modify |
| `engine/tests/unit/test_schedule_tick.py` | New test for dispatcher guard | Modify |
| `engine/tests/unit/test_cli_schedule_subapp.py` | New tests for `validate --target` | Modify |

### scout-app (UI PR — `plan-6-schedules-tab`)

| File | Responsibility | Action |
|---|---|---|
| `Scout/Models/Slot.swift` | Swift mirror of engine `Slot`; `Codable` from `list --json` | Create |
| `Scout/Services/ScheduleEditService.swift` | `@MainActor` service: load, save (mtime + atomic + header), delete | Create |
| `Scout/Services/StaleScheduleError.swift` | Typed error for the mtime-stale path | Create |
| `Scout/Schedules/SlotSummaryRow.swift` | Collapsed row display + chevron + Fire-now button | Create |
| `Scout/Schedules/SlotEditForm.swift` | Inline expanded edit form (Common + Advanced + actions) | Create |
| `Scout/Schedules/SlotRow.swift` | Container that switches between summary + form | Create |
| `Scout/Schedules/SchedulesView.swift` | Replace placeholder body; single-expansion controller + empty state + banner + toolbar | Modify |
| `Scout/Shell/AppState.swift` | Wire `ScheduleEditService`, drop `scheduleEditorService` | Modify |
| `Scout/Shell/SidebarView.swift` | Restore `.schedules` row | Modify |
| `Scout/Services/ScheduleEditorService.swift` | Delete (legacy) | Delete |
| `Scout/Schedules/ScheduleDetailView.swift` | Delete (legacy) | Delete |
| `Scout/Schedules/NewScheduleSheet.swift` | Delete (legacy) | Delete |
| `Scout/Models/Schedule.swift` | Delete (legacy launchd-plist Schedule type) | Delete |
| `ScoutTests/Models/SlotTests.swift` | Slot Codable round-trip tests | Create |
| `ScoutTests/Services/ScheduleEditServiceTests.swift` | Service tests (load + save + mtime + delete) | Create |
| `ScoutTests/Schedules/SlotEditFormTests.swift` | Form tests (live validation + Save enable + dialogs) | Create |
| `ScoutTests/Schedules/SchedulesViewTests.swift` | View tests (empty state + banner + new-slot flow) | Create |
| `ScoutTests/Integration/ScheduleEditE2ETest.swift` | Opt-in E2E against real vault | Create |
| `ScoutTests/Services/ScheduleEditorServiceTests.swift` | Delete (legacy tests) | Delete |

---

## Task 1: Engine — `Slot.runtime` enum + loader parsing

**Files:**
- Modify: `engine/scout/schedule.py`
- Test: `engine/tests/unit/test_schedule_loader.py`

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/unit/test_schedule_loader.py`:

```python
from scout.schedule import SlotRuntime, load_schedule
from scout.errors import ConfigError


def test_slot_runtime_defaults_to_local_when_absent(tmp_path):
    """Missing runtime field defaults to LOCAL — backward-compat for pre-Plan-6 vaults."""
    yaml_path = tmp_path / "schedule.yaml"
    yaml_path.write_text(
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
    sched = load_schedule(yaml_path)
    assert sched["morning-briefing"].runtime == SlotRuntime.LOCAL


def test_slot_runtime_parses_local_explicitly(tmp_path):
    yaml_path = tmp_path / "schedule.yaml"
    yaml_path.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  s:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
        "    runtime: local\n"
    )
    assert load_schedule(yaml_path)["s"].runtime == SlotRuntime.LOCAL


def test_slot_runtime_parses_remote(tmp_path):
    """remote is a valid value; the loader accepts it. The dispatcher guard
    rejects it at fire-time (Task 2)."""
    yaml_path = tmp_path / "schedule.yaml"
    yaml_path.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  s:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
        "    runtime: remote\n"
    )
    assert load_schedule(yaml_path)["s"].runtime == SlotRuntime.REMOTE


def test_slot_runtime_invalid_value_raises_config_error(tmp_path):
    yaml_path = tmp_path / "schedule.yaml"
    yaml_path.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  s:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
        "    runtime: cloud\n"
    )
    with pytest.raises(ConfigError, match="runtime"):
        load_schedule(yaml_path)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/jordanburger/scout-plugin/engine
/Users/jordanburger/scout-plugin/.venv/bin/pytest tests/unit/test_schedule_loader.py -v -k runtime
```
Expected: 4 failures with `ImportError: cannot import name 'SlotRuntime'` or `AttributeError`.

- [ ] **Step 3: Add `SlotRuntime` enum + field**

Edit `engine/scout/schedule.py`. Add the enum near the existing `SlotType` / `OnMissPolicy`:

```python
class SlotRuntime(enum.Enum):
    LOCAL = "local"
    REMOTE = "remote"  # Reserved for Plan 7. Loader accepts; dispatcher rejects.
```

Add the field to the `Slot` dataclass (after `tz`):

```python
@dataclass(frozen=True)
class Slot:
    # ... existing fields ...
    runtime: SlotRuntime = SlotRuntime.LOCAL
```

Update `_build_slot` to parse it. Find the existing function body and add (before the final `return Slot(...)`):

```python
runtime_raw = raw.get("runtime", "local")
try:
    runtime = SlotRuntime(runtime_raw)
except ValueError as e:
    raise ConfigError(
        f"slot {key!r}: runtime {runtime_raw!r} is not one of "
        f"{[r.value for r in SlotRuntime]}"
    ) from e
```

Then pass `runtime=runtime` into the `Slot(...)` constructor.

- [ ] **Step 4: Run tests to verify they pass**

```bash
/Users/jordanburger/scout-plugin/.venv/bin/pytest tests/unit/test_schedule_loader.py -v -k runtime
```
Expected: 4 passing. Then run the full schedule-loader suite to confirm no regression:
```bash
/Users/jordanburger/scout-plugin/.venv/bin/pytest tests/unit/test_schedule_loader.py -v
```
Expected: all green (the existing tests don't set `runtime`, so they exercise the default path).

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/jordanburger/scout-plugin
.venv/bin/ruff check engine/scout/schedule.py engine/tests/unit/test_schedule_loader.py
.venv/bin/ruff format --check engine/scout/schedule.py engine/tests/unit/test_schedule_loader.py
.venv/bin/mypy engine/scout/schedule.py
git add engine/scout/schedule.py engine/tests/unit/test_schedule_loader.py
git commit -m "feat(engine): Slot.runtime enum (LOCAL default, REMOTE reserved for Plan 7)"
```

---

## Task 2: Engine — Dispatcher guard for `runtime: remote`

**Files:**
- Modify: `engine/scout/scripts/schedule_tick.py:_spawn_runner`
- Test: `engine/tests/unit/test_schedule_tick.py`

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/unit/test_schedule_tick.py`:

```python
import pytest
from scout.errors import ConfigError
from scout.schedule import OnMissPolicy, Slot, SlotRuntime, SlotType
from scout.scripts.schedule_tick import _spawn_runner


def test_spawn_runner_rejects_remote_runtime(tmp_path):
    """Plan 7 forward-compat: dispatcher refuses to spawn `runtime: remote` slots
    until the routines API integration ships. Until then, save attempts in the
    Schedules tab UI render Remote as disabled, so this guard catches manual
    YAML edits that set runtime: remote."""
    slot = Slot(
        key="research",
        type=SlotType.RESEARCH,
        runner="run-research.sh",
        fires_at_local="14:00",
        weekdays=("Mon",),
        missed_window_hours=4,
        on_miss=OnMissPolicy.SKIP,
        cooldown_minutes=240,
        runtime=SlotRuntime.REMOTE,
    )
    with pytest.raises(ConfigError, match="runtime: remote.*Plan 7"):
        _spawn_runner(vault=tmp_path, slot_key="research", slot=slot)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/jordanburger/scout-plugin/engine
/Users/jordanburger/scout-plugin/.venv/bin/pytest tests/unit/test_schedule_tick.py::test_spawn_runner_rejects_remote_runtime -v
```
Expected: FAIL — likely `ConfigError` not raised; instead `_spawn_runner` calls subprocess (which may fail differently).

- [ ] **Step 3: Add the guard**

Edit `engine/scout/scripts/schedule_tick.py`. Find `_spawn_runner` and add at the very top (before any subprocess work):

```python
def _spawn_runner(vault: Path, slot_key: str, slot: Slot) -> int:
    if slot.runtime == SlotRuntime.REMOTE:
        raise ConfigError(
            f"slot {slot_key!r} has runtime: remote, which is reserved for Plan 7 "
            f"(remote routine integration). The dispatcher cannot fire remote slots "
            f"until that work lands. Edit ~/Scout/.scout-state/schedule.yaml and set "
            f"runtime: local, or delete the slot."
        )
    # ... existing body ...
```

Add the import at the top of the file if not already present:
```python
from scout.schedule import SlotRuntime
```

- [ ] **Step 4: Run tests**

```bash
/Users/jordanburger/scout-plugin/.venv/bin/pytest tests/unit/test_schedule_tick.py -v
```
Expected: the new test passes; all existing tests still pass (they construct `Slot(...)` without `runtime=`, so they get the default LOCAL).

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/jordanburger/scout-plugin
.venv/bin/ruff check engine/scout/scripts/schedule_tick.py engine/tests/unit/test_schedule_tick.py
.venv/bin/ruff format --check engine/scout/scripts/schedule_tick.py engine/tests/unit/test_schedule_tick.py
.venv/bin/mypy engine/scout/scripts/schedule_tick.py
git add engine/scout/scripts/schedule_tick.py engine/tests/unit/test_schedule_tick.py
git commit -m "feat(engine): dispatcher rejects runtime: remote with clear Plan-7 message"
```

---

## Task 3: Engine — `scoutctl schedule validate --target <path>` flag

**Files:**
- Modify: `engine/scout/cli.py::cli_schedule_validate`
- Test: `engine/tests/unit/test_cli_schedule_subapp.py`

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/unit/test_cli_schedule_subapp.py`:

```python
def test_schedule_validate_target_flag_passes_for_valid_yaml(tmp_path):
    """The --target flag points validate at an arbitrary path so the Schedules
    tab editor can validate a candidate before committing via atomic-rename."""
    target = tmp_path / "candidate.yaml"
    target.write_text(
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
    runner = CliRunner()
    result = runner.invoke(app, ["schedule", "validate", "--target", str(target)])
    assert result.exit_code == 0
    assert "schedule OK" in result.output


def test_schedule_validate_target_flag_fails_for_invalid_yaml(tmp_path):
    target = tmp_path / "broken.yaml"
    target.write_text("schema_version: 99\nslots: {}\n")
    runner = CliRunner()
    result = runner.invoke(app, ["schedule", "validate", "--target", str(target)])
    assert result.exit_code == 1
    assert "schema_version" in result.stderr or "schema_version" in result.output


def test_schedule_validate_target_flag_fails_for_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.yaml"
    runner = CliRunner()
    result = runner.invoke(app, ["schedule", "validate", "--target", str(missing)])
    assert result.exit_code == 1


def test_schedule_validate_no_flag_keeps_default_behavior(tmp_path, monkeypatch):
    """Without --target, validate reads from the vault path (or engine defaults).
    Backward-compat with pre-Plan-6 callers."""
    # Point the vault at an empty tmp_path so the defaults fallback kicks in.
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["schedule", "validate"])
    assert result.exit_code == 0
    assert "schedule OK" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/jordanburger/scout-plugin/engine
/Users/jordanburger/scout-plugin/.venv/bin/pytest tests/unit/test_cli_schedule_subapp.py -v -k validate
```
Expected: the three `--target` tests fail (flag doesn't exist); the no-flag test may already pass.

- [ ] **Step 3: Add the `--target` flag**

Edit `engine/scout/cli.py`. Find `cli_schedule_validate` (currently no parameters) and replace its signature + body:

```python
@schedule_app.command("validate")
def cli_schedule_validate(
    target: Path | None = typer.Option(
        None,
        "--target",
        "-t",
        help=(
            "Validate the schedule.yaml at this path instead of the vault canonical. "
            "Used by scout-app's editor to validate candidate writes before atomic-rename."
        ),
    ),
) -> None:
    """Re-load the schedule (canonical + overlay if present); exit 0 on success."""
    from scout import paths as _paths
    from scout.schedule import load_default_schedule, load_schedule

    if target is not None:
        if not target.exists():
            typer.echo(f"target does not exist: {target}", err=True)
            raise typer.Exit(code=1)
        load_schedule(target)
        typer.echo(f"schedule OK: {target}")
        return

    vault_path = _paths.data_dir() / ".scout-state" / "schedule.yaml"
    if vault_path.exists():
        load_schedule(vault_path)
        typer.echo(f"schedule OK: {vault_path}")
    else:
        load_default_schedule()
        typer.echo("schedule OK: (no vault file; using plugin defaults)")
```

Wrap the `load_schedule(target)` call with try/except so a `ConfigError` exits 1 with the engine's message:

```python
    if target is not None:
        if not target.exists():
            typer.echo(f"target does not exist: {target}", err=True)
            raise typer.Exit(code=1)
        try:
            load_schedule(target)
        except ConfigError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1) from e
        typer.echo(f"schedule OK: {target}")
        return
```

Make sure `from scout.errors import ConfigError` is imported at top of file (it likely is already).

- [ ] **Step 4: Run tests**

```bash
/Users/jordanburger/scout-plugin/.venv/bin/pytest tests/unit/test_cli_schedule_subapp.py -v -k validate
```
Expected: all 4 tests pass.

Then full suite:
```bash
/Users/jordanburger/scout-plugin/.venv/bin/pytest tests/ -q
```
Expected: 418+ passed (the previous baseline) + the 4 new ones from this task + the runtime tests from Tasks 1–2.

- [ ] **Step 5: Lint + commit**

```bash
cd /Users/jordanburger/scout-plugin
.venv/bin/ruff check engine/scout/cli.py engine/tests/unit/test_cli_schedule_subapp.py
.venv/bin/ruff format --check engine/scout/cli.py engine/tests/unit/test_cli_schedule_subapp.py
.venv/bin/mypy engine/scout/cli.py
git add engine/scout/cli.py engine/tests/unit/test_cli_schedule_subapp.py
git commit -m "feat(engine): scoutctl schedule validate --target <path> flag"
```

**Engine PR ready.** Push `plan-6-engine` and open the PR. Wait for CI green + merge before starting Task 4 (the scout-app side calls the new flag).

---

## Task 4: Scout-app — `Slot` Swift model

**Files:**
- Create: `Scout/Models/Slot.swift`
- Test: `ScoutTests/Models/SlotTests.swift`

- [ ] **Step 1: Branch scout-app**

```bash
cd /Users/jordanburger/scout-app
git checkout main
git pull --ff-only
git checkout -b plan-6-schedules-tab
```

If `docs/superpowers/FOLLOWUPS.md` is dirty in the working tree from prior work, stash with `git stash push -m fups -- docs/superpowers/FOLLOWUPS.md` first; pop after the final task's commit.

- [ ] **Step 2: Write the failing test**

Create `ScoutTests/Models/SlotTests.swift`:

```swift
import XCTest
@testable import Scout

final class SlotTests: XCTestCase {
    func test_decode_from_scoutctl_list_json() throws {
        let json = """
        {
          "key": "morning-briefing",
          "type": "briefing",
          "runner": "run-scout.sh",
          "fires_at_local": "08:00",
          "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri"],
          "missed_window_hours": 4,
          "on_miss": "fire",
          "cooldown_minutes": 60,
          "budget_usd": null,
          "tz": null,
          "runtime": "local"
        }
        """.data(using: .utf8)!
        let slot = try JSONDecoder().decode(Slot.self, from: json)
        XCTAssertEqual(slot.key, "morning-briefing")
        XCTAssertEqual(slot.type, .briefing)
        XCTAssertEqual(slot.runner, "run-scout.sh")
        XCTAssertEqual(slot.firesAtLocal, "08:00")
        XCTAssertEqual(slot.weekdays, ["Mon", "Tue", "Wed", "Thu", "Fri"])
        XCTAssertEqual(slot.missedWindowHours, 4)
        XCTAssertEqual(slot.onMiss, .fire)
        XCTAssertEqual(slot.cooldownMinutes, 60)
        XCTAssertNil(slot.budgetUsd)
        XCTAssertNil(slot.tz)
        XCTAssertEqual(slot.runtime, .local)
    }

    func test_decode_defaults_runtime_to_local_when_absent() throws {
        // Pre-Plan-6 vault YAMLs round-tripped through the engine emit no
        // runtime field — Swift must default to .local for compatibility.
        let json = """
        {
          "key": "s",
          "type": "briefing",
          "runner": "run-scout.sh",
          "fires_at_local": "08:00",
          "weekdays": ["Mon"],
          "missed_window_hours": 4,
          "on_miss": "fire",
          "cooldown_minutes": 60,
          "budget_usd": null,
          "tz": null
        }
        """.data(using: .utf8)!
        let slot = try JSONDecoder().decode(Slot.self, from: json)
        XCTAssertEqual(slot.runtime, .local)
    }

    func test_round_trip_encode_decode() throws {
        let original = Slot(
            key: "research",
            type: .research,
            runner: "run-research.sh",
            firesAtLocal: "14:00",
            weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
            missedWindowHours: 4,
            onMiss: .skip,
            cooldownMinutes: 240,
            budgetUsd: 5.0,
            tz: "America/New_York",
            runtime: .local
        )
        let encoded = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(Slot.self, from: encoded)
        XCTAssertEqual(original, decoded)
    }
}
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /Users/jordanburger/scout-app
xcodebuild test -only-testing:ScoutTests/SlotTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: BUILD FAILED — `Slot` type doesn't exist.

- [ ] **Step 4: Create `Scout/Models/Slot.swift`**

```swift
import Foundation

/// Swift mirror of the engine's Slot dataclass. Decoded from
/// `scoutctl schedule list --json` (snake_case keys); encoded back to
/// snake_case for round-trip tests + future `runtime` field shape parity.
///
/// Source of truth: engine/scout/schedule.py::Slot. Keep field set in sync.
struct Slot: Identifiable, Equatable, Hashable, Sendable, Codable {
    let key: String
    let type: SlotType
    let runner: String
    let firesAtLocal: String
    let weekdays: [String]
    let missedWindowHours: Int
    let onMiss: OnMissPolicy
    let cooldownMinutes: Int
    let budgetUsd: Double?
    let tz: String?
    let runtime: SlotRuntime

    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key
        case type
        case runner
        case firesAtLocal = "fires_at_local"
        case weekdays
        case missedWindowHours = "missed_window_hours"
        case onMiss = "on_miss"
        case cooldownMinutes = "cooldown_minutes"
        case budgetUsd = "budget_usd"
        case tz
        case runtime
    }

    init(
        key: String,
        type: SlotType,
        runner: String,
        firesAtLocal: String,
        weekdays: [String],
        missedWindowHours: Int,
        onMiss: OnMissPolicy,
        cooldownMinutes: Int,
        budgetUsd: Double? = nil,
        tz: String? = nil,
        runtime: SlotRuntime = .local
    ) {
        self.key = key
        self.type = type
        self.runner = runner
        self.firesAtLocal = firesAtLocal
        self.weekdays = weekdays
        self.missedWindowHours = missedWindowHours
        self.onMiss = onMiss
        self.cooldownMinutes = cooldownMinutes
        self.budgetUsd = budgetUsd
        self.tz = tz
        self.runtime = runtime
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.key = try c.decode(String.self, forKey: .key)
        self.type = try c.decode(SlotType.self, forKey: .type)
        self.runner = try c.decode(String.self, forKey: .runner)
        self.firesAtLocal = try c.decode(String.self, forKey: .firesAtLocal)
        self.weekdays = try c.decode([String].self, forKey: .weekdays)
        self.missedWindowHours = try c.decode(Int.self, forKey: .missedWindowHours)
        self.onMiss = try c.decode(OnMissPolicy.self, forKey: .onMiss)
        self.cooldownMinutes = try c.decode(Int.self, forKey: .cooldownMinutes)
        self.budgetUsd = try c.decodeIfPresent(Double.self, forKey: .budgetUsd)
        self.tz = try c.decodeIfPresent(String.self, forKey: .tz)
        // Forward-compat: default to .local when the engine omits the field.
        self.runtime = try c.decodeIfPresent(SlotRuntime.self, forKey: .runtime) ?? .local
    }
}

enum SlotType: String, CaseIterable, Codable, Sendable {
    case briefing
    case consolidation
    case dreaming
    case research
    case manual
}

enum OnMissPolicy: String, CaseIterable, Codable, Sendable {
    case fire
    case skip
    case collapse
}

enum SlotRuntime: String, CaseIterable, Codable, Sendable {
    case local
    case remote  // Reserved for Plan 7. UI renders disabled.
}
```

- [ ] **Step 5: Run tests**

```bash
xcodebuild test -only-testing:ScoutTests/SlotTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: 3 passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add Scout/Models/Slot.swift ScoutTests/Models/SlotTests.swift
git commit -m "feat(app): Slot Swift model — Codable mirror of engine Slot dataclass"
```

---

## Task 5: Scout-app — `ScheduleEditService.loadAll()` + `MockProcessRunner` test stub

**Files:**
- Create: `Scout/Services/ScheduleEditService.swift`
- Create: `Scout/Services/StaleScheduleError.swift`
- Test: `ScoutTests/Services/ScheduleEditServiceTests.swift`

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Services/ScheduleEditServiceTests.swift`:

```swift
import XCTest
@testable import Scout

@MainActor
final class ScheduleEditServiceTests: XCTestCase {

    // Reusable canned `scoutctl schedule list --json` output for tests.
    static let sampleListJSON = """
    [
      {"key":"morning-briefing","type":"briefing","runner":"run-scout.sh",
       "fires_at_local":"08:00","weekdays":["Mon","Tue","Wed","Thu","Fri"],
       "missed_window_hours":4,"on_miss":"fire","cooldown_minutes":60,
       "budget_usd":null,"tz":null,"runtime":"local"},
      {"key":"research","type":"research","runner":"run-research.sh",
       "fires_at_local":"14:00","weekdays":["Mon","Tue","Wed","Thu","Fri"],
       "missed_window_hours":4,"on_miss":"skip","cooldown_minutes":240,
       "budget_usd":null,"tz":null,"runtime":"local"}
    ]
    """

    func test_loadAll_decodes_slots_from_scoutctl_output() async throws {
        let runner = QueueProcessRunner(stdouts: [Self.sampleListJSON])
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            canonicalSchedulePath: URL(fileURLWithPath: "/tmp/none")
        )
        try await service.loadAll()
        XCTAssertEqual(service.slots.count, 2)
        XCTAssertEqual(service.slots[0].key, "morning-briefing")
        XCTAssertEqual(service.slots[1].key, "research")
    }

    func test_loadAll_invokes_scoutctl_with_correct_arguments() async throws {
        let runner = QueueProcessRunner(stdouts: [Self.sampleListJSON])
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            canonicalSchedulePath: URL(fileURLWithPath: "/tmp/none"),
            argumentsPrefix: ["scoutctl"]
        )
        try await service.loadAll()
        XCTAssertEqual(runner.calls.count, 1)
        XCTAssertEqual(runner.calls[0].arguments, ["scoutctl", "schedule", "list", "--json"])
    }

    func test_loadAll_throws_on_malformed_json() async throws {
        let runner = QueueProcessRunner(stdouts: ["{not valid json"])
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            canonicalSchedulePath: URL(fileURLWithPath: "/tmp/none")
        )
        do {
            try await service.loadAll()
            XCTFail("expected throw")
        } catch {
            // Expected — DecodingError or similar.
        }
    }
}

/// FIFO-stdouts `ProcessRunner` test stub. Mirrors the pattern used in
/// `ScheduleServiceTests.StubScheduleRunner` (Plan 5).
final class QueueProcessRunner: ProcessRunner, @unchecked Sendable {
    struct Call {
        let executable: URL
        let arguments: [String]
        let environment: [String: String]
        let workingDirectory: URL?
    }

    private(set) var calls: [Call] = []
    private var stdouts: [String]
    private let exitCode: Int32

    init(stdouts: [String], exitCode: Int32 = 0) {
        self.stdouts = stdouts
        self.exitCode = exitCode
    }

    func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessOutput {
        calls.append(Call(
            executable: executable,
            arguments: arguments,
            environment: environment,
            workingDirectory: workingDirectory
        ))
        let stdout: String
        if stdouts.isEmpty {
            stdout = ""
        } else if stdouts.count == 1 {
            stdout = stdouts[0]  // Reuse last entry on exhaustion.
        } else {
            stdout = stdouts.removeFirst()
        }
        return ProcessOutput(stdout: stdout, stderr: "", exitCode: exitCode)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
xcodebuild test -only-testing:ScoutTests/ScheduleEditServiceTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: BUILD FAILED — `ScheduleEditService` and `StaleScheduleError` don't exist.

- [ ] **Step 3: Create `Scout/Services/StaleScheduleError.swift`**

```swift
import Foundation

/// Thrown by `ScheduleEditService.save` when the canonical schedule.yaml's
/// mtime advanced since the most recent reload — i.e. someone (Vim,
/// scoutctl, etc.) edited it concurrently. The UI catches this, surfaces a
/// banner, and prompts the user to reload before saving again.
struct StaleScheduleError: Error, LocalizedError {
    let loadedAt: Date
    let modifiedAt: Date

    var errorDescription: String? {
        "schedule.yaml was modified externally at \(modifiedAt). Reload to bring in changes."
    }
}
```

- [ ] **Step 4: Create `Scout/Services/ScheduleEditService.swift`**

```swift
import Foundation
import Combine

@MainActor
final class ScheduleEditService: ObservableObject {
    @Published private(set) var slots: [Slot] = []
    @Published private(set) var loadedMtime: Date?

    private let scoutctl: URL
    private let runner: any ProcessRunner
    private let argumentsPrefix: [String]
    let canonicalSchedulePath: URL

    init(
        scoutctl: URL,
        runner: any ProcessRunner,
        canonicalSchedulePath: URL,
        argumentsPrefix: [String] = []
    ) {
        self.scoutctl = scoutctl
        self.runner = runner
        self.argumentsPrefix = argumentsPrefix
        self.canonicalSchedulePath = canonicalSchedulePath
    }

    /// Reads the live schedule via `scoutctl schedule list --json`, decodes,
    /// publishes. Captures the canonical file's mtime for the stale-check
    /// performed by save().
    func loadAll() async throws {
        let output = try await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + ["schedule", "list", "--json"],
            environment: [:],
            workingDirectory: nil
        )
        let data = output.stdout.data(using: .utf8) ?? Data()
        let decoded = try JSONDecoder().decode([Slot].self, from: data)
        self.slots = decoded
        self.loadedMtime = (try? FileManager.default
            .attributesOfItem(atPath: canonicalSchedulePath.path)[.modificationDate]) as? Date
    }
}
```

- [ ] **Step 5: Run tests**

```bash
xcodebuild test -only-testing:ScoutTests/ScheduleEditServiceTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add Scout/Services/ScheduleEditService.swift Scout/Services/StaleScheduleError.swift ScoutTests/Services/ScheduleEditServiceTests.swift
git commit -m "feat(app): ScheduleEditService skeleton + loadAll via scoutctl + QueueProcessRunner stub"
```

---

## Task 6: Scout-app — `ScheduleEditService.save()` (mtime + atomic + tmpfile cleanup)

**Files:**
- Modify: `Scout/Services/ScheduleEditService.swift`
- Test: `ScoutTests/Services/ScheduleEditServiceTests.swift` (extend)

- [ ] **Step 1: Write failing tests for save success + mtime stale + tmpfile cleanup**

Append to `ScheduleEditServiceTests.swift`:

```swift
extension ScheduleEditServiceTests {

    /// Helper: build a temp dir with a seed schedule.yaml + a service whose
    /// loadedMtime matches the seed file. Returns (service, runner, dir).
    func makeServiceOnDisk(
        listJSON: String = sampleListJSON,
        validateExitCode: Int32 = 0,
        validateStderr: String = ""
    ) throws -> (ScheduleEditService, QueueProcessRunner, URL) {
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("schedule-edit-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let canonical = dir.appendingPathComponent("schedule.yaml")
        try """
        # Header comment — line 1
        # Header comment — line 2
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

          research:
            type: research
            runner: run-research.sh
            fires_at_local: "14:00"
            weekdays: [Mon, Tue, Wed, Thu, Fri]
            missed_window_hours: 4
            on_miss: skip
            cooldown_minutes: 240
        """.write(to: canonical, atomically: true, encoding: .utf8)

        // Two queued stdouts: one for loadAll, one for the post-save reload.
        // Validate calls return exit 0 unless overridden.
        let runner = QueueProcessRunner(
            stdouts: [listJSON, listJSON],
            exitCode: 0,
            validateExitCode: validateExitCode,
            validateStderr: validateStderr
        )
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            canonicalSchedulePath: canonical,
            argumentsPrefix: ["scoutctl"]
        )
        return (service, runner, dir)
    }

    func test_save_writes_canonical_atomically_on_validate_success() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()

        // Edit cooldown on first slot.
        var draftSlots = service.slots
        draftSlots[0] = Slot(
            key: draftSlots[0].key, type: draftSlots[0].type, runner: draftSlots[0].runner,
            firesAtLocal: draftSlots[0].firesAtLocal, weekdays: draftSlots[0].weekdays,
            missedWindowHours: draftSlots[0].missedWindowHours, onMiss: draftSlots[0].onMiss,
            cooldownMinutes: 999,  // changed
            budgetUsd: draftSlots[0].budgetUsd, tz: draftSlots[0].tz, runtime: draftSlots[0].runtime
        )

        try await service.save(allSlots: draftSlots)
        let canonical = dir.appendingPathComponent("schedule.yaml")
        let written = try String(contentsOf: canonical, encoding: .utf8)
        XCTAssertTrue(written.contains("cooldown_minutes: 999"))
    }

    func test_save_throws_StaleScheduleError_when_file_modified_externally() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()

        // Simulate an external edit by touching the canonical with a newer mtime.
        let canonical = dir.appendingPathComponent("schedule.yaml")
        let future = Date().addingTimeInterval(60)
        try FileManager.default.setAttributes(
            [.modificationDate: future],
            ofItemAtPath: canonical.path
        )

        do {
            try await service.save(allSlots: service.slots)
            XCTFail("expected StaleScheduleError")
        } catch is StaleScheduleError {
            // Expected.
        }

        // Canonical still contains the original cooldown — save was blocked.
        let unchanged = try String(contentsOf: canonical, encoding: .utf8)
        XCTAssertTrue(unchanged.contains("cooldown_minutes: 60"))
    }

    func test_save_cleans_up_tmpfile_on_validate_failure() async throws {
        let (service, _, dir) = try makeServiceOnDisk(
            validateExitCode: 1,
            validateStderr: "schema_version mismatch"
        )
        try await service.loadAll()

        do {
            try await service.save(allSlots: service.slots)
            XCTFail("expected throw")
        } catch {
            // Expected — engine validate failed.
        }

        // No tmpfiles left in the directory.
        let contents = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
        let tmps = contents.filter { $0.lastPathComponent.contains(".tmp") }
        XCTAssertEqual(tmps, [], "tmpfile leaked: \(tmps)")
    }

    func test_save_cleans_up_tmpfile_on_success() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()
        try await service.save(allSlots: service.slots)
        let contents = try FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
        let tmps = contents.filter { $0.lastPathComponent.contains(".tmp") }
        XCTAssertEqual(tmps, [], "tmpfile leaked after success: \(tmps)")
    }
}
```

Update `QueueProcessRunner` to recognize the validate invocation and return a custom exit code per call type:

```swift
final class QueueProcessRunner: ProcessRunner, @unchecked Sendable {
    struct Call {
        let executable: URL
        let arguments: [String]
        let environment: [String: String]
        let workingDirectory: URL?
    }

    private(set) var calls: [Call] = []
    private var stdouts: [String]
    private let listExitCode: Int32
    private let validateExitCode: Int32
    private let validateStderr: String

    init(
        stdouts: [String],
        exitCode: Int32 = 0,
        validateExitCode: Int32 = 0,
        validateStderr: String = ""
    ) {
        self.stdouts = stdouts
        self.listExitCode = exitCode
        self.validateExitCode = validateExitCode
        self.validateStderr = validateStderr
    }

    func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessOutput {
        calls.append(Call(executable: executable, arguments: arguments, environment: environment, workingDirectory: workingDirectory))

        // Validate calls produce no stdout, return validateExitCode + validateStderr.
        if arguments.contains("validate") {
            return ProcessOutput(stdout: "", stderr: validateStderr, exitCode: validateExitCode)
        }

        // Otherwise it's a `list` call — pop / reuse stdouts queue.
        let stdout: String
        if stdouts.isEmpty {
            stdout = ""
        } else if stdouts.count == 1 {
            stdout = stdouts[0]
        } else {
            stdout = stdouts.removeFirst()
        }
        return ProcessOutput(stdout: stdout, stderr: "", exitCode: listExitCode)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
xcodebuild test -only-testing:ScoutTests/ScheduleEditServiceTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: 4 new tests fail (`save` doesn't exist).

- [ ] **Step 3: Add `save()` to `ScheduleEditService.swift`**

Add `import Yams` to the top of the file. Add to the class body:

```swift
    /// Writes the candidate slots to canonical.
    /// Steps (mirror §7.1 of the spec):
    /// 1. Stale-check: live mtime must equal loadedMtime; else throw StaleScheduleError.
    /// 2. Compose YAML (header preservation in Task 7).
    /// 3. Write to tmpfile in same directory.
    /// 4. Validate via scoutctl schedule validate --target <tmpfile>.
    /// 5. Atomic-rename via FileManager.replaceItemAt.
    /// 6. Reload via scoutctl schedule list --json + recapture mtime.
    func save(allSlots: [Slot]) async throws {
        // 1. Stale-check.
        let liveMtime = try (FileManager.default
            .attributesOfItem(atPath: canonicalSchedulePath.path)[.modificationDate]) as? Date
        if let live = liveMtime, let loaded = loadedMtime, live > loaded {
            throw StaleScheduleError(loadedAt: loaded, modifiedAt: live)
        }

        // 2. Compose YAML — Task 7 adds header preservation. For now, pure Yams.
        let body = try Yams.dump(object: serializeSlotsToDict(allSlots))

        // 3. Tmpfile in same directory; defer guarantees cleanup on every exit path.
        let tmp = canonicalSchedulePath
            .deletingLastPathComponent()
            .appendingPathComponent("schedule.yaml.\(UUID().uuidString).tmp")
        try body.write(to: tmp, atomically: false, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: tmp) }

        // 4. Validate via scoutctl.
        let validate = try await runner.run(
            executable: scoutctl,
            arguments: argumentsPrefix + ["schedule", "validate", "--target", tmp.path],
            environment: [:],
            workingDirectory: nil
        )
        guard validate.exitCode == 0 else {
            throw NSError(
                domain: "ScheduleEditService.save",
                code: Int(validate.exitCode),
                userInfo: [NSLocalizedDescriptionKey: validate.stderr]
            )
        }

        // 5. Atomic rename. replaceItemAt consumes tmp on success, so the
        // defer becomes a no-op (removeItem on a missing file fails silently
        // because of the `try?`).
        _ = try FileManager.default.replaceItemAt(
            canonicalSchedulePath,
            withItemAt: tmp,
            backupItemName: nil,
            options: [.usingNewMetadataOnly]
        )

        // 6. Reload + recapture mtime.
        try await loadAll()
    }

    /// Compose a YAML-serializable dict from the slot array. Preserves YAML
    /// insertion order via NSMutableOrderedDictionary semantics (Yams handles
    /// dict order on macOS).
    private func serializeSlotsToDict(_ slots: [Slot]) -> [String: Any] {
        var slotsDict: [String: [String: Any]] = [:]
        for slot in slots {
            var dict: [String: Any] = [
                "type": slot.type.rawValue,
                "runner": slot.runner,
                "fires_at_local": slot.firesAtLocal,
                "weekdays": slot.weekdays,
                "missed_window_hours": slot.missedWindowHours,
                "on_miss": slot.onMiss.rawValue,
                "cooldown_minutes": slot.cooldownMinutes,
                "runtime": slot.runtime.rawValue,
            ]
            if let b = slot.budgetUsd { dict["budget_usd"] = b }
            if let tz = slot.tz { dict["tz"] = tz }
            slotsDict[slot.key] = dict
        }
        return [
            "schema_version": 1,
            "slots": slotsDict,
        ]
    }
```

- [ ] **Step 4: Run tests**

```bash
xcodebuild test -only-testing:ScoutTests/ScheduleEditServiceTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -15
```
Expected: 7 passed (3 from Task 5 + 4 from Task 6).

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/ScheduleEditService.swift ScoutTests/Services/ScheduleEditServiceTests.swift
git commit -m "feat(app): ScheduleEditService.save — mtime stale-check + atomic rename + tmpfile defer"
```

---

## Task 7: Scout-app — Header-comment preservation in `save()`

**Files:**
- Modify: `Scout/Services/ScheduleEditService.swift`
- Test: `ScoutTests/Services/ScheduleEditServiceTests.swift` (extend)

- [ ] **Step 1: Write failing tests**

Append to `ScheduleEditServiceTests.swift`:

```swift
extension ScheduleEditServiceTests {

    func test_save_preserves_header_comments_byte_for_byte() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()

        let canonical = dir.appendingPathComponent("schedule.yaml")
        let beforeText = try String(contentsOf: canonical, encoding: .utf8)
        let header = String(beforeText.prefix(while: { _ in true })
            .components(separatedBy: "\nslots:").first ?? "")
        // The seed has two header comment lines + a `schema_version: 1` line.
        XCTAssertTrue(header.contains("# Header comment — line 1"))
        XCTAssertTrue(header.contains("schema_version: 1"))

        try await service.save(allSlots: service.slots)
        let afterText = try String(contentsOf: canonical, encoding: .utf8)
        let afterHeader = afterText.components(separatedBy: "\nslots:").first ?? ""
        XCTAssertEqual(afterHeader, header, "header should survive byte-for-byte")
    }

    func test_save_falls_back_to_pure_yaml_when_no_slots_anchor() async throws {
        // Seed a file that's malformed for header detection (no `\nslots:` line).
        // ScheduleEditService still writes valid YAML via pure Yams emit.
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("schedule-edit-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let canonical = dir.appendingPathComponent("schedule.yaml")
        try "not valid yaml at all".write(to: canonical, atomically: true, encoding: .utf8)

        let runner = QueueProcessRunner(
            stdouts: [Self.sampleListJSON, Self.sampleListJSON]
        )
        let service = ScheduleEditService(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            runner: runner,
            canonicalSchedulePath: canonical,
            argumentsPrefix: ["scoutctl"]
        )
        try await service.loadAll()
        try await service.save(allSlots: service.slots)

        let afterText = try String(contentsOf: canonical, encoding: .utf8)
        XCTAssertTrue(afterText.contains("schema_version"))
        XCTAssertTrue(afterText.contains("morning-briefing"))
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
xcodebuild test -only-testing:ScoutTests/ScheduleEditServiceTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: `test_save_preserves_header_comments_byte_for_byte` fails (Task 6's pure Yams emit drops the header).

- [ ] **Step 3: Add header-preservation logic**

Edit `ScheduleEditService.swift`. Modify the `save` body — replace step 2 (the YAML compose) with:

```swift
        // 2. Compose YAML — header from canonical (regex anchor on \nslots:),
        // body re-emitted via Yams. Inline slot-block comments are not
        // preserved (explicit tradeoff documented in spec §7.2).
        let header = (try? extractHeader(from: canonicalSchedulePath)) ?? ""
        let body = try Yams.dump(object: serializeSlotsToDict(allSlots))
        let composed: String
        if header.isEmpty {
            composed = body
        } else {
            composed = header + "\nslots:\n" + dropLeadingSlotsLine(body)
        }
```

Replace the `try body.write(...)` line with `try composed.write(to: tmp, atomically: false, encoding: .utf8)`.

Add the two private helpers:

```swift
    /// Read the canonical file and capture everything up to (but not
    /// including) the `\nslots:` line. Returns the empty string if the file
    /// doesn't exist or the anchor isn't found.
    private func extractHeader(from path: URL) throws -> String {
        let raw = try String(contentsOf: path, encoding: .utf8)
        // Match everything from start of string up to (but not including) `\nslots:`.
        guard let range = raw.range(of: #"\A.*?(?=\nslots:)"#, options: .regularExpression) else {
            return ""
        }
        return String(raw[range])
    }

    /// Yams serializes our root dict with `schema_version` then `slots:`. Strip
    /// the synthetic `slots:` line so we can splice the canonical's preserved
    /// `slots:` opener back in. Defensive: if the body doesn't start with
    /// schema_version + slots:, return as-is.
    private func dropLeadingSlotsLine(_ body: String) -> String {
        // Body looks like "schema_version: 1\nslots:\n  morning-briefing:\n    ..."
        // We want the part after "slots:\n", indented as Yams produced it.
        if let range = body.range(of: "\nslots:\n") {
            return String(body[range.upperBound...])
        }
        return body
    }
```

- [ ] **Step 4: Run tests**

```bash
xcodebuild test -only-testing:ScoutTests/ScheduleEditServiceTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: 9 passed (7 from prior + 2 from Task 7).

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/ScheduleEditService.swift ScoutTests/Services/ScheduleEditServiceTests.swift
git commit -m "feat(app): ScheduleEditService — header-comment preservation via regex anchor"
```

---

## Task 8: Scout-app — `ScheduleEditService.delete()`

**Files:**
- Modify: `Scout/Services/ScheduleEditService.swift`
- Test: `ScoutTests/Services/ScheduleEditServiceTests.swift` (extend)

- [ ] **Step 1: Write the failing test**

Append:

```swift
extension ScheduleEditServiceTests {
    func test_delete_removes_slot_and_rewrites_canonical() async throws {
        let (service, _, dir) = try makeServiceOnDisk()
        try await service.loadAll()

        try await service.delete(slotKey: "research")

        let canonical = dir.appendingPathComponent("schedule.yaml")
        let written = try String(contentsOf: canonical, encoding: .utf8)
        XCTAssertFalse(written.contains("research:"), "research slot should be gone")
        XCTAssertTrue(written.contains("morning-briefing:"), "morning-briefing should remain")
    }

    func test_delete_throws_when_key_not_found() async throws {
        let (service, _, _) = try makeServiceOnDisk()
        try await service.loadAll()
        do {
            try await service.delete(slotKey: "nonexistent")
            XCTFail("expected throw")
        } catch {
            // Expected.
        }
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
xcodebuild test -only-testing:ScoutTests/ScheduleEditServiceTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: `delete` doesn't exist → BUILD FAILED.

- [ ] **Step 3: Implement `delete()`**

Add to `ScheduleEditService.swift`:

```swift
    /// Delete a slot by key and persist via the same atomic-write path as save.
    /// Throws if the key isn't in the current slot list.
    func delete(slotKey: String) async throws {
        guard slots.contains(where: { $0.key == slotKey }) else {
            throw NSError(
                domain: "ScheduleEditService.delete",
                code: 404,
                userInfo: [NSLocalizedDescriptionKey: "no such slot: \(slotKey)"]
            )
        }
        let remaining = slots.filter { $0.key != slotKey }
        try await save(allSlots: remaining)
    }
```

- [ ] **Step 4: Run tests**

```bash
xcodebuild test -only-testing:ScoutTests/ScheduleEditServiceTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/ScheduleEditService.swift ScoutTests/Services/ScheduleEditServiceTests.swift
git commit -m "feat(app): ScheduleEditService.delete — remove slot via save(allSlots: filtered)"
```

---

## Task 9: Scout-app — `SlotSummaryRow` (collapsed view)

**Files:**
- Create: `Scout/Schedules/SlotSummaryRow.swift`
- Test: `ScoutTests/Schedules/SlotSummaryRowTests.swift`

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/Schedules/SlotSummaryRowTests.swift`:

```swift
import XCTest
import SwiftUI
@testable import Scout

@MainActor
final class SlotSummaryRowTests: XCTestCase {
    func test_renders_slot_key_type_and_time_summary() {
        let slot = Slot(
            key: "morning-briefing",
            type: .briefing,
            runner: "run-scout.sh",
            firesAtLocal: "08:00",
            weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
            missedWindowHours: 4,
            onMiss: .fire,
            cooldownMinutes: 60
        )
        // The view renders Text containing the key, type, time, and weekdays.
        // We assert the description by reading the view's `summary` computed
        // property (exposed for testing).
        let row = SlotSummaryRow(slot: slot, hasDirtyDraft: false, isExpanded: false)
        XCTAssertEqual(row.summary, "morning-briefing · briefing · 08:00 MTWThF")
    }

    func test_summary_collapses_full_weekend() {
        let slot = Slot(
            key: "weekend-briefing",
            type: .briefing,
            runner: "run-scout.sh",
            firesAtLocal: "08:30",
            weekdays: ["Sat", "Sun"],
            missedWindowHours: 4,
            onMiss: .fire,
            cooldownMinutes: 60
        )
        let row = SlotSummaryRow(slot: slot, hasDirtyDraft: false, isExpanded: false)
        XCTAssertEqual(row.summary, "weekend-briefing · briefing · 08:30 SaSu")
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
xcodebuild test -only-testing:ScoutTests/SlotSummaryRowTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: BUILD FAILED — `SlotSummaryRow` doesn't exist.

- [ ] **Step 3: Create `Scout/Schedules/SlotSummaryRow.swift`**

```swift
import SwiftUI

/// One-line summary view for a slot in the collapsed state.
/// Format: `<slot-key> · <type> · <HH:MM> <weekday-shortlist>`
/// Examples:
///   morning-briefing · briefing · 08:00 MTWThF
///   weekend-briefing · briefing · 08:30 SaSu
struct SlotSummaryRow: View {
    let slot: Slot
    let hasDirtyDraft: Bool
    let isExpanded: Bool

    var summary: String {
        "\(slot.key) · \(slot.type.rawValue) · \(slot.firesAtLocal) \(weekdaysShort)"
    }

    private var weekdaysShort: String {
        // Map full weekday names to one- or two-char abbreviations matching
        // the design doc: Mon→M, Tue→T, Wed→W, Thu→Th, Fri→F, Sat→Sa, Sun→Su.
        slot.weekdays.map {
            switch $0 {
            case "Mon": return "M"
            case "Tue": return "T"
            case "Wed": return "W"
            case "Thu": return "Th"
            case "Fri": return "F"
            case "Sat": return "Sa"
            case "Sun": return "Su"
            default:    return $0
            }
        }.joined()
    }

    var body: some View {
        HStack {
            Text(slot.key).font(.body.monospaced())
            Text("·").foregroundStyle(.secondary)
            Text(slot.type.rawValue).foregroundStyle(.secondary)
            Text("·").foregroundStyle(.secondary)
            Text(slot.firesAtLocal).foregroundStyle(.secondary)
            Text(weekdaysShort).foregroundStyle(.secondary)
            if hasDirtyDraft {
                Image(systemName: "circle.fill").foregroundStyle(.orange).font(.system(size: 6))
            }
            Spacer()
            Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())  // make whole row tappable
    }
}
```

- [ ] **Step 4: Run tests**

```bash
xcodebuild test -only-testing:ScoutTests/SlotSummaryRowTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add Scout/Schedules/SlotSummaryRow.swift ScoutTests/Schedules/SlotSummaryRowTests.swift
git commit -m "feat(app): SlotSummaryRow — collapsed-state one-line slot display"
```

---

## Task 10: Scout-app — `SlotEditForm` core (sections, draft, Save/Revert, live validation)

**Files:**
- Create: `Scout/Schedules/SlotEditForm.swift`
- Create: `Scout/Schedules/SlotDraft.swift`
- Test: `ScoutTests/Schedules/SlotEditFormTests.swift`

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Schedules/SlotEditFormTests.swift`:

```swift
import XCTest
@testable import Scout

@MainActor
final class SlotEditFormTests: XCTestCase {

    static let sampleSlot = Slot(
        key: "morning-briefing",
        type: .briefing,
        runner: "run-scout.sh",
        firesAtLocal: "08:00",
        weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
        missedWindowHours: 4,
        onMiss: .fire,
        cooldownMinutes: 60
    )

    func test_validate_slot_key_kebab_case() {
        XCTAssertNil(SlotDraft.validateSlotKey("morning-briefing"))
        XCTAssertNil(SlotDraft.validateSlotKey("a"))
        XCTAssertNil(SlotDraft.validateSlotKey("a1-b2"))
        XCTAssertNotNil(SlotDraft.validateSlotKey(""))
        XCTAssertNotNil(SlotDraft.validateSlotKey("MorningBriefing"))
        XCTAssertNotNil(SlotDraft.validateSlotKey("has space"))
        XCTAssertNotNil(SlotDraft.validateSlotKey("-leading-dash"))
        XCTAssertNotNil(SlotDraft.validateSlotKey("1-leading-digit"))
    }

    func test_validate_fires_at_local_HH_MM() {
        XCTAssertNil(SlotDraft.validateFiresAtLocal("00:00"))
        XCTAssertNil(SlotDraft.validateFiresAtLocal("23:59"))
        XCTAssertNil(SlotDraft.validateFiresAtLocal("08:30"))
        XCTAssertNotNil(SlotDraft.validateFiresAtLocal("25:00"))
        XCTAssertNotNil(SlotDraft.validateFiresAtLocal("08:60"))
        XCTAssertNotNil(SlotDraft.validateFiresAtLocal("8:00"))    // need leading zero
        XCTAssertNotNil(SlotDraft.validateFiresAtLocal(""))
        XCTAssertNotNil(SlotDraft.validateFiresAtLocal("not a time"))
    }

    func test_validate_weekdays_at_least_one() {
        XCTAssertNil(SlotDraft.validateWeekdays(["Mon"]))
        XCTAssertNil(SlotDraft.validateWeekdays(["Mon", "Tue", "Wed", "Thu", "Fri"]))
        XCTAssertNotNil(SlotDraft.validateWeekdays([]))
    }

    func test_draft_is_dirty_when_any_field_differs_from_live() {
        let live = Self.sampleSlot
        var draft = SlotDraft(from: live)
        XCTAssertFalse(draft.isDirty(against: live))

        draft.cooldownMinutes = 999
        XCTAssertTrue(draft.isDirty(against: live))
    }

    func test_draft_save_blocked_when_validation_errors_present() {
        var draft = SlotDraft(from: Self.sampleSlot)
        draft.firesAtLocal = "25:00"  // invalid
        XCTAssertNotNil(draft.firstError)
    }

    func test_draft_save_unblocked_when_clean() {
        let draft = SlotDraft(from: Self.sampleSlot)
        XCTAssertNil(draft.firstError)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
xcodebuild test -only-testing:ScoutTests/SlotEditFormTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: BUILD FAILED — `SlotDraft` doesn't exist.

- [ ] **Step 3: Create `Scout/Schedules/SlotDraft.swift`**

```swift
import Foundation

/// Mutable working copy of a Slot. The view edits this in @State; on Save,
/// the form serializes it back to a Slot and hands it to ScheduleEditService.
struct SlotDraft: Equatable {
    var key: String
    var type: SlotType
    var runner: String
    var firesAtLocal: String
    var weekdays: Set<String>
    var missedWindowHours: Int
    var onMiss: OnMissPolicy
    var cooldownMinutes: Int
    var budgetUsd: Double?
    var tz: String?
    var runtime: SlotRuntime

    init(from slot: Slot) {
        self.key = slot.key
        self.type = slot.type
        self.runner = slot.runner
        self.firesAtLocal = slot.firesAtLocal
        self.weekdays = Set(slot.weekdays)
        self.missedWindowHours = slot.missedWindowHours
        self.onMiss = slot.onMiss
        self.cooldownMinutes = slot.cooldownMinutes
        self.budgetUsd = slot.budgetUsd
        self.tz = slot.tz
        self.runtime = slot.runtime
    }

    /// Materialize back to a Slot for the save path.
    /// Weekdays come out in MTWThFSaSu canonical order.
    func toSlot() -> Slot {
        let order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return Slot(
            key: key,
            type: type,
            runner: runner,
            firesAtLocal: firesAtLocal,
            weekdays: order.filter { weekdays.contains($0) },
            missedWindowHours: missedWindowHours,
            onMiss: onMiss,
            cooldownMinutes: cooldownMinutes,
            budgetUsd: budgetUsd,
            tz: tz,
            runtime: runtime
        )
    }

    func isDirty(against live: Slot) -> Bool {
        toSlot() != live
    }

    /// Returns the first per-field validation error, or nil if all clean.
    /// Used by the form to disable the Save button.
    var firstError: String? {
        if let e = SlotDraft.validateSlotKey(key) { return e }
        if let e = SlotDraft.validateFiresAtLocal(firesAtLocal) { return e }
        if let e = SlotDraft.validateWeekdays(Array(weekdays)) { return e }
        if runner.trimmingCharacters(in: .whitespaces).isEmpty { return "Runner can't be empty" }
        if cooldownMinutes < 0 { return "Cooldown must be >= 0" }
        if missedWindowHours <= 0 { return "Missed window must be > 0" }
        return nil
    }

    // MARK: - Static field validators (for unit tests + live form errors).

    static func validateSlotKey(_ s: String) -> String? {
        guard !s.isEmpty else { return "Slot key required" }
        // kebab-case: lowercase letter, then lowercase letters/digits/hyphens.
        let re = try! NSRegularExpression(pattern: #"^[a-z][a-z0-9-]*$"#)
        let range = NSRange(s.startIndex..<s.endIndex, in: s)
        return re.firstMatch(in: s, range: range) == nil
            ? "Slot key must be lowercase kebab-case (a-z, 0-9, hyphens; first char a letter)"
            : nil
    }

    static func validateFiresAtLocal(_ s: String) -> String? {
        let re = try! NSRegularExpression(pattern: #"^([01]\d|2[0-3]):[0-5]\d$"#)
        let range = NSRange(s.startIndex..<s.endIndex, in: s)
        return re.firstMatch(in: s, range: range) == nil
            ? "Time must be HH:MM (24-hour, leading zero required)"
            : nil
    }

    static func validateWeekdays(_ days: [String]) -> String? {
        days.isEmpty ? "Pick at least one weekday" : nil
    }
}
```

- [ ] **Step 4: Create `Scout/Schedules/SlotEditForm.swift`** (minimal — full UI in next task)

```swift
import SwiftUI

/// Inline expanded edit form for a single slot. Holds a SlotDraft in @State,
/// validates per-field live, and exposes a Save button when (draft != live)
/// AND all fields validate.
struct SlotEditForm: View {
    let liveSlot: Slot
    let isNewDraft: Bool   // true when the slot hasn't hit disk yet (Add flow)

    @State private var draft: SlotDraft
    @State private var saveError: String?

    init(liveSlot: Slot, isNewDraft: Bool) {
        self.liveSlot = liveSlot
        self.isNewDraft = isNewDraft
        _draft = State(initialValue: SlotDraft(from: liveSlot))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            slotKeyField
            timeAndWeekdaysSection
            onMissSection
            cooldownSection

            DisclosureGroup("Advanced") {
                advancedSection
            }
            .padding(.top, 8)

            if let err = saveError {
                Text(err)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding(.top, 8)
            }

            actionBar
        }
        .padding()
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.4))
        .cornerRadius(8)
    }

    @ViewBuilder
    private var slotKeyField: some View {
        if isNewDraft {
            VStack(alignment: .leading, spacing: 4) {
                Text("Slot key").font(.callout).foregroundStyle(.secondary)
                TextField("new-slot-1", text: $draft.key)
                    .textFieldStyle(.roundedBorder)
                if let err = SlotDraft.validateSlotKey(draft.key) {
                    Text(err).font(.caption).foregroundStyle(.red)
                } else {
                    Text("Slot keys are immutable after first save. Choose carefully.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        } else {
            HStack {
                Image(systemName: "lock.fill").foregroundStyle(.secondary)
                Text(draft.key).font(.body.monospaced())
                Spacer()
            }
        }
    }

    @ViewBuilder
    private var timeAndWeekdaysSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Time").font(.callout).foregroundStyle(.secondary)
            TextField("HH:MM", text: $draft.firesAtLocal)
                .textFieldStyle(.roundedBorder)
                .frame(width: 120)
            if let err = SlotDraft.validateFiresAtLocal(draft.firesAtLocal) {
                Text(err).font(.caption).foregroundStyle(.red)
            }
        }

        VStack(alignment: .leading, spacing: 4) {
            Text("Weekdays").font(.callout).foregroundStyle(.secondary)
            HStack(spacing: 4) {
                ForEach(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], id: \.self) { day in
                    Toggle(day, isOn: Binding(
                        get: { draft.weekdays.contains(day) },
                        set: { on in if on { draft.weekdays.insert(day) } else { draft.weekdays.remove(day) } }
                    ))
                    .toggleStyle(.button)
                }
            }
            if let err = SlotDraft.validateWeekdays(Array(draft.weekdays)) {
                Text(err).font(.caption).foregroundStyle(.red)
            }
        }
    }

    @ViewBuilder
    private var onMissSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("On miss").font(.callout).foregroundStyle(.secondary)
            Picker("", selection: $draft.onMiss) {
                Text("Fire").tag(OnMissPolicy.fire)
                Text("Skip").tag(OnMissPolicy.skip)
                Text("Collapse").tag(OnMissPolicy.collapse)
            }
            .pickerStyle(.segmented)
            .frame(width: 240)
        }
    }

    @ViewBuilder
    private var cooldownSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Cooldown (minutes)").font(.callout).foregroundStyle(.secondary)
            Stepper(value: $draft.cooldownMinutes, in: 0...720, step: 15) {
                Text("\(draft.cooldownMinutes)")
            }
            .frame(width: 200)
        }
    }

    @ViewBuilder
    private var advancedSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Runner").font(.callout).foregroundStyle(.secondary)
                TextField("run-scout.sh", text: $draft.runner)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 240)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Missed window (hours)").font(.callout).foregroundStyle(.secondary)
                Stepper(value: $draft.missedWindowHours, in: 1...12) {
                    Text("\(draft.missedWindowHours)")
                }
                .frame(width: 200)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Type").font(.callout).foregroundStyle(.secondary)
                Picker("", selection: $draft.type) {
                    ForEach(SlotType.allCases, id: \.self) { t in
                        Text(t.rawValue.capitalized).tag(t)
                    }
                }
                .pickerStyle(.segmented)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Runtime").font(.callout).foregroundStyle(.secondary)
                Picker("", selection: $draft.runtime) {
                    Text("Local").tag(SlotRuntime.local)
                    Text("Remote (Plan 7)").tag(SlotRuntime.remote)
                }
                .pickerStyle(.segmented)
                .disabled(true)  // Plan 6: remote disabled until Plan 7 ships.
                Text("Remote slot execution arrives in Plan 7 (Anthropic routines integration).")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.top, 4)
    }

    @ViewBuilder
    private var actionBar: some View {
        HStack {
            // Delete + Fire-now wired in Task 11.
            Spacer()
            Button("Revert") {
                draft = SlotDraft(from: liveSlot)
            }
            .disabled(!draft.isDirty(against: liveSlot))
            Button("Save") {
                // Save action wired in Task 12 (needs ScheduleEditService dispatch).
            }
            .keyboardShortcut(.return, modifiers: [.command])
            .disabled(draft.firstError != nil || !draft.isDirty(against: liveSlot))
        }
        .padding(.top, 8)
    }
}
```

- [ ] **Step 5: Run tests**

```bash
xcodebuild test -only-testing:ScoutTests/SlotEditFormTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: 6 passed.

- [ ] **Step 6: Build the app to confirm SlotEditForm compiles in context**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -5
```
Expected: BUILD SUCCEEDED.

- [ ] **Step 7: Commit**

```bash
git add Scout/Schedules/SlotEditForm.swift Scout/Schedules/SlotDraft.swift ScoutTests/Schedules/SlotEditFormTests.swift
git commit -m "feat(app): SlotEditForm + SlotDraft — Common + Advanced sections, live validation, Revert"
```

---

## Task 11: Scout-app — `SlotEditForm` action wiring (Save, Delete, Fire-now, type-change confirm)

**Files:**
- Modify: `Scout/Schedules/SlotEditForm.swift`
- Test: `ScoutTests/Schedules/SlotEditFormTests.swift` (extend)

- [ ] **Step 1: Write failing tests**

Append to `SlotEditFormTests.swift`:

```swift
extension SlotEditFormTests {
    func test_typeChange_triggers_confirmation_path() {
        var draft = SlotDraft(from: Self.sampleSlot)
        XCTAssertFalse(SlotEditForm.requiresTypeChangeConfirmation(draft: draft, live: Self.sampleSlot))
        draft.type = .consolidation
        XCTAssertTrue(SlotEditForm.requiresTypeChangeConfirmation(draft: draft, live: Self.sampleSlot))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
xcodebuild test -only-testing:ScoutTests/SlotEditFormTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: BUILD FAILED — `requiresTypeChangeConfirmation` doesn't exist.

- [ ] **Step 3: Wire actions into `SlotEditForm`**

Edit `Scout/Schedules/SlotEditForm.swift`:

1. Add new init parameters and bindings — replace the struct's stored properties + init:

```swift
struct SlotEditForm: View {
    let liveSlot: Slot
    let isNewDraft: Bool
    let onSave: (Slot) async -> Void
    let onDelete: () async -> Void
    let onFireNow: (String) async -> Void
    let onRevertNewDraft: (() -> Void)?  // Only set when isNewDraft

    @State private var draft: SlotDraft
    @State private var saveError: String?
    @State private var isConfirmingTypeChange = false
    @State private var isConfirmingDelete = false
    @State private var isSaving = false

    init(
        liveSlot: Slot,
        isNewDraft: Bool,
        onSave: @escaping (Slot) async -> Void,
        onDelete: @escaping () async -> Void,
        onFireNow: @escaping (String) async -> Void,
        onRevertNewDraft: (() -> Void)? = nil
    ) {
        self.liveSlot = liveSlot
        self.isNewDraft = isNewDraft
        self.onSave = onSave
        self.onDelete = onDelete
        self.onFireNow = onFireNow
        self.onRevertNewDraft = onRevertNewDraft
        _draft = State(initialValue: SlotDraft(from: liveSlot))
    }

    static func requiresTypeChangeConfirmation(draft: SlotDraft, live: Slot) -> Bool {
        draft.type != live.type
    }
```

2. Replace `actionBar` with the wired version:

```swift
    @ViewBuilder
    private var actionBar: some View {
        HStack {
            if !isNewDraft {
                Button("Delete") { isConfirmingDelete = true }
                    .foregroundStyle(.red)
                Button("Fire now") {
                    Task { await onFireNow(draft.key) }
                }
                .disabled(draft.isDirty(against: liveSlot))
            }
            Spacer()
            Button("Revert") {
                if isNewDraft {
                    onRevertNewDraft?()
                } else {
                    draft = SlotDraft(from: liveSlot)
                }
            }
            .disabled(!isNewDraft && !draft.isDirty(against: liveSlot))
            Button("Save") {
                if Self.requiresTypeChangeConfirmation(draft: draft, live: liveSlot) {
                    isConfirmingTypeChange = true
                } else {
                    Task { await performSave() }
                }
            }
            .keyboardShortcut(.return, modifiers: [.command])
            .disabled(draft.firstError != nil || (!isNewDraft && !draft.isDirty(against: liveSlot)))
        }
        .padding(.top, 8)
        .alert(
            "Change slot type?",
            isPresented: $isConfirmingTypeChange,
            actions: {
                Button("Cancel", role: .cancel) { }
                Button("Change") {
                    Task { await performSave() }
                }
            },
            message: {
                Text("Changing slot type updates which connectors are required at fire time and reorders single-fire-per-tick priority. Continue?")
            }
        )
        .confirmationDialog(
            "Delete \(draft.key)?",
            isPresented: $isConfirmingDelete,
            titleVisibility: .visible,
            actions: {
                Button("Delete", role: .destructive) {
                    Task { await onDelete() }
                }
                Button("Cancel", role: .cancel) { }
            },
            message: {
                Text("Removes this slot from schedule.yaml. Tracker history is retained but unused. Run-event logs keep their references.")
            }
        )
    }

    private func performSave() async {
        isSaving = true
        defer { isSaving = false }
        await onSave(draft.toSlot())
    }
```

- [ ] **Step 4: Run tests**

```bash
xcodebuild test -only-testing:ScoutTests/SlotEditFormTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: 7 passed.

Also ensure the build still works:

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -5
```
Expected: BUILD SUCCEEDED. (`SlotEditForm` callers will be wired in Task 13.)

- [ ] **Step 5: Commit**

```bash
git add Scout/Schedules/SlotEditForm.swift ScoutTests/Schedules/SlotEditFormTests.swift
git commit -m "feat(app): SlotEditForm action wiring — Save callbacks, type-change confirm, Delete dialog"
```

---

## Task 12: Scout-app — `SlotRow` (collapsed/expanded container)

**Files:**
- Create: `Scout/Schedules/SlotRow.swift`

(No new tests — `SlotRow` is a thin pass-through. Indirectly covered by `SchedulesViewTests` in Task 13.)

- [ ] **Step 1: Create `Scout/Schedules/SlotRow.swift`**

```swift
import SwiftUI

/// Container that switches between SlotSummaryRow (collapsed) and
/// SlotEditForm (expanded). Tap on the summary toggles expansion.
struct SlotRow: View {
    let slot: Slot
    let isExpanded: Bool
    let isNewDraft: Bool
    let hasDirtyDraft: Bool
    let onToggleExpand: () -> Void
    let onSave: (Slot) async -> Void
    let onDelete: () async -> Void
    let onFireNow: (String) async -> Void
    let onRevertNewDraft: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onToggleExpand) {
                SlotSummaryRow(slot: slot, hasDirtyDraft: hasDirtyDraft, isExpanded: isExpanded)
            }
            .buttonStyle(.plain)

            if isExpanded {
                SlotEditForm(
                    liveSlot: slot,
                    isNewDraft: isNewDraft,
                    onSave: onSave,
                    onDelete: onDelete,
                    onFireNow: onFireNow,
                    onRevertNewDraft: onRevertNewDraft
                )
                .transition(.opacity.combined(with: .move(edge: .top)))
            }

            Divider()
        }
    }
}
```

- [ ] **Step 2: Build to confirm**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
```
Expected: BUILD SUCCEEDED.

- [ ] **Step 3: Commit**

```bash
git add Scout/Schedules/SlotRow.swift
git commit -m "feat(app): SlotRow — container switching between SlotSummaryRow + SlotEditForm"
```

---

## Task 13: Scout-app — `SchedulesView` (controller, empty state, banner, toolbar, new-slot insertion)

**Files:**
- Modify: `Scout/Schedules/SchedulesView.swift` (replace placeholder body)
- Test: `ScoutTests/Schedules/SchedulesViewTests.swift`

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Schedules/SchedulesViewTests.swift`:

```swift
import XCTest
@testable import Scout

@MainActor
final class SchedulesViewTests: XCTestCase {

    func test_placeholderKey_collision_bumps_to_next_integer() {
        let existing: [Slot] = [
            Slot(key: "new-slot-1", type: .briefing, runner: "run-scout.sh",
                 firesAtLocal: "09:00", weekdays: ["Mon"], missedWindowHours: 4,
                 onMiss: .fire, cooldownMinutes: 60),
            Slot(key: "new-slot-2", type: .briefing, runner: "run-scout.sh",
                 firesAtLocal: "09:00", weekdays: ["Mon"], missedWindowHours: 4,
                 onMiss: .fire, cooldownMinutes: 60),
        ]
        XCTAssertEqual(SchedulesView.nextNewSlotKey(existing: existing.map(\.key)), "new-slot-3")
    }

    func test_placeholderKey_no_collision_starts_at_one() {
        XCTAssertEqual(SchedulesView.nextNewSlotKey(existing: []), "new-slot-1")
        XCTAssertEqual(
            SchedulesView.nextNewSlotKey(existing: ["morning-briefing", "research"]),
            "new-slot-1"
        )
    }

    func test_makeNewDraftSlot_uses_safe_defaults() {
        let draft = SchedulesView.makeNewDraftSlot(key: "new-slot-1")
        XCTAssertEqual(draft.key, "new-slot-1")
        XCTAssertEqual(draft.type, .briefing)
        XCTAssertEqual(draft.runner, "run-scout.sh")
        XCTAssertEqual(draft.firesAtLocal, "09:00")
        XCTAssertEqual(draft.weekdays, ["Mon", "Tue", "Wed", "Thu", "Fri"])
        XCTAssertEqual(draft.onMiss, .fire)
        XCTAssertEqual(draft.cooldownMinutes, 60)
        XCTAssertEqual(draft.missedWindowHours, 4)
        XCTAssertEqual(draft.runtime, .local)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
xcodebuild test -only-testing:ScoutTests/SchedulesViewTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: BUILD FAILED — `SchedulesView.nextNewSlotKey` and `.makeNewDraftSlot` don't exist; placeholder body is still there.

- [ ] **Step 3: Replace `SchedulesView.swift` body**

Open `Scout/Schedules/SchedulesView.swift`. Delete `body`, `legacyBody`, `list`, `commitErrorBanner`, and `statusDot` (Plan 5 placeholder + lifted-legacy helpers). Replace with:

```swift
import SwiftUI

struct SchedulesView: View {
    @EnvironmentObject var service: ScheduleEditService
    @EnvironmentObject var appState: AppState

    @State private var expandedSlotKey: String?
    @State private var newDraftSlot: Slot?
    @State private var staleBannerVisible = false
    @State private var stalenessDetail: String?
    @State private var errorMessage: String?
    @State private var isInitialLoading = true

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if staleBannerVisible {
                staleBanner
            }
            if let err = errorMessage {
                errorBanner(err)
            }
            content
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    addDraftSlot()
                } label: {
                    Label("Add slot", systemImage: "plus")
                }
            }
        }
        .task {
            await reload()
        }
    }

    // MARK: - Content branches

    @ViewBuilder
    private var content: some View {
        if isInitialLoading {
            ProgressView().padding()
        } else if service.slots.isEmpty && newDraftSlot == nil {
            emptyState
        } else {
            slotList
        }
    }

    @ViewBuilder
    private var emptyState: some View {
        ContentUnavailableView {
            Label("No scheduled slots", systemImage: "calendar.badge.plus")
        } description: {
            Text("Add a slot to start scheduling Scout runs. Or run `scoutctl schedule init` from the terminal to seed the plugin defaults (10 standard slots).")
        } actions: {
            Button("+ Add slot") { addDraftSlot() }
        }
    }

    @ViewBuilder
    private var slotList: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                if let new = newDraftSlot {
                    SlotRow(
                        slot: new,
                        isExpanded: true,
                        isNewDraft: true,
                        hasDirtyDraft: true,
                        onToggleExpand: { },  // new-draft can't collapse
                        onSave: { saved in await saveNewDraft(saved) },
                        onDelete: { },         // new-draft can't delete; Revert removes it
                        onFireNow: { _ in },
                        onRevertNewDraft: { newDraftSlot = nil }
                    )
                }
                ForEach(service.slots) { slot in
                    SlotRow(
                        slot: slot,
                        isExpanded: expandedSlotKey == slot.key,
                        isNewDraft: false,
                        hasDirtyDraft: false,
                        onToggleExpand: { toggleExpand(slot.key) },
                        onSave: { saved in await saveExistingSlot(saved, original: slot) },
                        onDelete: { await deleteSlot(slot) },
                        onFireNow: { key in await appState.fireNow(slotKey: key, bypassBudget: false) },
                        onRevertNewDraft: nil
                    )
                }
            }
            .padding()
        }
    }

    // MARK: - Banners

    @ViewBuilder
    private var staleBanner: some View {
        HStack(alignment: .firstTextBaseline) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.yellow)
            VStack(alignment: .leading, spacing: 2) {
                Text("schedule.yaml was modified externally").font(.callout.bold())
                if let detail = stalenessDetail {
                    Text(detail).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button("Reload now") {
                Task { await reload() ; staleBannerVisible = false }
            }
            Button("Dismiss") { staleBannerVisible = false }
        }
        .padding(8)
        .background(Color.yellow.opacity(0.15))
    }

    @ViewBuilder
    private func errorBanner(_ text: String) -> some View {
        HStack {
            Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
            Text(text).font(.callout)
            Spacer()
            Button("Dismiss") { errorMessage = nil }
        }
        .padding(8)
        .background(Color.red.opacity(0.12))
    }

    // MARK: - Helpers (testable)

    static func nextNewSlotKey(existing: [String]) -> String {
        var n = 1
        while existing.contains("new-slot-\(n)") { n += 1 }
        return "new-slot-\(n)"
    }

    static func makeNewDraftSlot(key: String) -> Slot {
        Slot(
            key: key,
            type: .briefing,
            runner: "run-scout.sh",
            firesAtLocal: "09:00",
            weekdays: ["Mon", "Tue", "Wed", "Thu", "Fri"],
            missedWindowHours: 4,
            onMiss: .fire,
            cooldownMinutes: 60,
            budgetUsd: nil,
            tz: nil,
            runtime: .local
        )
    }

    // MARK: - Actions

    private func addDraftSlot() {
        guard newDraftSlot == nil else { return }   // Already adding one — re-expand it.
        let existing = service.slots.map(\.key)
        let key = Self.nextNewSlotKey(existing: existing)
        newDraftSlot = Self.makeNewDraftSlot(key: key)
        expandedSlotKey = nil
    }

    private func toggleExpand(_ key: String) {
        if expandedSlotKey == key {
            expandedSlotKey = nil
        } else {
            expandedSlotKey = key
        }
    }

    private func reload() async {
        do {
            try await service.loadAll()
            isInitialLoading = false
        } catch {
            errorMessage = error.localizedDescription
            isInitialLoading = false
        }
    }

    private func saveNewDraft(_ slot: Slot) async {
        var combined = service.slots
        combined.append(slot)
        do {
            try await service.save(allSlots: combined)
            newDraftSlot = nil
            expandedSlotKey = slot.key
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func saveExistingSlot(_ saved: Slot, original: Slot) async {
        var updated = service.slots
        if let idx = updated.firstIndex(where: { $0.key == original.key }) {
            updated[idx] = saved
        }
        do {
            try await service.save(allSlots: updated)
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func deleteSlot(_ slot: Slot) async {
        do {
            try await service.delete(slotKey: slot.key)
            if expandedSlotKey == slot.key { expandedSlotKey = nil }
        } catch let stale as StaleScheduleError {
            staleBannerVisible = true
            stalenessDetail = stale.errorDescription
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
```

- [ ] **Step 4: Run tests**

```bash
xcodebuild test -only-testing:ScoutTests/SchedulesViewTests -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: 3 passed.

Build the app to confirm everything compiles:

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -5
```
Expected: BUILD SUCCEEDED. (App will fail to launch until Task 14 wires `ScheduleEditService` into `AppState`.)

- [ ] **Step 5: Commit**

```bash
git add Scout/Schedules/SchedulesView.swift ScoutTests/Schedules/SchedulesViewTests.swift
git commit -m "feat(app): SchedulesView — single-expansion controller, empty state, stale banner, new-slot flow"
```

---

## Task 14: Scout-app — Wire `ScheduleEditService` into `AppState`, restore sidebar, delete legacy code

**Files:**
- Modify: `Scout/Shell/AppState.swift`
- Modify: `Scout/Shell/SidebarView.swift`
- Modify: `Scout/Shell/MainWindowView.swift` (env-object plumbing)
- Delete: `Scout/Services/ScheduleEditorService.swift`
- Delete: `Scout/Schedules/ScheduleDetailView.swift`
- Delete: `Scout/Schedules/NewScheduleSheet.swift`
- Delete: `Scout/Models/Schedule.swift`
- Delete: `ScoutTests/Services/ScheduleEditorServiceTests.swift` (and any related test files exercising the deleted types)

- [ ] **Step 1: Wire `ScheduleEditService` in `AppState.swift`**

Replace the existing `editor: ScheduleEditorService` field + its construction with `ScheduleEditService`. Find the AppState declarations near the top of the file and:

- Remove: `let editor: ScheduleEditorService`
- Add: `let scheduleEditService: ScheduleEditService`

Find the AppState init body, remove the `let editor = ScheduleEditorService(...)` block, and replace with:

```swift
        let canonical = scoutDir
            .appendingPathComponent(".scout-state")
            .appendingPathComponent("schedule.yaml")
        let scheduleEditService = ScheduleEditService(
            scoutctl: scoutctlExe,
            runner: runner,
            canonicalSchedulePath: canonical,
            argumentsPrefix: ["scoutctl"]
        )
        self.scheduleEditService = scheduleEditService
```

The Plan 5 task block that disabled `editor.loadAll()` / `editor.startWatching()` (the multi-line comment) can be removed entirely — those calls don't apply to the new service.

- [ ] **Step 2: Update `MainWindowView.swift`**

Replace the `.environmentObject(appState.scheduleEditorService)` line with:

```swift
            case .schedules:
                SchedulesView()
                    .environmentObject(appState.scheduleEditService)
```

(`SchedulesView` already declares `@EnvironmentObject var appState: AppState` and uses it for `fireNow`; pass it through if not already injected at the parent level — typically the root view wires it once.)

- [ ] **Step 3: Restore Schedules row in `SidebarView.swift`**

Open `Scout/Shell/SidebarView.swift`. Replace the multi-line "Schedules tab hidden in Plan 5" comment block with the active row:

```swift
                sidebarRow(.schedules,     label: "Schedules",      system: "calendar.badge.clock")
```

- [ ] **Step 4: Delete legacy files (with dependency check)**

The kept files (`PlistIO`, `ScheduleDiff`, `ScheduleTriggerFormatter`) MAY depend on the legacy `Models/Schedule.swift` type — they were originally written together. Before deleting `Schedule.swift`, grep for callers:

```bash
cd /Users/jordanburger/scout-app
grep -rn "\bSchedule\b" Scout/Services/PlistIO.swift Scout/Services/ScheduleDiff.swift Scout/Services/ScheduleTriggerFormatter.swift Scout/Services/SystemLaunchctlClient.swift 2>&1 | grep -v "^#" | head -20
```

- **If zero hits in kept files** (only `ScheduleEditorService.swift` and `ScheduleDetailView.swift` referenced it) → safe to delete:
  ```bash
  git rm Scout/Services/ScheduleEditorService.swift
  git rm Scout/Schedules/ScheduleDetailView.swift
  git rm Scout/Schedules/NewScheduleSheet.swift
  git rm Scout/Models/Schedule.swift
  ```

- **If kept files reference `Schedule`** → keep `Models/Schedule.swift` for now; delete only the editor service + detail view + sheet:
  ```bash
  git rm Scout/Services/ScheduleEditorService.swift
  git rm Scout/Schedules/ScheduleDetailView.swift
  git rm Scout/Schedules/NewScheduleSheet.swift
  # Schedule.swift kept — PlistIO/ScheduleDiff still use it.
  ```
  Update the PR description to note `Schedule.swift` was kept; flag for a future "PlistIO redesign" cleanup if Plan 6's reviewer wants a deeper rip.

Then delete legacy test files:

```bash
# Delete any test file exercising the deleted types — adjust as the build reveals.
git rm -f ScoutTests/Services/ScheduleEditorServiceTests.swift 2>/dev/null || true
# ScoutTests/Models/ScheduleTests.swift — only delete if Models/Schedule.swift was deleted.
```

- [ ] **Step 5: Build, fix any references**

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -20
```

Expect compile errors pointing at any callers of the deleted types (e.g., test files referencing `ScheduleEditorService` or `Schedule`). Delete or update them as needed. Common cleanup spots:

- Other test files importing `Scout.Schedule` or `Scout.ScheduleEditorService` → adjust or delete the references.
- `ScoutTests/Services/ScheduleDiffTests.swift` (if it exists) — `ScheduleDiff` is kept; its tests should still work as long as they don't depend on the deleted `Schedule` model. If they do, port the test fixtures to a local stub type.

Iterate until BUILD SUCCEEDED.

- [ ] **Step 6: Run full test suite**

```bash
xcodebuild test -project Scout.xcodeproj -scheme Scout 2>&1 | tail -15
```
Expected: all tests in scope pass; the previous count from Plan 5 (164 passed + 1 pre-existing flake) plus the new SlotTests / ScheduleEditServiceTests / SlotEditFormTests / SlotSummaryRowTests / SchedulesViewTests we've added throughout this plan.

- [ ] **Step 7: Smoke-launch the app**

```bash
osascript -e 'tell application "Scout" to quit' 2>&1 ; sleep 2
open ~/Library/Developer/Xcode/DerivedData/Scout-*/Build/Products/Debug/Scout.app
```

Click around: Control Center → Action Items → Schedules → Settings. Schedules should now show the 10 default slots (loaded via `scoutctl schedule list --json`). Click a slot row → expanded edit form. Hit `+ Add slot` → expanded draft row with `new-slot-1` placeholder. Edit a `cooldown_minutes`, hit Save. Verify the file changed:

```bash
grep -A1 "morning-briefing:" ~/Scout/.scout-state/schedule.yaml | head -5
```

- [ ] **Step 8: Commit**

```bash
git add Scout/Shell/AppState.swift Scout/Shell/SidebarView.swift Scout/Shell/MainWindowView.swift
# Already-staged deletions from `git rm` are included.
git commit -m "feat(app): wire ScheduleEditService into AppState + restore sidebar + delete legacy"
```

---

## Task 15: Scout-app — Optional E2E integration test

**Files:**
- Create: `ScoutTests/Integration/ScheduleEditE2ETest.swift`

This test runs only when `SCOUT_DATA_DIR` is set + points at a real vault. Skipped on CI / fresh checkouts.

- [ ] **Step 1: Create the integration test**

```swift
import XCTest
@testable import Scout

@MainActor
final class ScheduleEditE2ETest: XCTestCase {

    func test_round_trip_edit_and_revert_against_real_vault() async throws {
        guard let vault = ProcessInfo.processInfo.environment["SCOUT_DATA_DIR"] else {
            throw XCTSkip("SCOUT_DATA_DIR not set — opt-in E2E only")
        }

        // Find scoutctl on PATH.
        let scoutctl = URL(fileURLWithPath: "/usr/bin/env")
        let canonical = URL(fileURLWithPath: vault)
            .appendingPathComponent(".scout-state")
            .appendingPathComponent("schedule.yaml")

        let originalText = try String(contentsOf: canonical, encoding: .utf8)

        // Use the production runner (real subprocess). Look up the actual
        // type name in AppState.swift — it's the same `runner: any ProcessRunner`
        // wired into ScheduleService and the new ScheduleEditService.
        // Likely `SystemProcessRunner` or `ProcessRunnerImpl`; grep for
        // "class.*: ProcessRunner" if uncertain.
        let runner: any ProcessRunner = SystemProcessRunner()
        let service = ScheduleEditService(
            scoutctl: scoutctl,
            runner: runner,
            canonicalSchedulePath: canonical,
            argumentsPrefix: ["scoutctl"]
        )

        try await service.loadAll()
        guard let target = service.slots.first(where: { $0.key == "morning-briefing" }) else {
            throw XCTSkip("morning-briefing slot not present in this vault")
        }
        let originalCooldown = target.cooldownMinutes

        // Edit cooldown to a sentinel value, save, reload, verify.
        var bumped = service.slots
        if let idx = bumped.firstIndex(where: { $0.key == "morning-briefing" }) {
            bumped[idx] = Slot(
                key: target.key, type: target.type, runner: target.runner,
                firesAtLocal: target.firesAtLocal, weekdays: target.weekdays,
                missedWindowHours: target.missedWindowHours, onMiss: target.onMiss,
                cooldownMinutes: 999_999,  // sentinel
                budgetUsd: target.budgetUsd, tz: target.tz, runtime: target.runtime
            )
        }
        try await service.save(allSlots: bumped)
        try await service.loadAll()
        XCTAssertEqual(
            service.slots.first(where: { $0.key == "morning-briefing" })?.cooldownMinutes,
            999_999
        )

        // Revert to original.
        var restored = service.slots
        if let idx = restored.firstIndex(where: { $0.key == "morning-briefing" }) {
            restored[idx] = target.with(cooldownMinutes: originalCooldown)
        }
        try await service.save(allSlots: restored)

        // Final assertion: original text restored byte-for-byte? Not byte-for-byte
        // — but cooldown is back to the original value.
        try await service.loadAll()
        XCTAssertEqual(
            service.slots.first(where: { $0.key == "morning-briefing" })?.cooldownMinutes,
            originalCooldown
        )

        // Sanity: header is preserved, file isn't truncated.
        let afterText = try String(contentsOf: canonical, encoding: .utf8)
        XCTAssertTrue(afterText.contains("schema_version: 1"))
        XCTAssertTrue(originalText.split(separator: "\nslots:").first == afterText.split(separator: "\nslots:").first)
    }
}

// Slot.with(...) — small convenience for tests.
extension Slot {
    func with(cooldownMinutes: Int) -> Slot {
        Slot(
            key: key, type: type, runner: runner,
            firesAtLocal: firesAtLocal, weekdays: weekdays,
            missedWindowHours: missedWindowHours, onMiss: onMiss,
            cooldownMinutes: cooldownMinutes,
            budgetUsd: budgetUsd, tz: tz, runtime: runtime
        )
    }
}
```

- [ ] **Step 2: Run the test**

```bash
SCOUT_DATA_DIR=$HOME/Scout xcodebuild test -only-testing:ScoutTests/ScheduleEditE2ETest -project Scout.xcodeproj -scheme Scout 2>&1 | tail -10
```
Expected: passes, leaves `~/Scout/.scout-state/schedule.yaml` with `morning-briefing.cooldown_minutes` at its original value.

- [ ] **Step 3: Commit**

```bash
git add ScoutTests/Integration/ScheduleEditE2ETest.swift
git commit -m "test(app): opt-in E2E test for ScheduleEditService against real vault"
```

---

## Task 16: Scout-app — Push, open PR, merge sequence

- [ ] **Step 1: Push the engine PR (already merged from Task 3)**

The scout-plugin `plan-6-engine` PR should have merged after Task 3 reviewed clean. Confirm `main` is current:

```bash
cd /Users/jordanburger/scout-plugin
git checkout main
git pull --ff-only
```

- [ ] **Step 2: Push scout-app branch**

```bash
cd /Users/jordanburger/scout-app
git push -u origin plan-6-schedules-tab
```

- [ ] **Step 3: Open the scout-app PR**

```bash
gh pr create --title "Plan 6: Schedules tab rewrite — schedule.yaml editor" --body "$(cat <<'EOF'
## Summary

Rebuilds the Schedules tab as a real `~/Scout/.scout-state/schedule.yaml` editor — full CRUD, atomic saves with mtime stale-check, header-comment preservation. Companion to scout-plugin Plan 6 engine PR (already merged).

13 commits, ~XYZ tests, build SUCCEEDED.

### What's new

- `Slot` Swift model — Codable mirror of engine `Slot` dataclass
- `ScheduleEditService` — `loadAll` / `save` / `delete` via `scoutctl schedule list --json` / `validate --target`
- `SlotSummaryRow` / `SlotEditForm` / `SlotRow` — single-column inline-expand UI
- `SchedulesView` rebuilt — empty state, stale-edit banner, single-expansion controller, new-slot flow
- `runtime` field renders in Advanced (Remote disabled tooltip → Plan 7)
- Type-change confirm dialog; delete confirm; save-failure inline banner

### What's deleted

- `ScheduleEditorService.swift` + tests (legacy launchd-plist editor)
- `ScheduleDetailView.swift`, `NewScheduleSheet.swift`
- `Models/Schedule.swift` (legacy launchd-plist `Schedule` type — distinct from engine `Slot`)
- `SchedulesView.legacyBody` + helpers

### What's kept

`PlistIO`, `ScheduleDiff`, `ScheduleTriggerFormatter`, `SystemLaunchctlClient` — still serve heartbeat / schedule-tick plist editing in scoutctl + tests.

## Test plan

- [ ] xcodebuild build clean
- [ ] All ScheduleEditServiceTests / SlotEditFormTests / SlotSummaryRowTests / SchedulesViewTests / SlotTests pass
- [ ] Manual smoke: launch app, edit a slot's cooldown, watch next dispatcher tick honor it
- [ ] Manual smoke: add a new slot via UI, watch it appear in `list-upcoming`, fire at target time
- [ ] Manual smoke: delete a slot, confirm it's gone from the next tick

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Wait for review + merge**

After review approval and merge, sync local main + delete the local branch:

```bash
git checkout main
git pull --ff-only
git branch -d plan-6-schedules-tab
```

- [ ] **Step 5: Final smoke verification (post-merge)**

Rebuild from current main:

```bash
xcodebuild -project Scout.xcodeproj -scheme Scout build 2>&1 | tail -3
osascript -e 'tell application "Scout" to quit' 2>&1 ; sleep 2
open ~/Library/Developer/Xcode/DerivedData/Scout-*/Build/Products/Debug/Scout.app
```

Click around: Schedules tab works, edit roundtrips, dispatcher honors changes. **Plan 6 complete.**
