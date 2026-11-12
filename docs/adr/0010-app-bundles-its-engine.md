---
status: accepted
---

# The app bundles and manages its own engine install

Scout.app ships an engine bundle and installs it into a versioned directory under `~/.local/share/scout`, with its own virtualenv outside the plugin tree and a pointer file (`~/.local/state/scout/engine.json`) that tells every caller which engine install is live. Cloning the engine from git at a pinned ref was deferred as a possible future "latest" channel, and a setup guide without a managed engine was rejected. The accepted cost is coupling: an engine change reaches app users only through an app release. Decided in [the app-managed engine spec](../superpowers/specs/2026-09-08-app-managed-engine-design.md) §3, landed in [#312](https://github.com/Raven-Scout/Scout/pull/312) and [#319](https://github.com/Raven-Scout/Scout/pull/319) (2026-10-05 to 2026-10-11).

## Consequences

Only the app upgrades an engine install it manages: the self-update refuses it ([#421](https://github.com/Raven-Scout/Scout/pull/421)) and `scoutctl setup` leaves it alone ([#328](https://github.com/Raven-Scout/Scout/pull/328)).
