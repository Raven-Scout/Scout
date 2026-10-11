# Changelog — Scout for macOS

Scout's app and plugin share one version and one release (`vX.Y.Z`), cut with `scripts/release.sh`. Each
release's notes combine this file's section with [`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md).
Keep an `## [Unreleased]` section at the top. `scripts/release.sh prepare` promotes it.

## [Unreleased]

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

## Releases before the monorepo

Up to v0.14.0 the app was released from its own repository, now
[Raven-Scout/scout-app-legacy](https://github.com/Raven-Scout/scout-app-legacy/releases). Those releases are
re-tagged `app/vX.Y.Z` here.
