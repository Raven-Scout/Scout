# Changelog — Scout for macOS

Scout's app and plugin share one version and one release (`vX.Y.Z`), cut with `scripts/release.sh`. Each
release's notes combine this file's section with [`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md).
Keep an `## [Unreleased]` section at the top. `scripts/release.sh prepare` promotes it.

## [Unreleased]

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

### Fixed
- Action Items search matches a task's `[#TAG]` (typed with or without the
  `#`). Before, searching a tag found nothing, so a task beyond a section's
  first 20 rows could only be found with **Show more** or by searching its
  title (Raven-Scout/Scout#356).

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
