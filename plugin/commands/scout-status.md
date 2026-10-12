---
name: scout-status
description: Show the current state of your Scout installation — config, last runs, KB health, pending proposals, and wishlist status.
---

# Scout Status Dashboard

You are displaying a status dashboard for the user's Scout installation. Follow each section below in order. Present the final output as a clean, readable dashboard — not as a series of intermediate steps.

---

## Step 1: Locate the Scout Config

Check for `scout-config.yaml` in these locations, in order:

1. The current working directory (`./scout-config.yaml`)
2. `~/Scout/scout-config.yaml`
3. `~/scout/scout-config.yaml`

```bash
for f in "./scout-config.yaml" "$HOME/Scout/scout-config.yaml" "$HOME/scout/scout-config.yaml"; do
    if [ -f "$f" ]; then echo "FOUND:$f"; break; fi
done
```

**If no config is found:**

Tell the user:

```
No Scout installation found. Run `/scout-setup` to create one.
```

Stop here. Do not proceed with the dashboard.

**If found:** note the path as `SCOUT_CONFIG` and derive `SCOUT_DIR` as its parent directory. Read the config file, then continue.

---

## Step 2: Parse the Config

From the YAML config, extract:

- `instance_name` → `INSTANCE_NAME`
- `user.name` → `USER_NAME`
- `user.email` → `USER_EMAIL`
- `connectors` block → `CONNECTORS` (the map of service → true/false)
- `schedule.briefing` → `BRIEFING_TIME`
- `schedule.consolidation` → `CONSOLIDATION_TIMES`
- `schedule.dreaming` → `DREAMING_TIMES`
- `platform` → `PLATFORM`
- `scout_dir` → `SCOUT_DIR` (use this if present; otherwise use the parent directory of the config file)

---

## Step 3: Gather Data

Run all of the following data-gathering steps before composing the dashboard output.

### 3a. Last Runs (git log)

```bash
git -C "SCOUT_DIR" log --oneline -10
```

Collect the 10 most recent commit messages for the "Last 10 Runs" listing. Identify the most recent commit that mentions "work" and "meta-review" (case-insensitive match on the commit message): those sessions are interactive and never fire from the scheduler, so git is the only record of them.

The "Last briefing / consolidation / dreaming / research" lines come from `slots[]` in the run-health report (3g) when they can: for each type, take the slot with the newest `last_fire`, and report its `last_fire`, plus its `last_outcome.outcome` and `last_outcome.finished_at` when `last_outcome` is not null. `last_fire` is the newer of the scheduler's dispatch and the last session end, so word it "last ran", never "dispatched". When run health is unavailable (3g), or `slots[]` has no slot of that type, fall back to the most recent commit whose message mentions that type (case-insensitive), as for work sessions.

### 3b. KB Health

```bash
find "SCOUT_DIR/knowledge-base" -type f -name "*.md" | sort
```

For each file found, read it and look for a line matching either:
- `**Last verified:**`
- `**Last updated:**`

Extract the date from that line. Compare it to today's date to determine staleness.

Staleness thresholds (apply per-file based on its content — if no priority signal is obvious, default to medium):

| Priority | Stale after |
|----------|-------------|
| High     | 3 days      |
| Medium   | 7 days      |
| Low      | 14 days     |

Files without any date marker count as "unknown" — flag them separately but do not count them as stale.

### 3c. Pending Proposals

Read `SCOUT_DIR/dreaming-proposals.md`.

Look for proposal blocks with status `Pending` or `Approved` (i.e., not yet `Applied` or `Rejected`). A proposal block typically looks like:

```
### Proposal N: ...
**Status:** Pending
```

Count how many Pending and Approved proposals exist.

### 3d. Wishlist

Read every `*.md` file in `SCOUT_DIR/docs/wishlist/` (if the directory exists). Each file is one item with YAML frontmatter — the `status:` field is the state (`open` | `in-progress` | `done` | `dropped`).

Collect the active items: `status: open` (new) and `status: in-progress`. Exclude `done` and `dropped`.

Count `open` vs `in-progress` so the dashboard can report e.g. "3 new, 2 in progress."

### 3e. Scheduler Health (macOS only)

Only run this step if `PLATFORM` is `macos` or if running on macOS (Darwin).

```bash
INSTANCE_LOWER=$(echo "INSTANCE_NAME" | tr '[:upper:]' '[:space:]' | tr ' ' '-' | tr -d '\n' | tr '[:upper:]' '[:lower:]')
launchctl list | grep "$INSTANCE_LOWER"
```

Note: derive `INSTANCE_LOWER` by lowercasing `INSTANCE_NAME` and replacing spaces with hyphens.

Collect the output lines. Each loaded plist appears as a row with PID (or `-`), last exit code, and label. Exit code `0` means healthy; any other value is a warning.

#### Scheduler bin-path validation (macOS)

A loaded plist can still be broken if it points at a non-existent or non-executable scoutctl, or if scoutctl lives under a macOS TCC-protected directory (`~/Documents/`, `~/Desktop/`, `~/Downloads/`). Those failures are invisible to `launchctl list` — the job loads fine but every tick crashes during Python init.

Read the installed schedule-tick plist and extract the scoutctl path it references:

```bash
PLIST="$HOME/Library/LaunchAgents/com.scout.schedule-tick.plist"
if [ -f "$PLIST" ]; then
    SCOUTCTL_BIN=$(/usr/libexec/PlistBuddy -c "Print :ProgramArguments:0" "$PLIST" 2>/dev/null)
    echo "PLIST_SCOUTCTL_BIN=$SCOUTCTL_BIN"
fi
```

Then verify three properties of `SCOUTCTL_BIN`:

1. **Exists** — `[ -e "$SCOUTCTL_BIN" ]`. If not, flag as a broken plist.
2. **Executable** — `[ -x "$SCOUTCTL_BIN" ]`. If not, flag.
3. **Not under a TCC-protected dir** (after resolving symlinks). Resolve with `RESOLVED=$(readlink -f "$SCOUTCTL_BIN" 2>/dev/null || python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$SCOUTCTL_BIN")`. Then check:
   ```bash
   case "$RESOLVED" in
       "$HOME/Documents/"*|"$HOME/Desktop/"*|"$HOME/Downloads/"*) flag ;;
   esac
   ```

Note: `realpath` on macOS BSD coreutils doesn't support `-f`; the Python one-liner is the portable fallback.

#### Scheduler bin-path validation (Linux)

If `PLATFORM` is `linux`, extract the scoutctl path from the user's crontab managed block:

```bash
SCOUTCTL_BIN=$(crontab -l 2>/dev/null | awk '
    /^# >>> scout-managed >>>$/ { in_block = 1; next }
    /^# <<< scout-managed <<<$/ { in_block = 0; next }
    in_block && /scoutctl schedule tick/ { print $6; exit }
')
```

Then check `[ -e "$SCOUTCTL_BIN" ]` and `[ -x "$SCOUTCTL_BIN" ]`. (TCC restrictions are macOS-specific; skip the protected-dir check on Linux.)

### 3f. Update status

Check the installed plugin version against the latest available:

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}"
# Fallback only: a Raven-Scout/Scout monorepo clone at ~/scout-plugin keeps the plugin under plugin/.
if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$PLUGIN_ROOT/plugin/.claude-plugin/plugin.json" ]; then PLUGIN_ROOT="$PLUGIN_ROOT/plugin"; fi
# The venv sits in the plugin root for Claude Code and dev checkouts. An engine Scout.app manages lives
# elsewhere (~/.local/state/scout/engine.json names it); the ~/.local/bin/scoutctl shim runs it.
SCOUTCTL="$PLUGIN_ROOT/.venv/bin/scoutctl"; [ -x "$SCOUTCTL" ] || SCOUTCTL="$HOME/.local/bin/scoutctl"
"$SCOUTCTL" self-update check || echo "SELF_UPDATE_UNAVAILABLE"
```

Capture the output. The command prints the installed version, the available version, and whether an update is available (e.g. `up to date (<version>)` or `update available: <installed> -> <available>`). If the command exits non-zero or the output contains `SELF_UPDATE_UNAVAILABLE`, treat the update status as unavailable (network or other error).

Then list vault edits to plugin-owned files (scripts, hooks, runners, `render.py`, `parser.py`):

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}"
# Fallback only: a Raven-Scout/Scout monorepo clone at ~/scout-plugin keeps the plugin under plugin/.
if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$PLUGIN_ROOT/plugin/.claude-plugin/plugin.json" ]; then PLUGIN_ROOT="$PLUGIN_ROOT/plugin"; fi
# The venv sits in the plugin root for Claude Code and dev checkouts. An engine Scout.app manages lives
# elsewhere (~/.local/state/scout/engine.json names it); the ~/.local/bin/scoutctl shim runs it.
SCOUTCTL="$PLUGIN_ROOT/.venv/bin/scoutctl"; [ -x "$SCOUTCTL" ] || SCOUTCTL="$HOME/.local/bin/scoutctl"
"$SCOUTCTL" bootstrap drift --json || echo "DRIFT_UNAVAILABLE"
```

Store both results for rendering in the dashboard below.

### 3g. Run health

The runners record how every scheduled run ended in `.scout-logs/run-outcomes.jsonl`, and the scheduler records every fire, both outside Claude, so a run that dies at startup (an expired login) still leaves a trace. `scoutctl health` reads both. The git log in 3a can't show this: a failed run commits nothing.

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}"
# Fallback only: a Raven-Scout/Scout monorepo clone at ~/scout-plugin keeps the plugin under plugin/.
if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$PLUGIN_ROOT/plugin/.claude-plugin/plugin.json" ]; then PLUGIN_ROOT="$PLUGIN_ROOT/plugin"; fi
# The venv sits in the plugin root for Claude Code and dev checkouts. An engine Scout.app manages lives
# elsewhere (~/.local/state/scout/engine.json names it); the ~/.local/bin/scoutctl shim runs it.
SCOUTCTL="$PLUGIN_ROOT/.venv/bin/scoutctl"; [ -x "$SCOUTCTL" ] || SCOUTCTL="$HOME/.local/bin/scoutctl"
SCOUT_DATA_DIR="$SCOUT_DIR" "$SCOUTCTL" health --json; echo "exit=$?"
```

Of the output, the first line is the JSON report and the last line is `exit=N`. Exit codes 0 (green), 1 (yellow) and 2 (red) are all normal. Any other exit code, a first line that is not a JSON report, or a report with `"severity": "unknown"` (a vault the command cannot find) means `RUN_HEALTH_UNAVAILABLE`: skip the run-health headline, and take the per-type last-run lines from the commit-message match instead (3a).

The report is `{severity, evaluated_at, last_ok_at, findings[], slots[]}`. Each finding has `id`, `severity` (`yellow`, `red` or `note`), `since`, `summary` and `fix`. Ignore `note` findings everywhere: they are informational and never change the headline. Each slot has `key`, `type`, `last_fire` and `last_outcome` (`{outcome, finished_at, failure_class}` or null). Keep the whole report for Step 4.

---

## Step 4: Compose and Display the Dashboard

Present the dashboard as follows. Use clean Markdown with headers and lists. Do not show raw shell output — interpret it into human-readable form.

---

```
╔══════════════════════════════════════════════════╗
║           SCOUT STATUS — <INSTANCE_NAME>         ║
╚══════════════════════════════════════════════════╝
```

**Run-health headline (from 3g).** Put this directly under the banner, before anything else. It's the first thing a user whose Scout "went quiet" needs to see. Pick the worst finding (red before yellow) and render it:

- A finding with id `auth.expired`:

  ```
  ❌ Scout can't run: your Claude login expired.
     <the finding's summary: failed-run count and last success time>
     Fix: open Terminal and run  claude auth login
  ```

- Any other red finding:

  ```
  ⚠️  <summary>
     Fix: <fix>
  ```

- Only yellow findings: one line, `⚠️  <N> run-health warnings — first: <summary of the first>`.

- Green (no yellow or red finding; `note` findings don't count): `✅ Last successful run: <last_ok_at as days ago>` (`today` for 0). When `last_ok_at` is null, show `✅ No runs recorded yet` instead.

### Config

| Field    | Value                                |
|----------|--------------------------------------|
| Instance | `<INSTANCE_NAME>`                    |
| User     | `<USER_NAME>` (`<USER_EMAIL>`)       |
| Dir      | `<SCOUT_DIR>`                        |
| Platform | `<PLATFORM>`                         |

### Connected Services

List every connector from the config. Use ✅ for `true` and ❌ for `false`:

```
  ✅ Slack
  ❌ Google Calendar
  ✅ Gmail
  ...
```

### Schedule

```
  Briefing:       <BRIEFING_TIME>
  Consolidation:  <CONSOLIDATION_TIMES>
  Dreaming:       <DREAMING_TIMES>
```

---

### Last 10 Runs

Show the 10 most recent git log entries as a simple list (hash + message):

```
  abc1234  briefing [09:03]: ...
  def5678  consolidation [17:00]: ...
  ...
```

Then below the list, highlight:

```
  Last briefing:       <last ran date/time> — <outcome> at <finished_at>
  Last consolidation:  <last ran date/time> — <outcome> at <finished_at>
  Last dreaming:       <last ran date/time> — <outcome> at <finished_at>
  Last research:       <last ran date/time> — <outcome> at <finished_at>
  Last work session:   <commit message and date/time if identifiable>
  Last meta-review:    <commit message and date/time if identifiable>
```

The first four come from `slots[]` (3a, 3g): the newest `last_fire` among the slots of that type. When that slot's `last_outcome` is null, show just `<last ran date/time> — no outcome recorded`. When run health is unavailable or no slot has that type, show the commit-message match instead (`<commit message and date/time>`), as 3a describes. The work-session and meta-review lines come from the git match. If a run type hasn't happened yet (its slot's `last_fire` is null, or nothing matched), show: `(none found)`

---

### KB Health

```
  Total files:    X
  Up to date:     Y
  Need attention: Z
  Unknown dates:  W
```

If any files need attention, list them:

```
  ⚠️  knowledge-base/people.md  — last updated 12d ago (medium priority, stale after 7d)
  ⚠️  knowledge-base/channels.md — no date marker found
```

If all files are up to date:

```
  ✅ All KB files are fresh.
```

---

### Pending Proposals

If there are Pending or Approved proposals, list their titles and statuses:

```
  • [Pending]  Proposal 1: Add LinkedIn connector
  • [Approved] Proposal 2: Expand Slack channel coverage
```

If none:

```
  No pending proposals.
```

---

### Wishlist

If there are active (non-done) wishlist items, list them:

```
  • [in-progress] Sharing the Scout skill with others internally
  • Custom GUI and TUI for working through action items
  • ...
```

If all items are done or the directory is empty or doesn't exist:

```
  Wishlist is clear.
```

---

### Scheduler Health *(macOS only)*

If not on macOS, omit this section entirely.

For each launchctl entry found:

```
  ✅ com.scout.briefing      — running (PID 12345)
  ✅ com.scout.consolidation  — idle (last exit: 0)
  ⚠️  com.scout.dreaming       — last exit: 1  ← check logs
```

If no entries are found for this instance:

```
  ⚠️  No launchd plists found for '<INSTANCE_NAME_LOWER>'. Scheduler may not be configured.
  Run `/scout-setup` and choose "Reconfigure" to set up scheduling.
```

If the bin-path validation (step 3e sub-checks above) found issues, render one of these blocks immediately under the launchctl table:

```
  ❌ scoutctl path in plist not executable: /Users/foo/old/.venv/bin/scoutctl
      Fix: scoutctl schedule install-plist --force
      (the install command re-derives the canonical path from the loaded
      plugin's venv — no manual override needed)
```

```
  ❌ scoutctl is under ~/Documents (resolved: /Users/foo/Documents/.../scoutctl)
      macOS TCC blocks launchd from reading this directory. Either:
        - Move the plugin out of ~/Documents/, or
        - Grant Full Disk Access to /opt/homebrew/bin/python3.x in
          System Settings → Privacy & Security → Full Disk Access,
      then re-install: scoutctl schedule install-plist --force
```

On Linux, swap "in plist" for "in crontab" and "install-plist" for "install-cron".

If bin-path validation passes, no extra output — the launchctl table alone is enough.

---

### Update status

Using the version info collected in step 3f, display:

```
  Plugin:       <installed-version>  (up to date (<version>) or update available: <installed> -> <available>)
```

If the update check was unavailable (network error or non-zero exit), display:

```
  Plugin:       <installed-version>  (update status: unavailable (couldn't reach marketplace))
```

If an update is available, add: "Run `/scout-update` to apply it."

From the `bootstrap drift --json` output, add one line when `files` is non-empty (skip it when empty). If the command failed (`DRIFT_UNAVAILABLE`), show `Vault edits:  check failed` with its error output rather than hiding it:

```
  Vault edits:  <n> edited · <n> conflict · <n> parked   (scoutctl bootstrap drift)
```

A `conflict` means the user is running their own version of a plugin-owned file while the plugin's update waits under `.scout-state/drift/` — say so, and point at `scoutctl bootstrap drift` for how to resolve it.

---

### Knowledge Graph Health

Run the ontology parser to check the knowledge graph:

```bash
cd "SCOUT_DIR" && python knowledge-base/ontology/parser.py stats 2>/dev/null
cd "SCOUT_DIR" && python knowledge-base/ontology/parser.py validate 2>/dev/null
```

If the parser is available and succeeds, show:

```
  Entities:       X (Y person, Z project, ...)
  Relationships:  N
  Validation:     X errors / No errors
```

If the parser isn't set up (file doesn't exist or Python error), show:

```
  Knowledge graph not configured. Run /scout-setup to set up the ontology.
```

---

### Budget Tracking

Check if a usage tracker file exists at `SCOUT_DIR/.scout-logs/usage-tracker.jsonl`.

If it exists, parse the last 24 hours of entries and show:

```
  Sessions today:      N (briefing: X, consolidation: Y, dreaming: Z, research: W)
  Estimated spend:     $X.XX (last 24h)
  Last failure:        <time and type, or "none">
  Rate limit events:   N in last 24h
```

If it doesn't exist:

```
  No usage tracking data yet. Cost tracking begins after the first run.
```

---

End the dashboard with a one-line summary:

```
Scout is healthy. / Scout needs attention — Z KB files stale, X proposals pending.
```

Choose the appropriate variant based on findings. "Needs attention" if any of: KB files stale, proposals pending with Approved status, scheduler entries missing or with non-zero exit codes.
