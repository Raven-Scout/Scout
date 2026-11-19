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
  canonical version — lives at `plugin/.claude-plugin/plugin.json`. Its scout
  entry pins a `git-subdir` source to the **`stable` branch** (#362), so installs
  get the latest release, never `main`'s tip. Only `release.sh finalize` moves
  `stable`. Never point the entry back at `"./plugin"`, and never push `stable`
  by hand except to run the recovery command `finalize` prints.
- **`plugin/.claude-plugin/marketplace.json` makes `plugin/` its own marketplace
  root**: the root manifest with only the scout entry, sourced `"./"`. Hosts
  that serve a checkout and can't follow `git-subdir` (Agnes) read it from a
  `vX.Y.Z` tag, never `main` (see `plugin/README.md`), and Scout.app's engine
  bundle ships it (`bundle-engine.sh` fails if it differs from what it would
  generate). Its version is a release version file like the others; when the
  root entry changes, change this one to match, keeping `"./"`.
- **Everything the plugin needs at runtime must stay under `plugin/`.**
  `CLAUDE_PLUGIN_ROOT` resolves to the materialized `plugin/` subtree only; a
  plugin file that reaches outside it is broken for every installed user, and
  nothing in this repo will tell you.
- **One version, one tag, one release.** Scout's app and plugin share one version `X.Y.Z`. It lives in
  `plugin/.claude-plugin/plugin.json`, the root `marketplace.json`, `plugin/.claude-plugin/marketplace.json`, `plugin/engine/pyproject.toml`,
  `plugin/engine/scout/__init__.py`, and every `MARKETING_VERSION` in `apps/macos/Scout.xcodeproj`;
  `versioning check` (CI: `contract`) fails on drift. From v0.16.0 every release is ONE tag `vX.Y.Z`, cut
  with `scripts/release.sh prepare` then `finalize`; release candidates are `vX.Y.Z-rc.N`, cut with `rc` from
  `release/vX.Y.Z`. Never create `app/v*` or `plugin/v*` tags (sole exception before v0.16.0: an urgent
  `plugin/v0.15.x` patch via `plugin/scripts/release-plugin.sh`); the existing ones (`app/v0.1.0`–`app/v0.14.0`,
  `plugin/v0.14.0`, `plugin/v0.15.0`) are history. The app was never released as 0.15.0. Bare `v0.4.0`–`v0.13.0` are the plugin's pre-monorepo releases. Agents never
  run `finalize`/`rc` for real.
- **A changelog entry is a fragment file, never an edit to a CHANGELOG.** Add
  `plugin/changelog.d/<branch-slug>.<section>.md` (plugin and engine) and/or
  `apps/macos/changelog.d/<branch-slug>.<section>.md` (app): `<branch-slug>` is
  your branch name after its last `/`, `<section>` one of `added`, `changed`,
  `fixed`, `removed`, `security`, `deprecated`, `performance`, `documentation`,
  `upgrade-notes`, and the content the entry's `- ` bullets, no headings
  (format: `plugin/changelog.d/README.md`). `scripts/release.sh prepare`
  assembles them into `## [X.Y.Z]` and deletes them. If the slug is generic or
  reused, add a short random suffix, and never edit a fragment main already
  has. Never add to `## [Unreleased]` (it is kept only for PRs opened before
  fragments, and goes once they merge) and never touch a `## [X.Y.Z]` section
  main already has: `contract` runs `python -m scout.scripts.changelog check`
  and fails on a bad fragment and, on PRs and in the merge queue (not on the
  push to main), on a changed existing fragment or a changed versioned
  section. Two exceptions: a PR into `release/vX.Y.Z` edits
  `[X.Y.Z]` directly and adds no fragment (one there would never reach X.Y.Z),
  and a deliberate fix to a published entry ends a commit message with the
  trailer `Changelog-Edit-Released: X.Y.Z <why>`.
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
- **The doctor run-health fixture has one canonical copy too:**
  `plugin/engine/tests/fixtures/contract/doctor-run-health.json`, generated —
  never hand-edited — from the vault builder in
  `plugin/engine/tests/unit/test_doctor_run_health_contract.py`: from
  `plugin/engine` run
  `env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_doctor_run_health_contract.py`,
  then from the repo root `cp` it to
  `apps/macos/ScoutTests/Fixtures/doctor-run-health.json` and update the pins in
  `apps/macos/ScoutTests/Engine/DoctorReportTests.swift` — all in one commit.
  `contract.yml` byte-compares the two copies.
- **The connector alert state has one canonical copy too:**
  `plugin/engine/tests/fixtures/contract/connector-alerts.json`, the format of
  `.scout-logs/connector-alerts.json` (documented on `render_alerts_state` in
  `connector_health_report.py`), which the app's `ConnectorAlert.parseFile`
  reads. Generate it the same way, from `plugin/engine`:
  `env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_connector_alerts_contract.py`,
  then `cp` it to `apps/macos/ScoutTests/Fixtures/connector-alerts.json` and
  update the pins in `apps/macos/ScoutTests/Models/ConnectorAlertContractTests.swift`
  — all in one commit. `connector-alerts.log` is for people; neither side parses it.
- **The usage summary has one canonical copy too:**
  `plugin/engine/tests/fixtures/contract/usage-summary.json`, the output of
  `scoutctl budget usage --json` (`scout/scripts/usage_summary.py`), which the
  app's `ScoutUsageService` decodes for the usage card and status bar. Generate
  it from `plugin/engine`:
  `env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_usage_summary.py`,
  then `cp` it to `apps/macos/ScoutTests/Fixtures/usage-summary.json` and
  update the pins in `apps/macos/ScoutTests/Services/ScoutUsageServiceTests.swift`
  — all in one commit.
- **The run protection summary has one canonical copy too:**
  `plugin/engine/tests/fixtures/contract/protection-status.json`, the output of
  `scoutctl protection status --json`, which the app's Run protection card reads.
  Generate it from `plugin/engine`:
  `env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_run_protection_contract.py`,
  then `cp` it to `apps/macos/ScoutTests/Fixtures/protection-status.json` and update
  the pins in `apps/macos/ScoutTests/Engine/RunProtectionTests.swift` — all in one commit.
- **The run ledger has one canonical copy too:**
  `plugin/engine/tests/fixtures/contract/run-ledger.json`, the output of
  `scoutctl runs --json` (`scout/run_ledger.py`; recent `run-outcomes.jsonl`
  rows plus the run-health report, versioned by `schema_version`), which the
  app's Health panel reads (`RunLedger.parse`). Generate it the same way, from
  `plugin/engine`:
  `env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_run_ledger_contract.py`,
  then from the repo root `cp` it to
  `apps/macos/ScoutTests/Fixtures/run-ledger.json` and update the pins in
  `apps/macos/ScoutTests/Health/RunLedgerContractTests.swift` — all in one
  commit. A new ledger field or outcome goes into the test's vault builder, not
  the JSON.
- **The delegate status has one canonical copy too:**
  `plugin/engine/tests/fixtures/contract/delegate-status.json`, the output of
  `scoutctl delegate status --json` (`scout/delegate_status.py`, #550), which
  the app's Settings ▸ Agent copies card reads (`DelegateStatus.parse`).
  Generate it from `plugin/engine`:
  `env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_delegate_status_contract.py`,
  then from the repo root `cp` it to
  `apps/macos/ScoutTests/Fixtures/delegate-status.json` and update the pins in
  `apps/macos/ScoutTests/Engine/DelegateStatusTests.swift` — all in one commit.
  It never carries a key, token, vault path or note name.
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
