---
status: accepted
---

# Installs follow a `stable` branch, never `main`

The marketplace entry pins a `git-subdir` source to the `stable` branch, which only `release.sh finalize` moves, fast-forwarding it to the release commit once the release is published. With the earlier `"./plugin"` source, Claude Code served `main`'s tip under the last release's version number, so unreleased fixes shipped to users labelled as v0.16.0. A tag ref would also pin a release but would need the entry rewritten on every release; we found no record of it being weighed. Decided as an amendment to [the unified-release spec](../superpowers/specs/2026-10-05-unified-release-design.md) in [#367](https://github.com/Raven-Scout/Scout/pull/367) (2026-10-11). Hosts that cannot follow `git-subdir` read `plugin/.claude-plugin/marketplace.json` from a `vX.Y.Z` tag instead ([#494](https://github.com/Raven-Scout/Scout/pull/494)).
