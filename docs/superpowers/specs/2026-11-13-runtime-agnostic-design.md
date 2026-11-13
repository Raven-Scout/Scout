# Runtime-agnostic Scout: scheduled runs and skills on agent CLIs other than Claude Code — design

Status: **draft for Jordan's review.** Docs only; nothing here is built.
Implementation starts per phase (§12) on Jordan's go, after the open questions
(§14) are answered. The coordinator cuts implementation cards from the plan
(`docs/superpowers/plans/2026-11-13-runtime-agnostic.md`).

Builds on, and cites as "privacy §n", "delegates §n", "models §n", "eval §n":

- `2026-10-20-per-note-privacy-design.md` (#453): readers, output classes, `scoutctl kb`.
- `2026-10-31-vault-access-for-agents-design.md` (draft PR #550): the
  `delegate:<id>` reader (built, #625), delegate mirrors and the inbox.
- `2026-10-13-slot-model-selection-design.md` (#460, #461, #462).
- `2026-10-12-eval-harness-design.md` and the KB-writing release gate (`eval.sh kb-gate`).
- `2026-10-11-run-health-and-headless-updates-design.md` (auth, usage limits, self-update).

## 1. Why

Jordan's ask:

> "We're relying on the fact that it's focused on Claude Code way too much. We
> need to prioritize bringing support for Codex, Claude Duet, Gemini CLI, and
> Grokbot — as well as marketing that support in the repo and the website."

Today every scheduled run is a `claude -p` session launched by a vault shell
runner. Every interactive skill is a Claude Code plugin skill or slash command.
Distribution is a Claude Code marketplace. The privacy gates are Claude Code
hooks. A user without Claude Code can't use Scout at all, and a user who
prefers another agent can't move even one slot to it.

The ask has two halves. This spec designs the first: Scout runs on other
runtimes, with the privacy model intact. For the second, it sets the rule for
what the repo and site may claim at each step (§13). It does not change any
marketing copy.

## 2. Summary of the recommendation

1. **The four names fall into two kinds of target** (§3):
   - **Local agent CLIs** that Scout launches on the owner's Mac, as Claude
     Code is today:
     - OpenAI **Codex CLI**;
     - Google's CLI, which for consumer plans is now **Antigravity CLI**
       (Gemini CLI moved those users there on 2026-06-18 and now needs a paid
       key or a Code Assist licence);
     - xAI's **Grok Build** CLI, *if* that is what "Grokbot" means.
   - **Hosted agent platforms** that run off the laptop and that Scout never
     launches: xAI's **Grok Bot** and Anthropic's **Claude Duet**. Both are
     official, and neither has a CLI, API or hooks. Jordan's own spec #550
     already uses "grokbot" and "Claude duet" for exactly these two, and the
     `delegate:<id>` reader built for them has shipped (#625).

   Both identities stay explicit questions for Jordan (Q1, Q2). The evidence
   points to the hosted platforms.
1a. **Urgent, and independent of the rest.** Claude Code's docs say `--bare`
   will become the default for `-p`, and bare mode loads no hooks. Scout's
   gates would then silently stop running in scheduled runs. A guard ships
   first, as Phase 1a (§3.5, §12).
2. **One adapter interface in the engine, Claude Code first behind it.**
   `scout/agent_runtimes/` holds one adapter per local CLI. Each adapter
   builds the command, normalises output into one event and usage model,
   translates hook payloads to and from Scout's gates, and **declares its
   capabilities**. The shell runners keep their lock, lease, retry and
   liveness logic, and launch the CLI through `scoutctl agent-runtime exec`.
   Phase 1 moves Claude Code behind the interface with **identical argv,
   identical hooks.json and identical ledgers**. A golden test proves it.
3. **Privacy fails closed, decided by capabilities, never by trust.**
   - Before every launch, `scoutctl agent-runtime check` compares the
     adapter's *verified* capabilities with what the vault, reader and mode
     need. An unsafe combination is refused with a reason.
   - A runtime that can't block a tool call before it runs gets no scheduled
     run on a real vault. It may still read a **delegate mirror** (#550), which
     is safe without hooks because private and local-only content isn't in it.
   - A new model provider (OpenAI, Google, xAI) sees vault content only after
     the owner opts in, per provider (§8.4).
   - No existing gate is weakened to fit a runtime.
4. **Per-slot choice of runtime**, next to the per-slot model (#460):
   - `agent_runtime:` on a slot, `agent_runtimes.default` in config, and
     `claude-code` built in.
   - Model values are validated per runtime.
   - Slots already have a `runtime: local|remote` field (where the slot runs),
     so the new key gets a different name.
5. **Hosted platforms get no runtime adapter.** "Support" for Grok Bot and
   Claude Duet means #550's delegate path: mirror, bootstrap, inbox and,
   later, the MCP server. This spec only adds their marketing rule and how
   their acceptance is checked (§10).
6. **Claims follow shipped code.** A runtime is "supported" on the site or in
   the README only once it has shipped in a release *and* passed:
   - its conformance suite;
   - the KB-writing gate (`eval.sh kb-gate`);
   - the privacy eval scenario, on that runtime (§13).

## 3. The targets, with evidence

Facts were checked on 2026-11-13 against vendor docs and repositories.
**[docs]** is the vendor's own page, **[repo]** is its source repository or
release notes, **[2nd]** is a secondary source, and **[unknown]** means not
verified. Phase 0 re-checks every **[unknown]** that changes the plan by
hand.

### 3.1 OpenAI Codex CLI (`codex`)

| Question | Answer |
|---|---|
| Maintainer, license, version | OpenAI, Apache-2.0; **0.169.0, 2026-11-13** (npm `@openai/codex`, tag `rust-v0.169.0`) [repo] |
| Install | `npm i -g @openai/codex` [docs]; Homebrew [unknown] |
| Auth | Sign in with ChatGPT (subscription) or an API key (billed at API rates) [docs: learn.chatgpt.com/docs/auth]. `codex login --device-auth` (beta) for machines without a browser. `codex exec` "reuses saved CLI authentication by default" [docs: learn.chatgpt.com/docs/non-interactive-mode] |
| Plan needed for the owner's own login | CLI table lists Plus, Pro, Business, Edu/Enterprise; Free/Go access to the *CLI* [unknown]. Plus has 5-hour and weekly limits; Pro has no 5-hour limit [docs: learn.chatgpt.com/docs/pricing] |
| Headless | `codex exec "<prompt>"`: progress on stderr, final message on stdout. `--json` emits JSONL: `thread.started`, `turn.started`, `turn.completed`, `turn.failed`, `item.*`, `error`. `-o/--output-last-message`, `--output-schema`, `--ephemeral`, `--ignore-user-config`. Resume `codex exec resume <id>`. `--full-auto` removed. Exit codes not documented [docs] |
| Usage / cost | `turn.completed.usage`: input, cached input, output, reasoning tokens. **No cost field** [docs] |
| Permissions and sandbox | `sandbox_mode` `read-only` (exec default) / `workspace-write` / `danger-full-access`; `approval_policy = "never"` recommended headless. Network on/off. **Permission profiles** can `deny` *reads* of paths at the OS level (Seatbelt on macOS) [docs: learn.chatgpt.com/docs/sandboxing, config-reference] |
| Hooks | `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart/End` and more; **on by default**. User config `~/.codex/hooks.json` / `config.toml`; project `.codex/` only if the project is trusted. PreToolUse matches Bash, `apply_patch`, MCP tools (`mcp__server__tool`); deny via `permissionDecision: "deny"` or exit 2; input has `tool_name`, `tool_input`, `cwd`, `session_id`, `transcript_path`. **No dedicated file-read tool**: Codex reads through the shell. Docs call hooks "a useful guardrail, not a complete enforcement boundary". Whether hooks fire in `codex exec` [unknown] [docs: learn.chatgpt.com/docs/hooks] |
| Extensions | Skills = `SKILL.md` with `name` + `description` in `.agents/skills` or `~/.agents/skills`; plugins bundle skills, MCP and hooks; MCP stdio or streamable HTTP, `enabled_tools`/`disabled_tools` per server [docs: learn.chatgpt.com/docs/build-skills] |
| Context | `AGENTS.md`; `developer_instructions` (append) and `model_instructions_file` (replace), settable per run with `-c` [docs] |
| Custom endpoint | `model_providers.<id>.base_url`; the wire API must be the Responses API [docs] |
| ACP | Not native; adapter `agentclientprotocol/codex-acp` [repo] |

**For Scout:** a strong candidate for Tier F. The hook protocol is close to
Claude Code's, deny is first-class, and gate config can live in `~/.codex`,
outside the vault and outside a `workspace-write` sandbox. Two points need
Phase 0:

- whether hooks fire under `exec`;
- whether a crashed hook blocks.

Because every Codex read is a shell call, the read gate's Bash classifier
(`scout.kb.raw_read`, already shared with the eval) does all the work.
Codex's OS-level read deny could add a second, structural layer (§8.3, Tier
S).

### 3.2 Google: Gemini CLI (`gemini`) and Antigravity CLI (`agy`)

**Google moved consumer users off Gemini CLI.** Announced 2026-05-19,
effective 2026-06-18 [docs: developers.googleblog.com, "An important update:
transitioning Gemini CLI to Antigravity CLI"]:

- Free, Google AI Pro/Ultra and Google One users moved to **Antigravity CLI**.
- Gemini CLI still ships and keeps working with paid Gemini API keys,
  Vertex AI, and Gemini Code Assist Standard/Enterprise licences.

Jordan's ask names "Gemini CLI". For a user without one of those, the
Google target is in practice Antigravity CLI (Q5).

| Question | Gemini CLI | Antigravity CLI |
|---|---|---|
| License, version | Apache-2.0; **0.68.0, 2026-11-10** (npm `@google/gemini-cli`) [repo] | Closed source (the public repo holds issues, changelog and examples only); version [unknown] [repo] |
| Install | `npm i -g @google/gemini-cli` | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` [docs] |
| Own login without an API key | Only with a Code Assist Standard/Enterprise licence; otherwise a paid API key or Vertex. Whether a free AI Studio key still works [unknown] [docs: geminicli.com/docs/get-started/authentication] | Yes. All plans include it: a weekly quota for non-paying users, 5-hour + weekly for Pro/Ultra. "Bring-your-own-key is not supported"; headless reuses a prior interactive login [docs: antigravity.google/docs/plans] |
| Headless | `gemini -p`, `-o text\|json\|stream-json`; events `init`, `message`, `tool_use`, `tool_result`, `error`, `result`; exit codes 0/1/42/53; `--resume` [docs: geminicli.com/docs/cli/headless] | `agy -p`, `--output-format text\|json\|stream-json`, `--json-schema`, `--print-timeout`; events `init`, `step_update`, `result`. In request-review mode unapproved actions are soft-denied and the run still exits 0 [docs: antigravity.google/docs/cli/headless] |
| Usage / cost | Tokens per model; **no cost** | Tokens; **no cost** |
| Permissions | `--approval-mode default\|auto_edit\|yolo\|plan`; policy engine (TOML rules with `toolName`, `mcpName`, `argsPattern`, `commandRegex`, `decision allow\|deny\|ask_user`; `ask_user` = deny headless); workspace-tier policies currently disabled [docs: geminicli.com/docs/reference/policy-engine] | `permissions.allow/deny/ask` rules `action(target)` over `read_file`, `write_file`, `command`, `mcp`, …; deny wins; path read-deny supported [docs: antigravity.google/docs/permissions] |
| Hooks | `BeforeTool`/`AfterTool`, `SessionStart/End` and more, in `settings.json` (user, project, system) or extensions; block with exit 2 or `{"decision":"deny"}`; **malformed stdout fails open**; project hooks are fingerprinted and re-trusted on change. Whether hooks run under `-p` and the stdin schema [unknown] [docs: geminicli.com/docs/hooks] | `PreToolUse`, `PostToolUse`, `SessionStart`, `Stop`, …; `.agents/hooks.json` or `~/.gemini/config/hooks.json`; input `toolCall{name,args}`; `decision: "deny"` hard-blocks; 30 s default timeout; headless behaviour [unknown] [docs: antigravity.google/docs/hooks] |
| Extensions, context | Extensions bundle commands, MCP, hooks, skills; `GEMINI.md`; `GEMINI_SYSTEM_MD` replaces the system prompt [docs] | Plugins (`agy plugin import gemini`), skills in `.agents/skills/`, MCP in `.agents/mcp_config.json`; reads `GEMINI.md`/`AGENTS.md` [docs: antigravity.google/docs/cli/gcli-migration] |
| Custom endpoint | `GOOGLE_GEMINI_BASE_URL` [2nd only] | **None** [docs] |
| ACP | Native, `gemini --acp` [docs] | `agy acp` [docs] |

**For Scout:**

- **Antigravity** fits "the owner's own login, no API spend".
  - It has a deny-capable PreToolUse hook *and* path deny rules.
  - It is closed source and has no custom endpoint, so its conformance can
    only be proven by model-backed runs on Jordan's login (§11.2).
- **Gemini CLI** is open and testable with a fake model.
  - For most users it needs a paid key or an employer's Code Assist
    licence. The first breaks the no-paid-API rule for Scout's own testing;
    the second is the user's business.
  - A malformed hook reply fails open, so Scout's hook must always print
    valid JSON, including on its own errors. That is checkable, but it is the
    opposite of today's exit-2 default.

### 3.3 "Grokbot": xAI's Grok Bot, or xAI's Grok Build CLI

- **Grok Bot**
  - Official xAI product, announced 2026-08-11, beta [docs:
    x.ai/news/introducing-grok-bot]. Team Bots in public beta since
    2026-09-28 [docs: x.ai/news/team-bots].
  - Persistent agents, each on its own cloud computer, included with
    SuperGrok tiers and with some Cursor plans. It "signs in with your Cursor
    account" [docs: docs.x.ai/grok-bot/faq].
  - **No CLI, API or headless mode** is documented.
  - It is the product Jordan's #550 calls "grokbot". It is a hosted
    platform, so Tier D by construction (§8.12).
- **Grok Build (`grok`)**
  - xAI's official coding CLI; early beta from 2026-05-25 for SuperGrok and
    X Premium+ [docs: x.ai/news/grok-build-cli].
  - Apache-2.0, `xai-org/grok-build`; install `curl -fsSL
    https://x.ai/cli/install.sh | bash`; version [unknown] (x.ai/cli
    returned 403; a secondary page lists 1.0.13 on 2026-08-28).
  - Auth: browser login, `grok login --device-auth`, or `XAI_API_KEY`.
    Subscription login works headless from a cached token [docs:
    docs.x.ai/build/cli/headless-scripting].
  - Headless: `grok -p`, `--output-format plain|json|streaming-json`
    (schema undocumented), `-m`, sessions, `--always-approve`,
    `--max-turns`, `--rules` (append), Claude-style aliases
    (`--allowedTools`, `--append-system-prompt`). Exit codes, usage and cost
    output [unknown].
  - Hooks: Claude-like events (`PreToolUse`, `PostToolUse`, `Stop`, …) in
    `~/.grok/hooks/*.json`, `.grok/hooks/*.json`, and **also
    `.claude/settings.json` and `.cursor/hooks.json`**, with Claude tool
    names mapped. Deny with `{"decision":"deny"}` or exit 2.
    - **Default timeout 5 s, and a hook fails open on timeout, crash or bad
      output** [docs: docs.x.ai/build/features/hooks].
  - Permissions: `Read(…)`, `Bash(…)`, `MCPTool(server__*)` rules; `deny`
    wins, applies to paths shell commands touch, and still applies under
    always-approve [repo: grok-build user guide, permissions-and-safety].
  - ACP: native, `grok agent stdio`.
- **Community, not xAI:** `superagent-ai/grok-cli` (npm `grok-dev` 1.1.7,
  2026-05-15, MIT). API key only, so it is out under the no-paid-API rule.
  Nothing literally named "Grokbot" was found as a CLI.

**For Scout:** if Jordan means the CLI, Grok Build is a runtime candidate,
but **fail-open hooks keep it out of Tier F** unless Phase 0 finds a
fail-closed setting. Its path-deny rules may give a structural tier instead
(§8.3, Tier S). If he means the Bot, it is #550's delegate. Q1.

### 3.4 "Claude Duet": three candidates

| Candidate | Evidence | What "support" would mean |
|---|---|---|
| **Anthropic Claude Duet** | Official: "Introducing Claude Duet", 2026-10-21 [docs: claude.com/blog/claude-duet]; product page claude.com/product/claude-duet; help article "Get started with Claude Duet" on support.claude.com. Proactive agent app on iOS, Android and claude.ai, working on its own cloud computer; research preview with a waitlist for US Pro, Max and Ultra; model not selectable; uses the account's connectors, plugins and skills. **No CLI, API or hooks documented.** It is what #550 calls "Claude duet" | Tier D delegate via #550: the mirror through its GitHub connector, later a `scoutctl mcp` connector (#550 V5). Not a runtime adapter |
| `claude-duet` (EliranG) | npm 0.3.2 (2026-03-11), MIT [repo: github.com/EliranG/claude-duet]. Shares one Claude Code session between two people (P2P, end-to-end encrypted), driving the Claude Code CLI headless | Nothing to port: it *is* Claude Code underneath. At most a docs note that Scout's skills work inside it |
| `duet-cli` | PyPI 0.2.21 (2026-11-11), MIT [repo: github.com/volkan/duet]. Two agent CLIs (Claude Code, Codex, Gemini, …) take alternating implement/review turns | An orchestrator over runtimes. Covered once each runtime it drives is; no adapter of its own |

The brief's "unverified site" for the 2026-10-21 launch is now verified on
Anthropic's own pages. Recommended reading: the Anthropic product (Q2).

### 3.5 Agent Client Protocol, and Claude Code itself

- **ACP.** An agent *may* send `session/request_permission` before a tool
  call, and clients may auto-decide. No agent is required to ask, and
  auto-approve modes generally skip it [docs:
  agentclientprotocol.com/protocol/tool-calls]. So ACP is **not** a privacy
  gate. Approach E (§7) stays an implementation option at most.
- **Claude Code** (npm 2.1.325, 2026-11-13). Two facts from its headless docs
  [docs: code.claude.com/docs/en/headless] bear on Scout *today*:
  - Under "Start faster with bare mode", the docs say `--bare` "will become
    the default for `-p` in a future release". Bare mode skips
    auto-discovered hooks and installed plugins. Hooks passed with
    `--settings`, or in the `hooks/hooks.json` of a plugin loaded by flag,
    don't run either. Neither the headless page nor the CLI reference names
    an opt-out flag.
    - **When that default ships, every Scout privacy gate silently stops
      running in scheduled runs.**
    - This is a live risk independent of this spec. It gets its own urgent
      card (§12, "Phase 1a").
  - Since v2.1.302, `-p` starts in Manual mode when nothing sets a mode.
    Scout's runners set `--permission-mode auto`, so they are unaffected.
  - `total_cost_usd` and the `--max-budget-usd` check are documented as
    client-side estimates.

### 3.6 What each target needs the owner to hold

| Target | Plan for an own-login headless run | Scout cost |
|---|---|---|
| Claude Code | A paid Claude plan (today's requirement) | none |
| Codex CLI | ChatGPT Plus or higher (Free/Go CLI access unverified) | none |
| Antigravity CLI | Any Google account (weekly quota), Pro/Ultra for more | none |
| Gemini CLI | A Code Assist Standard/Enterprise licence, or a paid API key / Vertex | none for Scout; **a paid key is the user's choice and is never used in Scout's own CI or tests** |
| Grok Build CLI | SuperGrok or X Premium+ | none |
| Grok Bot (delegate) | A SuperGrok tier or an eligible Cursor plan | none |
| Claude Duet (delegate) | US Pro, Max or Ultra, off the waitlist | none |

Each vendor's data terms for consumer plans (training use, retention, human
review) are recorded per provider in Phase 0, for the consent prompt in §8.4
[unknown today].

## 4. Goals, non-goals, anti-goals

**Goals**

- G1. A scheduled slot can run on Codex CLI or Google's CLI (Antigravity,
  or Gemini CLI for users who have it) on the owner's Mac.
  It uses the owner's own login or subscription and keeps the same lock,
  lease, retry, run-health, ledger and commit-hook behaviour.
- G2. The interactive skills (`scout-ask`, `scout-work`, the launch skills, and
  the setup/status/update commands where they make sense) can be used from
  those CLIs.
- G3. Every privacy gate that protects a run on Claude Code protects a run on
  every other runtime Scout runs against the canonical vault. Where it can't,
  Scout refuses the run.
- G4. Claude Code stays the default and keeps working unchanged: same argv,
  hooks, ledgers, install path and app flow.
- G5. A capability model and a conformance suite, so a new runtime is a new
  adapter and a test run, not a redesign.
- G6. One rule for marketing claims, tied to evidence.

**Non-goals (this spec)**

- Running scheduled modes on hosted platforms (Grok Bot, Claude Duet). #550
  keeps them read-and-propose. Q2 of #550 is still Jordan's.
- Non-macOS hosts. Linux runners are a separate question; adapters shouldn't
  make them harder.
- Paid API keys, project API keys or paid CI. Never (§5).
- Replacing claude.ai connectors with a Scout-hosted connector service.
- Editing the website, README or plugin README copy. Only §13's rule is set
  here.

**Anti-goals: these would make it a failure even if it "works"**

- A runtime that runs as `autonomous` on a vault with private notes without a
  working read gate.
- A gate config that a run can edit or switch off (§8.5).
- A non-Claude run whose spend or usage Scout can't see, so it silently
  exhausts the owner's plan.
- A claim of "supported" that the evidence table (§13) doesn't back.
- Claude Code users seeing any change before they opt in.

## 5. Constraints

- **Privacy is non-negotiable** (privacy §4, §8; #595, #604, #607, #616,
  #625). Every gate decision stays in the engine (`scout/hooks/*`,
  `scout/kb/*`). Adapters only translate.
- **No paid API spend** (repo rule): each runtime uses the owner's own CLI
  login or subscription. Live tests on Jordan's accounts need his OK *per
  run*. CI never starts a model. The conformance suite uses a scripted local
  fake model, where the CLI allows a custom endpoint (§11.2).
- **One version, one tag, one release** (root CLAUDE.md). Adapters ship inside
  the plugin and engine, and release with them.
- **Everything the plugin needs at runtime stays under `plugin/`.** Per-runtime
  packaging lives under `plugin/runtimes/<id>/`.
- **Contract artifacts are generated, never hand-edited.** A slot field in
  `schedule.snapshot.json` or a connector key in `connectors.snapshot.json` is
  a contract change: regenerate, copy to the app, and land in one commit
  (`contract.yml`).
- **Public surfaces carry no vault content.** Fixtures use the shared
  stand-ins (plugin/CLAUDE.md).
- **Local tests: affected only.** CI's matrix is the gate.

## 6. The coupling audit

Each coupling is classified as one of:

- **portable**: already works on any runtime;
- **adapter**: needs a per-runtime adapter or a neutral replacement;
- **blocking**: a runtime without the capability named can't support it, so
  Scout must refuse or confine (§8.3).

Paths are relative to the repo root. Line numbers are as of `origin/main` at
59483466.

### 6.1 Launching sessions

| Where | What | Class |
|---|---|---|
| `plugin/templates/run-scout.sh.tmpl:12` (dreaming :11, research :11) | `CLAUDE_BIN="{{CLAUDE_BIN}}"`, filled by `bootstrap.py:244-261` (`resolve_claude_bin`) and `:299` | adapter |
| `plugin/templates/run-scout.sh.tmpl:168-177` (dreaming :162-171, research :147-156) | `claude-with-retry.sh $LOG $CLAUDE_BIN --permission-mode auto --model "$SCOUT_MODEL" --max-budget-usd N --name "$RUN_NAME" -p "$PROMPT"`. No allowlist: `auto` lets the session call any tool, and the gates do the restricting | adapter |
| `plugin/templates/run-scout.sh.tmpl:95-108` | The prompt: "Read SKILL.md … follow the Run Modes row" | portable (any agent with file read and shell) |
| `plugin/templates/run-scout.sh.tmpl:74-92` | Exports `SCOUT_MODE`, `SCOUT_READER=autonomous`, `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` | portable (`SCOUT_*`) / adapter (the title variable, §6.4) |
| `plugin/templates/scripts/claude-with-retry.sh.tmpl:57, 276` | Runs `"$CLAUDE_BIN" "$@"` with bounded retry | adapter |
| `claude-with-retry.sh.tmpl:77, 85, 89` | `TRANSIENT_PATTERNS`, `AUTH_PATTERNS`, `USAGE_LIMIT_PATTERN`: Claude CLI wording. Copied in `run-outcome.sh.tmpl:66,74`, `rate-limit-detect.sh.tmpl:36-43` and `plugin/engine/scout/health.py:62,90` | adapter |
| `claude-with-retry.sh.tmpl:95-146` | Headless login: `CLAUDE_CODE_OAUTH_TOKEN` from `scoutctl auth setup-token` (`plugin/engine/scout/claude_auth.py:43-53, 381-447`) | adapter |
| `claude-with-retry.sh.tmpl:222-249, 362` | Per-attempt spend and a smaller `--max-budget-usd` for the retry (#561) | adapter; blocking where there's no native cap (§8.6) |
| `plugin/engine/scout/tui/spawn_cmd.py:46`; `plugin/engine/scout/action_items/render.py:711` | TUI spawn and the "Launch Claude" button run `claude …` | adapter |
| `plugin/engine/scout/scripts/heartbeat.py:283` | `pgrep "claude.*scout-"` detects a running session | adapter |
| `plugin/engine/scout/scripts/connector_draft.py:270-279, 307-311, 428-464, 492-521` | Connector drafting: a second headless launcher (`--output-format json`, `dontAsk`, `--json-schema`, `--setting-sources ""`, `disableAllHooks`) | adapter (stays Claude-only in v1, §8.9) |
| `plugin/skills/scout-{briefing,consolidation,dream,research}/SKILL.md:10-12` | `nohup bash ~/Scout/run-*.sh &` | portable (the runner picks the runtime) |
| `plugin/engine/scout/defaults/com.scout.schedule-tick.plist:24-29` | launchd runs `scoutctl schedule tick`, which dispatches to `run-*.sh` | portable |

### 6.2 Output, cost and budget

| Where | What | Class |
|---|---|---|
| `plugin/engine/scout/scripts/run_cost.py:95-170` | Finds the run's transcript under `$CLAUDE_CONFIG_DIR/projects` (cwd-encoded folder), reads `custom-title` and `cost-state.totalCostUSD`, and falls back to `message.usage` de-duplicated by `message.id` | adapter |
| `plugin/engine/scout/cli.py:1015-1087` | `budget record-run` / `record-attempt` write ledger rows with `cost_source: transcript` | adapter |
| `plugin/engine/scout/scripts/budget_check.py:259-283, 328-412` | Daily budget gate; backs off on a Claude usage limit (`resets_at`) | portable logic over adapter data |
| `plugin/engine/scout/scripts/rate_limit_reset.py:1-30`; `cli.py:1089` | Parses "resets Oct 15 at 2pm" from the log | adapter |
| `plugin/engine/scout/hooks/session_tokens.py` (pricing at :66, :105-129) | Stop hook; prices tokens by Claude model family | adapter |
| `plugin/engine/scout/hooks/session_tool_log.py:57-85, 138-205` | Stop hook pairs `tool_use`/`tool_result` in the transcript for connector telemetry | adapter |
| `plugin/engine/scout/hooks/run_context.py:56-80` | Infers the run mode from the transcript's first user row | adapter |
| `plugin/phases/core/git-setup.md:86`; `plugin/phases/research/commit-notify.md:34` | `CLAUDE_MAX_BUDGET_USD` / `CLAUDE_BUDGET_SPENT_USD`, which Claude Code never sets (`run_cost.py:4`) | vestigial; remove in Phase 1 |

### 6.3 Permissions and tool names

| Where | What | Class |
|---|---|---|
| runners (§6.1) | `--permission-mode auto`, no `--allowedTools` | adapter |
| `plugin/engine/scout/hooks/mcp_tool_gate.py:1-24, 39-60, 93-96`; `plugin/engine/scout/custom_connectors.py:736-780` | `restrict_tools` is enforced by a PreToolUse hook over `mcp__<server>__<tool>` names | **blocking** without a pre-tool block, or a native per-server tool allowlist |
| `plugin/engine/tests/eval/harness/runner.py:119-131` | Eval allowlist in Claude permission-rule syntax (`Read(//vault/**)`, `Bash(<tool> *)`) | adapter |
| `plugin/phases/**` | Tool names: Read 25 lines in 17 files, Write 16/10, Edit 10/7, Glob 5/5, WebSearch 1, WebFetch 1, ToolSearch 1 (`core/00-run-modes.md:42`). Connector phases say "never grep or Glob in `knowledge-base/` … open a note with Read only to edit it" (`connectors/slack.md:166` and others) | mostly portable prose. The ToolSearch line and the tool-name rules need a runtime-neutral wording or a per-runtime glossary (§8.10) |
| `mcp__<server>__<tool>` naming: `hooks.json:16, :58`, `outbound_gate.py:64`, `connector_log.py:117-121`, `connector_detect.py:71-84` | Claude's MCP tool-name convention | adapter |

### 6.4 Privacy and security enforcement (the part that decides everything)

Every gate's decision is engine code over a payload `{tool_name, tool_input,
cwd, …}` (for example `kb_read_gate.decide()`,
`plugin/engine/scout/hooks/kb_read_gate.py:180-226`). Only the *delivery*,
which hook fires and how a deny is returned, is Claude Code's.

| Gate | Where | Blocks | Returned as | Without hooks |
|---|---|---|---|---|
| **kb-read-gate** (#453, #536; hook-skip #607 via #616) | `plugin/hooks/hooks.json:26-30`; `kb_read_gate.py:180-226, 272-279` | A run's shell/Grep/Glob read of `knowledge-base/` and `action-items/context/`; Bash that changes `SCOUT_MODE`/`SCOUT_READER` or runs `env -i`; detaching (#595); git calls that skip or redirect the commit hooks (#607) | JSON `permissionDecision: deny`; exit 2 when scoutctl can't run | **Partial.** `scoutctl kb` labels and filters what it serves, and the pre-commit widening guard refuses a run's widening commits. A raw `cat` of a private note is stopped only by this hook: **blocking** |
| **delegate-reader gate** (#625, #550 V1) | `hooks.json:36-40`; `kb_read_gate.py:255-269` | A Read by an `agent:*` reader of a KB note, or by a `delegate:<id>` reader of any vault file `scoutctl kb` wouldn't show it | JSON deny / exit 2 | **Partial.** `delegate_write_refusal` (`cli.py:4343-4410`) covers `scoutctl` only. On a delegate *mirror* the boundary is structural: the content isn't there (#550 §6) |
| **outbound-gate** (#453) | `hooks.json:16-20`; `outbound_gate.py:145-161` | MCP write tools whose input names private content: denied in a run, warned for the owner | JSON deny / additionalContext; exit 2 fail-closed for runs | **Partial.** The phase rule asks for `scoutctl kb check-outbound` (`phases/core/privacy.md:18`), but that relies on the model. Engine-made sends enforce it in code (`notify_telegram.py:254-257`, `phase_backport.py:396`). **Blocking** for model-made connector writes |
| **mcp-tool-gate** (`restrict_tools`, #590) | `hooks.json:6-10`; `mcp_tool_gate.py:93-96` | MCP tools a `restrict_tools` connector doesn't list, in every session | JSON deny | **blocking** (unless the runtime has its own per-server allowlist, §8.3) |
| **kb-read-label** | `hooks.json:48-52`; `kb_read_label.py:150` | Nothing; tells the session a note's label after a Read | PostToolUse `additionalContext` | Partial: `scoutctl kb read` labels its own output. Needs post-tool context injection, or Read denied in favour of `scoutctl kb read` |
| **shared-read-record** | `hooks.json:58-62`; `shared_read_record.py:100` | Nothing; records shared-notes answers for `check-outbound` | PostToolUse | adapter (only where the shared-notes MCP tools are configured) |
| **Run liveness / tamper trace** (#595, #604) | `plugin/engine/scout/kb/live_run.py:200-217`; `plugin/engine/scout/kb/hook.py:65-77`; `scout-lock.sh.tmpl:329-348` | Decides from `ps` that a commit is a run's: the lock holder, a running `claude-with-retry.sh`, or a `claude` argv with `--name`, `-p`, `--max-budget-usd` and the vault path, visible only because the runner sets `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` | — | adapter: each adapter supplies its argv markers. The wrapper and lock-holder checks are already portable |
| **Pre-commit widening guard** (privacy §8.3) | `plugin/engine/scout/kb/hook.py:41-100, 151` | A run's commit that widens privacy | git hook | **portable** (git, not the agent) |
| **Sharing / federation** | `plugin/engine/scout/kb/share.py`, `privacy.py`, `repo_sync.py` | Enforced at sync and read time | — | **portable** |

### 6.5 Transcripts and session stores

| Where | What | Class |
|---|---|---|
| `plugin/engine/scout/sessions/cli_home.py:20-21, 80-110`; `sessions/desktop.py:5-8, 183, 351`; `sessions/index.py:137-149` | Scout's session index reads `~/.claude/sessions`, `~/.claude/projects/*/*.jsonl` and the Claude desktop store | adapter (one reader per runtime's session store) |
| `plugin/engine/scout/scripts/cc_session_cache.py`; `plugin/phases/connectors/claude-sessions.md:17, 24` | The "Claude Code sessions" connector digests the owner's CC sessions | portable as a *connector*: other runtimes' sessions could become sibling connectors later (out of scope) |
| `plugin/engine/scout/repo_sync.py:170-176, 1311-1312` | Writes `OTEL_LOG_*=0` into `<vault>/.claude/settings.json` | adapter |
| `plugin/hooks/hooks.json` (8×), `plugin/templates/scripts/scoutctl.sh.tmpl:10, 27-43`, `plugin/engine/bin/scoutctl:13, 98-110`, `setup_wizard.py:570` | `CLAUDE_PLUGIN_ROOT` and the plugin cache layout locate the engine | adapter (the engine pointer, not the plugin root, becomes the neutral way to find scoutctl) |

### 6.6 Model selection

| Where | What | Class |
|---|---|---|
| `plugin/engine/scout/models.py:13-16, 32-36, 45-57` | Only `opus|sonnet|haiku` or `^claude-…$`; built-in `opus` | adapter (validate per runtime) |
| `plugin/engine/scout/schedule.py:86, 252-253`; `cli.py:2188-2260` | `Slot.model`; `scoutctl model resolve/show/set-default` | portable plumbing; values become per-runtime |
| `plugin/engine/scout/schedule.py:47-51, 85, 245-250` | **`Slot.runtime: local | remote` already exists** (where a slot runs) | naming constraint (§8.7) |
| `apps/macos/Scout/Models/ModelChoice.swift:9-75`; `Schedules/SlotEditForm.swift:188,197`; `Schedules/SlotModelTag.swift:10`; `Shell/ModelSettingsSection.swift:3,18,53` | Hardcoded Claude aliases in the app's pickers (#461) | adapter (the engine lists models per runtime) |
| `plugin/engine/scout/schedule.snapshot.json` | Per-slot keys `key, type, runner, fires_at_local, weekdays, on_miss`: no `model`, no runtime | contract (§8.7) |

### 6.7 Connectors

| Where | What | Class |
|---|---|---|
| `plugin/engine/scout/connectors.yaml:36-127` | Slack, Linear, Gmail, Google Calendar, Granola, Fathom and Google Drive are **claude.ai connectors** on the owner's Claude account; every fix link is claude.ai/settings/connectors | **blocking** on non-Claude runtimes: the accounts don't exist there. Each runtime needs its own MCP servers (§8.8) |
| `connectors.yaml:143-161` | Messages via the Claude-in-Chrome extension | blocking outside Claude |
| `connectors.yaml:129-141, 163-201` | GitHub via `gh`, WhatsApp as a local stdio MCP bridge, Telegram via `scoutctl notify` | portable / adapter (WhatsApp needs each runtime's MCP config) |
| `plugin/engine/scout/scripts/connector_detect.py:179-183`; `connector_uncovered.py:87` | Detection is `claude mcp list` | adapter |
| `plugin/engine/scout/scripts/connector_draft.py:347-370` | Custom connectors read `~/.claude.json` `mcpServers` | adapter |
| `plugin/engine/scout/connectors.snapshot.json:6-51`; app `ConnectorKeyAlias.swift:21-22`; `plugin/templates/connector-probes.yaml:5-38` | Keys `mcp:claude_ai_*`, `mcp:claude-in-chrome` | contract (neutral aliases, §8.8) |

### 6.8 Packaging, install and update

| Where | What | Class |
|---|---|---|
| `.claude-plugin/marketplace.json`; `plugin/.claude-plugin/{plugin,marketplace}.json` | Distribution is a Claude Code marketplace pinned to `stable`; `plugin.json` is the version source | portable as a version file. Distribution itself is adapter: other runtimes install the engine with `uv tool install …@stable` (as #550 §6.5) |
| `plugin/hooks/hooks.json` | Claude Code hook config | adapter (per-runtime gate configs, §8.5) |
| `plugin/skills/*/SKILL.md` (5) | Frontmatter is `name` and `description` only; no Claude-only tokens | portable (SKILL.md is read by Codex and Antigravity skills too, §3) |
| `plugin/commands/*.md` (7) | Slash commands. `scout-update.md` has 48 Claude references (`claude plugin …`, :27-118); `$CLAUDE_PLUGIN_ROOT` in `scout-setup.md:36-41`, `scout-status.md:173-205`; `$ARGUMENTS` in `scout-plan.md:12`; `/mcp` in `scout-connect.md:25,33` | adapter (generated per runtime; update/connect stay per-runtime) |
| `install.sh:24-26, 84-91, 121-122, 139-166` | Requires `claude`; installs through `claude plugin …` | adapter |
| `plugin/engine/scout/scripts/self_update_run.py:299-315`; `self_update.py:18` | Headless updater uses `claude plugin marketplace update` / `plugin update` | adapter |
| `plugin/engine/scout/scripts/bootstrap.py:69, 769, 825` | `--skip-claude`: a dead flag | cleanup |
| `plugin/engine/scout/scripts/bootstrap_doctor.py:369-430, 775-784`; `health.py:56-74, 495-514, 1340-1378` | Doctor and health probe the runner's `CLAUDE_BIN` and Claude auth; findings worded in Claude CLI terms | adapter |
| `scripts/e2e/self-update/bin/claude:1-180` | A Python stub of the Claude CLI surface the installer and updater use | template for a per-runtime stub |

### 6.9 macOS app

| Where | What | Class |
|---|---|---|
| `apps/macos/Scout/Engine/PrerequisiteChecker.swift:18-27, 84-97` | "Claude Code *installed* is the only hard gate" | adapter ("at least one supported runtime") |
| `Engine/ClaudeCodeCLI.swift:5-32`; `Utilities/ClaudeLauncher.swift:380-415` | Finds, versions, signs in to and installs `claude` | adapter |
| `Engine/EngineInstaller.swift:4, 11, 281-331, 354-355`; `EngineUpgrader.swift:17, 42, 113`; `ClaudePluginsRegistry.swift:3-81`; `EngineLocator.swift:18, 135-138, 193` | Engine install and upgrade register the plugin with Claude Code; `managed_by: claude-code` | adapter (the app-managed engine path, #312/#319, already exists for non-plugin installs) |
| `Onboarding/OnboardingView.swift:52-56, 100-121, 177, 203, 283`; `OnboardingViewModel.swift:325-329, 423-429` | Onboarding copy and connector detection through `claude mcp list` | adapter |
| `Services/ClaudeSessionService.swift:84-349`; `ControlCenter/Detail/{FilesTab,ToolsTab}.swift`; `ControlCenter/UsageRailCard.swift:128, 158-159`; `Sessions/*` | Tools/Files tabs, the Usage card and the Sessions page parse Claude transcripts and session stores | adapter (via `scoutctl session index`, which becomes runtime-aware) |
| `ActionItems/Views/LaunchClaudeMenu.swift`; `ActionItems/TopicBoard/BoardPromptSender.swift:16-66` | "Launch Claude Code", "Ask Claude" | adapter (later phase; a launch menu per installed runtime) |
| `Models/Run.swift:103-105` | Reads `=== model: … ===` from the run log | portable; gains `=== agent runtime: … ===` |

### 6.10 Eval harness

| Where | What | Class |
|---|---|---|
| `plugin/engine/tests/eval/harness/claude_shim.py:1-31, 57-58, 104-172, 211-213, 285` | Stands in for `CLAUDE_BIN`; isolates `CLAUDE_CONFIG_DIR`; Claude's Bash sandbox; `--strict-mcp-config` stub servers; `dontAsk` + allowlist; stream-json init guard | adapter (one shim per runtime, same contract) |
| `plugin/engine/tests/eval/harness/metrics.py:123-176` | Parses stream-json `system/init`, tool_use/tool_result, and `result.usage`/`total_cost_usd` | adapter (normalised events, §8.2) |
| `plugin/engine/tests/eval/harness/cli.py:134-180, 326-389`; `plan.py:15` | `kb-gate`, `compare-models` with a Claude model regex | adapter (`--agent-runtime`) |
| `plugin/engine/tests/eval/harness/checks/` (for example `privacy_hygiene`, which shares `scout.kb.raw_read` with the gate) | Scoring | portable, given normalised tool events |

## 7. Approaches considered

**A. Engine-side adapters; shell runners launch through `scoutctl agent-runtime exec` (recommended).**
The runner keeps everything it does today. It replaces only the line that
builds the `claude` argv with a call into the engine, and that call
`exec`s the CLI, so the process the liveness check sees is the CLI itself.
Runtime-specific knowledge lives in one Python package with unit tests:
argv, error patterns, output parsing, hook translation, models and
capabilities.

- For: small diff to the runners, which hold the recent privacy fixes (#595,
  #604, #607). One place to test. Claude's path can be proved byte-identical.
- Against: the runner templates change once, a managed-file rollout through
  `/scout-update`, which is the usual cost.

**B. A Python runner (`scoutctl run`) replacing the three shell templates.**
- For: cleaner long-term, and the adapters would be plain calls.
- Against: it rewrites lock, lease, retry, liveness and run-outcome
  handling, which is where the hardest privacy and reliability bugs were
  fixed. That is too much risk to couple to "support another CLI". Could
  follow later on its own merits.

**C. A runner template per runtime** (`run-scout.codex.sh`, …).
- Rejected: three templates × N runtimes of drift, and every fix lands N
  times.

**D. A shim that pretends to be `claude`** (translate Claude flags into
another CLI's, as `claude_shim.py` does for the eval).
- For: fastest to a demo.
- Against: it hides capability differences, which is the one thing the
  privacy model needs explicit. Rejected for product use; fine for a
  Phase 0 spike.

**E. One common protocol for every runtime (Agent Client Protocol).**
- For: if every target spoke ACP, with permission requests Scout could
  reject, one client could drive them all.
- Against: ACP lets an agent *skip* asking, and auto-approve modes do (§3.5).
  A permission request isn't a gate. Rejected as the interface and as a
  privacy mechanism. It might still serve as an adapter's transport where it
  beats a CLI's own headless mode.

## 8. Design

### 8.1 Vocabulary

- **Agent runtime**: a local agent CLI Scout launches (`claude-code`, `codex`,
  `antigravity` (or `gemini`), maybe `grok`). New config key `agent_runtime`.
- **Slot runtime**: the existing `Slot.runtime: local | remote`, meaning where
  a slot runs. Unchanged.
- **Delegate platform**: a hosted agent Scout doesn't launch (Grok Bot, Claude
  Duet). It reads as `delegate:<id>` per #550. It is not an agent runtime.

### 8.2 The adapter interface

`plugin/engine/scout/agent_runtimes/` has `base.py` (the protocol and data
types), `registry.py`, and one module per runtime (`claude_code.py`, then
`codex.py`, `antigravity.py`, …). Sketch:

```python
class AgentRuntime(Protocol):
    id: str                                   # "claude-code", "codex", "antigravity"
    display_name: str
    provider: str                             # "anthropic", "openai", "google", "xai"

    def locate(self, hint: Path | None) -> Path | None: ...      # the binary
    def version(self, binary: Path) -> str | None: ...
    def auth_status(self, binary: Path) -> AuthStatus: ...       # logged in? which plan?
    def capabilities(self, version: str | None) -> Capabilities: ...  # verified set, §8.3

    def validate_model(self, value: str, *, where: str) -> str: ...
    def default_model(self) -> str: ...
    def known_models(self) -> list[ModelOption]: ...             # for the app's picker

    def headless_argv(self, req: RunRequest) -> Launch: ...       # argv + env + stdin
    def gate_config(self, ctx: GateContext) -> list[GeneratedFile]: ...  # §8.5
    def hook_in(self, event: str, raw: bytes) -> GatePayload: ... # runtime → Scout
    def hook_out(self, event: str, d: GateDecision) -> HookReply: ...  # Scout → runtime

    def parse_events(self, lines: Iterable[str]) -> Iterator[RunEvent]: ...  # normalised
    def run_usage(self, run: RunRef) -> Usage: ...                # tokens, cost, source
    def classify_failure(self, log_tail: str, exit_code: int) -> Failure: ...
    def process_markers(self, req: RunRequest) -> ProcessMarkers: ...  # for live_run
    def session_store(self) -> SessionStore | None: ...           # index for app/TUI
```

- `RunRequest` carries the vault, mode, run name, prompt, model, budget cap,
  reader and the gate context.
- `RunEvent` is a small closed set: `init`, `tool_call(name, kind, input)`,
  `tool_result(ok)`, `message`, `usage(tokens, model)`, `final(ok, reason)`.
  The eval metrics, `session_tool_log` and `session_tokens` all move onto it,
  so one parser per runtime feeds all three.
- `Failure` is `transient | auth | usage_limit(resets_at) | budget | other`.
  The retry wrapper and run health use it, instead of grepping Claude's
  wording in four places.

**Runner plumbing.** Each runner resolves the runtime next to the model:

```bash
SCOUT_AGENT_RUNTIME="$("$SCOUTCTL_BIN" agent-runtime resolve --slot "$MODE" 2>>"$LOG_FILE" || true)"
[ -n "$SCOUT_AGENT_RUNTIME" ] || SCOUT_AGENT_RUNTIME=claude-code   # the pre-change behaviour
export SCOUT_AGENT_RUNTIME
"$SCOUTCTL_BIN" agent-runtime check --slot "$MODE" >>"$LOG_FILE" 2>&1 || { scout_record_skip refused_runtime; exit 0; }
```

- The launch line becomes `claude-with-retry.sh $LOG $SCOUTCTL_BIN
  agent-runtime exec --runtime … --model … --max-budget-usd N --name R -p
  "$PROMPT"`.
  - `exec` keeps the literal flag names `--max-budget-usd`, `--name` and `-p`.
    The wrapper reads the cap and run name by those names
    (`claude-with-retry.sh.tmpl:226-235`), and rewrites `--max-budget-usd`
    for a retry (`:362-370`, #561).
  - The adapter maps them to its CLI's own flags.
- `exec` builds the argv with the adapter and replaces itself with the CLI
  (`os.execvpe`). The wrapper's child is then the CLI, exactly as today.
- **Fallback asymmetry is deliberate.** A *resolve* failure falls back to
  `claude-code`, the default, as model resolve falls back to `opus`. A
  *check* failure, including "can't evaluate", skips the run (fail closed).
- **Claude Code acceptance.** For `claude-code`, `exec`'s argv must equal
  today's line (`--permission-mode auto --model M --max-budget-usd N --name R
  -p PROMPT`). A golden test pins it.
- **The wrapper keeps its file name** (`claude-with-retry.sh`), because
  `live_run.py:204, 212` and `kb/hook.py:65-77` match it. Renaming is a later
  cleanup with a dual match.
- **Between the wrapper's `exec` and the CLI,** `ps` briefly shows the
  `scoutctl` process carrying `--max-budget-usd`, `--name`, `-p` and the
  vault path. That process is already a run process under `live_run`'s
  argv rule, and the wrapper rule covers it anyway.

### 8.3 Capabilities, and the refusal rule

Each adapter declares capabilities **per CLI version range**. The
conformance suite (§11.2) *verifies* them, and a capability without a
passing test is treated as absent. The ones that matter:

| Capability | Meaning | Needed for |
|---|---|---|
| `pre_tool_block` | A hook runs before every tool call with the tool name and full arguments, and can deny it | kb-read-gate, delegate gate, outbound-gate, mcp-tool-gate |
| `pre_tool_fail_closed` | A hook that errors, times out or exits abnormally blocks the call (or can be made to) | Same: today's exit-2 fail-closed |
| `hook_env` | `SCOUT_MODE`, `SCOUT_READER`, `SCOUT_DATA_DIR` reach both the hook process and shell tool children unchanged | Every gate's scoping, and the commit hook |
| `tool_coverage` | Every built-in tool that can read a file, list a directory, search, or run a command is known and classed. Unknown tools are denied in a run | kb-read-gate completeness |
| `gate_config_outside_session` | The gate config loads from a path the session can't write (or the gate denies writes to it) | Tamper resistance (§8.5) |
| `post_tool_context` | A hook can add text to the model's context after a tool result | kb-read-label, shared-read-record |
| `mcp_stdio`, `mcp_tool_allowlist` | Runs local MCP servers; can restrict a server's tools natively | Connectors; `restrict_tools` without a hook |
| `path_deny` | The runtime itself (OS sandbox or permission rules that also cover shell commands) refuses reads and writes of listed paths, whatever the hook does | Tier S (structural reads) |
| `headless_events` | Machine-readable event stream with tool calls, a final status and usage | Ledgers, telemetry, eval |
| `usage_tokens`, `cost_estimate`, `budget_cap` | Reports tokens; reports a USD estimate; enforces a spend cap itself | §8.6 |
| `custom_endpoint` | Can be pointed at a local fake model | Conformance suite (§11.2) |
| `skills`, `commands`, `context_file` | Reads SKILL.md-style skills; custom slash commands; an instructions file (AGENTS.md, GEMINI.md) | Interactive skills (§8.10) |

**Privacy tiers**, derived from the verified set:

- **Tier F (full):** `pre_tool_block` + `pre_tool_fail_closed` + `hook_env`
  + `tool_coverage` + `gate_config_outside_session` + `headless_events`.
  May run as `autonomous` on the canonical vault, like Claude Code today.
- **Tier L (labels missing):** Tier F without `post_tool_context`.
  - Allowed, but the adapter makes the read gate also deny a run's raw `Read`
    of a KB note in favour of `scoutctl kb read`, which labels its own
    output.
  - That is *stricter* than Claude Code, never looser. Editing a note still
    works: the session reads it through `scoutctl kb read` and then edits it.
- **Tier S (structural; to be proven in Phase 0, not assumed):** a runtime
  with `path_deny` but no fail-closed hook (Grok Build's hooks fail open,
  §3.3; Codex's OS read-deny could back up its hook, §3.1).
  - The run gets `knowledge-base/`, `action-items/context/`, local-only
    paths and the gate configs as denied paths at the runtime level.
  - It reaches the KB only through Scout's MCP server (`scoutctl mcp serve`,
    the server #550 V5 designs, here on the laptop over the canonical vault
    as `autonomous`). The server runs outside the runtime's sandbox and
    applies the labels itself.
  - KB writes become MCP write tools that run kb-lint.
  - Connector writes still need the outbound check, so a Tier S runtime
    either has `pre_tool_block` for MCP writes, or gets no connector write
    tools in a run: drafts go to `drafts/` and the wrap goes through
    `scoutctl notify`.
  - This is a bigger build than Tier F. It only happens if a wanted runtime
    can't reach Tier F.
- **Tier D (delegate only):** anything less.
  - The runtime may run only against a **delegate mirror clone** (#550 V2)
    as `delegate:<id>`.
  - No private notes, no local-only paths, no shared-from-others notes, and
    no `.scout-*` state are in the checkout, so nothing a missing hook would
    have stopped is there to leak.
  - It never runs against the canonical vault, never as `autonomous`, and
    never commits the vault.

**The refusal rule.** `scoutctl agent-runtime check` (in the runner before
every run; in the skills' preamble before interactive use) refuses, with a
reason, when any of these hold:

1. The vault, reader and mode need a capability the runtime lacks:
   - an `autonomous` run on the canonical vault needs Tier F or L;
   - a vault whose `connectors.custom.yaml` uses `restrict_tools` needs
     `pre_tool_block` or `mcp_tool_allowlist`, in *any* session, as today;
   - a vault with Agnes shared-notes tools configured needs
     `post_tool_context`, or those tools are disabled for that runtime.
2. The vault has content the runtime's tier mustn't see:
   - Tier D on a checkout that holds any `visibility: private` note, a
     local-only path, a `source: shared:` note, `federation_scope` other than
     `none`, or `.scout-inbox/`, which means any canonical vault;
   - in practice, Tier D is a mirror-only mode.
3. The runtime's model provider isn't in the owner's consented providers
   (§8.4).
4. The installed binary's version hasn't passed the conformance probe
   (§11.2). A newer CLI isn't assumed to keep its hooks working.
   - **Where the CLI takes a custom endpoint** (Claude Code, Codex, maybe
     Gemini CLI and Grok Build), the probe runs **locally and
     automatically** the first time a new version is seen.
     - It is the fake-model probe of Phase 1a, generalised. It is free,
       takes seconds, and is cached by version and gate-config hash.
     - So a CLI auto-update costs one probe, not a Scout release.
   - **Where it doesn't** (Antigravity), only versions proven by a
     model-backed run on Jordan's login, shipped in `verified.json`, pass.
     A newer one is refused until re-proved. Q11 asks whether that's
     acceptable, or whether such a runtime should pin its CLI version.
5. The check can't run, or its inputs can't be read: refuse (fail closed). The
   refusal is a run-health event `agent_runtime.refused` with the reason, and
   it shows in the doctor.

Never: lowering a gate's strictness, a "trust me" override for runs, or a
fallback from a refused runtime to Claude Code. A silent fallback would hide
the refusal; `SCOUT_AGENT_RUNTIME=claude-code` is the owner's explicit
override.

### 8.4 The provider boundary (a new privacy question)

Today every run sends vault content to one model provider (Anthropic), under
the owner's own plan. A slot on Codex sends it to OpenAI; on Antigravity or Gemini CLI, to
Google; on Grok, to xAI. Their terms differ:

- whether a consumer plan's prompts train models;
- how long data is retained;
- whether a free tier's data is reviewed by people.

§3 records what each vendor says. The privacy spec's readers don't model
this, because it never had to.

**Proposal (Jordan decides, Q4):**

- `privacy.providers` in the owner's overlay lists consented providers.
  The default is `[anthropic]`, which is today's behaviour.
- Adding one is an explicit owner step, `scoutctl privacy consent-provider
  openai`, recorded like the P5 consent (#510).
- Until then `check` refuses that runtime for any session that reads the
  vault as `owner` or `autonomous`. A delegate mirror is already
  normal-notes-only, so it needs only the same consent.
- Local-only paths (privacy §4.3: never leaves the laptop) stay readable by a
  consented provider's laptop runs, exactly as they are by Anthropic's
  today. Q4 asks whether Jordan wants them *excluded* for non-default
  providers. If so, the read gate denies them to those runs, which needs
  Tier F.

### 8.5 Gates on another runtime

- **One decision, many deliveries.** The decision functions don't change.
  - Each adapter's `hook_in` turns the runtime's hook payload into Scout's
    `GatePayload`: `tool_kind` ∈ `{shell, read, search, glob, list, write,
    edit, web, mcp, unknown}`, plus `tool_name`, `input`, `cwd` and
    `session`.
  - `hook_out` renders the runtime's deny or allow reply and its exit code.
  - `scoutctl hook kb-read-gate --agent-runtime codex` is the entry point.
    Without the flag it is Claude Code, unchanged.
- **Tool classing is an allowlist.** For a non-Claude runtime, a tool the
  adapter doesn't class is `unknown` and denied in a run. Today's Claude Code
  matchers (`Grep|Glob|Bash`, `Read`) stay as they are in Phase 1, so G4
  holds. Phase 1 also files a separate card to check Claude Code's newer
  read-capable tools against them. That check doesn't change behaviour in
  this spec.
- **Gate config is generated, outside the vault, and out of the session's
  reach.**
  - `scoutctl agent-runtime install` writes each runtime's hook
    config from the adapter's `gate_config()` into that runtime's *user*
    config, not the vault. It writes a marked block and owns only that
    block.
  - On a non-Claude runtime, the read gate denies a run's shell, write and
    edit calls that touch any runtime's gate config path or `.git/hooks`.
    - #607 does this only for **Bash** on Claude Code: the matcher is
      `Grep|Glob|Bash` (`hooks.json:26`), and `decide()` allows Write and
      Edit (`kb_read_gate.py:186-187`).
    - So a Claude run's Write/Edit into `.git/hooks`, `.git/config` or
      `<vault>/.claude/settings.json` isn't gated today.
    - That is an existing gap, independent of this spec. It gets its own
      hardening card: a privacy fix to `hooks.json` and `decide()` that
      ships as a normal fix. It isn't a Phase 1 change, so G4's
      byte-identical `hooks.json` pins the file as it is when Phase 1
      starts.
  - Where a runtime only loads hooks from the project directory (inside the
    vault), the run's gate denies writes to that file. The adapter is still
    Tier F only if the conformance suite proves a deleted or edited config
    fails closed.
  - **Project-level config in the vault is a two-way risk.** Grok Build
    also loads hooks from a project's `.claude/settings.json` and
    `.cursor/hooks.json` (§3.3), and Codex, Gemini and Antigravity read
    `.codex/`, `.gemini/` and `.agents/`. So:
    - the read gate denies a run's write to any of those paths in the vault
      (`repo_sync` already writes `<vault>/.claude/settings.json`, §6.5);
    - `install --check` reports any hook config found there that
      Scout didn't write.
  - **Hook replies are always well-formed.** Gemini CLI treats malformed
    hook output as *allow* (§3.2). Every adapter's `hook_out` therefore
    prints a valid deny on any internal error and never relies on a crash
    meaning "block". The conformance suite injects errors to prove it.
  - Generated configs are compared with `install --check` in the doctor
    and in a unit test. They are never hand-edited.
- **The shell prefilters** in `hooks.json` exist to avoid a Python start per
  tool call. Each runtime's generated config carries an equivalent prefilter
  where its hook protocol allows. Where it doesn't, the hook runs
  `python -m scout.hooks` (the fast path, `hooks/__main__.py:18-23`) on every
  call; that costs speed, never safety.
- **Liveness.** `process_markers()` gives `live_run` the argv words that
  identify the run's CLI process, plus any environment the runner must set
  to keep argv visible (for Claude, `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1`).
  The wrapper-ancestry and lock-holder checks already cover every runtime.
  The argv check is a third signal. A runtime whose process hides its argv
  can't pass the liveness conformance test, and loses Tier F.

### 8.6 Usage, cost and budget

- **Ledgers gain three keys.** `agent_runtime`, `provider`, and `cost_source`
  ∈ `{reported, priced, unpriced}` join the existing `model`. Absent means
  Claude Code (old rows).
- **Claude Code is unchanged.** Its cost comes from the transcript's
  `cost-state`, and the native `--max-budget-usd` cap applies.
- **Runtimes that report tokens but no USD:**
  - Tokens are recorded, and `cost_source: unpriced`.
  - Scout keeps no price catalogue for other vendors (models §3 already
    refused one for Claude IDs). On a subscription the USD figure is
    notional anyway; the real limit is the plan's quota.
- **Budget without a native cap.** The runner always enforces:
  - a wall-clock timeout per run (the wrapper already bounds retries; add
    `run_timeout_minutes`, default 45);
  - a turn cap where the CLI has one.

  The daily budget check counts USD for priced rows. For unpriced runtimes it
  adds an optional daily run count and token ceiling
  (`budget.per_runtime.<id>.{max_runs_per_day, max_tokens_per_day}`), off by
  default. Q6 asks whether Jordan wants a ceiling on by default.
- **Usage limits.** Each adapter's `classify_failure` parses its CLI's
  quota-exhausted message into `usage_limit(resets_at)`. The existing back-off
  (`budget_check.py:328-412`, `rate_limit_reset.py`) then applies per
  runtime, so one plan running dry doesn't stop slots on another.
- **Stop-hook accounting** (`session_tokens`, `session_tool_log`) becomes a
  post-run step on the normalised events, for runtimes without a Stop hook.
  Claude keeps its Stop hooks in Phase 1.

### 8.7 Choosing the runtime per slot

Mirrors models §3:

```yaml
# .scout-state/schedule.yaml
  dreaming-night:
    type: dreaming
    runner: run-dreaming.sh
    agent_runtime: codex      # optional; absent = inherit
    model: gpt-5-codex        # validated by the slot's runtime
# scout-config.yaml
agent_runtimes:
  default: claude-code
  codex:       { model: <default model for codex>, binary: null }   # binary: auto-detect
  antigravity: { model: <default model for agy>, binary: null }
```

- **Precedence:** `SCOUT_AGENT_RUNTIME`, then the slot's `agent_runtime`, then
  `agent_runtimes.default`, then `claude-code`.
- **Model precedence** is unchanged, except that `models.default` applies only
  to Claude Code and `agent_runtimes.<id>.model` applies to the others.
- **Validation:** `validate_model(value, runtime)`. Claude keeps today's
  regex. Other adapters syntax-check their vendor's ID shape, with no
  catalogue.
  - A slot whose `model:` doesn't fit its runtime is a schedule error naming
    both.
  - The app's save path rejects it first.
- **Snapshot:** like `model` (models §4.4), `agent_runtime` stays out of
  `schedule.snapshot.json` while the seeded defaults carry none, so there is
  no contract churn. If the app later needs it from the snapshot, it is a
  generated-artifact change in one commit.
- **CLI:** `scoutctl agent-runtime list|show|resolve|check|exec|install|models [--json]`.
  The app uses `list` (installed, version, signed in, tier, consented) and
  `models --runtime X` (its picker options). That removes the hardcoded
  `ModelChoice.swift` lists over time.
- **Mixed setups are the expected first use.** Connector-heavy slots
  (briefing, consolidation) stay on Claude Code. KB-internal slots (dreaming)
  and web-heavy ones (research) are the natural first moves to another
  runtime (§8.8).

### 8.8 Connectors

The hosted connectors are the biggest functional gap. They live on the
owner's Claude account (§6.7), and no other CLI can use them.

- **Detection per runtime.** `connector_detect` asks the slot's runtime for
  its MCP servers (`codex mcp list`, `agy`'s `.agents/mcp_config.json`, or the config files),
  and connector health records which runtime saw what.
- **Neutral keys.** Connector keys become runtime-neutral (`slack`, `gmail`,
  …), with aliases for `mcp:claude_ai_*` and `mcp:plugin_*`; the app already
  aliases `plugin_*` (`ConnectorKeyAlias.swift:21-22`). This is a contract
  change to `connectors.yaml` → `connectors.snapshot.json` → the app's copies,
  in one commit, in Phase 2.
- **Missing connectors degrade, they don't fail.** The phases already skip a
  connector that isn't there. The slot editor warns when a slot's
  `required_in_types` connectors aren't available on its chosen runtime.
- **Each provider is the owner's job, per runtime.** Scout documents how to add
  the standalone MCP servers (Slack, Linear, Google) to each CLI, but doesn't
  bundle or host them.
- **`restrict_tools`** applies on every runtime: by the hook where the
  runtime has `pre_tool_block`, else by the runtime's native per-server tool
  allowlist, else the runtime is refused for that vault (§8.3 rule 1).
- **Connector drafting** (`connector_draft.py`) stays Claude-only in v1. It is
  owner tooling, not a run.

### 8.9 Auth, install, doctor and update

- **`auth_status()` per adapter.** The doctor's runner-binary and auth checks
  (`bootstrap_doctor.py:369-430`) loop over the runtimes the schedule uses.
  Findings name that runtime's own fix command.
- **Headless login is per runtime.** Claude keeps `scoutctl auth
  setup-token`. For the others, the doctor checks that their login persists
  under launchd, which has no keychain prompt and a different environment.
  This is a Phase 0 check for each CLI.
- **The engine reaches non-Claude users without the Claude marketplace.**
  - The app-managed engine (#312, #319) and `uv tool install
    "scoutctl @ git+…@stable#subdirectory=plugin/engine"` (#550 §6.5) already
    work without Claude Code.
  - `install.sh` gains a path that skips the plugin registration when
    `claude` is absent and another supported runtime is present.
  - The headless updater (`self_update_run.py:299-315`) then updates the
    engine pointer instead of the plugin.
- **Bootstrap.** `--agent-runtime` (repeatable) records which runtimes are set
  up, runs `agent-runtime install` for each, and fills `CLAUDE_BIN` only when Claude
  Code is one of them. The dead `--skip-claude` flag is removed.

### 8.10 Skills, commands and instructions on other runtimes

- **Skills are portable already.** The five SKILL.md files carry only `name`
  and `description`, which §3 records is the format Codex and Antigravity read.
  The launch skills only run `nohup bash ~/Scout/run-*.sh`, and the runner
  picks the runtime.
- **Commands are generated, not hand-forked.** `plugin/commands/*.md` stays
  the source. A generator writes each runtime's form under
  `plugin/runtimes/<id>/`:
  - Codex and Antigravity both read SKILL.md skills from `.agents/skills`
    (§3.1, §3.2), so one generated skills tree serves both;
  - Codex plugins and Antigravity plugins carry the commands;
  - Gemini CLI, if wanted, gets an extension with `commands/*.toml` and
    `GEMINI.md`.

  It maps `$ARGUMENTS` and replaces `$CLAUDE_PLUGIN_ROOT` with the engine
  pointer (`scoutctl` on PATH). A test fails when a generated file drifts.
  `scout-update` and `scout-connect` are runtime-specific, and each runtime
  gets its own.
- **How they get there and stay current.**
  - `scoutctl agent-runtime install <id>` does three things:
    - writes the gate config (above);
    - links the generated tree into the runtime's user location (for
      example `~/.agents/skills/scout-*` → the engine checkout's
      `plugin/runtimes/<id>/skills/…`, resolved through the engine pointer);
    - registers the runtime in the vault's config.
  - The links point into the engine checkout, so an engine update (the
    app-managed engine, or `self-update` moving the engine pointer) updates
    the skills with no second step.
  - `install --check` (doctor, and `check` before a run) re-links a missing
    or stale link and reports a Scout-owned file someone edited. Edited
    files are parked like any managed file.
  - The headless updater's Claude-specific steps
    (`self_update_run.py:299-315`) run only when Claude Code is one of the
    vault's runtimes.
- **The vault's instructions file.** A run is told to read `SKILL.md`, so
  runtimes that auto-load `AGENTS.md` / `GEMINI.md` need no extra file for
  runs. For interactive use in the vault, `agent-runtime install` also writes a short
  managed `AGENTS.md` / `GEMINI.md` pointer. It covers reading the KB through
  `scoutctl kb` and where the skills are, the same text #550 V3 writes for
  delegates. It is a managed file, so `/scout-update`'s keep/merge/park rules
  apply.
- **Tool names in phases.** Phase text that names a Claude tool is rewritten
  to the action ("open the note to edit it", "search the web"). Where a tool
  name is load-bearing (`ToolSearch`, `phases/core/00-run-modes.md:42`), it
  moves into a short per-runtime glossary the runner prepends. Behavioural
  rules don't change, so the eval-seed rule (plugin/CLAUDE.md) is about
  re-running the existing seeds on each runtime, not new ones.
- **Interactive sessions and gates.** The owner's interactive session is
  `owner`, which today's read gate doesn't restrict. `outbound-gate` warns
  the owner, and `mcp-tool-gate` applies to every session. So interactive use
  on a runtime needs:
  - `pre_tool_block` if the vault uses `restrict_tools`;
  - provider consent (§8.4).

  `scout-ask` and `scout-work` call `scoutctl agent-runtime check
  --interactive` first, and say why when they refuse.

### 8.11 The macOS app

- **Onboarding.** "Claude Code installed" becomes "at least one supported
  agent runtime installed and signed in", from `scoutctl agent-runtime list
  --json`. Claude Code stays first and recommended.
- **Engine install.** When `claude` is absent, the app installs the
  app-managed engine (#312/#319) and skips "Register with Claude Code".
- **Slot editor.** A runtime picker next to the model picker. Model options
  come from `agent-runtime models`, and refused runtimes are shown with the
  reason (not consented, Tier D, version unverified).
- **Sessions page, Tools/Files tabs and the Usage card** read
  `scoutctl session index`, which adds each adapter's `session_store()`.
  Model prefixes stop being Claude-only.
- **Launch menus** ("Launch Claude Code", "Ask Claude") get one entry per
  installed runtime, in a later phase.

### 8.12 Hosted platforms: Grok Bot and Claude Duet

Covered by #550, which this spec doesn't duplicate:

- the `delegate:<id>` reader (V1, built #625);
- the per-delegate mirror (V2);
- `scoutctl delegate init` writing `AGENTS.md`/`CLAUDE.md` (V3);
- the inbox (V4);
- an MCP server only if needed (V5).

This spec adds two things:

- these platforms are Tier D by construction, so the same capability table
  describes them;
- their marketing rule (§13).

If "Grokbot" turns out to mean the Grok Build CLI (Q1), it becomes an agent
runtime in Phase 4 instead.

## 9. Error handling and failure modes

| Event | Effect | Handling |
|---|---|---|
| The slot's runtime isn't installed or isn't signed in | `check` refuses | Run-health `agent_runtime.refused` with the fix command; no fallback to Claude |
| A CLI update changes the hook payload or drops a hook | Conformance no longer proven for that version | Rule 4 refuses versions outside the verified range; the doctor says "update Scout or pin the CLI" |
| A hook process crashes on a non-Claude runtime | Depends on the runtime | Tier F requires fail-closed, proven by test; otherwise not Tier F |
| A run edits or deletes the gate config | Gates off for later calls | Config outside the vault; writes to it denied; `install --check` at the next run's `check` refuses on drift |
| A plan's quota runs out | That runtime's slots back off until `resets_at` | Per-runtime back-off (§8.6); other runtimes continue |
| A connector is missing on the slot's runtime | Phase skips it; connector health records it per runtime | Slot editor warning (§8.8) |
| Output stream is unparseable | No usage, no telemetry | Row with `cost_source: unpriced`, event `agent_runtime.unparsed`; the run itself stands |

## 10. Acceptance, per kind of target

- **Every local runtime (Tier F):**
  - its conformance suite passes against a pinned CLI version;
  - `eval.sh kb-gate --agent-runtime <id>` passes on Jordan's login for that
    runtime, with Jordan's OK for that run;
  - the privacy scenario passes, scored by `privacy_hygiene`;
  - a week of one real slot on Jordan's vault (dreaming suggested), with run
    health green and no `agent_runtime.refused` or gate-error events.
- **Hosted platforms:** #550's own "done when" lines for V2–V4, plus V6 live
  on Jordan's delegate.

## 11. Testing

### 11.1 Deterministic (CI, free)

- **Golden argv:** `claude-code` `exec` argv equals today's runner line, for
  all three runners.
- **Gate translation:** every adapter's `hook_in`/`hook_out` round-trips
  recorded payload fixtures (anonymised) through the same `decide()` cases
  the Claude tests use. A table test asserts the same verdicts across
  runtimes.
- **Unknown tools:** a non-Claude adapter denies an unclassed tool in a run.
- **Generated files:** `install --check` and the command generator are
  drift-tested.
- **Model and runtime validation, precedence and refusal-rule matrix:**
  tier × reader × vault contents × consent × version.
- **Ledgers:** old rows without the new keys still parse
  (`health._parse_row`).

### 11.2 Conformance against the real CLI, with a fake model (local; CI where the CLI installs freely)

Where the CLI accepts a custom endpoint (§3), a small local server speaking
that vendor's API returns a *scripted* turn. The turn calls the CLI's shell
tool to `cat knowledge-base/<canary>.md`, then its read tool on it, then
writes to the gate config, then detaches. The suite asserts:

- each call reached Scout's hook with the right `GatePayload`;
- each was denied;
- a hook that exits abnormally blocks the call;
- the environment reached the hook and a shell child;
- `ps` shows the markers.

No model runs and nothing is spent. The same probe runs in two places:

- **In CI and the release branch:** it records `(runtime, version,
  capabilities)` in `plugin/engine/scout/agent_runtimes/verified.json`.
  That file is generated by the suite and never hand-edited.
- **On the owner's Mac,** whenever an installed CLI version or gate config
  is new: the result is cached in `.scout-state/agent-runtime-probes.json`
  (§8.3 rule 4).

A runtime's verified capabilities are the local probe's result where one
exists, else `verified.json`.

- **If a CLI can't be pointed at a fake model,** its conformance runs only as
  a model-backed eval on Jordan's login, with his per-run OK. Its tier is
  capped at what that run proved.
  - This applies to Antigravity, which has no custom endpoint (§3.2).
  - Re-proving it on every CLI update costs a model run each time, so for
    Antigravity rule 4 (§8.3) pins a version range more tightly.
- **Endpoints, per §3:**
  - Claude Code: `ANTHROPIC_BASE_URL`.
  - Codex: `model_providers.<id>.base_url`, Responses API only.
  - Gemini CLI: `GOOGLE_GEMINI_BASE_URL`, secondary evidence only.
  - Grok Build: custom models in `~/.grok/config.toml`, keys unverified.
  - Antigravity: none.

### 11.3 Model-backed (local only, Jordan's login, per-run OK)

- `kb-gate` per runtime;
- the privacy scenario per runtime;
- `compare-models` generalised to `compare-runtimes` for quality A/B on one
  scenario before moving a slot.

The eval shims follow `claude_shim.py`'s contract per runtime: isolated
config, stub MCP servers only, a sandboxed shell, and an init guard.

## 12. Phases

Each phase is one PR or more, and starts on Jordan's go. Details, file lists
and per-PR checks are in the plan.

| Phase | Ships | Done when | Effort (agent-days) |
|---|---|---|---|
| **0 Answers and facts** | Jordan answers §14. A throwaway spike per CLI checks the §3 **[unknown]**s that change the design: hooks under headless mode, fail-closed behaviour, env passthrough, custom endpoint, launchd login, and each vendor's consumer data terms. Spec updated | §3 has no unknown that changes a tier | 1–2 (no merge but the spec update) |
| **1a Bare-mode guard (urgent, Claude Code only)** | **Prevention before launch,** because a bare session fires no hook that could detect it from inside. A free pre-launch probe runs `claude` with the run's own flags against a local fake Anthropic endpoint (`ANTHROPIC_BASE_URL`). A scripted tool call reads a fixture KB file, and the probe passes only if Scout's kb-read-gate denied it. Cached by Claude version + `hooks.json` hash + flags. The runners skip the run (`skipped_gates`) on a failed or unrunnable probe; health and doctor report `gates.not_loaded`. If Claude Code later ships an opt-out flag, the runners pass it too | Unit tests; the manual probe passes with today's flags and fails with `--bare` added | 2–3 |
| **1b Claude Write/Edit hardening (separate privacy fix)** | `hooks.json` and `decide()` deny a run's Write/Edit into `.git/hooks`, `.git/config` and agent-config paths in the vault (§8.5) | Unit tests on `decide()`; the hooks.json prefilter covers Write/Edit only for those paths, so the cost per call stays low | 1–2 |
| **1 Interface, Claude Code behind it** | `agent_runtimes` package, `claude_code` adapter, `agent-runtime` CLI, runner plumbing, refusal rule with only Claude registered, ledger keys, normalised events (eval metrics and Stop-hook accounting onto them), `verified.json` for Claude, doctor loop, vestigial-variable cleanup | Golden argv identical; `hooks.json` byte-identical; full plugin CI green; one week of Jordan's live runs unchanged (run health green); `kb-gate` passes on Claude through the new path | 6–9 |
| **2 Codex CLI** | `codex` adapter; gate translation + generated config; fake-model conformance; provider consent; per-runtime models; neutral connector keys (contract change); eval shim; app runtime picker and onboarding; generated Codex commands/skills | Conformance at Tier F (or L) for a pinned version; `kb-gate` + privacy scenario pass on Codex; one slot live for a week | 8–12 |
| **3 Google CLI** | `antigravity` adapter (or `gemini`, per Q5); skills/plugin packaging; conformance by model-backed runs only for Antigravity; eval shim | Same bar as Codex | 5–8 (+2–3 for a second, Gemini CLI adapter) |
| **4 Grok** | If Grok Build CLI: adapter + conformance. Its hooks fail open (§3.3), so it starts Tier D and reaches Tier S only if the Tier S work is approved. If Grok Bot: #550 V2–V4 (mirror, bootstrap, inbox) for a Grok delegate | CLI: same bar as Codex at whatever tier it proves. Bot: #550 V2–V4 "done when" + V6 | CLI 4–8, plus Tier S (MCP server, path-deny profiles) 8–12 if wanted; Bot 8–12 (shared with Duet) |
| **5 Claude Duet** | #550 path: mirror via the GitHub connector first, V5 MCP only if needed | #550 V2 + Duet answers a scout-ask question from its mirror; V6 live | 3–6 after Phase 4's mirror work (V5 adds 4–6) |
| **6 Marketing** | Copy changes per §13, one PR per claim change, each citing its evidence row | Claims match `docs/runtimes.md` | 1–2 per step |

Phase 1a can start today and doesn't wait for this spec's approval of the
rest. Phase 1 doesn't depend on Q1/Q2. Phases 2 and 3 are independent of
each other once Phase 1 lands. Phase 4/5 and the #550 track can run in
parallel with them.

**Total, recommended path** (1a, 1, Codex, Antigravity, and the hosted
platforms via #550 V2–V4): about 35–50 agent-days over 20–30 PRs, plus
Jordan's review time and the per-runtime live weeks.

## 13. What the repo and site may claim, phase by phase

**The evidence table.** `docs/runtimes.md` (new in Phase 1; not marketing copy)
lists for each runtime:

- the CLI version tested;
- the tier;
- its conformance result;
- its `kb-gate` result and date;
- its privacy-scenario result and date;
- the subscriptions it needs;
- the Scout release that shipped it.

Every claim below must point at a row.

| State | What may be said, and where |
|---|---|
| Nothing shipped (now, and through Phase 1) | No new claims. The site and README keep "for Claude Code". A "Roadmap" line saying "planned: Codex CLI, Google's Antigravity CLI" is allowed only if Jordan wants it (Q8), and must say *planned* |
| Adapter merged on `main`, not released | Nothing outside `docs/runtimes.md` and the CHANGELOG's Unreleased section |
| Released, conformance green, evals not yet passed | "Preview" in `plugin/README.md`'s runtime section and `docs/runtimes.md` only. No site hero, no root README pitch |
| Released + conformance + `kb-gate` + privacy scenario passed on that runtime | **"Supported"**: site, README and app onboarding may say "Runs on Claude Code, Codex CLI …". Each wording names what is supported (scheduled runs, interactive skills, or both) and the tier, if it isn't F |
| Tier D only (a CLI without proven gates) | "Works with X for read-only questions over a filtered copy of your notes." Never "runs Scout" |
| Hosted platforms after #550 V3–V4 ship and V6 is live | "Your Grok Bot / Claude Duet agent can read your Scout notes (normal notes only) and propose updates." Never "runs your briefings" |

Never, at any phase:

- "the same privacy protections" for a runtime below Tier F;
- a runtime name in the site hero before its row says Supported;
- a vendor's logo without that vendor's brand guidelines being checked;
- any wording that implies an affiliation with or endorsement by a vendor.

## 14. Open questions for Jordan

1. **"Grokbot":** xAI's hosted **Grok Bot** (the reading in your #550 spec,
   where you wrote "grokbot (xAI's Grok Bot)"), or xAI's **Grok Build** CLI
   (§3.3)? Or both: the Bot as a delegate, the CLI as a runtime?
2. **"Claude Duet":** Anthropic's hosted **Claude Duet** (your #550 reading),
   the `claude-duet` npm package (shared Claude Code sessions: nothing to
   port, since it already runs Claude Code), or `duet-cli` (Claude and Codex
   taking turns: covered once Codex is)? §3.4 has the evidence. Recommended:
   the hosted product, via #550.
3. **Approach:** A (engine adapters, shell runners exec through `scoutctl`), as
   recommended in §7?
4. **Providers (§8.4):** opt-in per provider with `[anthropic]` as the
   default? And should local-only paths be excluded from non-Anthropic runs?
5. **Google's CLI:** your ask says Gemini CLI, but Google moved consumer
   plans to **Antigravity CLI** (§3.2). Gemini CLI now needs a paid key or a
   Code Assist licence. Target Antigravity (recommended), Gemini CLI, or
   both? And the order: Codex first (as in this spec), or Google first?
   Which subscriptions do you already hold (§3.6), and which account may
   live tests use?
6. **Budget on subscription runtimes (§8.6):** timeout plus an optional daily
   run/token ceiling, off by default, or on by default with which numbers?
7. **Connectors (§8.8):** is "connector-heavy slots stay on Claude Code" the
   right first use, or do you want Slack/Gmail/Linear on Codex/Antigravity early
   (standalone MCP servers you add to each CLI)?
8. **Marketing before shipping (§13):** may the site carry a "planned" line
   now, or nothing until something is Supported?
9. **Interactive scope:** which commands must work on every runtime in the
   first release (proposal: `scout-ask`, `scout-work`, `scout-status` and the
   launch skills; `scout-update`/`scout-connect` per runtime later)?
10. **Tier S (§8.3):** worth building, so that a runtime with fail-open hooks
    but path-deny rules (Grok Build) can run scheduled slots? Or is such a
    runtime delegate-only until its vendor fixes the hooks?
11. **CLIs Scout can't probe for free (Antigravity, §8.3 rule 4):** each new
    CLI version is refused until a model-backed run on your login re-proves
    it. Accept that, ask users to pin the CLI version, or skip such
    runtimes for scheduled runs?

## 15. Calls made in this spec (check while reading)

- Two kinds of target: local agent runtimes vs hosted delegate platforms
  (§2, §8.1).
- New key `agent_runtime`, because `Slot.runtime` is taken (§8.1, §8.7).
- Approach A; the wrapper keeps the name `claude-with-retry.sh` for liveness
  (§7, §8.2).
- Capabilities are *verified* by a conformance suite, per CLI version, and
  unverified means absent (§8.3, §11.2).
- Tier L denies raw KB reads in favour of `scoutctl kb read`: stricter than
  Claude, never looser (§8.3).
- No fallback from a refused runtime to Claude Code (§8.3).
- Provider consent, defaulting to Anthropic only (§8.4).
- No price catalogue for other vendors; `cost_source: unpriced` plus run
  timeouts (§8.6).
- `agent_runtime` stays out of the schedule snapshot for now (§8.7).
- Neutral connector keys in Phase 2, a contract change (§8.8).
- Connector drafting stays Claude-only in v1 (§8.8).
- Commands are generated per runtime from `plugin/commands/` (§8.10).
- `docs/runtimes.md` is the evidence table every claim cites (§13).
- Google's target is Antigravity CLI unless Jordan says Gemini CLI (§3.2,
  Q5).
- The Claude bare-mode guard ships first, as Phase 1a, ahead of the rest
  (§3.5).
- Tier S exists on paper only, until a wanted runtime needs it (§8.3, Q10).
- Every hook reply is well-formed, never a crash meaning "block" (§8.5).
