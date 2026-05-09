# Plan 8 — `/scout-setup` repair + onboarding/upgrade flow

**Status:** Design (brainstorm complete, awaiting approval before plan write-up)
**Date:** 2026-05-09
**Predecessor:** Plan 7 (Schedules tab visual rewrite, shipped) → arc audit identified `scout-setup` staleness as biggest gap
**Successor:** Plan 9 (dreaming-proposals as canonical edit log + reverse-promotion)

---

## 1. Problem

After Plans 1–7, the running Scout system uses a single `com.scout.schedule-tick.plist` dispatcher (every 5 min) plus `com.scout.heartbeat.plist`, dispatching slots from `~/Scout/.scout-state/schedule.yaml`. All 8 legacy per-slot plists were deleted in Plan 5.

`scout-plugin/commands/scout-setup.md` still seeds the legacy world:

1. **Schedule install (Step 5) is wrong.** Generates two now-deleted per-mode plists (`com.{name}.briefing.plist`, `com.{name}.dreaming.plist`) from `templates/launchd-plist.tmpl`. Never installs `schedule-tick`, `heartbeat`, the engine venv, or seeds `schedule.yaml`. A fresh user runs `/scout-setup` today and gets a non-functional install.
2. **Runner templates have stale clock-derived mode logic** (`case $HOUR in {{BRIEFING_HOUR}})`). The live runners in `~/Scout/` already use `MODE="${SCOUT_FORCE_MODE:-manual}"` because we hand-edited them during Plan 5. Future scaffolding fixes don't propagate.
3. **Connector probes (Step 2) use stale tool names** (`gcal_list_calendars`, `gmail_get_profile`, `slack_read_user_profile`) without MCP namespace prefixes — they all fail-out and the wizard concludes nothing is connected.
4. **Reset / Reassemble paths are unsafe** — Reset `rm -rf`s the vault but doesn't bootout the live `com.scout.*` jobs (orphans them); Reassemble overwrites SKILL.md/DREAMING.md/RESEARCH.md verbatim, clobbering months of dreaming-proposal-driven edits.
5. **No `/scout-update`** — plugin updates have no path into a running vault. Jordan's only path to take a plugin improvement is hand-editing files, which is exactly how we got into the drift state.
6. **Heartbeat plist has no plugin source-of-truth** (the live one was hand-installed).
7. **Pre-flight only checks `scout-config.yaml`** — doesn't notice live launchd jobs or `.scout-state/`. A vault that lost its config but has running jobs slips through as "no existing instance."
8. **Linux scheduling path is dead code** — generates cron entries calling legacy runners that no longer exist as schedulable units.
9. **`scoutctl` is invisible to the wizard** — the entire engine subsystem (Plans 1–7) never gets touched.

## 2. Goals

- Fresh `/scout-setup` produces a fully working Plan-5 install on macOS or Linux without hand-fixes.
- New `/scout-update` lets existing vaults pick up plugin changes without clobbering vault edits.
- Pipeline is **stage-based and extensible** so future categories (Scout-generated runtime files; sqlite/duckdb migrations) plug in without rewriting `/scout-update`.
- Slash commands stay thin; install/upgrade logic lives in `scoutctl bootstrap` (testable in pytest).
- Connector probes survive future MCP namespace shifts via a declarative registry.

## 3. Non-goals

- **Plan 9:** dreaming-proposals as a canonical structured edit log; reverse-promotion (vault edits → plugin phase files).
- **Plan 11+:** runner unification to a `scoutctl run <mode>` shim.
- **Future categories implementation:** Scout-generated runtime hooks/connectors; plugin-managed databases. Pipeline *stages* exist, but they're empty in Plan 8.
- **Settings tab DS adoption** (Plan 7-polish followup).

## 4. Architecture

### 4.1 Two commands, thin wrappers around `scoutctl bootstrap`

| Command | Use | Refuses if |
|---------|-----|------------|
| `/scout-setup` | Greenfield install | Vault detected (any of: `scout-config.yaml`, `.scout-state/`, `~/Library/LaunchAgents/com.scout.*.plist`) |
| `/scout-update` | Idempotent upgrade | No vault detected |

Each slash command:
1. Runs pre-flight detection.
2. Collects user input (instance name, connectors, schedule customizations).
3. Calls `scoutctl bootstrap {install|upgrade}` with the collected config.
4. Reports result.

The engine entry point (`scoutctl bootstrap`) is the testable surface. Slash commands handle conversational UX only.

### 4.2 File ownership taxonomy

| Cat | Behavior on `/scout-update` | Files |
|-----|------|-------|
| 1 — Plugin-owned, always overwrite | Mechanical regeneration | `~/Library/LaunchAgents/com.scout.{schedule-tick,heartbeat}.plist`; `knowledge-base/ontology/{parser.py,__init__.py}`; `action-items/render.py`; `scripts/{budget-check,heartbeat,pre-session-data,cc-session-cache,write-session-cost,rate-limit-detect}.sh`; `hooks/kb-pre-filter.sh` |
| 1b — Plugin-owned, extracted vars | Regenerate body from template, sub vars from `scout-config.yaml`, back up hand-edits to `.bak.YYYY-MM-DD` | `run-{scout,dreaming,research}.sh` |
| 2 — Vault-owned, never touch | User data — entirely off-limits | `knowledge-base/` content; `action-items/` content (not `render.py`); `docs/Wishlist*.md`; `knowledge-base/scout-mistake-audit.md`; `knowledge-base/review-queue.md`; `dreaming-proposals.md`; `CLAUDE.md`; `.gitignore` |
| 3 — Plugin-seeded once, then hands-off | Write only on first install | `scout-config.yaml`; `.scout-state/schedule.yaml` |
| 4 — Assembled, edited after assembly | 3-way merge against snapshot | `SKILL.md`; `DREAMING.md`; `RESEARCH.md` |
| 5 (future) — Vault-generated at runtime | Marked, never touched | Hooks/connectors Scout authors during dreaming/research |
| 6 (future) — Plugin-managed databases | Schema migrations, not file overwrite | sqlite/duckdb backing stores |

Categories 5 and 6 are not implemented in Plan 8; the pipeline reserves stages so they can be added without restructuring.

### 4.3 Pipeline (8 stages, behavior varies by command)

| # | Stage | `/scout-setup` (install) | `/scout-update` (upgrade) |
|---|-------|--------------------------|---------------------------|
| 1 | Pre-flight | Vault must NOT exist | Vault MUST exist; check version delta |
| 2 | Schema migrations | Skipped (fresh, no prior state) | Run any in `migrations/` not yet in `applied_migrations` |
| 3 | Cat 1 file writes | Initial write | Overwrite from current templates |
| 4 | Cat 1b runner writes | Initial write (var-templated) | Detect hand-edits, back up, regenerate |
| 5 | Cat 4 assembled files | Assemble + write (no merge) | 3-way merge against snapshots |
| 6 | Job lifecycle | Install launchd jobs / write cron managed block | Bootout + re-bootstrap launchd / replace cron block |
| 7 | Version stamp | Write `version_at_last_setup` + `version_at_last_update` | Update `version_at_last_update` |
| 8 | Doctor smoke | `scoutctl bootstrap doctor` | `scoutctl bootstrap doctor` |

Both commands run all 8 stages; only stages 1, 2, 4, 5, 6, 7 have command-specific behavior.

### 4.4 Hand-edit detection for category 1b (runners)

A hand-edit is detected by exact-content comparison: render the runner template using the variables currently in `scout-config.yaml` and compare byte-for-byte to the file in the vault.

- Equal → silent overwrite (no-op).
- Not equal → vault file copied to `run-scout.sh.bak.2026-05-09`, fresh template rendered in place, action logged to stdout. Subsequent comparisons use the freshly rendered file as the new baseline.

This is intentionally strict — runners are not expected to be hand-edited, and the cost of a false-positive backup (one extra `.bak` file) is much lower than silently overwriting a customization.

### 4.5 3-way merge for category 4

After every assembly (setup or update), snapshot is written to `.scout-state/last-assembled/{SKILL,DREAMING,RESEARCH}.md` (gitignored).

On `/scout-update` stage 5:

```python
for name in ("SKILL", "DREAMING", "RESEARCH"):
    base = read(f".scout-state/last-assembled/{name}.md")  # or current vault file if absent
    theirs = read(f"{name}.md")                             # current vault, with edits
    ours = assemble_from_phases(name)                       # fresh from current plugin phases

    result, conflicts = git_merge_file(base=base, ours=ours, theirs=theirs, marker_diff3=True)
    write(f"{name}.md", result)
    if not conflicts:
        write(f".scout-state/last-assembled/{name}.md", ours)
    else:
        abort_pipeline(f"Conflict in {name}.md — resolve and re-run /scout-update")
```

Legacy vaults (no snapshot from before Plan 8): treat current vault file as the initial snapshot. First `/scout-update` is degenerate (snapshot = theirs → no edits detected → merge result = ours). Subsequent updates see real merges.

This intentionally trades "first /scout-update on legacy vault loses some merge intelligence" for "no need to backfill what edits exist." Plan 9 (dreaming-proposals as edit log) will eliminate the need for snapshots entirely for proposal-driven edits.

### 4.6 Reset path — removed from both commands

Today's `/scout-setup` Reset path (`rm -rf` after a typed confirmation) is removed. Documented as a manual snippet in `/scout-setup`'s pre-flight error message and in `README.md`:

```bash
# macOS
launchctl bootout gui/$UID/com.scout.schedule-tick gui/$UID/com.scout.heartbeat
rm -f ~/Library/LaunchAgents/com.scout.*.plist

# Linux
crontab -l | sed '/# >>> scout-managed >>>/,/# <<< scout-managed <<</d' | crontab -

# Both
rm -rf ~/Scout
```

Rationale: rare, dangerous, easy to do manually. Removes the "type 'reset' to confirm" footgun. `/scout-setup`'s pre-flight detects the half-reset state ("vault gone but launchd jobs running") and fails with this snippet rather than papering over it.

### 4.7 Connector probe registry — `templates/connector-probes.yaml`

Declarative, replaces hardcoded probe calls in scout-setup.md Step 2:

```yaml
slack:
  primary: mcp__plugin_slack_slack__slack_read_user_profile
  fallbacks: [mcp__claude_ai_Slack__slack_read_user_profile]
  needs_user_input: [user_slack_id]
calendar:
  primary: mcp__claude_ai_Google_Calendar__list_calendars
  fallbacks: []
gmail:
  primary: mcp__claude_ai_Gmail__list_labels
  fallbacks: []
linear:
  primary: mcp__plugin_linear_linear__list_teams
  fallbacks: []
github:
  primary: bash
  command: "gh auth status"
  needs_user_input: [github_username, github_repos]
granola:
  primary: mcp__claude_ai_Granola__list_meetings
  fallbacks: []
drive:
  primary: mcp__claude_ai_Google_Drive__list_recent_files
  fallbacks: []
claude_sessions:
  primary: bash
  command: "test -d ~/.claude/projects"
```

The wizard reads this and tries each tool in order, marking the connector as enabled on first success. When MCP namespaces shift, update one YAML file — no wizard prose changes.

### 4.8 Linux scheduling

Add `scoutctl schedule install-cron`:
- Writes a managed block to `crontab -l` between `# >>> scout-managed >>>` / `# <<< scout-managed <<<` markers.
- Block contains:
  - `*/5 * * * * scoutctl schedule tick >> ~/Scout/.scout-logs/cron.log 2>&1`
  - `*/30 * * * * ~/Scout/scripts/heartbeat.sh >> ~/Scout/.scout-logs/cron.log 2>&1`
- Idempotent: removes existing managed block before writing new one.

Add `scoutctl schedule install-all` — platform-agnostic wrapper that picks launchd (`install-plist` + `install-heartbeat-plist`) or cron (`install-cron`) based on `uname -s`. Single platform-detection point.

### 4.9 `scout-config.yaml` additions

```yaml
plugin:
  version_at_last_setup: "0.4.0"
  version_at_last_update: "0.4.0"
  applied_migrations: []
```

Read by stage 1 (pre-flight version delta) and written by stage 7 (version bump). Migration framework is reserved for Plan 8+ but no migrations ship in 0.4.0 itself.

## 5. Plugin file changes

### 5.1 Add

- `engine/scout/defaults/com.scout.heartbeat.plist`
- `engine/scout/defaults/cron-managed-block.tmpl`
- `engine/scout/scripts/install_heartbeat_plist.py`
- `engine/scout/scripts/install_cron.py`
- `engine/scout/scripts/bootstrap.py` (install / upgrade / doctor entry points)
- `engine/scout/scripts/three_way_merge.py` (wraps `git merge-file`)
- `commands/scout-update.md`
- `templates/connector-probes.yaml`
- `templates/dreaming-proposals.md.tmpl`
- `templates/scout-mistake-audit.md.tmpl`
- `templates/review-queue.md.tmpl`
- `templates/.gitignore.tmpl`
- `engine/tests/unit/test_install_heartbeat_plist.py`
- `engine/tests/unit/test_install_cron.py`
- `engine/tests/unit/test_bootstrap_install.py`
- `engine/tests/unit/test_bootstrap_upgrade.py`
- `engine/tests/unit/test_three_way_merge.py`
- `engine/tests/unit/test_connector_probe_registry.py`
- `engine/tests/integration/test_bootstrap_smoke.sh`

### 5.2 Modify

- `commands/scout-setup.md` — rewrite Step 2 (probe registry call) and Step 5 (scoutctl-driven scheduling). Remove Reset/Reassemble branches. Add hardened pre-flight (vault file + launchd jobs + .scout-state). Strip inline templates that move to `templates/`.
- `templates/run-scout.sh.tmpl` — replace clock-derived `case $HOUR in {{BRIEFING_HOUR}})` with `MODE="${SCOUT_FORCE_MODE:-manual}"`. Add `export SCOUT_DATA_DIR="$SCOUT_DIR"`.
- `templates/run-dreaming.sh.tmpl` — same `SCOUT_FORCE_MODE` change.
- `templates/run-research.sh.tmpl` — verify already correct; minor cleanup.
- `engine/scout/cli.py` — register `bootstrap install`, `bootstrap upgrade`, `bootstrap doctor`, `schedule install-all`, `schedule install-cron`, `schedule install-heartbeat-plist`.
- `engine/scout/schedule.py:47` — drop the stale "Reserved for Plan 7" comment on `SlotRuntime.REMOTE`. Replace with: `# Reserved for a future plan (remote routine integration via Anthropic routines API); not yet wired. Loader accepts; dispatcher rejects.`
- `engine/scout/scripts/schedule_tick.py:387–395` — update the `runtime: remote` rejection error message to drop the "reserved for Plan 7" claim. New message: `"slot {slot_key!r} has runtime: remote, which is not yet implemented. Remote routine integration is reserved for a future plan. Edit ~/Scout/.scout-state/schedule.yaml and set runtime: local, or delete the slot."`
- `plugin.json` — add `commands/scout-update.md`; bump `version` to `0.4.0`.

### 5.3 Delete

- `templates/launchd-plist.tmpl` — per-mode plist generator (dead since Plan 5)
- `templates/cron-entry.tmpl` — replaced by managed-block approach in `install_cron.py`

## 6. Pre-flight detail

### 6.1 `/scout-setup` pre-flight

Refuses (with actionable message) if any of:
- `~/Scout/scout-config.yaml` exists
- `~/Scout/.scout-state/schedule.yaml` exists
- Any `~/Library/LaunchAgents/com.scout.*.plist` exists
- macOS: `launchctl list | grep com.scout` returns matches
- Linux: `crontab -l` contains `# >>> scout-managed >>>`

Verifies (auto-installs if missing):
- `~/scout-plugin/.venv/bin/scoutctl` — engine venv. If missing, runs `python3 -m venv ~/scout-plugin/.venv && ~/scout-plugin/.venv/bin/pip install -e ~/scout-plugin/engine`.

### 6.2 `/scout-update` pre-flight

Refuses if `~/Scout/scout-config.yaml` is missing.

Reads `scout-config.yaml`:
- `plugin.version_at_last_update` (or `version_at_last_setup` if first update)
- Compares against `${CLAUDE_PLUGIN_ROOT}/plugin.json` version
- If equal, asks: "no version change detected; force reassembly anyway? (y/N)"

Validates current vault:
- `scoutctl schedule validate` — fail early if `schedule.yaml` is broken
- `scoutctl bootstrap doctor --read-only` — surface any current breakage before touching anything

## 7. Error handling

| Stage | Failure | Recovery |
|-------|---------|----------|
| 1 | Vault detected during `/scout-setup` | Abort with manual reset snippet |
| 1 | Orphan jobs without vault (half-reset state) | Abort with manual reset snippet |
| 1 | engine venv install fails | Abort with `python3 -m venv` instructions |
| 3 | Permission denied / disk full | Abort; rerun is idempotent |
| 4 | Hand-edited runner detected | Back up to `.bak.YYYY-MM-DD`, regenerate, log to terminal output |
| 5 | 3-way merge conflict | Write conflict markers in vault file, abort, prompt user to resolve and re-run |
| 6 | `launchctl bootout` fails | Warn; user runs `scoutctl schedule install-all --force` separately |
| 6 | crontab write fails (Linux) | Warn; user fixes crontab manually |
| 8 | Doctor reports red | Loud warning to terminal; do *not* roll back (rollback is destructive) |

Idempotency property: rerunning the pipeline must converge. Each stage either succeeds or aborts cleanly without partial state. Specifically:
- Stage 3 overwrites are atomic per-file (write to `.tmp`, rename).
- Stage 4 only takes a backup if the file was modified vs the previously-rendered template; otherwise overwrite is silent.
- Stage 5 only writes the snapshot on clean merge; conflict path leaves snapshot stale (rerun after resolution succeeds).

## 8. Testing

### 8.1 Unit (pytest, `engine/tests/unit/`)

- `test_install_heartbeat_plist.py` — mirror of existing `test_install_schedule_plist.py`
- `test_install_cron.py` — managed-block insert/replace/remove against synthetic crontab
- `test_bootstrap_install.py` — install pipeline stages with mocked filesystem; verify file taxonomy honored
- `test_bootstrap_upgrade.py` — upgrade pipeline stages with snapshot scenarios (legacy-no-snapshot, clean merge, conflict)
- `test_three_way_merge.py` — synthetic phase-update scenarios: phase rename, section addition, vault edit at same anchor, conflict detection
- `test_connector_probe_registry.py` — yaml load, fallback chain, command-type probes (`gh`, `test -d`)
- Extend `test_install_schedule_plist.py` for new `install-all` wrapper

### 8.2 Integration smoke (`engine/tests/integration/test_bootstrap_smoke.sh`)

```bash
TEST_VAULT=$(mktemp -d)
SCOUT_DATA_DIR=$TEST_VAULT scoutctl bootstrap install \
    --skip-claude \
    --no-jobs \
    --instance-name TestScout \
    --user-name "Test User" \
    --user-email test@example.com \
    --timezone America/New_York

# Assert: directory tree, every cat-1 file written, schedule.yaml valid
test -f $TEST_VAULT/SKILL.md
test -f $TEST_VAULT/scripts/heartbeat.sh
test -f $TEST_VAULT/.scout-state/schedule.yaml
SCOUT_DATA_DIR=$TEST_VAULT scoutctl schedule list

# Run upgrade against same vault — should be idempotent
SCOUT_DATA_DIR=$TEST_VAULT scoutctl bootstrap upgrade --skip-claude --no-jobs

# Verify version bumped, snapshot present
grep "version_at_last_update" $TEST_VAULT/scout-config.yaml
test -f $TEST_VAULT/.scout-state/last-assembled/SKILL.md

rm -rf $TEST_VAULT
```

`--no-jobs` skips launchd/cron mutation so the smoke test doesn't pollute the host. CI can run on macOS and Linux runners.

### 8.3 `scoutctl bootstrap doctor`

Non-mutating health check. Used as pipeline stage 8 and as a standalone diagnostic.

Checks:
- Vault directory present at `$SCOUT_DATA_DIR`
- launchd (macOS) jobs `com.scout.schedule-tick` and `com.scout.heartbeat` registered, OR cron managed block present (Linux)
- `schedule.yaml` parses and validates
- Every cat-1 file exists with non-zero content; sha matches plugin template
- `.scout-state/last-assembled/{SKILL,DREAMING,RESEARCH}.md` present, non-empty
- `scout-config.yaml` has `plugin.version_at_last_update` set

Output: green (all checks pass) / yellow (warnings) / red (errors). Exit code: 0 / 1 / 2.

## 9. Implementation sequencing

1. **Engine core** — `bootstrap.py`, `three_way_merge.py`, `install_heartbeat_plist.py`, `install_cron.py`, all unit tests
2. **Engine CLI** — `scoutctl bootstrap {install,upgrade,doctor}`, `schedule install-all`, `schedule install-cron`, `schedule install-heartbeat-plist` + integration smoke test
3. **Plugin templates** — extract inline template blocks from scout-setup.md; add `connector-probes.yaml`
4. **Plugin: rewrite `commands/scout-setup.md`** against new engine surface; remove Reset; rewire Step 2 + Step 5
5. **Plugin: add `commands/scout-update.md`**
6. **Plugin: fix runner templates** (`SCOUT_FORCE_MODE`, `SCOUT_DATA_DIR`)
7. **Plugin: delete dead templates** (`launchd-plist.tmpl`, `cron-entry.tmpl`); bump `plugin.json` to `0.4.0`
8. **End-to-end test** — clean macOS user dir + clean Linux user dir; run `/scout-setup`, then `/scout-update`
9. **Live-vault test** — Jordan runs `/scout-update` against his `~/Scout/`; verify no clobbering of vault edits, version bump records, snapshot files appear
10. **Ship** — tag `scout-plugin v0.4.0`

## 10. Out of scope (deferred to later plans)

- **Plan 9 — dreaming-proposals as canonical edit log + reverse-promotion.** Make `dreaming-proposals.md` the structured source of truth for vault-side SKILL.md edits, replacing 3-way merge for proposal-driven changes. Add reverse-promotion: detect when vault edits to phase content represent a generalizable improvement, surface them as a proposed plugin PR.
- **Remote slot execution (`runtime: remote`) — needs its own plan, number TBD.** Originally labeled "Plan 7" in `engine/scout/schedule.py:47` and `schedule_tick.py:387` when Plan 5 was being written. Plan 7 ended up scoped to the schedules tab visual rewrite, so the labels became stale. Plan 8 fixes the labels (see §5.2) but does not implement remote execution. Implementation is a substantial standalone effort: Anthropic routines API integration, auth/key flow, schedule translation, status sync (scout-app cannot observe remote stdout), failure handling, cost surfacing in usage telemetry, and the gating question of which slot types can run remote (sandboxed routines cannot use `gh` CLI per project conventions, so any GitHub-writing slot stays local). Schedule for whenever Jordan wants — likely after Plan 9.
- **Plan 11+ — Runner unification.** Replace `run-{scout,dreaming,research}.sh` body with a one-liner `exec scoutctl run <mode> "$@"` shim; move locking/budget/prompt logic into the engine. Categories 1 and 1b collapse to category 1.
- **Cat 5 implementation — Vault-generated runtime files.** When Scout starts authoring its own hooks/connectors, decide on identification mechanism (manifest? path convention? frontmatter marker?) so `/scout-update` knows to leave them alone.
- **Cat 6 implementation — Plugin-managed databases.** When Scout adopts sqlite/duckdb for ACID-transactional local storage, pipeline stage 2 (schema migrations) gets populated.
- **Settings tab DS adoption** — Plan 7 polish followup.

## 11. Risks

- **3-way merge surprises on phase rewrites.** If a phase file gets restructured (sections renamed, INSERT markers reorganized), the merge sees it as "ours changed everything" and any vault edit becomes a conflict. Mitigation: when shipping phase rewrites in future plans, ship them as multi-step PRs (rename first, restructure later) so each individual update merges cleanly. Plan 9's structured-proposal model eliminates this risk for proposal-driven edits.
- **Engine venv drift.** `~/scout-plugin/.venv/` is outside the vault and not version-tracked. If it gets out of sync with the plugin code, `scoutctl bootstrap` fails opaquely. Mitigation: pre-flight runs `scoutctl --version` and compares against `plugin.json`; if mismatch, runs `pip install -e ~/scout-plugin/engine` before proceeding.
- **Linux `cron` doesn't run in a login shell.** PATH/HOME drift risk. Mitigation: managed block sets explicit `PATH=` and `SHELL=/bin/bash` headers; smoke test covers a non-login-shell environment.
- **`launchctl bootout` race.** If the dispatcher is mid-tick when stage 6 runs, bootout interrupts the runner. Mitigation: stage 6 acquires `.scout-session.lock` before bootout; releases on completion. If lock can't be acquired in 30s, abort stage 6 and tell user to retry.

## 12. Open questions resolved during brainstorm

- **Q1 — Plan 8 scope:** Option 3 (fresh-install fix + safety hardening + upgrade flow). Plan 9 = reverse-promotion only.
- **Q2 — Command shape:** Two commands (`/scout-setup` + `/scout-update`). Each command is shorter, focused, and the upgrade verb is correct for the recurring action.
- **Q3 — File ownership taxonomy:** Cat 1 / 1b / 2 / 3 / 4 with futures cat 5 / 6 reserved as design-extensibility constraints.
- **Q4 — Cat 4 conflict policy:** 3-way merge with `.scout-state/last-assembled/` snapshots. Plan 9 will layer dreaming-proposal-as-edit-log on top.
- **Q5 — Linux:** In scope. New `scoutctl schedule install-cron` parallels `install-plist`. `install-all` is the platform-agnostic wrapper.
- **Q6 — Remote slot execution:** Not folded into Plan 8 scope (too large). Plan 8 cleans up the stale "Plan 7" labels in `schedule.py:47` and `schedule_tick.py:387–395` so they no longer claim a plan number that doesn't own the work. Implementation gets its own plan slot (likely after Plan 9), TBD.
