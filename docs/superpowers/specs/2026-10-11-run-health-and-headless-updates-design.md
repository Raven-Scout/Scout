# Run health and headless updates — design

**Status:** approved 2026-10-11 (§12 records the decisions). Docs only; no code lands from this PR.
**Date:** 2026-10-11 (against `origin/main` @ `157db11`, v0.16.0).
**Issues:** #233, #199, #267 (remaining items), #210, #237, #218, #231 (Track 3, "Scout stopped
running and nobody knew") and #360 (scheduled self-update for engines Scout.app doesn't manage).

## 1. Intent

Scout's scheduled runs fail quietly. An expired login cost a vault 21 days and 72 runs before a
person noticed (#233). A session type was dead for three months (#199). A laptop's idle sleep kills
runs partway through, and their logs read like API errors (#210). Since v0.15 parts of this are
covered: the retry wrapper raises a desktop alert on an auth failure, and `/scout-status` leads with
the failed-run streak. But each surface has its own rules, and most of them only help someone who
already suspects a problem.

#360 adds a new unattended actor: an engine that upgrades itself on a headless host. An upgrade that
fails, or leaves a sidecar nobody sees, is the same silent failure. So this spec does two things,
in this order:

1. It defines **one run-health surface**: what gets recorded, what turns it yellow or red, and who
   gets told.
2. It defines **headless updates** as one more producer and consumer of that surface.

**Success means:**
- A run that fails, a slot that stops firing, or a scheduler that stops ticking reaches a human
  within a day. That holds with no app installed and with Claude's login dead.
- Every surface (doctor, `/scout-status`, Scout.app, the session wrap, push notifications) shows
  the same verdict, computed by the same code.
- An `install.sh` or `claude-code` engine with `auto_update.enabled: true` gets each stable release
  within about a day of publication. It never upgrades while a session holds the vault lock. A
  failed or held upgrade is visible on the same surface. App-managed engines are never touched.

## 2. What exists today (verified on `157db11`)

| Piece | Where | What it does | Gap |
|---|---|---|---|
| Run ledger | `templates/scripts/run-outcome.sh.tmpl` → `.scout-logs/run-outcomes.jsonl` | One row per run: slot, mode, start/finish, exit code, `failure_class` (`oauth_expired`, `network`, `git_lock`, `rate_limited`, `budget`, `unknown`) | A lock-busy or budget skip `exit 0`s before the row is written, so a fired slot can leave no row. No count of the run's output. |
| Telegram streak notice | same file, `cmd_record` | Pushes at streak 1 (`info`) and 2 (`action_required`), then stops | Telegram only, and only if it is configured. Stays silent for the rest of a long outage. Rules live in bash. |
| Auth desktop alert | `templates/scripts/claude-with-retry.sh.tmpl` `notify_auth_failure` | `osascript` / `notify-send`, at most once a day, cleared by a successful run | Auth only. |
| `failures.log` | the three `run-*.sh` templates | One line per failed run, plus lock reaps | Only `/scout-meta-review` reads it, by hand. |
| Lane liveness | `templates/scripts/session-lane-liveness.py`, run once a day by `heartbeat.sh` with `--quiet --notify` | Per session type: OK / DEGRADED / FIRING-BUT-SILENT / DARK, judged by commits and the run ledger | A second rule set with its own notifier. Stdlib only, by design. |
| Doctor | `engine/scout/scripts/bootstrap_doctor.py` | Install health. Also turns RED when the newest run log ends in an auth failure. | No slot or scheduler liveness (#199). The auth check makes an engine upgrade "fail" (red post-upgrade doctor) when only the login is broken. |
| `/scout-status` 3g | `commands/scout-status.md` | Inline Python over `run-outcomes.jsonl`: last OK, streak, newest class | A third copy of the streak rule. |
| Scout.app | `EngineHealthService` runs `bootstrap doctor --json` at launch and every 600 s; `SessionLogService` parses `.log` markers for the menu-bar dot | Shows install health and the newest run | Never reads `run-outcomes.jsonl`, `last-fire.json` or `failures.log`. |
| Scheduler | `schedule_tick.py` | Records `last-fire.json` on spawn; events in `.scout-logs/schedule-events-<date>.jsonl`; skips spawning with `network-offline` when api.anthropic.com is unreachable | Records fires, not results. Nothing joins the schedule against fires. |
| `degraded.json` | — | Not in this repo. #237's alert text and `degraded.json` belong to one vault's local wrapper. | — |
| Self-update | `self_update.py`, `scoutctl self-update check` | Read-only. Compares `__version__` with `plugins[0].version` of `marketplace.json` on `main` | Nothing applies updates. `main`'s manifest can be ahead of the latest published release (§7.4). |
| `auto_update` | template, `bootstrap install --auto-update`, `config set-auto-update` | Writes the key | Nothing reads it. The template comment promises scheduled upgrades. `/scout-setup` Step 3 still asks the question. `scoutctl setup` doesn't ask. The template variable reads `connector_inputs.auto_update_enabled`, which no code sets. |
| Session lock | inline in each `run-*.sh` (#331), `bootstrap_lock.py` (#330) | Atomic create, takeover guard, 2 h reap of a wedged holder | #330/#331 fixed the double-hold race only. Runners still `exit 0` immediately when the lock is busy ("skipping"), with no outcome row. There is no wait and no redundancy check (#218). The lock code is copied three times. |
| Heartbeat | `heartbeat.py`, every 30 min (launchd `com.scout.heartbeat`, cron `*/30`) | Launches extra dreaming/research sessions | 4 of 6 cadence knobs can't be set (#231). The dirty-tree operand has no filter. |
| Power | — | No `caffeinate` anywhere (#210). `install_wake_schedule.py` (pmset wake) exists for the asleep-through-the-window case. | — |

## 3. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **One evaluator**, `engine/scout/health.py`, computes a `RunHealth` report from the on-disk ledgers. It reads only and writes nothing except the notifier state. Every consumer calls it, through `scoutctl health --json` or in-process. | Today there are three streak rules (bash, status prose, lane script). One verdict needs one implementation. |
| D2 | **Run health is separate from install health.** `bootstrap doctor` keeps `severity` and its exit code for install health. Its `--json` gains an additive `run_health` block, and its text output gains a "Run health" section. The auth-log check moves from doctor errors into run health. | The app's `EngineUpgrader` and `bootstrap auto` treat a red doctor as a failed upgrade. An expired login must not fail an engine upgrade, and a broken install must not hide behind "runs are fine". |
| D3 | **Ledgers, not snapshots.** Append-only: `run-outcomes.jsonl` (extended) and new `update-outcomes.jsonl`. No `health.json` cache and no `degraded.json`. `failures.log` stays as written for one release (meta-review reads it) and gets no new readers. | Derived state goes stale. A ledger is something every consumer can recompute and test. |
| D4 | **One notifier**, `scoutctl health notify`. It pushes on transitions into red, sends a reminder every 24 h while red, and sends one "recovered" notice. Yellow never pushes. It replaces run-outcome's inline Telegram streak and the lane script's `--notify`. | The 1-then-2-then-silence rule lets a long outage go quiet. One state machine fixes de-duplication in one place. |
| D5 | **The engine-independent floor stays in bash.** The wrapper's auth desktop alert is unchanged. `run-outcome.sh` gains one fallback: if `scoutctl` can't run, it raises a local desktop alert, rate-limited by a marker, saying the engine is broken. | A broken engine, including one an unattended upgrade broke, can't report itself. The floor has to work without it. |
| D6 | **Headless updates ride the heartbeat.** `heartbeat.sh` starts `scoutctl self-update run` detached, at most once a day. No new launchd job, plist or cron line. | Both platforms already run the heartbeat every 30 min. A new job means a new registration check, a new install path, and a new way to break. |
| D7 | **Eligibility:** `auto_update.enabled` is true, the pointer's `managed_by` is `install.sh` or `claude-code`, and the pointer describes the engine running the command. `scout-app` is refused before anything else, by its own test. `dev` and `unknown` are skipped with a recorded reason. | Agrees with the coordinator on `dev`: a checkout belongs to its owner. It may be on a branch, carry unpushed or unstaged engine edits (#199 comment), or be the live install itself. The updater reports "behind" for it instead. `unknown` names no channel to update through. |
| D8 | **Stable means published.** Apply version V only when `main`'s `marketplace.json` says V **and** a non-draft, non-prerelease GitHub release `vV` exists that was published at least `soak_hours` ago (default 24). | `release.sh prepare` bumps `main`'s manifest before `finalize` publishes. A manifest-only check would ship a release that was never finalized. |
| D9 | **The engine moves before the switch; the vault upgrades under the lock.** Fetching the plugin and building the new venv happen without the lock: they touch neither the vault nor the live engine. Only `bootstrap auto` takes the lock, which it already does. If the lock is busy, the attempt is `deferred`, not failed. | Holding the lock across a venv build (minutes) would delay or skip a scheduled run. `bootstrap`'s own lock already serialises it against `run-*.sh`, and no reentrancy is needed. |
| D10 | **Roll back the engine only before the switch; after it, roll forward.** This mirrors the app's Part C rule: a failure before the vault step leaves the old engine live and pointed to. A failure at or after `bootstrap auto` holds, goes red and notifies. Nothing re-renders the vault with an older engine. | A vault rendered by V and then re-rendered by V−1 is a state no test covers. `bootstrap` steps are idempotent, so a retry, or `scoutctl bootstrap upgrade` by hand, converges. |
| D11 | **Sidecars never block runs, and only blocking sidecars block updates.** A brain-file sidecar left by an unattended upgrade is parked as today (the live file keeps running), shows as a yellow finding, and gets one informational push. A pending brain sidecar does not hold later updates: #266 made upgrades skip just that file. A `parser.py.proposed-merge` (which `bootstrap` refuses on) or a red install doctor holds the update. | Consistent with #266. #360's text says to hold on any brain sidecar; that is Open question Q2. |
| D12 | **No scheduler pre-flight skip on a dead login** (the last open box of #267). | Slots fire on their own schedule, so a dead login costs one 4-second run per slot, not one per tick. A skip would have to detect that the user logged back in, and getting that wrong keeps Scout down after the fix. The run is the probe. Q3 asks Jordan to confirm. |

## 4. The run-health surface

### 4.1 Records

**`.scout-logs/run-outcomes.jsonl`** (existing; new fields are additive and readers ignore unknown keys):

| Field | New? | Meaning |
|---|---|---|
| `slot`, `mode`, `started_at`, `finished_at`, `duration_s`, `exit_code`, `failure_class`, `log` | — | As today. |
| `outcome` | new | `ok` \| `failed` \| `skipped_lock` \| `skipped_budget` \| `skipped_redundant`. Rows without it read as `ok`/`failed` from `exit_code`. |
| `trigger` | new | `schedule` (`SCOUT_FORCE_MODE` set by the tick), `heartbeat` (new `SCOUT_TRIGGER=heartbeat` env from `heartbeat.launch_runner`), or `manual`. |
| `commits_in_window` | new | The number of commits in `[started_at, finished_at]`, or `null` when unmeasured (§4.5, #237). |
| `newest_commit` | new | The short SHA of the newest of those commits, or `null`. |

Skips write a row too. `run-*.sh` calls `run-outcome.sh record` on the lock-busy and budget paths
before `exit 0`. That lets the evaluator tell "fired, waited, skipped" from "fired and vanished".

**`.scout-logs/update-outcomes.jsonl`** (new, one row per `self-update run` that got past the daily gate):

```json
{"at": 1791720000, "from": "0.16.0", "target": "0.17.0", "managed_by": "install.sh",
 "result": "applied", "step": "verify", "doctor": "green", "sidecars": [],
 "detail": "", "log": "self-update-2026-10-12.log"}
```

`result` is one of:
- `up_to_date`
- `not_eligible`: carries the reason (`disabled`, `managed_by=dev`, …)
- `waiting_release`: on `main`, but not published or still soaking
- `deferred`: lock busy, or a slot is due soon
- `held`: a blocking sidecar, or a red install doctor before the update started
- `applied`
- `applied_with_sidecars`
- `failed`

`step` is one of `check`, `fetch`, `venv`, `vault`, `verify`.

**Existing inputs the evaluator also reads:**
- `.scout-state/schedule.yaml` and `.scout-state/last-fire.json`, for slot liveness;
- `schedule-events-*.jsonl` (`schedule.tick.completed`), for scheduler liveness;
- `brain_merge.pending_brain_sidecars`, for sidecars;
- `git log`, for lane output, ported from `session-lane-liveness.py`.

**`.scout-state/health-notify.json`** (new, notifier state only):
`{finding_id: {first_seen, last_pushed, pushes}}` plus `last_overall`.

### 4.2 Findings: what turns it yellow or red

`RunHealth = {severity, findings[], evaluated_at}`. Each finding is
`{id, severity, since, summary, fix, source}`. Its `id` is stable, so the notifier can de-duplicate.
`severity` is the worst finding, or green when there are none.

| id | Condition | Severity | Source | Issue |
|---|---|---|---|---|
| `auth.expired` | The newest non-skip outcome is `oauth_expired`. Fallback for vaults without the ledger: today's newest-log scan. | red | ledger, log | #267, #233 |
| `runs.failing` | Trailing consecutive non-skip runs with `outcome=failed`: 1 is yellow, ≥2 is red. `since` is the last success. | yellow / red | ledger | #233 |
| `runs.paused` | The newest failure is `rate_limited` and names a reset (`resets_at`) still ahead. Replaces `runs.failing` for that streak. *(Added in implementation, §13.9.)* | yellow | ledger | #407, #424 |
| `slot.never_fired:<key>` | A slot in `schedule.yaml` has no `last-fire` entry, and the vault is older than that slot's longest weekday gap plus its `missed_window_hours`. | yellow | schedule × last-fire | #199 |
| `slot.stale:<key>` | Last fire is older than 2 × the slot's largest legitimate gap (from `weekdays` + `fires_at_local`) + `missed_window_hours`. Red when **every** slot is stale. | yellow / red | schedule × last-fire | #199, #210 |
| `slot.fired_silent:<key>` | Fired at least `missed_window_hours` ago, and no outcome row of that slot *finished* at or after the fire. Not judged while the session lock is held by a live PID. *(Changed in implementation, §13.1.)* | yellow | last-fire × ledger | #218 |
| `scheduler.stalled` | No `schedule.tick.completed` event in the last 2 h. Judged only when at least one local, non-manual slot is scheduled. "Never ticked" also requires that the tick job is installed on this host. *(Changed in implementation, §13.2.)* | red | events | #199 |
| `lane.dark:<type>` / `lane.silent:<type>` | Ported unchanged from `session-lane-liveness.py`: the budgets, `NO DATA YET`, commit attribution. | red / red *(silent changed from yellow in implementation, §13.3)* | git × ledger | #199 |
| `lock.skips` | ≥3 `skipped_lock` rows in 24 h. | yellow | ledger | #218 |
| `update.failed` | The newest `update-outcomes` row is `failed`. | red | update ledger | #360 |
| `update.held` | The newest row is `held`, or `deferred` for more than 72 h. | yellow | update ledger | #360 |
| `update.sidecars` | Brain sidecars are pending and the newest update row lists them. Otherwise the doctor's existing sidecar warning covers it. | yellow | sidecars | #360 |
| `update.behind` | An eligible engine is >14 days behind a published release, or a `dev`/`unknown` engine is behind at all. | note (never changes severity) | self-update check | #360 |
| `update.last` | The newest update row is from an eligible engine: "Last checked <when>: <result>". *(Added in implementation, §13.9.)* | note | update ledger | #360 |

A note on `scheduler.stalled`: on a laptop that just woke, launchd runs the missed `StartInterval`
tick within seconds. So a 2 h threshold doesn't fire on wake. When the scheduler is dead, nothing
evaluates on a timer; only on-demand consumers see it (doctor, status, the app every 600 s). That is
an accepted limit: the heartbeat is the second job and could cross-check, but see §10.

### 4.3 Who gets told

| Consumer | How it reads | What it shows |
|---|---|---|
| **Push** (`scoutctl health notify`) | Called by producers after they write: `run-outcome.sh` after each run, `self-update run` after each attempt, and `heartbeat.sh` on every tick (every 30 min) as a liveness sweep, which catches slots that never fire and so never call `run-outcome.sh`. *(Changed from once a day in implementation, §13.4.)* | Red transitions, a 24 h reminder ("Scout has not completed a run in 3 days — your Claude login expired"), and "recovered". Channels below. |
| **`bootstrap doctor`** | In-process. | A "Run health" section in text output. A `run_health` block in `--json`. The exit code is unchanged (D2). |
| **`scoutctl health`** | — | `--json` (the contract), human text, exit 0/1/2. Replaces the inline Python in `/scout-status` 3g. |
| **`/scout-status`** | `scoutctl health --json` | Headline from the worst finding, using today's wording for `auth.expired`. Per-type "last run" lines come from `last-fire.json` and the ledger, not `git log -10` (#199 §3). |
| **Session wrap** | The pre-flight step of the phases runs `scoutctl health --json` into `.scout-cache/run-health.json`. | The wrap notification leads with red findings, then a yellow summary. Only measured claims (§4.5). |
| **Scout.app** | `bootstrap doctor --json`, which it already runs every 600 s. Decodes the new optional `run_health`. | Settings ▸ Engine gets a "Runs" row with severity and the first finding. The sidebar dot also lights on run-health red. External engines also get an "Updates" row from `update.*` findings (§7.8). |
| **`/scout-meta-review`** | Unchanged for one release, then moved to `scoutctl health`. | — |

**Push channels**, tried in order and recorded per push in the notifier state:
1. **Desktop**: `osascript` (macOS) or `notify-send` with the user bus (Linux), moved from the wrapper into `scout/notify_desktop.py`. It skips `auth.expired` when the wrapper's `.auth-alert-sent` marker is less than 24 h old, so a dead login doesn't ping twice.
2. **Telegram**: through `scoutctl notify telegram` when configured. Red uses `action_required`; recovery and informational pushes use `info`.
3. **`notify.command`** (Q4): an optional `notify.command` in `scout-config.yaml`. It runs the user's command with `SCOUT_NOTIFY_TITLE`, `SCOUT_NOTIFY_BODY`, `SCOUT_NOTIFY_SEVERITY` and `SCOUT_NOTIFY_FINDING` in its environment, with a 30 s timeout. Its exit status is recorded but never changes the caller's. It is how an always-on Linux host without a desktop or Telegram pages someone, for example through `mail` or a webhook `curl`.

### 4.4 Notifier state machine

For each finding `id` that is red now:
- Not in the state, or seen less than 24 h ago and not yet pushed → push "new" and record it.
- Pushed more than 24 h ago → push a reminder that names how long it has been.

When the overall severity drops from red to yellow or green, send one "recovered" push and clear
the red ids. Findings that disappear are dropped from the state. Pushes are best-effort and never
change a caller's exit code.

As implemented, the state machine has five refinements (§13.4–§13.5):
- one call sends at most one red push, listing every new or overdue red, plus at most one "recovered";
- `scheduler.stalled` and `slot.stale` must persist for 20 min before they push;
- lane findings share one state key per lane;
- a push that no channel delivered is retried on the next call;
- calls are serialised by a lock.

`run-outcome.sh` keeps its ledger write and classifier in bash. It loses the streak and Telegram
code and gains:

```bash
"$SCOUTCTL" health notify --quiet >>"$log" 2>&1 \
  || notify_engine_unreachable   # D5: bash, marker-rate-limited, local only
```

### 4.5 Measured claims only (#237)

Upstream has no "run produced nothing" literal; that text lives in one vault's local wrapper. The
rule #237 asks for still applies to everything this design writes:

- `run-outcome.sh` measures `commits_in_window` with `git log --since=@<started_at>`. It first checks
  that `started_at` is a plausible epoch (> 1 000 000 000 and ≤ now). An invalid bound records
  `null`, never `0`: `approxidate` silently treats a bad bound as "now".
- Finding and push text says "N commits in this run's window (newest `abc1234`)", or "no commits in
  this run's window", or says nothing about output when the value is `null`. It never says "by this
  run".

## 5. Runner hardening

### 5.1 Power assertion (#210)

`claude-with-retry.sh` wraps both the attempt and the backoff `sleep`:
- macOS: `caffeinate -i -m -s`.
- Linux: `systemd-inhibit --what=idle:sleep --why="Scout run"` when it is present.
- `SCOUT_NO_CAFFEINATE=1` turns it off.

The backoff sleep is the more exposed line (#210 §"backoff path"), so a test asserts that both call
sites use the wrapper array.

A host that sleeps through a whole slot window is not a power-assertion problem. The existing pmset
wake schedule covers it, and `slot.stale` reports it.

### 5.2 The session lock moves into one sourced file (#218)

The three inline copies of the #331 protocol move to `scripts/scout-lock.sh` (a new cat-1 template).
Each runner sources it.

**Generation 1: wait instead of skip.**
- A busy lock is polled every 30 s for up to `SCOUT_LOCK_WAIT_SECS` (default 1800).
- Recovery, the takeover guard and the 2 h wedge reap keep their current behaviour.
- On timeout, the runner records `outcome=skipped_lock` and exits 0.

**Generation 2: redundancy.**
- At start, unconditionally, the runner looks for a **witness**: the newest ledger row of the same
  runner kind with `outcome=ok` and `finished_at` inside the last `SCOUT_REDUNDANT_WINDOW_MIN`
  (default 60). This reads the ledger, not log mtimes or epilogue dates.
- **Waited** (`waited > 0`) and the runner is `dreaming` or `research` → skip with
  `outcome=skipped_redundant`. The waiter's work is provably the holder's.
- **Every other case** (no wait, or `scout` kind) → log a warning and run. A back-to-back run is
  sometimes the only reader of the previous run's blind spot (#218, second comment).
- `SCOUT_REDUNDANT_WAIT=off|warn|enforce` overrides the default.

The updater's `bootstrap auto` is just another lock holder (D9). A run that fires during it waits,
and the updater's lock hold is bounded by the vault step, which takes seconds.

### 5.3 Heartbeat knobs (#231)

- Wire `min_gap_minutes`, `off_peak_min_gap_minutes`, `dreaming_signal_hours` and
  `research_min_gap_hours` under a nested `heartbeat:` block. The flat keys keep working.
- An unknown key under `heartbeat:` produces a warning in `heartbeat.log` and in doctor. It is no
  longer dropped silently.
- The dirty-tree operand ignores `.scout-state/`, `.scout-logs/` and `.scout-cache/`. A new
  `heartbeat.dirty_tree_triggers: true|false` (default true) can turn it off.
- `heartbeat.log` names the operand that fired (`signal_hours` or `dirty_tree`) and stamps times in
  the configured zone.
- Heartbeat launches set `SCOUT_TRIGGER=heartbeat`, so the ledger can count them.

## 6. Preventing the expired login (#267, remaining items)

- **`claude setup-token` at setup.** `scoutctl setup` gains a step for engines it sets up as
  `install.sh`/`claude-code`.
  - The question: "Scheduled runs use your Claude login, which expires. Create a long-lived token for
    unattended runs? [y/N]". Interactive only.
  - Under `--yes`, it prints how to do it later: `scoutctl auth setup-token`, a new subcommand that
    wraps `claude setup-token` and stores the result.
  - Storage: `~/.config/scout/claude-oauth-token`, mode 0600, outside the vault and never committed.
  - Runners `export CLAUDE_CODE_OAUTH_TOKEN` from it when the file exists and the variable is unset.
  - Doctor warns on wrong permissions.
- **Scheduler pre-flight skip:** not built (D12).

## 7. Headless updates (#360)

### 7.1 Trigger and cadence

- `heartbeat.sh` gains a block next to the lane-liveness one. Once per local day (date marker in
  `.scout-cache/`), it starts `scoutctl self-update run` detached (`nohup`, own session, output to
  `.scout-logs/self-update-<date>.log`), then continues to `heartbeat run`.
- The marker is written only after `self-update run` has recorded a row other than `deferred`. A
  deferred attempt therefore retries on the next heartbeat tick.
- **Preferred window:** the configured off-peak hours (default 23–06). Outside them it runs only
  when the last completed check is more than 36 h old. A laptop that's always asleep at night still
  updates.
- **Not near a run:** defer when the vault lock is held by a live PID, or when any slot is due in
  the next 45 min (from `schedule list-upcoming`).

### 7.2 Eligibility (D7)

Checked in this order, each recording `not_eligible` with its reason:

1. The pointer's `managed_by` is `scout-app` → refuse. A test asserts that nothing past this line
   runs, whatever the config says.
2. `auto_update.enabled` is not `true`.
3. The pointer is missing, or doesn't describe this interpreter.
4. `managed_by` is `dev` or `unknown`. The row still carries `target` from the check, which feeds
   `update.behind`.
5. `channel` is not `stable`.

### 7.3 Steps

| Step | Lock? | What | On failure |
|---|---|---|---|
| `check` | no | `self_update.check()` plus the release check (§7.4). Equal → `up_to_date`. Unpublished or soaking → `waiting_release`. | `failed` with `step=check` only after 3 consecutive network failures; earlier ones are `deferred`. |
| pre-flight | no | `bootstrap_auto.detect(vault)` refuses on a blocking sidecar, and the install doctor is not red. | `held` |
| `fetch` | no | `claude plugin marketplace update scout-plugin`, then `claude plugin update scout@scout-plugin --scope user`. Resolve `installPath` from `claude plugin list --json`. Require that its `plugin.json` version equals the target and that the path differs from the live `engine_root`. | `failed`, old engine live |
| `venv` | no | `install-venv.sh` in the **new** root only. `install-venv.sh` deletes an existing venv, so it never runs against the live root. Then check that `<new>/.venv/bin/scoutctl version` equals the target. | `failed`, old engine live |
| `vault` | `bootstrap` takes it (300 s wait) | `<new scoutctl> bootstrap auto --no-interactive --yes --json --managed-by <same>`, the same call the app's `EngineInstaller` makes. It re-points the plist or cron block, shim and pointer at the new venv. | `LockBusyError` refusal (`mutated:false`) → `deferred`. Any other refusal, or `mutated:true` → `failed` (D10). |
| `verify` | no | New `bootstrap doctor --json`: install severity is not red. | `failed` (D10) |

Results:
- Install doctor green or yellow with no new `conflicts` → `applied`.
- With new `conflicts` (sidecars) → `applied_with_sidecars`, which sends one informational push
  naming the files and `scoutctl bootstrap drift`.

### 7.4 What "available" means (D8)

`self_update.check()` reads `marketplace.json` on the `stable` branch, which `finalize` moves to each
published release ([#362](https://github.com/Raven-Scout/Scout/issues/362)). `self-update run` additionally
requires `GET https://api.github.com/repos/Raven-Scout/Scout/releases/tags/v<target>` (unauthenticated,
one call a day) to return a release that is not a draft, not a prerelease, and whose `published_at`
is at least `auto_update.soak_hours` (default 24) old.

The marketplace engine installs the release's own tree: since [#362](https://github.com/Raven-Scout/Scout/issues/362)
the marketplace pins the plugin to `stable`, so commits that land on `main` after a release never
reach an install. The soak still gates *when* an update applies.

### 7.5 Rate limits and backoff

- At most one `check` per 24 h. At most one applied version per 24 h.
- After `failed` for target V: wait 24 h before retrying V. After 3 failures of V, stop retrying V
  until a newer version appears or the user runs `scoutctl self-update run --force`. Meanwhile
  `update.failed` stays red, with a reminder push every 24 h.
- `deferred` doesn't count as a failure. `update.held` turns yellow once deferrals pass 72 h.

### 7.6 Rollback (D10)

- **Before `vault`:** nothing has been re-pointed, so the old venv, plist, shim and pointer stay
  live. The leftover state is that Claude Code's plugin registry now names V. Interactive sessions
  load V's commands, while scheduled runs and the shim use the old engine. The finding says so, and
  the next successful attempt converges it. (Re-pinning the registry belongs to the tag-pinning issue, Q5.)
- **At or after `vault`:** no automatic downgrade. `update.failed` is red and names the step. The
  fix line is `scoutctl bootstrap upgrade` (idempotent), with the log path. The previous version's
  cache directory is left in place. The updater never deletes engine directories; Claude Code's
  cache owns them.

### 7.7 Sidecars unattended (D11)

They are parked exactly as an interactive upgrade parks them, and runs continue on the live file.
`applied_with_sidecars` sends one push, and the `update.sidecars` / doctor warnings stay yellow until
`scoutctl bootstrap resolve`. Only the parser-sidecar refusal and a red install doctor hold later
updates.

### 7.8 The app

- App-managed engines: nothing changes. The updater refuses at step 1, and Sparkle plus
  `EngineUpgrader` remain the only path.
- External engines with the updater on: Settings ▸ Engine replaces "Copy `/scout-update`" with
  "Updates automatically. Last checked <when>: <result>", read from the `update.*` findings in
  `run_health`. With it off, the hand-off row stays.

### 7.9 Config and the leftovers

- `templates/scout-config.yaml.tmpl` comment:
  "Engines installed with install.sh or Claude Code: when enabled, the heartbeat installs each
  stable release about a day after it is published and tells you if an upgrade needs attention.
  Engines managed by Scout.app ignore this; the app updates itself. Toggle with
  `scoutctl config set-auto-update`."
  It also gains `soak_hours: 24`.
- The template variable renders from the vault's existing `auto_update.enabled`, else from the
  packaged default, not from `connector_inputs` (#195 comment, write side).
- `auto_update_config.py`'s appended-block comment drops "toggle via /scout-update".
- `/scout-setup` Step 3 and its `--auto-update` hand-off are deleted. The retirement spec (S2, §7.1)
  already shrinks `/scout-setup` to a stub; if that stub lands first, this item is a no-op.
- `scoutctl setup` asks "Keep Scout up to date automatically? [Y/n]" for `install.sh`/`claude-code`
  engines and accepts `--auto-update/--no-auto-update`. The default is **on** (Q1), and it passes
  through to `bootstrap auto`, which gains the flag.
- This partly reverses Decision S2 of the setup-retirement spec, for non-app engines only. The spec
  should be amended with a pointer here when this is approved.

## 8. Per-issue mapping

| Issue | Closed by | PR (§9) |
|---|---|---|
| #233 permanent failure notifies nobody | `runs.failing` + `auth.expired` findings, the notifier with reminders, and the engine-unreachable floor | P1, P2 |
| #199 no slot liveness in doctor | `slot.never_fired` / `slot.stale` / `scheduler.stalled` / lane findings in doctor and status; status re-sourced from last-fire + ledger. The #199 comment's "deployment state" ask (is the engine current against a live ref) becomes `update.behind` for every engine kind. | P1, P3, P7 |
| #267 remaining: setup-token; pre-flight skip | `scoutctl auth setup-token` + setup step + runner export; pre-flight closed as won't-do (D12) | P6 |
| #210 no power assertion | `caffeinate` / `systemd-inhibit` on attempt and backoff | P4 |
| #237 hard-coded "run produced nothing" | Measured `commits_in_window` with a validated bound, and the measured-claims rule for all health text. The literal itself is vault-local. | P4 |
| #218 lock wait without completion check | `scout-lock.sh` (wait, skip rows, unconditional witness, skip only for waited dreaming/research) | P4 |
| #231 heartbeat knobs | Nested `heartbeat:` block, unknown-key warning, dirty-tree filter, operand logging, zone stamps | P5 |
| #360 headless self-update | §7 | P7, P8 |

## 9. Sequencing into small PRs

Each PR is independently shippable and leaves `main` releasable.

1. **P1: health core.**
   - `scout/health.py` (evaluator, findings for `auth.*`, `runs.*`, `slot.*`, `scheduler.*`) and
     `scoutctl health [--json]`.
   - Doctor's `run_health` block and text section. The auth check moves out of install severity (D2).
   - Engine only. The doctor JSON change is additive, so the app is unaffected until P3.
2. **P2: notifier.**
   - `scoutctl health notify`, `notify_desktop.py`, the `notify.command` channel (Q4), `health-notify.json`.
   - `run-outcome.sh` drops its streak and Telegram code and gains the notify call plus the bash
     floor.
   - The lane rules are ported into `health.py`. `heartbeat.sh` runs the sweep (every tick, §13.4) instead of
     `session-lane-liveness.py --notify`; the script stays as a stdlib-only manual instrument.
3. **P3: consumers.**
   - `/scout-status` 3g and its last-run lines; the wrap pre-flight in `phases/`.
   - The app's `DoctorReport.runHealth` decoder, Settings ▸ Engine "Runs" row, and sidebar dot, with
     a shared fixture. The plugin and app sides go in ONE commit (the contract rule in the root
     `CLAUDE.md`).
4. **P4: runner hardening.**
   - `scout-lock.sh` (gen 1 + gen 2); skip outcome rows; `outcome` / `trigger` /
     `commits_in_window` fields; `caffeinate`.
   - Template-only plus the cat-1 registration. Vaults pick it up on `/scout-update`, or through P7.
5. **P5: heartbeat config (#231).** Engine only.
6. **P6: setup-token (#267).** `scoutctl auth setup-token`, the setup step, the runner export, the
   doctor permission check.
7. **P7: `self-update run`.**
   - Eligibility, release check, steps, `update-outcomes.jsonl`, the `update.*` findings, the
     heartbeat hook.
   - It switches on for existing non-app vaults whose `auto_update.enabled` is already `true`:
     `/scout-setup` Step 3 wrote that key for years, believing it was live. That is the behaviour
     those users opted into. The P7 release notes must say so, and the first push after an applied
     update names the setting and how to turn it off.
   - `--dry-run` prints the plan without fetching.
8. **P8: switch it on.**
   - The `scoutctl setup` question and flag, `bootstrap auto --auto-update`, the template comment
     and render fix, removing `/scout-setup` Step 3.
   - The app's "Updates automatically" row for external engines (one commit with its fixture).

P1 → P2 → P3 is the critical path for Track 3. P4–P6 are independent of each other and of P2. P7
needs P1 (findings) and P2 (pushes).

## 10. Out of scope

- A scheduled `meta-review` slot (#199's third ask). Run health makes it unnecessary for liveness.
- The heartbeat cross-checking a dead tick job. `scheduler.stalled` is visible on demand. A second
  watchdog is not justified until a dead tick job is observed in the field.
- Pinning marketplace engines to release tags ([#362](https://github.com/Raven-Scout/Scout/issues/362), Q5).
- Updating `dev` checkouts (D7).
- iOS app consumption of run health.

## 11. Test strategy

- **Evaluator (P1):** pure function over a fixture vault directory (`tmp_path` with ledger,
  schedule, last-fire, event rows) and an injected clock. Cover:
  - one test per finding, including the boundaries (streak 1 vs 2, exactly 2× gap, a vault younger
    than a slot's gap → no `never_fired`);
  - the #233 shape: 72 `oauth_expired` rows with fresh `last-fire`, so `slot.*` stays green while
    `auth.expired` is red;
  - the #193 shape: a slot key absent from `last-fire`.

  Fixtures are anonymised per `plugin/CLAUDE.md`.
- **Doctor contract:** severity and exit code are unchanged with a red `run_health` (D2). A vault
  whose only problem is an expired login gives install `green` and run health `red`. A JSON snapshot
  test covers `run_health`, and the Swift `DoctorReportTests` decode it with and without the block.
- **Notifier (P2):** a table-driven state machine (new red → push; 23 h → none; 25 h → reminder;
  red → green → one recovery; yellow → none; wrapper marker fresh → no desktop for `auth.expired`).
  Channels are injected; no real `osascript`. `notify.command` gets its env vars, and a hanging
  or failing command is timed out and recorded without changing the exit code.
- **Bash templates (P2, P4):** follow the existing pattern of tests rendering a template and running
  it with stub binaries on `PATH`. Cover:
  - `run-outcome.sh` with an unreachable `scoutctl` → one floor alert, rate-limited;
  - `commits_in_window` against a repo with a commit at `@1500000000` and bounds `@0` / garbage →
    `null`, not 0;
  - both `caffeinate` sites;
  - `scout-lock.sh` wait / timeout / witness cases, including the #218 negative controls: failed
    holder, other kind, outside the window, `scout` kind, the escape hatch, and the unwaited serial
    run that must not skip.

  The three-way regex-equality test is kept.
- **Updater (P7):** `self-update run` with injected `check`, release fetcher, and a fake `claude`
  executable that materialises a new plugin root.
  - `scout-app` refusal is the first test and asserts that no subprocess runs.
  - `dev` / `unknown` → `not_eligible` with a target.
  - Unpublished, prerelease and soaking → `waiting_release`.
  - Lock held → `deferred`, with no fetch.
  - A fetch or venv failure leaves the pointer and plist untouched.
  - `bootstrap auto` refusals map to `deferred` / `failed`.
  - Conflicts → `applied_with_sidecars`.
  - Backoff after 3 failures.
- **End to end (manual, before P8 merges):**
  - A Linux container with `install.sh`, cron and no desktop, pointed at a local marketplace path
    with a fake newer release. Expect: applied within a day; doctor green; scheduled runs continue;
    a conflicting brain edit gives `applied_with_sidecars`.
  - The same on macOS, with an app-managed engine present: the updater refuses.

## 12. Decisions on the review questions (2026-10-11)

Jordan accepted every recommendation on 2026-10-11. The table keeps each question with its decision.

| # | Question | Decision |
|---|---|---|
| Q1 | Auto-update default for non-app engines when `scoutctl setup` / `install.sh` asks, and under `--yes`. | **On** (§7.9). A headless host has no other path to fixes, and D8's soak plus D10's hold limit the blast radius. |
| Q2 | Hold updates on any brain-file sidecar (#360's text), or only on the blocking parser sidecar or a red install doctor? | **Parser sidecar or red install doctor only** (D11), consistent with #266. |
| Q3 | Scheduler pre-flight skip on a dead login (#267). | **Dropped** (D12). Close that checkbox on #267. |
| Q4 | Push channel for headless hosts with no desktop and no Telegram. | **Add `notify.command`** in P2 (§4.3, channel 3). |
| Q5 | Is "published release + 24 h soak" enough, or pin marketplace installs to the tag? | **Ship D8 now.** Tag pinning, including re-pinning the registry on rollback, is tracked in [#362](https://github.com/Raven-Scout/Scout/issues/362). |
| Q6 | Where the setup token lives, and who is offered it. | **A 0600 file at `~/.config/scout/claude-oauth-token`, offered only to non-app engines** (§6). *Amended 2026-10-12: the login Keychain on macOS, the file elsewhere (§13.7).* |
| Q7 | Exclude `dev` engines? | **Excluded** (D7). They get `update.behind` as a note only. |
| Q8 | Doctor exit code. | **Install-only** (D2). `scoutctl health` carries the run verdict. |

## 13. Changes made during implementation (2026-10-11)

P1 and P2 departed from the text above in the ways below. Each change came out of a whole-branch
review that ran `scoutctl health` read-only against a mature vault, and the coordinator agreed to
each one. The sections above point here.

### 13.1 `last-fire.json` is not the dispatch time (P1)

`schedule_tick` builds `last-fire.json` from the newest of two timestamps:
- the `usage-tracker.jsonl` row the tick writes at dispatch;
- the `session-tokens.jsonl` row the Stop hook writes when the session ends.

So a slot's "last fire" is usually its last session's end. As first written, `slot.fired_silent`
looked for an outcome row that *started* after the fire, and it flagged every healthy slot on a
mature vault. A fire now counts as covered by any outcome row of that slot that *finished* at or
after it, minus 120 s of slack. That reading holds whichever timestamp the file carries. A run
still in progress, found by the session lock being held by a live PID, is not judged, because a
research run can outlast `missed_window_hours`. `slots[].last_fire` in the JSON has the same
meaning: the newer of the dispatch and the last session end.

### 13.2 `scheduler.stalled` gating (P1)

The rule as written turned a vault red forever in three cases:
- a second machine syncing the vault, where the events are gitignored but `last-fire.json` is
  committed;
- a host with jobs deliberately not installed;
- a schedule with no local slots.

As implemented, the finding is judged only when at least one local, non-manual slot is scheduled.
Its "has never recorded a tick" branch also requires two things: the tick job is installed on this
host (a launchd plist or the scout-managed crontab line), and the later of the vault's first commit
and the `schedule.yaml` mtime is more than 2 h old. A scheduler that ticked once and then stopped is
still red, with no gate.

### 13.3 `lane.silent` is red, not yellow (P2)

`session-lane-liveness.py` treated FIRING-BUT-SILENT as 🔴 and pushed it. A lane is only "silent"
when it has produced no commits for its whole dark budget while it still fires, so it is broken.
As yellow it would never push, and a lane that budget-skips every run would stay quiet forever,
which is the #233 failure. That keeps "ported unchanged" literally true.

### 13.4 Sweep cadence and the wake grace (P2)

After a laptop wakes, launchd fires the missed heartbeat and the missed schedule tick together, and
`schedule.tick.completed` is written only when the tick ends. A sweep that evaluates first sees "no
tick for more than 2 h" and pushes a false red, then a "recovered" push. The mature vault had 15
tick gaps over 2 h in one week. As implemented:
- `scheduler.stalled` and `slot.stale:*` push only once they have been continuously present for
  20 min;
- `heartbeat.sh` runs `scoutctl health notify` on every tick (30 min) instead of once a day, so a
  real stall is confirmed by the next tick. The notifier is read-only apart from its own state, and
  its state de-duplicates pushes, so running it every tick is cheap.

The notifier's "last overall" severity is the severity it has announced, so a red still inside its
grace window never triggers a "recovered".

### 13.5 Notifier refinements (P2)

- **Coalescing.** A call sends at most one red push, listing every red that is new or overdue a
  reminder, with `auth.expired` first, plus at most one "recovered". State stays per finding. One
  outage, such as an expired login that also darkens four lanes, is one message a day, not five.
- **Lane key.** Lane findings share the state key `lane:<type>`, so a lane alternating between
  silent and dark does not re-push "new" and "recovered" each cycle. `SCOUT_NOTIFY_FINDING` carries
  that key for lanes.
- **Undelivered pushes retry.** When no channel reports success, including the case where no
  channel is configured, every finding the push listed keeps its previous push stamp, so the next
  call retries. A Telegram send refused as a duplicate (#241) counts as delivered.
- **Serialised.** `run-outcome.sh` and the heartbeat can call `health notify` at the same moment,
  so calls take an advisory lock on `.scout-state/health-notify.lock`. If the lock can't be had
  within 10 s, the call skips without pushing.
- **Visible failures.** An internal error in the notifier prints one line to stderr, which lands in
  the run log or `health-notify.log`, and still exits 0.
- **Telegram duplicate guard (#241, folded into P2).** `scoutctl notify telegram` refuses a
  byte-identical body to the same chat within 10 min, unless `--allow-duplicate` is passed. It
  prints its receipt as `id=<id>` on stderr line 1.

### 13.6 The redundancy witness (P4)

§5.2 reads the witness from the ledger, but a ledger row had no field naming the runner that
wrote it: `slot` and `mode` are the dispatcher's slot key, which need not name its type. Rows now
carry `runner` (`scout`, `dreaming` or `research`, from `SCOUT_RUNNER_KIND`, which each runner
exports). Only rows that carry it can be a witness, so rows from older helpers never cancel a run.

A waited dreaming or research run skips only when its witness finished at or after the run's own
start, that is, during its wait. A witness that finished earlier is a serial run that the waiter
did not wait behind (it waited behind some other holder), so it gets the note and runs. This is
the #218 rule ("the waiter's work is provably the holder's") made exact.

`SCOUT_REDUNDANT_WAIT=enforce` skips a waited run of any kind, `scout` included; it never skips an
unwaited run. A wait that runs out records `skipped_lock`, as §5.2 says: no new outcome was needed.

`commits_in_window` is measured over `[started_at, finished_at]` of the ledger row (`started_at`
is the session's start, after the lock and the pre-session hooks). A vault with no git history
records `null`. Of the finding texts, only `runs.failing` describes a run's output, and it states
the measured count (§4.5).

### 13.7 Setup token (P6)

**Storage changed from Q6 (Jordan, 2026-10-12).**
- **macOS:** the token goes in the login Keychain, as a generic-password item: service
  `scout-claude-oauth-token`, account = the user.
  - It is written through `security -i` with the command on stdin, never in an argv that `ps` can
    show. It is then read back to confirm the write, because `-i` can exit 0 after a failed command.
  - It is read with `find-generic-password -w`.
- **Linux and other hosts:** the 0600 file at `~/.config/scout/claude-oauth-token`, as Q6 says.
- Both sit behind a `TokenStore` interface in `scout/claude_auth.py`. The wrapper picks its store
  the same way, by `uname -s`.
- If the Keychain is locked, or refuses a launchd process, the run falls back to the interactive
  login. Any `security` exit other than 44 (no item) counts as that.

The rest of §6 is built as written. P6 also adds:

- **The wrapper exports the token.** The runner export lives in `scripts/claude-with-retry.sh`, not
  in the three `run-*.sh`, because every runner launches Claude through it.
- **Each run records how it signed in.** The wrapper writes `.scout-logs/.claude-sign-in.json`:
  `{at, source, token_unreadable}`, where `source` is `keychain`, `file`, `environment` or `login`.
  It never holds the token.
  - Doctor notes the last run's mode.
  - Run health adds a yellow `auth.token_unreadable` when a stored token couldn't be read.
- **A rejected token names the token fix.** An exported token overrides the interactive login, so
  `claude auth login` can't fix a rejected token. While a token is stored, the wrapper's banner and
  desktop alert, and `auth.expired`'s summary and fix, name `scoutctl auth setup-token` (or
  `scoutctl auth clear`). `auth.expired`'s default fix now says `scoutctl auth setup-token` instead
  of a raw `claude setup-token`, which stored nothing. The contract fixture is regenerated to match.
- **Expiry is estimated.** `claude setup-token` tokens last a year, and the CLI can't report a
  token's expiry. So `~/.config/scout/claude-oauth-token.meta.json` records when the token was
  stored, never the token itself.
  - Doctor warns in the last 30 days, and when the file store is readable by others.
  - Doctor also warns when a token is stored but the vault's wrapper predates the export.
  - Run health adds a yellow `auth.token_expiring` (source `token`).
- **The setup step is install-only.** `scoutctl setup` asks on a fresh install, interactive only and
  default no. It skips the question when a token is already stored, and under `--yes` it prints the
  command instead. An upgrade asks nothing, and app-managed engines are never offered it (Q6).
- **"Not logged in" is an auth failure.** It is the signature of a Mac set up again (#267 comment).
  It joins the three shared auth regexes.
- **Tests can't reach the real Keychain.** `tests/conftest.py` puts a fake `security` first on PATH
  for every test. It is a temp-dir store for Scout's one item, and it exits 99 on any other
  subcommand or service. Scout calls `security` through PATH only, and a test pins that.

### 13.8 A usage limit is one pause, not a failing streak (#424)

Claude Code's weekly limit names a date ("resets Oct 15 at 2pm (Europe/Prague)"), which the reset
parser from #407 did not read. With no `resets_at`, the budget gate backed off for only two hours,
so every slot until the reset started, failed in seconds as `rate_limited`, and `runs.failing` went
red for days, re-pushed every 24 h. #426 taught the parser the date (`Oct 15`, `15 Oct`, ordinals;
the year is the next one in which it is still ahead). Health changed with it:

- **`runs.paused` (yellow) replaces `runs.failing`** when the newest failure is `rate_limited` with a
  `resets_at` still ahead. It says "Claude usage limit — runs paused until <time>" and counts the
  trailing runs that failed on the limit. It is yellow however long that streak is: the slots that
  failed before the reset was known are part of the same pause, and the gate records the slots it
  holds as `skipped_budget`, which never extend a streak. Once the reset passes, the finding goes,
  and the next failure (if any) is an ordinary `runs.failing` again.
- **Lanes inside their budget when the pause began are not judged.** A weekly limit can pause runs
  longer than a lane's dark budget (4 days for briefing). While paused, a lane is reported only if
  it was already past its dark budget when the first run failed on the limit.
- **The notifier says "paused", not "recovered".** When the report drops out of red because the
  pause replaced a red streak, the one push of that transition reads "Scout: runs paused (Claude
  usage limit)" with the pause's summary, instead of "Scout: runs recovered". The pause, being
  yellow, never pushes again; the state machine is otherwise unchanged.

### 13.9 Headless updates as built (P7, P8)

- **`update.last` (note).** A healthy `applied` or `up_to_date` state produced no `update.*`
  finding, so §7.8's "Last checked <when>: <result>" row had nothing to read. Every eligible
  attempt now yields an `update.last` note ("Updates automatically. Last checked 6 h ago:
  updated to 0.17.0."). Notes never change severity or push. Scout.app shows the "Updates" row
  for an external engine whose doctor reports it, in place of the `/scout-update` hand-off. An
  `update.failed` or `update.held` summary takes precedence in that row.
- **`scout-config.yaml.tmpl` no longer exists** (#386 removed it). The §7.9 comment and
  `soak_hours: 24` went into the packaged defaults (`engine/scout/defaults/scout-config.yaml`).
  The `AUTO_UPDATE_ENABLED` render variable now reads this run's flag, then the vault's
  `auto_update.enabled`, then that default.
- **`/scout-setup` Step 3 is reworded, not deleted.** `/scout-setup` is still a full installer.
  Deleting the question would have left its installs off, against Q1. The question now describes
  the real behaviour, and a blank answer means yes.
- **Spec order for `dev`.** A `dev` engine with `auto_update` off gets no target and no
  `update.behind`, as §7.2 orders it. The read-only check runs only for an enabled `dev` or
  `unknown` engine. #360's `git pull --ff-only` for `dev` is superseded by D7.
- **The scheduled gate writes no row** for "outside the off-peak window" or "applied a version
  in the last 24 h", and the §7.5 backoff skips also write no row. The day's marker is written
  for the 24 h and backoff skips, and for every row except `deferred`.

### 13.10 The §11 end-to-end check as a harness (2026-10-13)

v0.18.0 shipped without the manual §11 run. The Linux half is now
`scripts/e2e/self-update/run.sh` (or the manual `e2e-self-update` workflow). It installs
`--from` with that tag's `install.sh` in a throwaway container, with cron, a stub `claude`
and no desktop, then updates to `--to`: a tag, an rc, a release branch or a local build.
It asserts the §7 outcomes and the rollback before `vault`. `release.sh prepare` prints it
as a step, not a gate. The macOS "app-managed engine present" half is
`test_self_update_run_app_managed.py`: the real `default_deps` with every side effect a
tripwire. Two observations from building it:

- The off-peak window reads the vault zone (`user.timezone`, else `/etc/localtime`). The slot
  lookahead reads `TZ` first. They agree on a normal host. The harness sets both.
- The install doctor's launchd check runs on Linux too, so a fresh Linux install is yellow
  and `install.sh` exits 1. The harness reports this as a known issue, along with #433 and
  #434.
