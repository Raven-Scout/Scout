# Eval harness, slices 1–3: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a maintainer can run `plugin/scripts/eval.sh run busy-weekday` and get #371's placement rate,
the no-drop result and today's tool-call profile from one isolated, stubbed Scout session on their
own Claude login. Everything except that one command runs in free CI.

**Architecture:** a dev-only Python package at `plugin/engine/tests/eval/harness/` builds a fixture
vault with the checkout's own `bootstrap.install()`, overlays hand-written anonymized content, and
serves scripted connectors: a stdlib stdio MCP server and a fake `gh`. It runs the vault's real
`run-scout.sh` with `CLAUDE_BIN` pointed at an isolating shim. Pure `metrics` and `checks` modules
score the result.

**Tech stack:** Python 3.11+, stdlib only (plus the engine's existing `pyyaml`), pytest, bash.

**Spec:** `docs/superpowers/specs/2026-10-12-eval-harness-design.md`

## Global constraints

- No `ANTHROPIC_API_KEY` and no `--bare`, anywhere (spec §13 Q1).
- Isolation is #354's recipe plus a mandatory `--strict-mcp-config` and the init-event guard
  (spec §4.1).
- The default model is `sonnet`; `--opus` opts in. More than 10 sessions needs `--many`.
- CI is tier 0 only. No test in a `test_*.py` file may start `claude`.
- The fixture is hand-written and never derived from a real vault. The only names allowed are
  `Alex`, `Priya`, `Sam`, `example-org`, `acme-co.slack.com`, and `PROJ-`/`OPS-`/`DESK-` ids
  (`plugin/CLAUDE.md`).
- The harness lives in `plugin/engine/tests/eval/` and is never imported by `scout/`. Coverage's
  `source = ["scout"]` is unchanged.
- Run ruff from `plugin/engine` (`.venv/bin/ruff check tests/eval tests/unit/test_eval_*.py`).
- While developing, model-backed runs are N=1 on Sonnet only.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review focus

1. **A non-stub MCP server reaching a session.** The init-event guard must kill the session. Its
   test feeds the guard a synthetic init event (Task 12).
2. **An unrendered date macro or `{{…}}` left in an overlay file.** Today's file would then name a
   literal `{{d-1}}`. `build_vault` fails loudly (Task 4).
3. **A real name or host slipping into the fixture.** The scrub test fails and names the file and
   token (Task 3).
4. **Stream-json lines the harness doesn't know** (new event types, partial lines from a killed
   process). `metrics` skips and counts them and never crashes (Task 10).
5. **A sample with no daily file today**, because the agent crashed before writing. `placement` and
   `no_drop` report `missing_output`, not a Python traceback (Task 11).

---

## PR 1 — fixture vault (branch `feat/eval-fixture-vault`)

### Task 1: Date macros

**Files:** create `tests/eval/__init__.py`, `tests/eval/harness/__init__.py`,
`tests/eval/harness/dates.py`; test `tests/unit/test_eval_dates.py`.

**Produces:** `render_dates(text: str, today: date) -> str` and `UNRENDERED = re.compile(r"\{\{[^}]*\}\}")`.

The macros are `{{today}}`, `{{d±N}}` (calendar days), `{{wd±N}}` (working days; Saturday and
Sunday are skipped, and a weekend `today` counts from the previous Friday) and `{{month}}`
(`YYYY-MM`). Any other `{{…}}` is left alone so that `UNRENDERED` can catch it.

- [ ] Write tests:
  - `{{d-1}}` on 2026-10-14 gives `2026-10-13`;
  - `{{wd-2}}` on Monday 2026-10-12 gives `2026-10-08`;
  - `{{wd+1}}` on Friday 2026-10-16 gives `2026-10-19`;
  - `{{month}}`;
  - an unknown macro is untouched.
- [ ] Run them and watch them fail. Implement. Run them and watch them pass. Commit.

### Task 2: Generated KB index

**Files:** `tests/eval/harness/index_gen.py`; test `tests/unit/test_eval_index_gen.py`.

**Produces:** `generate_index(*, seed: int = 196, entries: int = 600) -> str`.

The index is a deterministic `knowledge-base.md`: grouped headings, and one line per entry,
`- [[topics/<slug>]] — <phrase> · **Last verified:** {{d-N}}`. Words come from a fixed list of
lowercase neutral nouns in the module, so the scrub allowlist only needs the few capitalised
headings.

- [ ] Tests: the same seed gives the same text; 600 entries is at least 60 KB; every wikilink slug
  is lowercase.
- [ ] Implement. Commit.

### Task 3: Base overlay content, allowlist and scrub

**Files:**
- create `tests/eval/vault/**`, all hand-written:
  - `knowledge-base/projects/{widgets,api-gateway,billing-cleanup,docs-refresh}/<slug>.md`, with
    one each of: verified `{{d-2}}`, verified 4 months ago, no `**Last verified:**` line, and
    `last_updated: {{d-90}}   # derived`;
  - `knowledge-base/people/{alex,priya,sam}.md` and `knowledge-base/people.md`. The thin entity
    is `sam.md`, whose `works_on` lists one project while the projects name Sam in three;
  - `meetings/meetings.md` and `knowledge-base/meetings/meetings.md`, disagreeing on a weekly
    sync's day;
  - `inbox.md`, `docs/wishlist/README.md`, `knowledge-base/research-queue/README.md`;
- `tests/eval/allowlist.txt`;
- `tests/eval/harness/scrub.py`;
- test `tests/unit/test_eval_scrub.py`.

**Produces:** `scan(root: Path, allowlist: set[str]) -> list[Finding]`, where
`Finding(path: str, line: int, token: str, kind: str)`, and
`load_allowlist(path: Path) -> set[str]`.

The scan covers every text file under `tests/eval/vault/` and `tests/eval/scenarios/`:
- capitalised words `\b[A-Z][a-zA-Z]+\b` (kind `word`);
- email domains;
- URL hosts;
- `owner/repo` after `github.com/` or in `Refs:` (kind `repo`);
- `\b[A-Z]{2,}-\d+\b` prefixes (kind `id`);
- `@handles`.

Each must be in the allowlist. Structural tokens (`**Last verified:**`, `[#TAG]`, …) contain only
allowlisted words.

- [ ] Tests:
  - the committed fixture scans clean;
  - a tmp dir holding the files listed below gives exactly one finding each, with the right kind:
    - `Contact Morgan at morgan@realcorp.com`;
    - `https://realcorp.atlassian.net/x`;
    - `Refs: ACME-12 · bigco/payments#3`.
- [ ] Write the overlay files, the allowlist and the scanner. Iterate until the fixture scans
  clean. Commit.

### Task 4: `build_vault` and `eval.sh build`

**Files:**
- `tests/eval/harness/build.py`, `tests/eval/harness/cli.py`, `tests/eval/harness/__main__.py`;
- `plugin/scripts/eval.sh`;
- test `tests/unit/test_eval_build.py`.

**Consumes:** Tasks 1–2, and `scout.scripts.bootstrap.BootstrapConfig` / `install`.

**Produces:**
```python
FIXTURE_USER = "Alex Example"; FIXTURE_SLACK_ID = "U0123456789"
def build_vault(dest: Path, *, today: date, scenario_dir: Path | None = None,
                claude_bin: str = "/usr/bin/false", connectors: frozenset[str] = DEFAULT_CONNECTORS,
                max_budget: str = "5") -> Path
```
`DEFAULT_CONNECTORS = frozenset({"slack", "calendar", "linear", "github"})`.

Steps inside:
1. `install()` with fixed inputs (spec §5.1, `skip_jobs=True`, `skip_claude=True`,
   `timezone="UTC"`).
2. Copy `tests/eval/vault/` and then `<scenario>/vault/` over it. In `.md`/`.yaml`/`.json` files,
   render file contents **and** path components with `render_dates`. Raise
   `FixtureError(path, token)` if `UNRENDERED` still matches in a rendered overlay file.
3. Write `knowledge-base.md` from `generate_index()`.
4. Replace `scripts/cc-session-cache.sh` with `#!/bin/bash\nexit 0\n` (mode 0755).
5. Run `git init -q`, `git add -A` and `git commit -q -m "fixture"` with `GIT_AUTHOR_*`,
   `GIT_COMMITTER_*` and dates fixed, and `GIT_CONFIG_GLOBAL=/dev/null`.

`cli.py build <dest> [--scenario NAME] [--today YYYY-MM-DD]` prints the path. `eval.sh` does
`cd plugin/engine && exec .venv/bin/python -m tests.eval.harness "$@"`.

- [ ] Tests (tmp dirs):
  - SKILL.md contains `## Run Modes`;
  - `action-items/action-items-<d-1>.md` exists when the scenario has one;
  - `git log --oneline` has one line;
  - cc-session-cache is a no-op;
  - two builds with the same `today` have byte-identical trees (excluding `.git`);
  - an overlay with `{{bogus}}` raises `FixtureError`.
- [ ] Implement. Run `.venv/bin/pytest tests/unit/test_eval_*.py` and ruff. Commit.
- [ ] Fresh-context review of the branch (subagent, Opus). Fix its findings.
- [ ] Open PR 1 (`feat(eval): fixture vault + scrub test (#196)`) and send the number to the
  coordinator.

---

## PR 2 — scripted connectors (branch `feat/eval-stubs`, stacked on PR 1)

### Task 5: Rule engine

**Files:** `tests/eval/harness/stubs/__init__.py`, `stubs/rules.py`; test
`tests/unit/test_eval_rules.py`.

**Produces:**
```python
@dataclass(frozen=True) class Reply: kind: str  # "ok" | "error" | "unstubbed"
                                   payload: Any; rule: str | None; capture: str | None
class Script:
    @classmethod
    def load(cls, path: Path, *, today: date) -> "Script"
    def reply(self, persona: str, tool: str, args: dict, *, prior_hits: Callable[[str], int]) -> Reply
```

Rule fields:
- `match` (field → regex, against the stringified arg);
- `respond` (an inline object, or a string naming a file relative to the script, rendered with
  `render_dates` and parsed as JSON);
- `error` (`auth_expired` | `unavailable` | a free string);
- `capture`;
- `times`.

Tool key `"*"` is a persona-wide default. Rules are tried in order, and the first match wins. A
rule with `times: N` is skipped once `prior_hits(rule_id) >= N`. Rule ids are `persona:tool:index`.

- [ ] Tests:
  - match by regex;
  - the default rule;
  - a `"*"` error rule;
  - a `times: 1` rule followed by a fallback;
  - a file response with a date macro;
  - an unknown tool gives `unstubbed`.
- [ ] Implement. Commit.

### Task 6: Stdio MCP stub server

**Files:**
- `stubs/mcp_server.py`;
- catalogs `tests/eval/stubs/catalog/{slack,linear,calendar,email}.yaml` (tool name, description,
  `inputSchema`; written from the phases, never captured from a live account);
- test `tests/unit/test_eval_mcp_server.py`.

**Produces:** `python -m tests.eval.harness.stubs.mcp_server --persona P --script S --catalog C --calls L --today D`.

It speaks newline-delimited JSON-RPC 2.0:
- `initialize` echoes the client's `protocolVersion` and advertises `capabilities: {tools: {}}`;
- `tools/list` returns the catalog;
- `tools/call` logs to `calls.jsonl` first (`{ts, persona, tool, args, rule, kind}`) and then
  replies;
- `ping` replies;
- notifications get no reply;
- an unknown method returns error `-32601`.

An `error` reply is `{"content":[{"type":"text","text":<msg>}],"isError":true}`, with
`auth_expired` → `"Authentication required: the connection's OAuth token has expired. Reconnect it in Settings."`.
`prior_hits` counts that rule id in the calls log.

- [ ] Tests (subprocess, pipes): initialize → tools/list → tools/call twice with `times: 1`; the
  calls log has two rows; an unknown method; malformed JSON gives a parse error and the server
  keeps serving.
- [ ] Implement. Commit.

### Task 7: Fake `gh` and the MCP config writer

**Files:** `stubs/gh.py`, `tests/eval/stubs/bin/gh` (bash: `exec "$SCOUT_EVAL_PYTHON" -m tests.eval.harness.stubs.gh "$@"`),
`harness/mcp_config.py`; tests `tests/unit/test_eval_gh.py`, `test_eval_mcp_config.py`.

`gh` rules live under persona `gh`, tool `"*"`, and `match: {argv: <regex on " ".join(argv)>}`.
`respond: {stdout: <text|file>, exit: 0}`. `error` exits 1 with a message on stderr. It reads
`SCOUT_EVAL_SCRIPT`, `SCOUT_EVAL_CALLS`, `SCOUT_EVAL_TODAY` and `SCOUT_EVAL_ENGINE`.

`write_mcp_config(dest: Path, *, personas: dict[str, str], script: Path, calls: Path, today: date, python: str) -> set[str]`.
`personas` maps the persona to its server key (`PERSONA_SERVERS = {"slack": "claude_ai_Slack", "linear": "claude_ai_Linear", "calendar": "claude_ai_Google_Calendar", "email": "claude_ai_Gmail"}`).
It returns the set of server keys, which is what the init guard allows.

- [ ] Tests: `gh pr list …` matches a fixture; an unmatched argv exits 1 and logs `unstubbed`; the
  config JSON has one entry per persona with absolute paths.
- [ ] Implement. Commit.

### Task 8: Stub coverage lint

**Files:** `harness/coverage_lint.py`, `tests/eval/stubs/unserved.txt`; test
`tests/unit/test_eval_coverage_lint.py`.

`referenced_tools(phases_dir) -> dict[str, set[str]]` collects, per connector phase file:
- backticked snake_case tokens that are a catalog tool name for that persona, or that look like
  tools (`^(slack|gcal|gmail)_\w+$`, or `^(list|get|save|search)_\w+$`);
- `gh <sub> <sub>` and `gh api`.

`lint(phases_dir, catalog_dir, unserved) -> list[str]` returns each referenced tool that is neither
served nor listed in `unserved.txt`. Write tools (`save_issue`, `save_comment`) are served,
returning `ok` and captured, so a run that writes back is visible.

- [ ] Tests: the live phases lint clean; a temp phase naming `slack_fly_to_moon` is reported.
- [ ] Implement and fill the catalogs until the lint is clean. Commit. Review. Open PR 2. Send the
  number.

---

## PR 3 — runner, metrics, first checks (branch `feat/eval-runner`, stacked on PR 2)

### Task 9: Settle the two slice-3 facts by experiment and record them in the spec

Both experiments are N=1 with Haiku, a scratch `CLAUDE_CONFIG_DIR`, and `--strict-mcp-config`.
- **(a)** `--max-budget-usd 0.001` with a prompt that needs several turns: does the run stop with a
  budget error on an OAuth login?
- **(b)** `--allowedTools "Read(<tmpvault>/**)"` with `dontAsk`, asking it to read one file in the
  vault and one outside: is the outside read denied (does it show in `permission_denials`)? Also
  try the `//abs/path/**` form.
- [ ] Run both. Record the results in spec §4.1 / §4.3 and drop the "slice 3 verifies" hedges.
  Commit.

### Task 10: Pure metrics with a self-consistent sample record

**Files:** `harness/metrics.py`, a golden stream `tests/eval/golden/stream-basic.jsonl` (synthetic,
hand-written); tests `tests/unit/test_eval_metrics.py` and `tests/unit/test_eval_purity.py`.

**Produces:**
```python
@dataclass(frozen=True) class ToolCall: seq: int; name: str; input: dict; is_error: bool; position: float
@dataclass(frozen=True) class SampleMetrics:
    calls: tuple[ToolCall, ...]; tool_call_count: int; error_count: int; turns: int
    input_tokens: int; output_tokens: int; cache_read_tokens: int; cost_usd: float | None
    duration_s: float | None; denials: tuple[str, ...]; model: str | None
    mcp_servers: tuple[str, ...]; skipped_lines: int
def parse_stream(lines: Iterable[str]) -> SampleMetrics
def conforms(m: SampleMetrics) -> list[str]          # empty = consistent
def longest_search_run(calls) -> int                 # grep-spiral length
def whole_reads(calls, filename: str) -> int         # Read with no offset/limit
def repeated_calls(calls) -> int
def outside_reads(calls, roots: Sequence[str]) -> int
def position_buckets(calls, name: str) -> dict[str, int]   # early/early_mid/late_mid/late
```

Positions are `(seq-1)/(total-1)`, and a single call gets 0.5 (the idea is borrowed generically
from the tool-position analysis the maintainer pointed to). `conforms` re-derives
`tool_call_count == len(calls)` and `error_count`, and checks that positions are in `[0,1]` and
monotone. Unknown or partial lines increment `skipped_lines` and never raise.

`test_eval_purity.py` parses (doesn't import) `metrics.py` and `checks/*.py`, and asserts there
are no imports of `subprocess`, `os`, `socket`, `urllib`, `requests`, and no `open(`.

- [ ] Tests on the golden stream:
  - counts;
  - a 7-search spiral;
  - one whole read of `knowledge-base.md`;
  - one denial;
  - init model and servers;
  - a truncated last line counted;
  - positions.
- [ ] Implement. Commit.

### Task 11: Checks `placement` and `no_drop`

**Files:** `harness/checks/__init__.py`, `checks/placement.py`, `checks/no_drop.py`, golden vaults
`tests/eval/golden/busy-weekday/{good,bad}/action-items/*.md`; test `tests/unit/test_eval_checks.py`.

**Produces:**
```python
@dataclass(frozen=True) class CheckResult: name: str; passed: bool | None; details: tuple[str, ...]
# passed=None means "could not evaluate" (missing_output)
def placement(items_today: list[ActionItem], expected: dict[str, set[str]]) -> CheckResult
def no_drop(items_yesterday, items_today, *, archived_tags: set[str], expected_closures: set[str]) -> CheckResult
```

Sections are compared by their leading emoji (`🔴`, `🟡`, `🟢`, `✅`, `🅿️`), taken from
`ActionItem.section`. The caller (the runner) does the file I/O with `parser.parse_file` and passes
the item lists in, which keeps the checks pure.

- [ ] Tests: `good` passes both; `bad` fails placement on two named tags and no_drop on one;
  `items_today=None` gives `passed=None`.
- [ ] Implement. Commit.

### Task 12: `claude_shim.py` with the init-event guard

**Files:** `harness/claude_shim.py`, `harness/runner_env.py`; test `tests/unit/test_eval_shim.py`,
which uses a fake `claude` script that emits a canned stream.

**Produces:** the shim is invoked as `CLAUDE_BIN` by `claude-with-retry.sh` with the runner's
argv. It reads `SCOUT_EVAL_*` env (`RESULT_DIR`, `MCP_CONFIG`, `ALLOWED_SERVERS`, `MODEL`,
`REAL_CLAUDE`, `ALLOWED_TOOLS`, `TIMEOUT_S`). It drops `--permission-mode`/`--model` from the
runner's argv and keeps `-p`, `--max-budget-usd` and `--name`, then adds the spec §4.1 flags.

It execs the real claude with `CLAUDE_CONFIG_DIR=<result>/claude-config` and
`CLAUDE_SECURESTORAGE_CONFIG_DIR=""`, and tees stdout to `<result>/stream.jsonl`. On the first
`system/init` line, if `mcp_servers` names hold anything outside `ALLOWED_SERVERS`, it kills the
process group, writes `<result>/aborted.json` `{"reason": "mcp_leak", "servers": [...]}` and exits
3. On timeout it kills and writes `{"reason": "timeout"}`. It prints the final `result` text to
stdout for the run log.

`runner_env.build_env(...) -> dict[str, str]` sets:
- `PATH=<stubs/bin>:<engine .venv/bin>:/usr/bin:/bin:/usr/sbin:/sbin` (plus the dir of the real
  `claude`);
- `SCOUT_FORCE_MODE`, `TZ=UTC`, `SCOUT_USER_TIMEZONE=UTC`, `GIT_CONFIG_GLOBAL`, `GH_CONFIG_DIR`;
- the `SCOUT_EVAL_*` variables.

- [ ] Tests: argv rewriting (exact list); a fake claude whose init lists `claude.ai Slack` gives
  exit 3 and `aborted.json`; a clean fake stream is tee'd intact; the timeout path.
- [ ] Implement. Commit.

### Task 13: `eval.sh run` and the `busy-weekday` scenario

**Files:**
- `harness/runner.py`, `harness/plan.py`;
- `tests/eval/scenarios/busy-weekday/{scenario.yaml,connectors.yaml,fixtures/*,vault/action-items/action-items-{{d-1}}.md}`;
- tests `tests/unit/test_eval_plan.py` and `test_eval_scenario.py`.

`scenario.yaml` holds:
- `mode: morning-consolidation`;
- `weekdays_only: true`;
- `personas: [slack, linear, calendar]`;
- `expected_placement: {TAG: [🟡, 🔴], …}`;
- `expected_closures: [TAG]`;
- `checks: [placement, no_drop]`.

Seeds come from spec §7.1, minus #372 and the context notes.

`plan.session_plan(scenarios, reps, model, opus, many) -> Plan` raises `PlanRefused` above 10
sessions without `many`, or when the model is opus without `opus`. `Plan.describe()` is the
one-line summary from spec §9.

`runner.run_sample(scenario, *, today, model, results_root) -> SampleResult`:
1. build the vault;
2. write the MCP config;
3. run `bash <vault>/run-scout.sh` with the env and a timeout;
4. parse the stream;
5. run the checks;
6. write `metrics.json` and `checks.json`;
7. return.

`cli run <scenario>… [--reps N] [--opus] [--many] [--yes] [-j 1|2]` prints the plan and waits
for Enter unless `--yes`. Afterwards it prints a per-check table and a metrics summary. A login
failure (`Not logged in`) aborts the whole command.

- [ ] Tests (no claude): plan refusals and the describe text; the scenario's seeds parse;
  `expected_placement` tags all exist in yesterday's file; the scenario scans clean (scrub).
- [ ] Implement. Commit.

### Task 14: One live sample and the measured numbers

- [ ] `plugin/scripts/eval.sh run busy-weekday --reps 1 --yes`, on Sonnet.
- [ ] Read the result dir. Confirm:
  - the init guard saw only stub servers;
  - the stub calls landed in `calls.jsonl`;
  - the checks ran.

  Fix harness bugs, never the phases. A failing check on main is a finding to report, not to fix
  here.
- [ ] Record the measured duration, tool calls and usage-equivalent cost in spec §4.2 (replacing
  the estimates). Commit.
- [ ] Fresh-context review of the branch. Fix its findings. Open PR 3 and send the number with the
  first measured result.
