# Changelog — Scout for macOS

Scout's app and plugin share one version and one release (`vX.Y.Z`), cut with `scripts/release.sh`. Each
release's notes combine this file's section with [`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md).
Keep an `## [Unreleased]` section at the top. `scripts/release.sh prepare` promotes it.

## [Unreleased]

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
