# Agent-behaviour eval harness — design

**Status:** draft, revision 2 (2026-10-12). Revised after the maintainer's decision on credentials:
no API key and no paid spend, because this is a low-budget open-source project. Model-backed runs
happen locally, on the maintainer's normal Claude Code login, and CI runs only the free tier.
§13 records the decisions. Docs only; no code lands from this PR.
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
5. Nothing costs money. There is no API key anywhere. Model-backed runs use the maintainer's own
   subscription login, on their own machine, only when they ask, and they see how many sessions a
   command will start before it starts them. CI makes no model calls at all.

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
| Session isolation precedent | #354 (`connector_draft.py` `Isolation`, `session_settings`, `isolated_session`); setup-retirement spec §4.2 | Headless `claude -p` with `--setting-sources ""`, `--settings '{"disableAllHooks":true,…}'`, `dontAsk`, a narrow `--allowedTools` and `--no-session-persistence`. Its spike used a scratch `CLAUDE_CONFIG_DIR` with only the keychain login shared. The harness reuses this recipe; §4.1 adds what an eval needs on top. |

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

### 4.1 The claude shim: isolation on a subscription login

`run-scout.sh` calls `claude-with-retry.sh $LOG $CLAUDE_BIN <runner flags> -p "$PROMPT"`. The shim
receives the runner flags and runs the real `claude` with #354's isolation recipe plus what an
eval needs. There is no `--bare` and no `ANTHROPIC_API_KEY`: `--bare` reads only an API key, and
this project spends nothing (§13 Q1). Isolation comes from flags and a scratch config dir instead.

| Flag / env | Why |
|---|---|
| `CLAUDE_CONFIG_DIR=<fresh temp dir>` | The session reads and writes no part of the operator's `~/.claude`: no settings, no `.claude.json` MCP entries, no memory, no session history. |
| `CLAUDE_SECURESTORAGE_CONFIG_DIR=""` | Keeps the operator's keychain login reachable from the scratch config dir. This is the only thing shared. **Required:** without it a scratch config dir runs "Not logged in" (verified, §4.3). |
| `--setting-sources ""` and `--settings '{"disableAllHooks":true,"sandbox":{…}}'` | No user, project or local settings, so no global allow rule widens the tool list and no hook runs, plugin hooks included. This is #354's `Isolation.flags()`. The `sandbox` block confines Bash writes to the result dir and turns network off (§12 R2a′). |
| `CLAUDE_CODE_TMPDIR=<result>/tmp` | `TMPDIR` for sandboxed Bash, inside the write list. The default, `/tmp/claude`, is shared with other sessions. |
| `--strict-mcp-config --mcp-config <scenario>.mcp.json` | **Mandatory, not hygiene.** Without it the account's claude.ai connectors load even with a scratch config dir and empty setting sources. That means the maintainer's live, signed-in Slack, Linear, Calendar, Gmail and GitHub (verified, §4.3), so a sample could read or post to real accounts. With it, only the stub servers exist. Server keys match production names (`claude_ai_Slack`, `claude_ai_Linear`, `claude_ai_Google_Calendar`, …), so tool names (`mcp__claude_ai_Slack__slack_search_public_and_private`) and `connector_log.classify()` match production. |
| **Init-event guard** | The shim reads the stream's `system/init` event before the first turn. If `mcp_servers` lists anything outside the scenario's stub set, it kills the session and marks the sample `aborted: mcp_leak`. If a CLI release changes how `--strict-mcp-config` behaves, the harness fails closed instead of quietly using real accounts. |
| `--output-format stream-json --verbose` | Tee'd to `stream.jsonl` in the result dir. The run log gets only the final result line. |
| `--model <alias>`; resolved model **recorded** | The CLI resolves aliases (`sonnet` → `claude-sonnet-5` on 2.1.278) and rejected the full id `claude-sonnet-5-5` as unrecognized, so the shim passes an alias. It records the resolved id from the init event plus `claude --version`. `compare` refuses to judge arms whose resolved model or CLI version differ. Default `sonnet`; `--opus` opts in (§13 Q3). |
| `--permission-mode dontAsk --allowedTools <eval allowlist>` | Replaces the runner's `auto`. The list: Read/Write/Edit/Glob/Grep/ToolSearch scoped to the vault, written as `Read(//<abs vault>/**)` (the `//` form is an absolute path; verified, §4.3: a Read outside the vault is denied and lands in `permission_denials`), `Bash(git *)`, `Bash(gh *)`, `Bash(scoutctl *)`, `Bash(date*)`, `Bash(python3 *)`, `Bash(python *)`, the vault's `scripts/*`, and `mcp__<stub servers>__*`. **Denials are a metric:** a phase that needs an unlisted tool shows up in `permission_denials`, not as a hang. This is the one deliberate deviation from production (§12 R2). |
| `--no-session-persistence`, `--disable-slash-commands` | No transcript lands in any projects dir, and no skill expands by accident. |
| `--max-budget-usd` | Kept from the runner as a runaway brake. On a subscription the figure is a usage-equivalent estimate, not spend. **The CLI enforces it on an OAuth login** (verified, §4.3): the run ends after the turn that crosses it, with result subtype `error_max_budget_usd` and exit 1, so overrun is bounded by one turn. The harness's wall-clock timeout backs it up. |
| `PATH` = `stubs/` (fake `gh`) + the checkout's `scoutctl` + system | Real `gh` is unreachable. The checkout's `scoutctl` is the one under test. |
| `SCOUT_USER_TIMEZONE`, `TZ`, `GIT_AUTHOR_*`, `GIT_COMMITTER_*`, `GIT_CONFIG_GLOBAL=<fixture file>`, `GH_CONFIG_DIR=<temp>` | Same dating and commit identity every time. No global git or `gh` config is read. |

**HOME stays real**, because isolating it broke the keychain login (verified, §4.3). So the paths
that reach through HOME are closed one by one:
- The fixture vault's `scripts/cc-session-cache.sh` is replaced with a no-op. It reads
  `~/.claude/projects`, the maintainer's real session history.
- The `claude_sessions` connector is never enabled in a fixture.
- Git and `gh` config are redirected (table above).
- Files outside the vault and the plugin root are denied by the scoped allowlist.

A Read of a HOME path outside the vault is a reported metric (`outside_reads`), so a phase that
starts reaching into `~` is visible.

Fixed inputs, stubbed connectors, a recorded model and CLI version, and a scratch config are as far
as determinism goes. The remaining variance is the model's, so it is measured, not removed.

### 4.2 Tiers, models and what a run uses

All model-backed tiers run **locally, on demand, on the maintainer's subscription login**. CI runs
only tier 0, which makes no model call.

**Measured** (slice 3, 2026-10-12): one `busy-weekday` sample (`morning-consolidation`) on Sonnet
(`claude-sonnet-5`), CLI 2.1.278:
- 85 tool calls over 86 turns, 7.5 minutes;
- `total_cost_usd` 1.90 (a usage-equivalent, not money);
- longest search run 4, no whole read of the big index, no repeated calls, no reads outside the vault;
- 0 unstubbed connector calls.

It also confirmed the isolation:
- the init event listed exactly the three stubs, all `connected`;
- every GitHub call reached the fake `gh` (the Bash tool kept the stub first on PATH);
- the Slack wrap DM was captured.

A second sample after the review fixes ran 62 tool calls in 6 minutes, at 1.72. Each sample
missed a different placement:
- the first skipped the team-only review request;
- the second put a PR waiting on a reviewer into 🟡, counting Friday → Monday as two working days.

That seed was moved off the 2-working-day boundary, so it now tests the rule rather than the
arithmetic. Two samples, two different misses: this is why every claim is a rate over N, never a
single run.

On a subscription the cost is usage-limit headroom, not money, so the harness counts **sessions
started** and reports the usage-equivalent figure for scale. An Opus sample is expected to cost
several times as much.

| Tier | Where / when | Model | Default reps | Scenarios | Sessions started | Est. wall time (concurrency 2) |
|---|---|---|---|---|---|---|
| 0 — static | CI on every PR, and locally | none | — | all | 0 | < 1 min |
| 1 — check | locally, on demand while editing `phases/` | Sonnet | 3 head (base from cache, §8) | 1–2 named | 3 (6 on a cache miss) | ~15–30 min |
| 2 — pre-release | locally, a release-checklist step (§9) | Sonnet; Opus with `--opus` | 5 head (base from cache) | all | ~15–20 (×2 on misses) | ~1–2 h |
| (later) slice probes | locally, iterating on one rule | Sonnet | 5 | single rule | 5 | minutes |

Tier 0 (§9) covers fixture builds, the scrub test, assembly against the fixture, stub self-tests
and coverage lint, and the checks run against golden outputs. The LLM judge (§6.3) is off by
default. With `--judge` it adds three short Haiku calls per judged sample, through the same
isolated `claude -p`.

### 4.3 Verified on this machine (Claude Code 2.1.278, 2026-10-12)

Each run below used a fresh scratch `CLAUDE_CONFIG_DIR` and the flags `claude -p --model haiku
--tools "" --no-session-persistence --permission-mode dontAsk --setting-sources ""
--settings '{"disableAllHooks":true}'`, reading the stream's `system/init` event:

| Variant | Result |
|---|---|
| `CLAUDE_SECURESTORAGE_CONFIG_DIR=""`, `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` | Logged in on the subscription (`apiKeySource: none`), `mcp_servers: []`, answered. |
| Same, without `CLAUDE_SECURESTORAGE_CONFIG_DIR` | `Not logged in · Please run /login`, exit 1. |
| `CLAUDE_SECURESTORAGE_CONFIG_DIR=""`, **no** `--strict-mcp-config` | About 40 of the account's claude.ai connectors in `mcp_servers`, several `connected` (Slack, Linear, Gmail, Google Calendar, Google Drive, GitHub). |
| As the first row, plus `HOME=<temp dir>` | `Not logged in`. HOME can't be isolated. |
| `--model sonnet` | Resolved to `claude-sonnet-5`. The full id `claude-sonnet-5-5` was rejected as `unrecognized_model`. |
| `--max-budget-usd 0.001`, a prompt asking for five sequential Bash calls (slice 3) | Stopped after the first turn: result subtype `error_max_budget_usd`, `is_error: true`, exit 1, one tool call made. Enforced on the subscription login. |
| `--tools Read --allowedTools "Read(//<vault>/**)"` with `dontAsk`, cwd the vault, asked to read one file inside and one outside (slice 3) | The inside read succeeded and the outside read was denied, recorded in `permission_denials`. The single-slash form behaved the same here, but it is relative to the settings source, so the harness uses `//`. Reads inside the cwd may be allowed without any rule, so the rule's effect is "nothing outside the vault". |

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

- Tool calls (total and per tool), assistant turns, input / output / cache tokens, the result
  message's usage-equivalent `total_cost_usd`, wall time.
- **`outside_reads`:** Reads of paths outside the vault and the plugin root (§4.1).
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

There are two uses, both off unless `--judge` is passed:

- **Wrap clarity:** does the wrap lead with what needs the user, in plain words, without
  infrastructure narration?
- **Next-step phrasing:** for 🔴/🟡 items, is the body phrased as the user's own next step?

The judge is Haiku (`--model haiku`), run through the same isolated `claude -p` with no tools and
`--json-schema` for the verdict, with three votes, majority wins. The rubric and the judge's verdicts are
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

**As built (slice 6).** #372 landed before this check did, so `demotion` is a normal check. It
has one `busy-weekday` seed per branch:
- `OVRD`: `step_down`, 10 working days past due with no news;
- `LATE`: `hold`, 2 days past due, Alex's move, with its own tracker issue;
- `PARK`: `park`, stepped down 16 days ago per its seeded context note.

`hold` fails only on what the rule forbids: moving sections, asking "still want this?", or a body
stating its age. `park` requires the text unchanged, which is literal in the rule.

The recurring-`overdue` seed is left for later.

**First live sample** (2026-10-12, Sonnet, N=1):
- **`LATE` held** and **`PARK` parked**, both as the rule says.
- **`OVRD`** stepped down to 🟡 with `due 2026-09-28; still want this?`.
- **The miss:** its context note paraphrased the required line as "stepped down to 🟡, 14 quiet
  days with no news" instead of the literal `stepped down, past due with no news`. A later run
  must find that exact line to park the item, so the check counts the paraphrase as a miss.

### 7.3 `connector-down` (mode `morning-consolidation`)

`busy-weekday`'s vault with:
- Linear scripted `auth_expired` on every tool, and email scripted unavailable;
- **today's** action-items file already present, as if an earlier run today wrote it, and missing
  two of yesterday's open tags. The dropoff audit has a real silent drop to catch, and the drop
  is not the sample's own doing, so the check is fair.

Checks: `outage_reported`, `no_false_outage`, `dropoff_caught`, `no_drop`, `count_guard`, and the
Linear-sourced seeds still present (not dropped because their source was dark).

**As built (slice 5)** differs from the design above in three ways:
- the scenario carries its own vault rather than reusing `busy-weekday`'s;
- the earlier run's today file is pre-seeded without the carry-forward banner;
- a dropoff wrap counts when it surfaces the drop ("a silent dropoff"), not only for the literal
  token.

**First live sample** (2026-10-12, Sonnet, N=1). It ran before the inline-date fix, so Calendar
served a literal `{{today}}`, and the agent correctly flagged it as bad data.
- `outage_reported` passed: Linear and Gmail were named as "degraded", each with a logged call.
- `no_false_outage` passed.
- `no_drop` passed: RUNB and INVX came back with `[carried-via-audit]`, and `review-queue.md`
  gained an entry.
- `dropoff_caught` failed only on the wrap's wording ("came back after a silent dropoff"). That
  wording is now accepted.

Two fixture lessons came out of the review:
- **Inline connector replies weren't date-rendered.** Fixed.
- **A fixture vault read to run-health as "the scheduler has never recorded a tick".** So every
  wrap led with that red finding and its "reinstall the scheduler job" fix. The runner now seeds a
  recent tick.

The Bash sandbox (§12 R2a′) is what keeps a session from acting on such advice outside its result
dir.

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
2. When both arms run fresh, samples are **interleaved** (A B A B …), so API-side drift and time
   of day hit both arms alike. A base arm served from cache (below) can't be interleaved, so the
   report says so and shows the cached samples' age.
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
- `compare` exits 0 with no verdict when either arm had a sample abort (budget, login, rate
  limit, `mcp_leak`, crash). A harness failure never reads as a regression.

**As built (slice 4).**
- **The base arm** is a detached `git worktree` of the ref under
  `~/.cache/scout-eval/checkouts/<sha>`. It runs on head's venv with `PYTHONPATH` set to its own
  engine, so its `bootstrap install`, runner hooks and the session's `scoutctl` are all the base's.
  The harness's stubs and scorer stay head's.
- **The cache key** also includes the **weekday**, because phases branch on the day.
- **The cache** serves its newest samples first. `--fresh-base` replaces a key's samples, but only
  once a new sample has succeeded.
- **Stopping:** `compare` stops launching sessions after the first aborted sample.
- **Unevaluated heads:** a head that can't be evaluated where base could (it stopped rewriting
  today's file) counts as a regression.
- **Run limits void only their sample (2026-10-23, #84).** A sample that hit the session timeout,
  the runner timeout or the budget cap is left out of the checks and metrics, and the rest of the
  scenario keeps running and scoring. A slower or costlier run is behaviour, so a `within limits`
  row scores it like a check: one extra head hit is `flaky on head`, two are a regression. Other
  aborts (no session, a crash, `mcp_leak`) are harness failures: they still block the verdict and
  stop new launches. A truncated base sample is never cached. Before this, one head timeout on a
  slow network voided a whole 12-session compare twice.

**First live compare** (2026-10-12, Sonnet, N=1 per arm, base `origin/main` against a branch with
identical phases): the report gave placement 0/1 on base and 1/1 on head, verdict `need N≥2`.
- The base sample did a whole read of `knowledge-base.md`, the #196 trap. The head sample didn't.
- Tool calls were 82 against 108.

The same phases scored differently in one sample each. That is the noise the N≥2 rule and the
verdict rules exist for.

**The base arm is cached locally (§13 Q6).** Model-backed runs use someone's own subscription, so
running main again on every compare is wasted usage.
- A base sample is stored under `~/.cache/scout-eval/baselines/<key>/` with its result dir intact.
- The key is a hash of everything that shapes the run and nothing else:
  - the scenario's assembled SKILL.md and the rendered runner;
  - the tree hashes of `plugin/phases/`, `plugin/templates/` and the scenario dir;
  - the harness version;
  - the resolved model id and `claude --version`.
- A main commit that touches none of those reuses the cache. A phase change, a fixture change, a
  new model or a CLI upgrade misses it.
- When the cache holds fewer reps than asked for, only the missing reps run, and the new samples
  join the cache.
- `--fresh-base` ignores the cache and interleaves both arms. That's the right call when a
  verdict is close or the cache is more than two weeks old (the report flags age).
- Nothing is committed and nothing is shared between machines. A cache is one maintainer's
  record, and results stay in `.eval-results/` (gitignored).

## 9. When it runs, and the guardrails

**CI runs only what makes no model call (tier 0).** pytest runs it on every PR in the existing
`plugin-test.yml`; there is no new workflow, no secret and no paid tier. `tests/unit/test_eval_*.py`
covers:
- the fixture build and assembly of the brain files against it;
- the allowlist scrub;
- the stub server and fake `gh` self-tests: scripted responses, `times:` sequences, `unstubbed`
  logging, and the init-event guard fed a synthetic init event that lists a non-stub server;
- stub coverage lint;
- every check function run against committed golden outputs: a "good" and a "bad" post-run
  vault, `calls.jsonl` and `stream.jsonl` per check, all hand-written;
- `metrics.py` against a committed synthetic stream.

The live harness lives outside `test_*.py` names, so plain `pytest tests/` never starts a session.

**Model-backed runs are local and on demand:** `plugin/scripts/eval.sh run|compare|build|lint|cache`.
It is a script, not a `scoutctl` subcommand: `scoutctl` ships to every user, and this is a
maintainer tool that needs the repo checkout, the stubs and a logged-in Claude Code.
`eval.sh build <scenario>` builds a vault to open and author seeds against, with no session.

**Guardrails.** Usage comes out of someone's own subscription, so the harness is conservative by
default and loud about what it will do:

1. **Small defaults.** `run` and `compare` default to N=3 head reps on Sonnet, with the base arm
   from cache.
2. **A printed plan before anything starts.** Every model-backed command prints, then waits for
   Enter (or `--yes` in a script):
   `Will start 5 sessions (3 head, 2 base cache misses) on sonnet → claude-sonnet-5 via your Claude login. Estimated 15–30 min. Cached base samples: 1 (3 days old).`
3. **Explicit opt-in for anything larger.** A command that would start more than 10 sessions
   refuses without `--many`. Opus refuses without `--opus`. The judge needs `--judge`.
4. **Per sample:** a 20-minute (30 since 2026-10-23, #84) wall-clock timeout enforced by the harness, plus the runner's
   `--max-budget-usd` as a runaway brake if the CLI enforces it on a subscription login (§4.1).
5. **Concurrency 1 by default**, 2 with `-j 2`, never more. A sample that hits a usage or rate
   limit is `aborted`, never failing. The harness stops launching new samples after the first
   one, and writes a partial report that says which samples never ran.

**Pre-release (tier 2) is a documented local step, never a gate.** The release checklist gains:
"Run `plugin/scripts/eval.sh run --all` (or `compare --base <last tag>` when `phases/` changed)
and link the report in the release PR." `release.sh prepare` prints a reminder with the date of
the newest local tier-2 report and whether `phases/` changed since. It never refuses, because the
run depends on someone's local subscription (§13 Q2).

*As built (slice 7):*
- **There is no `--all` flag.** The checklist (README "Releases") names the scenarios, or points
  at `ls plugin/engine/tests/eval/scenarios`.
- **"Newest report" means the newest result dir under `.eval-results/`.** The date comes from the
  dir's `<YYYYMMDD-HHMMSS>` name.
- **"Changed since"** counts commits to `plugin/phases` after that time.
- **`compare --base <last tag>` is suggested** when `plugin/phases` differs from the previous
  release.
- **"Link the report" became "paste the summary".** `run` and `compare` print their report and
  write no report file.
- **The release PR body gains an unticked eval checkbox.** Nothing reads that checkbox.

## 10. Error handling

- **A sample that crashes, hits its budget brake or times out** is `aborted`, kept with its logs,
  and excluded from pass rates.
- **A login failure** (`Not logged in`, an expired OAuth token) aborts the whole command on the
  first sample, with the fix: `claude auth login`, then rerun. It never produces N zero scores.
- **A usage or rate limit** stops new launches and keeps what finished (§9).
- **An `mcp_leak`** (a non-stub server in the init event) aborts the command, not just the sample.
  It means isolation is broken, and nothing more should run until someone looks.
- **A stub that receives a malformed call** returns an MCP error, as a real server would, and logs
  it. The model's handling of that error is part of the behaviour under test.
- **A fixture build failure** (bootstrap changed shape) is a tier-0 failure. It surfaces in pytest
  long before anyone starts a session.

## 11. Sequencing into PRs

Each PR is small, merges on its own, and leaves main working.

1. **Fixture vault builder + base overlay + allowlist scrub test** (tier 0). #196's step 1:
   valuable on its own, and the piece that can't be improvised later. Includes `eval.sh build`.
2. **Stubs:** the MCP persona server, fake `gh`, `calls.jsonl`, stub coverage lint (tier 0).
3. **Runner + metrics + the first checks:**
   - `claude_shim.py` with the §4.1 isolation and the init-event guard;
   - `eval.sh run` with the printed plan and the opt-in flags;
   - `metrics.py`;
   - the `busy-weekday` scenario with `placement`, `no_drop`, `count_guard` and process metrics;
   - golden-output tests for each check.

   **This is the first useful slice.** A maintainer runs `eval.sh run busy-weekday` (3 Sonnet
   sessions) and gets #371's placement rate and today's tool-call profile on main. The slice also
   replaces §4.2's estimates with measured session times, and settles two §4.1 unknowns: whether
   `--max-budget-usd` is enforced on a subscription login, and how much the vault-scoped Read rules
   restrict.
4. **`compare`** (worktrees, the local base cache, interleaving with `--fresh-base`, verdict rules).
5. **More checks:** `context_note`, `waiting_format`, `no_collapse`, `body_budget`, and the
   `connector-down` scenario (`outage_reported`, `no_false_outage`, `dropoff_caught`).
6. **`demotion` as `expect: fail, tracks: 372`.** Lands before or with #372.
7. **Release-checklist step and the `release.sh prepare` reminder** (§9). The PR template gains
   the `phases/` nudge (§13 Q7). There is no CI workflow for model-backed runs.
8. **Later, each its own spec-light PR:** `run-health-wrap` after #366; the LLM judge;
   DREAMING/RESEARCH scenarios; #196's knowledge-question tasks and the interactive commands,
   possibly on `claude plugin eval`; phase-slice probes.

Slices 1–3 together are the minimum that answers "did this phase edit make runs worse?". Slice 4
makes that answer one command.

## 12. Risks

- **R1. Eating the maintainer's subscription.** Mitigated by small defaults, the printed plan,
  the opt-in flags, the base cache and stopping on the first usage limit (§9). CI never calls a
  model.
- **R2a′. The allowlist cannot confine Bash, so the sandbox does.** Read/Edit/Write are scoped to
  the vault. Allowed commands are not path-confined, and HOME is real:
  - `python3` runs arbitrary code, and the phases need it;
  - `sed -i`, `find -delete`, `awk 'system()'` and `git -C <elsewhere>`/`push` take any path;
  - a script written in the vault can be run.

  So the shim's `--settings` turns on Claude Code's Bash sandbox. That JSON is the only settings
  source under `--setting-sources ""`, and the sandbox applies there with `-p`:

  | Key | Value | Why |
  |---|---|---|
  | `sandbox.enabled`, `failIfUnavailable` | `true` | A session that cannot sandbox exits instead of running unsandboxed. |
  | `autoAllowBashIfSandboxed` | `false` | The eval allowlist still decides what runs. The default `true` would allow any command. |
  | `allowUnsandboxedCommands` | `false` | `dangerouslyDisableSandbox` is ignored. |
  | `filesystem.allowWrite` | the sample's result dir | The vault, `calls.jsonl`, `gh-config` and `tmp/` all live there. |
  | `filesystem.denyWrite` | `/tmp/claude*/-*`, `/private/tmp/claude*/-*` | These are other Claude sessions' trees, which the sandbox leaves writable. |
  | `network.allowedDomains`, `strictAllowlist` | `[]`, `true` | No host is reachable. The phases need none: MCP stubs run outside the sandbox, and `gh` is a stub. |

  The shim also sets `CLAUDE_CODE_TMPDIR=<result>/tmp`, so `TMPDIR` for Bash is in the sample.

  Verified on CLI 2.1.278 (macOS, `sandbox-exec`) with a scratch `CLAUDE_CONFIG_DIR`, Haiku and an
  empty strict MCP config:
  - `touch` outside the write list is denied before it runs. With the sandbox on, the Bash
    permission check reads a command's target paths, and `dontAsk` turns the prompt into a denial;
  - `python3` writes the check can't see fail at the OS layer with `EPERM`. These were tested into
    HOME, the system `$TMPDIR` and another session's `/tmp/claude-<uid>/-…` tree;
  - HTTPS through the proxy gets a `403` (`host is not on the allow list`), and a raw socket gets
    `EPERM`;
  - a command off the allowlist (`mkdir`) is still denied;
  - `git add` and `git commit` into the vault's existing `.git` work. `git init` does not: the
    sandbox always denies `.git/config` and `.git/hooks`. `build.py` creates the repo before the
    session starts.

  Measured with one live `busy-weekday` Sonnet sample (N=1). The sandbox broke none of the phases'
  steps:
  - no sandbox violations;
  - `scoutctl health`, the vault's `scripts/`, the stub `gh` (which writes `calls.jsonl`), and
    `git add` and `git commit` all ran;
  - 92 tool calls, 9.6 min and 2.38 usage-equivalent, against 85, 7.5 min and 1.90 for the first
    unsandboxed sample.

  The sample's 5 denials come from the allowlist and are the same with the sandbox off (checked
  A/B): `$(…)` and `${…}` expansions, a `for` loop, and `python` instead of `python3`. `python` is
  now on the list. It resolves to the engine venv's interpreter, and the sandbox confines it like
  `python3`.

  Writable regardless of the settings:
  - the top level of `/tmp/claude-<uid>/`. The Bash tool writes its cwd file there, and denying it
    fails every command;
  - the sandbox's built-in `/dev/*`, `~/.npm/_logs` and `~/.claude/debug`.

  Reads are not confined. HOME is readable, but with no network a read cannot leave the machine
  except through the transcript. The `outside_reads` metric still reports reads outside the vault.

  The containment from before stays:
  - the stub `gh`, plus a temp `GH_CONFIG_DIR`, with `GH_TOKEN`/`GITHUB_TOKEN` dropped;
  - only stub MCP servers;
  - API credentials dropped from the environment, and an `apiKeySource` check in the init guard;
  - nothing destructive or networked on the list (no `rm`, `mv`, `cp` or `curl`).

  Like R2a, this depends on CLI behaviour. Re-run the probe when `claude --version` changes.
- **R2. The eval allowlist differs from production's `--permission-mode auto`.** A behaviour that
  depends on a tool outside the list shows up as denials, which are always reported. If denials
  dominate, widen the list. Running `auto` under `--setting-sources ""` is not an option, because
  the classifier needs the operator's settings.
- **R2a. Isolation depends on CLI behaviour that may change.** `CLAUDE_SECURESTORAGE_CONFIG_DIR`
  is undocumented, and the claude.ai connectors load unless `--strict-mcp-config` is passed. A
  login change fails loudly (§10). An MCP change is caught by the init-event guard, which a
  tier-0 test pins. §4.3 is re-run whenever `claude --version` changes. The cache key includes
  the version, so the first compare after an upgrade shows it.
- **R2b. Results are one person's.** Without CI runs, a verdict is reproducible only by someone
  with a Claude login. Every report records the model, CLI version, harness version and scenario
  hashes, and result dirs keep full streams, so a reviewer can check the evidence without
  rerunning.
- **R3. Seeds teach to the test.** A phase could be tuned to the fixture's exact wording. Seeds
  are paraphrased per scenario, and the judge is never a gate.
- **R4. The fixture drifts from what real vaults look like.** Structural properties come from
  #196's list and from defects as they are found. When a real-vault bug shows a new property, a
  maintainer describes it structurally in an issue, and the seed is written from that
  description, never from the vault.
- **R5. Weekend runs.** Handled by `weekdays_only` (§5.2).

## 13. Decisions (2026-10-12)

Q1–Q3 and Q6 are the maintainer's decisions, relayed by the coordinator. Q4, Q5, Q7 and Q8 are the
recommendations, which the maintainer didn't object to. All stand until the revised spec is
approved.

1. **Credentials: no API key and no paid spend.** This is a low-budget open-source project. There
   is no `ANTHROPIC_API_KEY` anywhere, no `--bare`, and no CI secret. Model-backed runs use the
   maintainer's normal Claude Code login (subscription OAuth), isolated with #354's recipe plus a
   scratch `CLAUDE_CONFIG_DIR` and `CLAUDE_SECURESTORAGE_CONFIG_DIR=""` (§4.1, verified in §4.3).
   CI runs only tier 0.
2. **Release gate: remind only, never refuse.** A pre-release run depends on someone's local
   subscription. `release.sh prepare` prints a reminder (§9), and the tier-2 run is a documented
   checklist step.
3. **Models: Sonnet by default, Opus opt-in** (`--opus`), to spare subscription usage. The report
   names the resolved model, so a Sonnet verdict is never read as an Opus one.
4. **#372: land `demotion` first as an expected failure** (`expect: fail, tracks: 372`), so it is
   #372's acceptance test.
5. **Run-health wrap: wait for #366.** The assertion is that the wrap names an expired *connector*
   login and never says the *Claude* login is dead.
6. **Baselines: cache main's arm locally** and reuse it until anything that shapes the run changes
   (§8's key). `--fresh-base` reruns and interleaves both arms.
7. **Contribution rule: yes, as a nudge.** The PR template asks a PR that changes a behavioural
   rule in `phases/` whether it adds or updates an eval seed and check, and `plugin/CLAUDE.md` says
   the same. The nudge is not enforced.
8. **Scope: no.** Briefing and consolidation (SKILL.md) first. DREAMING/RESEARCH and the
   interactive commands stay in slice 8.

**Still open:** none. Slice 3 settled the two remaining facts (§4.3): `--max-budget-usd` is
enforced on a subscription login, and vault-scoped Read rules work in `--allowedTools`.
