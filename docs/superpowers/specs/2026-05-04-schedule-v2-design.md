# Schedule v2 — engine-canonical schedule, TZ-aware dispatch, sleep-aware catch-up

**Status:** Design ready for review (2026-05-04). Implementation lands in Plan 5.

**Reframes:** v0.4 unification spec §6 (data directory contract) and §11 (plugin/vault content boundary). v0.5+ event-architecture spec "Connector taxonomy", "Async-first user comms", and event-type taxonomy.

**Companion artifacts:**
- v0.4 spec amendment: `2026-04-24-scout-unification-design.md` §6 + new sub-section under §11.
- v0.5+ spec amendment: `2026-04-25-scout-event-architecture-design.md` Connector taxonomy `(mode, tier)` → `(slot_type, tier)` + four new event kinds.

---

## 1. Position in the unification arc

The v0.4 unification spec set a clear direction: engine becomes source-of-truth, vault holds user data, scout-app becomes a UI + power-state observer. Plans 1–4 implemented that arc subsystem-by-subsystem:

| Plan | Subsystem unified |
|---|---|
| 1 | action-items module → engine |
| 2 | scout.kb foundation → engine |
| 2-supp | stable IDs + Events → engine canonical |
| 3 | action-items watch → engine |
| 4 | connector subsystem (roster + hooks + scripts) → engine |

Schedule v2 is the **next subsystem on the same arc**, not a new direction. Today the schedule lives inside scout-app's in-app dispatcher state; the canonical mode names (`consolidation-11am`, …) have ET clock times baked into them; the dispatcher cannot follow the user across timezones; and laptop-asleep slots are silently lost. These are the same anti-patterns Plan 4 fixed for connectors (where the roster used to live in a hardcoded Python dict in a bash script).

This spec applies the engine-canonical pattern to the schedule subsystem, with three concrete moves:

1. **Schedule definition moves to vault YAML** at `~/Scout/.scout-state/schedule.yaml`, validated by `scout.schedule` in scout-plugin.
2. **Dispatcher moves to engine** (`scoutctl schedule tick`), driven by a 5-minute launchd plist (`com.scout.schedule-tick.plist`). Scout.app becomes a read-only UI mirror — its `RunnerService` is deleted; new `ScheduleService` consults `scoutctl schedule list-upcoming --json`.
3. **TZ-aware semantics by construction.** Slot wall-clock times are interpreted in the system's current local timezone. Travel ET → CEST and the schedule moves with you. Optional per-slot `tz:` pinning available for users who explicitly want a slot anchored to a fixed zone.

## 2. Plan-queue reorganization

| Was (after Plan 4 merged) | Now |
|---|---|
| Plan 4-supplement: 7 bash script ports | Plan 4-supplement: 6 ports + a slimmer heartbeat (see §10 — Plan 5 absorbs heartbeat's *scheduled-dispatch* role; heartbeat's *opportunistic-dispatch* role for dreaming/research stays in Plan 4-supplement, redesigned to consult schedule.yaml for slot definitions) |
| Plan 5: KB ontology cache (per v0.4 §11 Phase D) | **Plan 5: Schedule v2** (this spec) |
| Plan 6: scout-app refactor (`ScoutEnvironment` + `EngineClient` + first-run wizard) | Plan 6: scout-app refactor (minus the schedule pieces, which land in Plan 5) |
| Plan 7: personal-data scrub | Plan 7: KB ontology cache (renumbered from old Plan 5) |
| (none) | Plan 8: personal-data scrub (renumbered from old Plan 7) |

Plan 5 establishes patterns — `scoutctl schedule snapshot` (parallel to `scoutctl connectors snapshot`), engine-canonical CLI for vault-stored config, scout-app `EngineClient`-style consultation — that Plans 6 and 7 reuse. The reorganization resolves sequencing without adding work.

## 3. Slot semantics (the data model)

Two-level vocabulary:

- **Slot keys** are user-chosen identifiers. The plugin ships defaults in `engine/scout/defaults/schedule.yaml`; `scoutctl schedule init` copies them into the vault on first run; the user is then free to rename, retime, add, or remove. Slot keys appear verbatim in JSONL `mode` fields.
- **Slot types** are a fixed plugin vocabulary: `briefing`, `consolidation`, `dreaming`, `research`, `manual`. Each slot declares its `type:`. Aggregation surfaces (`connectors.yaml` `required_in_types`, `connector-health-report` rollup buckets, scout-app routing rules) reference *types*, not keys, so user renames don't break alerting.

Default `schedule.yaml` shipped with the plugin (Jordan's current rhythm; mirrors today's launchd schedule with TZ-neutral keys):

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
    # budget_usd: 10  # optional; not load-bearing in v0.5

  weekend-briefing:
    type: briefing
    runner: run-scout.sh
    fires_at_local: "08:30"
    weekdays: [Sat, Sun]
    missed_window_hours: 6
    on_miss: fire
    cooldown_minutes: 60

  morning-consolidation:        # was consolidation-11am
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "11:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 2
    on_miss: collapse
    cooldown_minutes: 90

  midday-consolidation:         # was consolidation-1pm
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "13:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 2
    on_miss: collapse
    cooldown_minutes: 90

  afternoon-consolidation:      # was consolidation-5pm
    type: consolidation
    runner: run-scout.sh
    fires_at_local: "17:00"
    weekdays: [Mon, Tue, Wed, Thu, Fri]
    missed_window_hours: 3
    on_miss: collapse
    cooldown_minutes: 90

  evening-consolidation:        # was consolidation-7pm
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

Schema invariants (all validated by `scout.schedule.SchemaValidator`):
- `fires_at_local` is `HH:MM` 24-hour.
- `weekdays` is a non-empty subset of `[Mon, Tue, Wed, Thu, Fri, Sat, Sun]`.
- `on_miss ∈ {fire, skip, collapse}`.
- `runner` resolves to an executable script in the vault.
- Slot keys are unique kebab-case identifiers.
- `budget_usd` optional; absent → `None`; consumers must handle the absent case.
- Optional `tz` field: if present, slot interpreted in that IANA zone; absent → system local.

## 4. Dispatcher (`scoutctl schedule tick`)

Engine module: `scout.scripts.schedule_tick`. Entry: `scoutctl schedule tick`. Invocation: every 5 minutes via `com.scout.schedule-tick.plist` (the only new launchd plist Plan 5 adds; replaces the existing 7 per-slot plists). Idempotent and concurrency-safe via fcntl flock on `.scout-state/.schedule-tick.lock`.

Per-tick algorithm:

1. Load `schedule.yaml` (cached if mtime unchanged from prior tick).
2. Load run-tracker (`usage-tracker.jsonl`); build per-slot `last_fire_ts` index.
3. For each slot whose weekday matches `now`: compute today's target local datetime; check cooldown; classify as candidate if past target and not yet fired today.
4. Group candidates by slot type. Apply per-slot `on_miss` policy:
   - **`fire`** — fire if within `missed_window_hours`; else emit `slot.skipped` with `reason="stale-after-window"`.
   - **`skip`** — emit `slot.skipped` with `reason="on_miss=skip"`. Always.
   - **`collapse`** — within a type group, only the most-recently-targeted candidate fires; earlier ones get `slot.skipped` with `reason="collapsed-into=<key>"`.
5. **Pre-spawn network check.** Before spawning any runner, attempt a TCP connect to `api.anthropic.com:443` with a 3-second timeout. If unreachable, sleep 5s and retry up to 6 times (≤ 30s total). If still offline after the budget, emit `slot.skipped` with `reason="network-offline"` for every fire-decision slot and exit the tick (do NOT mark them fired in the tracker; the next tick will re-evaluate). This handles the wake-from-sleep race where launchd fires `tick` within milliseconds of lid-open but the Wi-Fi stack hasn't reconnected yet (typical 2–5s delay).
6. **Single-fire-per-tick (priority-ordered).** Among the slots that survived steps 4–5, fire **at most one** subprocess this tick, picked by slot-type priority: `briefing > consolidation > dreaming > research > manual`. The non-winners stay eligible (no `slot.skipped` emitted for them — they're a transient deferral, not a final decision); the next tick re-evaluates and fires the next highest-priority. With a 5-min tick interval, a 4-slot wake-from-sleep backlog clears in ~20 minutes. This prevents concurrent runner subprocesses from racing to update the same `action-items-YYYY-MM-DD.md`, and keeps API rate-limit consumption predictable.
7. For the winning slot: spawn the runner with `SCOUT_FORCE_MODE=<slot_key>`; record fire timestamp in tracker; emit `slot.fired`.
8. Emit `schedule.tick.completed` summarizing the tick (fired slot, skipped slots with reasons, deferred slots that remain eligible, duration_ms).

Catch-up after extended sleep is **the same code path** — when tick wakes after a sleep gap, `last_fire_ts` for one or more slots is older than today's target; the on_miss + missed_window rules naturally do the right thing without a separate "catch-up mode." Network-readiness and single-fire-per-tick provide the wake-from-sleep guardrails.

`run-scout.sh` simplification: the existing `case $HOUR in 08) MODE="morning-briefing" ...` block is deleted. The runner reads `SCOUT_FORCE_MODE` unconditionally (already its existing override path).

## 5. Sleep handling

Three layers, ordered by what carries the load:

1. **Catch-up dispatcher (primary).** Mac sleeps normally; missed slots are evaluated on the next tick after wake, per their on_miss policy. No system config required. This is the load-bearing mechanism — everything below is optimization.
2. **Scout.app menubar power-state awareness (primary defense for battery + lid-closed).** Polls `pmset -g batt` every 30s; surfaces a yellow banner above the schedule strip when on battery: *"On battery — runs may be missed if the lid closes. Plug in for guaranteed firing."* This is the **first-line UX defense** for the case `pmset wakeorpoweron` cannot solve (see point 3). The honest framing: catch-up will collect missed runs whenever the laptop next wakes, but live firing during long lid-closed-on-battery stretches is not achievable on modern Apple Silicon.
3. **`scoutctl schedule install-wake-schedule` (optional, AC-only).** Opt-in. Computes the earliest weekday `fires_at_local`; runs `pmset repeat wakeorpoweron MTWRF HH:MM:SS`. **Documented limitation:** on Apple Silicon laptops, `wakeorpoweron` is unreliable when on battery with the lid closed — macOS's deep-sleep / standby state suppresses wake timers to preserve battery. This layer is therefore most useful for **AC-powered desk-Mac scenarios** (e.g., user's primary workstation stays plugged in overnight). The `install-wake-schedule` command prints this caveat at install time and recommends keeping the laptop plugged in if guaranteed live firing matters. Companion `--uninstall` removes the rule.

The `slot.skipped` event carries `reason` so connector-health-report can distinguish `laptop-asleep` skips from real connector outages — no false-positive Slack-dark alerts on a Monday morning that follows a closed-laptop weekend.

## 6. Scout.app changes

Net code change: smaller, not bigger. Scout.app stops being a dispatcher and becomes a UI mirror.

**Deleted:**
- `Scout/Services/RunnerService.swift` — its only caller was the in-app dispatcher path. Engine owns dispatch now.
- The in-app heartbeat scheduler (whatever Timer publishes "next slot fires in 1 minute" today; identifying its exact location is a Plan 5 task).

**New:**
- `Scout/Services/ScheduleService.swift` — calls `scoutctl schedule list-upcoming --window 24h --json` every 60s. Parses output. Publishes `[UpcomingRun]`.
- `Scout/Services/PowerStateService.swift` — observes battery state via `pmset -g batt` every 30s. Publishes `.onAC` / `.onBattery(level: Double)`.
- One menubar item: "Install wake-schedule…" runs the scoutctl command interactively.

**Refactored:**
- `RunNowButton` calls `scoutctl schedule fire-now <slot-key>` (subprocess) instead of `RunnerService.runNow`.
- The "Heartbeat schedule" view consumes `ScheduleService` instead of in-app state.

## 7. Cross-repo snapshot sync

Following the Plan 4 / connectors precedent:

- `scoutctl schedule snapshot` writes a JSON projection of `schedule.yaml` to two paths by default:
  - Canonical: `engine/scout/schedule.snapshot.json` (in scout-plugin checkout). CI drift-checks this against the seeded vault default.
  - App fixture: `~/scout-app/ScoutTests/Fixtures/schedule.snapshot.json` (best-effort dual-write; skips with a warning if the path doesn't exist).
- `--check` mode exits 1 on drift, prints unified diff (with `generated_from` SHA stripped, like the connectors snapshot).
- Scout.app's tests assert `ScheduleService` parses the snapshot fixture without errors.

## 8. Mode-rename migration

Big-bang rename across run-scout.sh, connectors.yaml, scout-app, SKILL.md/DREAMING.md/RESEARCH.md, CLAUDE.md, plus a one-shot `tools/migrate-mode-names.py` for historical JSONL.

Rename map:

| Old mode | New slot key | New slot type |
|---|---|---|
| `morning-briefing` | `morning-briefing` | `briefing` |
| `weekend-briefing` | `weekend-briefing` | `briefing` |
| `consolidation-11am` | `morning-consolidation` | `consolidation` |
| `consolidation-1pm` | `midday-consolidation` | `consolidation` |
| `consolidation-5pm` | `afternoon-consolidation` | `consolidation` |
| `consolidation-7pm` | `evening-consolidation` | `consolidation` |
| `dreaming-nightly-10pm` | `dreaming-nightly` | `dreaming` |
| `dreaming-weekend-6am` + `-7am` | `dreaming-weekend-morning` (one slot, 07:00) | `dreaming` |
| (was unwired) | `dreaming-evening` (18:30) | `dreaming` |
| `manual` | `manual` (kept; SCOUT_FORCE_MODE-driven) | `manual` |

`scout-plugin/tools/migrate-mode-names.py` walks `~/Scout/.scout-logs/connector-calls-*.jsonl` and `session-tokens.jsonl`, rewrites the `mode`/`scout_mode` fields per the rename map, backs up originals to `.scout-logs/.pre-plan-5-backup/`. Idempotent; running twice is a no-op.

`scout-plugin/tools/regenerate-connector-health.py` regenerates `knowledge-base/connector-health.md` from the renamed logs so the matrix headers match.

`connectors.yaml` schema migrates `required_in: [list-of-mode-names]` → `required_in_types: [list-of-slot-types]`. The mode-aware baseline rule in `connector_health_report.py` keeps operating on slot key (preserves the "weekend gh CLI dark = no alert" property at the slot-key granularity); the chronic-skip rule operates on slot type.

## 9. Event taxonomy additions (v0.5+ spec)

Four new event kinds:

| Kind | Source | Payload |
|---|---|---|
| `slot.fired` | `cli:schedule_tick` | `{slot_key, slot_type, target_local, target_utc, runner, pid_spawned}` |
| `slot.skipped` | `cli:schedule_tick` | `{slot_key, slot_type, target_local, reason}` where reason ∈ `{on_miss=skip, collapsed-into=<key>, stale-after-window, cooldown_active, laptop-asleep, network-offline}` |
| `slot.fire_failed` | `cli:schedule_tick` | `{slot_key, slot_type, target_local, error}` (subprocess spawn failed) |
| `schedule.tick.completed` | `cli:schedule_tick` | `{fired: [slot_key,...], skipped: [...], duration_ms}` |

These flow through the same `Event` shape from the v0.4 §13.2 commitment and emit through the same JSONL writer the hooks use today.

## 10. Heartbeat split: scheduled vs opportunistic

The current `~/Scout/scripts/heartbeat.sh` (running every 30 min via `com.scout.heartbeat.plist`) plays two roles:

1. **Scheduled-dispatch fallback.** When the lock-file is free, it could nudge a scheduled slot that didn't fire on its own (this is partly why today's heartbeat-dispatcher in scout-app exists alongside the bash heartbeat).
2. **Opportunistic dispatch.** When a scheduled session isn't running and conditions are favorable (budget healthy, no recent dreaming/research, KB has uncommitted changes), heartbeat fires `run-dreaming.sh` or `run-research.sh` to use available capacity productively.

Plan 5 takes over **role 1** completely — `scoutctl schedule tick` is the engine-canonical scheduled-dispatch path. Plan 4-supplement's port of heartbeat keeps **role 2**: the opportunistic-dispatch logic is rewritten to consult `schedule.yaml` for slot definitions (so it knows which type a "next dreaming session" maps to) and emits its own events (`heartbeat.opportunistic.fired`, `heartbeat.opportunistic.skipped`). The two ticks run on different plists, different cadences, and different code paths — clean separation.

The slimmer Plan 4-supplement heartbeat is ~80 lines of Python instead of ~250 lines of bash.

## 11. Testing strategy

- **Unit (pytest)** — `scout.schedule` schema validator, `compute_due_slots`, `apply_miss_rules`, the catch-up scenarios (3 hour gap, 24 hour gap, weekend-bridging gap), the per-slot tz override, the cooldown lockout. Target ≥25 unit tests.
- **Integration (pytest)** — `scoutctl schedule tick` end-to-end against a tmp_path vault: write a synthetic schedule.yaml + tracker, advance a fake clock, assert correct subprocesses spawned (with a no-op stub for the runner script).
- **Bats parity (one test, transitional)** — assert that `scoutctl schedule tick` produces the same set of fire decisions as today's scout-app heartbeat output for a fixed schedule + clock. Removed once Plan 5 lands and the old dispatcher is gone.
- **Swift (XCTest)** — `ScheduleService` parses `schedule.snapshot.json` correctly; falls back when snapshot missing; updates published `UpcomingRun` list when subprocess output changes.
- **Cross-repo CI** — `scoutctl schedule snapshot --check` runs in scout-plugin CI against the committed canonical snapshot.

## 12. Spec amendments included with this plan

The PR for Plan 5's design phase includes:

1. This concept doc.
2. **v0.4 spec §6 (Layout)** — adds `schedule.yaml` to the `.scout-state/` block of the layout diagram.
3. **v0.4 spec §11** — new sub-section *"Schedule definition lives in the vault"* mirroring the existing *"plugin/vault content boundary for connector phases"* sub-section. Same shape: plugin ships generic defaults, vault holds user instantiation, plugin never inlines specific times into prose.
4. **v0.5+ spec "Connector taxonomy"** — table of `(mode, tier)` channel routing rewritten to `(slot_type, tier)`. `slot_type` is the more stable join key; `slot_key` is too granular for routing rules.
5. **v0.5+ spec "Async-first user comms"** — same `(mode, tier)` → `(slot_type, tier)` substitution.
6. **v0.5+ spec event taxonomy** — adds the four `slot.*`/`schedule.tick.*` event kinds.

## 13. YAGNI / non-scope

- **Per-user `~/.config/scout/schedule.yaml` outside the vault.** YAGNI; vault is the canonical user-data home. If multi-user-on-one-machine becomes a concern, deferred to Plan 9+.
- **Schedule UI editor inside scout-app.** v0.5 ships read-only UI; users edit `schedule.yaml` in their Obsidian vault directly. Scout.app gains an editor in Plan 6 alongside the rest of the wizard work.
- **Calendar-aware dispatch** (e.g. "skip morning-consolidation if a meeting is happening at 11:00"). Calendar integration is async-collaborator territory — v0.7+ at the earliest.
- **Multi-machine schedule sync.** YAGNI; one user, one machine. If Jordan adds a second machine later, the schedule.yaml is in the vault, which can be synced via Obsidian Sync or git.
- **Hot-reload of schedule.yaml without a tick.** The 5-min tick already picks up changes. Manual `scoutctl schedule reload` available for instant feedback during editing.

## 14. Open questions

- Should the dispatcher's tick frequency be 5 min (default) or configurable in `schedule.yaml`? Current proposal: hardcoded 5 min, revisit if pmset-wake users want tighter granularity.
- Should `slot.fired` events be emitted by the runner script (after the session actually starts) rather than the dispatcher (which only spawns the subprocess)? Current proposal: emit from dispatcher (matches "I attempted to fire" semantics); a separate `session.started` event from the runner can be added later if needed for finer-grained observability.
- Should the network-readiness retry budget (currently hardcoded 30s = 6 × 5s) be tunable in `schedule.yaml`? Defer to user feedback after Plan 5 lands.

### Resolved during 2026-05-04 review

- ~~pmset on Apple Silicon battery + lid-closed wake reliability~~ — **resolved: it doesn't wake reliably.** Modern MacBooks enter standby with wake timers suppressed when on battery + lid closed; `wakeorpoweron` only works dependably on AC. §5 reframed accordingly: scout-app's power-state banner is the primary defense; pmset wake-schedule is opt-in for AC-power scenarios with documented limitations.

## 15. References

- v0.4 unification spec: `2026-04-24-scout-unification-design.md`
- v0.5+ event-architecture spec: `2026-04-25-scout-event-architecture-design.md`
- Plan 4 (connector subsystem migration, merged 2026-05-01): `2026-04-28-scout-unification-plan-4-connector-subsystem-and-hooks-port.md`
- Today's diagnosis trail (the missed 11am consolidation that surfaced the TZ + sleep issues): conversation log 2026-05-04.
