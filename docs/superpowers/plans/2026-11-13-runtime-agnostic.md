# Runtime-agnostic Scout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scout's scheduled runs and interactive skills run on agent CLIs other
than Claude Code, behind one adapter interface, with every privacy gate
enforced or the run refused.

**Two tracks:**

- **Track A** is Scout's own sessions on Codex, Google's CLI and maybe
  Grok Build. Phases 0–4 and 6 belong to it, and this plan owns them.
- **Track B** is Grokbot and Claude Duet consuming Scout through #550's
  delegate mirror (#625, #632) plus `scoutctl`. Phase 5 tracks it, but
  #550's plan owns it.

**Architecture:** A new engine package `scout/agent_runtimes/` holds one
adapter per local CLI, behind a protocol with declared and verified
capabilities. The shell runners keep lock, lease, retry and liveness. They
resolve the runtime with `scoutctl agent-runtime resolve`, gate the run with
`agent-runtime check`, and launch through `agent-runtime exec`, which
`exec`s the CLI. Claude Code moves behind the interface first, with
byte-identical argv and hooks.

**Tech Stack:** Python 3.12 engine (typer, pytest, ruff, mypy), bash runner
templates, SwiftUI app (Phase 2+), GitHub Actions (no model runs).

**Spec:** `docs/superpowers/specs/2026-11-13-runtime-agnostic-design.md` ("spec §n").

**How the coordinator uses this plan:**

- **Phase 1a** is detailed and can start now. It is urgent and independent
  of the rest (spec §3.5).
- **Phase 1** is detailed and starts on Jordan's approval of the spec. It
  does not depend on the open questions.
- **Phases 0 and 2–6** are card-level. Each needs Phase 0's answers, and gets
  its own detailed plan (`…-phase-N.md`) when its card is cut. Writing
  their task-level steps now would mean guessing hook payloads that Phase 0
  exists to observe.

## Global Constraints

- Claude Code stays the default. Its runner argv stays exactly
  `--permission-mode auto --model "$SCOUT_MODEL" --max-budget-usd {{MAX_BUDGET}} --name "$RUN_NAME" -p "$PROMPT"`,
  in that order, and `plugin/hooks/hooks.json` doesn't change in Phase 1
  (spec G4).
- No gate is weakened. A refusal fails closed, and a refused runtime never
  falls back to Claude Code (spec §8.3).
- Resolve failure falls back to `claude-code`. Check failure (or "can't
  evaluate") skips the run with `scout_record_skip refused_runtime` (spec §8.2).
- New config key names: slot `agent_runtime`, config `agent_runtimes.default`,
  env `SCOUT_AGENT_RUNTIME`, runtime ids `claude-code`, `codex`,
  `antigravity`, `gemini`, `grok`. Never reuse `Slot.runtime` (spec §8.1).
- Ledger keys `agent_runtime`, `provider`, `cost_source` ∈ `{reported, priced, unpriced}`.
  Absent means Claude Code (spec §8.6).
- `privacy.providers` defaults to `[anthropic]` (spec §8.4).
- No paid API spend, no API keys in tests or CI, no model in CI. Model-backed
  runs are local, on Jordan's login, with his OK per run (repo rule).
- Never run a test that does `bootstrap install` without `--no-jobs`, and
  never touch `~/Scout`, `~/scout-plugin` or the live launchd jobs.
- Local tests: affected files only. CI is the gate. Before pushing engine
  changes run, from `plugin/engine`: `ruff check .`, `ruff format --check .`,
  `.venv/bin/mypy scout`, `.venv/bin/python -m scout.scripts.versioning check`.
- Fixtures are anonymised (plugin/CLAUDE.md stand-ins). No vault content in
  code, tests, commits or PRs.
- Runner templates are managed files. A template change ships through
  `/scout-update`'s keep/merge/park. Check `tests/unit/test_template_manifest.py`
  and `test_template_backport.py` on every template edit.
- One version, one tag. No version bumps in these PRs; `release.sh` does them.

## Review Focus

1. **A Claude Code update that turns on `--bare` for `-p`.** A run must be
   refused before it starts, not after it has read the vault ungated
   (Task 1a.3).
2. **A probe that can't run** (port in use, `claude` missing, fake server
   crash). The run is refused with a reason, never allowed (Task 1a.2,
   `test_probe_infrastructure_failure_is_refusal`).
3. **An old or hand-edited vault runner without the new lines.** It keeps
   running Claude exactly as before, and `agent-runtime check` is never
   needed for it to work (Task 1.4, `test_unrendered_old_runner_still_launches_claude`).
4. **A slot naming an unknown runtime, or a model that doesn't fit its
   runtime.** Schedule validation rejects it, naming the slot, the runtime
   and the value; it doesn't reach the runner (Task 1.3).
5. **A ledger row from before this change** (no `agent_runtime` key). Health,
   budget and the app parse it as Claude Code (Task 1.6,
   `test_legacy_rows_default_to_claude_code`).

---

## Phase 1a — Bare-mode guard for Claude Code (urgent)

Why: Claude Code's headless docs say `--bare` "will become the default for
`-p` in a future release". Bare mode loads no hooks, plugin hooks included,
and there is no documented opt-out (spec §3.5). A Claude update could
silently switch off every Scout gate in scheduled runs.

Approach:

- **Prevent.** Before each run, a free, deterministic **probe** launches
  `claude -p` with the run's own flags against a local fake Anthropic
  endpoint (`ANTHROPIC_BASE_URL`). The fake model's scripted turn asks for a
  Bash read of a KB file in a throwaway fixture vault. The probe passes only
  if Scout's kb-read-gate denied it.
- **Cache.** The result is cached by Claude version + `hooks.json` sha256 +
  flag set, so a normal run pays nothing.
- **Detect.** A run without a passing probe is refused (`skipped_gates`) and
  raises a run-health event.

### Task 1a.1: Fake Anthropic Messages endpoint

**Files:**
- Create: `plugin/engine/scout/agent_runtimes/__init__.py` (empty package marker)
- Create: `plugin/engine/scout/agent_runtimes/fake_anthropic.py`
- Test: `plugin/engine/tests/unit/test_fake_anthropic.py`

**Interfaces:**
- Produces:
  - `class ScriptedTurn(NamedTuple): tool_name: str; tool_input: dict[str, Any]`
  - `class FakeAnthropic: def __init__(self, script: list[ScriptedTurn]) -> None`
  - `def start(self) -> str`: binds 127.0.0.1 on port 0, returns the base URL.
  - `def stop(self) -> None`
  - `property tool_results: list[dict[str, Any]]`: every `tool_result` block
    the client sent back.

Uses stdlib `http.server` in a thread. It answers `POST /v1/messages`
(streaming SSE when `stream: true`, else JSON):

- the first request with one `tool_use` per scripted turn;
- later requests with a final `end_turn` text "done".

Any other path returns a 200 `{}` (for example the CLI's metadata calls).

- [ ] **Step 1: Write the failing tests**

```python
def test_first_request_returns_scripted_tool_use(): ...
    # POST /v1/messages {"stream": false, ...} → content[0] == {"type":"tool_use","name":"Bash","input":{"command":"cat knowledge-base/probe.md"}, "id": <str>}
def test_streaming_request_emits_message_start_and_tool_use_block(): ...
    # SSE events include message_start, content_block_start(type=tool_use), message_stop
def test_second_request_records_tool_result_and_ends_turn(): ...
    # tool_results[0]["is_error"] is True when the client sent is_error true; response stop_reason == "end_turn"
```

- [ ] **Step 2: Run `cd plugin/engine && .venv/bin/python -m pytest tests/unit/test_fake_anthropic.py -v`.** Expected: FAIL (module missing).
- [ ] **Step 3: Implement `FakeAnthropic` in `fake_anthropic.py`.**
- [ ] **Step 4: Re-run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(agent-runtimes): scripted fake Anthropic endpoint for gate probes"`

### Task 1a.2: `scoutctl gates probe`

**Files:**
- Create: `plugin/engine/scout/agent_runtimes/gate_probe.py`
- Modify: `plugin/engine/scout/cli.py` (new `gates_app = typer.Typer(...)`, `app.add_typer(gates_app, name="gates")`, registered near `hook_app` at :84-85)
- Test: `plugin/engine/tests/unit/test_gate_probe.py`

**Interfaces:**
- Consumes: `FakeAnthropic`, `ScriptedTurn` (Task 1a.1).
- Produces:
  - `@dataclass(frozen=True) class ProbeResult: ok: bool; reason: str; claude_version: str | None; hooks_sha256: str; flags: tuple[str, ...]; checked_at: str`
  - `def probe(claude_bin: Path, *, plugin_root: Path, flags: Sequence[str], timeout_s: float = 60.0) -> ProbeResult`
  - `def cached_probe(vault: Path, claude_bin: Path, *, plugin_root: Path, flags: Sequence[str]) -> ProbeResult`:
    reads and writes `<vault>/.scout-state/gate-probe.json`, and re-probes
    when the version, `hooks.json` sha256 or flags differ.
  - CLI: `scoutctl gates probe --claude-bin PATH [--cached] [--json]`.
    Exit 0 = gates live; exit 3 = refused (reason on stderr).

The probe:

1. builds a temp fixture vault (`scout-config.yaml`, `knowledge-base/probe.md`
   holding a canary line);
2. sets `SCOUT_DATA_DIR` to it, `SCOUT_MODE=probe`,
   `SCOUT_READER=autonomous`, `ANTHROPIC_BASE_URL` to the fake;
3. runs `claude <flags> --no-session-persistence -p "probe"` with cwd = the
   fixture vault and the scripted turn `Bash: cat knowledge-base/probe.md`;
4. returns `ok=True` only when the fake saw a `tool_result` with
   `is_error: true` whose text contains `[scout:kb-read-gate]` or the gate's
   deny reason, *and* never saw the canary text.

Any exception, timeout, or non-start returns `ok=False` with the reason.

- [ ] **Step 1: Write the failing tests** (they use a stub `claude` script under `tmp_path` that plays the client side against the fake, as `scripts/e2e/self-update/bin/claude` stubs the CLI):

```python
def test_probe_ok_when_gate_denies(tmp_path): ...            # stub posts tool_result is_error=True "[scout:kb-read-gate] ..." → ok True
def test_probe_refused_when_tool_ran(tmp_path): ...          # stub posts the canary text as a successful result → ok False, "gates not loaded" in reason
def test_probe_infrastructure_failure_is_refusal(tmp_path): ...  # claude_bin missing / exits 1 / hangs past timeout → ok False
def test_cached_probe_reprobes_on_version_or_hooks_change(tmp_path): ...
def test_cli_exit_codes(tmp_path): ...                        # 0 on ok, 3 on refusal
```

- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement `probe`, `cached_probe` and the CLI command.**
- [ ] **Step 4: Run the tests, then `ruff check`, `ruff format --check`, `mypy scout`.** Expected: PASS.
- [ ] **Step 5: Manual check, real CLI, no spend** (the request goes to the fake, not Anthropic):
  `cd plugin/engine && .venv/bin/scoutctl gates probe --claude-bin "$(command -v claude)" --json`.
  Expected: `"ok": true`. Repeat with `--bare` added to the flags (a
  test-only `--extra-flag` option): expected `"ok": false`. Record both
  outputs in the PR. If `ANTHROPIC_BASE_URL` isn't honoured with an OAuth
  login, stop and report to the coordinator. The design then needs another
  probe transport.
- [ ] **Step 6: Commit.** `git commit -m "feat(gates): probe that Scout's hooks fire under the run's claude flags"`

### Task 1a.3: Runners refuse a run whose gates aren't live

**Files:**
- Modify: `plugin/templates/run-scout.sh.tmpl` (after the budget check, :134-142; before model resolve, :144), `plugin/templates/run-dreaming.sh.tmpl`, `plugin/templates/run-research.sh.tmpl` (same place)
- Modify: `plugin/engine/scout/health.py` (new finding `gates.not_loaded`)
- Test: `plugin/engine/tests/unit/test_runner_gate_probe.py`; extend `plugin/engine/tests/integration/test_run_scout_mode_export.py` with a refused case

**Interfaces:**
- Consumes: `scoutctl gates probe --cached` (Task 1a.2).
- Produces: skip reason `skipped_gates` in `run-outcomes.jsonl`; health finding id `gates.not_loaded`.

Block to add (identical in all three runners):

```bash
# Scout's privacy gates are Claude Code hooks. A claude that doesn't load them
# (bare mode, spec 2026-11-13 §3.5) must not run against the vault.
if [ -x "$SCOUTCTL_BIN" ] && ! "$SCOUTCTL_BIN" gates probe --cached --claude-bin "$CLAUDE_BIN" >> "$LOG_FILE" 2>&1; then
    echo "=== Gate probe failed: Scout's hooks are not loaded — skipping this run ===" >> "$LOG_FILE"
    scout_record_skip skipped_gates
    exit 0
fi
```

A vault without `scoutctl` keeps today's behaviour. That is the existing
convention for optional engine steps; `bootstrap doctor` already flags a
missing scoutctl.

- [ ] **Step 1: Write the failing tests:**
  - `test_each_runner_probes_before_launch` (static order: probe block after the lock and before the claude launch);
  - `test_refused_probe_records_skipped_gates_and_never_launches` (integration: stub scoutctl exits 3; stub claude must not be called);
  - `test_health_reports_gates_not_loaded`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Add the block to the three templates. Add the health finding**, with the fix text "Update Scout (a newer plugin may know this Claude version) or pin Claude Code to the last version whose probe passed; see `scoutctl gates probe --json`."
- [ ] **Step 4: Run the new tests, `test_template_manifest.py`, `test_template_backport.py`, `test_runner_exports_scout_mode.py` and `test_privacy_runners.py`.** Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(runners): refuse a run when the gate probe fails (bare-mode guard)"`

### Task 1a.4: Doctor shows the probe

**Files:**
- Modify: `plugin/engine/scout/scripts/bootstrap_doctor.py` (beside `_check_runner_claude_bin`, :369-391, and its call at :775-779)
- Test: `plugin/engine/tests/unit/test_bootstrap_doctor_checks.py`

**Interfaces:**
- Consumes: `cached_probe` (Task 1a.2).
- Produces: doctor warning `gate probe failed: <reason>` (YELLOW), and a note with the cached probe's version and date.

- [ ] **Step 1: Write the failing tests:** `test_doctor_warns_when_cached_probe_failed` and `test_doctor_notes_probe_version_when_ok`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement it, reading only the cache.** The doctor never launches claude.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.** Open PR "fix(gates): refuse scheduled runs when Claude Code doesn't load Scout's hooks (bare mode)" with Tasks 1a.1–1a.4.

## Phase 1b — Claude Write/Edit hardening (card; separate privacy fix)

- **Gap:** a run's Write/Edit into `.git/hooks`, `.git/config` or
  `<vault>/.claude/settings.json` isn't gated today. #607 covers Bash only:
  `hooks.json:26` matches `Grep|Glob|Bash`, and `kb_read_gate.decide()`
  allows Write/Edit (:186-187).
- **Fix:**
  - add a `Write|Edit|MultiEdit|NotebookEdit` matcher whose shell
    prefilter starts Python only when the payload names `.git/` or an
    agent-config dir;
  - `decide()` denies those paths for runs, with the event
    `kb_read_gate.hook_skip`;
  - unit tests in `test_hooks_kb_read_gate.py`.
- It ships as its own fix, before Phase 1, so Phase 1's byte-identical
  `hooks.json` golden pins the hardened file.

---

## Phase 1 — The adapter interface, Claude Code behind it

Done when:

- the golden argv test passes for all three runners;
- `hooks.json` is unchanged;
- CI is green;
- one week of Jordan's live runs shows green run health;
- `eval.sh kb-gate` passes on Claude through the new path, run with Jordan's
  OK (spec §12).

### Task 1.1: Types, protocol and registry

**Files:**
- Create: `plugin/engine/scout/agent_runtimes/base.py`, `plugin/engine/scout/agent_runtimes/registry.py`
- Test: `plugin/engine/tests/unit/test_agent_runtimes_base.py`

**Interfaces:**
- Produces (all frozen dataclasses unless noted):
  - `Capability(enum.StrEnum)` with exactly the values `pre_tool_block`,
    `pre_tool_fail_closed`, `hook_env`, `tool_coverage`,
    `gate_config_outside_session`, `post_tool_context`, `mcp_stdio`,
    `mcp_tool_allowlist`, `path_deny`, `headless_events`, `usage_tokens`,
    `cost_estimate`, `budget_cap`, `custom_endpoint`, `skills`, `commands`,
    `context_file` (spec §8.3).
  - `Tier(enum.StrEnum)`: `F`, `L`, `S`, `D`.
  - `def tier_for(caps: frozenset[Capability]) -> Tier`, implementing the
    spec §8.3 rules. **S** requires `path_deny` and is returned only when
    the F/L rules fail.
  - `RunRequest(vault: Path, mode: str, run_name: str, prompt: str, model: str, budget_usd: float | None, reader: str)`
  - `Launch(argv: tuple[str, ...], env: Mapping[str, str], stdin: str | None)`
  - `FailureKind(enum.StrEnum)`: `transient`, `auth`, `usage_limit`, `budget`, `other`;
    `Failure(kind: FailureKind, resets_at: datetime | None, detail: str)`
  - `ProcessMarkers(argv_words: tuple[str, ...], env: Mapping[str, str])`
  - `Usage(input_tokens: int | None, output_tokens: int | None, cost_usd: float | None, cost_source: Literal["reported", "priced", "unpriced"], model: str | None)`
  - `class AgentRuntime(Protocol)`: the members in spec §8.2, minus
    `gate_config`, `hook_in`, `hook_out`, `parse_events` and
    `session_store`, which arrive in Phase 2 with their first non-Claude
    user (YAGNI).
  - `registry.get(runtime_id: str) -> AgentRuntime` (`KeyError` names the known ids);
    `registry.ids() -> tuple[str, ...]`; `BUILTIN_DEFAULT = "claude-code"`.

- [ ] **Step 1: Write the failing tests:**
  - `test_tier_f_requires_all_six`;
  - `test_tier_l_is_f_without_post_tool_context`;
  - `test_tier_s_needs_path_deny`;
  - `test_anything_else_is_tier_d`;
  - `test_registry_unknown_id_names_known_ids`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 1.2: The `claude-code` adapter

**Files:**
- Create: `plugin/engine/scout/agent_runtimes/claude_code.py`
- Modify: `plugin/engine/scout/agent_runtimes/registry.py` (register it)
- Test: `plugin/engine/tests/unit/test_agent_runtime_claude_code.py`

**Interfaces:**
- Consumes: Task 1.1 types; `scout.models.validate_model`, `scout.models.BUILTIN_DEFAULT`;
  `scout.scripts.bootstrap.resolve_claude_bin`; `scout.claude_auth`;
  `scout.scripts.run_cost.measure`; the gate probe cache (Task 1a.2).
- Produces: `ClaudeCode` with `id="claude-code"`, `provider="anthropic"`.
  - `headless_argv(req)` returns
    `(bin, "--permission-mode", "auto", "--model", req.model, "--max-budget-usd", <cap>, "--name", req.run_name, "-p", req.prompt)`.
  - `process_markers(req)` returns argv words `("--name", "-p", "--max-budget-usd")`
    and env `{"CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1"}`.
  - `classify_failure()` matches the wrapper's `TRANSIENT_PATTERNS`,
    `AUTH_PATTERNS` and `USAGE_LIMIT_PATTERN`
    (`claude-with-retry.sh.tmpl:77, 85, 89`), plus `rate_limit_reset`'s
    parser.
  - `capabilities()` returns the full Claude set when the cached gate probe
    passed for this version, else the set without the hook capabilities.
    Tier D, so `check` refuses (that ties 1a to the refusal rule).

- [ ] **Step 1: Write the failing tests:**
  - `test_headless_argv_is_todays_runner_line` (golden tuple);
  - `test_cap_formatting_matches_template` (`{{MAX_BUDGET}}` renders, e.g. `"5"` / `"2.50"`, as the template does);
  - `test_classify_failure_patterns` (one case per wrapper pattern, using the template's own pattern strings read from the file so they can't drift);
  - `test_capabilities_drop_hooks_when_probe_failed`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 1.3: Config and validation: `agent_runtime`, `agent_runtimes.default`, per-runtime models

**Files:**
- Modify: `plugin/engine/scout/schedule.py` (`Slot` gains `agent_runtime: str | None = None`; `_build_slot` validates, next to `model` at :252-266)
- Modify: `plugin/engine/scout/models.py` (`validate_model(value, *, where, runtime="claude-code")` dispatches to the adapter; the Claude rule is unchanged)
- Create: `plugin/engine/scout/agent_runtimes/resolve.py`
- Modify: `plugin/engine/scout/defaults/scout-config.yaml` (commented `agent_runtimes:` block after `models:` at :63-69)
- Test: `plugin/engine/tests/unit/test_agent_runtime_resolve.py`; extend `tests/unit/test_models.py`

**Interfaces:**
- Produces:
  - `ResolvedRuntime(runtime: str, source: Literal["env", "slot", "config", "builtin"])`
  - `def resolve_runtime(data_dir: Path | None, slot_key: str | None) -> ResolvedRuntime`
    (spec §8.7 precedence).
  - `def resolve_model_for(data_dir, slot_key, runtime) -> ResolvedModel`:
    for `claude-code` this is exactly `models.resolve_model`; for others it is
    the slot's `model:`, then `agent_runtimes.<id>.model`, then the adapter's
    `default_model()`.

- [ ] **Step 1: Write the failing tests:**
  - `test_precedence_env_slot_config_builtin`;
  - `test_unknown_runtime_in_slot_is_schedule_error_naming_slot_and_value`;
  - `test_model_must_fit_slot_runtime`;
  - `test_claude_validation_unchanged` (every existing `test_models.py` case passes untouched);
  - `test_slot_runtime_local_remote_field_untouched`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** `agent_runtime` stays out of `schedule_snapshot` (spec §8.7). Assert that in a test: `test_snapshot_fields_unchanged`.
- [ ] **Step 4: Run the new tests plus `test_models.py`, `test_models_config.py`, `test_cli_model.py` and the schedule tests.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 1.4: `scoutctl agent-runtime` and runner plumbing

**Files:**
- Create: `plugin/engine/scout/agent_runtimes/cli.py` (registered in `scout/cli.py` like `model_app` at :2186-2194)
- Create: `plugin/engine/scout/agent_runtimes/check.py`
- Modify: the three runner templates (the model-resolve block at `run-scout.sh.tmpl:144-155` and the launch at :168-177, with the same spots in dreaming and research)
- Test: `plugin/engine/tests/unit/test_agent_runtime_cli.py`, `test_agent_runtime_check.py`; integration `plugin/engine/tests/integration/test_runner_agent_runtime.py`

**Interfaces:**
- Consumes: Tasks 1.1–1.3; `privacy.providers` (read here; default `["anthropic"]`).
- Produces:
  - `scoutctl agent-runtime resolve --slot KEY [--json]` prints the id.
  - `scoutctl agent-runtime check --slot KEY [--interactive] [--json]`:
    exit 0 allow, exit 3 refuse, reason on stderr.
  - `scoutctl agent-runtime exec --runtime ID --model M --max-budget-usd C --name N -p PROMPT`:
    `os.execvpe`s the adapter's argv with its `process_markers().env` added.
    The flag names are literal on purpose: the retry wrapper reads and
    rewrites `--max-budget-usd` and `--name` (`claude-with-retry.sh.tmpl:226-235, 362-370`, #561).
  - `scoutctl agent-runtime list|show [--json]`.
  - `def check(vault: Path, runtime: AgentRuntime, *, reader: str, mode: str | None, interactive: bool) -> Refusal | None`,
    where `Refusal(code: str, reason: str)`. The codes are
    `tier_too_low`, `restrict_tools_unenforceable`,
    `provider_not_consented`, `version_unverified` and `check_failed`
    (spec §8.3 rules 1–5).

Runner changes (all three):

1. Resolve `SCOUT_AGENT_RUNTIME` (fallback `claude-code`, logged).
2. Run `check`; on a non-zero exit, `scout_record_skip refused_runtime` and
   exit 0.
3. Log `=== agent runtime: $SCOUT_AGENT_RUNTIME ===`.
4. Launch `claude-with-retry.sh "$LOG_FILE" "$SCOUTCTL_BIN" agent-runtime exec --runtime "$SCOUT_AGENT_RUNTIME" --model "$SCOUT_MODEL" --max-budget-usd {{MAX_BUDGET}} --name "$RUN_NAME" -p "$PROMPT"`.
5. Keep the old direct-`claude` launch as the `else` branch when
   `$SCOUTCTL_BIN` isn't executable.

`claude-with-retry.sh` rewrites `--max-budget-usd` on retries, and
`record-attempt` finds the run by `--name` (:222-249, :362-370). Pin both
with a test: the wrapper's rewrite must reach the exec'd CLI.

- [ ] **Step 1: Write the failing tests:**
  - `test_exec_argv_for_claude_code_equals_golden` (patch `os.execvpe`, assert args);
  - `test_exec_honours_retry_rewritten_cap`;
  - `test_check_refusal_matrix`: parametrised tier × reader × vault {plain, has `restrict_tools`} × providers × verified-version, against a dummy Tier-D adapter registered in the test;
  - `test_check_error_is_refusal`;
  - `test_runner_resolve_failure_falls_back_to_claude_code`;
  - `test_runner_check_refusal_records_refused_runtime_and_never_launches`;
  - `test_unrendered_old_runner_still_launches_claude`;
  - `test_live_run_still_matches_exec_launched_claude` (the `ps` line after `exec` carries `--name -p --max-budget-usd` and the vault path; reuse `test_kb_live_run.py` helpers).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement the CLI, `check` and the template edits.**
- [ ] **Step 4: Run the new tests and `test_runner_exports_scout_mode.py`, `test_privacy_runners.py`, `test_kb_live_run.py`, `test_kb_hook.py`, `test_template_manifest.py`, `test_template_backport.py`, `test_runner_wrap_channel_neutral.py`.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 1.5: Liveness reads markers from the adapters

**Files:**
- Modify: `plugin/engine/scout/kb/live_run.py:200-217` (the hard-coded `--name`/`-p`/`--max-budget-usd` test becomes "any registered adapter's `process_markers` argv words, all present, plus a vault path")
- Modify: `plugin/engine/scout/kb/hook.py:65-77` (the awk twin: generate its word list from the same source, or leave the Claude words and add a test that both lists agree for `claude-code`)
- Modify: `plugin/engine/scout/scripts/heartbeat.py:283` (`pgrep` pattern per registered adapter)
- Test: extend `plugin/engine/tests/unit/test_kb_live_run.py`, `test_kb_hook.py`

**Interfaces:**
- Consumes: `registry.ids()`, `AgentRuntime.process_markers`.

- [ ] **Step 1: Write the failing tests:** `test_markers_come_from_registry` (a dummy adapter's markers are recognised) and `test_claude_markers_unchanged` (today's fixtures still classify identically).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 1.6: Ledgers name the runtime

**Files:**
- Modify: `plugin/templates/scripts/run-outcome.sh.tmpl` (add `"agent_runtime": $SCOUT_AGENT_RUNTIME` when set, as `model` is), `plugin/engine/scout/cli.py:1015-1087` (`budget record-run`/`record-attempt` rows gain `agent_runtime`, `provider`, `cost_source: "reported"` for Claude transcript cost, `"priced"` for the usage fallback)
- Modify: `plugin/engine/scout/health.py` (`_parse_row` defaults `agent_runtime` to `claude-code`), `plugin/engine/scout/scripts/budget_check.py` (unchanged maths; reads the new keys only to group usage limits per runtime)
- Test: `plugin/engine/tests/unit/test_run_cost.py`, `test_budget_cli.py`, `test_budget_check.py`

- [ ] **Step 1: Write the failing tests:** `test_record_run_writes_runtime_keys`, `test_legacy_rows_default_to_claude_code` and `test_usage_limit_backoff_is_per_runtime` (a Claude `resets_at` doesn't block a dummy runtime's run).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Leave `doctor-run-health.json` alone unless `RunHealth` itself changes. If it does, regenerate it per the root CLAUDE.md, in the same commit.
- [ ] **Step 4: Run the tests and `test_doctor_run_health_contract.py`.** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 1.7: Provider consent

**Files:**
- Modify: `plugin/engine/scout/kb/privacy.py` (overlay key `privacy.providers`, default `["anthropic"]`; no label change, so `privacy-corpus.json`, `privacy.snapshot.json` and `CLASSIFIER_VERSION` stay as they are, as #625 did)
- Modify: `plugin/engine/scout/cli.py` (`scoutctl privacy consent-provider <id>` / `revoke-provider <id>`, written through the overlay path `privacy-migrate --accept` uses (#510))
- Test: `plugin/engine/tests/unit/test_privacy_providers.py`

- [ ] **Step 1: Write the failing tests:**
  - `test_default_providers_is_anthropic_only`;
  - `test_consent_records_provider_and_timestamp`;
  - `test_revoke_removes_it`;
  - `test_check_refuses_unconsented_provider` (with Task 1.4's dummy adapter `provider="openai"`).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them, plus `test_privacy_corpus.py` (must be untouched and green).** Expected: PASS.
- [ ] **Step 5: Commit.**

### Task 1.8: Doctor loop, evidence table, cleanup

**Files:**
- Modify: `plugin/engine/scout/scripts/bootstrap_doctor.py:369-430, 775-784` (loop the runner-binary and auth checks over the runtimes the schedule uses; Claude's findings are worded as today)
- Create: `docs/runtimes.md` (the spec §13 evidence table, with one row: Claude Code, Tier F, probe version and date, kb-gate date, privacy-scenario date, "paid Claude plan", release)
- Modify: `plugin/phases/core/git-setup.md:86`, `plugin/phases/research/commit-notify.md:34` (drop the vestigial `CLAUDE_MAX_BUDGET_USD` / `CLAUDE_BUDGET_SPENT_USD` arguments; check `write-session-cost.sh`'s argument order first)
- Modify: `plugin/engine/scout/scripts/bootstrap.py:69, 769, 825` and `cli.py:2937, 3002, 3317` (remove the dead `--skip-claude`; keep it accepted and ignored with a deprecation note if any caller passes it, so grep the app and `install.sh` first)
- Test: `test_bootstrap_doctor_checks.py`, `test_doctor_claude_auth.py`

- [ ] **Step 1: Write the failing tests:** `test_doctor_checks_each_scheduled_runtime` (dummy runtime in the schedule → its binary check runs) and `test_claude_doctor_messages_unchanged`.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Write `docs/runtimes.md`. It is evidence, not marketing: no adjectives, one row per runtime.
- [ ] **Step 4: Run the tests, the phase lint (`scoutctl phases lint`, if the phases change trips it) and `ruff`/`mypy`/`versioning check`.** Expected: PASS.
- [ ] **Step 5: Commit, and open the Phase 1 PR(s).** Suggested split:
  - PR A: Tasks 1.1–1.3;
  - PR B: Tasks 1.4–1.5 (runners and liveness, the risky one; own review);
  - PR C: Tasks 1.6–1.8.

### Task 1.9: Live acceptance (Jordan's OK per step)

- [ ] **Step 1:** After PR B merges and a release ships it, Jordan upgrades his live install by hand, as usual.
- [ ] **Step 2:** Check in the run logs that each run logs `=== agent runtime: claude-code ===` and its argv is unchanged, and that `scoutctl health` stays green for 7 days.
- [ ] **Step 3:** With Jordan's OK for the run, `plugin/scripts/eval.sh kb-gate --yes` on the release branch. Record the date in `docs/runtimes.md`.

---

## Phase 0 — Answers and facts (card; no product code)

- **Inputs:** spec §14 questions to Jordan (the coordinator asks them).
- **Spike per CLI** (throwaway, in a scratch dir, never merged). For Codex,
  Antigravity, Gemini CLI if chosen, and Grok Build if chosen, observe and
  record in spec §3 (replacing each **[unknown]**):
  - Do PreToolUse hooks fire in the headless mode (`codex exec`, `agy -p`,
    `gemini -p`, `grok -p`)?
  - What is the exact hook stdin and reply JSON?
  - Does a crashing, hanging or garbage-printing hook block or allow?
  - Do `SCOUT_*` variables reach the hook and a shell child?
  - Does the login persist under launchd?
  - Is a custom endpoint usable (for the fake model)?
  - What do each tool's names look like in the hook payload?
  - What are the vendor's consumer-plan data terms (spec §8.4)?

  A spike that needs a real model uses Jordan's login only with his per-run OK.
- **Done when:** spec §3 has no unknown that changes a tier, and the
  coordinator has a go per runtime.

## Phase 2 — Codex CLI (cards; detailed plan after Phase 0)

1. `codex` adapter: argv (`codex exec --json`, `approval_policy=never`,
   `sandbox_mode=workspace-write`, `developer_instructions` for the run
   prompt); model validation; `classify_failure`; `run_usage` from
   `turn.completed.usage` (`cost_source: unpriced`); process markers.
2. Protocol additions: `hook_in`/`hook_out`/`gate_config`/`parse_events`.
   Codex gate translation: Bash and `apply_patch` → `GatePayload`; unknown
   tools are denied in a run. `agent-runtime install` writes `~/.codex/hooks.json`
   (a managed block) plus `--check`. Gate denies writes to `.codex/`,
   `.agents/` and `.claude/` in the vault.
3. Fake Responses-API server (sibling of `fake_anthropic.py`) and the Codex
   conformance suite. It writes `agent_runtimes/verified.json` (generated,
   never hand-edited).
4. Normalised `RunEvent`s: eval `metrics.py` and the Stop-hook accounting
   consume them; Claude's stays as it is.
5. Neutral connector keys: `connectors.yaml` → regenerate
   `connectors.snapshot.json` with `--no-also-write-app-fixture` → copy to
   the app → aliases in `ConnectorKeyAlias.swift`. **One commit**;
   `contract.yml` is the gate. Per-runtime connector detection.
6. Eval: `codex_shim.py` (isolated `CODEX_HOME`, stub MCP servers, sandbox,
   init guard); `eval.sh kb-gate --agent-runtime codex`;
   `compare-runtimes`.
7. App: onboarding "any supported runtime"; runtime picker in
   `SlotEditForm`; model options from `agent-runtime models`; the
   app-managed engine path when `claude` is absent.
8. Generated skills/commands for Codex under `plugin/runtimes/codex/`
   (`.agents/skills` tree shared with Antigravity), with a drift test.
9. Acceptance: conformance at Tier F or L for a pinned version;
   `kb-gate` + privacy scenario pass on Codex (Jordan's OK per run); one
   slot (dreaming suggested) live for a week. Then add the
   `docs/runtimes.md` row.

## Phase 3 — Google CLI (cards; after Phase 0 and Q5)

- `antigravity` adapter.
  - Its conformance is model-backed only, because there is no custom
    endpoint.
  - Tighter version pinning.
  - Packaging reuses the `.agents/skills` tree; hooks go in
    `~/.gemini/config/hooks.json`.
- Optionally a `gemini` adapter: fake-model conformance through
  `GOOGLE_GEMINI_BASE_URL` if Phase 0 confirms it; an extension package; and
  `hook_out` that always prints valid JSON (malformed output fails open).
- Same acceptance bar as Codex.

## Phase 4 — Grok Build CLI, Track A (cards; only if Q1 says yes; Q10 decides Tier S)

- Adapter and conformance.
- It starts Tier D, because hooks fail open after a 5 s default timeout.
  Tier D means it works in a clone of a #632 delegate mirror, as a delegate.
- Tier S only if Q10 says build it: `scoutctl mcp serve` on the laptop over
  the canonical vault as `autonomous` (shares code with #550 V5), plus
  path-deny profiles for the KB, local-only paths and gate configs.

## Phase 5 — Track B: Grokbot and Claude Duet (tracked here, owned by #550)

- Grokbot (Grok Bot) and Claude Duet are off-laptop agents that consume
  Scout through #550's delegate path:
  - V1 reader, built in #625;
  - V2a mirror, draft #632 (`scout/delegate_mirror.py`, `scoutctl delegate
    link|sync|preview|status|unlink`);
  - then V3 `delegate init`, V4 inbox, and V6 live.
  - Duet first reads its mirror through the GitHub connector; V5 MCP only
    if that's too weak (#550 Q3/Q9).
- **No card from this plan touches `delegate_mirror` or `repo_sync` code.**
  The tasks live in #550's plan and are owned by its session.
- Acceptance: #550's "done when" lines; each agent answers a scout-ask
  question from its mirror.

## Phase 6 — Marketing (cards, one per claim change)

- Each PR changes the site, README or app copy for exactly one row moving
  state in `docs/runtimes.md`, and links that row.
- Wording follows spec §13, including the "never" list.
- No PR in Phases 1–5 touches `docs/index.html`, `README.md`'s pitch or
  `plugin/README.md`'s requirements table.

---

## Self-review notes

- **Spec coverage:**
  - §8.2 → 1.1, 1.2, 1.4.
  - §8.3 → 1.1, 1.4.
  - §8.4 → 1.7.
  - §8.5 liveness → 1.5; gate translation → Phase 2.2.
  - §8.6 → 1.6 and Phase 2.1/2.4.
  - §8.7 → 1.3, 1.4.
  - §8.8 → Phase 2.5.
  - §8.9 → 1.8 and Phase 2.7.
  - §8.10 → Phase 2.8.
  - §8.11 → Phase 2.7.
  - §8.12 → Phases 4–5.
  - §11 → 1a.1–1a.2 (seed of the conformance suite) and Phase 2.3.
  - §13 → 1.8 (`docs/runtimes.md`) and Phase 6.
  - §3.5's bare-mode risk → Phase 1a.
- **Deferred on purpose:** the protocol members without a Claude caller
  (`hook_in`, `hook_out`, `gate_config`, `parse_events`, `session_store`)
  arrive with their first user in Phase 2.
