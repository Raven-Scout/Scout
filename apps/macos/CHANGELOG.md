# Changelog — Scout for macOS

Versions are tagged `app/vX.Y.Z`. The plugin has its own changelog at
[`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md) and its own version line.

## [Unreleased]

### Added
- **Topics view mode for Action Items** (#340). A third mode next to List and
  Board groups tasks by workstream (`- Topic:`, then the topic's root session),
  nests blocked steps under the step that blocks them, orders by Linear
  priority and cycle, and parks on-hold work in one group. The state legend
  filters the board. Each row offers Ask Claude with the item's place on the
  board in the prompt. Done shows this week, today or yesterday. Task edits
  stay in List.
- **Inbox, Loose ends, topic rename and close-for-good on the topic board**
  (#340). The board lists the daily file's `📥 Inbox` and `🧹 Loose ends`. A
  pencil on the topic row saves your own name through
  `scoutctl action-items rename-topic`; a × closes an entry through
  `scoutctl action-items dismiss`. Both are hidden when the installed engine
  doesn't advertise `topic_board_v1`.

### Changed
- The app now lives at `apps/macos/` in the Scout monorepo (was
  `Raven-Scout/Scout` repo root). No behavior change.

## Releases before the monorepo

Versions up to the latest bare `vX.Y.Z` tag were released from the standalone
`Raven-Scout/Scout` repository, before this app moved into the monorepo. See
[releases](https://github.com/Raven-Scout/Scout/releases) and `git log` for
that history.
