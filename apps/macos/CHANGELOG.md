# Changelog — Scout for macOS

Scout's app and plugin share one version and one release (`vX.Y.Z`), cut with `scripts/release.sh`. Each
release's notes combine this file's section with [`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md).
Keep an `## [Unreleased]` section at the top. `scripts/release.sh prepare` promotes it.

## [Unreleased]

### Added
- **A Health page that reads the run ledger** (UI audit card C8; `Scout/Health/*`, `Shell/MainWindowView.swift`, `Shell/SidebarView.swift`, `Shell/AppState.swift`). A new sidebar row, Health, gets a warning dot when run health is red. The page reads `scoutctl runs --json` at launch, whenever `.scout-logs/run-outcomes.jsonl` changes, and every 5 minutes.
  - **Findings.** Every run-health finding is grouped as "Needs fixing" or "Worth a look". Each shows the run's own error line, "How to fix" and Show log. Notes and the doctor's informational lines go under a collapsed "Everything else".
  - **Recent runs.** The last two weeks of runs, in plain words: "Skipped: privacy checks didn't load", "Stopped: took too long", "Paused: usage limit" (with when it resets). Each run shows its length (awake time), agent and model. A line is added when a run went without write protection or Scout had to put a protected file back. "Needs attention" hides routine runs and skips.
  - **Older engines.** An engine without `scoutctl runs` gets an "update the engine" notice instead of an error.
  - All the wording lives in `Health/RunOutcomeDisplay.swift`, copied from the plain-language pass (C4) so that card can absorb it.

### Changed
- **The Control Center takes run statuses from the run ledger.** A run the ledger records shows the ledger's outcome, matched by its log file name. Safety skips (privacy checks, write protection, agent runtime, writer lease) and redundant skips now show as skips instead of "running" and then "orphaned". `RunStatus` gains `skippedGates`, `skippedSandbox`, `skippedRuntime`, `skippedLease` and `skippedRedundant`. Runs with no ledger row keep their log-derived status: older engines, or a run still going.
- **`DoctorReport` decodes the doctor's `notes`.**

### Fixed
- **The usage card counts Scout's own runs, once each** (`Scout/ControlCenter/UsageRailCard.swift`, `Scout/Services/ScoutUsageService.swift`, `Scout/Models/ScoutUsage.swift`, `Scout/Shell/StatusBarView.swift`, `Scout/Shell/AppState.swift`). On a real vault the card showed about 270,000M tokens "today", rising by about 2,000M a minute, with $0 cost. It summed the vault's per-session token log on its own. It now shows the engine's figures (`scoutctl budget usage --json`): finished scheduled runs only, never your own Claude sessions, each message counted once. Input and output are shown apart from cache reads and writes, in K/M/B/T units, and the day and week follow the vault's timezone instead of New York's. The cost line says how the figure was found (Claude Code's figure, estimated, at least $X when some runs reported no cost, or unknown), so an unmeasured $0 no longer reads as a measurement. The status bar's cost cell ("runs today") uses the same figure. The "Quota: TBD (Phase 2)" placeholder is gone. It re-reads the figures when the usage tracker changes and when the vault's day ends, so "today" doesn't carry yesterday's figures past midnight. It needs an engine with this release; with an older one the card says it needs a newer Scout engine.
- **The run-log parse cache is one file per build and vault** (`Scout/Services/SessionLogParseCache.swift`). It was one fixed `~/Library/Caches/Scout/session-parse-cache.json` for every build and vault, so a dev build or a second vault rewrote the installed app's cache and each re-parsed every log at launch. It now lives at `~/Library/Caches/Scout/parse-cache/<bundle id>/<vault key>.json`, the vault key a hash of the vault's path. The shipping app (`com.scout.Scout`) deletes the old shared file once at launch; a dev build never does, so it can't remove the file an installed app still reads.
- **A failed schedule load can no longer lead to an overwrite** (`Scout/Services/ScheduleEditService.swift`, `Scout/Services/StaleScheduleError.swift`, `Scout/Schedules/SchedulesView.swift`, `Scout/Schedules/SchedulesHeader.swift`). When `scoutctl schedule list` failed, the Schedules page showed the empty state with "+ Add slot", and saving from there wrote a `schedule.yaml` holding only the new slot, because the stale-file check was skipped whenever no file time had been recorded. A failed load now shows "Couldn't load the schedule" with Retry, the New button is off until a load succeeds, and the app refuses to save or delete unless the last load succeeded. The stale check always runs: a file that appeared, vanished or changed time in either direction since the load is stale, and the file time is taken before `scoutctl` reads the file. A schedule file that exists but can't be read stops the save instead of being rewritten without its header.
- **Unsaved knowledge-base edits survive navigation** (`Scout/KnowledgeBase/KBDraftStore.swift`, `Scout/KnowledgeBase/Views/KBEditorView.swift`, `Scout/KnowledgeBase/KnowledgeBaseService.swift`, `Scout/AppDelegate.swift`). Opening another note, a wikilink, a backlink, a search hit, the overview or another page used to drop the draft. The app now keeps each note's unsaved draft while it runs and restores it when you come back, with the "unsaved" marker, and still warns if the file changed on disk in the meantime. Renaming a note carries its draft along, including when the file moved but the git commit failed (that error now shows; it used to vanish with the editor). Quitting with unsaved drafts asks first and names the notes.
- **Esc on "File changed on disk" keeps your edits** (`Scout/KnowledgeBase/Views/KBEditorView.swift`). "Reload" had the cancel role, so Esc discarded the edits. The alert now offers Overwrite, Keep my edits (the cancel action) and Discard my edits and reload.
- **The KB editor never shows an unreadable file as an empty note** (`Scout/KnowledgeBase/Views/KBEditorView.swift`). A file that exists but can't be read (not UTF-8, or unavailable) shows "Couldn't read" with Retry instead of a blank editor whose text Overwrite could have written over the file.
- **Read mode's inline edits are part of the note's draft as you type** (`Scout/KnowledgeBase/Views/KBEditableView.swift`). Edits to a block or a table cell used to sit in the inline editor until you clicked its confirm button, so navigating away, quitting or pressing Esc lost them. Each keystroke now goes into the draft, so the note shows "unsaved", the edit survives navigation and the quit prompt counts it. Esc closes the editor and keeps the change; a Discard button puts the block back. While an inline edit is open, "Send to Scout" waits, since it saves the note at once. The confirm button now says Done, not Save: it only closes the editor, and Save (⌘S) writes the file.
- **A failed comment keeps its text** (`Scout/ActionItems/Views/CommentComposerView.swift`, `Scout/ActionItems/Views/CommentListView.swift`, `Scout/ActionItems/Views/TaskCardView.swift`). Adding or editing a comment cleared the text before the write, so a failed write lost it. The text now stays in the open editor until the write lands.
- **Quick-action errors show on collapsed cards** (`Scout/ActionItems/Views/TaskCardView.swift`). A failed Done, Snooze or Reopen from a collapsed card's header now shows its error under the header instead of only inside the expanded card.
- **The run Feedback tab never saves over a note it couldn't read** (`Scout/ControlCenter/Detail/FeedbackTab.swift`). An unreadable note loaded as empty text, and the next save deleted it. It now shows "Couldn't read the saved note" and turns editing off. Opening a note or switching runs no longer rewrites it, and an edit typed just before switching runs is saved to its own run's note.
- **An unreadable connector-acknowledgement file is set aside, not replaced** (`Scout/Services/ConnectorAckStore.swift`). A torn `.scout-cache/connector-alerts-acked.json` loaded as no acknowledgements, and the next acknowledgement overwrote the file. The app now moves it to `connector-alerts-acked.json.unreadable` before writing, and reads dates with fractional seconds too.
- **Saving a schedule slot keeps its agent runtime** (`Scout/Models/Slot.swift`, `Scout/Schedules/SlotDraft.swift`, `Scout/Services/ScheduleEditService.swift`). The app rewrote `schedule.yaml` without the `agent_runtime:` a slot may name, so any save in Schedules silently reset every slot to the default agent. The slot now carries it from `scoutctl schedule list --json` through the editor and writes it back unchanged; a slot without one stays without one. The slot form shows it read-only as "Runs on Claude Code" (or "Runs on Scout's default agent"); choosing an agent comes later. It needs an engine with this release: an older engine doesn't report the field, so a save there still drops it.

## [0.18.15] - 2026-11-16

## [0.18.14] - 2026-11-16

### Fixed
- **Connector alerts reach the banner again** (`Scout/Models/ConnectorAlert.swift`, `Scout/Services/ConnectorHealthService.swift`). The app parsed `.scout-logs/connector-alerts.log` as the pipe-delimited lines of the retired bash report; the engine's Python report writes a different line, so every alert was dropped and the banner and its acknowledgements never showed anything. The app now reads the engine's `.scout-logs/connector-alerts.json`, which holds the current alerts with a `first_seen` per alert, so an acknowledgement lasts for that alert's streak (runs that don't call the connector don't end it) and an alert a later run clears leaves the banner. A missing, torn or unknown-schema state file no longer prunes acknowledgements. It needs an engine with this release; an older one writes no such file, and the banner stays empty as before. The test fixture is now a byte copy of the engine's generated contract fixture.
- **Control Center's session list reaches past its newest 30 runs** (`Scout/ControlCenter/RunWindow.swift`, `Scout/ControlCenter/SessionsListView.swift`). The list showed the first 30 runs that passed the filters and nothing else, so an older run could not be opened from the app. It now shows the same first 30, then "Show 30 more" and "Showing 30 of N" below them. Changing the search, the type filter or the day goes back to one page.

## [0.18.13] - 2026-11-15

## [0.18.12] - 2026-11-13

## [0.18.11] - 2026-11-09

## [0.18.10] - 2026-11-07

## [0.18.9] - 2026-11-05

## [0.18.8] - 2026-11-05

## [0.18.7] - 2026-11-04

### Added
- **A planned task shows its `/scout-plan` time on the card** (`Scout/ActionItems/Models/TaskPlan.swift`, `Scout/ActionItems/ActionItemsParser.swift`, `Scout/ActionItems/Models/ActionTask.swift`, `Scout/ActionItems/Views/TaskCardView.swift`, `Scout/Profile/DisplayProfile.swift`, `Scout/Shell/ActionItemsDisplaySection.swift`). The `- estimate:`, `- block:` and `- actual:` lines `/scout-plan` writes under a task are read into the task instead of showing as three comments. An open task gets a clock chip in its header: `10:00-10:45 · 45m`, with the weekday first when the block isn't today, `· took 1h` once the actual time is logged, or just `45m` for an estimate without a block. A done task reads `Done · 1h`. The tooltip spells out the date, estimate, raw estimate, kind and actual. The app reads exactly the lines the engine treats as markers, so a hand-written `- block: waiting on Priya` stays a comment. `scoutctl`'s `--index` still counts the markers, so editing or deleting a comment adds the markers above it back into the index it sends, and the right comment changes with any engine version. Settings → Action Items has a Planned time switch (`actionItems.fields.plan` in `scout-display.json`, on by default) that hides the chip and the actual time on done tasks. A snoozed task never shows the chip, also when Snooze date is off.

## [0.18.6] - 2026-10-31

## [0.18.5] - 2026-10-30

### Added
- **Text in Action Items cards can be selected and copied with ⌘C** (`Scout/ActionItems/Views/TaskBodyView.swift`, `Scout/ActionItems/Views/TaskDetailsView.swift`, `Scout/ActionItems/Views/CommentListView.swift`, `Scout/ActionItems/Views/TaskCardView.swift`, `Scout/ActionItems/Views/BoardCardView.swift`, `Scout/ActionItems/TopicBoard/StepDetailView.swift`). In an open List card, the body, the sub-bullets, the comments and nested sub-tasks are selectable, like a context note already was. So are a Board card's tag, subject and link labels, and a Topics step's detail. Before, the only way to take text out of a card was the Copy button, which copies the whole task. A card's title and its collapsed summary stay click targets that open and close the card, so they aren't selectable. Links in selectable text open as before.
- **Action Items display options, kept in `scout-display.json`** (#290, spec #320; `Scout/Profile/DisplayProfile.swift`, `Scout/Profile/DisplayProfileCodec.swift`, `Scout/Profile/DisplayProfileService.swift`, `Scout/Profile/ActionItemsDisplayMenu.swift`, `Scout/ActionItems/ActionItemsArrangement.swift`, `Scout/ActionItems/ActionItemsLayout.swift`, `Scout/Shell/ActionItemsDisplaySection.swift`). Action Items can open in List, Board or Topics, sort tasks A to Z instead of file order, show one list instead of sections, use compact cards, and show references, the snooze date and a comment count on collapsed cards. Change them in Settings ▸ Action Items, or from the View menu next to the view switch, which shows only what applies to the current mode. The options live in `scout-display.json` in the vault root: the app writes only the keys you changed, keeps keys it doesn't know, and never commits the file. Without the file Action Items looks as before. A hand edit applies while the app runs, and a file the app can't read keeps the last good options and isn't overwritten. Settings also has the Topics Done range, saved as `actionItems.topics.doneRange`. The Topics view doesn't read it yet.

### Fixed
- **A `## 🎯 Today's Focus` section shows as Today's Focus** (`Scout/ActionItems/ActionItemsParser.swift`). The engine's phases gave the heading as both 💡 and 🎯, and about half of daily files used 🎯, which the parser read as an ordinary section. 🎯 now maps to the focus section like 💡; the phases now write 💡 only.

## [0.18.4] - 2026-10-27

### Added
- **The Knowledge Base tree shows a lock on private notes** (Raven-Scout/Scout#453; `Scout/KnowledgeBase/KBPrivacyLabels.swift`, `Scout/KnowledgeBase/Views/KBTreeView.swift`, `Scout/KnowledgeBase/Views/KnowledgeBaseView.swift`, `Scout/Shell/AppState.swift`). A note labelled `private` (its `visibility:` line, or a path default such as `knowledge-base/personal/`) gets a small lock next to its name. The app doesn't read frontmatter for this: it asks the engine, `scoutctl kb classify --reader owner --json`, for the files in open folders, up to 200 files per call. Each label is cached by the file's modification date, size and inode, so after a reparse only changed files are asked about again. A changed `scout-config.yaml` (its `privacy:` overlay can relabel any file) drops the cache. When `scoutctl` fails, the tree shows no locks and the exit code is logged (stderr only as private log data). An engine without `kb classify` (exit 2 or "No such command") isn't asked again until the `scoutctl` executable changes.

## [0.18.3] - 2026-10-21

## [0.18.2] - 2026-10-20

### Added
- **Choose the Claude model each scheduled slot runs on** (`Scout/Schedules/SlotEditForm.swift`, `Scout/Models/ModelChoice.swift`, `Scout/Services/ModelSettingsService.swift`, `Scout/Shell/ModelSettingsSection.swift`, `Scout/Schedules/SlotModelTag.swift`, `Scout/Services/SessionLogService.swift`). The slot editor in Schedules has a Model row: Default (the model named), Opus, Sonnet or Haiku. A full model ID set by hand in `schedule.yaml` shows as its own option and is kept on save. Settings has a Model section that sets the default for every slot without its own, written through `scoutctl model set-default` (the app never edits `scout-config.yaml`). Slots with their own model show a small tag in the table and cards; the rest look as before. A run's detail Metadata tab lists the model it ran on, read from the run log. Both pickers hide on an engine without `scoutctl model`, which would ignore the setting. Nothing changes until you pick a model: every run stays on Opus.

### Changed
- **CI also runs ScoutTests on Xcode 27, without blocking merges** (#337; `.github/workflows/app-ci.yml`). A new `xcode-27` job uses GitHub's free `xcode-27` preview image. It isn't part of the required `app-ci` check, and releases still build on Xcode 26.x. Grape 1.1.0 doesn't compile against the macOS 27 SDK yet; the fix is upstream in li3zhen1/Grape#78. Until Grape tags a release with that fix, the job patches its own throwaway copy of Grape so it can still test Scout's code. Shipped builds don't include that patch.

### Fixed
- **An archived group under an Action Items section opens from its whole header row** (#463; `Scout/ActionItems/Views/SectionView.swift`). Only the small arrow opened it before: on macOS a `DisclosureGroup` label doesn't take clicks, so the summary text did nothing. The group now keeps its open state in the section view, the way Recently Completed has since Raven-Scout/scout-app-legacy#122, and the state survives the reparse after a write. A link inside an archived group's summary now opens the group instead of following the link. Thanks to @ottomansky.
- **Saving a slot in Schedules keeps every slot's `model:`** (`Scout/Models/Slot.swift`, `Scout/Schedules/SlotDraft.swift`, `Scout/Services/ScheduleEditService.swift`). The app rewrites the whole `schedule.yaml` on save from the fields it knows. Now that the engine reads a per-slot `model:`, the app decodes it from `scoutctl schedule list --json` and writes it back, so a model set by hand isn't dropped the next time any slot is saved.
- **Scout builds on Xcode 27 except for Grape** (#337; `Scout/Services/GitService.swift`, `Scout/Services/Updates/UpdateService.swift`). The first Xcode 27 run found two more protocols that test actors couldn't conform to, `GitServiceProtocol` and `PluginUpdateChecking`. Both are now `nonisolated`, the same fix #353 made for `ProcessRunner`. `GitService.commitPaths` is pinned to `@MainActor` so it keeps the isolation it had. `KBTagTests` now imports SwiftUI for the chip text attributes, because the member-import-visibility check rejects them without that import on the macOS 27 SDK.

## [0.18.1] - 2026-10-13

### Security
- **Links in action items open only web and mail addresses** (Raven-Scout/Scout#435; `Scout/ActionItems/LinkSchemePolicy.swift`, `Scout/ActionItems/Views/InlineMarkdownText.swift`, `Scout/ActionItems/Models/TaskDeepLink.swift`, `Scout/ActionItems/TopicBoard/SideEntry.swift`). Action-item text is written from Slack, mail, GitHub and Linear content, and a click on any markdown link went straight to `NSWorkspace`, so a `[…](file:///…)` link could launch an app and another app's URL scheme would open that app. Only `http`, `https` and `mailto` links now render as links; any other scheme, including entity-encoded `javascript:`, renders as plain text. The click handler refuses anything else too. Wikilinks and `#TAG` chips work as before. A wikilink target can no longer add parameters to the `obsidian://open` URL it falls back to (`&`, `=`, `+` and `#` are now percent-encoded).

## [0.18.0] - 2026-10-13

### Added
- **Settings ▸ Engine shows an "Updates" row for an engine that updates itself** (#360; `Scout/Engine/EngineSettingsModel.swift`, `Scout/Shell/EngineSettingsSection.swift`). Engines installed with `install.sh` or Claude Code can now install each stable release on their own (`scoutctl self-update run`). When the engine's doctor reports that it does (an `update.last` note in `run_health`), the row reads "Automatic", with the last check and its result. A failed or held update shows "Needs attention" and its reason. That row replaces the "Copy /scout-update" hand-off. Engines the app manages are unchanged.

## [0.17.4] - 2026-10-13

## [0.17.3] - 2026-10-12

## [0.17.2] - 2026-10-12

## [0.17.1] - 2026-10-12

### Changed
- **Topics: tasks without a topic stand alone** (#397). Each is its own row,
  ordered among the topics by priority with what it blocks nested under it,
  instead of one collapsed "Other" group holding every untopiced task. When
  no task names a topic yet, a line above the board says so.
- **Topics: Inbox and Loose ends show only when the daily file has them**
  (#398), so a vault whose runs don't write those sections has no empty
  columns.
- **Topics: an expanded step shows its sub-bullets and context note** (#399),
  as the List card does.

### Fixed
- **Topics: Done shows the last 7 days** (#389). The window was the calendar
  week from Monday, so on a Monday it held only today's work. A done item
  with no `- Completed:` date now takes its day from the `### YYYY-MM-DD`
  subhead it sits under in Recently Completed, before falling back to the
  first daily file it showed up ticked in.
- **Topics: step rows render markdown** (#390). A row's second line showed
  `**bold**`, backticks and `[[wikilinks]]` as raw text; it now renders them
  like the List view's cards.
- **Topics: no generic "Your action" chip** (#391). A Mine step shows the chip
  only when it has an `- Action:`; the hover bubble still opens the prompt box.
- **Inline `[[wikilinks]]` in Action Items open the Knowledge Base tab**
  (#392). #380 routed only the `- Refs:` entity chips there; a wikilink in a
  task's text still opened Obsidian. It now opens the note in the KB tab,
  which falls back to Obsidian when it can't resolve the target. Linear keys
  still open Linear.

## [0.17.0] - 2026-10-12

### Added
- **Topics view mode for Action Items** (#340). A third mode next to List and
  Board groups tasks by workstream (`- Topic:`, then the topic's root session),
  nests blocked steps under the step that blocks them, orders by Linear
  priority and cycle, and parks on-hold work in one group. The state legend
  filters the board. Sub-steps (`- Parent:`) nest under their parent. A Mine
  step shows its next action as a chip; the chip or the hover bubble opens an
  inline "What should Claude do?" box that sends the note to the item's own
  session (resume a local one from its own folder, open a cloud one, or
  start a new one). Done shows this week, today or yesterday. Open rows are remembered. Tab walks
  the rows; Space expands one and Return asks Claude. A topic shows the
  user's own name from `action-items/topic-names.md` when there is one.
  Task edits stay in List.
- **Inbox, Loose ends, topic rename and close-for-good on the topic board**
  (#340). The board lists the daily file's `📥 Inbox` and `🧹 Loose ends`. A
  pencil on the topic row saves your own name through
  `scoutctl action-items rename-topic`; a × closes an entry through
  `scoutctl action-items dismiss`. Names go to `action-items/topic-names.md`,
  which runs never write. Both are hidden while the installed engine doesn't
  advertise `topic_board_v1`, and show once it does, without a relaunch. A
  failed save shows scoutctl's reason.

### Changed
- When a section or board column has more rows than it shows, a
  "Showing 20 of 53 · Show 20 more" line now sits right under its header, not
  only at the bottom of the list. When 21–40 rows are hidden, both lines also
  offer **Show all**. The 20-row page size is unchanged (Raven-Scout/Scout#356).
- An action item's entity chip (`[[people/alex]]` in `- Refs:`) opens the
  note in the Knowledge Base tab instead of Obsidian. Obsidian is now only
  the fallback when the KB can't find the note. In List view, a cross-ref
  chip (`#TAG`) scrolls to that item and outlines it, showing more rows first
  if the item is past the ones shown. If the item isn't in the list (done,
  archived or filtered out), a note says so (Raven-Scout/Scout#295).

### Fixed
- Action Items search matches a task's `[#TAG]` (typed with or without the
  `#`). Before, searching a tag found nothing, so a task beyond a section's
  first 20 rows could only be found with **Show more** or by searching its
  title (Raven-Scout/Scout#356).
- The engine update sheet no longer re-opens on every launch when Claude Code
  can't be found. It shows once; later launches skip the automatic update until
  Claude Code is found, then run it. Settings ▸ Engine still offers **Update**
  (Raven-Scout/Scout#346).
- **Claude Desktop — new Cowork task** now opens with the Scout folder
  attached, the same way the Claude Code session does. Before, the task
  started with no access to the vault (Raven-Scout/Scout#292).
- Hover-revealed controls (Comment for Scout, comment edit/delete, Ask
  Claude, the topic rename pencil, ×) and the pointing-hand cursor show only
  in the key window. In a background window the first click only brings the
  window forward, so a revealed button looked clickable but did nothing
  (Raven-Scout/Scout#294).

## [0.16.0] - 2026-10-11

### Added
- In-app updates through Sparkle: **Scout → Check for Updates…**, a daily
  background check, and a **Settings ▸ Updates** pane. An update badge shows
  when a new version is available. Debug builds never check.
- For engines Scout.app doesn't manage, the app detects when the plugin is
  behind and hands off to `/scout-update`.

### Changed
- The app now lives at `apps/macos/` in the Scout monorepo (was
  `Raven-Scout/Scout` repo root). No behavior change.
- The app and the plugin share one version and ship as one release, cut with
  `scripts/release.sh` (Raven-Scout/Scout#317). The plugin shipped 0.15.0 on
  its own, and the app was never released as 0.15.0; v0.16.0 is the first
  release of both.

### Fixed
- A failed update check (typically offline) no longer hides an update that was
  already found: the badge, menu-bar dot and footer item stay.
- **Settings ▸ Updates ▸ Check now** is disabled while an update check can't
  start (a background check or permission prompt is running), instead of
  silently doing nothing.
- An action on a task that changed after the list loaded now says so ("Task
  may have been edited externally") and the list reparses. The writer expected
  exit codes 2 and 3 for no match and ambiguous, which the engine never sent.
  It now reads the engine's 22 and 23. An engine older than the app that
  rejects a flag (exit 2, "No such option") shows as an environment problem,
  not a missing task. The writer's backfill-and-retry on a no-match is removed:
  it read the prefix at the task's old line number, which after an outside
  write can hold a different task.

## Releases before the monorepo

Up to v0.14.0 the app was released from its own repository, now
[Raven-Scout/scout-app-legacy](https://github.com/Raven-Scout/scout-app-legacy/releases). Those releases are
re-tagged `app/vX.Y.Z` here.
