# Run health P7–P8: headless engine updates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Engines that Scout.app doesn't manage (`install.sh`, `claude-code`) install each published stable release on their own. Every attempt is recorded and shows up on the run-health surface. App-managed and `dev` engines are never touched.

**Architecture:** One new orchestrator, `scoutctl self-update run`, started detached by `heartbeat.sh` at most once a day. It writes one row per attempt to `.scout-logs/update-outcomes.jsonl`. `health.py` turns that ledger into `update.*` findings, and `health notify` pushes them. P7 builds the updater and its findings. P8 turns on the setup question and flag, fixes the config template, and adds the app's "Updates automatically" row (plugin and app in one commit, with the contract fixture).

**Tech Stack:** Python 3.11+ engine (typer, pytest), bash templates, SwiftUI app (XCTest).

**Spec:** `docs/superpowers/specs/2026-10-11-run-health-and-headless-updates-design.md`, §7, §4.1–4.2 (`update.*` rows), §9 P7/P8 and §11 "Updater". All §12 decisions are accepted (Q1 default on, Q2 hold only on the parser sidecar or a red doctor, Q5 D8 now, Q7 `dev` excluded).

## Global Constraints

- `managed_by: scout-app` is refused **first**, before any config read, network call or subprocess (D7, §7.2.1). A test asserts zero subprocess calls and zero fetcher calls.
- `managed_by: dev` / `unknown` are never updated. They get a `not_eligible` row with a `target` (read-only check), which feeds `update.behind` as a **note**.
- "Available" means: `stable`'s `marketplace.json` says V **and** `GET /repos/Raven-Scout/Scout/releases/tags/vV` returns a release that is not a draft, not a prerelease, and was published ≥ `auto_update.soak_hours` (default 24) ago (D8).
- No lock is held for fetch or venv. Only `bootstrap auto` takes the vault lock. `LockBusyError` (`mutated:false`) → `deferred` (D9).
- Roll back only before `vault`: a fetch or venv failure leaves the pointer, plist/cron, shim and venv untouched. From `vault` on, the update rolls forward: `failed`, red, notify (D10). Never delete engine dirs.
- `install-venv.sh` deletes an existing venv, so it never runs against the live `engine_root`. A test asserts that `new_root != pointer.engine_root`.
- Tests never touch real launchd, crontab, desktop notifications, `~/Scout`, `~/scout-plugin`, `~/.local/state/scout`, or the network. `HOME` is a `tmp_path`, `claude`/`scoutctl` are fakes on `PATH` or injected, and notify channels are injected.
- No paid APIs. GitHub is called unauthenticated, at most once a day.
- Fixtures are anonymised (`plugin/CLAUDE.md`). `doctor-run-health.json` is regenerated with `SCOUT_REGEN_CONTRACT=1`, never edited by hand, and its app copy is updated in the same commit.
- Every PR: CHANGELOG `[Unreleased]`, the full `plugin/engine` suite including perf, ruff run from `plugin/engine`, coverage ≥ 98%.

## Review Focus

1. **The pointer is missing, or names another interpreter** (a second engine on the host). Expect `not_eligible: pointer_mismatch`, with no fetch. → Task 2 test.
2. **`claude plugin list --json` in either shape** (a top-level list, or `{"plugins": {...}}`), or with no scout entry. Expect the root to resolve from either shape, and a missing entry to give `failed` at step=fetch, with the old engine live. → Task 3 test.
3. **The fetched root equals the live root** (Claude Code didn't move versions). Expect `failed` at step=fetch, and `install-venv.sh` never runs. → Task 3 test.
4. **A 72 h laptop-asleep gap.** Expect the next tick outside off-peak to run (last check > 36 h), and `deferred` rows alone to never turn yellow before 72 h. → Task 2 and Task 4 tests.
5. **A malformed or truncated `update-outcomes.jsonl` line** (a partial append). Readers skip it, and the evaluator never raises. → Task 1 test.

---

## P7 — `self-update run` (branch `feat/self-update-run`, PR 1)

### Task 1: Update ledger + `update.*` findings

**Files:**
- Create: `plugin/engine/scout/update_outcomes.py`
- Modify: `plugin/engine/scout/health.py` (`evaluate` gains `update_findings`)
- Test: `plugin/engine/tests/unit/test_update_outcomes.py`, `plugin/engine/tests/unit/test_health_update_findings.py`

**Interfaces (produces):**
```python
LEDGER = ".scout-logs/update-outcomes.jsonl"
RESULTS = ("up_to_date","not_eligible","waiting_release","deferred","held","applied","applied_with_sidecars","failed")
STEPS = ("check","fetch","venv","vault","verify")

@dataclass(frozen=True)
class UpdateOutcome:
    at: datetime; from_version: str; target: str | None; managed_by: str
    result: str; step: str; doctor: str | None; sidecars: tuple[str, ...]
    detail: str; log: str | None; release_published_at: datetime | None = None
    def to_json(self) -> dict  # keys exactly as spec §4.1 ("from", epoch "at") + "release_published_at"

def append(vault: Path, row: UpdateOutcome) -> None   # O_APPEND, one line, mkdir -p
def read(vault: Path) -> list[UpdateOutcome]          # skips bad lines, oldest first
```
`health.update_findings(vault, rows, *, now) -> list[Finding]`, with `source="update ledger"`:
- `update.failed`: red, when the newest row is `failed`. The summary names the step, the fix is `scoutctl bootstrap upgrade` plus the log path, and `since` is the first failed row of the trailing run.
- `update.held`: yellow, when the newest row is `held`, or the trailing `deferred` run started more than 72 h ago.
- `update.sidecars`: yellow, when `brain_merge.pending_brain_sidecars(vault)` is non-empty and the newest row's `sidecars` lists them.
- `update.behind`: note. Either the newest row is eligible with `target > from` and `release_published_at` more than 14 days old, or the newest row is `not_eligible` (`managed_by=dev|unknown`) with `target > from`.
- `update.last`: note, added in P8 (Task 6).

- [ ] Failing tests: round-trip; a bad line is skipped; one test per finding plus its boundary (deferred for 71 h vs 73 h; behind for 13 d vs 15 d; dev behind at all; app-managed rows never exist); `evaluate()` on a vault with no update ledger has no `update.*` findings, so the contract fixture is unchanged.
- [ ] Implement, run `pytest tests/unit/test_update_outcomes.py tests/unit/test_health_update_findings.py tests/unit/test_doctor_run_health_contract.py`, then commit.

### Task 2: Eligibility, release check and the scheduled gate

**Files:**
- Modify: `plugin/engine/scout/scripts/self_update.py` (adds `release_status`)
- Create: `plugin/engine/scout/scripts/self_update_run.py` (the eligibility and gate half)
- Test: `plugin/engine/tests/unit/test_self_update_run_eligibility.py`, `plugin/engine/tests/unit/test_self_update_release.py`

**Interfaces:**
```python
# self_update.py
RELEASE_API = "https://api.github.com/repos/Raven-Scout/Scout/releases/tags/v{version}"
@dataclass(frozen=True)
class ReleaseStatus: published: bool; published_at: datetime | None; reason: str  # "draft"|"prerelease"|"missing"|"soaking"|""
def release_status(version: str, *, now: datetime, soak_hours: float, fetch: Callable[[str], dict | None] = _fetch_release) -> ReleaseStatus
# _fetch_release: 404 → None; URLError/timeout → raises RuntimeError

# self_update_run.py
@dataclass(frozen=True)
class Deps:   # every side effect injectable; tests build it with fakes
    home: Path; vault: Path; now: Callable[[], datetime]
    read_pointer: Callable[[], EnginePointer | None]; interpreter: str
    load_config: Callable[[], dict]; check: Callable[[], UpdateStatus]
    release: Callable[[str], ReleaseStatus]; run: Callable[[list[str], dict], CompletedProcess]
    lock_busy: Callable[[], bool]; next_slot_within: Callable[[timedelta], bool]
    notify: Callable[[UpdateOutcome], None]
def eligibility(deps: Deps) -> str | None   # None = eligible, else the not_eligible reason
def scheduled_gate(deps: Deps, rows: list[UpdateOutcome]) -> str | None  # None = run now; else why not (no row written)
```
Eligibility order (§7.2):
1. `scout-app` → `"managed_by=scout-app"`, returned **before** `load_config` is called.
2. `auto_update.enabled is not True` → `"disabled"`.
3. No pointer, or `pointer.python != interpreter` → `"pointer_mismatch"`.
4. `dev` / `unknown` → `"managed_by=<x>"`.
5. `channel != "stable"` → `"channel=<x>"`.

The scheduled gate covers the 36 h / off-peak window (heartbeat's `off_peak` config, default 23–06), the "one applied version per 24 h" rule and the §7.5 backoff (24 h after a failure of V; stop after 3 failures of V unless `--force` or a newer V appears). Lock busy, or a slot due within 45 min, → a `deferred` row (written, not a gate).

- [ ] Failing tests. **The first test** is `scout-app` with `auto_update.enabled: true` and a `Deps` whose `run`, `check`, `release` and `load_config` raise if called: the result is `not_eligible`, and only `read_pointer` was touched. Then: disabled; pointer missing; pointer for another python; dev and unknown each get a row with `target` (the check runs, nothing else does); a channel other than stable. Release: draft, prerelease, 404, soaking at 23 h vs 25 h, a network error. Gate: in off-peak; outside off-peak with the last check at 35 h vs 37 h; backoff after 1 failure at 23 h vs 25 h; 3 failures of V → skip, a newer V → runs, `--force` → runs.
- [ ] Implement, run, commit.

### Task 3: Steps fetch → venv → vault → verify, and the CLI

**Files:**
- Modify: `plugin/engine/scout/scripts/self_update_run.py` (`run_update`)
- Modify: `plugin/engine/scout/cli.py` (`self-update run`; the help text drops "check only in v0.4")
- Test: `plugin/engine/tests/unit/test_self_update_run_steps.py`, `plugin/engine/tests/integration/test_self_update_run_cli.py`

**Interfaces:**
```python
def run_update(deps: Deps, *, scheduled: bool, dry_run: bool, force: bool) -> UpdateOutcome | None
#   None only when the scheduled gate says "not now". Appends the row, then calls deps.notify(row).
def resolve_install_path(list_json: str) -> Path | None   # both `claude plugin list --json` shapes, id "scout@scout-plugin"
```
CLI: `scoutctl self-update run [--scheduled] [--dry-run] [--force] [--json]`. It always exits 0 except on a usage error (the heartbeat must not care). `--dry-run` prints the plan, makes no fetch and writes no row. On a non-`deferred` scheduled row it writes `.scout-cache/self-update-<local date>.done`.

Steps (§7.3):
- **check**: no target, or `target <= from` → `up_to_date`. A network error is `deferred`, and the third consecutive one is `failed`, step=check. Unpublished or soaking → `waiting_release`.
- **pre-flight**: `bootstrap_auto.detect(vault)` REFUSED, or the current doctor severity is red → `held`.
- **fetch**: `claude plugin marketplace update scout-plugin`, then `claude plugin update scout@scout-plugin --scope user`, then `claude plugin list --json`. Require `<root>/.claude-plugin/plugin.json` version == target and `root != pointer.engine_root`. The claude binary comes from `scout-config.yaml` `claude_bin`, else `PATH`.
- **venv**: `bash <root>/scripts/install-venv.sh` with `SCOUT_VENV_DIR` removed from the env, then `<root>/.venv/bin/scoutctl version` == target.
- **vault**: `<root>/.venv/bin/scoutctl bootstrap auto --no-interactive --yes --json --managed-by <same> [--claude-bin X]` with `SCOUT_DATA_DIR=vault`. A refusal with `mutated:false` and an error containing `"held by live PID"` → `deferred`. Any other refusal, or `mutated:true` → `failed`, step=vault.
- **verify**: `<root>/.venv/bin/scoutctl bootstrap doctor --json` severity is not red → `applied`, or `applied_with_sidecars` when `conflicts` is non-empty. Otherwise → `failed`, step=verify.

- [ ] Failing tests. These use a fake `claude` (a bash script in `tmp_path/bin`) that materialises `tmp_path/cache/scout/<target>/` with `plugin.json` and a fake `scripts/install-venv.sh` that writes a `.venv/bin/scoutctl` stub (prints the version, and returns canned `bootstrap auto` / `doctor` JSON from env-selected files). Cases: the happy path → `applied` and the stub saw `--managed-by install.sh`; `claude-code` is preserved; a fetch failure (exit 1, no entry, a version mismatch, the same root) → `failed` at fetch, the pointer file byte-identical and `install-venv.sh` not run; a venv failure → `failed` at venv, the pointer byte-identical; a lock refusal → `deferred`; `mutated:true` → `failed` at vault; red verify → `failed` at verify; conflicts → `applied_with_sidecars`; a red pre-flight doctor → `held`; a parser sidecar → `held`; `--dry-run` writes no row and runs nothing; the CLI `--json` round-trip; the marker is written for `waiting_release` and not for `deferred`.
- [ ] Implement, run, commit.

### Task 4: Notifications and the heartbeat hook

**Files:**
- Modify: `plugin/engine/scout/scripts/self_update_run.py` (`default_notify`)
- Modify: `plugin/templates/scripts/heartbeat.sh.tmpl`
- Test: `plugin/engine/tests/unit/test_self_update_run_notify.py`, `plugin/engine/tests/unit/test_heartbeat_template.py` (extend the existing heartbeat template test if one exists, else create it)

`default_notify(row)`:
- `health_notify.notify(vault)` after every row, which covers `update.failed` red, its reminders and the recovery.
- One `Push(kind="info")` through `health_notify.fan_out` for `applied_with_sidecars`, naming the files and `scoutctl bootstrap drift` / `resolve`.
- One `Push(kind="info")` for the **first** `applied` on a vault, naming `auto_update.enabled` and `scoutctl config set-auto-update --disabled` (§9 P7).

Whether a push is the first is decided from the ledger: no earlier `applied*` row.

Heartbeat block, after the health sweep and skipped under `--dry-run`:
```bash
SU_MARKER="$SCOUT_DATA_DIR/.scout-cache/self-update-$(date +%F).done"
if [[ ! -e "$SU_MARKER" ]]; then
    nohup "$SCOUTCTL_BIN" self-update run --scheduled \
        >>"$SCOUT_DATA_DIR/.scout-logs/self-update-$(date +%F).log" 2>&1 </dev/null &
    disown 2>/dev/null || true
fi
find "$SCOUT_DATA_DIR/.scout-cache" -name 'self-update-*.done' -mtime +7 -delete 2>/dev/null || true
```

- [ ] Failing tests:
  - Notify: injected channels see one info push for the first applied update, and none for the second.
  - Notify: `applied_with_sidecars` pushes once.
  - Notify: `failed` routes through `health_notify.notify` (an injected channel receives a red `update.failed`).
  - Template: render it and run it with a stub `scoutctl` on `PATH` that logs argv. No marker → `self-update run --scheduled` is invoked. Marker present → it isn't. `--dry-run` → it isn't. Bash 3.2-safe (`set -u`, empty arrays).
- [ ] Implement, run, commit.

### Task 5: Docs, CHANGELOG, full verification, PR

- [ ] CHANGELOG `[Unreleased]` → Added: `scoutctl self-update run` and the `update.*` findings. **Behaviour change:** existing non-app vaults whose `auto_update.enabled` is already `true` start updating themselves; turn it off with `scoutctl config set-auto-update --disabled`.
- [ ] Update `self_update.py`'s module docstring. Add `docs/` / README run-health lines for `update.*` if the findings table is documented there.
- [ ] From `plugin/engine`: `.venv/bin/python -m pytest tests/ --cov=scout --cov-fail-under=98` (includes perf), then `.venv/bin/ruff check . && .venv/bin/ruff format --check .`.
- [ ] Push, open the PR against `main`, bind it, and send the PR number and summary to the coordinator. Don't merge.

## P8 — switch it on (branch `feat/self-update-switch-on`, stacked on P7, PR 2)

### Task 6: `update.last` note + app "Updates automatically" row (ONE commit, plugin + app + fixture)

**Files:**
- Modify: `plugin/engine/scout/health.py`. `update.last` (note) is emitted whenever the newest update row is not `not_eligible`. Summary: `Last checked <iso>: <result>[ (<target>)]`.
- Modify: `plugin/engine/tests/unit/test_doctor_run_health_contract.py`. The builder vault gains one `applied` update row. Regenerate `tests/fixtures/contract/doctor-run-health.json`, then `cp` it to `apps/macos/ScoutTests/Fixtures/doctor-run-health.json`.
- Modify: `apps/macos/Scout/Engine/EngineSettingsModel.swift`. Add `updatesAutomatically: Bool` (external state and an `update.last` finding present) and `autoUpdateSummary: String?` (`"Updates automatically. Last checked <relative>: <result>"`, with an `update.failed` / `update.held` summary preferred when present). `showsHandOff` is false for `.external` when `updatesAutomatically`.
- Modify: the Settings ▸ Engine view that renders `showsHandOff` (find it with `grep -rn showsHandOff apps/macos/Scout`). It renders the summary row instead.
- Test: `apps/macos/ScoutTests/Engine/DoctorReportTests.swift` (pins), `apps/macos/ScoutTests/Engine/EngineSettingsModelTests.swift` (external + `update.last` → no hand-off, summary text; external without it → hand-off unchanged; managed → never shows it).

- [ ] Failing Python test for `update.last`. Regenerate the fixture. Failing Swift tests. Implement. Run `xcodebuild test` for the Scout scheme, filtered to the Engine tests (as `apps/macos/CLAUDE.md` says). Commit everything in one commit.

### Task 7: Setup question, `bootstrap auto --auto-update`, template and leftovers

**Files:**
- Modify: `plugin/engine/scout/cli.py`. `bootstrap auto` gains `--auto-update/--no-auto-update` (default None). On install it goes into `BootstrapConfig`; on upgrade/migrate, when not None, it goes through `write_auto_update(enabled=...)` after a successful run. `setup` gains the same flag.
- Modify: `plugin/engine/scout/setup_wizard.py`. When installing with `managed_by` in (`install.sh`, `claude-code`), ask "Keep Scout up to date automatically? [Y/n]" (default yes; `--yes` → yes; an explicit flag wins, no prompt). Pass it on through `auto_argv`. Never ask for `scout-app` or `dev`.
- Modify: `plugin/templates/scout-config.yaml.tmpl`. Use the §7.9 comment verbatim, add `soak_hours: 24`, and render `enabled` from the vault's existing `auto_update.enabled`, else the packaged default (find the render site with `grep -rn auto_update_enabled plugin/engine/scout`).
- Modify: `plugin/engine/scout/scripts/auto_update_config.py`. The `_APPENDED_BLOCK_COMMENT` drops "/scout-update".
- Modify: `plugin/commands/scout-setup.md`. Delete Step 3's auto-update question and the `--auto-update` hand-off (keep the budget half of Step 3 if it is still there).
- Modify: `docs/superpowers/specs/2026-10-06-scout-setup-retirement.md` (or wherever S2 lives). Add a one-line amendment pointing to run-health §7.9.
- Test: `tests/unit/test_setup_wizard.py` (prompt shown for install.sh/claude-code, not for scout-app/dev; `--yes` → `--auto-update`; `--no-auto-update` → no prompt), `tests/integration/test_bootstrap_auto_cli.py` (flag written on install and on upgrade, with comments preserved; omitted → key untouched), and a template render test (an existing `false` survives a re-render; an absent key → default true; `soak_hours` present).

- [ ] Failing tests, implement, run, commit.

### Task 8: CHANGELOG, full verification, PR

- [ ] CHANGELOG `[Unreleased]`: the setup question (default on for headless installs), the flag, the template fix, the app row, and the `/scout-setup` Step 3 removal.
- [ ] Full engine suite including perf, coverage ≥ 98%, ruff from `plugin/engine`, and the app Engine tests.
- [ ] Push, open the PR with base = the P7 branch, then send it to the coordinator. The manual end-to-end checks in §11 (a Linux container and macOS with an app-managed engine) are listed in the PR as **not run by the agent**. They need a host where a real upgrade is allowed.

## Open points for the coordinator

1. **`update.last` is an addition to §4.2.** §7.8 wants "Last checked <when>: <result>" from the `update.*` findings, but a healthy `applied` / `up_to_date` state produces none. A note-severity `update.last` carries it without changing severity.
2. **The read-only check also runs for `dev`/`unknown` when `auto_update` is off?** Spec order (§7.2) puts `disabled` before `dev`, so a dev engine with the setting off gets no `target` and no `update.behind`. As planned, I follow the spec order. The alternative is to run the read-only check for every non-app engine regardless of the setting, which is what #199's ask implies.
3. **#360's text says `git pull --ff-only` for `dev`.** It is superseded by D7 and the brief: `dev` is never updated.
