# Action Items display profile: design

**Date:** 2026-10-01 (revised 2026-10-03 after review, updated 2026-10-05)
**Status:** Proposed (for review). Docs only; code follows after approval.
**Surface:** Scout.app in `apps/macos/` (Action Items tab, Settings). Paths below are relative to `apps/macos/`. No engine or plugin change.
**Refs:** #290 (the customization half; it was Raven-Scout/scout-app-legacy#52 before the move).
The three rendering fixes from the same issue shipped in Raven-Scout/scout-app-legacy#122.

> **Revised 2026-10-03 per review.** "Oldest first" is dropped: no daily file
> carries the `_(carried in from YYYY-MM-DD)_` marker it sorted on. `fields.plan`
> and the reserved top-level keys are dropped; each key arrives with the feature
> that uses it. Sub-tasks whose parent is filtered out stand alone (section 6).
> The arranged sections are cached (section 6). The app never commits the file,
> and the List/Board switch no longer writes it: it changes the current view
> only, and the default view is set in Settings (sections 4 and 6).

## 1. Context

#290 asks for "density/layout options, which fields/metadata show, list-vs-board
default, sort/group options". On `main` today:

| Ask | Today |
|---|---|
| List vs Board default | `@SceneStorage("actionItemsView")` (`ActionItemsView.swift:13`): restores the last view, there is no default to choose |
| Sort | None. Tasks render in source-file order |
| Group | Fixed: by section kind (Urgent, To Do, Watching, Personal), done tasks consolidated into Recently Completed |
| Fields | Fixed: relation chips, snooze pill, done pill always shown on the collapsed card |
| Density | None |

These are preferences a person sets once and expects everywhere: on a second
Mac, after a reinstall, and later in the iOS app, which reads the same vault.
That points at the vault, not `UserDefaults`. The app also deliberately holds no
YAML knowledge (`BudgetSettingsService.swift:50`), so the preferences cannot
live in `scout-config.yaml`. This spec adds one small JSON file in the vault
root that the app owns.

The file can later hold more of a per-user display profile (sidebar order,
Control Center cards). This spec implements the Action Items section only; other
sections are added together with the features that read them.

## 2. Goals and non-goals

### Goals

1. A default view (List or Board) chosen in Settings, kept in the vault.
2. Sort: file order (today) or alphabetical.
3. Group: by section (today), or one list.
4. Choose which fields show on a card: relation chips, snooze date, comment
   count.
5. Density: comfortable (today) and compact.
6. Every option can be changed in Settings. Sort, grouping, density and fields
   can also be changed from a View menu in the Action Items toolbar. Both write
   the same file. The List/Board switch in the toolbar changes the current view
   only and writes nothing.
7. The app never breaks because of the file. Missing, unreadable, or newer than
   the app all have a safe, visible fallback.
8. With no file, the tab renders exactly as it does today.

### Non-goals

- Other profile sections (sidebar, Control Center, presets, onboarding).
- Sorting by task age. The vault has no per-task date the app can read; this can
  follow once the engine reports task age.
- A planned-time field. It belongs with the planned-time chip, which is not on
  `main` yet.
- Any engine or plugin change. Nothing outside the app writes the file yet.
- Committing the file from the app (section 4).
- iOS reading the file (possible later at no extra cost, since it is in the
  vault).
- Persisting filters or search. They stay per-session `@State`.

## 3. File and schema

**Path:** `<scoutDirectory>/scout-profile.json`, always derived from
`AppState.scoutDirectory` and never from a hardcoded `~/Scout`, so a
configurable vault root (Raven-Scout/scout-app-legacy#104) needs no change here.

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
| `actionItems.sort` | `fileOrder`, `alphabetical` | `fileOrder` |
| `actionItems.group` | `section`, `none` | `section` |
| `actionItems.density` | `comfortable`, `compact` | `comfortable` |
| `actionItems.fields.refs` | boolean | `true` |
| `actionItems.fields.snooze` | boolean | `true` |
| `actionItems.fields.comments` | boolean | `false` |

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
only the keys that changed, keeps every other key and value (unknown keys and
values it does not understand), and writes the result atomically
(`Data.write(to:options: .atomic)`). Re-reading first means a hand edit to
another key between two app changes is not lost. If two writers change the same
key, the last one wins. Because unknown keys are preserved, a section a later
release adds survives being edited by an older app.

**Schema bumps are for breaking changes only.** Adding a key or a value keeps
`schema: 1`, and older apps ignore what they don't know.

**Warnings** go to `os.Logger` (subsystem `com.scout.Scout`, category
`DisplayProfile`) once per distinct file content, and appear as one line in the
Settings section.

**No commits from the app.** The app writes the file and leaves it. The next
Scout session that commits the vault (`git add -A`) picks it up. The app never
competes with a running session for `index.lock` over this file, and the
service needs no `GitService`. Writes only happen on deliberate preference
changes (Settings, the View menu), never on the List/Board switch.

## 5. Service

`Scout/Profile/DisplayProfileService.swift`, `@MainActor final class ...:
ObservableObject`, in the style of the other vault-backed services.

- `@Published private(set) var profile: DisplayProfile`
- `@Published private(set) var status: DisplayProfileStatus` (`.ok(warnings:)`,
  `.unreadable(reason:)`, `.unsupportedSchema(found:)`)
- `init(scoutDirectory:fileEvents:)` reads the file once, synchronously. It is
  under 1 KB, and reading it before the first frame avoids the tab flashing
  from List to Board at launch.
- `startWatching()` subscribes to the file path itself through the injected
  `FileSystemEventSource`. Checked against FSEvents directly: a watch on a path
  that does not exist yet delivers its creation, atomic replacement, in-place
  edits, deletion, and re-creation, and nothing for sibling files. FSEvents
  matches on the real path, so the service resolves the vault directory
  (`realpath`) before building the file URL. Without that, a vault reached
  through a symlink gets no events.
- `update(_ change: (inout ActionItemsDisplay) -> Void)` applies the change in
  memory, publishes it, and writes the patch if the status allows.
- The app's own write comes back through the watcher, re-parses to the same
  value, and does not republish.

Parsing and patching live in a pure, `nonisolated` `DisplayProfileCodec` so the
rules in section 4 are unit-tested without a file system:

- `decode(_ data: Data) -> Result<Decoded, Failure>` (profile plus warnings, or
  the unreadable/unsupported reason)
- `patch(_ existing: Data?, with entries: [DisplayProfile.Entry]) throws -> Data`

Wiring: one instance in `AppState`, built from `scoutDirectory` and the shared
`FileSystemEventSource`; `startWatching()` runs with the other background work.
It is injected as an `@EnvironmentObject` into the Action Items tab and the
Settings scene. No new `AppState.Configuration` field: the path comes from
`scoutDirectory`, and tests already pass a temporary one.

## 6. UI

### Action Items toolbar

- The List/Board switch changes the view for the current session and writes
  nothing. The tab opens in the profile's `defaultView` at launch. The current
  choice lives in memory in `DisplayProfileService`, so it survives switching
  sidebar sections and resets to the default on relaunch. (The main window is a
  single `Window` scene, so there is no second window whose state could diverge.)
- A new View menu (`slider.horizontal.3`) next to it: Sort, Group (List only),
  Density, and Show on cards (three toggles). These write the profile.

### Settings

A new Action Items section built from the existing atoms (`SettingsRow`,
`SettingsToggle`, and an inline menu `Picker` as used for the CLI terminal at
`SettingsView.swift:78`). It shows Default view and the same controls as the
View menu, the file path (from `scoutDirectory`), and the warning or unreadable
line in `DS.Status.warn` when there is one.

### Rendering

- **Sort and group** are pure functions in
  `Scout/ActionItems/ActionItemsArrangement.swift`, applied after the existing
  done-task consolidation and filters, so List and Board keep seeing the same
  task set.
  - Sorting moves a top-level task together with its sub-tasks (`indentLevel >
    0`), and ties keep file order. Alphabetical uses `plainSubject` with
    `localizedStandardCompare`.
  - A sub-task belongs to its parent in the source file, worked out before any
    filter runs. When a search or status filter removes the parent and keeps the
    sub-task, the sub-task is sorted as its own entry. It never attaches to an
    unrelated task above it, and in One list it never crosses into another
    section's task.
  - One list merges the open tasks of Urgent, To Do, Watching and Personal into
    a single section. Each card keeps its own priority stripe and its source
    kind for snooze's `--from-kind`. Focus, Meetings, Recently Completed, Digest
    and unrecognized sections are unchanged.
  - The Board ignores grouping (its columns are the grouping) and applies the
    sort inside each column.
  - **Cached.** The arranged sections are stored in view state and recomputed
    only when the document, the filter or the profile changes, the same way
    `visibleSelectableIDs` is cached (Raven-Scout/scout-app-legacy#83,
    Raven-Scout/scout-app-legacy#88). The default profile returns the input
    unchanged, so it costs nothing either way.
  - **Task windows.** The List builds one page of rows per section
    (`TaskWindow`), keyed by the drawn section. Select all, the visible-row check
    and reopen therefore read the arranged sections, not the file order: under
    A-Z, Select all takes the first page on screen, and One list pages the
    merged list once instead of each source section.
- **Fields** apply to the collapsed list card and to the board card. The
  expanded card always shows everything.
  - `refs`: the relation chips (GitHub, Linear, Slack, entity, cross-ref, plain)
    on the list card and the link labels on the board card. The "carried" chip
    is age, not a reference, and stays.
  - `snooze`: the snooze date pill (list) and moon icon (board).
  - `comments`: a new chip with the comment count. Off by default so the
    default card does not change.
- **Compact density:** list card padding 14 → 8 points, no body preview on the
  collapsed card, every card starts collapsed (today Urgent starts expanded,
  `TaskCardView.swift:51`). Board card padding 12 → 8, title 3 → 2 lines.
  A density change re-applies the start state to the cards already on screen,
  so choosing Compact collapses open cards at once. A card whose caller fixed
  its state (`startsExpanded:`, as in Recently Completed) keeps it.

## 7. Testing

All Swift Testing, `AppState.Configuration.testing()` with an isolated
`UserDefaults` suite, anonymized fixtures.

- **Codec:** parametrized decode cases zipped with their expected results:
  valid, empty object, missing keys, unknown keys at each level, wrong types,
  unknown enum values, not JSON, array root, `schema: 2`, missing `schema`.
  Patch cases: writes only changed keys, preserves unknown keys and values it
  does not understand, adds `schema` on first write, output is pretty-printed
  and sorted.
- **Service** with `InjectableFS`: missing file stays missing; first change
  creates the file; a hand edit is picked up; a broken file keeps the last good
  profile and holds writes; a `schema: 2` file is never overwritten; switching
  the current view writes nothing.
- **FileWatcher:** a watch on a not-yet-existing file path reports its creation
  and an atomic replace.
- **Arrangement:** defaults return the input unchanged; sorting keeps sub-tasks
  under their parent; ties are stable; a sub-task whose parent was filtered out
  stands alone and does not attach to the task above it, within a section and
  across sections in One list; one list keeps each task's kind; Board columns
  are sorted.
- **Rendering:** 7 profiles chosen so every pair of values (view, density,
  sort, group, each of the three fields, light and dark) appears together at
  least once, rendered through `SmokeVault` and `ViewHost`, plus the default
  profile on its own. The full cross product would be 256 renders.
- **Manual,** against a real vault in a Debug build: no file; a change in
  Settings creates it; a hand edit applies live; a broken file does not break
  the app; unknown keys survive an in-app change; a vault without the file
  behaves as today; the List/Board switch leaves the file untouched.

## 8. Migration and compatibility

- **Existing vaults:** no file, so defaults, so today's rendering. Launch writes
  nothing.
- **`@SceneStorage("actionItemsView")`** is replaced. It restored the last view
  across relaunches; now the tab opens in the chosen default, and the toolbar
  switch still changes the view for the session. Someone who wants to land on
  Board picks it once as the default in Settings.
- **Older app versions** ignore the file. **Newer versions** add keys under
  schema 1, and older apps preserve them.
- **Vault git:** the app does not commit. The next session that commits the
  vault picks the file up.
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
  the AI sessions read (#176); display settings are a different reader.
- **Patches, not whole-file writes.** Preserves what the app does not understand
  and lets app defaults evolve for keys the user never touched.
- **Fail closed on unreadable or newer files.** The app never overwrites a file
  it cannot fully read.
- **No commits, no writes on the List/Board switch** (review, 2026-10-03). Writes
  are rare and deliberate, and committing is left to the sessions that already
  commit the vault.
- **Keys arrive with their features** (review, 2026-10-03). No reserved keys;
  unknown keys are preserved, which is what keeps later sections safe.
- **Fields hide on the collapsed card only.** Detail stays one click away, so
  hiding a field never makes information unreachable.

## 10. Open questions for review

1. **File name.** `scout-profile.json` anticipates later sections (sidebar,
   Control Center). #176 proposes a `/scout-profile` command for the
   AI-session profile. Keep the name, or prefer something narrower such as
   `scout-display.json`?

## 11. Follow-ups (out of scope)

- Sorting by task age, once the engine reports it.
- A planned-time field, together with the planned-time chip.
- Sidebar order and visibility, Control Center cards, presets, first-run setup
  of the profile.
- A setup step or Claude session writing the file.
- iOS reading the file.
