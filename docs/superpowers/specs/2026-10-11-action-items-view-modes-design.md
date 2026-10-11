# Action Items view modes: one model for List, Board and Topics

**Date:** 2026-10-11
**Status:** Draft for review (coordinator). Docs only.
**Surface:** Scout.app (`apps/macos/`). Paths are relative to `apps/macos/`.
**Refs:** #290 and PR #320 (display profile); #340 and PRs #341, #342, #343, #345 (topic board).

## 1. Why

Two contributions change the Action Items screen at the same time:

- **#320** (display profile) adds a vault file the app owns, holding a default view (`list|board`), sort, grouping, density and which fields cards show. The List/Board switch becomes session state held in a service, replacing `@SceneStorage("actionItemsView")`.
- **#343** (topic board, stacked on #342's task fields) adds a third case, `.topics`, to the same `ActionItemsViewMode`. It keeps its legend filter, Done range and open groups in per-Mac `@AppStorage`.

Each PR is sound on its own. Together they leave three things undecided:

1. whether `defaultView` can be `topics`;
2. which of #320's options apply in Topics;
3. where per-mode settings live: the vault file, per-Mac storage, or nowhere.

This note settles them, so the PRs can land in either order.

## 2. The model

### 2.1 One mode enum, three cases

`ActionItemsViewMode` has `list`, `board` and `topics` (#343's addition). There is one switch in the toolbar (`EditorialSegmentedControl`), and every mode reads the same loaded document from `ActionItemsDocumentService`. No mode gets its own loader or writer. Writes go through `ActionItemsWriter` / `scoutctl`, as today.

### 2.2 Where state lives

Three tiers, decided by one question: *would the person expect this to follow them to another Mac, or to the iOS app?*

| Tier | Storage | What goes there |
|---|---|---|
| **Preference** | #320's vault display-profile file (patch writes, unknown keys kept, never committed by the app) | Choices a person sets once: the default view, sort, grouping, density, fields, and the Topics Done range default |
| **Session** | In memory (`DisplayProfileService` for the current view; `@State` for filters and search) | The current mode, filters, search, selection |
| **Per-Mac UI memory** | `@AppStorage` / `@SceneStorage` | What is expanded or collapsed (Topics' open groups, card expansion). Losing it costs nothing |

Consequences for the open PRs:

- **`defaultView` accepts `topics`.** Under #320's §4 rules this is a new value with no schema bump. An older app that reads `"defaultView": "topics"` falls back to `list` with its existing warning, which is the safe behaviour #320 already specifies.
- **Topics' legend filter becomes session state**, matching #320's non-goal "filters stay per-session". #343 drops `topicBoard.filter` from `@AppStorage`. If the Topics view should open with only Mine/Doing/Waiting showing (#340 §3), that is the *default filter*, not a remembered one.
- **Topics' Done range** (today / yesterday / this week) is a preference: `actionItems.topics.doneRange` in the profile file, with #343's `DoneRange` raw values (`today`, `yesterday`, `week`) and default `week`. Changing it from the toggle on the Topics screen writes it, the way #320's View menu writes sort or density.
- **Topics' open groups** stay in `@AppStorage` (`topicBoard.open`) as per-Mac UI memory.

### 2.3 Which options apply in which mode

The View menu shows only the options that apply in the current mode. Settings shows all of them, with a short note on which modes each one affects.

| Option | List | Board | Topics |
|---|---|---|---|
| `sort` (file order / A-Z) | Yes | Within each column | No: Topics has its own priority order (#340 §4) |
| `group` (section / one list) | Yes | Ignored (columns are the grouping) | Ignored (topics are the grouping) |
| `density` | Yes | Yes | Ignored: rows are always compact and fixed-height (#340 §5) |
| `fields.refs` | Relation chips | Link labels | PR chips on the row; Linear and Slack links stay in the expanded row |
| `fields.snooze` | Snooze pill | Moon icon | No-op: a snoozed task shows in On hold (§2.4) |
| `fields.comments` | Count chip | Count chip | Count chip on the row |
| `topics.doneRange` | n/a | n/a | Yes |

### 2.4 One task state, computed in one place

Topics groups by an effective state. cjayyy's rule from #340, in order: a ticked task is **done**, then an active snooze makes it **hold**, then its `State:` field, then its section (🔴/🟡 → **mine**, 🟢 → **waiting**). #343 already implements that rule as `BoardState.of(_:in:today:)` in `TopicBoard/BoardState.swift`. It takes the section kind as well as the task, because the section is the last fallback. The rule (the `BoardState` enum and `of`) moves to `Models/`, next to #342's `TaskFields`, so it isn't private to the Topics code. Then:

- List and Board can show the same state later without re-deriving it;
- filters, counts and "Ask Claude" prompts agree across modes;
- the engine's parser contract stays the one source for what a field means (#342 owns that).

### 2.5 One filtered task set

#320's arrangement order is: done-task consolidation, then filters and search, then sort/group. Topics hooks in at the same point: it consumes the consolidated, searched task set, then applies its state filter and topic grouping. Search typed in one mode gives the same matches in another.

## 3. What each PR does

**#320 (display profile), whenever its code PR opens:**
- Add `topics` to `defaultView`'s accepted values, and `actionItems.topics.doneRange` (`today|yesterday|week`, default `week`) to the schema table.
- The View menu shows the options in §2.3 for the current mode only.
- Decide the file name, the question #320 already asks. The coordinator recommends **`scout-display.json`**: #176's `/scout-profile` is about the AI-session profile, and a narrower name avoids two "profiles".

**#343 (Topics view mode):**
- Move the legend filter from `@AppStorage("topicBoard.filter")` to session state with a default (Mine/Doing/Waiting).
- Read and write the Done range through the display profile when #320 has landed. Until then, keep `@AppStorage("topicBoard.doneRange")`, and migrate its value into the profile once when it first exists.
- Keep `topicBoard.open` in `@AppStorage`.
- Move `BoardState` and its `of(_:in:today:)` rule from `TopicBoard/` to `Models/`. Its style extension (`BoardState+Style.swift`) can stay with the Topics views.

**Whichever lands second** rebases onto the other and does the obligations above that touch the other's code. Neither PR changes the other's files beyond that.

## 4. Shared files: who touches what

| File | #320 | #343 | #325 (context notes) |
|---|---|---|---|
| `ActionItemsViewMode.swift` | reads | adds `topics` | — |
| `ActionItemsView.swift` | current view moves to the service; View menu | Topics branch; hides `FilterChipsView` in Topics | — |
| `Views/TaskCardView.swift` | density, fields | — | +3 lines in `detail` |
| `Views/BoardCardView.swift` | density, fields | — | — |
| `Models/ActionTask.swift` | — | fields | — |
| `Models/BoardState.swift` (moved from `TopicBoard/`) | — | the shared state rule | — |

#325 is likely to merge first. Its `TaskCardView` change is three lines in the expanded detail, which #320's density and fields don't touch.

## 5. Out of scope

- Whether List and Board stay once Topics reaches parity. #340 defers that decision.
- The live PR-status poller and event-driven refreshes (#340's later phase).
- iOS reading the display file. It comes for free later, because the file is in the vault.
