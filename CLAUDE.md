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
  `vX.Y.Z` — the two artifacts collided on `v0.5.0`–`v0.13.0` before the merge.
  The bare `v*` tags in this repo are the plugin's pre-monorepo releases; the
  app's were re-tagged `app/vX.Y.Z`.
- **Issue and PR numbers come from two repos.** This repo is the former
  `Raven-Scout/scout-plugin`, renamed `Raven-Scout/Scout` when the app moved in,
  so its own `#N` numbering is scout-plugin's. The old app repo is
  `Raven-Scout/scout-app-legacy` (archived). A bare `#N` in `apps/macos/` files
  or in a pre-move app commit subject, and any `Raven-Scout/Scout#N` written
  before the move, means `Raven-Scout/scout-app-legacy#N`. Link old app items
  there; never rewrite them to a bare `#N` here.
- **Contract artifacts are generated, never hand-edited.** The connector roster
  and schedule snapshot each have ONE canonical file under
  `plugin/engine/scout/`, generated from its `.yaml`, and every client copy
  under `apps/*/` is a byte copy of it. To change one, from `plugin/engine`:
  edit `scout/connectors.yaml` (or `scout/defaults/schedule.yaml`), regenerate
  the canonical file with
  `.venv/bin/python -m scout.scripts.connectors_snapshot --no-also-write-app-fixture`
  (or `schedule_snapshot`), then from the repo root `cp` it over every client
  copy `contract.yml` checks. **Always pass
  `--no-also-write-app-fixture`** — the default also writes into a sibling
  `~/scout-app` checkout outside this repo, and never into `apps/`.
  `contract.yml` fails the build if a copy drifts. See
  `docs/superpowers/specs/2026-09-03-monorepo-consolidation-design.md` §7.
- **The parser corpus has one canonical copy too:**
  `plugin/engine/tests/fixtures/contract/parser-corpus.json`. Edit it, set
  `EXPECTED_SHA256` (`plugin/engine/tests/unit/test_parser_corpus_checksum.py`)
  and `canonicalSHA256`
  (`apps/macos/ScoutTests/ActionItems/ParserContractTests.swift`) to its new
  `shasum -a 256`, then `cp` it to
  `apps/macos/ScoutTests/Fixtures/parser-corpus.json` — all in one commit.
  (scout-iOS-app vendors a copy in its own repo; see `apps/macos/CLAUDE.md`.)
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
