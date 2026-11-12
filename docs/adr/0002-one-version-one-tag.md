---
status: accepted
---

# The app and the plugin share one version and one release tag

From v0.16.0 every release is one version `X.Y.Z` and one tag `vX.Y.Z`, cut with `scripts/release.sh prepare` and `finalize`. This reverses the monorepo spec's own plan of prefixed `plugin/v*` and `app/v*` tags on separate cadences. The reason is the install story: installing the app should be the only step for most users, so cutting a version is one release action that produces the app with the matching plugin inside, and the two are matched by construction rather than by a compatibility table. Signing and notarizing in CI was deferred because it needs the Developer ID certificate and a notary key in Actions secrets. Decided in [the unified-release spec](../superpowers/specs/2026-10-05-unified-release-design.md), landed in [#317](https://github.com/Raven-Scout/Scout/pull/317) and released as v0.16.0 ([#355](https://github.com/Raven-Scout/Scout/pull/355)), both 2026-10-11.

## Consequences

An engine-only fix ships as a full release; its app section may simply say "No app changes". `plugin/scripts/release-plugin.sh` and its workflow still exist for the one allowed `plugin/v0.15.x` patch, and nothing stops their use after v0.16.0.
