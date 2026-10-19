# Per-slot model selection for scheduled runs — design

Status: proposed, awaiting Jordan's approval (2026-10-13)
Owner of the decision: Jordan (product behaviour + UI change)

## 1. Goal

Let the user choose which Claude model each scheduled Scout slot runs on, both
in config and in the macOS app's Schedules editor. Today every runner hard-codes
`--model opus` (`plugin/templates/run-scout.sh.tmpl:138`,
`run-dreaming.sh.tmpl:131`, `run-research.sh.tmpl:117`).

Why: the #84 design card measured a scheduled briefing or consolidation at
~176 turns with ~194k average context on Opus. A cheaper model on light slots
(e.g. midday and afternoon consolidations) may save more than trimming
instructions would. It is a quality trade-off, so the user decides, slot by
slot, and can measure it first (§8).

**Non-goals.** Scout does not pick models automatically, does not change any
default, and does not route models per phase within a run. The SKILL.md/prompt
slicing belongs to #84 Phase A; this work touches only the model argument.

## 2. Decisions for Jordan

Each has a recommendation. §3–§9 assume the recommendations.

| # | Question | Recommendation |
|---|---|---|
| D1 | Where does the per-slot choice live? | An optional `model:` on the slot in the vault's `schedule.yaml`. |
| D2 | Is there a global default? | Yes: `models.default` in `scout-config.yaml`. It covers slots without `model:` and launches that have no slot. |
| D3 | Is there also a per-type default (`models.by_type.consolidation`)? | **Not in v1** (YAGNI). The app edits per slot, and a type-level layer is a third precedence level users must reason about. It can be added later without breaking anything. |
| D4 | Do defaults change? | **No.** Built-in fallback stays `opus`; no seeded slot gets a `model:`. Nothing about quality changes until the user picks a model. |
| D5 | Does `/scout-setup` ask? | **No.** Setup is already long. Document it, and point to the app and `scoutctl model` instead. |

## 3. Data model and precedence

### Schedule slot (`plugin/engine/scout/schedule.py::Slot`)

Add `model: str | None = None`. It is optional, so absent means "inherit".

```yaml
  midday-consolidation:
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "13:00"
    ...
    model: sonnet        # optional
```

### Config (`scout-config.yaml`)

```yaml
models:
  default: opus          # used by slots without `model:` and by slotless launches
```

The packaged `defaults/scout-config.yaml` gains this block with `opus`.

### Precedence (first match wins)

1. `SCOUT_MODEL` env var, if set (manual/debug override; nothing in Scout sets
   it before the runner resolves).
2. The slot's `model:`, when the run's `SCOUT_FORCE_MODE` names a slot that has one.
3. `models.default` from `scout-config.yaml`.
4. Built-in `opus`.

Slotless launches skip step 2. These are heartbeat-launched dreaming and
research runs, and `/scout-briefing`, `/scout-dream` and similar launches
started with `nohup`.

### Allowed values

- **Aliases:** `opus`, `sonnet`, `haiku`. These are the stable Claude Code
  aliases, which always track the latest model in each family.
- **Full model IDs:** anything matching `^claude-[a-z0-9][a-z0-9.\-]*$` (for
  example `claude-sonnet-5-5`), for users who want to pin a version. These are
  syntax-checked only; Scout keeps no model catalogue to go stale.
- **Everything else is rejected** with an error that names the slot (or config
  key), the bad value, and the allowed forms. For example:
  `schedule.yaml: slot 'midday-consolidation': model 'sonet' is not opus, sonnet, haiku or a claude-… model ID`.
- Comparison is case-insensitive, and values are normalised to lower case.

One validator, `scout.models.validate_model()`, is shared by the schedule
loader, the config loader and the `scoutctl model` commands.

## 4. Engine changes

1. **`scout/models.py` (new, small).** It holds `ALIASES`, `validate_model()`,
   and `resolve_model(vault, slot_key) -> Resolved(model, source)`, where
   `source` is one of `env | slot | config | builtin`.
2. **`schedule.py`.** `_build_slot` reads and validates `model`. `schedule list
   --json` and `schedule show` include it (as `null` when absent).
3. **`scoutctl model` subcommands:**
   - `scoutctl model resolve [--slot KEY] [--json]` prints the model and source.
     The runners call this.
   - `scoutctl model show [--json]` prints the default and every slot's
     effective model with its source. The app and `/scout-status` use it.
   - `scoutctl model set-default <model>` writes `models.default` through the
     same config-writing path `scoutctl budget set` uses. That keeps the app's
     rule that it has no YAML knowledge of `scout-config.yaml`.
4. **Snapshot is unchanged.** `schedule_snapshot` emits a fixed field subset,
   and the seeded defaults carry no `model:`. So the canonical
   `schedule.snapshot.json` and its app copy do not change, and `contract.yml`
   sees no churn.
5. **`scoutctl health` / doctor are unchanged in v1.** `health._parse_row`
   ignores extra keys, so the new ledger column is safe. Surfacing the model in
   `RunHealth` would regenerate the `doctor-run-health.json` contract fixture;
   that is deferred until someone needs it.

## 5. Runner plumbing

In all three runner templates, after the lock and before the `claude` call,
the runner resolves the model:

```bash
# Model for this run — the slot's `model:`, else scout-config's models.default,
# else opus. A resolver failure must not block the run: fall back to opus, the
# behaviour before per-slot models, and say so in the log.
SCOUT_MODEL="${SCOUT_MODEL:-$("$SCOUTCTL_BIN" model resolve --slot "$MODE" 2>>"$LOG_FILE" || true)}"
if [ -z "$SCOUT_MODEL" ]; then
    echo "=== model resolve failed — falling back to opus ===" >> "$LOG_FILE"
    SCOUT_MODEL=opus
fi
export SCOUT_MODEL
echo "=== model: $SCOUT_MODEL ===" >> "$LOG_FILE"
```

The `claude` call then passes `--model "$SCOUT_MODEL"` as two separate
arguments. The eval shim strips `--model <value>` pairs
(`claude_shim.py:53`), so the eval harness keeps working.

- **Why the runner resolves the model, not the dispatcher.** Slotless launches
  (heartbeat, skills) never pass through `schedule_tick._spawn_runner`, so the
  runner must resolve anyway. Doing it in one place gives one code path.
  `resolve` is an ordinary `scoutctl` call, the same kind the runners already
  make for `connector-health-report`.
- **Why opus on failure.** A resolver error, such as a vault edited into an
  invalid state, should not stop the run, and the safe direction is the
  pre-change behaviour. It is not silent: the run log says so. The app's save
  path and `schedule validate` already reject invalid values before they land.
- **No new template variable.** That keeps the change clear of
  `phase_backport`'s risky-variable handling and the pinned-variable drift check.
- `claude-with-retry.sh.tmpl:272` is cosmetic help text. It changes to
  `--model ${SCOUT_MODEL:-opus}`.
- **Upgrade path.** Runner templates are managed files, so `/scout-update`
  rolls them out through `vault_drift.reconcile`. If a user hand-edited the
  model line, the three-way merge keeps their edit or parks a drift file, as
  for any other managed-file change.
- **Conflict with #84.** The hunk sits beside the `claude` invocation and
  touches only the `--model` line plus the resolve block. The `PROMPT` text and
  the brain-file handling are left alone.

## 6. Ledgers — attributing cost to a model

- **`run-outcome.sh` (`.scout-logs/run-outcomes.jsonl`).** Each row gains
  `"model": $SCOUT_MODEL`, which is omitted when unset, for example in rows
  from an old runner. This is the column health and eval analysis join on.
- **`write-session-cost.sh` (`usage-tracker.jsonl`).** The runner-fallback row
  gains an optional `model`. `budget_check.py` reads only named keys, so it is
  unaffected.
- **`session-tokens.jsonl`** already records the model actually observed per
  session (`primary_model`), plus per-model pricing. It needs no change. With
  run-outcomes recording the *requested* model, a mismatch between the two
  becomes detectable.
- **Budget checks.** `--max-budget-usd {{MAX_BUDGET}}` and the daily budget are
  unchanged. A cheaper model just spends less against the same caps.

## 7. macOS app

**Write path.** The app already writes `schedule.yaml` itself
(`ScheduleEditService.save`: stale check, tmp file, `scoutctl schedule
validate --target`, atomic swap). The per-slot model follows that existing
path. The global default goes through `scoutctl model set-default`, mirroring
`BudgetSettingsService`.

**Data-loss hazard, which sets the PR order.** `serializeSlotsToYAMLBody`
re-emits a fixed field list. Any `model:` the engine accepts would be
**silently dropped** the next time a user saves any slot in the app. So
round-tripping `model` in the app ships in the **same commit** as the engine
field (§10, PR 1).

**Changes:**

1. `Slot.swift`: a `model: String?` field, decoded with `decodeIfPresent`,
   emitted by the serializer only when non-nil. `SlotDraft` carries it.
   `makeNewDraftSlot` sets it to nil.
2. `SlotEditForm` gets a "Model" row (an `EditorialSegmentedControl`, like
   "On miss") with these options:
   - **Default (opus)**: the label shows the resolved default.
   - **Opus**
   - **Sonnet**
   - **Haiku**

   A full model ID set by hand in YAML shows as a fourth, read-only "Custom:
   claude-…" option and is preserved unless the user picks another. A one-line
   caption sits under the picker: "Cheaper models use less of your plan; test a
   slot on Sonnet before switching (see Docs)."
3. The Schedules table gains a narrow MODEL column, shown only when any slot
   overrides the default. Otherwise the table stays as it is today, and
   `SchedulesColumns` chrome math is updated. Cards show a small model tag when
   the slot overrides.
4. Settings gets a "Default model" picker next to Budget, backed by `scoutctl
   model show --json` / `set-default`.
5. Run detail `MetadataTab` gets a `model` row, read from the run-outcomes row
   when present. (The RunRow list stays unchanged in v1.)

## 8. Measuring quality before switching (eval)

The harness default is already Sonnet, with `--opus` to switch. But `compare`
compares **refs** on one model, and `compare_arms` refuses mixed models. The
proposal is a small harness addition:

- `scripts/eval.sh compare-models <scenario…> [--a opus] [--b sonnet] [--reps N]`
  runs both arms on the working tree, interleaved, and reports per-check
  deltas plus turn and cost totals from the result dirs. It reuses
  `run_sample` and the existing check scorers. Only the "same model" guard is
  bypassed for this command.
- **Scenarios.** `busy-weekday` exercises a consolidation-like load. A light
  midday-consolidation scenario (few new inputs) is the realistic target for
  "can this slot run on Sonnet". It would be added as a scenario seed in the
  same PR.
- **Recommended procedure for Jordan:**
  1. Run `compare-models busy-weekday midday-light --reps 3` (12 sessions,
     which needs `--many`).
  2. If no check regresses, set that slot to Sonnet in the app.
  3. Watch `run-outcomes`/`session-tokens` for a week.
- **Timing.** Tier-0 (no-model) tests only until **2026-10-15 14:00
  Europe/Prague**, when Jordan's weekly limit resets. No live eval runs are
  part of this work.

## 9. Setup and docs

- `/scout-setup` and `scoutctl setup` are unchanged (D5).
- The plugin README / schedule docs get a "Choosing a model per slot" section
  covering precedence, allowed values, the app picker, `scoutctl model`, and
  the eval procedure.
- `/scout-status` shows each slot's effective model (via `model show`).

## 10. Delivery: small PRs, in this order

1. **Engine + app round-trip (one commit, cross-cutting rule).**
   - `scout/models.py`, the `Slot.model` field and validation, `scoutctl model
     resolve|show|set-default`, `models.default` in the defaults config, the
     runner templates, the `run-outcome` and `write-session-cost` columns, and
     the retry help text.
   - App side: `Slot.swift`/`SlotDraft`/serializer round-trip `model`, with no UI.
   - Tests: engine unit tests (validator, precedence, schedule loader, CLI,
     runner template rendering, run-outcome row) and app `SlotTests`,
     `ScheduleYAMLEmissionTests`, `ScheduleEditServiceTests`.
   - CHANGELOG `[Unreleased]` entry.
2. **App UI.** The slot picker, table column and card tag, the Settings
   default picker, and the run-detail model row. Tests: `SlotEditFormTests`,
   `SchedulesViewTests`, a new `ModelSettingsService` test, plus smoke tests.
   CHANGELOG entry.
3. **Eval `compare-models` + light-consolidation scenario.** Tier-0 tests only.
   CHANGELOG entry.
4. **Docs** (it can fold into PR 1 if small).

PRs 1 and 2 must both land before the next release cut, so a release never
ships engine support without the picker. If release timing forces it, PR 1
alone is still safe, because the app round-trips the field.

**Testing.** Locally, run only the affected test files (engine pytest
selections, plus `xcodebuild -only-testing` for the touched app suites). CI is
the gate. Nothing touches real launchd, the real crontab, the Keychain, or
`~/Scout`. Tests render runners into temp vaults with a stub `claude`.

## 11. Risks

- **A wrong model ID in a hand-edited YAML** is caught by `schedule validate`
  (which the app runs before saving), and at runtime the run falls back to opus
  with a log line. `scoutctl health` does not flag it in v1. A doctor check
  could be added later.
- **The Claude CLI may change its aliases.** The alias set is three words in
  one module, and full IDs pass through unchanged.
- **Lower-quality output on a cheap model** is the user's choice. §8 gives a way
  to measure it, and nothing changes by default.
