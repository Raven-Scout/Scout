# Changelog — Scout for macOS

Scout's app and plugin share one version and one release (`vX.Y.Z`), cut with `scripts/release.sh`. Each
release's notes combine this file's section with [`plugin/CHANGELOG.md`](../../plugin/CHANGELOG.md).
Keep an `## [Unreleased]` section at the top. `scripts/release.sh prepare` promotes it.

## [Unreleased]

### Changed
- The app now lives at `apps/macos/` in the Scout monorepo (was
  `Raven-Scout/Scout` repo root). No behavior change.

## Releases before the monorepo

Up to v0.14.0 the app was released from its own repository, now
[Raven-Scout/scout-app-legacy](https://github.com/Raven-Scout/scout-app-legacy/releases). Those releases are
re-tagged `app/vX.Y.Z` here.
