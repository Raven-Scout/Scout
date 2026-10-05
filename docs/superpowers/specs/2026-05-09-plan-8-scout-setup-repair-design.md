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

### 4.5 3-way merge for category 4 (sidecar policy + M3-incident amendment)

**M3-incident amendment (2026-05-11):** the original "first /scout-update on legacy vault is degenerate — merge result = ours" branch silently overwrote 85KB of vault edits when tested live against `~/Scout/`. The fix below replaces that branch with a sidecar.

After every assembly (setup or update), snapshot is written to `.scout-state/last-assembled/{SKILL,DREAMING,RESEARCH}.md` (gitignored).

On `/scout-update` stage 5:

```python
for name in ("SKILL", "DREAMING", "RESEARCH"):
    base = read(f".scout-state/last-assembled/{name}.md")  # or current vault file if absent
    theirs = read(f"{name}.md")                             # current vault, with edits
    ours = assemble_from_phases(name)                       # fresh from current plugin phases

    # CASE 1: plugin produced the same content vault already has — advance
    # snapshot, no live touch.
    if ours == theirs:
        write(f".scout-state/last-assembled/{name}.md", ours)
        continue

    # CASE 2 (M3-incident protection): no recorded vault edits vs base, but
    # plugin diverged. We cannot distinguish 'no edits yet' from 'edits with
    # no edit history' — write proposed plugin content to sidecar for user
    # review; leave live + snapshot untouched.
    if base == theirs:
        write(f"{name}.md.proposed-merge", ours)
        log_warning(f"Plugin content diverged; review {name}.md.proposed-merge")
        continue

    # CASE 3: both sides diverged from base — real 3-way merge.
    result, conflicts = git_merge_file(base=base, ours=ours, theirs=theirs, marker_diff3=True)
    if not conflicts:
        write(f"{name}.md", result)
        write(f".scout-state/last-assembled/{name}.md", ours)
    else:
        # Live + snapshot untouched on conflict.
        write(f"{name}.md.proposed-merge", result)
        log_warning(f"Conflict in {name}.md — proposed merge at {name}.md.proposed-merge")
        # DO NOT abort. Continue the pipeline (stages 6, 7, 8).
```

**Why sidecar instead of in-place markers:** SKILL.md is read as the prompt for every dispatcher-fired Claude session. Conflict markers (`<<<<<<< HEAD`, `=======`, `>>>>>>> theirs`) embedded in the live file would cause Claude to follow malformed instructions. The sidecar approach keeps the running system functional during conflict resolution.

**Why continue the pipeline instead of aborting:** Aborting at stage 5 leaves the vault in a torn state — Cat 1 files (scripts, ontology, plists) updated, but version stamp not bumped and jobs not restarted. Re-running `/scout-update` on the same delta repeats the same conflict. Sidecar + continue gives idempotent completion.

**Pre-flight handling on next run:** `/scout-update` pre-flight (§6.2) detects existing `*.proposed-merge` sidecar files and refuses to run until the user has resolved them. Resolution path: user edits the sidecar to remove conflict markers, runs `mv SKILL.md.proposed-merge SKILL.md`, then re-runs `/scout-update` (which will now see the resolved content and merge cleanly into the snapshot).

**Doctor reporting:** Stage 8 reports yellow (not red) if any `*.proposed-merge` sidecar exists. Yellow means "system functional, user action pending."

**Legacy vault migration path (added post-M3):** legacy Plan-5-era vaults (no `scout-config.yaml`, no `.scout-state/last-assembled/` snapshots) must run `scoutctl bootstrap migrate-legacy` before `/scout-update`. The migration command:

1. Requires `--user-name`, `--user-email`, plus optional `--user-slack-id`, `--claude-bin`, `--max-budget`, `--instance-name`, `--timezone`, `--github-username`, `--github-repos`, `--connectors` flags
2. Snapshots current SKILL/DREAMING/RESEARCH → `.scout-state/last-assembled/` (establishes merge baseline = current live)
3. Seeds `.scout-state/schedule.yaml` from engine defaults if missing
4. Runs cat-1 writes with the now-correct template vars
5. Runs cat-1b runner regen with hand-edit detection (legacy hand-edited runners get backed up to `.bak.YYYY-MM-DD`)
6. **Skips cat-4 merge entirely** — snapshots just established equal current live; nothing to merge
7. Writes `scout-config.yaml` with `user`, `instance`, `connectors.enabled`, `connectors.inputs`, `timezone`, `platform`, `plugin.version_at_last_*`
8. Doctor

`upgrade()` pre-flight refuses on legacy vaults (no `scout-config.yaml`) with the actionable error: `"legacy vault detected — run scoutctl bootstrap migrate-legacy first."`

After migration, the first `/scout-update` will hit CASE 2 (base == theirs because snapshot was seeded equal to current; ours may diverge from plugin phase updates) and write sidecars for review rather than overwriting. The user reviews `*.proposed-merge` files at leisure and chooses what (if anything) to adopt.

Plan 9 (dreaming-proposals as edit log) will eliminate the need for these sidecars for proposal-driven edits.

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
- Manages a marked block in the user's crontab between `# >>> scout-managed >>>` / `# <<< scout-managed <<<` markers.
- Block contains:
  - `*/5 * * * * scoutctl schedule tick >> ~/Scout/.scout-logs/cron.log 2>&1`
  - `*/30 * * * * ~/Scout/scripts/heartbeat.sh >> ~/Scout/.scout-logs/cron.log 2>&1`

**Atomic rewrite (avoids data loss on partial-failure):**

```python
def install_cron(slots: list[str]) -> None:
    # 1. Read current crontab (may be empty).
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    current_lines = current.stdout.splitlines() if current.returncode == 0 else []

    # 2. Strip any existing managed block IN MEMORY.
    new_lines = strip_managed_block(current_lines)

    # 3. Append fresh managed block IN MEMORY.
    new_lines.extend(build_managed_block(slots))

    # 4. Write to a temp file (atomic write to disk).
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cron") as tf:
        tf.write("\n".join(new_lines) + "\n")
        tmp_path = tf.name

    # 5. Apply with a single crontab call. If it fails, the original
    #    crontab is still active — the user has lost nothing.
    result = subprocess.run(["crontab", tmp_path], capture_output=True, text=True)
    if result.returncode != 0:
        os.unlink(tmp_path)
        raise CrontabApplyError(f"crontab apply failed: {result.stderr}")

    # 6. Backup the previous crontab for one-revision rollback.
    backup_path = Path.home() / f".crontab.scout-bak.{date.today().isoformat()}"
    backup_path.write_text("\n".join(current_lines) + "\n")
    os.unlink(tmp_path)
```

Critically, the previous crontab is *never* written-then-modified-then-applied. We compose the entire new crontab in memory, write it as a single temp file, and apply atomically. Any failure leaves the user's crontab in its prior state.

Add `scoutctl schedule install-all` — platform-agnostic wrapper that picks launchd (`install-plist` + `install-heartbeat-plist`) or cron (`install-cron`) based on `uname -s`. Single platform-detection point.

### 4.9 Global pipeline lock

`scoutctl bootstrap install|upgrade` acquires `~/Scout/.scout-logs/.scout-session.lock` at the *start* of stage 1 and holds it through stage 8. The lock file contains the bootstrap process PID.

**Interaction with running Scout sessions:**

- Runner scripts already check this lock at startup (`run-scout.sh.tmpl` lines 23–32). If the lock is held by a live PID, the runner logs "Another SCOUT session running — skipping" and exits cleanly. The dispatcher (`scoutctl schedule tick`) keeps firing on its 5-min cadence, but each fire becomes a no-op until bootstrap completes.
- If the lock is held by a dead PID (stale from a crash), bootstrap removes it and proceeds. Same convention runners already use.
- If the lock is held by a *live* PID (a runner is mid-flight when the user invokes `/scout-update`), bootstrap waits up to 5 minutes for it to clear, polling every 10s. After 5 minutes, abort with: `"Scout session in progress (PID N), retry /scout-update in N minutes."`

**Why this scope:** Without a global lock, the dispatcher's 5-min tick can fire a runner mid-pipeline. The runner reads partially-written `run-scout.sh` (stage 4 in progress), partially-written `SKILL.md` (stage 5 in progress), or runs against an old plist that's about to be replaced (stage 6 imminent). All three produce nondeterministic failures. Holding the lock for the full pipeline closes every window.

**Why 5-min wait:** typical runner durations are 2–4 minutes. Five minutes covers the long tail without making bootstrap feel hung. Polling at 10s gives a reasonable user-visible "waiting for runner X (PID N) to finish" message.

**Bootstrap doctor exemption:** `scoutctl bootstrap doctor` is read-only and does not acquire the lock. Doctor can run safely during a session.

### 4.10 `scout-config.yaml` additions

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
- `scripts/install-venv.sh` (plugin-root, executable) — documented manual fallback for users whose `/scout-setup` venv install times out

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

Verifies engine venv:
- `plugin/.venv/bin/scoutctl` — required. If missing, `/scout-setup` runs as a *separate pre-stage* (before stage 1):
  - User-visible message: `"Engine venv missing. Installing now (this typically takes 30–60 seconds)..."`
  - Bash invocation: `python3 -m venv plugin/.venv && plugin/.venv/bin/pip install -e plugin/engine` with explicit `timeout: 300000` (5 min) to cover slow networks and dependency resolution.
  - Slash command supports a `--skip-venv-install` flag for users who pre-built the venv (e.g., via `bash plugin/scripts/install-venv.sh`, which Plan 8 ships as a documented manual fallback).
  - On failure, abort with: `"Engine venv install failed. Run manually: bash plugin/scripts/install-venv.sh, then retry /scout-setup."`

### 6.2 `/scout-update` pre-flight

Refuses if `~/Scout/scout-config.yaml` is missing.

**Sidecar conflict check (from prior incomplete update):**
- If any `~/Scout/{SKILL,DREAMING,RESEARCH}.md.proposed-merge` file exists, refuses with: `"Unresolved merge conflict from a prior /scout-update. Resolve <files> by editing them to remove conflict markers, then 'mv X.md.proposed-merge X.md' and re-run /scout-update."`
- Rationale: the running system is currently using the last-known-good live SKILL.md while the user still owes a merge. Layering a new merge attempt on top would compound state.

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
| 0 (pre-stage) | engine venv install fails (timeout, dep error, no network) | Abort with `bash plugin/scripts/install-venv.sh` instructions |
| 1 | Vault detected during `/scout-setup` | Abort with manual reset snippet |
| 1 | Orphan jobs without vault (half-reset state) | Abort with manual reset snippet |
| 1 | Sidecar `*.proposed-merge` from prior failed update | Abort with resolution instructions |
| 1 | Cannot acquire `.scout-session.lock` within 5 min | Abort with `"Scout session in progress (PID N), retry in N minutes"` |
| 3 | Permission denied / disk full | Abort; rerun is idempotent |
| 4 | Hand-edited runner detected | Back up to `.bak.YYYY-MM-DD`, regenerate, log to terminal output |
| 5 | 3-way merge conflict | Write proposed merge to `<file>.proposed-merge` sidecar; live file untouched; pipeline continues; doctor reports yellow |
| 6 | `launchctl bootout` fails | Warn; user runs `scoutctl schedule install-all --force` separately |
| 6 | crontab apply fails (Linux) | Original crontab still active (atomic temp-file approach); abort; user inspects `~/.crontab.scout-bak.YYYY-MM-DD` |
| 8 | Doctor reports red (e.g., schedule.yaml invalid, jobs not registered) | Loud warning to terminal; do *not* roll back (rollback is destructive) |
| 8 | Doctor reports yellow (sidecar files, hand-edit backups) | Print summary of pending user actions; pipeline still considered successful |

Idempotency property: rerunning the pipeline must converge. Each stage either succeeds or aborts cleanly without partial state. Specifically:
- Stage 3 overwrites are atomic per-file (write to `.tmp`, rename).
- Stage 4 only takes a backup if the file was modified vs the previously-rendered template; otherwise overwrite is silent.
- Stage 5 writes the snapshot only when the merge is clean; conflict path leaves snapshot at its previous value (next `/scout-update` after user resolves the sidecar will see the resolved live file as `theirs` and merge cleanly).
- Stage 6 (Linux) writes new crontab atomically via temp-file; on failure the prior crontab remains active and is backed up to `~/.crontab.scout-bak.YYYY-MM-DD`.
- The global pipeline lock (§4.9) blocks dispatcher tick from interleaving with any stage.

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

- **3-way merge surprises on phase rewrites.** If a phase file gets restructured (sections renamed, INSERT markers reorganized), the merge sees it as "ours changed everything" and any vault edit becomes a conflict. Mitigation: when shipping phase rewrites in future plans, ship them as multi-step PRs (rename first, restructure later) so each individual update merges cleanly. The sidecar conflict policy (§4.5) keeps the running system functional even when conflicts arise; user resolves at their convenience. Plan 9's structured-proposal model eliminates this risk entirely for proposal-driven edits.
- **Engine venv drift.** `plugin/.venv/` is outside the vault and not version-tracked. If it gets out of sync with the plugin code, `scoutctl bootstrap` fails opaquely. Mitigation: pre-flight runs `scoutctl --version` and compares against `plugin.json`; if mismatch, runs `pip install -e plugin/engine` before proceeding (with the same 5-min timeout as the cold-install case).
- **Linux `cron` doesn't run in a login shell.** PATH/HOME drift risk. Mitigation: managed block sets explicit `PATH=` and `SHELL=/bin/bash` headers; smoke test covers a non-login-shell environment.
- **Dispatcher-tick interleaving with pipeline stages.** Without the global lock, a 5-min tick fires a runner mid-pipeline. The runner reads partially-written runner script (stage 4), reads SKILL.md mid-merge (stage 5), or executes against an old plist about to be replaced (stage 6). Mitigation: §4.9 global pipeline lock — bootstrap acquires `.scout-session.lock` at start of stage 1, holds through stage 8. Runners and dispatcher already respect this lock; ticks become no-ops until the pipeline completes.
- **Crontab clobber on partial failure.** If the Linux install path strips the managed block before successfully writing the new one, the user permanently loses their schedule. Mitigation: §4.8 atomic rewrite — compose entire new crontab in memory, write to temp file, apply with single `crontab <tmpfile>` call. On failure, original crontab is intact. Previous crontab also backed up to `~/.crontab.scout-bak.YYYY-MM-DD` for one-revision rollback.
- **Conflict markers reaching the running system.** If we wrote `<<<<<<< HEAD` markers into the live SKILL.md on conflict, the next dispatcher fire would feed them as a Claude prompt — runs would produce garbage. Mitigation: §4.5 sidecar policy — live SKILL.md is never overwritten on conflict; markers go to `SKILL.md.proposed-merge`. Dispatcher fires keep using the last-known-good content.
- **Venv install timeout under Claude tool-use.** Default Bash timeout is 2 min; cold `pip install` on slow networks can exceed that. Mitigation: §6.1 explicit `timeout: 300000` (5 min) on the venv install bash call; documented `plugin/scripts/install-venv.sh` as a manual fallback; `--skip-venv-install` flag on `/scout-setup` for users who pre-built.

## 12. Open questions resolved during brainstorm

- **Q1 — Plan 8 scope:** Option 3 (fresh-install fix + safety hardening + upgrade flow). Plan 9 = reverse-promotion only.
- **Q2 — Command shape:** Two commands (`/scout-setup` + `/scout-update`). Each command is shorter, focused, and the upgrade verb is correct for the recurring action.
- **Q3 — File ownership taxonomy:** Cat 1 / 1b / 2 / 3 / 4 with futures cat 5 / 6 reserved as design-extensibility constraints.
- **Q4 — Cat 4 conflict policy:** 3-way merge with `.scout-state/last-assembled/` snapshots. Plan 9 will layer dreaming-proposal-as-edit-log on top.
- **Q5 — Linux:** In scope. New `scoutctl schedule install-cron` parallels `install-plist`. `install-all` is the platform-agnostic wrapper.
- **Q6 — Remote slot execution:** Not folded into Plan 8 scope (too large). Plan 8 cleans up the stale "Plan 7" labels in `schedule.py:47` and `schedule_tick.py:387–395` so they no longer claim a plan number that doesn't own the work. Implementation gets its own plan slot (likely after Plan 9), TBD.
