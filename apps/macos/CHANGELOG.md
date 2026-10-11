# Changelog — Scout for macOS

Versions are tagged `app/vX.Y.Z`. The plugin has its own changelog at
[`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md) and its own version line.

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

## Releases before the monorepo

Versions up to the latest bare `vX.Y.Z` tag were released from the standalone
`Raven-Scout/Scout` repository, before this app moved into the monorepo. See
[releases](https://github.com/Raven-Scout/Scout/releases) and `git log` for
that history.
