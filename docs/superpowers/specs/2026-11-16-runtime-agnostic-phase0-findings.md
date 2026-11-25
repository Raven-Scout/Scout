# Runtime-agnostic Scout, Phase 0: findings

**Date:** 2026-11-16. **Spec:** `2026-11-13-runtime-agnostic-design.md`
("spec §n"). **Plan:** `../plans/2026-11-13-runtime-agnostic.md`, "Phase 0".

Phase 0 is a set of throwaway spikes. Nothing here is product code. Every
experiment ran in a scratch directory with a throwaway `HOME`, a fixture vault
holding one canary note, and a local fake model. Each CLI ran inside a
loopback-only `sandbox-exec` profile, the same one #635's gate probe uses, so
no request could leave the machine.

Evidence labels: **[run]** means observed in a local run on 2026-11-16;
**[docs]** means a vendor page; **[src]** means vendor source on GitHub `main`;
**[cl]** means agy's own `agy changelog`; **[pending]** means it needs a
real-login run that Jordan hasn't OK'd yet.

Versions tested: Antigravity CLI `agy` **1.5.9**, Codex CLI **0.169.0**,
Gemini CLI **0.68.0**.

## 0. The headline

1. **Antigravity *can* be probed against a fake model, for free.** Run `agy`
   with `--settings '{"modelProvider":"gemini"}'`, `GEMINI_API_KEY=<dummy>` and
   `GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:<port>`. It then sends every model
   request to the local fake, and runs its real hooks, tools and permission
   engine against scripted tool calls **[run]**.
   - Spec §3.2 says "Custom endpoint: **None**", and §8.3 rule 4 and §11.2
     are built on that. Both change; see §1.9.
2. **Antigravity's PreToolUse hook is close to fail-closed.**
   - A crash, a non-zero exit, garbage output, a kill and a timeout each
     *deny* the call.
   - The one hole: **empty stdout with exit 0 allows** the call **[run]**.
   - With the read-tool caveat in §1.3, that makes Tier L reachable.
3. **Codex and Gemini CLI hooks fail open.**
   - Codex blocks only on a well-formed deny or exit 2 with stderr text.
     Everything else allows the call, timeout included **[run]**.
   - Gemini CLI blocks on a deny, and on exit ≥ 2 with output. Exit 1, empty
     output, a kill and a timeout all allow **[run]**.
   - Both still reach Tier F only if Scout's hook *never* fails. The spec
     already asks for "hook replies always well-formed" (§8.5). These runs show
     that rule is load-bearing, not belt and braces.
4. **Codex silently skips untrusted user hooks.**
   - Without `--dangerously-bypass-hook-trust`, or a trusted or managed hook,
     the canary read went through. No hook ran and nothing was printed
     **[run]**.
5. **No CLI's own OS sandbox works inside Scout's sandbox (#642).**
   - agy `--sandbox` and Codex's Seatbelt both fail with `sandbox-exec:
     sandbox_apply: Operation not permitted` (exit 71) **[run]**.
   - So a run under Scout's sandbox must switch the CLI's own sandbox off:
     agy without `--sandbox`, Codex with `-s danger-full-access`.
   - Scout's outer sandbox is then the only OS boundary, which is what #642
     designed.
6. **`agy` updates itself in place.**
   - Running `agy` 1.0.8 once (`agy models`, throwaway `HOME`) let its
     background updater replace the binary in `~/.local/bin` with 1.5.9
     **[run]**.
   - No opt-out is documented **[cl]**.
   - A scheduled agy run can therefore change version between two runs, or
     during one. §1.9 covers what that means for the probe.

## 1. Spike 1: Antigravity CLI (`agy` 1.5.9)

### 1.1 Headless

- `agy -p "<prompt>"`.
  - `--output-format text|json|stream-json`, `--input-format stream-json`.
  - `--json-schema`, `--model`, `--effort low…max`, `--agent`.
  - `--mode accept-edits|auto|plan`.
  - `--print-timeout` (default 0 = wait for the turn).
  - `--dangerously-skip-permissions`, `--sandbox`.
  - `--settings <json|file>`: settings for this session only, never saved.
  - `--disable-slash-commands`.
  - All of these are in `agy --help` **[run]**.
- stream-json events **[run]**:
  - `init` carries `cwd`, `tools` (the display names) and `permission_mode`.
  - `step_update` carries `step_type` (`user_input`, `agent_response`,
    `tool`, …), `tool_name`, `tool_info{name,parameters,output|error}` and a
    per-step `usage`.
  - `result` carries `status`, `response`, `num_turns`, `usage{input_tokens,
    output_tokens, thinking_tokens, cache_read_tokens, total_tokens}` and
    `denied_actions[]`.
- Exit codes:
  - 0 on success, and **also 0 when a tool call is denied** **[run]**.
  - 3 plus an `AGY_ERROR: {json}` line on stderr for a model or agent failure,
    and for a `--print-timeout` cut **[cl 1.2.6, 1.5.0]**.
  - 1 for an unknown `--agent` **[cl 1.3.4]**.
  - `agy models` without a login prints an error and still exits 0 **[run]**.
- **Approvals in headless.**
  - In the default `request-review` mode, a hook `allow` still needs a human
    approval, which `-p` can't give. The call is soft-denied and the run exits
    0 with `denied_actions` **[run]**.
  - `--mode auto` did the same for a shell read **[run]**.
  - A hook reply of `{"decision":"auto_approve"}` runs the call with no
    approval **[run]**.
  - So **Scout's hook can be the single gate without
    `--dangerously-skip-permissions`**: `auto_approve` what the gate allows,
    `deny` the rest.

### 1.2 Hooks: config, stdin, reply

- **Config.**
  - User: `~/.gemini/config/hooks.json`.
  - Project: `<workspace>/.agents/hooks.json`, loaded only for a trusted
    folder **[cl 1.1.1]**.
  - Plugin: a plugin's `hooks.json` **[docs]**.
  - Schema: `{"<hook-name>": {"enabled": true, "PreToolUse": [{"matcher":
    "<regex>", "hooks": [{"type":"command","command":…,"timeout":<s>}]}]}}`.
  - User hooks loaded under `-p` with no trust step **[run]**.
- **Stdin (PreToolUse) [run]:**

  ```json
  {"hookEventName":"PreToolUse","conversationId":"…","cwd":"<workspace>",
   "workspacePaths":["<workspace>"],"lastUserInput":"…","modelName":"…","stepIdx":2,
   "toolCall":{"id":"…","name":"run_command","args":{"CommandLine":"cat knowledge-base/probe.md","WaitMsBeforeAsync":5000,"toolSummary":"…"}},
   "transcriptPath":"~/.gemini/antigravity-cli/brain/<conv>/.system_generated/logs/transcript_full.jsonl",
   "artifactDirectoryPath":"~/.gemini/antigravity-cli/brain/<conv>"}
  ```

- **The hook's own cwd is the config dir** (`~/.gemini/config`), not the
  workspace **[run]**. The adapter's `hook_in` must take `cwd` from the
  payload.
- **Reply [run]:**
  - `{"decision":"deny","reason":"…"}` blocks. The reason reaches the model as
    `tool call denied by pre-tool hook: <reason>`.
  - `{"decision":"allow"}` defers to the permission engine.
  - `{"decision":"auto_approve"}` runs the call.
  - **`{}` (no decision) is a deny.**
- **Env [run]:**
  - The hook inherits the CLI's environment: `SCOUT_MODE`, `SCOUT_READER` and
    the dummy key all reached it.
  - A `run_command` child inherits it too (`SCOUT_MODE=autonomous` printed).
  - The child also gets `ANTIGRAVITY_CSRF_TOKEN`, `ANTIGRAVITY_LS_ADDRESS` and
    `ANTIGRAVITY_AGENTAPI_EXE`: the address and token of agy's own local
    server. See the risks in §1.8.

### 1.3 Tool names

- The names the **model and the hook** see differ from the display names in
  `init` and in the docs **[run]**.
- The model's declared tools (gemini-provider path) were `read_file`,
  `run_command`, `write_file`, `edit_file`, `search_web`, `invoke_subagent`,
  `define_subagent`, `manage_subagents`, `run_workflow`, `swarm`,
  `manage_task`, `schedule`, `send_message` and `ask_question`.
- `init.tools` lists 40-odd names, among them `view_file`, `write_to_file`,
  `grep_search`, `list_dir`, `find_by_name`, `browser_*` and `call_mcp_tool`.
- The hook saw `toolCall.name` = `run_command` (args `CommandLine`) and
  `read_file` (args `Path`) **[run]**.
- **For Scout:**
  - The tool-class allowlist (spec §8.5) must be built from the *hook-side*
    names. Both lists are recorded per version by the conformance suite.
  - Anything unclassed is denied in a run.
  - `run_workflow`, `swarm` and `invoke_subagent` start further agents. They
    are denied in a run until a test shows their tool calls reach the same
    hook.
- `post_tool_context`:
  - The PostToolUse reply is `{}` only, and a PostToolUse failure is logged
    but doesn't affect the tool **[docs, cl 1.4.3]**.
  - No path to inject a label after a read was found.
  - So Antigravity is **Tier L at best**: deny a run's raw `read_file` of a KB
    note, in favour of `scoutctl kb read`.

### 1.4 Failure modes (the `pre_tool_fail_closed` question)

Fake model, `--dangerously-skip-permissions` (so that "needs approval" can't
masquerade as a gate deny), a scripted `run_command` that `cat`s the canary
**[run]**:

| Hook does | agy |
|---|---|
| prints `{"decision":"deny"}` | **blocked** |
| prints `{}` | **blocked** (empty decision = deny) |
| exits 2, 1, 3 or 127, with or without stderr | **blocked** ("JSON hook …" error) |
| prints garbage | **blocked** (protojson unmarshal error) |
| prints `allow` and exits 1 | **blocked** |
| killed by a signal | **blocked** |
| hangs past its `timeout` (5 s) | **blocked** |
| prints nothing and exits 0 | **allowed** |
| prints `allow` / `auto_approve` | allowed (control) |

- **Rule for the adapter:** `hook_out` always prints a decision, never empty
  output with exit 0.
- The conformance suite keeps the "empty output allows" row as a pinned fact,
  so a release that changes it in either direction gets noticed.

### 1.5 Sandbox

- **agy's own `--sandbox` under Scout's `sandbox-exec`.** Every command fails
  with `sandbox_apply: Operation not permitted`, exit 71 **[run]**. That is
  fail-closed but useless, so Scout runs agy without `--sandbox`.
- Without `--sandbox`, agy's file and shell tools ran normally inside Scout's
  loopback-only profile **[run]**.
- **Not yet run:** agy under the real #642 profile
  (`scout.sandbox`, built from `hook_paths.protected_specs()`).
  - Expected issues: agy writes `~/.gemini/antigravity-cli/{conversations,brain,log}`.
  - `.gemini` is a protected root in `hook_paths`, so the profile needs a
    write carve-out for agy's data dir and none for `~/.gemini/config`, where
    the hooks live.
  - The self-updater writes `~/.local/bin/agy`.
  - This is Phase 2's first task, and it needs no model.

### 1.6 Sessions and transcripts

- One SQLite file per conversation, `~/.gemini/antigravity-cli/conversations/<id>.db`.
  Its tables are `steps`, `trajectory_meta`, `trajectory_metadata_blob`,
  `gen_metadata`, `executor_metadata`, `parent_references` and
  `battle_mode_infos` **[run]**.
- A JSONL transcript, `~/.gemini/antigravity-cli/brain/<id>/.system_generated/logs/transcript.jsonl`
  and `transcript_full.jsonl`, with per-step output under `steps/<n>/` **[run]**.
  - Rows have the shape `{step_index, source, type, status, created_at,
    content}`.
  - The hook payload's `transcriptPath` points at `transcript_full.jsonl`.
- **For Scout's session reader (spec §6.5):** read the JSONL, not the SQLite.
  The JSONL is the documented hook contract; the SQLite schema is private.
- Log: `~/.gemini/antigravity-cli/cli.log`.

### 1.7 Auth on an AI Pro login

- Sign-in is Google OAuth from an interactive `agy`. The token lives in the
  macOS Keychain, with a file fallback if the Keychain write fails
  **[cl 1.5.9]**.
- **A throwaway `HOME` doesn't see the owner's login.** `agy models` under a
  fresh `HOME` said "Please sign in" **[run]**.
  - So the token store is keyed by `HOME`, or the Keychain item is.
  - For Scout this is good: `HOME` isolation also isolates credentials.
  - It also means a spike or eval shim on the owner's login must sign in once
    inside its own `HOME`, or reuse the real one.
- **Under launchd:** **[pending]**. Jordan's ground rules forbid loading a
  launchd job in this spike.
  - Risk: a Keychain prompt with no GUI session.
  - Phase 2 tests it with a one-shot `launchctl` job Jordan starts himself.
- **Quota:**
  - Pro has a 5-hour window plus a weekly cap. No numbers are published
    **[docs]**.
  - An individual-quota hit is reported at once, not retried **[cl 1.3.3]**.
  - The exact headless text is **[pending]**.
  - `agy -p "/usage"` prints quota and reset times without a model turn
    **[cl 1.1.12, 1.2.15]**. That is the source `classify_failure` and the
    doctor should read.
- **Data terms (spec §8.4 consent prompt):**
  - Google collects "Interactions" to evaluate and improve its products and ML
    technologies.
  - Employees and contractors may review them.
  - No retention period is stated. Telemetry off doesn't clearly stop review
    **[docs: antigravity.google/terms]**.

### 1.8 Risks

- **The local server is in reach of the shell.**
  - A run's shell sees agy's local server address and its CSRF token
    (`ANTIGRAVITY_LS_ADDRESS`, `ANTIGRAVITY_CSRF_TOKEN`).
  - If that API can start tool calls, or change settings, without the hooks,
    it is a bypass.
  - Phase 2 must either show it can't, or deny shell commands that reference
    those variables, as the read gate already does for `env -i`.
- **Self-update** changes the hooked binary under a pinned verification; see
  §1.9.
- **The gemini-provider path is not the OAuth path.** The fake-model probe
  proves the harness under `modelProvider: "gemini"`. agy once failed to load
  hooks for one auth mode only (ADC, fixed in **[cl 1.4.7]**). So hook loading
  on the owner's real login needs its own proof; see §1.9.
- **`run_workflow` and swarms run sub-agents** in other conversations. Until a
  test shows their tools hit the same hook, deny them in runs.

### 1.9 Recommended adapter shape for Phase 2

- **Argv:**

  ```
  agy -p "$PROMPT" --output-format stream-json --model "$MODEL"
      --print-timeout <run_timeout> --settings '<generated>'
  ```

  - No `--sandbox`; Scout's sandbox wraps the process.
  - No `--dangerously-skip-permissions`. The hook returns `auto_approve` or
    `deny`, and the generated `--settings` sets `permissions.deny` for the gate
    config paths as a second layer.
- **Gate config:**
  - `agent-runtime install` writes a managed block into
    `~/.gemini/config/hooks.json`, with one PreToolUse entry, `matcher: ""`
    (every tool), calling `python -m scout.hooks … --agent-runtime antigravity`.
  - `hook_out` prints `{"decision":"deny","reason":"[scout:…] …"}` on any
    internal error, and never prints empty output.
- **Probe (changes spec §8.3 rule 4 and §11.2):**
  1. **Free and automatic, per new agy version:** the fake-model probe, as
     Claude's.
     - `--settings '{"modelProvider":"gemini"}'`, a dummy key, a loopback
       fake, the canary `run_command` and `read_file`, plus the failure-mode
       rows above.
     - Seconds, no quota.
     - It covers every self-update with no model run.
  2. **Real login, rare:** the canary on the owner's own AI Pro login.
     - It runs once per install, and again when the hook code or the auth mode
       changes, not per version.
     - It proves the OAuth path loads the same hooks.
     - Q11's "tiny scripted model call" stays, at a lower frequency; it no
       longer has to run on every version.
  - **Decision needed from Jordan:** whether this narrower Q11 is acceptable.
    It is cheaper and more deterministic than the spec's version.
- **Tier:** L, with the hook-side tool allowlist from §1.3.

## 2. Spike 2: Codex CLI (0.169.0) and Gemini CLI (0.68.0)

No real model calls. Both ran against the same local fake, in the same
loopback-only profile **[run]**.

### 2.1 Codex CLI

- **Fake endpoint works.** Pass `-c model_provider=fake -c
  'model_providers.fake={name="fake",base_url="http://127.0.0.1:<port>/v1",wire_api="responses",env_key="FAKE_KEY"}'`
  with a dummy `FAKE_KEY`. Codex sends `POST /v1/responses`, streaming.
  - The fake answers with `response.created` →
    `response.output_item.done{function_call}` → `response.completed{usage}`,
    and Codex runs the call **[run]**.
- **Tools (custom provider):**
  - Offered: `exec_command{cmd}`, `write_stdin`, `view_image`,
    `request_user_input`, `multi_agent_v1`, goal tools and `web_search`.
  - **No `apply_patch` was offered** for an unknown model, even under a known
    model slug. Every file write in that setup is a shell call.
  - The `apply_patch` hook payload is from the docs only **[docs]**.
- **Hook stdin [run]:**
  - `{session_id, turn_id, transcript_path, cwd, hook_event_name:"PreToolUse",
    model, permission_mode, tool_name:"Bash", tool_input:{command}, tool_use_id}`.
  - That is Claude Code's shape: `tool_name` is `Bash` for `exec_command`.
  - `kb_read_gate.decide()` takes it nearly unchanged.
- **Hooks under `codex exec`: yes**, but only trusted ones.
  - An untrusted `~/.codex/hooks.json` is **skipped silently** **[run]**.
  - `--dangerously-bypass-hook-trust` makes it run.
  - `--ignore-user-config` does *not* drop `hooks.json` **[run]**.
- **Failure modes [run]:**

  | Hook does | Codex |
  |---|---|
  | `hookSpecificOutput.permissionDecision: "deny"` | blocked |
  | exit 2 with stderr | blocked |
  | Gemini-style `{"decision":"deny"}` | **allowed** (not a Codex shape) |
  | exit 1, 3 or 127, garbage, empty, killed | **allowed** |
  | hang past `timeout` | **allowed** (after the timeout) |

- **Env:** `SCOUT_*` reached both the hook and the shell child **[run]**. The
  default `shell_environment_policy` inherits everything **[src]**.
- **Sandbox:**
  - Codex's own Seatbelt can't nest in Scout's (exit 71, `sandbox_apply`)
    **[run]**.
  - So the run argv needs `-s danger-full-access`, with `approval_policy=never`
    (exec's default **[src]**).
- **Transcript:** `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl`
  **[run]**. State lives in `$CODEX_HOME/*.sqlite`.
- **Usage:** `turn.completed.usage{input_tokens, cached_input_tokens,
  cache_write_input_tokens, output_tokens, reasoning_output_tokens}` **[run]**.
- **Data terms (consumer plans):** "Improve the model for everyone" is on by
  default for Free, Plus and Pro, and covers Codex tasks. Opt-out is in
  ChatGPT settings. Retention is unconfirmed **[docs, 2nd]**.
- **Adapter shape (Phase 3):**
  - Argv: `codex exec --json --skip-git-repo-check
    --dangerously-bypass-hook-trust -s danger-full-access -c
    developer_instructions=… "$PROMPT"`.
  - Managed hooks (`requirements.toml`) are the stronger choice if they install
    without root. Phase 3 checks.
  - `hook_out` only ever prints the `hookSpecificOutput` deny, or exits 2 with
    stderr. Every error path in the hook must take one of those two.
  - A watchdog denies before the hook `timeout`, as #637 does for Claude.
  - **Tier F is reachable only with that discipline.** The CLI gives no safety
    net, which is the opposite of agy.

### 2.2 Gemini CLI

- **Fake endpoint works.** `GOOGLE_GEMINI_BASE_URL` plus a dummy
  `GEMINI_API_KEY` send `POST /v1beta/models/<m>:streamGenerateContent?alt=sse`
  to the fake **[run]**.
  - It also sends one non-streaming `generateContent` call to a flash-lite
    model first: the model router.
- **Headless needs folder trust:** `--skip-trust`, or
  `GEMINI_CLI_TRUST_WORKSPACE=true`. Otherwise it exits 55 **[run]**.
  - Without trust, `--approval-mode yolo` is overridden to `default`.
- **Hooks fire under `-p` [run].**
  - Stdin: `{session_id, transcript_path, cwd, hook_event_name:"BeforeTool",
    timestamp, tool_name, tool_input}`.
  - Tool names: `run_shell_command{command}` and `read_file{file_path}` were
    seen; the rest are from the docs.
- **Env:**
  - The hook gets `GEMINI_PROJECT_DIR`, `GEMINI_SESSION_ID` and
    **`CLAUDE_PROJECT_DIR`**, plus the parent env, dummy key included
    **[run]**.
  - `SCOUT_*` reached the shell child **[run]**.
  - Scout's hooks must not take `CLAUDE_PROJECT_DIR` to mean "this is Claude
    Code".
- **Failure modes [run]:**

  | Hook does | Gemini CLI |
  |---|---|
  | `{"decision":"deny"}` | blocked |
  | exit 2 with stderr | blocked |
  | exit 127 with stderr (crash) | blocked (plain text + exit ≥ 2 = deny) |
  | exit 1 with stderr | **allowed** |
  | exit 3 with no output, empty output, garbage on stdout with exit 0, killed | **allowed** |
  | hangs past `timeout` (`exec sleep`) | **allowed** |
  | hangs with a grandchild holding the pipe | run hangs until the outer timeout |

- **Transcript:** `~/.gemini/tmp/<project>/chats/session-<ts>-<id>.jsonl`
  **[run]**.
- **Usage:** `result.stats{total_tokens, input_tokens, output_tokens, cached,
  models{…}}` **[run]**.
- **Auth:**
  - Consumer logins (AI Pro and Ultra, and the free Code Assist tier) stopped
    on 2026-06-18.
  - Paid API keys, Vertex, and Code Assist Standard or Enterprise still work
    **[2nd]**.
- **Data terms:** unpaid API use may train and be reviewed by people; paid API
  use isn't used for training and is logged for a limited period for abuse
  checks **[docs: ai.google.dev terms]**.
- **Adapter shape (Phase 4):**
  - Argv: `gemini -p … -o stream-json --approval-mode yolo --skip-trust`.
  - Hooks in a managed block of `~/.gemini/settings.json`.
    - That file is shared with agy's `~/.gemini` tree, so one installer owns
      both blocks.
  - `hook_out` always prints a JSON decision, with exit 0.
  - A watchdog denies before the timeout.
  - Tier F only with that discipline, as for Codex.

## 3. Spike 3: the daily token ceiling (spec §8.6, Q6)

**Source:**
- The maintainer's own run cost ledger, read-only and aggregates only.
- Only the 71 scheduled runs priced from the transcript (`cost_source:
  "transcript"`, runner rows) are used: 10 days, 2026-11-07 to 11-16, all on
  Opus.
- Earlier rows are $0 by the bug `run_cost.py` fixed, so they can't be used.
- The ledger has USD, not tokens. The `session-tokens` paths were off limits
  for this spike, so tokens are derived (below).

| Slot kind | Runs | USD/run p50 | p90 | max |
|---|---|---|---|---|
| research | 17 | 3.48 | 4.24 | 4.98 |
| dreaming | 25 | 5.05 | 5.97 | 6.89 |
| briefing | 9 | 4.14 | 6.95 | 7.27 |
| consolidation | 20 | 6.36 | 8.45 | 8.79 |

All runs together: $38.7 per day at p50, $57.3 at p90, $62.1 max.

**USD to tokens.**
- At Opus 5.5 list prices ($4 input, $20 output, $0.20 cache read, $5 cache
  write per MTok), a Claude Code session dominated by cache reads costs about
  $0.8 to $1.3 per million *processed* tokens (input, cache read, cache write
  and output together).
- The range covers cache-write shares of 6–10% and output shares of
  1.5–3%. So **K ≈ 1.0 M tokens per USD**, ±25%.
- The other CLIs report `total_tokens` roughly comparably (agy reports
  `cache_read_tokens` separately), so the ceiling is counted on their
  `total_tokens`.

**Proposed formula** (per runtime, per day):

```
ceiling(runtime) = max(FLOOR, H × Σ over the runtime's slots of runs_per_day(slot) × T(slot))
T(slot)   = p90 USD per run for that slot kind, over the last 14 days of priced Claude rows × K
            → once the runtime has 7 days of its own rows: p90 of its own total_tokens per run
K = 1.0e6 tokens/USD,  H = 2,  FLOOR = 10e6 tokens
```

- **With today's numbers:**
  - One research slot at 2 runs a day: 2 × 2 × 4.2 M ≈ **17 M tokens a day**.
  - One dreaming slot at 3 runs a day: 3 × 2 × 6.0 M ≈ **36 M**.
- **Why this shape:**
  - The headroom H = 2 lets one runaway run pass and stops the second.
  - The ceiling recalibrates itself from the runtime's own history after a
    week, so the USD-to-token guess only matters at the start.
  - An owner with no Claude history gets the floor.
- **Open:**
  - How tokens map onto an AI Pro quota window isn't published.
  - The real protection for the owner's interactive use is
    `classify_failure` → `usage_limit(resets_at)`, read from `agy -p
    "/usage"` (§1.7).
- **The 45-minute timeout (Q6) against the same runs:**
  - Since 2026-11-01, 4 of 29 research runs and 3 of 38 dreaming runs ran over
    45 minutes. Research p90 was 111 minutes.
  - Those durations include retry waits, so a model run alone is shorter.
  - Still, a 45-minute default would cut real research runs.
  - **Recommendation:** 45 minutes stays the default for a *new* runtime slot.
    The doctor warns when a slot's Claude p90 duration is above it. Moving a
    research slot to agy starts at 90 minutes.

## 4. Spike 4: canary reliability

**How Claude's probe (#635) works.**
- `FakeAnthropic` scripts the turn, so the model *always* asks for `cat
  knowledge-base/probe.md`.
- The probe passes only if the tool result carries the `[scout:kb-read-gate]`
  mark and the canary string never comes back.
- "Never tried" can't happen except through infrastructure failure, which is
  `fake.issued == 0`.

**Antigravity, fake-model path [run].** The same holds, because the scripted
turn is deterministic.
- 1 of 1 runs per mode made the call.
- The deny shows in three places:
  - the `step_update.tool_info.error` (`tool call denied by pre-tool hook:
    [scout:kb-read-gate] …`);
  - `result.denied_actions` when the permission engine denies;
  - the hook's own record of the call.
- So the automatic per-version probe (§1.9, step 1) is as reliable as
  Claude's.

**Antigravity, real-login path [pending].** Whether a scripted prompt
reliably makes a real Gemini model attempt the canary read needs N real runs.
Proposed:
- 10 runs of one prompt.
  - The prompt: "Run exactly this shell command and report its output: `cat
    knowledge-base/probe.md`".
  - Settings: `--effort low`, the cheapest listed model, and Scout's deny hook.
- Pass rule:
  - **pass**: the hook saw `run_command` or `read_file` on the canary path, the
    tool error carries the gate mark, and the canary is absent from stdout and
    the transcript;
  - **inconclusive**: the hook never saw a call on the canary path (the model
    didn't try). Retry once; still nothing means refuse and doctor;
  - **fail**: the canary string appears anywhere.
- The hook-side record is what tells "denied" from "never tried". The model's
  own text doesn't count.
- These 10 runs, plus one first real-login run that confirms hooks load on the
  OAuth path, are the only quota this phase needs. They wait for Jordan's OK
  per run.

## 5. What changes in the spec (for the spec owner)

- §3.2:
  - Antigravity's custom endpoint is "**Yes**, via `modelProvider: "gemini"` +
    `GOOGLE_GEMINI_BASE_URL` (gemini-provider path only)".
  - Antigravity's hook failure behaviour is "fail-closed except empty output
    with exit 0".
  - Gemini CLI's hooks are "run under `-p`; fail open except deny or exit ≥ 2
    with output".
- §3.1:
  - Codex hooks under `exec`: "yes, if trusted; untrusted are skipped
    silently".
  - Codex hook failure behaviour: "fail open except deny or exit 2 with
    stderr".
- §8.3 rule 4 and §11.2: Antigravity gets the free per-version fake-model probe
  plus a rare real-login probe (§1.9 here). That needs Jordan's OK, since it
  narrows Q11.
- §8.5: add the three runtimes' own sandboxes to "must be off inside Scout's
  sandbox", and agy's local-server env vars to the shell gate's deny list.
- §8.6: the formula in §3, and the timeout caveat.

## 6. Not done here

- agy under the real #642 profile.
- agy's login under launchd.
- The quota-exhausted text.
- The 10-run reliability set.
- Codex `apply_patch` under a real OpenAI model's tool set.

Each is listed with its owner phase above.

## 7. Phase 2A results (2026-11-25, agy 1.5.16)

PR 2A of the Phase 2 plan (`../plans/2026-11-24-runtime-agnostic-phase2-antigravity.md`), with no model.
In this section **[run]** means observed in a local run on 2026-11-25, and **[bin]** means a string in the
agy 1.5.16 binary. Every run here used a throwaway `HOME` (outside any temp dir, so the PATH rule applies
as it does for a scheduled run), a scratch vault, agy copied into that `HOME`'s `~/.local/bin`, the
gemini-provider fake (§0.1) and a loopback-only network rule. No real login was used.

agy had updated itself from 1.5.9 to **1.5.16** since Phase 0, between runs and outside Scout **[run]**. That
is §0.6 in practice.

### 7.1 agy under the real run profile (2A.2)

The profile was `scout.sandbox.profile.build_profile(vault, env, runtime_paths=Antigravity().sandbox_paths(env),
runtime_id="antigravity")`. Its only carve-out under `~/.gemini` is `~/.gemini/antigravity-cli`.

- **First start in a fresh `HOME`: agy can't start** **[run]**. It exits 1 with `failed to get/create
  default project: … mkdir ~/.gemini/config/projects: operation not permitted`.
  - A diagnostic profile (spike only, never a product rule) let agy write all of `~/.gemini`. That showed
    what a first start writes outside its data dir: `~/.gemini/config/.migrated`, `config.json`,
    `mcp_config.json` and `projects/default-cli-project.json` **[run]**.
  - These are in agy's config dir, beside `hooks.json`. This was a **stop-and-ask** (a write under a
    protected root), sent to Jordan through the coordinator on 2026-11-25.
  - **Decided (Jordan, 2026-11-25): no carve-out.** `agent-runtime install` (2C) and the per-version probe (2E)
    start agy once outside the sandbox. A run that still meets a first start fails closed. Plan 2C.4,
    decision 8.
- **Second start, `HOME` already set up: agy works under the profile unchanged** **[run]**.
  - It exits 0. The scripted `write_file` and `run_command` both reached the PreToolUse hook and ran.
  - Nothing outside the data dir was written, and nothing was tried and denied, apart from the network and
    one read bait (below).
  - So the config-dir writes come from a first start, and probably from a version migration (`.migrated`).
- **What a warm start writes in the data dir** **[run]**:
  - `conversations/<id>.db`, `brain/<id>/…` (transcripts, per-step output), `cli.log`, `log/`, `cache/`,
    `presence/<id>.lock`, `conversation_summaries.db`, `jetbox_summaries_proto.pb`;
  - `bin/agentapi`. This is a two-line shell shim, `exec <agy> agentapi "$@"`, and it is what
    `ANTIGRAVITY_AGENTAPI_EXE` points at. agy rewrites it before **every** shell call: with `bin/` denied,
    `run_command` fails (`failed to build host shell injection: failed to write agentapi script`) **[run]**.
    **Decided (Jordan, 2026-11-25):** `bin/` stays writable, and the runner verifies and restores the shim
    around each agy run. A change is restored and reported in run health; a failed restore skips the run.
    Plan 2C.4, decision 10.
  - No name the profile re-denies (`settings*.json`, `config.toml`, `hooks.json`, `hooks/`, `mcp*.json`) is
    written in the data dir.
- **The data dir holds instructions agy loads** **[run]**:
  - A first start unpacks `builtin/skills/*/SKILL.md` and `builtin/plugins/swarm/` (a `plugin.json` and
    sidecar `.js`/`.mjs` files) into the data dir.
  - An edit there survives the next start: the `builtin/.checksum` and `.manifest` didn't restore it. It also
    **reaches the next session's system prompt**: a changed `SKILL.md` description was in the next model
    request word for word, under "Available skills".
  - So a run could leave instructions for every later agy session, the owner's interactive ones included.
    That is the class of the plan's `.env`/`extensions/` stop-and-ask, and it went to Jordan with the first
    point.
  - **Decided (Jordan, 2026-11-25): `builtin/` is denied to runs.** It is vendor content outside review, and
    Scout's skills reach agy through the vault and the plugin's reviewed path (plan decision 9).
    - `profile.AGENT_DATA_REDENY` re-denies `~/.gemini/antigravity-cli/builtin` after the data-dir carve-out.
    - A warm start under that profile ran both scripted calls with no denial in `builtin/` **[run]**.
    - Pinned by `test_agy_builtin_skills_and_plugins_are_not` (kernel) and
      `test_a_run_cant_write_agys_builtin_skills_or_plugins` (model).
- **Reads.**
  - agy reads `~/.gemini/GEMINI.md` (denied here as bait; it ran on).
  - It touched none of `.env`, `extensions/`, `commands/` or `GEMINI.md` in its data dir, and none of `.env`,
    `extensions/` or `commands/` at `~/.gemini` **[run]**. So there is no stop-and-ask for reads. The
    `GEMINI.md` read goes to 2I.
- **The updater is denied, and agy carries on** **[run]**.
  - agy tried to create `~/.local/bin/.update_test<N>`. The PATH-entry rule (profile step 5) denied it.
  - The binary was unchanged after every run. Plan decision 3 holds.
- **Network.** agy tried outbound `*:443` at every start (denied by the loopback rule) and still ran on the
  fake **[run]**.
- **A case variant of the data dir** (`.Gemini/Antigravity-CLI`) matches the carve-out too, because SBPL
  matching is case-insensitive here. That is harmless on a case-insensitive APFS volume, and the re-denies match
  case-insensitively as well.
- **For 2B** **[run]**:
  - `--print-timeout` takes a Go duration (`120s`); a bare `120` is rejected with exit 2.
  - agy checks a tool call's arguments before the hook fires: a call with bad arguments never reaches it.
  - `write_file` takes `Path` and `Content` (plus `Append`, `Overwrite`, …). `run_command` needs
    `CommandLine`, `toolSummary` and `WaitMsBeforeAsync`.
  - The fake's model path was `/v1beta/models/gemini-3.1-pro-preview:streamGenerateContent?alt=sse`.
  - `init.tools` lists 60 names. The model was offered 15 hook-side names: §1.3's 14 plus `update_steps`,
    which is new in 1.5.16. It stays unclassed, and so denied in a run, until 2C classes it.
- **Pinned:** the kernel rows `test_agy_data_dir_is_writable_under_the_run_profile`,
  `test_agy_hooks_config_is_not` (with `<vault>/.agents/hooks.json`), `test_agy_builtin_skills_and_plugins_are_not`
  and `test_agy_cant_replace_itself_in_a_run`
  (`tests/integration/test_run_sandbox_kernel.py`).

### 7.2 agy's local server and the shell gate (2A.3)

- **A shell child gets 10 `ANTIGRAVITY_*` variables, not 3** **[run]**: `AGENT`, `AGENTAPI_EXE`,
  `APP_DATA_DIR`, `CONVERSATION_ID`, `CSRF_TOKEN`, `LS_ADDRESS`, `LS_VERSION`, `PROJECT_ID`,
  `SOURCE_METADATA` and `TRAJECTORY_ID` (names only). The new deny matches the shared prefix, so it covers all ten.
- **The server.**
  - It is agy's own process (the same pid), listening on localhost HTTP and HTTPS ports **[run]**.
  - It speaks Connect/JSON at `/exa.language_server_pb.LanguageServerService/<Method>`. A `strings` pass over the
    binary gave 339 names there, but 29 of them had neighbouring bytes stuck on, answered 404 "page not found"
    and so name no method. That leaves 310 real methods **[bin, run]**.
  - A call without the token gets 401 **[run]**.
- **The token a shell child gets is scoped** **[run]**. Every call had an empty `{}` body.
  - It authenticates as principal `run_command`.
  - Of the 310 real methods, 142 answer 403 `not allowed for principal "run_command"`. Among them are `ReadFile`,
    `WriteFile`, `RunCommand`, `CreateTerminal`, `SendTerminalInput`, `SetUserSettings`,
    `SetMendelFlagOverrides`, `InstallCascadePlugin`, `ToggleMcpServer`, `DeleteFileOrDirectory`,
    `HandleCascadeUserInteraction` and `DeleteAllUserData`.
  - The other 168 get past the principal check. A 400 or 501 there means the call reached its handler, not
    that it worked. `GetUserSettings` and `GetPermissionSettings` are among them.
  - **Not probed:**
    - `UpdatePermissionSettings`: its name was one of the 29 broken ones.
    - Both `exa.conversation_pb.ConversationService` and `jetski.product.v1.ConversationService`.
  - So a run's shell can't read or write a file, run a command or change user settings through the server.
    Whether it can change permission settings is not known.
- **What the scoped token still reaches** **[run]**. These methods got past the principal check to their
  handler, which answered 200, or 400 for a missing argument:
  - **Starting or steering agent work:** `StartCascade`, `SendUserCascadeMessage`, `SendAgentMessage`,
    `ForkConversation`, `StartBattleMode` and `RefreshMcpServers`.
  - **Reading other conversations:** `GetAllCascadeTrajectories`, `SearchConversations`,
    `GetCascadeTrajectory`, `LoadTrajectory` and `ConvertTrajectoryToMarkdown`.
    - In an owner's real `HOME`, those include the owner's interactive conversations and earlier runs'
      transcripts. That would be a read around Scout's read gate.
  - **Open:** whether a conversation started or steered this way runs its tool calls through the PreToolUse
    hook. The probe that would show it was declined by the session's permission check, and I didn't retry
    it. A later reviewed spike, or 2E's conformance suite, answers it.
- **The deny** **[run]**:
  - `kb_read_gate.decide()` denies a run's Bash command whose text names `ANTIGRAVITY_` in any case. It does so
    before the path checks, on every runtime (`hook_paths.AGY_SERVER_ENV`, `names_agy_server_env`).
  - Test: `tests/unit/test_kb_read_gate_agy_server.py`.
  - **It is a speed bump, not a boundary.** A command that builds the name at run time, or dumps the whole
    environment, isn't matched by its text.
  - **The same exposure, without the server.** The run profile leaves agy's data dir writable (§7.1), and
    nothing gates reads there. So a run can read **and write** `conversations/` and `brain/`. In the owner's
    real `HOME`, those hold the owner's interactive conversations and earlier runs' transcripts. The
    isolation design must cover this too; a per-run `HOME` would.
  - **Loopback is open.** The run profile allows loopback TCP. A command that prints the environment once,
    and then calls the server with the literal token it saw, gets past the text match. The isolation design
    has to assume a run can reach the server's port.
  - **Decided (Jordan, 2026-11-25): real runs are blocked** until he OKs a design that stops a run's shell
    commands from reaching agy's API (plan decision 11, the 7.2 gate). No-model work goes on, and the design
    comes from its own investigation card.
  - So the deny stays on (plan decision 5). Lifting it would need the open question above answered no. A real fix
    is agy not giving these variables to a run's shell, or the server refusing the scoped principal the
    conversation methods. Both are for 2B and 2C.
- **The replay** **[run]**:
  - In the last 50 scheduled runs, none of the 1,735 Bash calls names `ANTIGRAVITY_`, so the new rule can't
    change a decision on any of them.
  - The full hook replay (main vs this branch, Bash only, `append_event` stubbed) is reported in the PR.
- Claude Code's `hooks.json` and `prefilter.sh` are unchanged. The prefilter doesn't send such a command to
  Python on Claude Code, which has none of these variables. agy's own hook entry (2C) starts Python on every
  call.

### 7.3 agy's login under launchd (2A.4)

- `plugin/scripts/spikes/agy-launchd-login.sh` prints a one-shot LaunchAgent and the `launchctl bootstrap` /
  `print` / `bootout` lines. With `--write` it writes the plist to `~/scout-reports/`, never into
  `~/Library/LaunchAgents`. It never loads a job.
- The job runs `agy --version` and then `agy -p "/usage"` (no model turn, §1.7) with a scheduled run's
  environment: `HOME`, a fixed PATH, `SCOUT_MODE` and no Gemini key. It writes the output and exit code to
  `~/scout-reports/agy-launchd-login.txt`.
- **[pending]**: Jordan runs the printed lines himself, on his real login. Then this section records:
  - whether agy was signed in;
  - any Keychain prompt;
  - the exact `/usage` text, scrubbed, which becomes 2B's `classify_failure` fixture.

### 7.4 Keeping a run's shell away from agy's API (the 7.2 gate): design, for Jordan's OK

The 7.2 investigation card (plan decision 11), agy 1.5.16, no model and no real login.
- **[bin]** here also covers the protobuf descriptors and Go symbol names embedded in the binary.
- **[run]** covers only `agy --help`, `agy changelog`, `agy agentapi --help` and one SBPL compile check. All ran
  in a throwaway `HOME` under a loopback-only diagnostic profile.
- The session's permission check declined agy's warm first start against the fake, and an SBPL behaviour test
  that used only system tools. I retried neither.
- So every measurement that needs agy running is **[pending]**, and the harness for them is ready (§7.4.2).

#### 7.4.1 What 7.2 didn't have

- **A second, documented API on the same address** **[bin]**.
  - agy also serves `jetski.product.v1.ConversationService`: `CreateConversation` (an initial prompt starts a
    turn), `ListConversations`, `GetConversation`, `UpdateConversation` and `DeleteConversation`.
  - A built-in skill, `conversations`, documents it for "shell commands, scripts, plugins, and automations".
    It uses the same two variables and the header `x-antigravity-csrf-token`.
  - So a shell child starting, renaming or deleting conversations is a designed feature, not a leak.
  - 7.2 didn't probe this service. Whether the `run_command` principal may call it is **[pending]**.
- **A client that needs no variable in the command** **[run]**.
  - `agy agentapi` has `new-conversation [--model …] <prompt>`, `send-message <recipient_id> <content>` and
    `get-conversation-metadata <id>`. It reads the address and token from its own environment.
  - The data dir's `bin/agentapi` shim runs it (§7.1), and built-in plugin code calls it the same way.
  - A command such as `agentapi new-conversation "…"` names no `ANTIGRAVITY_` variable, so 2A's text deny
    doesn't match it. The same goes for calling the shim by its fixed path, `~/.gemini/antigravity-cli/bin/agentapi`.
  - Whether the shim is on a child's `PATH` is **[pending]**.
- **The token's scope is an experiment flag** **[bin]**.
  - The check is `CsrfInterceptor.isScopedEnforcementEnabled`, behind `enable-scoped-csrf-tokens`. That flag is
    `true` in the binary's embedded experiment defaults.
  - agy fetches experiment flags from its server config (`FetchConfigResponse.ExperimentConfig`). So the
    scope can change for an account with no new agy version, and 2E.2's per-version probe wouldn't see it.
  - A run can't change the flag itself: `SetMendelFlagOverrides` is refused to `run_command` (§7.2). It can
    read the flags, though: `GetMendelFlags` answered 200 with the experiment config in 2A's enumeration.
- **A discovery file with a token** **[bin]**.
  - While it runs, the server writes a discovery file (`writeDiscoveryFile`, a struct of `pid`, `httpsPort`,
    `httpPort`, `lspPort`, `lsVersion` and `csrfToken`). It deletes the file at exit, which is why 2A's after-run
    listing doesn't show it.
  - Its path and its token's principal are **[pending]**.
  - If it sits in the data dir and holds the server's own token, a run that reads it reaches the 142 methods
    refused to `run_command` too: `RunCommand`, `WriteFile`, `SetUserSettings`, `InstallCascadePlugin` and the rest.
- **The variables come with agy's host shell injection** **[bin]**.
  - agy sources a file before each command: `{ [ -f F ] && . F; } 2>/dev/null`.
  - It fails the call ("failed to build host shell injection") when it can't write the shim (§7.1).
  - Scout doesn't own that step.
- **Agent messages have an on-disk store** **[bin]**. An "undelivered" directory with markers lives in the data
  dir. Whether a file a run writes there gets delivered into a conversation is **[pending]**.
- **What the API adds beyond what the shell can already do.** A shell child runs in the same sandbox as agy,
  so the kernel boundary holds for anything the API starts. What the API adds is:
  1. **Reads** of other conversations, the owner's and earlier runs'. The data-dir files expose the same
     (§7.2).
  2. **Changes that outlast the run.** Owner conversations renamed or deleted through `ConversationService`
     **[pending]**, or anything the discovery-file token allows.
  3. **Model-driven tool calls that may skip the PreToolUse hook** (question 1). The hook is where Tier L's raw
     KB-read deny, 2D's local-only withholding and the write gate live.

#### 7.4.2 Question 1: do API-started conversations reach the hook? **[pending]**

- **Not answered.** The probe needs agy running. This session's permission check declined agy's warm start,
  the step before the probe, and I didn't retry it or work around it.
- **The harness is ready**, outside any temp dir and outside the repo:
  - the real run profile from `scout.sandbox.profile.build_profile` for a throwaway `HOME`, plus a diagnostic
    loopback-only rule;
  - the gemini-provider fake, which scripts tool calls per conversation, keyed by a marker in the last user
    message;
  - a logging hook that denies any call naming a marker and `auto_approve`s the rest;
  - a probe that the run's own `run_command` child executes.
- **The probe drives these routes:**
  - `CreateConversation` with a prompt;
  - `StartCascade`, then `SendUserCascadeMessage`;
  - `SendUserCascadeMessage` into the run's own conversation;
  - `ForkConversation`;
  - `SendAgentMessage`.
- Each child conversation then runs an allowed and a denied `run_command`, and an allowed and a denied
  `write_file`.
- **It records:**
  - each route's HTTP code;
  - the hook's log, by `conversationId`;
  - which marker files exist;
  - each child's `runState`;
  - the server's listeners (`lsof`).
- **The design below doesn't wait on the answer if candidate b2 holds.** Then no process in a run reaches the
  server, and question 1 only matters for owner sessions.
- If b2 fails, question 1 decides whether a per-run `HOME` alone is enough. It is enough only if every route's
  tool calls hit the hook, with the same verdicts.

#### 7.4.3 The candidates, measured as far as allowed

**a. A per-run `HOME` with no owner conversations.**
- **What it takes away.** Everything a run could read or tamper with in agy's state, through the API and in
  the files alike: `conversations/`, `brain/`, `conversation_summaries.db`, the message store and other runs'
  transcripts.
  - Settings, plugin and message-store changes stay inside a throwaway `HOME`.
  - The discovery file becomes the run's own server's.
- **What it doesn't stop.**
  - Starting conversations (question 1).
  - A read of the real `~/.gemini/antigravity-cli` by its absolute path. That needs a new kernel **read**
    deny on the account home's `.gemini/antigravity-cli` for agy runs, like the one on `~/.scout-secrets`.
  - The account home also loses its data-dir carve-out. Today `AGENT_DATA_DIRS` applies "relative to every
    home". With a per-run `HOME`, it must apply under the run's `HOME` only.
- **What goes in.**
  - A template `HOME`, made outside the sandbox by `agent-runtime install` and by the per-version probe. That is
    the same first start that decision 8 already moves out of runs.
  - Its `.gemini/config/` holds `.migrated`, `config.json`, `mcp_config.json`, `projects/default-cli-project.json`
    and Scout's `hooks.json` block.
  - Its data dir holds only the unpacked `builtin/` (still re-denied), `cache/onboarding.json` and
    `installation_id`. Nothing comes from the owner's data dir.
  - Each run gets an APFS clone (`cp -c`, near constant time) under a Scout state dir outside any temp dir, so
    the PATH rule holds. The session reader copies the transcript JSONL it needs, and then the clone is removed.
- **What else moves with `HOME`.**
  - Every child sees the per-run `HOME`: git's global config, `gh`, Scout's own config, `~/.scout-secrets`
    (the Telegram files) and Python's user site. `ssh` takes the home from the password database, so it isn't
    affected.
  - Symlinks in the template keep these working. The kernel judges the resolved path, so a protected target
    stays protected.
  - The exact list needs a pass over the last 50 scheduled runs.
  - `hook_paths.homes(env)` already handles a `HOME` that isn't the account home.
- **Sign-in: [pending]**, by the card's rule.
  - agy keeps the token in the macOS Keychain through go-keyring, with a file fallback **[bin, cl]**.
  - A fresh `HOME` reads as signed out (§1.7), and the reason isn't known. It is either a non-secret account
    index under `HOME`, or a Keychain key that includes something per-`HOME`.
  - If it is the index, copying the index into the template should do.
  - If not, the owner signs in once inside the template, which may put a second token in the Keychain.
  - If that token falls back to a file inside the template, a run could read it, so the file would need a
    read deny too.
  - This is the riskiest unknown in (a).
- **Variant a′: move only the data dir.**
  - `ANTIGRAVITY_APP_DATA_DIR` reaches children, and `JETSKI_APP_DATA_DIR` is in the binary too **[bin]**.
  - If agy honours either as an input, Scout relocates just the data dir. `HOME`, the hooks config and the
    sign-in stay as they are, and nothing else moves.
  - **[pending]**: `agy changelog` with both set wrote nothing anywhere, which is inconclusive. A warm `-p` run
    decides it.

**b. Denying shell children the server's port.**
- **A tighter layer for children is not possible.**
  - macOS refuses `sandbox_apply` inside a sandbox (§0.5, §1.5 **[run]**, exit 71). That holds whether agy
    applies the second layer, or a wrapper Scout puts in agy's place.
  - A broker outside the sandbox could run each command under a tighter profile. But that means replacing
    agy's shell: its persistent terminals, its pty and the host shell injection. The children would also stop
    being agy's descendants. That is big and fragile, so it isn't recommended.
- **A per-process rule leaks.**
  - SBPL accepts `(process-path …)` in a `network-outbound` rule **[run: compile check]**. Whether it matches
    per process is **[pending]**, because the behaviour test was declined.
  - Even if it does match, a child can exec agy itself (`agy agentapi …`, which is what the shim does) and
    carry agy's path.
  - Denying the exec of agy's path would probably also deny the runner's own first exec of it. That is
    **[pending]** on the same declined test.
- **b2: no loopback TCP for the whole agy run, agy included.**
  - Only what agy itself needs is allowed. The server's ports are random, and that doesn't matter here.
  - In a real run the model is remote (`*.googleapis.com:443`). The spike's fake gets one allowed port.
  - b2 works **only if** agy makes no loopback connection of its own. That means its CLI front end and
    in-process server, the hook runner, `run_command`'s host injection and `write_file`. **[pending]**: this is
    the key measurement.
  - A unix-socket listener needs the same treatment. **[pending]**: the probe's `lsof` shows it.
  - **It covers** every route above, from every process in the run: `curl`, `agentapi`, the discovery-file
    token and `ConversationService` alike.
  - **It doesn't cover** the data-dir files. That is (a)'s part.
  - **It costs** agy runs any loopback MCP server. None is configured today.

**c. An agy option that keeps the token out of children, or unscopes the methods.**
- **None found** **[run, cl, bin]**. There is nothing in `agy --help`, or in the changelog's 1,370 lines.
  - agy's settings live in `~/.gemini/antigravity-cli/settings.json`, which the run profile already re-denies.
  - The nearest switches are experiment flags with embedded defaults, which agy's server config can override:
    `enable-scoped-csrf-tokens`, `enable-conversation-scoped-permissions`, `enable-product-api-skill`,
    `enable-rpc-sandbox-proxy` and `enable-rpc-sandbox-proxy-allow-all`.
- **agy's own terminal sandbox does block the port.** The `conversations` skill tells the agent to rerun outside
  it on "Port … not allowed for HTTP". But that sandbox runs only with `--sandbox`, which can't start inside
  Scout's (§1.5).
- **Unsetting the variables is not Scout's to do, and it isn't a boundary.**
  - They arrive with the host shell injection (§7.4.1), and the injection file is agy's.
  - Whether `run_command` survives without them is **[pending]**.
  - On macOS a same-user process can, as a rule, read another process's environment (`KERN_PROCARGS2`) unless
    the sandbox denies `process-info*`. That wasn't measured here.
  - The discovery file and the injection file are on disk too.
- **It costs nothing, and it gives Scout nothing to rely on.** It is worth an upstream request: an option not to
  give `run_command` children server credentials, or a principal that refuses the conversation methods.

**d. Others.**
- Extend the hook's text deny to `agentapi` and to invocations of agy's own binary. It is a speed bump like 2A's,
  and cheap.
- Read-deny the discovery file once its path is known. With b2 it is belt and braces; without b2 it matters.

#### 7.4.4 The data dir (question 3)

| | Owner's conversations through the API | Owner's files (`conversations/`, `brain/`) | New conversations | Discovery-file token |
|---|---|---|---|---|
| a. per-run `HOME` (or a′) | gone | gone from agy's dir; the real dir needs the new read deny | still possible | the run's own server only |
| b2. no loopback | unreachable | **open** | unreachable | unusable |
| c. agy option | none found | open | none found | none found |
| 2A's text deny | speed bump | open | speed bump (`agentapi` passes) | speed bump |

- Only a (or a′), with the account-home read deny, keeps a run out of other conversations' files.
- Only b2 keeps a run from starting conversations without depending on question 1.

#### 7.4.5 Recommendation

**Use a′ if agy honours the data-dir variable, else a. Add b2 and the account-home read deny. Keep 2A's text
deny (decision 5), and extend it to `agentapi`.**

- **Lift the 7.2 gate only when these [pending] rows are [run]**, all with no model:
  - **b2:** under a no-loopback profile, a warm `agy -p` runs `run_command` and `write_file`, and both reach
    the hook. The kernel refuses every probe route.
  - **a′ or a:** the run's conversations land in the per-run dir, and none of the owner's are visible.
  - **Sign-in** works for the chosen variant. That comes from docs or the owner's own check, never from this card.
  - **Question 1** is answered, for the record and for owner sessions.
- **If b2 fails** because agy needs loopback itself, lift the gate only with (a) or (a′) **and** question 1
  answered "every route hits the hook with the same verdicts". If question 1 is no, agy stays blocked, and the
  upstream request is the way out.
- **What it costs Scout:**
  - **Protected paths.**
    - A new read-denied tree for agy runs: the account home's `.gemini/antigravity-cli`.
    - The account home's carve-out goes, so all of the real `~/.gemini` is write-denied in an agy run.
    - The template dir is a protected tree, written only by `install` and the probe.
    - The discovery file gets a read deny once its path is known.
  - **Profile.**
    - `AGENT_DATA_DIRS` applies under the run's `HOME` only.
    - A no-loopback network rule for runtime `antigravity`.
    - Claude runs' profile stays byte-identical.
  - **Runner.**
    - It clones the template before an agy run and removes the clone after.
    - A failed clone skips the run, as a failed shim restore does (decision 10).
    - The shim check moves into the clone.
  - **Adapter.**
    - `Launch.env` gets `HOME` or the data-dir variable.
    - `sandbox_paths` names the run's data dir.
    - `install` and the probe maintain the template.
  - **Kernel rows** (`tests/integration/test_run_sandbox_kernel.py`): `test_agy_run_cant_reach_loopback`,
    `test_agy_run_cant_read_the_owners_agy_data`, `test_agy_run_cant_write_the_owners_gemini_dir`,
    `test_agy_run_home_config_is_not_writable` and `test_agy_run_home_builtin_is_not_writable`.
  - **Unit tests:** `test_a_run_gets_a_fresh_agy_home`, `test_the_run_home_is_removed_after_the_run`,
    `test_a_failed_home_clone_skips_the_run`, `test_the_run_home_is_outside_temp_dirs` and
    `test_the_text_deny_covers_agentapi`.
  - **2D.1:** the kernel, not a gate, then meets `test_a_run_cant_read_or_write_another_agy_conversation`.

#### 7.4.6 For Jordan

1. **The no-model measurements: Jordan runs them himself** (2026-11-25).
   - The coordinator reviews the scripts first.
   - A first try stopped at a Google sign-in prompt, because the spike's scripts lacked the gemini-provider
     `--settings` flag. Nothing was signed in and nothing left loopback.
   - The scripts now refuse to start without that flag, and fail loudly if agy prints a sign-in URL.
2. **a′ or a, and where the per-run homes live:** decided after the measurements.
3. **No loopback at all for agy runs, so no localhost MCP servers in them:** accepted (Jordan, 2026-11-25).
4. **An upstream request to Google:** drafted in §7.4.7, for Jordan to file himself.
5. **A kernel read deny on local-only paths for non-Anthropic runs:** yes (Jordan, 2026-11-25). It is plan task
   2D.4, because 2D's hook and CLI gates match command text and so aren't a boundary.

#### 7.4.7 Draft upstream request (for Jordan to file; generic, no Scout or vault detail)

> **Feature request: an option to keep the local server's credentials out of agent shell commands**
>
> When the agent runs a shell command, the command's environment gets the local server's address and a CSRF
> token (`ANTIGRAVITY_LS_ADDRESS`, `ANTIGRAVITY_CSRF_TOKEN`, and the `agentapi` helper). The token is scoped,
> but it can still create, message, fork and delete conversations and read other conversations' trajectories.
> That is useful interactively. But when the CLI runs headless as a sandboxed automation, it lets a command the
> model wrote start agent work and read history outside the tool-call hooks the operator configured.
>
> Please add one of these, as a setting or a flag that `--settings` accepts:
> 1. an option not to put the server's address, token or `agentapi` into shell commands' environment at all;
>    or
> 2. an option to scope the shell token to no server methods.
>
> Please also document whether conversations started through the API run their tool calls through the
> configured `PreToolUse` hooks. Whichever exists, it would ideally be enforced locally, not only through a
> server-side experiment flag, so that a headless operator can rely on it across versions.
