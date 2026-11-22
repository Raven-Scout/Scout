# Resolve with Claude Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Resolve with Claude" action to health-finding, connector-alert
and run-problem banners. It opens a new Claude Code session prefilled with an
investigation prompt that the engine builds.

**Architecture:**
- A new read-only engine module, `scout/resolve_prompt.py`, behind
  `scoutctl health resolve-prompt <kind> <key> [--json]`. It re-reads the
  live health, connector-alert and run-ledger state and returns versioned
  JSON.
- In the app, `Problem` carries an optional `ResolveSubject`.
  `ProblemView` shows a `LaunchClaudeMenu` when a `ResolvePromptService` is
  in its environment and the engine advertises `resolve_prompt_v1`.
- The launcher fetches the prompt asynchronously at click time.

**Tech Stack:** Python 3.12 + Typer + pytest (engine); Swift 6 / SwiftUI +
Swift Testing (app).

**Spec:** `docs/superpowers/specs/2026-11-19-resolve-with-claude-design.md`

## Global Constraints

- **One PR, cross-cutting.** The contract fixture, its app byte copy, the
  `contract.yml` step and the Swift pins land in one commit (Task 4).
- **No real vault content anywhere.** Use the stand-ins:
  - people: `Alex`, `Priya`, `Sam`;
  - repositories: `example-org/<repo>`;
  - slots: generic, such as `morning-briefing`;
  - branches: `claude/example-branch-1a2b3c`.
- **No file bodies in prompts.** Prompts carry only paths, ids, SHAs,
  timestamps and the engine's own `detail` line.
- **`cwd` is always the vault.** The app launches in
  `ResolvePromptService.scoutDirectory`, and refuses when `cwd`, after
  `resolvingSymlinksInPath()`, differs.
- **Schema:** `SCHEMA_VERSION = 1`. Every key is always present: `schema_version`,
  `kind`, `key`, `current`, `title`, `cwd`, `prompt`.
- **Patterns, the same on both sides:**
  - finding: `^[a-z][a-z0-9_.]{0,63}(:[A-Za-z0-9][A-Za-z0-9._-]{0,80})?$`
  - connector: `^[a-z][a-z0-9_-]{0,31}(:[A-Za-z0-9_.-]{1,80})?$`
  - log: `^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.log$`
- **Exit codes:** 0 when a prompt was built; 2 on a bad kind or key, or a
  missing vault. With `--json`, the exit-2 output is
  `{"schema_version":1,"error":"..."}`.
- **Run outcomes that get a subject:** `Problem(run:)` at `.error` or
  `.warning` only (Jordan, option B).
- **Testing discipline.** Run only the affected tests, under the build lock,
  in a command that starts with
  `until mkdir ~/.scout-worktrees/.build-lock 2>/dev/null; do sleep 30; done; trap 'rmdir ~/.scout-worktrees/.build-lock' EXIT; nice -n 10 …`.
  Run lint (ruff from `plugin/engine` as cwd, `mypy scout`,
  `python -m scout.scripts.versioning check`) in its own command. Never run a
  full local suite.
- **Swift tests.** Use `-only-testing:ScoutTests/<StructName>` (the struct
  name, not the `@Suite` label). Always add `BannerStyleGuardTests` and the
  other source-scan guard suites in `ScoutTests/Utilities`.

## Review Focus

1. **A finding id with a suffix** (`slot.stale:morning-briefing`,
   `lane.dark:mail`) must build a prompt, not exit 2, and use the scheduler
   playbook. Tests: Task 1 `test_suffixed_finding_id_uses_scheduler_playbook`;
   Task 5 `acceptsSuffixedFindingKey`.
2. **A manual run's log with no ledger row** must still build a prompt from
   the log alone, with no ledger lines. Test: Task 2
   `test_run_without_ledger_row_builds_from_log`.
3. **Path traversal in a key** (`../x.log`, `run/../../etc.log`, `a b`) must
   be refused before any file is touched, on both sides. Tests: Task 3
   `test_bad_keys_exit_2`; Task 5 `rejectsUnsafeKeys`.
4. **A canary string in the run log and in `.claude/settings.local.json`**
   must never appear in any prompt. Test: Task 1
   `test_prompt_never_contains_file_content`, reused for runs in Task 2.
5. **The engine answering for a different vault** (the app's
   `SCOUT_DATA_DIR` versus a `cwd` elsewhere) must be refused. Test: Task 5
   `refusesMismatchedCwd`.

---

### Task 1: Engine core and finding prompts

**Files:**
- Create: `plugin/engine/scout/resolve_prompt.py`
- Test: `plugin/engine/tests/unit/test_resolve_prompt.py`

**Interfaces:**
- Produces:
  - `SCHEMA_VERSION: int = 1`
  - `KINDS = ("finding", "connector", "run")`
  - `KEY_PATTERNS: dict[str, re.Pattern[str]]` (the three patterns above)
  - `class ResolveError(ValueError)`
  - `@dataclass(frozen=True) class ResolvePrompt` with fields `kind`, `key`,
    `current: bool`, `title`, `cwd: str`, `prompt`, and a `to_dict() -> dict`
    method.
  - `GUARDRAILS: str`, the exact block from the spec's §Prompt shape item 5,
    as a markdown list under `## Guardrails`.
  - `def validate(kind: str, key: str) -> None`, which raises `ResolveError`.
  - `def for_finding(vault: Path, finding_id: str, *, now: datetime | None = None, home: Path | None = None) -> ResolvePrompt`
  - `def _render(*, title, seen: list[str], evidence: list[str], steps: list[str], commands: list[str], done: str) -> str`
    renders the six sections in the spec's order: `## What Scout saw`,
    `## Evidence to read`, `## How to investigate` (numbered),
    `## Reproduce and verify` (a fenced `bash` block),
    `GUARDRAILS`, and `## Done when`.
  - `PLAYBOOKS: dict[str, Playbook]`, keyed by the id prefix before `:`. A
    playbook takes `(finding, vault)` and returns the steps, commands, extra
    evidence and the done text.
  - `_GENERIC`, the fallback playbook.

- [ ] **Step 1: Write the failing tests.**

Build a temp vault the way `tests/unit/test_run_ledger_contract.py` does
(read its builder first and reuse its helpers):
- a `.scout-logs/run-outcomes.jsonl` row with
  `integrity: "restored"`, `slot: "morning-briefing"`;
- its log `.scout-logs/morning-briefing-20261119-1703.log`, with the lines
  `integrity check: refs/heads/claude/example-branch-1a2b3c changed protected file(s): .claude/settings.local.json`
  and `integrity check: restored them on refs/heads/claude/example-branch-1a2b3c in commit 7452d70f0000`,
  plus the line `CANARY-LOG-7731`;
- `.claude/settings.local.json`, containing `CANARY-SETTINGS-7731`.

Pin `now` so `health.evaluate` reports `run.integrity_restored`.

```python
def test_integrity_finding_prompt(tmp_path, monkeypatch):
    p = resolve_prompt.for_finding(vault, "run.integrity_restored", now=NOW, home=home)
    assert (p.kind, p.key, p.current, p.cwd) == ("finding", "run.integrity_restored", True, str(vault))
    for heading in ("## What Scout saw", "## Evidence to read", "## How to investigate",
                    "## Reproduce and verify", "## Guardrails", "## Done when"):
        assert heading in p.prompt
    assert p.prompt.index("## What Scout saw") < p.prompt.index("## Done when")
    assert str(vault / ".scout-logs" / "morning-briefing-20261119-1703.log") in p.prompt
    assert "refs/heads/claude/example-branch-1a2b3c" in p.prompt and "7452d70f0000" in p.prompt
    assert "git worktree list --porcelain" in p.prompt
    assert "7 days" in p.prompt  # done condition: ages out, not "cleared"

def test_prompt_never_contains_file_content(...):  # finding prompt; Task 2 adds run and connector
    assert "CANARY-LOG-7731" not in p.prompt and "CANARY-SETTINGS-7731" not in p.prompt

def test_guardrails_verbatim_in_every_playbook(...):
    # for each PLAYBOOKS key plus one unknown id: GUARDRAILS in prompt
    # (build Findings directly via a monkeypatched health.evaluate)

def test_generic_playbook_for_unknown_id(...):   # id "config.unreadable": summary, fix, `scoutctl health --json`
def test_suffixed_finding_id_uses_scheduler_playbook(...):  # "slot.stale:morning-briefing" → "last-fire.json" in prompt
def test_cleared_finding_is_not_current(...):    # id absent from health → current False, "confirm it cleared" in prompt
def test_auth_playbook_never_prints_token(...):  # "auth.expired" → "scoutctl auth status" and "Never print"
def test_validate_rejects(...):  # parametrized: ("finding","Run.X"), ("finding","a"*70), ("run","../x.log"),
                                 # ("run","x.txt"), ("connector","mcp: x"), ("nope","x") → ResolveError
```

- [ ] **Step 2: Run the tests; expect them to fail** with `ModuleNotFoundError: scout.resolve_prompt`.

Run: `cd plugin/engine && until mkdir ~/.scout-worktrees/.build-lock 2>/dev/null; do sleep 30; done; trap 'rmdir ~/.scout-worktrees/.build-lock' EXIT; nice -n 10 .venv/bin/python -m pytest tests/unit/test_resolve_prompt.py -q`

- [ ] **Step 3: Implement `scout/resolve_prompt.py`.**

`for_finding` calls `health.evaluate(vault, now=now, home=home)` and picks
the finding by exact id. A playbook is looked up by `id.split(":")[0]`, first
in full and then by its family before the first `.`. That gives `run.*`,
`runs.*`, `auth.*`, `sandbox.*` and `gates.*`. Map the families from the
spec's §Playbooks list:
- `scheduler.stalled`, `lock.skips`, `lease.*`, `slot.*` and `lane.*` all
  map to the scheduler playbook;
- `runs.unsandboxed` and `runs.sandbox_env_off` map to the sandbox playbook;
- `update.*` maps to the update playbook.

The evidence always lists these paths, read through `scout.paths` and
`health` constants, never hard-coded:
- the finding's `log`;
- `.scout-logs/run-outcomes.jsonl`;
- `.scout-state/schedule.yaml`;
- the engine root, `Path(scout.__file__).parents[1]`, labelled "Scout's own
  code (read-only)".

For integrity, the restoring and changing refs and SHAs are parsed from the
log's `integrity check:` lines with a regex. Only those matched tokens go
into the prompt, never the raw line remainder.

When the finding is not current, `seen` gets this line:
`Scout no longer reports this. Confirm it cleared, and why, before investigating further.`

- [ ] **Step 4: Run the tests; expect PASS.** Same command as Step 2.

- [ ] **Step 5: Commit.**

`git commit -m "feat(engine): resolve_prompt — investigation prompts for run-health findings"`

---

### Task 2: Connector and run prompts

**Files:**
- Modify: `plugin/engine/scout/resolve_prompt.py`
- Test: `plugin/engine/tests/unit/test_resolve_prompt.py`

**Interfaces:**
- Consumes: Task 1's `_render`, `GUARDRAILS` and `ResolvePrompt`.
- Produces:
  - `def for_connector(vault: Path, connector_key: str) -> ResolvePrompt`, which
    reads `.scout-logs/connector-alerts.json` (the `render_alerts_state`
    format) and matches `connector_key` exactly;
  - `def for_run(vault: Path, log_name: str) -> ResolvePrompt`, which reads
    the ledger through `health.read_outcomes(vault)` and takes the newest row
    whose `log` basename equals `log_name`.
  - `def build(vault: Path, kind: str, key: str, *, now=None, home=None) -> ResolvePrompt`
    validates, then dispatches.

- [ ] **Step 1: Write the failing tests.**

```python
def test_connector_prompt(...):  # alerts file with connector_key "mcp:example_tracker", level CRITICAL,
    # reason "3 errors in a row", first_seen set
    p = resolve_prompt.for_connector(vault, "mcp:example_tracker")
    assert p.current and "mcp:example_tracker" in p.prompt and "3 errors in a row" in p.prompt
    assert "connector-alerts.json" in p.prompt and "scoutctl connectors probe-registry" in p.prompt

def test_connector_not_in_alerts_is_not_current(...):  # current False
def test_run_prompt_failed(...):   # row outcome "failed", failure_class "oauth_expired"
    # → log path, ledger path, "started_at" value, "oauth_expired" in prompt; current True
def test_run_rate_limited_names_reset(...):  # outcome rate_limited with resets_at → its ISO time in prompt
def test_run_later_ok_is_not_current(...):   # a later "ok" row for the same slot → current False,
    # and "a later run succeeded" in prompt
def test_run_without_ledger_row_builds_from_log(...):  # log file only → current True, the log path in prompt,
    # "run-outcomes.jsonl" not in "## Evidence to read" section
def test_run_and_connector_prompts_never_contain_file_content(...):  # canaries as in Task 1
def test_build_dispatches_and_validates(...):  # build(vault, "run", "../x.log") raises ResolveError
```

- [ ] **Step 2: Run; expect FAIL.** Same command as Task 1.

- [ ] **Step 3: Implement.**

Run playbook cases, keyed by `row.outcome`:
- `failed` or any failure: the steps read the failure line through
  `health.failure_line(log, failure_class)` and say whether the class is
  transient.
- `rate_limited`: `resets_at`.
- `orphaned`: read the log tail.
- `skipped_gates`, `skipped_sandbox`, `skipped_runtime`, `skipped_lease`:
  the matching state command.

A log with no ledger row is valid when `vault/.scout-logs/<log_name>` exists.
If neither the row nor the log exists, raise `ResolveError("no such run log")`.

- [ ] **Step 4: Run; expect PASS.**

- [ ] **Step 5: Commit.**

`git commit -m "feat(engine): resolve_prompt for connector alerts and runs"`

---

### Task 3: The CLI command and the manifest feature

**Files:**
- Modify: `plugin/engine/scout/cli.py`. Add a `resolve-prompt` command to
  `health_app`, inside `_register_health()`, after `cli_health_notify`.
- Modify: `plugin/engine/scout/manifest.py`. Add `"resolve_prompt_v1": True`
  to the features dict.
- Test: `plugin/engine/tests/unit/test_resolve_prompt_cli.py`; extend the
  manifest test that pins the features.

**Interfaces:**
- Consumes: `resolve_prompt.build`, `ResolveError` and `SCHEMA_VERSION`.
- Produces: the command
  `scoutctl health resolve-prompt KIND KEY [--json]`, where KIND and KEY are
  positional arguments.
  - With `--json`, it prints `json.dumps(prompt.to_dict())`.
  - Without it, it prints only `prompt.prompt`.
  - The vault comes from `paths.data_dir()`.

- [ ] **Step 1: Write the failing tests** with `typer.testing.CliRunner` and
  `SCOUT_DATA_DIR` set to the temp vault.

```python
def test_json_shape(...):    # exit 0; set(doc) == {"schema_version","kind","key","current","title","cwd","prompt"}
def test_plain_prints_prompt_only(...):  # stdout starts with "# " title line, no "{"
def test_bad_keys_exit_2(...):  # parametrized over ("run","../x.log"), ("run","run/../../etc.log"),
                                # ("finding","a b"), ("bogus","x") → exit 2, json {"schema_version":1,"error":...}
def test_missing_vault_exit_2(...):
def test_manifest_advertises_resolve_prompt(...):  # manifest features["resolve_prompt_v1"] is True
```

- [ ] **Step 2: Run; expect FAIL.**

- [ ] **Step 3: Implement.** Follow `cli_health`'s missing-vault handling.
  Catch `ResolveError` only, and map it to exit 2.

- [ ] **Step 4: Run Tasks 1–3's tests and the manifest test; expect PASS.**

- [ ] **Step 5: Commit.**

`git commit -m "feat(cli): scoutctl health resolve-prompt"`

---

### Task 4: The contract fixture and the Swift model (one commit)

**Files:**
- Create: `plugin/engine/tests/unit/test_resolve_prompt_contract.py`. Follow
  the pattern of `test_delegate_status_contract.py`: `REGEN`, `CANONICAL`,
  and a deterministic vault builder.
- Create: `plugin/engine/tests/fixtures/contract/resolve-prompt.json`,
  generated, never written by hand. It is one JSON object,
  `{"finding": {...}, "connector": {...}, "run": {...}}`, with 2-space
  indent, sorted keys and a trailing newline.
- Create: `apps/macos/ScoutTests/Fixtures/resolve-prompt.json`, a `cp` of
  the canonical file.
- Create: `apps/macos/Scout/Engine/ResolvePrompt.swift`
- Create: `apps/macos/ScoutTests/Engine/ResolvePromptContractTests.swift`
- Modify: `.github/workflows/contract.yml`. Copy the delegate-status step
  (lines ~430–460) for `resolve-prompt.json`.
- Modify: root `CLAUDE.md`, `plugin/CLAUDE.md` and `apps/macos/CLAUDE.md`.
  Add the "resolve prompt has one canonical copy too" rule and add the file
  to the lists of generated fixtures.

**Interfaces:**
- Produces, in Swift:

```swift
nonisolated enum ResolveSubject: Equatable, Hashable, Sendable {
    case finding(String), connector(String), run(logName: String)
    var kind: String { get }          // "finding" | "connector" | "run"
    var key: String { get }
    var isValidKey: Bool { get }      // the Global Constraints patterns, whole-match
}
nonisolated struct ResolvePrompt: Equatable, Sendable {
    let kind: String, key: String, current: Bool, title: String, cwd: String, prompt: String
    static let schemaVersion = 1
    /// nil unless schema_version == 1, every key present, and kind/key equal `subject`'s.
    static func parse(_ data: Data, for subject: ResolveSubject) -> ResolvePrompt?
}
```

- [ ] **Step 1: Write the engine contract test.**

`test_contract_matches_canonical` builds the vault, calls
`resolve_prompt.build` for each kind with pinned `now` and `home`, replaces
the temp vault path with `/Users/alex/Scout` in `cwd` and `prompt`, and
compares the result with `CANONICAL`. It rewrites the file when `REGEN` is
set.

- [ ] **Step 2: Generate the fixture.**

`cd plugin/engine && env SCOUT_REGEN_CONTRACT=1 .venv/bin/python -m pytest tests/unit/test_resolve_prompt_contract.py`
(under the lock). Then, from the repo root:
`cp plugin/engine/tests/fixtures/contract/resolve-prompt.json apps/macos/ScoutTests/Fixtures/resolve-prompt.json`

- [ ] **Step 3: Write the Swift tests in `ResolvePromptContractTests`** (Swift Testing).

```swift
@Test func parsesEachKind()          // fixture[kind] → parse(for: subject) non-nil; pins kind, key, current, title
@Test func refusesOtherSchema()      // schema_version 2 → nil
@Test func refusesMismatchedSubject()// finding doc parsed for .finding("other.id") → nil
@Test func refusesMissingKey()       // drop "cwd" → nil
```

- [ ] **Step 4: Implement `ResolvePrompt.swift`.** Decode with
  `JSONDecoder` into a private `Decodable` struct, then compare it with the
  subject.

- [ ] **Step 5: Run the engine contract test, and
  `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ResolvePromptContractTests`,
  both under the lock. Expect PASS.**

- [ ] **Step 6: Commit everything in this task as one commit.**

`git commit -m "contract: resolve-prompt.json — scoutctl health resolve-prompt --json, with the app's copy"`

---

### Task 5: `ResolvePromptService`

**Files:**
- Create: `apps/macos/Scout/Engine/ResolvePromptService.swift`
- Modify: `apps/macos/Scout/Shell/AppState.swift`. Construct the service
  next to `RunLedgerService` (around line 202), with the same
  `scoutctlResolved`, `runner` and `["SCOUT_DATA_DIR": scoutDir.path]`.
- Test: `apps/macos/ScoutTests/Engine/ResolvePromptServiceTests.swift`

**Interfaces:**
- Consumes: Task 4's `ResolveSubject` and `ResolvePrompt`, and
  `ProcessRunner`.
- Produces:

```swift
@MainActor final class ResolvePromptService {
    let scoutDirectory: URL
    init(scoutctl: URL, argumentsPrefix: [String], runner: any ProcessRunner,
         environment: [String: String], scoutDirectory: URL)
    static let feature = "resolve_prompt_v1"
    /// Caches only a yes, like BoardWriter.supportsBoardWrites.
    func isSupported() async -> Bool
    /// Runs `health resolve-prompt <kind> <key> --json`; throws ResolvePromptError.
    func prompt(for subject: ResolveSubject) async throws -> ResolvePrompt
    nonisolated static func arguments(for subject: ResolveSubject) -> [String]
}
nonisolated enum ResolvePromptError: LocalizedError, Equatable {
    case invalidKey, engineFailed(String), unreadable, wrongFolder
    // errorDescription: "Couldn't build the prompt: …"
}
```

- [ ] **Step 1: Write the failing tests** with a stub `ProcessRunner`.
  Copy the stub from `RunLedgerServiceTests` if one exists there.

```swift
@Test func buildsArguments()        // arguments(for: .run(logName: "a.log")) == ["health","resolve-prompt","run","a.log","--json"]
@Test func acceptsSuffixedFindingKey() // .finding("slot.stale:morning-briefing").isValidKey
@Test func rejectsUnsafeKeys()      // "../x.log", "a b", "" → prompt(for:) throws .invalidKey, runner never called
@Test func returnsParsedPrompt()    // stub stdout = fixture["finding"] with cwd == scoutDirectory.path → equal
@Test func refusesMismatchedCwd()   // cwd "/elsewhere" → throws .wrongFolder
@Test func surfacesEngineFailure()  // exit 2, stderr "boom" → .engineFailed("boom")
@Test func supportCachesOnlyYes()   // first manifest without the feature → false; second with it → true
```

- [ ] **Step 2: Run; expect FAIL.**
  `-only-testing:ScoutTests/ResolvePromptServiceTests`, under the lock.

- [ ] **Step 3: Implement.** Compare folders with
  `URL(fileURLWithPath: cwd).resolvingSymlinksInPath()` against
  `scoutDirectory.resolvingSymlinksInPath()`.

- [ ] **Step 4: Run; expect PASS.**

- [ ] **Step 5: Commit.**

`git commit -m "feat(app): ResolvePromptService runs scoutctl health resolve-prompt"`

---

### Task 6: `Problem` carries a resolve subject

**Files:**
- Modify: `apps/macos/Scout/Utilities/PlainLanguage/Problem.swift`
- Test: `apps/macos/ScoutTests/Utilities/DisplayNameTests.swift`. Add to its
  `ProblemTests` struct.

**Interfaces:**
- Produces: `var resolveSubject: ResolveSubject?` on `Problem`, defaulting to
  `nil`. It is not part of `id`. A new init parameter,
  `resolveSubject: ResolveSubject? = nil`, is added last.
  - `init(finding:)` sets `.finding(finding.id)`.
  - `init(connectorAlert:)` sets `.connector(alert.connector)`.
  - `init?(run:)` sets `.run(logName: run.logPath.lastPathComponent)`, only
    when the resulting severity is `.error` or `.warning`.
  - `init?(runStatus:runName:details:)` itself never sets it.

- [ ] **Step 1: Write the failing tests.**

```swift
@Test func findingProblemHasSubject()        // Problem(finding: f).resolveSubject == .finding(f.id)
@Test func plainProblemKeepsSubject()        // RunOutcomeDisplay.plainProblem(slotFinding).resolveSubject != nil
@Test func connectorProblemHasSubject()
@Test(arguments: ["failed", "orphaned", "rateLimited", "skippedGates", "skippedLease"])
func runProblemHasSubject(_ status: String)  // Run.fixture(status:) → .run(logName: "fake.log")
@Test(arguments: ["skippedBudget", "skippedConcurrency", "skippedRedundant"])
func infoSkipHasNoSubject(_ status: String)
@Test func otherFactoriesHaveNoSubject()     // engineUnavailable, connectorRosterMissing, failureClass → nil
```

Use the existing `Run` test factory: `Run.swift:260` has a `logPath:`
default. Check the exact `RunStatus` case names in `Run.swift` first.

- [ ] **Step 2: Run; expect FAIL.** `-only-testing:ScoutTests/ProblemTests`.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run; expect PASS.**

- [ ] **Step 5: Commit.**

`git commit -m "feat(app): Problem names what Resolve with Claude can investigate"`

---

### Task 7: The button: async launcher, `ProblemView`, environment

**Files:**
- Modify: `apps/macos/Scout/ActionItems/Views/LaunchClaudeMenu.swift`
- Modify: `apps/macos/Scout/Utilities/PlainLanguage/ProblemView.swift`
- Create: `apps/macos/Scout/Utilities/PlainLanguage/ResolveWithClaude.swift`.
  It holds the environment key and the button view.
- Modify: `apps/macos/Scout/ScoutApp.swift`. Add
  `.environment(\.resolvePromptService, appState.resolvePromptService)` next
  to each `.environmentObject(appState)`, at lines 42 and 68.
- Test: `apps/macos/ScoutTests/Utilities/ResolveWithClaudeTests.swift`, plus
  the guard suites.

**Interfaces:**
- Consumes: Tasks 4–6.
- Produces:
  - `LaunchClaudeMenu.init(title: String = "Launch Claude Code", asyncPrompt: @escaping @MainActor () async throws -> String, scoutDirectory: URL, launchError: Binding<String?>)`.
    The existing inits keep working by wrapping the sync closure. The split
    button's label uses `title`. `launch(_:)` awaits the prompt inside the
    existing `run { }`, so `isLaunching` covers the fetch. A thrown error
    takes the existing non-permission path into `launchError`.
  - `extension EnvironmentValues { @Entry var resolvePromptService: ResolvePromptService? = nil }`
  - `struct ResolveWithClaudeButton: View`, which takes a `subject` and a
    `launchError` binding. Its `.task` checks `isSupported()` and renders
    nothing until the check says yes.
    `static let title = "Resolve with Claude"`.
  - `ProblemView` puts `ResolveWithClaudeButton` in the `DSBannerActions`
    trailing slot, before Details and Copy details, when
    `problem.resolveSubject != nil` and the environment value is non-nil. It
    shows `launchError` as a caption in `DS.Ink.p2` under the buttons, and
    never in a status colour (`BannerStyleGuardTests`).

- [ ] **Step 1: Write the failing tests.**

```swift
@Test func titleIsResolveWithClaude()   // ResolveWithClaudeButton.title == "Resolve with Claude"
@Test func asyncPromptFailureSetsLaunchError()
    // factor the launcher's decision into a static helper:
    //   LaunchClaudeMenu.launchErrorText(for:) — ResolvePromptError.wrongFolder → its errorDescription
@Test func problemViewShowsButtonOnlyWithSubjectAndService()
    // static ProblemView.showsResolve(problem:service:) -> Bool:
    // subject+service → true; no subject → false; no service → false
```

- [ ] **Step 2: Run; expect FAIL** (`ResolveWithClaudeTests`).

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run these together under the lock; expect PASS.**
  - `ResolveWithClaudeTests`, `ProblemTests` and `ResolvePromptServiceTests`;
  - `BannerStyleGuardTests` and every other `*GuardTests` struct in
    `apps/macos/ScoutTests` (`grep -rl "GuardTests" apps/macos/ScoutTests`);
  - the existing launcher suites (`grep -rl "LaunchClaudeMenu\|ClaudeLauncher" apps/macos/ScoutTests`).

  Run `xcodebuild … build` first, so the existing call sites are confirmed
  to compile.

- [ ] **Step 5: Check it by hand.** Build and launch the app with a vault
  whose health is red, and confirm all of these:
  - the Control Center banner shows "Resolve with Claude";
  - the button opens Claude Desktop with a Code session in the vault,
    prefilled with the prompt;
  - the chevron lists the terminal, chat and Cowork targets;
  - routine `.info` skips in a run's detail view show no button.

  Report what was not checked.

- [ ] **Step 6: Commit.**

`git commit -m "feat(app): Resolve with Claude on health, connector and run banners"`

---

### Task 8: Changelog, lint and PR

**Files:**
- Create: `plugin/changelog.d/keen-williamson-58d4bb.added.md`, with one
  bullet for `scoutctl health resolve-prompt` and `resolve_prompt_v1`.
- Create: `apps/macos/changelog.d/keen-williamson-58d4bb.added.md`, with one
  bullet for the button and where it appears.

- [ ] **Step 1: Write both fragments.** Use `- ` bullets with no headings,
  following `plugin/changelog.d/README.md`.

- [ ] **Step 2: Lint in its own command** from `plugin/engine`:
  `.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout && .venv/bin/python -m scout.scripts.versioning check && .venv/bin/python -m scout.scripts.changelog check`

- [ ] **Step 3: Run the engine tests this PR touches, under the lock.** That
  is the Task 1–4 test files, the manifest test,
  `tests/unit/test_run_ledger_contract.py`, and the health CLI tests
  (`grep -l "cli_health\|\"health\"" tests/unit/*.py`).

- [ ] **Step 4: Scrub check.** Grep the diff for real vault names, using the
  check in `apps/macos/CLAUDE.md` against `~/Scout` (read-only) for any
  literal you're unsure of.

- [ ] **Step 5: Commit, push, and open a draft PR.**
  - Commit the fragments.
  - `git push -u origin HEAD`, then
    `gh pr create --draft --title "Resolve with Claude: engine-built investigation prompts on health, connector and run banners"`.
  - In the body, link the spec and say what was verified by hand.
  - Bind the PR with the ccd_pr tools.

- [ ] **Step 6: Report to the "Scout coordinator" session.** Send the PR
  link, the tests that were run, and what remains unverified. Don't merge.
