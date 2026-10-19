# Per-slot model selection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each scheduled Scout slot can run on a model the user chooses (per-slot `model:` in `schedule.yaml`, global `models.default` in `scout-config.yaml`), set in config and in the macOS app.

**Architecture:** One engine module (`scout/models.py`) validates and resolves the model. The three runner templates ask `scoutctl model resolve --slot "$MODE"` at run time and pass `--model "$SCOUT_MODEL"`. The ledgers record the model. The app round-trips `model` in its own `schedule.yaml` writer (PR 1) and gains pickers (PR 2).

**Tech Stack:** Python 3.12 engine (typer, pyyaml, pytest), bash runner templates, SwiftUI app (XCTest / Swift Testing).

**Spec:** `docs/superpowers/specs/2026-10-13-slot-model-selection-design.md` (approved by Jordan 2026-10-13, plus a Settings default-model picker in PR 2).

## Global Constraints

- Precedence: `SCOUT_MODEL` env > slot `model:` > `models.default` > `opus`.
- Accepted values: `opus`, `sonnet`, `haiku` (case-insensitive, normalised to lower case) or a full ID matching `^claude-[a-z0-9][a-z0-9.\-]*$`. Anything else → `ConfigError` naming where, the value, and the allowed forms.
- No default changes: built-in fallback `opus`; seeded schedule has no `model:`; packaged config `models.default: opus`.
- Runner: a resolve failure → `opus` + a log line. Never block a run.
- No new template variable. `schedule.snapshot.json` unchanged.
- The app never edits `scout-config.yaml`; the default model is written by `scoutctl model set-default`.
- Engine + app round-trip of `model` land in ONE commit (cross-cutting rule).
- Local verification: affected tests only; CI is the gate. No live evals before 2026-10-15 14:00 Europe/Prague. Never touch `~/Scout`, `~/scout-plugin`, launchd, crontab, Keychain.
- Fixtures anonymised (no vault content).

## Review Focus

1. A slot whose `model:` is invalid makes the whole schedule fail to load → dispatcher and `scoutctl health` break. Expected: `schedule validate` rejects it with a clear message; the runner still runs on opus. (Task 1 test: loader error message; Task 4 test: runner falls back.)
2. App saves a slot from a vault where another slot has `model: sonnet` → expected: that `model:` survives. (Task 6 emission test.)
3. A hand-written full ID with capitals (`Claude-Sonnet-5-5`) → normalised, accepted. (Task 1.)
4. `SCOUT_MODEL` containing a quote or spaces reaching the JSON ledger lines → must not corrupt `usage-tracker.jsonl`. (Task 5: unsafe values are omitted.)
5. `scout-config.yaml` with no `models:` block and a comment-rich file → `set-default` appends the block and keeps every other byte. (Task 3.)

---

## PR 1 — engine, runners, ledger, app round-trip (one branch `feat/slot-model-engine`)

### Task 1: `scout/models.py` — validator + resolver; `Slot.model`

**Files:**
- Create: `plugin/engine/scout/models.py`
- Modify: `plugin/engine/scout/schedule.py` (`Slot`, `_build_slot`)
- Modify: `plugin/engine/scout/defaults/scout-config.yaml` (add `models:` block)
- Test: `plugin/engine/tests/unit/test_models.py`, `plugin/engine/tests/unit/test_schedule.py` (append)

**Interfaces — Produces:**
- `ALIASES: tuple[str, ...] = ("opus", "sonnet", "haiku")`, `BUILTIN_DEFAULT = "opus"`
- `validate_model(value: object, *, where: str) -> str` (raises `ConfigError`)
- `@dataclass(frozen=True) class ResolvedModel: model: str; source: str` (`env|slot|config|builtin`)
- `resolve_model(*, slot_key: str | None, data_dir: Path | None = None, environ: Mapping[str, str] | None = None) -> ResolvedModel`
- `default_model(data_dir: Path | None = None) -> ResolvedModel` (config or builtin)
- `Slot.model: str | None = None`

- [ ] Step 1: tests — aliases accepted & lower-cased; full IDs accepted; `sonet`, `gpt-4`, `""`, `3` rejected with `where` and value in message; precedence matrix (env > slot > config > builtin) using a tmp vault with `.scout-state/schedule.yaml` and `scout-config.yaml`; unknown slot key skips slot layer; invalid config default raises; `_build_slot` reads `model` and rejects bad values with `slot <key>` in the message.
- [ ] Step 2: run `pytest tests/unit/test_models.py -q` → fails (module missing).
- [ ] Step 3: implement (code below).
- [ ] Step 4: run the two test files → pass.
- [ ] Step 5: commit.

```python
# scout/models.py
ALIASES = ("opus", "sonnet", "haiku")
BUILTIN_DEFAULT = "opus"
_FULL_ID_RE = re.compile(r"^claude-[a-z0-9][a-z0-9.\-]*$")

def validate_model(value, *, where):
    if isinstance(value, str) and (v := value.strip().lower()) and (v in ALIASES or _FULL_ID_RE.match(v)):
        return v
    raise ConfigError(f"{where}: model {value!r} is not {', '.join(ALIASES[:-1])} or {ALIASES[-1]}, "
                      "or a full claude-… model ID")
```

`resolve_model` loads the vault schedule via `scout.schedule.load_schedule(<data_dir>/.scout-state/schedule.yaml)` (fallback `load_default_schedule()` when absent) and config via `scout.config.load_config(data_dir)["models"]["default"]`.

### Task 2: `scoutctl model resolve|show`; `schedule list/show` emit `model`

**Files:** Modify `plugin/engine/scout/cli.py` (new `_register_model`, schedule list/show records). Test: `plugin/engine/tests/unit/test_cli_model.py`.

- `scoutctl model resolve [--slot KEY] [--json]` → stdout `sonnet` (or JSON `{"model","source"}`); on `ConfigError` print `error: …` to stderr, exit 1.
- `scoutctl model show [--json]` → JSON `{"default": {"model","source"}, "slots": [{"key","model","source"}], "aliases": [...]}`; slots sorted by key.
- `schedule list --json` and `schedule show` gain `"model": slot.model`.

TDD with `typer.testing.CliRunner`, `SCOUT_DATA_DIR` pointed at a tmp vault (pattern: `tests/unit/test_cli_health.py`).

### Task 3: `scoutctl model set-default` (comment-preserving)

**Files:** Create `plugin/engine/scout/scripts/models_config.py`; Modify `plugin/engine/scout/scripts/auto_update_config.py` only to import the shared line-edit helpers if extracted (keep its public API and tests unchanged). Test: `plugin/engine/tests/unit/test_models_config.py`.

- `apply_models_default(text: str, model: str) -> str` — pure; validates; edits `models.default` in place keeping comments, appends a commented `models:` block when absent, idempotent; raises `ModelsWriteError` on unparseable / duplicated block.
- `write_models_default(model: str, *, data_dir=None) -> dict` — `{config_path, default, changed}`; same mtime-guarded atomic replace as `write_auto_update`; refuses when no `scout-config.yaml`.
- Tests: byte preservation of a comment-rich file, append case, idempotence, invalid model, duplicate block.

### Task 4: Runner templates resolve and pass the model

**Files:** Modify `plugin/templates/run-scout.sh.tmpl`, `run-dreaming.sh.tmpl`, `run-research.sh.tmpl`, `plugin/templates/scripts/claude-with-retry.sh.tmpl:272`. Test: `plugin/engine/tests/integration/test_runner_model.py`.

Insert after the budget check, before `START_TIME`:

```bash
# Model for this run: SCOUT_MODEL, else the slot's `model:`, else
# scout-config's models.default, else opus — resolved by the engine. A resolver
# failure never blocks the run: it runs on opus, the default, and says so here.
SCOUT_MODEL="$("$SCOUTCTL_BIN" model resolve --slot "$MODE" 2>>"$LOG_FILE" || true)"
if [ -z "$SCOUT_MODEL" ]; then
    echo "=== model resolve failed — running on opus ===" >> "$LOG_FILE"
    SCOUT_MODEL=opus
fi
export SCOUT_MODEL
echo "=== model: $SCOUT_MODEL ===" >> "$LOG_FILE"
```

and `--model opus \` → `--model "$SCOUT_MODEL" \`.

Test (pattern: `tests/integration/test_run_scout_mode_export.py`): render each runner with a stub `claude-with-retry.sh` that records its argv; a stub `scoutctl` script that prints `sonnet` → argv contains `--model sonnet`; nonexistent scoutctl → `--model opus` and the log has "model resolve failed"; `SCOUT_MODEL` is exported to the child.

### Task 5: Ledger columns

**Files:** Modify `plugin/templates/scripts/run-outcome.sh.tmpl` (row), `plugin/templates/scripts/write-session-cost.sh.tmpl`. Tests: append to `plugin/engine/tests/integration/test_run_outcome.py`; new `plugin/engine/tests/integration/test_write_session_cost_model.py`.

- run-outcome: `model = os.environ.get("SCOUT_MODEL", "").strip().lower()`; add `row["model"]` only when it matches `^[a-z0-9][a-z0-9.\-]*$`.
- write-session-cost: `MODEL_FIELD=""`; when `$SCOUT_MODEL` matches `^[a-z0-9][a-z0-9.-]*$` append `,"model":"$SCOUT_MODEL"` before `}`.
- Tests: model recorded; absent when unset; a value with `"` is omitted and the line is valid JSON.

### Task 6: App round-trip of `model` (same commit as Tasks 1–5)

**Files:** Modify `apps/macos/Scout/Models/Slot.swift`, `apps/macos/Scout/Schedules/SlotDraft.swift`, `apps/macos/Scout/Services/ScheduleEditService.swift` (`serializeSlotsToYAMLBody`). Tests: `apps/macos/ScoutTests/Models/SlotTests.swift`, `apps/macos/ScoutTests/Services/ScheduleYAMLEmissionTests.swift`.

- `Slot.model: String?` (`case model`), `decodeIfPresent`, memberwise default `nil`.
- `SlotDraft.model: String?`, carried by `init(from:)` / `toSlot()`.
- Emitter: `if let m = slot.model { out += "    model: \(yamlScalar(m))\n" }` after `tz`.
- Tests: decoding `"model": "sonnet"` and absent/`null`; emission includes `model: sonnet` only when set; a draft round-trip keeps it.

### Task 7: Docs + CHANGELOG, PR

- `plugin/README.md` near line 416: "Choosing a model per slot" (precedence, values, `scoutctl model`, app picker coming).
- `plugin/CHANGELOG.md` and `apps/macos/CHANGELOG.md` `[Unreleased]` → `### Added`.
- Verify: `ruff`, `mypy scout`, affected pytest files, `xcodebuild … -only-testing:ScoutTests/SlotTests -only-testing:ScoutTests/ScheduleYAMLEmissionTests -only-testing:ScoutTests/ScheduleEditServiceTests`.
- Open PR, send to coordinator.

## PR 2 — App UI (branch `feat/slot-model-app-ui`, on top of PR 1)

### Task 8: `ModelSettingsService` (+ Settings section)
- `apps/macos/Scout/Services/ModelSettingsService.swift`: `load()` runs `scoutctl model show --json` (decodes default + slots); `setDefault(_:)` runs `scoutctl model set-default <m> --json`. Mirrors `BudgetSettingsService` (ProcessRunner, argsPrefix).
- `apps/macos/Scout/Shell/ModelSettingsSection.swift` next to `BudgetSettingsSection`: picker Opus/Sonnet/Haiku (+ read-only Custom when a full ID is set), caption on quality/cost.
- Tests: `ScoutTests/Services/ModelSettingsServiceTests.swift` (argument shape, decode, error surfacing) with `QueueProcessRunner`.

### Task 9: Slot picker in `SlotEditForm`
- Model row (EditorialSegmentedControl): Default (<resolved default>) / Opus / Sonnet / Haiku; a full ID shows as a read-only "Custom: …" option. Binding maps "Default" ↔ `nil`.
- `SlotModelChoice` enum in `SlotDraft.swift` with `init(model:)` / `model` for testability.
- Tests: `SlotEditFormTests` (choice mapping, dirty when changed), `SchedulesViewTests` (new slot default `nil`).

### Task 10: Table column, card tag, run detail
- MODEL column only when any slot has `model != nil` (`SchedulesColumns` chrome math updated); `SlotCard` tag when overridden.
- `MetadataTab` shows the run's model, read from `run-outcomes.jsonl` (`model` key) when present.
- Tests: column visibility predicate; metadata row presence.
- CHANGELOG app `[Unreleased]`. PR, coordinator.

## PR 3 — Eval `compare-models` (branch `feat/eval-compare-models`)

### Task 11
- `plugin/engine/tests/eval/harness/cli.py`: `compare-models <scenario…> --a opus --b sonnet --reps N` — both arms on the working tree, interleaved, via `run_sample(..., model=…)`; print per-check deltas and turn/cost totals via a new `format_model_comparison`.
- New scenario `midday-light` (light consolidation load), anonymised.
- Tier-0 tests with the stand-in `--claude` binary (pattern: existing `test_eval_*` tests). No model runs.
- CHANGELOG, PR, coordinator.
