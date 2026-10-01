# Action Items display profile: design

**Date:** 2026-10-01
**Status:** Proposed (for review). Docs only; code follows after approval.
**Surface:** Scout.app (Action Items tab, Settings). No engine or plugin change.
**Refs:** #52 (the customization half). The three rendering fixes from #52 ship
separately as a small fix PR.

## 1. Context

#52 asks for "density/layout options, which fields/metadata show, list-vs-board
default, sort/group options". On `main` today:

| Ask | Today |
|---|---|
| List vs Board default | `@SceneStorage("actionItemsView")` (`ActionItemsView.swift:13`): per-window restoration state, not a preference |
| Sort | None. Tasks render in source-file order |
| Group | Fixed: by section kind (Urgent, To Do, Watching, Personal), done tasks consolidated into Recently Completed |
| Fields | Fixed: relation chips, snooze pill, done pill always shown on the collapsed card |
| Density | None |

These are preferences a person sets once and expects everywhere: on a second
Mac, after a reinstall, and later in the iOS app, which reads the same vault.
That points at the vault, not `UserDefaults`. The app also deliberately holds no
YAML knowledge (`BudgetSettingsService.swift:47`), so the preferences cannot
live in `scout-config.yaml`. This spec adds one small JSON file in the vault
root that the app owns.

The file is meant to grow into a per-user display profile (sidebar order,
Control Center cards, presets). This spec reserves room for those sections and
implements the Action Items section only.

## 2. Goals and non-goals

### Goals

1. Default view (List or Board) that survives relaunch, kept in the vault.
2. Sort: file order (today), oldest first, alphabetical.
3. Group: by section (today), or one list.
4. Choose which fields show on a card: relation chips, snooze date, planned
   time, comment count.
5. Density: comfortable (today) and compact.
6. Every option can be changed in Settings and in place from the Action Items
   toolbar. Both write the same file.
7. The app never breaks because of the file. Missing, unreadable, or newer than
   the app all have a safe, visible fallback.
8. With no file, the tab renders exactly as it does today.

### Non-goals

- Other profile sections (sidebar, Control Center, presets, onboarding). Their
  keys are reserved, not implemented.
- Any engine or plugin change. Nothing outside the app writes the file yet.
- iOS reading the file (possible later at no extra cost, since it is in the
  vault).
- Persisting filters or search. They stay per-session `@State`.

## 3. File and schema

**Path:** `<scoutDirectory>/scout-profile.json`, always derived from
`AppState.scoutDirectory` and never from a hardcoded `~/Scout`, so a
configurable vault root (#104) needs no change here.

**A file the app has written after one change:**

```json
{
  "actionItems" : {
    "defaultView" : "board"
  },
  "schema" : 1
}
```

**Every key in schema 1:**

| Key | Values | Default (= today) |
|---|---|---|
| `schema` | integer | `1` |
| `actionItems.defaultView` | `list`, `board` | `list` |
| `actionItems.sort` | `fileOrder`, `oldestFirst`, `alphabetical` | `fileOrder` |
| `actionItems.group` | `section`, `none` | `section` |
| `actionItems.density` | `comfortable`, `compact` | `comfortable` |
| `actionItems.fields.refs` | boolean | `true` |
| `actionItems.fields.snooze` | boolean | `true` |
| `actionItems.fields.plan` | boolean | `true` |
| `actionItems.fields.comments` | boolean | `false` |

**Reserved top-level keys** (accepted without a warning, preserved, not read
yet): `sidebar`, `controlCenter`, `preset`, `axes`, `rhythm`.

The file records the user's choices only. A key the user never changed is not
written, so it keeps following the app's default if that default changes in a
later release. Keys are camelCase, like the other JSON the app writes. Output is
pretty-printed with sorted keys so a hand edit or a vault diff stays readable.

## 4. Reading and writing rules

The app reads the file at launch and on every change to it, and applies these
rules in order:

| Situation | What the app shows | What the app writes |
|---|---|---|
| File missing | Defaults | Nothing until the first change |
| File empty or whitespace only, or `{}` | Defaults, no warning | The first change fills it in |
| Valid file | The file's values; any missing key uses its default | Changes as a patch (below) |
| Known key with a wrong type or an unknown value (`"defaultView": "grid"`) | Default for that key only, rest of the file applies. Warning | Leaves that key as it is unless the user changes it |
| Unknown key at any level | Ignored. Warning | Preserved |
| `schema` missing | Treated as `1`. Warning | Adds `"schema": 1` on the next write |
| Not JSON, or the root is not an object | Last good profile (defaults at launch). Settings says the file can't be read | Nothing. Changes apply for this session only, and Settings says to fix or delete the file |
| `schema` greater than 1 | Same as unreadable: a newer app wrote it | Nothing, so an older app never downgrades a newer file |

**Writes are patches.** On a change the app re-reads the file from disk, sets
only the keys that changed, keeps every other key and value (unknown keys,
reserved sections, values it does not understand), and writes the result
atomically (`Data.write(to:options: .atomic)`). Re-reading first means a
hand edit to another key between two app changes is not lost. If two writers
change the same key, the last one wins.

**Schema bumps are for breaking changes only.** Adding a key or a value keeps
`schema: 1`, and older apps ignore what they don't know.

**Warnings** go to `os.Logger` (subsystem `com.scout.Scout`, category
`DisplayProfile`) once per distinct file content, and appear as one line in the
Settings section.

**Version control.** The vault is a git repo. After a change the app commits
only this path, `app: update display profile`, once the user has been idle for
2 seconds, so flipping List and Board a few times makes one commit, not five.
The commit is best effort through `GitServiceProtocol.commitPaths`, the same
call the proposals and per-file writers use.

## 5. Service

`Scout/Profile/DisplayProfileService.swift`, `@MainActor final class ...:
ObservableObject`, in the style of the other vault-backed services.

- `@Published private(set) var profile: DisplayProfile`
- `@Published private(set) var status: DisplayProfileStatus` (`.ok(warnings:)`,
  `.unreadable(reason:)`, `.unsupportedSchema(found:)`)
- `init(fileURL:fileEvents:gitService:commitDelay:)` reads the file once,
  synchronously. It is under 1 KB, and reading it before the first frame avoids
  the tab flashing from List to Board at launch.
- `startWatching()` subscribes to the file path itself through the injected
  `FileSystemEventSource`. Checked against FSEvents directly: a watch on a path
  that does not exist yet delivers its creation, atomic replacement, in-place
  edits, deletion, and re-creation, and nothing for sibling files. FSEvents
  matches on the real path, so the service resolves the vault directory
  (`realpath`) before building the file URL. Without that, a vault reached
  through a symlink gets no events.
- `update(_ change: (inout ActionItemsDisplay) -> Void)` applies the change in
  memory, publishes it, writes the patch if the status allows, and schedules the
  coalesced commit.
- The app's own write comes back through the watcher, re-parses to the same
  value, and does not republish.

Parsing and patching live in a pure, `nonisolated` `DisplayProfileCodec` so the
rules in section 4 are unit-tested without a file system:

- `decode(_ data: Data) -> DecodeResult` (profile plus warnings, or the
  unreadable/unsupported reason)
- `patch(_ existing: Data?, changes: [ProfileChange]) throws -> Data`

Wiring: one instance in `AppState`, built next to `GitService`
(`AppState.swift:102`) from `scoutDirectory`; `startWatching()` runs with the
other background work. It is injected as an `@EnvironmentObject` into the
Action Items tab and the Settings scene. No new `AppState.Configuration` field:
the path comes from `scoutDirectory`, and tests already pass a temporary one.

## 6. UI

### Action Items toolbar

- The List/Board segmented control binds to `actionItems.defaultView`. Switching
  view is the same action as setting the default; there is no separate
  per-window value.
- A new View menu (`slider.horizontal.3`) next to it: Sort, Group (List only),
  Density, and Show on cards (four toggles).

### Settings

A new Action Items section built from the existing atoms (`SettingsRow`,
`SettingsToggle`, and an inline menu `Picker` as used for the CLI terminal at
`SettingsView.swift:85`). It shows the same controls, the file path (from
`scoutDirectory`), and the warning or unreadable line in `DS.Status.warn` when
there is one.

### Rendering

- **Sort and group** are pure functions in
  `Scout/ActionItems/ActionItemsArrangement.swift`, applied after the existing
  done-task consolidation and filters, so List and Board keep seeing the same
  task set.
  - Sorting moves a top-level task together with the sub-tasks under it
    (`indentLevel > 0`), and ties keep file order.
  - Oldest first uses `carriedInFrom`; tasks that were not carried in sort last.
    Alphabetical uses `plainSubject` with `localizedStandardCompare`.
  - One list merges the open tasks of Urgent, To Do, Watching and Personal into
    a single section. Each card keeps its own priority stripe. Focus, Meetings,
    Recently Completed, Digest and unrecognized sections are unchanged.
  - The Board ignores grouping (its columns are the grouping) and applies the
    sort inside each column.
- **Fields** apply to the collapsed list card and to the board card. The
  expanded card always shows everything.
  - `refs`: the relation chips (GitHub, Linear, Slack, entity, cross-ref, plain)
    on the list card and the link labels on the board card. The "carried" chip
    is age, not a reference, and stays.
  - `snooze`: the snooze date pill (list) and moon icon (board).
  - `plan`: the planned-time chip from the plan marks work (separate PR). The
    key is part of schema 1 now so the schema does not change when that lands.
  - `comments`: a new chip with the comment count. Off by default so the
    default card does not change.
- **Compact density:** list card padding 14 → 8 points, no body preview on the
  collapsed card, every card starts collapsed (today Urgent starts expanded,
  `TaskCardView.swift:45`). Board card padding 12 → 8, title 3 → 2 lines.

## 7. Testing

All Swift Testing, `AppState.Configuration.testing()` with an isolated
`UserDefaults` suite, anonymized fixtures.

- **Codec:** parametrized decode cases zipped with their expected results:
  valid, empty object, missing keys, unknown keys at each level, reserved keys,
  wrong types, unknown enum values, not JSON, array root, `schema: 2`, missing
  `schema`. Patch cases: writes only changed keys, preserves unknown keys and
  values it does not understand, adds `schema` on first write, output is
  pretty-printed and sorted.
- **Service** with `InjectableFS` and a fake `GitServiceProtocol`: missing file
  stays missing; first change creates the file; a burst of changes makes one
  commit; a hand edit is picked up; a broken file keeps the last good profile
  and holds writes; a `schema: 2` file is never overwritten.
- **FileWatcher:** a watch on a not-yet-existing file path reports its creation
  and an atomic replace.
- **Arrangement:** defaults return the input unchanged; sorting keeps sub-tasks
  under their parent; ties are stable; one list keeps each task's kind; Board
  columns are sorted.
- **Rendering:** about 10 profiles chosen so every pair of values (density,
  view, sort, group, each of the four fields, light and dark) appears together
  at least once, rendered through `SmokeVault` and `ViewHost`, plus the default
  profile on its own. The full cross product would be 768 renders.
- **Manual,** against a real vault in a Debug build: no file; first change
  creates it and commits; a hand edit applies live; a broken file does not break
  the app; unknown keys survive an in-app change; a vault without the file
  behaves as today.

## 8. Migration and compatibility

- **Existing vaults:** no file, so defaults, so today's rendering. Launch writes
  nothing.
- **`@SceneStorage("actionItemsView")`** is removed, not migrated. It was window
  restoration state; someone who last had Board open sees List once, and one
  click sets the default and creates the file.
- **Older app versions** ignore the file. **Newer versions** add keys under
  schema 1 and older apps preserve them.
- **Vault git:** the app commits the file with a path-scoped commit. A run that
  commits the whole vault picks it up as well; there is no conflict.
- **Cold start:** one synchronous read of a file under 1 KB during `AppState`
  init.
- **iOS:** unaffected. It can read the file later.

## 9. Decisions recorded during design

- **A file in the vault root, not `UserDefaults`.** The preferences follow the
  vault across machines and later to iOS, and can be written by something other
  than the app (a setup step, a Claude session) without app changes.
- **JSON the app owns, not a block in `scout-config.yaml`.** The app holds no
  YAML knowledge, and the config file has several writers already.
- **Not `knowledge-base/profile/`.** That path is proposed for profile content
  the AI sessions read (plugin #176); display settings are a different reader.
- **Patches, not whole-file writes.** Preserves what the app does not understand
  and lets app defaults evolve for keys the user never touched.
- **Fail closed on unreadable or newer files.** The app never overwrites a file
  it cannot fully read.
- **Fields hide on the collapsed card only.** Detail stays one click away, so
  hiding a field never makes information unreachable.

## 10. Open questions for review

1. **File name.** `scout-profile.json` anticipates the later sections (sidebar,
   Control Center, presets). Plugin #176 proposes a `/scout-profile` command for
   the AI-session profile. Keep the name, or prefer something narrower such as
   `scout-display.json`?
2. **Commit from the app.** The proposal commits the file after a 2-second
   quiet period. The alternative is to leave it to the next run that commits the
   vault, at the cost of the change sitting uncommitted until then.

## 11. Follow-ups (out of scope)

- Sidebar order and visibility, Control Center cards, presets, first-run setup
  of the profile.
- A setup step or Claude session writing the file.
- iOS reading the file.
