# Scout Schedules — design

Date: 2026-04-22
Status: approved

## Problem

Scout's launchd schedules (briefing, dreaming, consolidation, heartbeat, and the
missing research cadence) live in two places: the live copy at
`~/Library/LaunchAgents/com.scout.*.plist` and the git-tracked source of truth
at `~/Scout/launchd/com.scout.*.plist`. Editing them today means opening a plist
by hand, running `launchctl bootout` and `bootstrap`, and remembering to commit
the repo copy. The Scout.app has a minimal `SettingsView` (launch-at-login plus
a path label) and no UI for schedules.

## Goal

A **Schedules** section in Scout.app that supports full CRUD on every
`com.scout.*.plist`: view current schedules, edit fire times, add new schedules
(e.g. backfill the research plist), remove ones that are no longer wanted.
Saving updates both the live and repo copies, reloads launchd automatically,
and commits the repo change — all from inside the app.

## Placement

- New sidebar entry **"Schedules"** in `SidebarView`, added to the top "Scout"
  section alongside Control Center and Action Items. Schedules are
  operational and belong next to the runs they produce — not tucked inside the
  Cmd+, Settings scene.
- `SidebarItem` gains a `.schedules` case. `MainWindowView`'s detail `switch`
  renders a new `SchedulesView`.
- The existing `Settings` scene (Cmd+,) stays minimal and unchanged.

## Data model (new `Models/Schedule.swift`)

```swift
struct Schedule: Identifiable, Equatable, Hashable, Sendable {
    let id: String            // filename stem, e.g. "com.scout.briefing-weekend"
    var label: String         // plist Label field (== id)
    var runnerScript: URL     // path from ProgramArguments[1]
    var workingDirectory: URL?
    var environment: [String: String]
    var logStdOut: URL?
    var logStdErr: URL?
    var trigger: ScheduleTrigger
    var unknownKeys: [String: PlistValue]  // preserved verbatim on round-trip
}

enum ScheduleTrigger: Equatable, Hashable, Sendable {
    case calendar([CalendarFire])   // StartCalendarInterval
    case interval(seconds: Int)     // StartInterval (heartbeat pattern)
}

struct CalendarFire: Identifiable, Equatable, Hashable, Sendable {
    let id: UUID
    var weekday: Int?   // nil = every day; launchd convention: 0=Sun ... 6=Sat
                        // (Apple's launchd.plist accepts 0 or 7 for Sunday;
                        // Scout's existing plists use 0. The model stores the
                        // raw plist value. The UI translates to a user-facing
                        // menu. Note: LaunchdScheduleService today has an
                        // incorrect "1=Sun ... 7=Sat" comment; shared PlistIO
                        // normalizes 7→0 on read to fix that drift.)
    var hour: Int       // 0..23
    var minute: Int     // 0..59
}

enum PlistValue: Equatable, Hashable, Sendable {
    case string(String)
    case int(Int)
    case bool(Bool)
    case date(Date)
    case data(Data)
    case array([PlistValue])
    case dict([String: PlistValue])
}
```

- `unknownKeys` lets us round-trip plist keys we don't surface in the UI
  (`RunAtLoad`, `KeepAlive`, `ProcessType`, etc.) without destroying them on
  save. Parse reads every top-level key; serialize writes the edited keys we
  know about plus every key in `unknownKeys` verbatim.
- `ScheduleTrigger` is a sum type so the heartbeat plist (`StartInterval` = 1800)
  is editable under the same UI. The two cases map directly to the two mutually
  exclusive plist keys.

## Service layer (new `Services/ScheduleEditorService.swift`)

```swift
@MainActor
final class ScheduleEditorService: ObservableObject {
    @Published private(set) var schedules: [Schedule]
    @Published private(set) var commitErrors: [CommitError]  // non-blocking banner queue

    init(
        repoDirectory: URL,      // ~/Scout/launchd
        agentsDirectory: URL,    // ~/Library/LaunchAgents
        launchctl: any LaunchctlClient,
        git: GitService,
        fileEvents: any FileSystemEventSource
    )

    func loadAll() async throws
    func save(_ schedule: Schedule, commitMessageOverride: String?) async throws
    func create(_ schedule: Schedule, commitMessageOverride: String?) async throws
    func delete(_ schedule: Schedule, commitMessageOverride: String?) async throws
}
```

### loadAll
Source of truth is `~/Scout/launchd/*.plist`. For each file whose name matches
`com.scout.*.plist`, parse it into a `Schedule`. If the repo has a file but the
live directory doesn't (or vice versa), flag as a drift warning surfaced in the
list row. Parse failures produce an "unreadable" row the user can delete or
open in Finder.

### save / create
1. Validate: id matches `^com\.scout\.[a-z0-9-]+$`, unique, trigger is
   `.calendar` with ≥1 fire or `.interval` with `seconds > 0`.
2. Serialize the `Schedule` to XML plist data (known keys + `unknownKeys`).
3. Write atomically: write to a temp file in the same directory, `rename()`
   into place. Do this for both the repo path and the live path.
4. Reload launchd via the injected `LaunchctlClient`:
   - `launchctl bootout gui/$UID <live-path>` — swallow exit 3 ("not loaded")
   - `launchctl bootstrap gui/$UID <live-path>`
   - If `bootstrap` fails, roll back: remove the live copy (leave the repo copy
     as the durable record), surface an alert with stderr and a Retry action.
5. On success, commit via `git.commitPaths([repoRelativePath], message:)` with
   either the caller's override or the generated default. A git failure does
   *not* roll back the plist edit — it enqueues a `CommitError` for the banner.

### delete
1. Run `launchctl bootout` on the live path (swallow "not loaded").
2. Remove both files (tolerate missing).
3. Commit the repo deletion the same way.

### Shared plist IO
Extract read/write helpers into `Services/PlistIO.swift`:

```swift
enum PlistIO {
    static func readSchedule(from url: URL) throws -> Schedule
    static func writeSchedule(_ schedule: Schedule, to url: URL) throws
}
```

The existing `LaunchdScheduleService.parsePlist` is rewritten to use
`PlistIO.readSchedule` (returning just the fields it needs: label + calendar
fires). This keeps the Control Center's upcoming strip behavior identical while
centralizing plist logic.

## UI

### `SchedulesView` (list)

- Toolbar: title "Schedules", "New Schedule" button (primary).
- `Table<Schedule>` with columns:
  - **Label** — id (monospaced muted)
  - **Runner** — last path component of `runnerScript`
  - **Trigger** — summary string: `"Weekdays 8:03, 11:03, 13:07, 17:03"`,
    `"Sat–Sun 8:00"`, `"Every 30 min"`. Formatter is a pure function,
    snapshot-tested.
  - **Next fire** — relative time, e.g. "in 2h"
  - **Status** — colored dot: green (loaded + synced), amber (drifted between
    repo/live), red (parse failure)
- Selection opens `ScheduleDetailView` inline in the right pane (same
  `NavigationSplitView` pattern as Control Center's run detail).
- Non-blocking banner stack at the top for commit errors.

### `ScheduleDetailView` (form)

- **Label** — read-only once saved. Renaming is not supported in-place; the
  view shows an info note explaining that renaming means create new + delete
  old. New-schedule sheet allows editing the label.
- **Runner script** — dropdown with the known scripts:
  - `run-scout.sh`
  - `run-dreaming.sh`
  - `run-research.sh`
  - `scripts/heartbeat.sh`
  - "Custom…" freeform path field
- **Trigger** — segmented control `[Calendar fires] [Interval]`:
  - Calendar mode: a `List` of `CalendarFire` rows. Each row has a weekday
    `Menu` ("Every day" / Sun / Mon / … / Sat), hour `Stepper` (0–23), minute
    `Stepper` (0–59). "Add fire" button below; swipe-to-delete on rows.
  - Interval mode: a single `Stepper` bound to seconds, with a helper that
    displays the equivalent in minutes/hours.
- **Advanced** (disclosure group):
  - Working directory (text field; empty = unset)
  - Environment variables (`Table<KeyValue>` editor with add/remove)
  - StdOut / StdErr log paths (text fields)
- **Commit message** (disclosure, collapsed by default) — text field
  pre-populated with the generated default; user may override before save.
- **Footer**: "Loaded in launchd: ✓ · Last reload: 2s ago" plus buttons
  `Save` (primary, disabled when clean or invalid), `Revert`, `Delete`
  (destructive, requires confirm).

### New-schedule sheet
Same view, starts empty. Label field is editable until first successful save.
Default trigger is `.calendar([CalendarFire(weekday: nil, hour: 9, minute: 0)])`.
Default runner is `run-scout.sh`.

## Commit messages (defaults)

Generated at save time, editable in the disclosure before commit:

- Create: `schedules: add <label>`
- Update: `schedules: update <label>` with an appended suffix derived from the
  diff: `(runner)`, `(trigger)`, `(env)`, or comma-joined for multi-field
  edits. Example: `schedules: update com.scout.briefing (trigger)`.
- Delete: `schedules: remove <label>`

Diff detection lives in a pure helper `ScheduleDiff.summarize(original:edited:)
-> String` so it's unit-testable independent of git.

## GitService additions

Add to `GitService`:

```swift
func commitPaths(_ relPaths: [String], message: String) async throws
```

Implementation:
1. `git -C <repo> rev-parse --is-inside-work-tree` — bail silently if not a
   repo (matches existing `commitAll` behavior).
2. `git -C <repo> add -- <paths>` — stage only the named paths.
3. `git -C <repo> diff --cached --quiet -- <paths>` — if no staged diff for
   these paths, skip.
4. `git -C <repo> commit -m <msg> -- <paths>` — the `-- <paths>` suffix on
   `commit` writes a commit containing only those paths. Any unrelated staged
   work in the repo is left untouched.

New error type `GitServiceError.commitFailed(exitCode:stderr:)` is thrown so
callers can surface stderr in the banner.

## `launchctl` abstraction

```swift
protocol LaunchctlClient {
    func bootout(userDomain uid: uid_t, path: URL) async throws -> Int32
    func bootstrap(userDomain uid: uid_t, path: URL) async throws
}
```

Production impl wraps `ProcessRunner` executing `/bin/launchctl`. Tests use a
`FakeLaunchctlClient` that records calls and returns scripted results.

## Error handling matrix

| Failure | Behavior |
|---|---|
| Plist serialize error (bug) | Throw; don't write either file. Alert with message. |
| Write failure on live path | Throw; don't touch repo path. Alert + retry. |
| Write failure on repo path (after live write) | Roll back live path (delete). Alert. |
| `bootout` exit 3 ("not loaded") | Swallow; proceed to bootstrap. |
| `bootout` other failure | Throw; don't bootstrap; don't commit. Alert + retry. |
| `bootstrap` failure | Delete live copy (keep repo copy); don't commit. Alert with stderr + retry. |
| Git commit failure | Plist edit stands. Enqueue `CommitError` for the banner; don't roll back. Per-banner retry. |
| Parse failure on load | Row rendered with red badge and "unreadable"; user can delete or open raw in Finder. |

## Testing

### `ScheduleEditorServiceTests`
- Round-trip: fixture plist (one per existing Scout plist shape — briefing,
  briefing-weekend, dreaming-nightly, heartbeat) → parse → serialize → parse,
  with deep equality including `unknownKeys`.
- `save` writes to both injected path roots, in that order, with temp+rename.
- `save` invokes `bootout` then `bootstrap` with expected args; swallows exit 3.
- `bootstrap` failure rolls back live file and throws without committing.
- `save` invokes `git.commitPaths` with expected path list and generated default
  message; override is respected when provided.
- Git failure enqueues `CommitError` but leaves plist written/reloaded.
- `delete` removes both files, tolerates either being missing.
- Validation: rejects invalid labels (spaces, uppercase, no `com.scout.`
  prefix), duplicate ids, empty `.calendar`, zero-second `.interval`.

### Pure-function tests
- `ScheduleTriggerFormatter` — snapshot a table of inputs → expected strings,
  including consolidations-style multi-fire, weekend-only, daily, and
  interval cases.
- `ScheduleDiff.summarize` — runner-only change, trigger-only, env-only,
  multi-field, identical inputs (→ empty suffix so the default reads
  `schedules: update <label>`).
- `PlistIO` weekday normalization — parsing a plist with `Weekday=7` yields
  `weekday = 0` in the model; serializing `weekday = 0` writes `Weekday=0`.

### View smoke tests
- `SchedulesView` renders a known-good `ScheduleEditorService` with three
  fixtures; asserts row count, selection binding, and banner presence when
  `commitErrors` is non-empty.

## Dependencies and wiring

- `AppState` gains a `scheduleEditorService: ScheduleEditorService` built from
  the existing `scoutDirectory`, a shared `SystemProcessRunner`, and the
  existing `FileWatcher`.
- `MainWindowView`'s `.schedules` case passes the service via
  `.environmentObject`.
- The existing `LaunchdScheduleService` continues to drive the Control Center
  upcoming strip unchanged (now using shared `PlistIO`).

## Non-goals (YAGNI)

- No push — the Scout repo is local-only (no GitHub remote).
- No in-app `git log` / diff viewer for schedule history; use the terminal.
- No runner-script existence or exec-bit validation; let launchd fail noisily
  if the script is missing.
- No cross-plist batch operations (e.g. "shift all consolidations by 1 hour").
- Non-Scout plists are ignored; filter is strictly `com.scout.*.plist`.
- No UI for `RunAtLoad` / `KeepAlive` / other advanced launchd keys — they
  round-trip via `unknownKeys` but are not exposed.

## Open questions

None remaining after brainstorming. All decisions are locked:
1. Full CRUD scope.
2. Write to both `~/Library/LaunchAgents/` and `~/Scout/launchd/` on save.
3. Plist-per-row in the list; fires are edited inside the detail view.
4. Placement: new sidebar entry in `MainWindowView`.
5. Auto-reload via `launchctl` on save (errors surface as alert + retry).
6. Auto-commit via path-scoped `git commit`, with optional message override
   in a collapsed disclosure.
