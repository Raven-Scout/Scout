# Scout — Backlog

A running list of improvements to tackle in future versions of Scout.app and
the surrounding Scout system. Items at the top are next-up; items below are
nice-to-haves.

---

## Shipped

### 2026-05-25
- **Action Items "Soon" cluster — delete/edit comments + custom snooze date
  + preserve original section kind.** Four backlog items shipped together:
  - `scoutctl action-items delete-comment` and `edit-comment` are new CLI
    verbs (with paired Python tests). Both take `--by-id` / `--subject` for
    the task and `--index` / `--text` for the specific comment; delete drops
    the line atomically, edit preserves the original indent + author prefix
    and replaces only the body. The `snoozed-until` marker is filtered from
    the selector index so `--index 1` always points at the first real
    user-authored comment.
  - `CommentListView` grows a hover-revealed pencil/trash row per comment.
    Edit flips the row into a `TextEditor` with `⌘↵` to save; delete
    confirms via an `.alert` and then routes through the new
    `WriteOp.deleteComment` / `WriteOp.editComment` cases on
    `ActionItemsWriter`. Git history is the archive (writer already
    commits each op path-scoped).
  - `SnoozePopoverView` gains an "Other date…" row that pivots the popover
    into a graphical `DatePicker` panel. The earlier macOS-26 epoch-leak
    bug is sidestepped by initializing the picker's `@State` to
    `.distantPast` and lazy-seeding to *tomorrow* inside the custom panel's
    `onAppear` — the picker never relies on an `init`-time `State` value.
  - `scoutctl action-items snooze` accepts `--from-kind <kind>` which
    records the source section as `(from-kind: urgent)` in the
    `snoozed-until` marker. The app passes the current section's
    `ActionSection.Kind.rawValue` on every snooze, the parser extracts the
    hint (and the carry-in line's optional `, was urgent` tail) into a new
    `ActionTask.snoozedFromKind` field, and `TaskCardView.effectiveKind`
    uses it for the gutter color. An urgent task that lands under
    `## 🛌 Snoozed` on the target day now keeps its visual urgency.
    *Follow-up:* the consolidation skill side (emitting the `, was <kind>`
    tail on the carry-in line in the target day's file) is still
    out-of-scope here — captured in BACKLOG below.

### 2026-04-22
- **Usage Rail Card + Connector Health (Phase 1).** `BudgetRailCard` replaced
  by `UsageRailCard` — reads tokens from a new `.scout-logs/session-tokens.jsonl`
  tracker fed by a Stop hook (`~/Scout/scripts/sum-session-tokens.sh`) that
  sums `message.usage.*` across the session transcript at the end of every
  run. Shows today / week token totals + per-model share; dollar cost hidden
  on purpose (wrong lens on a Claude team-plan seat — included up to quota,
  overage-only billing). Also added `ConnectorHealthRailCard` + top-of-window
  `ConnectorAlertBanner` over the already-wired shell telemetry pipeline
  (`connector-calls-*.jsonl`, `connector-alerts.log`); acks persist to
  `.scout-cache/connector-alerts-acked.json` with fingerprint-scoped GC so
  acking today's CRITICAL doesn't suppress tomorrow's. Old `usage-tracker.jsonl`
  path + `budget-check.sh` dispatcher gate intentionally left untouched —
  Phase 2 replaces them with `/usage` quota data (see below).
- **Launch Claude — split menu with Ghostty/tmux, Claude Desktop Chat, and
  Claude Desktop Cowork.** The old single "Launch Claude" button drove an
  AppleScript that pressed ⌘T and typed into Ghostty, which quietly failed
  whenever Accessibility permission wasn't granted to the current bundle
  ID. Rewritten as a Menu with three targets. The Ghostty path detects a
  running tmux server (via `tmux list-sessions` against `/tmp/tmux-$UID/
  default`, since macOS GUI apps get a different TMPDIR) and spawns a new
  tmux window with `claude` in the Scout directory — the only reliable way
  to get a fresh terminal surface when Ghostty's macOS config sets
  `command = tmux new-session -A`. Claude Desktop paths open
  `claude://claude.ai/new?q=…` or `claude://cowork/new?q=…`. The full
  action-item context (subject + body + prior comments + deep-link URLs)
  is copied to the clipboard on every launch as a reliable ⌘V fallback.
- **Schedules tab.** Full CRUD on `com.scout.*.plist` files from within
  Scout.app — edit times, add new schedules (including the long-missing
  research cadence), delete unwanted ones. Saving writes both the live copy
  in `~/Library/LaunchAgents/` and the repo copy in `~/Scout/launchd/`,
  reloads via `launchctl bootout`/`bootstrap`, and makes a path-scoped git
  commit. Also refactored `LaunchdScheduleService` to use the shared
  `PlistIO` helper, which fixed a latent off-by-one weekday-convention bug.

---

## Action Items view (Scout.app)

### Soon
- **Consolidation-side `, was <kind>` carry-in tail.** The app-side half of
  the section-kind preservation shipped 2026-05-25 — `scoutctl snooze`
  records `(from-kind: <kind>)` on the source-day marker and the parser
  reads both that and an optional `, was <kind>` tail on the carry-in
  line. The remaining work is teaching the consolidation / morning-briefing
  skill to emit `_(carried in from YYYY-MM-DD, was urgent)_` (or
  equivalent) instead of the bare `_(carried in from YYYY-MM-DD)_` when
  the source had a `(from-kind: …)` marker. Once that lands, a task
  snoozed from `🔴 Urgent` on Monday and rendered under `🛌 Snoozed` on
  Wednesday will keep its urgent gutter without any further app changes.

### Nice-to-have
- **Settings → "Test launch" button for Claude Code.** Surfaced during #12
  smoke testing. A button in the Claude Code card that attempts a launch
  with the current config (path + terminal) right where you configure it,
  so you can validate the `claude` path, terminal target, and Automation
  permission without going to an action item and hitting an error. Would
  also be the natural place to confirm a custom command works. Deferred
  from #12 to keep that PR scoped; the empty-custom case now shows inline
  validation in the meantime.
- **Launch Claude — broader terminal + shell support.** *Mostly addressed
  by #12:* Settings now offers a "Open Claude Code in" picker —
  Auto / Terminal.app / iTerm2 / Custom command — plus a custom `claude`
  binary path override. Auto prefers Ghostty/tmux and falls back to
  Terminal.app. iTerm2 and Terminal.app launch via AppleScript; kitty /
  WezTerm / any other terminal (and any future Linux/Windows host) are
  covered by the custom-command template (`{cwd}` / `{claude}`).
  *Remaining follow-ups:* (a) verify the non-tmux Ghostty `--command=`
  fallback renders a fresh window when the primary instance hasn't set a
  `command = tmux …` override; (b) consider a `.command`-file drop as an
  alternative to AppleScript for Terminal.app (avoids the Automation
  permission prompt).
- **Launch Claude — resolve `claude` path off the main thread.** Follow-up
  from the #12 code review. `ClaudeLauncher.resolveClaudePath(override:)`
  runs synchronously on the main thread; when no override is set *and*
  `claude` isn't at a probed location, the login-shell fallback
  (`$SHELL -lc "command -v claude"` + `waitUntilExit()`) can block the UI
  for ~50–300 ms. Pre-existing (carried over from the old `launchGhostty`)
  but #12 widened the surface (Terminal.app / iTerm2 targets now hit it
  too). The naive fix — offloading the whole `launch` off-main — is unsafe
  because `NSAppleScript.executeAndReturnError` **must** run on the main
  thread. Correct fix: make the launch path async, resolve the path on a
  background executor, then hop back to the main actor for the
  NSWorkspace / NSAppleScript invocation. (The Settings placeholder probe
  is already off-main via `Task.detached`; this is only about the launch
  path.)
- **Task-relevant cwd for Launch Claude.** Currently every Ghostty launch
  opens in `~/Scout`. If the task's deep links include a GitHub PR URL
  (e.g. `github.com/acme/mcp-server/pull/42`), we could try common clone
  locations (`~/<repo>`, `~/code/<repo>`, `~/src/<repo>`) and cd there
  instead. Falls back to `~/Scout` when nothing matches.
- **Keyboard navigation.** Arrow keys to move focus between cards; Enter to
  open the composer on the focused card.
- **Bulk actions.** Multi-select cards → mark done / snooze all together.
- **Drag-and-drop reorder** within a section, writing a stable ordering
  marker back to the markdown.
- **Pinned filter presets.** Save a filter combination (e.g. "Urgent +
  Watching, Open only") and restore it with one click.
- **In-card deep-link inline preview.** Hover a Linear chip → preview the
  issue title / status without leaving the app.

## Control Center view (Scout.app)

### Soon
- **Usage Rail Card — Phase 2 (quota bars + dispatcher gate).** Phase 1
  shipped tokens + dollar derivation; Phase 2 surfaces the three `/usage`
  bars (current session, current week all-models, current week Sonnet-only)
  and replaces the dollar-based `budget-check.sh` dispatcher gate with a
  quota-based one. Viable delivery path confirmed during Phase 1 spike:
  the `rate_limits.{five_hour,seven_day}.used_percentage` object is exposed
  to `statusLine` hooks but NOT to any hook that fires for `claude -p`. So
  the implementation is: wrap the global `~/.claude/settings.json`
  `statusLine` command (currently points at claude-hud) with a tee that
  dumps the JSON to `.scout-cache/usage-quota.json` before invoking
  claude-hud unchanged. Interactive Claude Code sessions (which Jordan runs
  throughout the workday) keep the file fresh; it's per-account data so it
  doesn't matter who triggers the refresh. Needs its own spec/brainstorm —
  deferred from Phase 1 to keep scope honest.
- **"Run now" should refresh the heartbeat schedule.** Clicking *Run now*
  from the heartbeat table fires the job, but the scheduled row sits there
  unchanged — the next-fire timestamp doesn't shift and the row isn't
  removed. Expected: either drop the row until the next cron tick recomputes
  it, or have `LaunchdScheduleService.recompute()` fire immediately after
  `RunnerService.runNow` completes so the same item doesn't keep looking
  "queued" at the past time.
### Nice-to-have
- **Usage / Connector Health — Phase 1 follow-ups from final review.**
  Small polish items surfaced by the final branch review on 2026-04-22,
  none blocking:
  - `SessionTokensService.tokens(for sessionId:)` lookup — spec-listed but
    not implemented since nothing currently calls it. Add before the
    per-run stats pane starts consuming token data.
  - "View full report" button on `ConnectorHealthRailCard` footer that
    opens a sheet rendering `~/Scout/knowledge-base/connector-health.md`
    as markdown. Called out in the spec, not in the plan.
  - "Open auth settings" button in `ConnectorAlertBanner` popover —
    deep-links to `claude.ai/settings/connectors` for Google/Granola or
    copies `gh auth login` / plugin re-auth commands to clipboard.
    Called out in the spec, not in the plan.
  - `connector-alerts.log` fixture trimmed to one CRITICAL line during
    implementation to match the test assertion — WARNING-level alert path
    currently has no test coverage. Add a fixture + test for WARNING
    parsing and rendering.
  - `ConnectorAlert.fingerprint` uses raw `connector|level|first_seen`
    string; spec called for `sha256(...)`. Semantically equivalent today
    but will diverge if shell side ever writes its own fingerprints. Align
    before any shell-side ack coordination work.
  - `ConnectorAckStore` wraps its dict in a `DispatchQueue` though all
    callers are `@MainActor`. Swift 6 strict mode will warn; simplify to
    plain `@MainActor` state when the time comes.


- **Activity heatmap should adapt to available history.** Hardcoded 52-week
  grid is overkill when Scout only has ~10 days of data — 99% of cells are
  empty and the real activity crowds into one column on the right. Should
  auto-scale: show from the first-recorded run (minimum 4 weeks, max 12
  months) through today so cells fill the width proportionally. Header
  label ("Activity — last 12 months") should reflect the actual range
  being rendered.
- **`SessionLogService.reconcile()` orphan-sweep end-to-end test.** Task 5
  of the session-status-parser refactor (shipped 2026-04-21) wired the
  orphan sweep into both `loadInitial()` and `reconcile()`, but only
  `loadInitial()` has end-to-end test coverage. A regression in the
  reconcile path would land silently. Needs a controllable `FileSystemEventSource`
  test double (possibly `AsyncStream.Continuation`-backed) since `NoopFS`
  doesn't emit events.

### Bigger initiatives (each needs its own spec/brainstorm)

Decomposed from the broader "make Control Center as good as possible" ask
on 2026-04-21. Recommended build order below. Each lands on the Run detail
pane, so the first one (stats) improves the surface that the next two
render onto.

- **View / interact with a running SCOUT session (RESEARCH-GATED).** The
  fundamentally missing capability — today every scheduled scout run is a
  sealed box. `run-scout.sh` invokes `claude --print ...` with
  `--remote-control-session-name-prefix "scout"`, but the comment in that
  script already flags that *`-p` mode does not support remote control*.
  Scout.app's Runs tab shows only what the session wrote to
  `.scout-logs/scout-*.log` after the fact. Jordan has no way to watch a
  run as it happens, pause it, answer a mid-run question, or drop in to
  course-correct. **This is a research problem first, not a build problem.**
  Open questions to investigate (tracked in `~/Scout/docs/Wishlist.md` as
  a dreaming research topic):
  - Can scheduled runs use a non-`-p` Claude Code mode that keeps remote
    control open while still being headless enough for launchd?
  - Is there a Claude Code "background session" or "detached session"
    pattern that Scout.app could attach to live?
  - As a minimum-viable start, can the app stream `tail -f` of the active
    run's log file (detectable via the lock file `.scout-session.lock` +
    filename pattern) into a live terminal view — read-only, but at
    least Jordan can see what the session is doing right now?
  - Claude Agent SDK alternative: if Scout moved off the `claude` CLI to
    the Agent SDK, would that open up richer streaming / interaction?
    (Bigger rewrite, but may be where the answer lives.)
  - Is there value in a hybrid — scheduled launches stay `-p`, but a
    "Take over this run" button spawns a parallel interactive session
    primed with the current run's context? (Doesn't actually attach to
    the running session, but lets Jordan intervene without waiting for
    it to finish.)
  Once research narrows the options, build out the read-only streaming
  view first (low-risk, high daily value) before attempting true
  attach/interact.

- **Per-run stats pane (v1).** `RunDetailView` today shows cost / errors /
  log-size. Add: duration (from startedAt + endedAt), diffstats for the
  commits in the run's time window (files touched, lines +/-), tool-use
  counts parsed from the log, and tokens in/out if they can be pulled from
  `.scout-logs/usage-tracker.jsonl`. No new surface — reuses the existing
  pane. Should ship **before** the two items below so they land on a
  cleaner detail pane. *Adjacent:* the existing "Budget panel shows $0"
  item is about the source-of-truth for cost, which feeds this pane.

- **Per-run feedback loop (app → dreaming).** Compose feedback from the
  Run detail pane → persist to a per-run file under `.scout-feedback/`
  (or similar) → teach `DREAMING.md` Phase 1 to read it alongside the
  two feedback channels that already exist (Slack DM reactions/replies,
  inline `//==<< ... >>==//` KB comments). Open questions for the spec:
  file format (YAML vs. markdown), whether the feedback dir is
  git-tracked, whether dreaming acknowledges what it processed (so the
  same feedback isn't re-ingested), and whether the composer supports
  ratings/tags or is pure prose. Prior art: the two existing channels
  are documented in `~/Scout/DREAMING.md` Phase 1.

- **Knowledge-graph-touched visualization.** Show every file and KB node
  a run touched, rendered as a graph overlay on the Run detail pane. Two
  phases, ship them separately:
  - **v1 (derived — ships first).** Parse commits-in-window → modified
    files → `[[wikilink]]` edges from each modified `.md` → force-directed
    SpriteKit / Canvas / WebView view. **Uses data we already have**; no
    changes to `run-*.sh` or skills required. Most of the design work is
    layout engine choice, interaction (click node → open file), and how
    the view is anchored in the pane (tab? overlay? modal?).
  - **v2 (explicit emit).** Each SCOUT session drops a sidecar JSON
    listing files *read / written / mentioned* during the run. Requires
    hooking `run-*.sh` to capture tool-use events from Claude Code and
    discipline across every skill to not skip emit. Weeks, not days —
    only worth building once v1 is proven to be worth looking at.

## Triggers tab (Scout.app — new view)

### Soon
- **Triggers tab — manage event-based fire conditions.** Companion UI for
  the event-trigger architecture being built into the scout-plugin engine
  (full design in `~/Scout/knowledge-base/projects/scout/scout-event-triggers.md`
  and `~/scout-plugin/docs/specs/event-triggers.md`). Today's Schedules tab
  surfaces time-based slots (`schedule.yaml`); the new Triggers tab surfaces
  event-based fire conditions (`triggers.yaml`) with the same CRUD pattern
  Plan 6 established for Schedules. **Status: gated on engine `triggers_v1`
  manifest flag.** Don't build the UI until the engine matcher is shipping
  fires. Two delivery phases:
  - **Phase 1 — read-only Triggers tab.** Lists all configured triggers
    from `triggers.yaml`, shows enabled/disabled state, last-fire-ts,
    daily fire count vs. cap, source connector liveness (pulled from the
    same `ConnectorHealthService` already in Scout.app v0.1.6). Per-row
    "Recent fires" inline expansion reads `.scout-logs/trigger-fires-*.jsonl`
    and renders the last 20 fires with their matched event payload + the
    dispatched action's outcome. Enables Jordan to *see* what's firing
    without touching YAML.
  - **Phase 2 — form-based editor.** Mirror the Schedules tab CRUD
    affordance (`Scout/Services/ScheduleEditService.swift` is the pattern
    to copy): edit existing triggers in-place, add new ones from a
    source-specific template, delete with confirmation. Each source's
    `SUPPORTED_MATCH_TYPES` becomes a dropdown; action-kind picker drives
    a conditional form (notify → surfaces picker; run_skill → skill picker
    populated from installed skills; interactive → no extra fields). Save
    writes through `TriggerEditService` (new, modeled on `ScheduleEditService`)
    → atomic temp-file + rename + mtime stale-check + path-scoped git commit
    + `scoutctl trigger reload` invocation.
- **Trigger-fire notifications surface in Scout.app menu bar.** When a
  trigger with `action.kind: interactive` fires, the matching
  `needs-jordan.md` artifact needs a visible affordance — menu-bar badge
  count + dropdown listing pending interactive triggers with "open in
  Claude Code" button (reuses the existing Launch Claude split-menu
  pattern). Don't compete with the Action Items view; this is a
  parallel surface for trigger-driven asks specifically.

### Nice-to-have
- **Trigger fire history view.** Beyond per-row recent fires, a full-tab
  history showing all trigger fires across all triggers, filterable by
  time range / source / action outcome. Reuses the Action Items
  table-vs-cards toggle pattern from Plan 7.
- **Trigger simulation / dry-run.** Right-click a trigger → "Test against
  last 24h of events" → invokes `scoutctl trigger test <id> --dry-run`
  and renders the would-fire list inline. Useful for tuning a match
  filter without enabling the trigger.

## Scout system (sessions, CLIs, pipelines)

### Soon
- **Scout-session awareness of snoozes.** Consolidation / dreaming sessions
  should read the target-day ``_(carried in from YYYY-MM-DD)_`` annotations
  so a task's snooze lineage is visible to the briefing prompt. Right now
  a snooze removes the source line and future sessions see a gap in the
  thread unless they ``git log`` it.
- **Comment-deletion helper CLI.** Paired with the app-side delete (above).

### Nice-to-have
- **Telemetry for writer errors.** ``ActionItemsWriter`` classifies failures
  (``.noMatch`` / ``.ambiguous`` / ``.environment`` / ``.other``); counting
  those per day in a sidecar log would make divergence between Scout.app
  and the Obsidian workflow easier to spot.

## Known paper cuts
- **Env banner copy drift.** The "missing: …" banner line lists scripts by
  filename; if the set of required CLIs changes, we need to keep the banner
  and ``ActionItemsEnvironmentCheck.requiredScripts`` in sync manually.
- **FilterChipsView's dual "all" representation.** ``filter.kinds == []``
  and ``filter.kinds == {every known kind}`` are treated as equivalent in
  the filter pipeline. Currently normalised to ``[]`` after every toggle
  but worth simplifying the data model so only one representation is valid.
