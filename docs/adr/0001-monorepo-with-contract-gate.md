---
status: accepted
---

# The plugin and the app live in one repository, gated by `contract.yml`

The app and the plugin share contract artifacts (the connector roster, the schedule snapshot, the parser corpus), and with two repositories every change to one of them needed a manual ritual or a best-effort sync, which drifted: the shipped connector roster was wrong for four months. We moved both into one repository (the former `scout-plugin`, renamed `Raven-Scout/Scout`), so a change touching both lands as one commit and `contract.yml` fails the build when a client copy differs from its canonical file. The plugin stays in `plugin/` rather than at the root, because a plugin at the root would make Claude Code copy the entire repository, app included, into every user's plugin cache. Decided in [the monorepo spec](../superpowers/specs/2026-09-03-monorepo-consolidation-design.md), landed in [#278](https://github.com/Raven-Scout/Scout/pull/278) (2026-10-05).

## Consequences

The spec's Stage 2, which would delete the client byte copies and make drift unrepresentable, has not landed: the gate detects drift, it does not prevent it.
