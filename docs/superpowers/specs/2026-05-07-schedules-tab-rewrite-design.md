# Plan 6 — Schedules Tab Rewrite

**Spec status:** draft 2026-05-07. Brainstormed via `superpowers:brainstorming` against the Plan 5 → Plan 6 carryforward note in `docs/superpowers/FOLLOWUPS.md`.

## 1. Position in the unification arc

| Plan | What it built | What's left for Plan 6 |
|---|---|---|
| Plan 4 | Connector subsystem + hooks port | — |
| Plan 5 | Schedule v2 (yaml-driven dispatcher), mode-name rename, `ScheduleService` + `PowerStateService` in scout-app, removed `RunnerService`/`LaunchdScheduleService` | Schedules tab placeholder is in place but unusable |
| **Plan 6** | Real Schedules tab editing `schedule.yaml` end-to-end | — |

Plan 5 collapsed 8 per-slot launchd plists into a single `com.scout.schedule-tick.plist` driven by `~/Scout/.scout-state/schedule.yaml`. The pre-existing Schedules tab — `ScheduleEditorService` editing per-slot plists via `PlistIO` — was hidden behind a placeholder because its data model no longer matched reality. Plan 6 replaces it with a `schedule.yaml` editor.

Plan 6 is **UI-only**. No new engine semantics; one tiny additive engine field (`Slot.runtime`) reserved as a Plan 7 forward-compat hook.

## 2. Goals & non-goals

**Goals**

1. Full CRUD on `~/Scout/.scout-state/schedule.yaml` slots: add, edit, delete.
2. Native macOS look and feel; matches scout-app's existing patterns (`ScheduleService`, `ConnectorHealthService`, etc.).
3. Read + validate via existing `scoutctl schedule {list,show,validate}` (one tiny additive flag — `validate --target <path>` — see §10).
4. Atomic, race-safe saves (dispatcher's tick can read at any time).
5. Forward-compat schema reservation for Plan 7's remote-execution work without implementing it.

**Non-goals (explicit)**

- Heartbeat / schedule-tick plist editing (still terminal-only via `scoutctl schedule install-plist`, `install-wake-schedule`).
- Two-way sync with Anthropic routines (Plan 7).
- Overlay-file editing (`schedule.local.yaml`) — direct canonical edit only.
- Field-mutability for `slot_key` (immutable post-creation; rename = delete + recreate).
- Background polling / FS watcher in the editor — explicit reload after writes is enough.

## 3. Architecture & data path

```
~/Scout/.scout-state/schedule.yaml   ← single source of truth
       ↑                  ↑
       │ atomic-rename    │ read
       │ on Save          │
   ┌───┴────┐          ┌──┴──────────────────┐
   │ scout- │          │ scoutctl schedule   │
   │ app    │          │ list-upcoming/list/ │
   │ (UI)   │ ←──────  │ show/validate       │
   └────────┘          │ tick (every 5 min)  │
                       └─────────────────────┘
```

**Read path (scout-app → engine).** A new `ScheduleEditService` shells out to `scoutctl schedule list --json` on tab appear and after every successful save. JSON shape is the existing one — slot records as emitted by `cli.py:cli_schedule_list`. No new endpoint.

**Write path (scout-app → file).** `ScheduleEditService.save([Slot])` composes the entire YAML, writes to a temp file in the same directory, runs `scoutctl schedule validate --target <tmpfile>`, atomic-renames onto canonical, then re-reads via `scoutctl schedule list --json`. Atomic rename means the dispatcher's 5-minute tick reads either the pre-edit or post-edit file, never partial.

**`validate --target` flag** (additive engine change, lands in the Plan 6 engine PR). Today's `scoutctl schedule validate` reads from `~/Scout/.scout-state/schedule.yaml` or engine defaults; the new flag accepts an optional path argument so the editor can validate a candidate before committing. Implementation is two lines wrapping the existing `load_schedule(path)` call. Default behavior (no `--target`) is unchanged.

**Why not a YAML-edit subcommand in scoutctl?** Considered, rejected. Rebuilding YAML from a Swift Slot model risks losing header comments and per-slot blank-line spacing. The Swift side serializes via `Yams` (already a scout-app dep for `ConnectorHealthService`) and round-trips comments by reading raw text → patching the slot dict → splicing the rewritten slot blocks back into the original byte stream.

## 4. UI structure

**Layout: single column with inline expand** (rejected: master/detail and modal-sheet alternatives during brainstorming — see decision log §13).

```
SchedulesView (root)
├── Toolbar: "+ Add slot"
├── List(slots)              ← @Published from ScheduleEditService
│   └── ForEach(slot) → SlotRow
│       ├── collapsed: SlotSummaryRow (default)
│       │     • slot_key (mono) · type chip · "HH:MM <weekdays>"
│       │     • next-fire (from list-upcoming if cached)
│       │     • inline "Fire now" button (disabled when row has unsaved draft)
│       │     • chevron ▸
│       └── expanded: SlotEditForm
│             • CommonFieldsSection
│             • AdvancedDisclosure (default-collapsed)
│             • inline ValidationErrorBanner
│             • action bar: Revert · Delete · Fire now · Save
└── alert("Save failed: …") for engine validation errors
```

Single-expansion model: at most one slot expanded at a time, controlled by `@State expandedSlotKey: String?` lifted to `SchedulesView`. Tapping a different row collapses the previous one. If a draft is dirty, switching prompts "Discard unsaved changes to X?".

### 4.1 New Swift files

- `Scout/Schedules/SchedulesView.swift` — replace placeholder body
- `Scout/Schedules/SlotRow.swift` — drives expand state
- `Scout/Schedules/SlotEditForm.swift` — the inline form
- `Scout/Schedules/SlotSummaryRow.swift` — collapsed summary
- `Scout/Services/ScheduleEditService.swift` — read/write/validate via scoutctl
- `Scout/Models/Slot.swift` — Swift mirror of the engine `Slot` (Codable from `scoutctl schedule list --json`)

### 4.2 Files to delete in this PR

- `Scout/Services/ScheduleEditorService.swift`
- `Scout/Schedules/ScheduleDetailView.swift`
- `Scout/Schedules/NewScheduleSheet.swift`
- `Scout/Models/Schedule.swift` (the launchd-plist `Schedule` type, distinct from engine `Slot`)
- `SchedulesView.legacyBody` + private helpers (`list`, `commitErrorBanner`, `statusDot`)
- The Plan-5 placeholder body
- `AppState.scheduleEditorService` (unused since Plan 5)

### 4.3 Files kept

- `Scout/Services/PlistIO.swift`
- `Scout/Services/ScheduleDiff.swift`
- `Scout/Services/ScheduleTriggerFormatter.swift`
- `Scout/Services/SystemLaunchctlClient.swift`

These still serve heartbeat / schedule-tick plist editing inside scoutctl + tests, even if scout-app no longer surfaces them.

### 4.4 Sidebar

`SidebarView.swift` — un-comment the `.schedules` row that Plan 5 hid. The `SidebarItem.schedules` enum case stayed in for state-restore compat; routing in `MainWindowView.swift` already points at `SchedulesView`.

## 5. Edit form spec

### 5.1 Field grouping

```
Common (always visible)
├── slot_key       — mono chip with lock icon (immutable post-creation)
├── Time           — HH:MM picker (text field with regex validation)
├── Weekdays       — 7 toggleable chips: Mon Tue Wed Thu Fri Sat Sun
├── On-miss        — segmented picker: Fire | Skip | Collapse
└── Cooldown       — minutes stepper (15 / 30 / 60 / 90 / 120 / 180 / 240)

Advanced (collapsible, default-collapsed)
├── Runner         — picker (run-scout.sh / run-dreaming.sh / run-research.sh) + custom path
├── Missed window  — hours stepper (1–12)
├── Budget         — optional USD float; empty = no budget cap
├── Timezone       — optional IANA picker; empty = system local (the common case)
├── Type           — segmented: Briefing | Consolidation | Dreaming | Research | Manual
│                    (changes show a confirm dialog: "Changes connector requirements + priority order")
└── Runtime        — segmented: Local | Remote (Plan 7)
                     (Remote disabled with tooltip in Plan 6; field present for forward compat — see §8)
```

`type` lives in Advanced because changing it post-creation has downstream consequences (`connectors.yaml required_in_types` filtering, `SlotPriority` ordering for single-fire-per-tick); the user should commit deliberately, not accidentally tap.

### 5.2 Mutability rules

- **`slot_key`:** immutable post-creation. Rename = delete + create. Editable in the Add-slot draft state only.
- **`type`:** editable post-creation, with a confirm-on-Save dialog naming the consequences.
- **All other fields:** freely editable.

### 5.3 Validation — two layers

**Live (per-field, on commit).** `SlotEditForm` runs Swift-side shape rules per field:
- `slot_key`: kebab-case regex (`^[a-z][a-z0-9-]*$`), uniqueness check against current `[Slot]`.
- `fires_at_local`: HH:MM regex, hour 0–23, minute 0–59.
- `weekdays`: at least one selected.
- `on_miss`: enum membership.
- `cooldown_minutes`, `missed_window_hours`: positive integer in their stepper bounds.
- `runner`: non-empty string.

Errors render inline next to the field in red. Save button disabled if any live errors present.

**On Save (whole-form).** Compose candidate YAML to a tmpfile, run `scoutctl schedule validate --target <tmpfile>`, parse exit code + stderr.
- exit 0 → atomic rename onto canonical.
- exit ≠ 0 → surface engine's stderr text in a red banner at the top of the expanded form. Save button re-enables for retry. Draft is **not** discarded.

### 5.4 Drafts

- `@State draft: SlotDraft` in `SlotEditForm`, initialized from the live `Slot`.
- `Save` enabled only when `draft != live` AND no live validation errors.
- `Revert` discards `draft`, re-initializes from `live`.
- Switching the expanded slot while a draft is dirty → confirm dialog ("Discard unsaved changes to X?").

## 6. New-slot + delete flows

### 6.1 New slot

Click `+ Add slot` in the toolbar. A draft row appears at the top of the list, auto-expanded, prefilled with safe defaults:

```
key:           "new-slot-1"          ← editable; auto-bumps to -2, -3 on collision
type:          briefing
runtime:       local
runner:        run-scout.sh
fires_at_local: "09:00"
weekdays:      [Mon, Tue, Wed, Thu, Fri]
on_miss:       fire
cooldown_minutes: 60
missed_window_hours: 4
```

The `slot_key` field is **editable in this draft state only**, with a hint underneath: *"Slot keys are immutable after first save. Choose carefully."*

`Save` is disabled until: `slot_key` is a valid kebab-case identifier, doesn't collide with existing keys, and all required fields pass live validation. Click `Save` → atomic-rename happens → key locks → the row sorts into its alphabetical position with the others.

`Revert` on a draft slot deletes it from the in-memory list (it never hit disk).

If the user clicks `+ Add slot` while a draft already exists at the top, just expand the existing draft instead of creating a second one.

### 6.2 Delete

`Delete` in the action bar of an expanded row → confirmation alert:

> *Delete `morning-briefing`? This removes it from `schedule.yaml`. Tracker history (last-fire timestamp) is retained but unused. Run-event logs keep their references.*

Confirm → write the new YAML without the slot → atomic-rename → reload the list.

### 6.3 Tracker hygiene

Don't auto-delete the slot's tracker entry on slot delete. Preserves the audit trail. The dispatcher already ignores tracker entries for slots not in the current `Schedule`, so leaving them is harmless. Useful for "show me when this slot last fired even after I deleted it" surfaces in future plans.

## 7. Save model & concurrency

### 7.1 Atomic write algorithm

```
1. ScheduleEditService.save(allSlots: [Slot])
2. Compose new YAML text via Yams + raw-file-merge to preserve header comments
3. Write to tmpfile in same directory: schedule.yaml.<uuid>.tmp
4. Run scoutctl schedule validate --target <tmpfile>
   ├── exit 0  → continue
   └── exit !0 → throw with stderr text, leave canonical untouched
5. Atomic rename tmp → schedule.yaml (POSIX rename = atomic on same FS)
6. Re-read via scoutctl schedule list --json (single source of truth)
7. Publish to @Published var slots → UI refreshes
```

Same-directory tmpfile + atomic rename is required (cross-FS rename is not atomic on macOS).

### 7.2 Comment preservation

The defaults `schedule.yaml` has a 12-line header comment block. Naive Yams round-trip drops them.

Approach: read raw text, parse to dict, mutate, re-emit slot blocks via Yams, splice back into the original byte stream so leading comments and per-slot blank-line separators survive.

If the structural splice fails (manual user edits broke the section markers), fall back to a pure Yams emit + log a warning. Better to lose comments than refuse to save.

### 7.3 Concurrency

- **Read side (dispatcher reading schedule.yaml every 5 min):** atomic rename → no problem.
- **Write side (two simultaneous Saves):** scout-app is single-window. UI gates Save behind "no dirty draft conflict on this slot" so racing yourself isn't possible.
- **Manual edits in Vim while scout-app has unsaved drafts:** the post-Save reload will surface the divergence (the user's manual edits lose to the app's Save). App-owned-while-open semantics.

### 7.4 Reload triggers

- Tab `.onAppear`: reload via `scoutctl schedule list --json`.
- After every successful Save: reload (fresh data + canonical sort order).
- After every successful Delete: reload.
- **No timer-based polling.** The user's already in the editor; they own the truth while editing. The Control Center strip's `ScheduleService` does its own polling for the live view.

### 7.5 Save-failure UX

If `scoutctl schedule validate` rejects the candidate:
- The Save button reverts to enabled (so you can retry after fixing).
- A red banner appears at the top of the expanded form with the engine's stderr text verbatim.
- The draft is **not** discarded — your in-progress edit survives the failure.

## 8. Runtime field + Plan 7 forward-compat

### 8.1 Schema addition

Engine `engine/scout/schedule.py`: `Slot` dataclass gains an optional `runtime` field:

```python
class SlotRuntime(enum.Enum):
    LOCAL = "local"
    REMOTE = "remote"

@dataclass(frozen=True)
class Slot:
    # ... existing fields ...
    runtime: SlotRuntime = SlotRuntime.LOCAL    # NEW. Optional with default.
```

Vault YAML files without a `runtime` key continue loading correctly — the field defaults to `LOCAL`. No migration needed.

### 8.2 Dispatcher behavior

`engine/scout/scripts/schedule_tick.py::_spawn_runner` raises `ConfigError` if it sees `runtime: remote` (Plan 7-pending). Test asserts the clear error message; protects users who somehow set `remote` in YAML before Plan 7 ships.

### 8.3 Editor UI for `runtime`

In `SlotEditForm`'s Advanced section, render `runtime` as a segmented picker `Local | Remote`. The `Remote` option is disabled with a tooltip:

> *Remote slot execution arrives in Plan 7 (Anthropic routines integration). Reserved field — your selection here saves but the dispatcher will reject `remote` until Plan 7 ships.*

This keeps the picker visible (so users can see the planned shape) without enabling a Save path that produces a tick-time error.

### 8.4 Plan 7 brief

For the FOLLOWUPS.md "Plan 7" entry:

> **Plan 7 — Remote slot execution + routines management.** Wire `runtime: remote` slots through Anthropic's routines API (`CronCreate` / `CronList` / `CronDelete`-equivalent). Add a "Routines" sub-section to the Schedules tab listing claude.ai-side scheduled agents with full CRUD via API.
>
> **Architectural call to make at brainstorm time:** does Scout's `schedule.yaml` push to Anthropic (Scout = source of truth, routines mirror), or does Scout poll Anthropic's routines list and project them as read-only rows (claude.ai = source of truth)?
>
> **Recommended:** Scout owns the slot definition. Plan 7 dispatcher routes `local` → `run-scout.sh` and `remote` → API-spawn one-off session per fire (no persistent cloud routine). Sidesteps two-way sync entirely.
>
> **Caveat:** most current slots can't trivially go remote. Briefing/consolidation/dreaming all read Granola transcripts via local MCP, write to vault, commit to git — none of that exists in Anthropic's routine sandbox today. Research slots are the only obvious Plan 7 candidate.

## 9. Testing plan

### 9.1 Unit — `ScoutTests/Services/ScheduleEditServiceTests.swift` (new)

- `loadAll` decodes `scoutctl schedule list --json` into `[Slot]` correctly given mocked `ProcessRunner` (queue-stub, like the existing `ScheduleServiceTests` pattern).
- `save` invokes validate-then-rename and returns success on exit 0.
- `save` surfaces stderr text and leaves canonical file untouched on exit ≠ 0.
- `save` is atomic — write a slow runner that sleeps; assert canonical's pre-save bytes are intact during the write window. (Uses temp dir, not real vault.)
- `save` preserves header comments — feed YAML with a 12-line header comment, edit one slot, assert the header survives byte-for-byte.
- `save` falls back to pure-Yams emit + warning when structural splice fails (corrupt section markers).
- `delete` removes the slot and rewrites the file.
- Reload after save returns fresh data (mock returns updated `list --json` payload).

### 9.2 Form / view-level — `ScoutTests/Schedules/SlotEditFormTests.swift` (new)

- Live validation: invalid slot_key (`"NotKebab"`, `"has space"`, empty) → field error + Save disabled.
- Live validation: invalid time (`"25:00"`) → field error.
- Save button enable/disable: matches `(draft != live) && noLiveErrors`.
- Type-change confirmation dialog appears on Save when type field differs from live.
- Switching the expanded slot with a dirty draft → confirm dialog.
- Delete confirmation alert names the slot key.
- New-slot draft prefills with collision-bumped key (`new-slot-1` → `new-slot-2` if first taken).
- New-slot Revert removes the in-memory row without touching disk.

### 9.3 Integration / smoke — `ScoutTests/Integration/ScheduleEditE2ETest.swift` (new, opt-in)

Skipped unless `SCOUT_DATA_DIR` is set + points at a real vault. Reads canonical `schedule.yaml`, edits a slot's `cooldown_minutes`, saves, reloads, asserts the change persisted, then reverts via a second save back to the original value. Cleanup-safe: original file restored at test exit.

### 9.4 Engine-side — `engine/tests/unit/test_schedule.py` (extension)

- `Slot.runtime` defaults to `SlotRuntime.LOCAL` when absent from YAML.
- `Slot.runtime` parses `"local"` and `"remote"` correctly.
- Loader rejects `runtime: invalid_value` with a clear `ConfigError`.
- `_spawn_runner` raises `ConfigError` when `runtime == SlotRuntime.REMOTE` (Plan 7-pending).
- `scoutctl schedule validate --target <path>` exits 0 on a valid YAML file and ≠ 0 with stderr message on invalid (e.g., schema_version mismatch, malformed slot, duplicate slot key).
- `scoutctl schedule validate` (no flag) continues to validate the canonical vault path — backward compat.
- Existing 19 + 10 + 8 schedule tests continue to pass — both engine changes are additive.

### 9.5 Manual smoke (post-merge)

- Edit a slot's `fires_at_local`, watch the next dispatcher tick honor it.
- Add a new slot, watch it appear in `list-upcoming` and fire at its target time.
- Delete a slot, confirm it's gone from the next tick's `compute_due_slots`.

## 10. Rollout & deletion

**No manifest flag.** Plan 5 already lit `schedule_v2: true`. Plan 6 is UI-only on top of that.

**Branch + PR strategy:**
- One branch in scout-app: `plan-6-schedules-tab`, branched from `main`.
- One small companion branch in scout-plugin: `plan-6-engine` (lands first). Two additive changes:
  - `Slot.runtime` enum + dispatcher guard + tests (§8.1, §8.2)
  - `scoutctl schedule validate --target <path>` flag + tests (§3 write path)
- Vault stays as-is. No migration needed.

**Order of operations:**
1. Land scout-plugin `plan-6-engine` PR. Both changes are additive; no behavior change for existing `runtime: local` slots, no behavior change for `validate` without `--target`.
2. Land scout-app `plan-6-schedules-tab` PR. Editor renders `Remote` disabled. Real CRUD on `runtime: local` slots.

## 11. Risks

**Comment preservation in YAML round-trip.** Approach is documented (raw-text-merge with Yams fallback) but needs a real test against the production `~/Scout/.scout-state/schedule.yaml` to confirm. Mitigation: pure-Yams fallback ensures saves never fail — they only lose comments in the worst case.

**`scoutctl` discovery from scout-app at runtime.** `ScheduleEditService` needs the same `scoutctlExecutable: URL` that `AppState` already wires for `ScheduleService`. Re-use that wiring; no new path resolution logic.

**No FS watcher means stale list if user edits YAML in Vim concurrently.** Acceptable. The reload-after-save and reload-on-tab-appear catch the common cases; concurrent terminal edits + UI session is a niche scenario the legacy tab also struggled with.

**Atomic rename across snapshot drives.** macOS Time Machine snapshots can occasionally make rename non-atomic if the source and target are on different APFS volumes. Mitigation: tmpfile is always in the same directory as canonical, which is always on the same volume.

## 12. References

- Plan 5 spec: `docs/superpowers/specs/2026-05-04-schedule-v2-design.md`
- Plan 5 implementation plan: `docs/superpowers/plans/2026-05-04-scout-unification-plan-5-schedule-v2-and-mode-rename.md`
- Plan 5 → Plan 6 carryforward: `docs/superpowers/FOLLOWUPS.md` ("Plan 5 → Plan 6 — Schedules tab rewrite")
- Engine schedule loader: `engine/scout/schedule.py` (overlay support, validation rules)
- Engine schedule CLI: `engine/scout/cli.py::_register_schedule()`
- Existing comment-preservation pattern (none — Plan 6 introduces it; closest analog is `engine/scout/scripts/connectors_snapshot.py`'s deterministic JSON serialization)

## 13. Decision log

| # | Question | Decision | Alternatives rejected |
|---|---|---|---|
| 1 | Primary user job | Full CRUD on slots | Read-only inspector; per-field editor only |
| 2 | Storage | Edit canonical `schedule.yaml` directly | Overlay file; hybrid by-operation |
| 3 | Heartbeat / schedule-tick scope | Out of scope (tab is yaml-only) | Read-only system row; full edit |
| 4 | `slot_key` mutability | Immutable post-creation | Editable with confirm; editable with tracker migration |
| 5 | Save model | Drafts + explicit Save | Auto-save; hybrid |
| 6 | Layout | Single column with inline expand | Master/detail; list + modal sheet |
| 7 | Remote-execution scope | Reserved field, Plan 7 implementation | Pull into Plan 6 |
| 8 | Engine-side change for tmpfile validation | Add `validate --target <path>` flag (additive) | Mv-canonical-then-validate; skip engine validate, rely solely on Swift-side checks |
