# Agent-behaviour eval harness — design

**Status:** draft for review. Docs only; no code lands from this PR. §13 lists the questions that
need a product decision before the implementation plan.
**Date:** 2026-10-12, written against `origin/main` @ `da6e3d1` (v0.16.0 + #371).
**Issue:** #196. Instruction changes it must be able to judge: #371, #372, #325, and run-health P3
(#366 / #370 / #373 stack).

## 1. Intent

The engine's Python is well tested. What a run actually *does* is not. `plugin/phases/` holds about
32k words of session instructions across 30 files, and it ships with no behavioural test. An edit
that makes runs worse only shows up when someone notices weeks later. #196 records three edits that
read as improvements and measurably weren't (7 → 11 tool calls, zero effect plus a misfire, 21 vs
13 calls). Review would have passed all three.

This spec defines a harness that runs a real Scout session against a synthetic vault with scripted
connectors, then scores what the session did.

**Success means:**

1. A phase edit can be scored before it merges: `plugin/scripts/eval.sh compare --base origin/main`
   runs the same scenarios against both checkouts and reports per-check pass rates and process
   metrics side by side.
2. Each recent rule change has a check that fails when the rule is broken: #371 placement, #372
   demotion, #325 context notes, the dropoff and count guards, the outage rule and the run-health
   wrap.
3. A check that is merely noisy is reported as **flaky**, not as a regression. A regression is
   reported only when the evidence clears a stated bar (§8).
4. No real person, project, organisation or text from any real vault appears anywhere in the
   fixture. This is enforced by a test, not by care.
5. Spend is bounded per run, per suite and per month, and CI never spends without someone asking.

**Non-goals:** testing the Python engine (pytest already does), testing Scout.app, and evaluating
the interactive commands (`/scout-work`, `/scout-plan`) or #196's knowledge-question tasks in the
first slices. §11 places those later.

## 2. What exists today (verified on `da6e3d1`)

| Piece | Where | Relevance |
|---|---|---|
| Brain-file assembly | `engine/scout/scripts/bootstrap.py` `_assemble(cfg, kind, *, custom=None)` :441-512; `phase_assembly.py` `select_sections` :109-138 | Effectively pure. It reads `phases/` plus an optional `connectors.custom.yaml` and returns text. A section is kept when `requires` is null or enabled, and `mode` is empty or intersects the kind's target modes. `slot:` does not order anything; files go in sorted order, `core/` then `connectors/` (SKILL). Impurities: `TODAY_DATE` reads the clock, and `_template_vars` resolves `scoutctl` / `claude` on PATH. |
| Fresh vault | `scoutctl bootstrap install --no-jobs --skip-claude …` (`cli.py:1724`), `bootstrap.install()` :1076 | Builds the full directory layout, seeds, managed scripts, the three runners, `scout-config.yaml` and the assembled brain files, with no launchd jobs. |
| Runner | `templates/run-scout.sh.tmpl` | Mode from `SCOUT_FORCE_MODE`. Pre-hooks: kb-pre-filter, pre-session-data, cc-session-cache, materialize-daily-file, budget-check. The claude call is `claude-with-retry.sh $LOG $CLAUDE_BIN --permission-mode auto --model opus --max-budget-usd N --name … -p "$PROMPT"` (:239-247). Post-hooks: backfill, run-outcome, connector-health-report, cost. **`CLAUDE_BIN` is a template input**, so the harness can swap the binary without touching the runner. |
| Connectors | `connectors.yaml` (roster), `templates/connector-probes.yaml` (probes) | Slack, Linear, Calendar, Gmail, Drive and Granola are MCP servers named `claude_ai_*` / `plugin_*` in the **user's own** Claude config. GitHub is the **`gh` CLI**: the runner prompt says "Use 'gh' CLI for all GitHub operations" (:194) and `phases/connectors/github.md` calls `gh pr list`, `gh search prs` and `gh api`. |
| Outage rule | `phases/core/00-run-modes.md:34-43` | An unreachable source is reported in the wrap and the run notes, never skipped silently. The agent must attempt a call before declaring it down ("the claim must match the log"). **No phase skips a connector because of its health status.** Health is computed after the run from logs (`connector_health_report.py`), and only assembly drops a connector, when it is not enabled. |
| Run-health wrap | `phases/core/01-run-health.md` on `run-health/p1` (PR #366, open, conflicting) | Not on main. The session runs `scoutctl health --json` at start and leads the wrap with red findings as `Before this run: <summary> — <fix>`, plus one `⚠️ N run-health warnings` line. It never repeats `auth.expired`, because the session running proves the Claude login works. |
| Action-item rules | `phases/core/action-items.md` | 🔴/🟡 only for the user's move (:37-44, #371); 🟢 for waiting on others (:46-49); deadline ceilings (:55-62); count guard (:272-286); continuity-dropoff audit (:288-295); context notes at `action-items/context/<TAG>.md` (:491, #325). |
| Deterministic readers | `scout.action_items.parser.parse_file`, `scoutctl action-items list --json`, `scoutctl kb lint` (incl. `missing-context-note`, `--lossless-rev`) | Usable directly as graders. `list --json` omits body and fields, so graders call the parser in-process. |
| Telemetry | Stop hook `session_tool_log` → `.scout-logs/connector-calls-*.jsonl` | Logs only `{tool, connector, error}`. The harness uses the stream-json transcript instead. |
| Fixtures | `engine/tests/fixtures/kb-sample/` (2 files), `conftest.py` `fake_data_dir` / `kb_repo`, `integration/test_run_scout_mode_export.py` (renders the runner with a stub claude) | Seeds, not a vault. The runner-with-stub-claude pattern is the precedent this harness extends. |
| `claude plugin eval` (CLI 2.1.278) | built in | Considered and rejected for the core runner (§4). |

There is no temperature or seed flag on `claude -p`. Every run is a sample, so every claim is a
rate over N repetitions.

## 3. Shape of the system

```
plugin/engine/tests/eval/
  harness/            Python package (dev-only; never imported by scout/)
    build.py          fixture vault builder
    stubs/mcp.py      one stdio MCP server, many personas (Slack, Linear, Calendar, Gmail)
    stubs/gh.py       fake `gh` on PATH
    claude_shim.py    the CLAUDE_BIN the runner calls; adds the eval flags
    metrics.py        stream-json → tool calls, turns, tokens, cost, spirals
    checks/           graders: placement, dropoff, context_notes, wrap, outage, process
    compare.py        baseline vs candidate
  vault/              committed, hand-written content overlay (anonymized)
  scenarios/<name>/   scenario.yaml + vault overlay + connector script
  allowlist.txt       every proper noun the fixture may contain
plugin/scripts/eval.sh   entry point (run | compare | build | lint)
.eval-results/        gitignored; one dir per run
```

One **scenario** is one run configuration: vault state + connector script + mode. One scenario
carries many **checks**. A morning-consolidation run is the expensive unit, so seeds for several
rules share one run, and each check is tied to its own seed items (tagged `[#TAG]`), so a failure
still points at one rule.

## 4. What runs: approaches considered

**A. The real runner, real `claude -p`, scripted connectors (recommended).** The harness builds a
vault with the checkout's own `bootstrap install`, sets `CLAUDE_BIN` to `claude_shim.py`, and runs
the vault's own `run-scout.sh` with `SCOUT_FORCE_MODE` set. The prompt, pre-hooks (materialize,
kb-pre-filter), post-hooks (backfill, connector-health-report) and brain files are exactly what a
user's run gets. The shim forwards to the real `claude` with eval flags added (§4.1). Cost: a full
session per sample.

**B. `claude plugin eval`.** It has a good case format, N-rep scoring and an HTML report. But:
- it has no fixture-as-cwd, only a scaffold script that copies;
- it has no custom-code graders, and Scout's checks are parser-level assertions over edited files;
- its mocks cover only MCP servers the *plugin* declares, while Scout's connectors live in the
  user's config and GitHub is a CLI;
- it has no version-vs-version arm, only with/without the plugin;
- it has no per-run tool-call metrics in its JSON.

We would be working around it at every step. Rejected for the core. Revisit it for the interactive
command evals (§11, slice 8), where its with/without ablation and skill-trigger graders fit.

**C. Phase-slice probes.** Assemble only the relevant sections (e.g. the action-items phase) with
a focused prompt over a small input. About 5–10× cheaper per check, but it tests a prompt that
never ships. A slice can pass while the full SKILL.md fails, because the whole failure class in
#196 is interaction and attention at full length. Kept as a later, optional tier for fast
iteration on one rule. It is never the gate.

**D. Replaying recorded tool calls without a model.** This cannot judge an instruction change,
because the instructions only matter to the model. A recorded transcript is useful as a stub
*source* (connector responses), not as the thing under test.

### 4.1 The claude shim and determinism levers

`run-scout.sh` calls `claude-with-retry.sh $LOG $CLAUDE_BIN <runner flags> -p "$PROMPT"`. The shim
receives the runner flags and adds or replaces:

| Flag / env | Why |
|---|---|
| `--bare` | Skips the operator's hooks, CLAUDE.md, auto-memory, plugins and keychain. Without it the operator's own setup leaks into the sample. Requires `ANTHROPIC_API_KEY` or an `apiKeyHelper` (§13 Q1). |
| `--strict-mcp-config --mcp-config <scenario>.mcp.json` | Only the stub servers exist. Server keys match production names (`claude_ai_Slack`, `claude_ai_Linear`, `claude_ai_Google_Calendar`, …) so tool names (`mcp__claude_ai_Slack__slack_search_public_and_private`) and `connector_log.classify()` match production. |
| `--output-format stream-json --verbose` | Tee'd to `stream.jsonl` in the result dir; the run log gets only the final result line. |
| `--model <pinned full id>` | Replaces the runner's `--model opus` alias, so alias drift never moves a baseline. Recorded in results. |
| `--permission-mode dontAsk --allowedTools <eval allowlist>` | Replaces `auto`. The allowlist: Read/Write/Edit/Glob/Grep/ToolSearch, `Bash(git *)`, `Bash(gh *)`, `Bash(scoutctl *)`, `Bash(date*)`, `Bash(python3 *)`, the vault's `scripts/*`, and `mcp__<stub servers>__*`. **Denials are a metric:** a phase that needs an unlisted tool shows up as a denial, not a hang. This is the one deliberate deviation from production (§12 R2). |
| `--max-budget-usd` | Kept from the runner (`MAX_BUDGET` input), so a runaway sample is cut off. |
| `--no-session-persistence`, isolated `HOME` and `CLAUDE_CONFIG_DIR` (temp dirs) | Never reads or writes the operator's `~/.claude`. |
| `PATH` = `stubs/` (fake `gh`) + the checkout's `scoutctl` + system | Real `gh` is unreachable. The checkout's `scoutctl` is the one under test. |
| `SCOUT_USER_TIMEZONE`, `TZ`, `GIT_AUTHOR_*` fixed | Same run, same dating, same commit authorship. |

Fixed inputs, stubbed connectors, a pinned model and CLI version, and a hermetic config are as far
as determinism goes. The remaining variance is the model's, so it is measured, not removed.

### 4.2 Model and cost per tier

Estimates for a consolidation run on the fixture: a ~45k-token SKILL.md plus 40–120 tool calls,
3–10 minutes. #196 measured $0.40–1.40 for single question tasks. A full run is larger, so these
are planning numbers that slice 3 replaces with measured ones.

| Tier | When | Model | Reps × arms | Scenarios | Est. cost | Est. wall time (concurrency 2) |
|---|---|---|---|---|---|---|
| 0 — static | every PR, plugin-test CI | none | — | all | $0 | < 1 min |
| 1 — smoke | on demand; PR label `eval` | Sonnet 5.5 (§13 Q3) | 3 × 2 | 2 core | ~$10–20 | ~20–30 min |
| 2 — full | before `release.sh prepare`; nightly once affordable | the production model (Opus) | 5 × 2 | all | ~$60–120 | ~1.5–2.5 h |
| (later) slice probes | developer iteration | Sonnet | 5 × 2 | single rule | ~$1–3 | minutes |

Tier 0 spends nothing (§9): fixture builds, the scrub test, assembly against the fixture, stub
coverage lint, and grader self-tests. The LLM judge (§6.3) is off in tier 1 and uses Haiku in
tier 2.

## 5. The fixture vault

### 5.1 Three layers, built fresh for every sample

1. **Skeleton, from the checkout under test.** `bootstrap.install(BootstrapConfig(...))` into a
   temp dir with fixed inputs:
   - user `Alex Example`, `alex@example.com`, instance `Scout`;
   - connectors `slack, calendar, linear, github` (plus `email` for the outage scenario);
   - `user_slack_id U0123456789`, `github_username alex-example`,
     `github_repos example-org/widgets,example-org/api`;
   - `claude_bin` = the shim, and a budget.

   The brain files, scripts, runners and seeds are therefore always the checkout's own. **No brain
   file is ever committed to the fixture.** This is the main answer to "kept current as phases
   change": a phase edit changes the fixture's SKILL.md automatically.
2. **Base content overlay** (`tests/eval/vault/`, committed, hand-written). The mature-vault
   properties #196 lists, authored from scratch:
   - `knowledge-base/projects/<slug>/<slug>.md` ×4: one verified recently, one months stale, one
     with no `**Last verified:**`, one with a commented date (the #41 trap);
   - `knowledge-base/people/` for Alex, Priya, Sam, a thin entity whose `works_on` under-reports,
     and `people.md`;
   - meetings in **both** `meetings/` and `knowledge-base/meetings/`, with registries that
     disagree;
   - `inbox.md`, `docs/wishlist/`, `research-queue/`, `review-queue.md`;
   - yesterday's `action-items/action-items-{{d-1}}.md` with tagged seeds, plus existing
     `action-items/context/*.md`.

   `knowledge-base.md` is **generated**, not committed: `build.py` writes a deterministic index
   (fixed seed, synthetic entries from a neutral word list) of a configurable size. The size is
   the trap #196 describes, and it isn't worth committing 500 KB of filler.
3. **Scenario overlay** (`scenarios/<name>/vault/`). The files this scenario adds or replaces.

After layering, the builder makes the vault a git repo with one commit, so "commits in this run"
and `--lossless-rev HEAD` work.

### 5.2 Dates

The model sees the real date (Claude Code's system prompt states it), and `date` is real. So
fixture dates are **relative and rendered at build time**: `{{d-1}}`, `{{d+5}}`, `{{wd-2}}`
(working days), `{{month}}`. A scenario declares `mode: morning-consolidation`, so the mode never
comes from the clock. A scenario that depends on working-day arithmetic declares
`weekdays_only: true`, and `eval.sh` refuses to run it on Saturday or Sunday unless
`--allow-weekend` is passed. This is honest about the one input we can't pin.

### 5.3 Anonymization is enforced, not promised

- The fixture is **never derived from a real vault**: not copied, not "sanitized", not used as a
  template. Every file is written from scratch to a structural description. The PR template for
  `tests/eval/` says so, and a reviewer checks it.
- **Allowlist scrub test (tier 0).** Every capitalised token, `@handle`, URL host, `[A-Z]+-\d+` id
  and `owner/repo` in `tests/eval/` must appear in `tests/eval/allowlist.txt`. That file holds the
  stand-ins only: Alex, Priya, Sam, `PROJ-`/`OPS-`/`DESK-`, `example-org`, `acme-co.slack.com`,
  generic nouns. A **denylist** would have to contain the real names it protects, so a public repo
  can't have one. The allowlist inverts that.
- Structural tokens survive because they are authored in, not scrubbed out: `[[wikilinks]]`,
  `[#TAG]`, `**Last verified:**`, `[unverified]`, `[single-source]` and ` — `.

### 5.4 Scripted connectors

**One stdio MCP server** (`stubs/mcp.py`, MCP Python SDK) launched once per persona from the
scenario's generated `mcp.json`. Each persona serves a catalogue of tool names and input schemas
for the tools the phases call. The catalogue is hand-written and holds only schemas, never
responses captured from a real account. Responses come from the scenario's `connectors.yaml`:

```yaml
slack:
  slack_search_public_and_private:
    - match: {query: "to:@alex"}           # regex per input field
      respond: fixtures/slack-dms.json     # rendered with the same date macros
    - respond: {messages: []}              # default
  slack_send_message:
    - capture: wrap                        # recorded; returns ok
linear:
  "*":
    - error: auth_expired                  # every Linear tool fails like an expired OAuth
calendar:
  gcal_list_events:
    - respond: fixtures/calendar.json
```

`gh` works the same way. `stubs/gh.py` is on PATH, matches argv against regexes, and prints a
fixture or exits non-zero with a scripted stderr. Unmatched calls on either side return a
"not available in eval fixture" error and are **logged as `unstubbed`**, which is a process
metric. Every call (persona, tool, input, matched rule, response) goes to `calls.jsonl` in the
result dir. Rules can carry `times: N` for "fails first, then succeeds" sequences.

The Slack DM that step 5 of the runner prompt asks for lands in `slack_send_message` and is
captured. That makes **the wrap notification a deterministic grading target**.

### 5.5 Keeping it current

- Brain files, runners and scripts: rebuilt from the checkout every sample (§5.1).
- **Stub coverage lint (tier 0).** Scan `phases/connectors/*.md` for MCP tool names and `gh`
  subcommands, and fail when one is neither served by a persona nor listed in
  `stubs/unserved.txt` with a reason. A phase that starts calling a new tool fails CI until the
  fixture learns it.
- **Grader self-tests (tier 0).** Each check runs against a committed, hand-written "good"
  post-run vault and a "bad" one. When a format change (a new section, a parser change) breaks a
  grader, CI says so before any money is spent.
- **Convention:** a PR that changes a behavioural rule in `phases/` adds or updates the seed and
  check for it, the way a code change adds a test (§13 Q7).

## 6. What's measured

### 6.1 Output checks (deterministic, per scenario)

Graders read the post-run vault through the engine's own parsers (`parser.parse_file`,
`render.parse`, `kb.lint`), so the harness and the product agree on what a file means.

| Check | Rule (source) | Assertion |
|---|---|---|
| `placement` | #371, `action-items.md:37-62` | Each seed tag is in its expected section. New items are matched by the PR or issue URL in `Refs:` and placed the same way. Seeds cover the table in §7.1. |
| `waiting_format` | `action-items.md:46-49, :489` | Every 🟢 waiting item's body matches `waiting on <who> since <date>`. |
| `no_drop` | dropoff audit `:288-295` | Every open tag from yesterday is open today, or in ✅ Recently Completed, or in `archive/stale-items-*.md` marked today, or is one of the scenario's `expected_closures`. |
| `count_guard` | `:272-286` | Open count today ≥ yesterday − closed − archived − explicit drops. |
| `dropoff_caught` | `:288-295` | When the scenario pre-seeds today's file with tags missing (§7.3), each missing tag is re-added with `[carried-via-audit]`, the wrap says `🚨 N items silently dropped`, and `review-queue.md` gained an entry. |
| `no_collapse` | `:270` | No open row summarises several items ("etc.", "and N more"). |
| `context_note` | #325, `:491` | Every **new** open tag has `action-items/context/<TAG>.md` with frontmatter `tag, title, created, updated`, created in the same commit as the item (from `git log`). `kb lint` reports no `missing-context-note`. Existing notes whose item gained evidence have a bumped `updated`. |
| `body_budget` | `:169` | Item body ≤ 2 sentences / ~25 words (warn at 25, fail at 40). |
| `outage_reported` | `00-run-modes.md:34-43` | For each persona scripted as down: the wrap and the run notes name it, `calls.jsonl` has ≥ 1 call to it (attempt before declaring), and the other personas were still queried. |
| `no_false_outage` | same | No persona that answered is reported as down. |
| `run_health_wrap` | P3, `01-run-health.md` (after #366) | With a scripted red finding, the wrap's first lines are `Before this run: <summary> — <fix>`. Seeded warnings give one `⚠️ N run-health warnings` line. The wrap **never** says the Claude login is expired. |
| `demotion` | #372 (proposed) | A seed past due by more than N days with no new evidence is in 🟡 with one "still want this?" line. Its headline is byte-identical to yesterday's, because "age is not a touch". Ships as an **expected failure** until #372 lands (§7.2). |

### 6.2 Process metrics (from `stream.jsonl` and `calls.jsonl`)

- Tool calls (total and per tool), assistant turns, input / output / cache tokens, cost (the result
  message's `total_cost_usd`), wall time.
- **Grep spiral:** the longest run of consecutive search calls (Grep, Glob, Bash `rg|grep|find`)
  with no Read or Edit of a returned path in between. Flagged at ≥ 6. This is the #194 regression.
- **Whole-file read of the big index:** a Read of `knowledge-base.md` with no `offset`/`limit`.
  The trap #196 names.
- **Repeated identical calls:** same tool and same input more than once.
- **`unstubbed` calls and permission denials.** Not failures in themselves, but always shown,
  because they mean the fixture or the allowlist is behind the phases.
- Commits made by the run.

A scenario can turn a metric into a check with a ceiling (`tool_calls <= 90`). Ceilings are set
from the first measured baseline at its median × 1.5, never guessed.

### 6.3 LLM-as-judge, only where nothing deterministic works

There are two uses, both off in tier 1:

- **Wrap clarity:** does the wrap lead with what needs the user, in plain words, without
  infrastructure narration?
- **Next-step phrasing:** for 🔴/🟡 items, is the body phrased as the user's own next step?

The judge is Haiku 5.5 with three votes, majority wins. The rubric and the judge's verdicts are
stored with the run. A judge check is advisory, never a gate, until its agreement with a human
review is measured on ~20 samples.

## 7. Starting scenarios

Two scenarios carry the first checks. A third is gated on #366.

### 7.1 `busy-weekday` (mode `morning-consolidation`, `weekdays_only`)

Yesterday's file plus connector scripts seed:

| Seed | Expected |
|---|---|
| Priya asked Alex a question in a DM, still open | 🟡 (🔴 if due < 3 days) |
| Review requested from `alex-example` by name on `example-org/widgets#12` | 🟡/🔴 |
| Alex's PR `CLEAN` and approved | 🟡/🔴 "merge" |
| Alex's PR `BEHIND` | 🟡/🔴 |
| Alex's PR with a human reviewer requested, bot approval only | 🟢 `waiting on Sam since {{wd-1}}` |
| Alex asked Sam something 1 working day ago | 🟢 |
| Alex asked Priya something 3 working days ago, no reply | 🟡 (nudge) |
| Delegated agent PR, `UNSTABLE` | 🟢 |
| Review requested only via team `@example-org/api-reviewers`, today | 🟢 |
| Alex's own task due in 40 days | 🟢 (ceiling) |
| Alex's own task due in 12 days | 🟡 at most |
| Deprecation notice saying "action required", cutover in 60 days | 🟢 |
| Waiting-on owner on leave | 🟢 with `(on leave through …)` |
| A ticket that closed overnight (Linear `Done`) | ✅ Recently Completed, origin row gone (`expected_closures`) |
| Two new inbound asks | new tags, each with a context note |
| 14 carried items with no new evidence | still open, untouched headlines (`no_drop`, `count_guard`) |

Checks: `placement`, `waiting_format`, `no_drop`, `count_guard`, `no_collapse`, `context_note`,
`body_budget`, `no_false_outage`, process metrics.

### 7.2 `overdue` (part of `busy-weekday` once #372 lands)

Seeds: a task due 10 working days ago with no new evidence, one due 2 days ago, and a recurring
`overdue` task. `demotion` is declared `expect: fail, tracks: 372` and reported but not scored as a
regression. On main it should fail, and that is the point: #372's PR flips it, and `compare` shows
the flip. The eval becomes #372's acceptance test.

### 7.3 `connector-down` (mode `morning-consolidation`)

`busy-weekday`'s vault with:
- Linear scripted `auth_expired` on every tool, and email scripted unavailable;
- **today's** action-items file already present, as if an earlier run today wrote it, and missing
  two of yesterday's open tags. The dropoff audit has a real silent drop to catch, and the drop
  is not the sample's own doing, so the check is fair.

Checks: `outage_reported`, `no_false_outage`, `dropoff_caught`, `no_drop`, `count_guard`, and the
Linear-sourced seeds still present (not dropped because their source was dark).

### 7.4 `run-health-wrap` (after #366 merges)

`busy-weekday` plus a pre-seeded `.scout-logs/run-outcomes.jsonl` and state, so `scoutctl health`
reports:
- a red `runs.failing` for *dreaming*;
- a red connector-login finding for Linear;
- a stale `auth.expired` from two days ago;
- two warnings.

Checks: `run_health_wrap` (leads with the dreaming and Linear reds, one warnings line, never
repeats `auth.expired`), plus everything in 7.1. "The wrap names the expired login" means the
**connector** login. The Claude login is the one thing the wrap must *not* claim is dead (§13 Q5).

## 8. Comparing a phase change

`eval.sh compare --base <ref> [--head <ref>|worktree] --scenarios … --reps N --model …`

1. Two git worktrees, each with its own engine venv. Each builds its own vault, so each arm gets
   its own brain files.
2. Samples are **interleaved** (A B A B …), so API-side drift and time of day hit both arms alike.
3. The output is one table per check (pass k/N per arm) and per metric (median and IQR per arm),
   plus links to every sample's result dir: stream, calls, vault diff and log.

**Verdict rules, chosen to be honest at small N:**

- A check is **stable** on an arm when it passed N/N, and **flaky** when 0 < k < N.
- **Regression:** base stable-pass and head ≤ N−2 passes. One miss in five on head reads as
  "flaky on head, rerun". Two or more misses where base had none reads as a regression. With N=5
  the chance of two or more misses from a check that truly passes 95% of the time is about 2%.
- **Improvement:** the mirror image.
- **Flaky on base:** reported as "noisy check" with its rate, and excluded from the verdict. A
  check that is flaky on main in three consecutive full runs is quarantined and gets an issue.
  Either the rule is ambiguous (a finding about the phase text) or the grader is wrong.
- **Metrics:** "worse" only when head's median is above base's max (non-overlapping ranges). This
  is the #194 shape: 7 → 11 is visible, 13 ± 2 isn't.
- `compare` exits 0 with no verdict when either arm had a sample abort (budget, auth, crash). A
  harness failure never reads as a regression.

Results are kept locally in `.eval-results/` and as CI artifacts. No baseline is committed in the
first slices, so every comparison pays for both arms. Caching main's arm is §13 Q6.

## 9. When it runs, and the budget

- **Locally, on demand:** `plugin/scripts/eval.sh run|compare|build|lint`. It is a script, not a
  `scoutctl` subcommand: `scoutctl` ships to every user, and this is a maintainer tool that needs
  the repo checkout, the stubs and an API key. `eval.sh build <scenario>` builds a vault you can
  open, which is useful for authoring seeds with no spend.
- **CI, tier 0:** pytest on every PR. `tests/unit/test_eval_*.py` covers the fixture build, the
  allowlist scrub, assembly against the fixture, stub coverage lint and grader self-tests. The live
  harness lives outside `test_*.py` names, so plain `pytest tests/` never spends.
- **CI, tier 1:** a new `plugin-eval.yml`. Triggers: `workflow_dispatch`, and the PR label `eval`
  on PRs touching `plugin/phases/**`, `plugin/templates/run-*.tmpl` or `plugin/skills/**`. The
  workflow posts the compare table as a PR comment. It is **advisory**: it never blocks a merge in
  the first release cycle.
- **Tier 2:** before `release.sh prepare`. `prepare` prints a reminder and links the last full
  report; whether it should refuse is §13 Q2. Nightly only once a month of tier-2 costs is known.

**Guardrails (all three, because each catches a different failure):**

1. **Per sample:** `--max-budget-usd` (default $6 Opus, $3 Sonnet) and a wall-clock timeout (20
   min), enforced by the harness, not trusted to the CLI.
2. **Per suite:** `--max-cost` (default tier 1 $25, tier 2 $150). It is checked before each sample
   launches with a running total from result messages. When the next sample would cross it, the
   harness stops and writes a partial report.
3. **Per month:** a dedicated API key in its own Console workspace with a hard monthly spend limit
   (§13 Q1). This is the only guardrail that survives a bug in the first two.

Concurrency is 2 by default. The key's rate limit is shared, and a rate-limited sample is an
aborted sample, never a failing one.

## 10. Error handling

- **A sample that crashes, hits its budget or times out** is `aborted`, kept with its logs, and
  excluded from pass rates. It counts toward the suite's spend.
- **An auth failure on the eval key** aborts the suite on the first sample. It never produces N
  zero scores.
- **A stub that receives a malformed call** returns an MCP error, as a real server would, and logs
  it. The model's handling of that error is part of the behaviour under test.
- **A fixture build failure** (bootstrap changed shape) is a tier-0 failure. It surfaces in pytest
  long before anyone spends.

## 11. Sequencing into PRs

Each PR is small, merges on its own, and leaves main working.

1. **Fixture vault builder + base overlay + allowlist scrub test** (tier 0). #196's step 1:
   valuable on its own, and the piece that can't be improvised later. Includes `eval.sh build`.
2. **Stubs:** the MCP persona server, fake `gh`, `calls.jsonl`, stub coverage lint (tier 0).
3. **Runner + metrics + the first checks:** `claude_shim.py`, `eval.sh run`, `metrics.py`, and the
   `busy-weekday` scenario with `placement`, `no_drop`, `count_guard` and process metrics. Grader
   self-tests. **This is the first useful slice**: a maintainer can run `eval.sh run busy-weekday
   --reps 3` and get #371's placement rate and the tool-call profile of today's main. It also
   replaces §4.2's estimates with measured cost and time.
4. **`compare`** (worktrees, interleaving, verdict rules).
5. **More checks:** `context_note`, `waiting_format`, `no_collapse`, `body_budget`, and the
   `connector-down` scenario (`outage_reported`, `no_false_outage`, `dropoff_caught`).
6. **`demotion` as `expect: fail, tracks: 372`.** Lands before or with #372.
7. **`plugin-eval.yml`** (tier 1, label-triggered, advisory) and the `release.sh prepare`
   reminder.
8. **Later, each its own spec-light PR:** `run-health-wrap` after #366; the LLM judge;
   DREAMING/RESEARCH scenarios; #196's knowledge-question tasks and the interactive commands,
   possibly on `claude plugin eval`; phase-slice probes.

Slices 1–3 together are the minimum that answers "did this phase edit make runs worse?". Slice 4
makes that answer one command.

## 12. Risks

- **R1. Cost creep.** Mitigated by §9's three guardrails and advisory-only CI.
- **R2. The eval allowlist differs from production's `--permission-mode auto`.** A behaviour that
  depends on a tool outside the list shows up as denials, which are always reported. If denials
  dominate, widen the list. Running `auto` in a temp HOME is not an option, because the
  classifier needs the operator's settings.
- **R3. Seeds teach to the test.** A phase could be tuned to the fixture's exact wording. Seeds
  are paraphrased per scenario, and the judge is never a gate.
- **R4. The fixture drifts from what real vaults look like.** Structural properties come from
  #196's list and from defects as they are found. When a real-vault bug shows a new property, a
  maintainer describes it structurally in an issue, and the seed is written from that
  description, never from the vault.
- **R5. Weekend runs.** Handled by `weekdays_only` (§5.2).

## 13. Open questions (product decisions)

1. **Credentials.** `--bare` (the hermetic option) needs `ANTHROPIC_API_KEY`. Create a dedicated
   key in its own Console workspace with a hard monthly cap, and set what cap? Suggested: $300 per
   month. CI needs it as a repo secret. The alternative, a subscription token via
   `claude setup-token` in an isolated `CLAUDE_CONFIG_DIR` without `--bare`, is less hermetic and
   has no spend cap.
2. **Release gate.** Should `release.sh prepare` *refuse* without a tier-2 report newer than the
   last `plugin/phases/` change, or only remind? Suggested: remind for v0.17, refuse from v0.18 if
   the reports have proven stable.
3. **Smoke-tier model.** Sonnet 5.5 keeps tier 1 cheap but isn't the production model, so a rule
   can pass on Sonnet and fail on Opus or the other way round. Use Sonnet for tier 1 and Opus for
   tier 2 (suggested), or Opus everywhere at roughly 3× tier-1 cost?
4. **#372 sequencing.** Land `demotion` as an expected failure first, so it serves as #372's
   acceptance test (suggested), or write it inside #372's PR?
5. **Run-health wrap target.** Confirm the intended assertion: the wrap names an expired
   **connector** login (Linear) and never claims the **Claude** login is expired. Should the
   scenario wait for #366 to merge (suggested), or be written against `run-health/p1` now?
6. **Baselines.** Pay for both arms on every compare (simple, suggested for now), or cache main's
   arm per commit and model for a week to halve tier-1 cost?
7. **Contribution rule.** Should a PR that changes a behavioural rule in `phases/` be expected to
   add or update its eval seed and check, stated in `plugin/CLAUDE.md` and the PR template?
8. **Scope of the first slices.** This spec targets briefing and consolidation runs (SKILL.md).
   Are DREAMING/RESEARCH runs and the interactive commands (`/scout-work`, `/scout-plan`) needed
   sooner than slice 8?
