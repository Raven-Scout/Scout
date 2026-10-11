# Changelog — Scout for macOS

Versions are tagged `app/vX.Y.Z`. The plugin has its own changelog at
[`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md) and its own version line.

## [Unreleased]

### Changed
- The app now lives at `apps/macos/` in the Scout monorepo (was
  `Raven-Scout/Scout` repo root). No behavior change.

### Fixed
- An action on a task that changed after the list loaded now says so ("Task
  may have been edited externally") and the list reparses. The writer expected
  exit codes 2 and 3 for no match and ambiguous, which the engine never sent.
  It now reads the engine's 22 and 23. An engine older than the app that
  rejects a flag (exit 2, "No such option") shows as an environment problem,
  not a missing task. The writer's backfill-and-retry on a no-match is removed:
  it read the prefix at the task's old line number, which after an outside
  write can hold a different task.

## Releases before the monorepo

Versions up to the latest bare `vX.Y.Z` tag were released from the standalone
`Raven-Scout/Scout` repository, before this app moved into the monorepo. See
[releases](https://github.com/Raven-Scout/Scout/releases) and `git log` for
that history.
