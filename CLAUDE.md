# CLAUDE.md

Working notes for AI agents in the Scout monorepo.

## This repo holds two shipping artifacts

| Path | Artifact | Read next |
|---|---|---|
| `plugin/` | The Scout Claude Code plugin + Python engine (`scoutctl`) | `plugin/CLAUDE.md` |
| `apps/macos/` | The Scout macOS menu-bar app (SwiftUI) | `apps/macos/CLAUDE.md` |

**Work in the subtree that owns the change, and read that subtree's `CLAUDE.md`
first.** The two have different languages, test runners, and release flows.

## Rules that span both subtrees

- **`.claude-plugin/marketplace.json` lives at the REPO ROOT**, not in `plugin/`.
  `claude plugin marketplace add` reads it from there. `plugin.json` — the
  canonical version — lives at `plugin/.claude-plugin/plugin.json`.
- **Everything the plugin needs at runtime must stay under `plugin/`.**
  `CLAUDE_PLUGIN_ROOT` resolves to the materialized `plugin/` subtree only; a
  plugin file that reaches outside it is broken for every installed user, and
  nothing in this repo will tell you.
- **Tags are prefixed:** `plugin/vX.Y.Z`, `app/vX.Y.Z`. Never push a bare
  `vX.Y.Z` — the two artifacts collided on `v0.5.0`–`v0.9.0` before the merge.
- **Contract artifacts are generated, never hand-edited.** The connector roster
  and schedule snapshot have one canonical source under
  `plugin/engine/scout/` and are copied into `apps/*/`. Edit the `.yaml`, then
  regenerate — `contract.yml` fails the build if a copy drifts. See
  `docs/superpowers/specs/2026-09-03-monorepo-consolidation-design.md` §7.
- **Never edit a `*.snapshot.json` by hand.** That is what broke the shipped
  connector roster for four months.
- **Plugin history was absorbed with `git subtree`, SHAs preserved.** `git blame
  plugin/<file>` crosses the merge to the original commits, but `git log --
  plugin/<file>` stops at the merge commit — for a plugin file's earlier
  history run `git log <subtree-merge>^2 -- <path without the plugin/ prefix>`
  (the first subtree merge is the commit titled "absorb scout-plugin into
  plugin/").

## Cross-cutting changes

A change touching both subtrees (a new `scoutctl` subcommand the app calls, a
snapshot schema change) lands as ONE commit, and `contract.yml` is the gate.
That is the entire reason these repos were merged — do not split such a change
across two PRs out of habit.
