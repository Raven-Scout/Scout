# Resolve with Claude: investigation prompts for health alerts

Status: draft for Jordan's approval, 2026-11-19.

## Why

When Scout raises a health alert, the owner today copies the details, opens a
Claude session, and rebuilds the context by hand: which run, which log, which
commits, which checks to re-run, and what not to touch. "Resolve with Claude"
does that in one click. It opens a new Claude Code session that already holds
the alert, the exact evidence to read, the commands to reproduce it, the
guardrails, and how to tell when it is fixed.

The motivating case is a red `run.integrity_restored` finding. The run's
integrity check had restored a protected file on a branch that, on
investigation, belonged to an interactive worktree session rather than to the
run (fixed separately in #737). A good prompt leads Claude to that kind of
conclusion: who made the change, whether it was the run, and whether the
restore was right.

## Goals

- A "Resolve with Claude" action on three kinds of alert:
  - run-health findings (the Control Center run-health banner and the Health
    panel's finding rows);
  - connector alerts (the Control Center connector banner's rows);
  - failed-run banners (the problem on a run's detail view). That means every
    run problem at `.error` or `.warning`: failures, `orphaned`,
    `rateLimited`, and the gates, sandbox, runtime and lease skips. Routine
    `.info` skips (budget, concurrency, redundant) get no button. *(Jordan,
    2026-11-19.)*
- The engine builds every prompt, so prompts are testable in pytest, versioned,
  and shared with any other client.
- Each prompt carries:
  - the alert's code and details;
  - the exact log, ledger and commit paths to read;
  - the commands that reproduce the alert;
  - the shared guardrails;
  - a "done" condition.
- Launching reuses the action-item launcher (`LaunchClaudeMenu` and
  `ClaudeLauncher`): the same targets, the same working folder, the same
  clipboard fallback.

## Non-goals

- Banners with nothing to investigate don't get the button: on battery, the
  schedule changed outside the app, a KB file changed on disk, protected-file
  notices and diagnostics.
- Claude never runs automatically. The owner clicks, reads the prefilled
  prompt, and sends it.
- No new launch targets or settings.
- The engine never writes anything for this feature. It only reads.

## Anti-goals

- A prompt that sends Claude straight to editing. Every prompt investigates
  read-only first and asks before any change.
- A prompt that copies file content, secrets or `.env` values into the session.
  Prompts carry paths, ids, commit SHAs and the engine's own one-line error
  text, which the app already shows. They never carry file bodies.
- Prompt text written in two places. Swift never composes a prompt. If the
  engine can't give one, the button doesn't show.

## Approaches considered

1. **The engine builds all three kinds (chosen by Jordan).** A new command,
   `scoutctl health resolve-prompt`, re-reads the live state and returns
   versioned JSON. There is one guardrail block and one test suite, and the
   engine already knows the log formats, ledger fields and git layout. Cost:
   one more contract fixture, and the button is hidden on engines that lack the
   command.
2. The engine builds finding prompts and Swift builds connector and run
   prompts. Faster, but the guardrails exist twice and drift, and Swift can't
   see log internals.
3. Everything in Swift. No engine change, but it can't be tested against real
   engine output, other clients can't share it, and the domain knowledge sits
   in the wrong layer.

## Design

### Engine: `scoutctl health resolve-prompt`

```
scoutctl health resolve-prompt finding <finding-id> [--json]
scoutctl health resolve-prompt connector <connector-name> [--json]
scoutctl health resolve-prompt run <log-name> [--json]
```

- The command is read-only. It runs at click time against the current vault
  (`paths.data_dir()`).
- Arguments are validated against tight patterns before use:
  - A finding id is a dotted code, optionally followed by `:` and a slot key
    or lane name: `[a-z][a-z0-9_.]{0,63}(:[A-Za-z0-9][A-Za-z0-9._-]{0,80})?`.
    `slot.stale:<key>` and `lane.dark:<lane>` are real ids. A red lane finding
    can be the banner's worst finding. *(Builder-check call.)*
  - A connector is the `connector_key` exactly as `connector-alerts.json`
    holds it, such as `mcp:<server>` or `notify:<channel>`. The app passes
    the raw key it read, so the engine needs no alias table. The pattern is
    `[a-z][a-z0-9_-]{0,31}(:[A-Za-z0-9_.-]{1,80})?`. *(Call.)*
  - A log name is a bare `*.log` basename (the run ledger's `_LOG_RE`), never a
    path.
- How each kind is resolved:
  - `finding`: run `health.evaluate(vault)` and pick the finding whose id
    matches exactly. A playbook is chosen by the id's prefix before `:`.
  - `connector`: read `.scout-logs/connector-alerts.json` (the
    `render_alerts_state` format) and pick that connector's alert.
  - `run`: read `.scout-logs/run-outcomes.jsonl` and pick the newest row
    whose log basename matches. Manual runs, older logs and rows the ledger
    has dropped have no row. For those, the prompt is built from the log
    alone, under `.scout-logs/<name>`, and the ledger lines are left out.
    *(Call.)*
- What `current` means depends on the kind:
  - `finding`: health still reports that id.
  - `connector`: the alert is still in `connector-alerts.json`.
    Acknowledgements live only in the app, so the engine doesn't see them.
  - `run`: no later run of the same slot has ended `ok`. A failed run is
    history and never clears.

  When the subject is not current, the command still returns a prompt, with
  `"current": false`. For a finding or connector alert, that prompt asks
  Claude to confirm it cleared and why. For a run, it says a later run
  succeeded, so the question becomes why this one failed. *(Call.)*
- Exit codes: 0 when a prompt was built; 2 when the arguments are bad or the
  vault is missing, with `{"schema_version": 1, "error": "..."}` under
  `--json`.
- Without `--json` it prints only the prompt text, which is handy from a
  terminal: `scoutctl health resolve-prompt finding run.integrity_restored | pbcopy`.

JSON (`schema_version: 1`, every key always present):

```json
{
  "schema_version": 1,
  "kind": "finding",
  "key": "run.integrity_restored",
  "current": true,
  "title": "Investigate: a scheduled run changed a protected file",
  "cwd": "/Users/alex/Scout",
  "prompt": "…markdown…"
}
```

`cwd` is always the vault, for clients that have no vault folder of their
own. The app launches in the vault folder its host already passes
(`scoutDirectory`, as action items do). It refuses with an inline error if the
engine's `cwd`, once symlinks are resolved, names a different folder. So a
session never opens in a folder the app didn't expect. *(Call.)*

The new module is `scout/resolve_prompt.py`. It contains:

- one function per kind that gathers the evidence;
- a per-code playbook table;
- a generic playbook for any code without its own;
- the shared guardrails and "done" blocks;
- `to_dict()`.

The manifest gains the feature `resolve_prompt_v1: True`.

### Prompt shape

Every prompt is markdown with the same sections in the same order:

1. **What Scout saw.** The alert's title and summary, code, severity, since
   when, the engine's own error line (`detail`), and whether it is still
   current.
2. **Evidence to read.** Absolute paths and ids:
   - the run log;
   - the ledger file (`.scout-logs/run-outcomes.jsonl`) and how to find the
     row (slot plus `started_at`);
   - the commit SHAs the integrity lines name;
   - `connector-alerts.json` and the connector-health log;
   - the schedule file;
   - the engine checkout (`engine_checkout`), for when the cause may be
     Scout's own code.
3. **How to investigate.** Numbered, read-only steps from the code's playbook.
4. **Reproduce and verify.** The exact commands, for example
   `scoutctl health --json`, `scoutctl runs --json --since 7d`,
   `scoutctl bootstrap doctor`, and the per-code `git` and `log` commands.
5. **Guardrails.** One shared block, word for word in every prompt:
   - Investigate read-only first. Report what you found before you change
     anything.
   - Never edit protected files without asking: runner scripts,
     `.claude/` and agent settings, `.mcp.json`, `.scout-state/schedule.yaml`,
     `triggers.yaml`, the delegate state.
   - Commit in the vault only through `scripts/git-safe-commit.sh`. Never
     rewrite history, force-push, or delete a branch, tag or stash entry
     without asking.
   - Never read secrets, `.env*` files, tokens or keychain items. Never paste
     file content from the vault into an issue or PR.
   - If the cause is in Scout itself, read the engine checkout but don't edit
     it. Draft an issue (with no vault content) for the owner to file.
6. **Done when.** A per-code condition. The default is: you have explained the
   cause, the owner has approved and applied any fix, and
   `scoutctl health --json` no longer lists `<id>`. Findings that age out by
   design say so. `run.integrity_restored` stays for 7 days, so its done
   condition is that the cause is known and the restore is confirmed right or
   wrong, not that the finding has cleared.

### Playbooks in v1

Codes with their own playbook:

- **`run.integrity_restored` / `run.integrity_unchecked`:**
  1. Read the log's `integrity check:` lines: which ref, which paths, which
     restoring commit.
  2. For each ref, check `git worktree list --porcelain` to see whether the
     ref is checked out in a vault worktree.
  3. Run `git log --format='%H %an %ad %s'` on the changing commit and compare
     its time with the run's start and finish.
  4. Use `git show --stat` on the changing and restoring commits.
  5. Decide which session made the change, whether it was the run, and whether
     the restore was right. If the restore was wrong, say how to undo it, and
     ask first.
- **`runs.failing`** (and failed-run prompts generally): read the failure
  line, the log tail, and the `failure_class` meaning. Check whether the
  failure repeats across slots. Distinguish the transient classes (the app's
  `softFailureClasses`) from real ones.
- **`auth.expired`, `auth.token_expiring`, `auth.token_unreadable`:** read
  `scoutctl auth status`. Never print a token. The fix is owner-run sign-in,
  so the prompt says so.
- **`sandbox.failed`, `runs.unsandboxed`, `runs.sandbox_env_off`:** read
  `scoutctl sandbox` status and `scoutctl protection status --json`, plus the
  log's sandbox lines.
- **`gates.not_loaded`, `gates.probe_unsafe`:** read `scoutctl gates` output
  and the hook registration.
- **`scheduler.stalled`, `lock.skips`, `lease.*`, `slot.*`, `lane.*`:** read
  `last-fire.json`, the tick job state, the lease holder, and the named slot's
  or lane's schedule entry and newest runs.
- **`update.failed`, `update.held`, `update.sidecars`:** read the update
  outcome, run `bootstrap drift`, and check for `*.proposed-merge` sidecars.

Every other finding id gets the generic playbook: its summary, fix, log, the
standard reproduce commands, and "work out why".

Connector alerts get one playbook, filled in with the connector's name:

- the alert's reason and level;
- the connector-health log;
- the roster's probe for it (`scoutctl connectors probe-registry`).

Failed runs get the run playbook, keyed by the row's outcome and then its
`failure_class`. It has its own cases for these outcomes:
- `rate_limited`: when the limit lifts (`resets_at`), and whether the runs are
  over budget or just colliding;
- `orphaned`: how far the log got;
- the safety skips: the gate, sandbox, runtime or lease state that stopped the
  run.

### App

- **The subject.** `Problem` gains an optional subject that names what can be
  resolved:
  `ResolveSubject: Equatable, Hashable, Sendable { case finding(String), connector(String), run(logName: String) }`.
  `init(finding:)` and `init(connectorAlert:)` always set it. `init?(run:)`
  sets it only when the problem's severity is `.error` or `.warning`. No other
  factory sets it, so no other banner can show the button. It is a plain value,
  so `Problem` stays `Equatable` and `Sendable`.
- **Fetching the prompt.** `ResolvePrompt` parses and validates the JSON:
  schema version 1, and `kind` and `key` must match what was asked.
  `ResolvePromptService` runs
  `scoutctl health resolve-prompt <kind> <key> --json` through the app's
  `ProcessRunner`, with the same environment and `scoutctl` lookup the run
  ledger uses. The key is re-checked against the same patterns before it is
  passed to the command.
- **The button.** `ProblemView` shows a `LaunchClaudeMenu` labelled
  "Resolve with Claude" next to the problem's other actions, when all of these
  hold:
  - the problem has a subject;
  - the engine's manifest has `resolve_prompt_v1` (`EngineFeatures`);
  - the host passed the vault folder.

  The main button opens Claude Desktop with a new Claude Code session in the
  vault. The chevron offers the terminal, chat and Cowork targets, as on
  action items.
- **Async prompts in the launcher.** `LaunchClaudeMenu` takes its prompt
  synchronously today. It gains an async, throwing prompt provider. Action
  items keep the sync path. While the engine is building the prompt, the
  button shows the existing `isLaunching` state. A failure goes to the host's
  inline error text, for example "Couldn't build the prompt: …", and nothing
  launches.
- **Call sites:**
  - `RunHealthBanner`;
  - `HealthView` `FindingRow`;
  - the connector rows in `ConnectorAlertBanner`;
  - the problem in `RunDetailView`.

  None of them change. `ProblemView` reads the prompt service from a SwiftUI
  environment value, which `ScoutApp` sets from `AppState`. The vault folder
  comes from the service. `ProblemView` keeps its own launch-error text.
  Previews, and any view without the environment value, show no button.
  *(Call.)*

### Contract and tests

- **Contract fixture.** The new fixture is
  `plugin/engine/tests/fixtures/contract/resolve-prompt.json`. It holds one
  document per kind, built from a synthetic vault in
  `tests/unit/test_resolve_prompt_contract.py` and regenerated with
  `SCOUT_REGEN_CONTRACT=1`. Its byte copy goes to
  `apps/macos/ScoutTests/Fixtures/resolve-prompt.json`, with pins in a new
  `ResolvePromptContractTests`. A `contract.yml` byte-compare step is added.
  The root and plugin `CLAUDE.md` files get the matching line. Everything
  lands in one commit.
- **Engine tests:**
  - every kind returns every key;
  - bad arguments exit 2;
  - a subject that is no longer current gives `current: false`;
  - the guardrail block appears word for word in every prompt;
  - prompts contain no file content: a canary string planted in the log and
    the settings file never appears in the prompt;
  - the playbook steps for integrity, auth and a generic code;
  - the manifest has the feature.
- **App tests:**
  - parsing and refusals in `ResolvePromptContractTests` and
    `ResolvePromptTests`;
  - which factories set a subject and which don't (`ProblemTests`);
  - the service builds the right arguments and rejects bad keys;
  - the guard suites: `BannerStyleGuardTests` and the source-scan guards.

### Changelog

- `plugin/changelog.d/<slug>.added.md`: the new command and the feature flag.
- `apps/macos/changelog.d/<slug>.added.md`: the button.

## Left to the builder

- Exact playbook wording, inside the shape above.
- How `ProblemView` lays out the button next to its existing actions, within
  the design system's banner rules.
