# Changelog — Scout for macOS

Versions are tagged `app/vX.Y.Z`. The plugin has its own changelog at
[`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md) and its own version line.

## [Unreleased]

### Added
- **Topics view mode for Action Items** (#340). A third mode next to List and
  Board groups tasks by workstream (`- Topic:`, then the topic's root session),
  nests blocked steps under the step that blocks them, orders by Linear
  priority and cycle, and parks on-hold work in one group. The state legend
  filters the board. Sub-steps (`- Parent:`) nest under their parent. A Mine
  step shows its next action as a chip; the chip or the hover bubble opens an
  inline "What should Claude do?" box that sends the note to the item's own
  session (resume a local one, open a cloud one, or start a new one). Done
  shows this week, today or yesterday. Open rows are remembered. Read-only:
  writes stay in List.

### Changed
- The app now lives at `apps/macos/` in the Scout monorepo (was
  `Raven-Scout/Scout` repo root). No behavior change.

## Releases before the monorepo

Versions up to the latest bare `vX.Y.Z` tag were released from the standalone
`Raven-Scout/Scout` repository, before this app moved into the monorepo. See
[releases](https://github.com/Raven-Scout/Scout/releases) and `git log` for
that history.
