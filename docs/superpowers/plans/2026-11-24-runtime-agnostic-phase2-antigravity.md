# Runtime-agnostic Phase 2: Antigravity CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a Scout schedule slot run on Google's Antigravity CLI (`agy`) at Tier L, with the same gates, sandbox, limits and run health as a Claude Code run.

**Architecture:** One new adapter, `scout/agent_runtimes/antigravity.py`, behind the interface Phase 1 and #652 built. Scout's gate decisions don't change. agy's hook payloads are translated into the Claude-shaped payload that `decide()` already judges, and the reply is translated back. A free fake-model probe proves each agy version before it may run on the vault. A rare real-login probe proves the owner's OAuth path loads the same hooks.

**Tech Stack:** Python 3.12 engine (`plugin/engine`, typer, pytest), bash runner templates, SwiftUI app (`apps/macos`), agy ≥ 1.5.9.

**Spec:** `docs/superpowers/specs/2026-11-13-runtime-agnostic-design.md` (§8, §10–§13; the Phase 2 row of §12), with `docs/superpowers/specs/2026-11-16-runtime-agnostic-phase0-findings.md` (§1, §3, §4). The parent plan is `docs/superpowers/plans/2026-11-13-runtime-agnostic.md`; this file replaces its Phase 2 card list.

**Starts after:** #652's follow-ups, merged as #806 (e32f1656): the non-Anthropic env allowlist (`base.NON_ANTHROPIC_ENV_ALLOW`), `Launch.env_unset`/`cwd`, `RunRequest.timeout_s`, `sandbox_paths` with `profile.AGENT_DATA_DIRS`, `gates probe --runtime`, `sandbox profile --runtime`, `agent_runtime.fallback`, `declared_capabilities`, `interactive_host`. Every interface below assumes them.

**Detail level.** PRs 2A–2C are written step by step: they are the critical path, and what they find can change the rest. PRs 2D–2K are written per task, with the interfaces, test names and done-when fixed. Each gets its step-level pass when the PR before it merges. A finding in 2A that contradicts this plan stops the work and goes back to the coordinator, not into a workaround.

## Global Constraints

- The runtime id is `antigravity`, provider `google`, display name `Antigravity CLI` (spec §8.7).
- Tier L at best: agy has no `post_tool_context` (findings §1.3). Never claim Tier F for it.
- Argv: `agy -p PROMPT --output-format stream-json --model MODEL --print-timeout SECONDS --settings JSON` (findings §1.9). Never `--sandbox` and never `--dangerously-skip-permissions`.
- The hook's reply is always a decision: `auto_approve` or `deny` in a scheduled run, `allow` or `deny` in an owner session. Never empty output with exit 0, which agy allows (findings §1.4).
- `hook_in` takes `cwd` from the payload, never from the process (the hook runs in `~/.gemini/config`, findings §1.2). Only `--agent-runtime antigravity` says the hook is agy's.
- Tools are classed by an allowlist of hook-side names (`run_command`, `read_file`, `write_file`, `edit_file`, …). Anything unclassed is denied in a run, and so are `run_workflow`, `swarm`, `invoke_subagent`, `define_subagent` and `manage_subagents` (spec §8.5).
- Until 2A proves agy's local server can't start tool calls around the hooks, a run's command naming `ANTIGRAVITY_LS_ADDRESS`, `ANTIGRAVITY_CSRF_TOKEN` or `ANTIGRAVITY_AGENTAPI_EXE` is denied (spec §8.5).
- Local-only paths are never readable by an agy run: not through the read gate, not through `scoutctl kb` (spec §8.4).
- No model run, no real-login probe and no eval on Jordan's login without his OK for that run (spec §8.3 rule 4, §11.3). CI never starts agy against a real model.
- **The 7.2 gate (decision 11): no agy run that calls a real model happens until Jordan has OK'd a design that stops a run's shell commands from reaching agy's local API** (findings §7.2). That covers a scheduled slot, the real-login probe (2E.3), the eval (2H) and acceptance (2K). 2B onward may build and test against the fake endpoint, and nothing in this gate holds them back. The design comes from its own no-model investigation card; the shell-gate deny of `ANTIGRAVITY_*` is not that design.
- Run limits are on by default for agy: 45 awake minutes (90 for a research slot) and the `auto` daily token ceiling (spec §8.6). agy has no `budget_cap`.
- Generated files (`~/.gemini/config/hooks.json` block, `plugin/runtimes/antigravity/`, `verified.json`, `connectors.snapshot.json`) are generated, never hand-edited, and drift-tested.
- Public surfaces (fixtures, PRs, commits, docs) use the stand-ins in `plugin/CLAUDE.md`. No vault content.
- Inside another agent CLI's home folder (`~/.gemini`, `~/.codex`, …) a run can write only the folders `profile.AGENT_DATA_DIRS` lists for its runtime id (agy: `~/.gemini/antigravity-cli`), and never a `settings*.json`, `config.toml`, `hooks.json`, `hooks/` or `mcp*.json` there, at any depth (#806), nor the folders `profile.AGENT_DATA_REDENY` lists inside the data folder (agy: `builtin/`, 2A). An adapter can't widen this. A new data folder is a change to `AGENT_DATA_DIRS` with a kernel row, and a need to write one of those names is a stop-and-ask, never a widening.
- Claude Code runs stay byte-identical: golden argv, `hooks.json` and the Claude sandbox profile don't change in any PR here.
- Every PR adds a changelog fragment (`plugin/changelog.d/<slug>.<section>.md`, and `apps/macos/changelog.d/` for 2J).

## Review Focus

1. **A run edits agy's gate config.** A `write_file` to `~/.gemini/config/hooks.json`, a `run_command` that `rm`s it, and a write to `<vault>/.agents/hooks.json` are each denied by the hook and by the sandbox. Tests: 2C.4 `test_a_run_cant_write_agys_gate_config`, 2A.2 kernel row.
2. **A run spoofs the runtime or reader.** A shell child run as `SCOUT_AGENT_RUNTIME=claude-code scoutctl kb read <local-only note>`, or with `SCOUT_READER=owner`, still gets nothing local-only. Test: 2D.2 `test_a_child_cant_claim_another_runtime`.
3. **agy updates itself between or during runs.** A new version is unverified until the free probe passes, and the sandbox denies the in-run replace of `~/.local/bin/agy`. Tests: 2E.2 `test_a_new_agy_version_reprobes`, 2A.2 kernel row.
4. **The hook process dies or hangs.** The watchdog answers `deny` before agy's own timeout, and a crash still prints a deny. Tests: 2C.3 `test_pre_tool_denies_on_any_internal_error`, `test_the_watchdog_denies_before_agys_timeout`.
5. **agy exits 0 after denied calls.** A run whose `result.denied_actions` is non-empty is still `ok`, but the ledger row counts the denials and run health shows them. Test: 2F.1 `test_denied_actions_are_counted`.

---

## PR 2A: No-model groundwork (spec §12 "first, and with no model")

The three things findings §6 left open that need no model. The result is code for the sandbox and the shell gate, plus a findings addendum.

### Task 2A.1: The adapter's skeleton, unregistered

**Files:**
- Create: `plugin/engine/scout/agent_runtimes/antigravity.py`
- Test: `plugin/engine/tests/unit/test_agent_runtime_antigravity.py`

**Interfaces:**
- Consumes: `base.AgentRuntime`, `base.SandboxPaths`, `base.Capability`, `base.Launch`, `base.RunRequest` (Phase 1, #652).
- Produces: `class Antigravity` with `id = "antigravity"`, `provider = "google"`, `display_name = "Antigravity CLI"`, `interactive_host = False`; `DECLARED_CAPABILITIES: frozenset[Capability] = {HEADLESS_EVENTS, USAGE_TOKENS, CUSTOM_ENDPOINT, SKILLS, CONTEXT_FILE}`; `data_dir(env) -> Path` (`$HOME/.gemini/antigravity-cli`); `config_dir(env) -> Path` (`$HOME/.gemini/config`); `sandbox_paths(env) -> SandboxPaths(protected=(), writable=(data_dir(env),))`. `~/.gemini` is already protected whole by `hook_paths.AGENT_DIRS`, so no protected entry is needed: the carve-out is accepted only because `profile.AGENT_DATA_DIRS["antigravity"]` lists `.gemini/antigravity-cli` and the profile is built with `runtime_id="antigravity"` (#806). `data_dir` must match that entry exactly; a test pins it.

- [ ] **Step 1: Write the failing tests**

```python
def test_antigravity_declares_no_budget_cap():
    a = Antigravity()
    assert (a.id, a.provider, a.interactive_host) == ("antigravity", "google", False)
    assert not caps_own_spend(a)
    assert not (a.declared_capabilities & HOOK_CAPABILITIES)

def test_its_data_dir_is_writable_and_its_config_dir_isnt(world):
    home, vault, env = world
    rules = sbx.build_rules(vault, env, runtime_paths=Antigravity().sandbox_paths(env), runtime_id="antigravity")
    assert sbx.decide(rules, str(home / ".gemini/antigravity-cli/brain/c1/x.jsonl")) == "allow"
    assert sbx.decide(rules, str(home / ".gemini/config/hooks.json")) == "deny"
    assert sbx.decide(rules, str(home / ".gemini/settings.json")) == "deny"
    assert sbx.decide(rules, str(home / ".gemini/antigravity-cli/settings.json")) == "deny"

def test_its_data_dir_is_the_profiles_listed_one(world):
    home, _vault, env = world
    (rel,) = sbx.AGENT_DATA_DIRS["antigravity"]
    assert Antigravity().data_dir(env) == home / rel
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/test_agent_runtime_antigravity.py -q`
Expected: FAIL, `ModuleNotFoundError: scout.agent_runtimes.antigravity`

- [ ] **Step 3: Implement the skeleton**

`locate(hint)`: the hint, else `shutil.which("agy")`, else `~/.local/bin/agy` when executable. `version(binary)`: first `\d+\.\d+\.\d+` of `agy --version` (15 s timeout, `None` on any failure). `capabilities()` returns `DECLARED_CAPABILITIES` until 2E. `headless_argv` raises `NotImplementedError` until 2B. Not added to `registry._FACTORIES`.

- [ ] **Step 4: Run the tests to see them pass**, then the protocol check: `isinstance(Antigravity(), AgentRuntime)`.

- [ ] **Step 5: Commit** `feat(agent-runtimes): antigravity adapter skeleton and its sandbox paths (Phase 2A)`

### Task 2A.2: agy under the real #642 profile (spike, then pinned kernel rows)

**Files:**
- Modify: `plugin/engine/tests/integration/test_run_sandbox_kernel.py` (new rows, macOS only, skipped without `sandbox-exec`)
- Modify: `docs/superpowers/specs/2026-11-16-runtime-agnostic-phase0-findings.md` (new §7, "Phase 2A results")

**Interfaces:**
- Consumes: `Antigravity.sandbox_paths`, `scout.sandbox.profile.build_profile(vault, env, runtime_paths=…, runtime_id="antigravity")`.

- [ ] **Step 1: Write the kernel rows** (no agy needed: they test the profile with `/bin/sh` writes)
  - `test_agy_data_dir_is_writable_under_the_run_profile`: a write to `~/.gemini/antigravity-cli/conversations/x.db` succeeds.
  - `test_agy_hooks_config_is_not`: a write to `~/.gemini/config/hooks.json` fails with `Operation not permitted`.
  - `test_agy_cant_replace_itself_in_a_run`: with `~/.local/bin` on `PATH`, `cp /bin/sh ~/.local/bin/agy` fails (the PATH-entry rule, `profile.py` step 5).
- [ ] **Step 2: Run them** (macOS): `.venv/bin/python -m pytest tests/integration/test_run_sandbox_kernel.py -q -k agy`. Expected: PASS. If the data-dir row fails, fix `sandbox_paths`, not the profile.
- [ ] **Step 3: The spike (throwaway, not committed).** In a scratch dir with a throwaway `HOME`, run agy 1.5.9+ under `sandbox-exec -p "$(scoutctl sandbox profile --runtime antigravity …)"` against the gemini-provider fake (findings §0.1), one scripted `write_file` and one `run_command`. Record: does agy start, write its data dir, write anything else (log the denials from `log show --predicate 'sender=="Sandbox"'`), and does its updater try `~/.local/bin`? Also record whether agy reads any of `GEMINI.md`, `.env`, `extensions/` or `commands/` from its data dir (`fs_usage -f filesys` on the agy pid, or `opensnoop`): these stay writable there (#806 re-denies only `settings*.json`, `config.toml`, `hooks.json`, `hooks/` and `mcp*.json`), and all four are already denied at `~/.gemini` itself. And record whether agy tries to write one of the re-denied names inside its data dir.
- [ ] **Step 4: Write findings §7.1** with each result and its evidence tag (**[run]**). Any write outside `data_dir` that agy needs to function goes into `sandbox_paths.writable` (and `AGENT_DATA_DIRS` when it is under `~/.gemini`) with a kernel row, or into a stop-and-ask if it is under a protected root. Two more stop-and-asks for Jordan via the coordinator: agy reads `.env` or `extensions/` from its data dir (they act as config there, so they need a re-deny, and agy may then not start), or agy needs to write a re-denied name there. A read of `GEMINI.md` or `commands/` is recorded in §7.1 and goes to 2I (skills and commands), which decides where Scout puts them. Note in §7.1 that a case variant of the data dir (`.Gemini/Antigravity-CLI`) is accepted too; that is harmless on a case-insensitive disk (APFS default), and the re-denies match case-insensitively.
- [ ] **Step 5: Commit** `test(sandbox): agy's data dir, gate config and self-update under the run profile (Phase 2A)`

### Task 2A.3: The local-server bypass check and the shell-gate deny

**Files:**
- Modify: `plugin/engine/scout/hooks/kb_read_gate.py` (the shell branch of `decide()`)
- Modify: `plugin/engine/scout/kb/live_run.py` only if the deny belongs with `run_escape` there; follow where `env -i` is handled.
- Test: `plugin/engine/tests/unit/test_kb_read_gate_agy_server.py`
- Modify: findings §7.2

**Interfaces:**
- Produces: `AGY_SERVER_ENV: tuple[str, ...] = ("ANTIGRAVITY_LS_ADDRESS", "ANTIGRAVITY_CSRF_TOKEN", "ANTIGRAVITY_AGENTAPI_EXE")` in `scout/kb/hook_paths.py`, denied for every runtime (it costs a Claude run nothing).

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("cmd", [
    'curl -H "x-csrf: $ANTIGRAVITY_CSRF_TOKEN" "$ANTIGRAVITY_LS_ADDRESS/api"',
    "printenv ANTIGRAVITY_CSRF_TOKEN",
    'python3 -c "import os; print(os.environ[\'ANTIGRAVITY_LS_ADDRESS\'])"',
    "env | grep ANTIGRAVITY_",
    '"$ANTIGRAVITY_AGENTAPI_EXE" --help',
])
def test_a_run_cant_reach_agys_local_server(run_env, vault, cmd):
    assert "[scout:kb-read-gate]" in decide(bash(cmd, vault), reader="autonomous", mode="dreaming", env=run_env)
```

`env | grep ANTIGRAVITY_` matches on the prefix: the rule is "the command text contains `ANTIGRAVITY_`", case-insensitive, like the `env -i` rule.

- [ ] **Step 2: Run it to see it fail.** Expected: `None` (allowed).
- [ ] **Step 3: Implement** the deny with the same mark and a reason that names the variable family, before the path checks.
- [ ] **Step 4: Run it, plus the 50-run replay** (`memory: gate-false-deny-replay`): zero new denies on Claude runs.
- [ ] **Step 5: The probe of the server (spike, not committed).** With the fake model, have a scripted `run_command` print the three variables to a file *outside* the gate (use a hook that allows), then from the owner's shell call the local server's endpoints with the token. Record in findings §7.2 whether any endpoint starts a tool call, changes settings, or reads files without a PreToolUse hook firing. Keep the deny either way. Lifting it later is a separate, reviewed PR.
- [ ] **Step 6: Commit** `fix(kb-read-gate): a run can't name agy's local-server variables (Phase 2A)`

### Task 2A.4: agy's login under launchd (Jordan runs it)

**Files:**
- Create: `plugin/scripts/spikes/agy-launchd-login.sh` (prints a plist and the `launchctl bootstrap gui/$UID …` / `bootout` lines; never runs them)
- Modify: findings §7.3

- [ ] **Step 1:** Write the script: a one-shot job that runs `agy -p "/usage"` (no model turn, findings §1.7) with the runner's environment, and writes stdout, stderr and exit code to `~/scout-reports/agy-launchd-login.txt`.
- [ ] **Step 2:** Ask Jordan to run the printed commands himself (`memory: bootstrap-install-in-tests-replaces-live-launchd`: no agent loads a launchd job).
- [ ] **Step 3:** Record in findings §7.3: signed in or not, any Keychain prompt, the exact `/usage` text (it becomes 2B's `classify_failure` fixture after scrubbing).
- [ ] **Step 4: Commit** the script and the addendum: `docs(spike): agy's login under launchd (Phase 2A)`.

**2A done when:** findings §7 has 7.1–7.3 with evidence, the kernel rows and the gate test pass in CI, the replay shows no new Claude denies, and the coordinator has read §7.

---

## PR 2B: The adapter launches agy

### Task 2B.1: `headless_argv`, the generated settings and the environment

**Files:**
- Modify: `plugin/engine/scout/agent_runtimes/antigravity.py`
- Test: `plugin/engine/tests/unit/test_agent_runtime_antigravity.py` (golden argv)

**Interfaces:**
- Produces: `settings_json(req: RunRequest) -> str` (compact JSON, sorted keys); `headless_argv(req) -> Launch` with `argv = (agy, "-p", prompt, "--output-format", "stream-json", "--model", model, "--print-timeout", str(req.timeout_s or 0), "--settings", settings_json(req))`, `env={}` and `env_unset=()` (the defaults), `cwd=req.vault`. A run uses the owner's login, never a key or endpoint from the runner's environment: `exec.launch_env` already passes agy only `base.NON_ANTHROPIC_ENV_ALLOW`, which holds no `GEMINI_*`, `GOOGLE_*` or `ANTIGRAVITY_*` name (#806). If 2A finds agy needs a variable outside the allowlist, the adapter adds it by name in `Launch.env`, where review sees it.
- `settings_json` sets `permissions.deny` for `write_file(~/.gemini/config/**)`, `write_file(<vault>/.agents/**)`, `write_file(<vault>/.gemini/**)` and `write_file(<vault>/.git/**)`: the second layer under the hook (findings §1.9). It never sets `modelProvider`.

- [ ] **Step 1: Failing tests:** `test_golden_argv` (exact tuple for a fixed request, `budget_cap` ignored), `test_the_runners_keys_never_reach_agy` (with `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GEMINI_BASE_URL`, `GOOGLE_APPLICATION_CREDENTIALS` and `CLAUDE_CODE_OAUTH_TOKEN` in the base env, none is in `exec.launch_env(...)` output: this pins the allowlist for agy, not an adapter list), `test_settings_never_choose_a_provider`, `test_print_timeout_is_the_run_timeout`.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them to see them pass.**
- [ ] **Step 5: Commit.**

### Task 2B.2: Models, failures and markers

**Interfaces:**
- `validate_model(value, *, where) -> str`: lower-cased, matches `^gemini-[a-z0-9][a-z0-9.\-]{1,60}$`, else `ConfigError` naming `where` and the value. No catalogue (spec §8.7). `default_model()` returns one fixed id, chosen in this task from what `agy models` lists on a signed-in agy (the cheapest model that passes 2E's probe), and written into this plan before merge. No id is guessed here.
- `known_models() -> tuple[str, ...]`: parsed from `agy models` (cached per version in `.scout-state/agent-runtime-models.json`), empty on failure.
- `classify_failure(log_tail, exit_code) -> Failure`: exit 3 with an `AGY_ERROR: {json}` line → parse `json["code"]`/`json["message"]`. Quota text (from 2A.4's fixture) → `USAGE_LIMIT` with `resets_at` from `rate_limit_reset.find_reset`. A sign-in message → `AUTH`. Network errors → `TRANSIENT`. Else `OTHER`.
- `process_markers(req) -> ProcessMarkers(argv_words=("-p", "--output-format", "--settings"), env={})`.
- `auth_status(binary) -> RuntimeAuth`: runs `agy -p "/usage"` (no model turn) with a 20 s timeout. "Please sign in" → `signed_in=False`. Quota text → `True`. Anything else → `None`.

Tests (one per rule, fixtures under `tests/fixtures/agy/`, scrubbed): `test_validate_model_rejects_other_vendors_ids`, `test_agy_error_json_is_parsed`, `test_quota_text_is_a_usage_limit_with_reset`, `test_sign_in_text_is_auth`, `test_live_run_sees_an_agy_run_process` (extends `tests/unit/test_kb_live_run.py` with agy's argv), `test_auth_status_never_starts_a_model_turn` (the stub binary records its argv; only `-p /usage`).

### Task 2B.3: Register it

**Files:** `plugin/engine/scout/agent_runtimes/registry.py`, `plugin/engine/tests/unit/test_agent_runtimes_base.py`, `docs/runtimes.md` (a row: every cell "not yet").

- `_FACTORIES["antigravity"] = _antigravity`. `registry.ids() == ("antigravity", "claude-code")`.
- Registering is safe before 2E: `check` refuses it (`tier_too_low`, then `provider_not_consented`, then `local_only_unenforced`) until the probe proves its hooks and 2D lifts the last refusal. Test: `test_antigravity_is_refused_until_proven` walks those three codes in order.
- `agent-runtime list --json` shows it with `installed`, `version`, `signed_in`, `tier`, `consented`.

**2B done when:** golden argv pinned, `check` refuses agy with each code in turn, and `scoutctl agent-runtime exec --runtime antigravity --print-argv …` prints the golden argv.

---

## PR 2C: Gate translation and the generated gate config

### Task 2C.1: The protocol members

**Files:** `plugin/engine/scout/agent_runtimes/base.py`, `claude_code.py`, `tests/_agent_runtimes.py`.

**Interfaces (produced, used by 2C.2–2C.4 and Phases 3–4):**

```python
ToolKind = Literal["shell", "read", "search", "glob", "list", "write", "edit", "web", "mcp", "agent", "unknown"]

@dataclass(frozen=True)
class GatePayload:
    tool_kind: ToolKind
    tool_name: str                      # the runtime's own name, for messages and audit
    claude_payload: Mapping[str, Any]   # what decide() judges: {"tool_name", "tool_input", "cwd", "session_id"}
    cwd: Path

@dataclass(frozen=True)
class HookReply:
    stdout: str
    exit_code: int

class AgentRuntime(Protocol):
    def hook_in(self, event: str, raw: bytes) -> GatePayload: ...       # raises ValueError on a bad payload
    def hook_out(self, event: str, deny_reason: str | None, *, scheduled: bool) -> HookReply: ...
    def gate_config(self, ctx: GateContext) -> list[GeneratedFile]: ...

@dataclass(frozen=True)
class GateContext:
    scoutctl: Path       # the engine's launcher, as the hook command calls it
    home: Path

@dataclass(frozen=True)
class GeneratedFile:
    path: Path
    marker: str          # the managed block's begin/end tag
    content: str         # the block's body
```

Claude Code's members raise `NotImplementedError`: its hooks stay in `hooks.json`, byte-identical (G4). A test pins that `hooks.json` is unchanged.

### Task 2C.2: agy's `hook_in` and `hook_out`

**Files:** `plugin/engine/scout/agent_runtimes/antigravity_gate.py` (translation tables, kept out of the adapter so `python -m scout.hooks` imports it without the adapter's subprocess code), `tests/unit/test_agy_gate_translation.py`, fixtures `tests/fixtures/agy/hook-*.json` (scrubbed copies of findings §1.2's payload shape).

**Interfaces:**
- `TOOL_CLASSES: Mapping[str, tuple[ToolKind, str]]`: hook-side name → (kind, Claude tool name). `run_command` → (`shell`, `Bash`), `read_file` → (`read`, `Read`), `write_file` → (`write`, `Write`), `edit_file` → (`edit`, `Edit`), `search_web` → (`web`, `WebSearch`), `send_message`/`ask_question` → (`unknown`, …) only if 2A shows what they do. Otherwise unclassed. `run_workflow`, `swarm`, `invoke_subagent`, `define_subagent`, `manage_subagents` → (`agent`, …), denied in a run.
- `ARG_MAP`: `run_command.CommandLine` → `tool_input.command`; `read_file.Path` → `file_path`; `write_file.Path`/`Content` → `file_path`/`content`; `edit_file` per 2A's recorded args.
- `to_gate_payload(raw: bytes) -> GatePayload`: `cwd` from `payload["cwd"]`, else `workspacePaths[0]`, else `ValueError`. The session id is `conversationId`.
- `reply(deny_reason, *, scheduled) -> HookReply`: deny → `{"decision":"deny","reason": deny_reason}`, exit 0. Allow in a run → `{"decision":"auto_approve"}`. Allow outside a run → `{"decision":"allow"}`. Always one JSON line, never empty.

Tests: `test_every_fixture_round_trips_to_the_claude_verdict` (a table: each agy fixture and its Claude twin get the same `decide()` verdict), `test_an_unclassed_tool_is_denied_in_a_run`, `test_agent_starting_tools_are_denied_in_a_run`, `test_cwd_comes_from_the_payload_not_the_process`, `test_reply_is_never_empty`, `test_an_owner_session_gets_allow_not_auto_approve`.

### Task 2C.3: The one PreToolUse entry: `python -m scout.hooks pre-tool --agent-runtime antigravity`

**Files:** `plugin/engine/scout/hooks/pre_tool.py` (new), `scout/hooks/__main__.py` (`HOOKS["pre-tool"]`, argv now `[name, *flags]`), `tests/unit/test_hooks_pre_tool.py`.

**Interfaces:**
- `pre_tool.main(argv: list[str]) -> int`: reads stdin, `--agent-runtime` picks the translator (the only signal, spec §8.5), then runs the same gates Claude's `hooks.json` runs, in its order, on `GatePayload.claude_payload`: mcp-tool-gate (kind `mcp`), outbound-gate (kind `mcp`, write names), kb-write-gate (kind `write`/`edit`/`shell`), kb-read-gate (kind `shell`/`read`/`search`/`glob`/`list`). The first deny wins. A `kind == "unknown"` or `"agent"` call in a scheduled run is denied with `[scout:pre-tool]`.
- A watchdog (`signal.alarm`) at `PRE_TOOL_WATCHDOG_S = 20`, below the generated config's `timeout: 30`, prints a deny and exits 0.
- Any exception prints a deny. Nothing reachable imports `scout.cli` (`tests/unit/test_hooks_entry.py` covers the new module).

Tests: `test_pre_tool_denies_on_any_internal_error` (monkeypatch each gate to raise), `test_the_watchdog_denies_before_agys_timeout`, `test_the_gates_run_in_claudes_order`, `test_an_owner_session_is_never_gated_by_the_read_gate`, `test_mcp_tool_gate_applies_in_every_session`.

### Task 2C.4: `scoutctl agent-runtime install antigravity [--check]`

**Files:** `plugin/engine/scout/agent_runtimes/install.py`, `scout/cli.py` (`install` command), `scout/scripts/bootstrap_doctor.py` (the drift finding), tests `tests/unit/test_agent_runtime_install.py`.

**Interfaces:**
- `Antigravity.gate_config(ctx) -> [GeneratedFile(path=ctx.home/".gemini/config/hooks.json", marker="scout", content=…)]`. The content is one hook named `scout` with `{"enabled": true, "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "<scoutctl> hook pre-tool --agent-runtime antigravity", "timeout": 30}]}]}`. Merged into the file's JSON object under the key `scout`. Other keys are kept.
- `install(runtime, ctx, *, check: bool) -> InstallReport(changed: list[Path], drift: list[str])`. `--check` writes nothing and exits 1 on drift. Run as a scheduled run, it is refused (owner only, like `set-limit`).
- `check` (the refusal rule) gains rule 4b: an installed gate config that differs from `gate_config()` refuses with `version_unverified` ("Scout's hooks for agy aren't installed as Scout wrote them; run scoutctl agent-runtime install antigravity").
- `install --check` also reports any `<vault>/.agents/hooks.json`, `.gemini/` or `.codex/` hook file Scout didn't write (spec §8.5).

Tests: `test_install_writes_only_its_own_key`, `test_install_is_idempotent`, `test_check_reports_an_edited_block`, `test_a_scheduled_run_cant_install`, `test_check_refuses_a_run_on_drift`, `test_a_run_cant_write_agys_gate_config` (2C.3's entry, a `write_file` to the config path and a `run_command` that `rm`s it, both denied).

**agy's first start and its agentapi shim (Jordan, 2026-11-25; findings §7.1).** These come from 2A's stop-and-asks.
- **First start happens outside the sandbox. No carve-out in `~/.gemini/config`.** agy's first start in a `HOME` writes `~/.gemini/config/{.migrated,config.json,mcp_config.json,projects/default-cli-project.json}`. The run profile denies all of those, and a warm start writes none of them.
  - `install` starts agy once, outside the sandbox and without a model turn. This task picks the command with a throwaway-`HOME` spike.
  - `install --check` then checks that `projects/default-cli-project.json` exists.
  - 2E's per-version probe does the same after a self-update, so a version migration (`.migrated`) also happens outside a run.
  - A run that still meets a first start fails closed (agy exits 1), and run health shows it.
- **`bin/agentapi` is verified and restored around every agy run.** agy rewrites this shim (`exec <agy> agentapi "$@"`) before every shell call, so `~/.gemini/antigravity-cli/bin/` stays writable. A run could still replace it between agy's own writes.
  - At install and at each probe, Scout records the shim's checksum and keeps a copy where a run can't write it, under a protected path such as `~/.local/state/scout/`.
  - Before and after each agy run, the runner compares the file with that record. If it changed, the runner restores the recorded copy and reports the named finding `agent_runtime.agy_shim_restored` in run health. The finding carries no file contents.
  - If the restore can't happen, the run is skipped.
  - Tests: `test_a_changed_shim_is_restored_and_reported`, `test_an_unrestorable_shim_skips_the_run`, `test_the_shim_record_is_outside_the_runs_reach`.
- **`builtin/` is denied to runs (done in 2A: `profile.AGENT_DATA_REDENY`).** agy loads `builtin/skills` and `builtin/plugins` into every later session. That is vendor content outside review.
  - Scout's own skill edits go through the vault and the plugin's reviewed path.
  - If agy ever needs skills a run edited, Scout generates them at install time from its reviewed skills, outside the run (2I).

**2C done when:** the translation table test is green for every fixture, the generated config is drift-tested, and `hooks.json` is byte-identical.

---

## PR 2D: Privacy: local-only paths are withheld from non-Anthropic runs (spec §8.4)

### Task 2D.1: The read gate withholds local-only paths

- `kb_read_gate.decide()` gains a branch: when the run's provider (from `SCOUT_AGENT_RUNTIME` via `registry.get(...).provider`, in the *hook's* environment) isn't `anthropic`, a read, search, glob, list or shell call that names a local-only path (`scout.kb.privacy.classify(...) == "local-only"`) is denied. An unknown runtime id counts as non-Anthropic.
- The Tier L rule: for a runtime without `post_tool_context`, a raw `read` of a `knowledge-base/` note is denied in favour of `scoutctl kb read` (spec §8.3 Tier L). The adapter's verified capabilities decide this, not its id.
- Tests: `test_local_only_is_denied_to_a_google_run`, `test_local_only_stays_readable_to_claude_runs` (unchanged verdicts on the replay), `test_tier_l_denies_raw_kb_reads`, `test_an_unknown_runtime_counts_as_non_anthropic`.

- **agy's other transcripts (review of #814; findings §7.2).** A run can read and write `~/.gemini/antigravity-cli/conversations/` and `brain/`. In the owner's `HOME`, those hold the owner's interactive agy conversations and earlier runs' transcripts, and nothing gates them today. 2C's hook translation and this task must handle it, in whichever form the 7.2 design takes: either a run's `HOME` holds no other conversations (a per-run `HOME`), or the gates deny a run's read and write of any conversation but its own. Test: `test_a_run_cant_read_or_write_another_agy_conversation`.

### Task 2D.2: `scoutctl kb` leaves local-only paths out

- `scout/kb/reader.py` resolves `provider` the same way, from the process env. Because a run's shell child can set its own env, the read gate also denies a shell command that sets `SCOUT_AGENT_RUNTIME`, `SCOUT_READER` or `SCOUT_DATA_DIR` for a child (extend the existing `run_escape` handling).
- Tests: `test_kb_read_omits_local_only_for_google`, `test_kb_search_omits_local_only_for_google`, `test_a_child_cant_claim_another_runtime`.

### Task 2D.3: Lift `local_only_unenforced`

- `check.evaluate` drops the refusal and keeps `provider_not_consented`. The refusal-matrix test (`test_agent_runtime_check.py`) gains rows for `google` consented and not.
- `scoutctl privacy consent-provider google` prints the vendor terms summary from findings §1.7 before writing (owner only).

**2D done when:** the privacy scenario's seeds cover a local-only note and a `google` run, and the 50-run replay shows no Claude change.

---

## PR 2E: Conformance: the free probe and the real-login probe (spec §8.3 rule 4, §11.2)

### Task 2E.1: A fake Gemini endpoint

- `plugin/engine/scout/agent_runtimes/fake_gemini.py`: `FakeGemini` with the same shape as `FakeAnthropic` (`ScriptedTurn`, `issued`, loopback only). It serves the generateContent / streamGenerateContent calls agy makes under `modelProvider: "gemini"`. 2E's spike records which ones, and the fixture copies them.
- Tests: `tests/unit/test_fake_gemini.py` (request shapes from scrubbed fixtures; refuses non-loopback binds).

### Task 2E.2: `scoutctl agent-runtime probe antigravity`

- `scout/agent_runtimes/agy_probe.py`: `probe(binary, vault, *, plugin_root) -> AgyProbeResult(ok, reason, version, rows: dict[str, str])`. In a throwaway fixture vault and `HOME`, it runs agy with `--settings '{"modelProvider":"gemini"}'`, a dummy `GEMINI_API_KEY`, `GOOGLE_GEMINI_BASE_URL` → the fake, Scout's generated hooks under the throwaway `HOME`, and the real sandbox profile. The scripted turn makes four calls: `run_command cat knowledge-base/probe.md`, `read_file knowledge-base/probe.md`, `write_file ~/.gemini/config/hooks.json`, `run_command env`. It passes only if each was denied with Scout's mark, the canary never came back, and the hook saw `SCOUT_MODE` (`hook_env`).
- Failure-mode rows (findings §1.4) are re-measured with a test hook and recorded as pinned facts. A change in either direction fails the probe and names the row.
- The cache is `.scout-state/agent-runtime-probes.json`, keyed by runtime, version, hook code hash and gate config hash. `Antigravity.capabilities()` returns `DECLARED | proven` from the cache entry for this version, else `DECLARED` (so `check` refuses an unproven version).
- `check` runs the probe itself, for free, when the installed version has no cache entry (spec §8.3 rule 4).
- The probe runs agy's first start for a new version outside the sandbox, so a migration never happens inside a run. It also records `bin/agentapi`'s checksum for the runner's verify-and-restore (2C.4, Jordan 2026-11-25).
- Tests (stub agy that replays recorded stream-json and hook calls): `test_a_new_agy_version_reprobes`, `test_the_probe_fails_on_a_canary_leak`, `test_a_changed_failure_mode_row_fails_the_probe`, `test_capabilities_follow_the_cache`. A real-agy run is a CI job only where agy installs without a login (decide in this task, from 2A's notes).

### Task 2E.3: `probe antigravity --real-login` and `verified.json`

- Runs on the owner's login in the real `HOME`, once per install and again when the hook code hash or agy's auth mode changes (spec §8.3 rule 4, narrowed Q11). Pass, inconclusive and fail follow findings §4: the hook-side record decides. One retry on inconclusive, then refuse and show in the doctor.
- `plugin/engine/scout/agent_runtimes/verified.json`, generated by `python -m scout.scripts.verified_runtimes` from probe results, never hand-edited. Its real-login rows come only from Jordan's runs, with his OK per run.
- Tests: `test_real_login_never_runs_in_ci` (refuses under `CI=true`), `test_inconclusive_retries_once`, `test_verified_json_is_generated` (drift).

**2E done when:** the free probe passes against agy ≥ 1.5.9 locally and fails on each injected fault, and Jordan has OK'd and run one real-login probe.

---

## PR 2F: Usage, events and run health

### Task 2F.1: `parse_events` and `run_usage`

- `base.RunEvent` (closed set: `init`, `tool_call(name, kind, input)`, `tool_result(ok)`, `message`, `usage(tokens, model)`, `final(ok, reason, denied: int)`), `AgentRuntime.parse_events(lines) -> Iterator[RunEvent]`.
- agy's: `init`, `step_update` (`step_type == "tool"` → `tool_call` and `tool_result`), `result` → `usage(total_tokens)` and `final(status == "success", …, len(denied_actions))`.
- The runner tees agy's stdout to `.scout-logs/<run>.events.jsonl` (the wrapper already owns stdout). `scoutctl budget record-run` reads it for a non-Claude runtime and writes the usage row with `cost_kind: unpriced`, `tokens: total_tokens`.
- The run-outcome row gains `denied_actions` (int, left out when 0). Run health shows a yellow `agent_runtime.denied_calls` finding when a slot's last run had any.
- Tests: `test_stream_json_fixture_parses`, `test_unparseable_lines_are_skipped_and_counted` (spec §9 `agent_runtime.unparsed`), `test_denied_actions_are_counted`, `test_usage_row_is_unpriced`. Claude's Stop-hook accounting is unchanged.

### Task 2F.2: Per-runtime back-off

- `classify_failure`'s `usage_limit(resets_at)` feeds the existing back-off keyed by runtime (`budget_check.py`), so an agy quota hit doesn't pause Claude slots. Test: `test_a_google_quota_hit_pauses_only_agy_slots`.

---

## PR 2G: Neutral connector keys (contract change, ONE commit; spec §8.8)

- `plugin/engine/scout/connectors.yaml`: keys become neutral (`slack`, `gmail`, `google-calendar`, `linear`, …). `aliases:` keep `mcp:claude_ai_*` and `mcp:plugin_*`.
- Regenerate from `plugin/engine`: `.venv/bin/python -m scout.scripts.connectors_snapshot --no-also-write-app-fixture`, then `cp` the canonical snapshot over every client copy `contract.yml` checks. Add aliases in `apps/macos/Scout/Models/ConnectorKeyAlias.swift` and tests in `ConnectorKeyAliasTests.swift`. One commit; `contract.yml` is the gate.
- `scripts/connector_detect.py` asks the slot's runtime: for agy, `.agents/mcp_config.json` in the vault and `~/.gemini/config/mcp_config.json`. Connector health records `runtime` per row.
- The slot editor warning for `required_in_types` connectors missing on the chosen runtime comes in 2J.

## PR 2H: Eval on agy

- `plugin/engine/tests/eval/harness/antigravity_shim.py`, following `claude_shim.py`'s contract: isolated `HOME` and config, stub MCP servers only, the sandboxed shell, and an init guard that refuses to start when the real `~/.gemini/config` would load.
- `scripts/eval.sh kb-gate --agent-runtime antigravity` and `compare-runtimes` (generalising `compare-models`). Both are local only, on Jordan's AI Pro login, with his OK per run.
- `eval/harness/metrics.py` consumes `RunEvent` for agy, and keeps Claude's path.

## PR 2I: Skills and commands for agy (spec §8.10)

- Generator `python -m scout.scripts.runtime_skills` writes `plugin/runtimes/antigravity/skills/<name>/SKILL.md` for `scout-ask`, `scout-work`, `scout-status`, `scout-briefing`, `scout-consolidation`, `scout-dream` and `scout-research`, mapping `$ARGUMENTS` and replacing `${CLAUDE_PLUGIN_ROOT}` with `scoutctl` on PATH. `tests/unit/test_runtime_skills_drift.py` fails on drift.
- `agent-runtime install antigravity` links `~/.agents/skills/scout-*` → the engine checkout's tree through the engine pointer, and writes the managed `AGENTS.md` pointer into the vault (keep/merge/park rules apply).
- `scout-ask` and `scout-work` call `scoutctl agent-runtime check --interactive` first. agy isn't an `interactive_host`, so the check runs in full there.
- Watch the `SKILL.md` token pin (`memory: action-items-size-budget-status`): the generated copies don't count toward it, but the sources do.

## PR 2J: The app

- Onboarding: "at least one supported agent runtime installed and signed in", from `scoutctl agent-runtime list --json`. Claude Code stays first and recommended (`OnboardingViewModel.swift`).
- `SlotEditForm.swift`: a runtime picker beside the model picker. Model options come from `scoutctl agent-runtime models --runtime <id> --json`. Refused runtimes show the refusal reason. `ModelChoice.swift`'s hardcoded list stays the Claude fallback.
- A contract fixture `agent-runtime-list.json` (generated, `SCOUT_REGEN_CONTRACT=1`), copied to `apps/macos/ScoutTests/Fixtures/`, with pins in a new `AgentRuntimeListContractTests.swift` and a `contract.yml` step. One commit with the engine side.
- App suites: derive with `memory: app-affected-suites-include-guards`, every guard suite included.

## PR 2K: Acceptance and the evidence row (spec §10, §13)

Every box below that runs a real model waits for the 7.2 gate (decision 11), and so do 2E.3 and 2H's runs.

- [ ] The free probe passes at Tier L for a pinned agy version (CI where possible, else Jordan's Mac).
- [ ] One real-login probe on Jordan's AI Pro login (his OK).
- [ ] `eval.sh kb-gate --agent-runtime antigravity` passes (his OK).
- [ ] The privacy scenario passes on agy, scored by `privacy_hygiene` (his OK).
- [ ] One dreaming or research slot runs on agy on Jordan's vault for a week, with run health green, no `agent_runtime.refused` or `agent_runtime.fallback`, and no gate-error events. Jordan switches the slot himself (`memory: live-setup-stays-dev-managed`).
- [ ] `docs/runtimes.md` gains agy's filled row. The README's "planned" line changes only in a Phase 6 PR that cites that row (spec §13).

---

## Decisions this plan makes (check while reviewing)

1. **Translate into Claude-shaped payloads.** `decide()` keeps judging `{"tool_name": "Bash" | "Read" | …}`. That puts every existing gate test and the 50-run replay behind agy too. The alternative, teaching each gate a `tool_kind`, touches every gate for no gain in Phase 2.
2. **One PreToolUse entry for agy** (`hook pre-tool`), which runs the gates in `hooks.json`'s order. agy's matcher is a regex over names it may rename, so per-gate matchers would be a second allowlist to keep in step.
3. **agy can't update itself during a run.** The sandbox's PATH rule already denies it. The next run sees the new version and probes it first.
4. **The runner's Gemini keys never reach agy** (the #806 env allowlist, so the adapter lists nothing in `env_unset`). A run uses the owner's login only, so a stray `GEMINI_API_KEY` can't send the vault to a different account or a paid key.
5. **The local-server deny stays on** even if 2A finds no bypass. Lifting it is its own PR.
6. **`default_model()`** is fixed in 2B from `agy models`, not guessed here.
7. **Denied calls are counted, not failed.** agy exits 0 after a deny (findings §1.1), so the run stands and health shows the count.
8. **agy's first start never happens in a run** (Jordan, 2026-11-25). `install` and the per-version probe start agy outside the sandbox. There is no carve-out in `~/.gemini/config`, and a run that meets a first start fails closed (2C.4, 2E.2).
9. **A run can't write agy's `builtin/` skills and plugins** (Jordan, 2026-11-25). They reach every later agy session, and they are vendor content outside review (2A, `profile.AGENT_DATA_REDENY`).
10. **`bin/agentapi` is verified and restored around each agy run** (Jordan, 2026-11-25). agy needs to write it. A change is restored and reported; a failed restore skips the run (2C.4).
11. **Real runs are blocked on the 7.2 isolation design** (Jordan, 2026-11-25). A run's shell child gets a token that still reaches agy's conversation methods and other conversations' transcripts (findings §7.2). It is still open whether a conversation started that way runs its tool calls through the hook. So no agy run that calls a real model happens (2E.3, 2H, 2K, any scheduled slot) until Jordan OKs a design that stops a run's shell commands from reaching agy's API.
    - Candidates: a per-run `HOME` with no owner conversations; the sandbox denying shell children the language server's port or socket; an agy option that keeps the token out of children's environment.
    - Building and testing against the fake endpoint goes on. The text-matching deny from 2A stays on either way.
    - The design must assume a run can reach the server's port. The run profile allows loopback TCP, so a command that prints the environment once and then calls the server with the literal token gets past the text match.
    - It must also cover agy's data-dir transcripts. A run can read and write `~/.gemini/antigravity-cli/{conversations,brain}/`, which in the owner's `HOME` hold the owner's interactive conversations and earlier runs (findings §7.2). That is the same exposure as the server's trajectory reads, without the server. A per-run `HOME` would cover it.
