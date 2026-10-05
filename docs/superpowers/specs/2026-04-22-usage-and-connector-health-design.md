# Usage Rail Card + Connector Health — Design

**Status:** Draft for review
**Author:** Jordan (paired with Claude)
**Scope:** Phase 1 only. Quota (`/usage` bars) deferred to a separate Phase 2 spec; rationale in *Non-goals*.

---

## Motivation

Today, two Control Center capabilities are broken or missing:

1. **`BudgetRailCard` reads zero.** It sums `Run.cost` joined from `usage-tracker.jsonl`, but ~50% of those rows log `budget_spent=0` because Scout sessions exit before the model writes real cost (the script itself warns about this at `write-session-cost.sh:50-58`). On top of that, the view hardcodes a `$8/day` cap while `.scout-config.yaml` is actually `$150/day`, and the concept of dollar cost is itself the wrong lens for a Claude team-plan seat — included usage is free up to a per-session and per-week quota, and only *overage* gets billed.
2. **Connector health is invisible in the app.** A recent change wired per-connector telemetry via `~/Scout/hooks/connector-log.sh` (PostToolUse) and `~/Scout/scripts/connector-health-report.sh`. The pipeline writes `.scout-logs/connector-calls-YYYY-MM-DD.jsonl`, `.scout-logs/connector-alerts.log`, and `knowledge-base/connector-health.md`, plus `.scout-cache/connector-alerts-pending.md` for the next Slack DM. None of that surfaces in Control Center — so a broken Google Drive auth is only visible via a Slack phone push.

Both items are called out in `BACKLOG.md` under *Control Center view → Soon*, and both are prerequisites for the larger "per-run stats pane" initiative (the stats pane needs trustworthy cost/token data to render against).

## Non-goals

- **Quota utilization (`/usage` bars).** Investigation confirmed that `rate_limits.{five_hour,seven_day}.used_percentage` is only delivered via the `statusLine` hook, which does not fire for headless `claude -p` sessions (the `Stop` hook's payload has `transcript_path` but `.rate_limits` is `null`). A viable Phase 2 path exists — wrap the global statusLine command to tee the JSON into `.scout-cache/usage-quota.json` so interactive Claude Code sessions refresh per-account quota data — but it touches the user's global `~/.claude/settings.json` and wants its own design cycle.
- **Replacing the dispatcher gate.** `heartbeat.sh:52-58` calls `budget-check.sh`, which reads `usage-tracker.jsonl`. This design does not touch that gate. `write-session-cost.sh` / `usage-tracker.jsonl` continue to exist and receive the same (unreliable) writes they do today. Phase 2 will rewrite the gate against quota utilization.
- **Fixing the "~50% of session-reported rows are $0" bug in the existing tracker.** It's irrelevant to this work — we bypass that path entirely with a new, runner-guaranteed capture mechanism.
- **UI snapshot tests.** Scout.app has no snapshot-testing infrastructure today. Not worth building it for this change.

## Architecture

Two independent features share one pattern: the shell side produces structured telemetry, Scout.app reads and renders. **No changes to `run-scout.sh`, `run-dreaming.sh`, `run-research.sh`, `heartbeat.sh`, or `RunnerService.swift`.**

```
┌─ Scout system (shell) ─────────────────┐   ┌─ Scout.app (Swift) ─────────────────┐
│                                        │   │                                     │
│ Stop hook → sum-session-tokens.sh  ────┼───┼→ SessionTokensService               │
│   reads transcript_path                │   │   (.scout-logs/session-tokens.jsonl)│
│   writes .scout-logs/session-          │   │                                     │
│     tokens.jsonl                       │   │→ UsageRailCard (replaces Budget)    │
│                                        │   │   renders tokens + split + model%   │
│ PostToolUse hook → connector-log.sh    │   │                                     │
│   (exists) → connector-calls-*.jsonl   ├───┼→ ConnectorHealthService             │
│                                        │   │   (parses *.jsonl directly)         │
│ run-scout.sh end → connector-health-   │   │→ ConnectorHealthRailCard            │
│   report.sh writes connector-alerts.log├───┼→ ConnectorAlertBanner (top-of-window│
│                                        │   │   reads alerts.log + ack sidecar)   │
└────────────────────────────────────────┘   └─────────────────────────────────────┘
```

### Why Stop hook, not runner stdout parse

The existing runner scripts invoke `claude -p --output-format=text` (implicit default). Switching to `--output-format=json` would give us `total_cost_usd` and per-model `usage.*` for free, but would change the stdout contract the runners already rely on (they log the text response verbatim). The `Stop` hook is a lower-impact touch-point: it's invoked exactly once at session end with a payload that includes `transcript_path` pointing at the Claude Code session's own JSONL, and a Scout-scoped project `.claude/settings.json` registers it without touching global settings. The transcript contains every turn's `message.usage` block — same information the JSON output would have given us, without contract-breaking the runners.

## Components

### Shell (new)

- **`~/Scout/scripts/sum-session-tokens.sh`** — invoked by Stop hook. Reads a JSON payload on stdin, extracts `.transcript_path`, sums token fields across every turn in that JSONL, writes one row to `.scout-logs/session-tokens.jsonl`.

  Schema per row:
  ```json
  {
    "ts":                         "2026-04-22T22:10:33Z",
    "ts_et":                      "2026-04-22 18:10 EDT",
    "session_id":                 "672c14ea-8d4d-...",
    "scout_mode":                 "dreaming",
    "primary_model":              "claude-opus-4-7",
    "input_tokens":               42300,
    "output_tokens":              8100,
    "cache_read_input_tokens":    2100000,
    "cache_creation_input_tokens":156000,
    "cost_usd":                   4.12,
    "num_turns":                  37,
    "duration_ms":                512000,
    "error":                      null
  }
  ```

  `scout_mode` reads from `$SCOUT_MODE` env var set by the runner scripts (already exists). `cost_usd` derived via a small pricing dict at the top of the script:
  ```bash
  # PRICING: $/1M tokens. Phase 2 will make this irrelevant once quota % is the display metric.
  OPUS_INPUT=15.00; OPUS_OUTPUT=75.00; OPUS_CACHE_READ=1.50; OPUS_CACHE_CREATE=18.75
  SONNET_INPUT=3.00; SONNET_OUTPUT=15.00; SONNET_CACHE_READ=0.30; SONNET_CACHE_CREATE=3.75
  ```
  Primary model = the model used on the most turns. Cost computed per-turn against that turn's model, then summed — so mixed-model runs are priced correctly.

- **`~/Scout/.claude/settings.json`** — new project-local settings file (currently does not exist). Registers the Stop hook pointing at `sum-session-tokens.sh`. Scout-scoped: does not touch `~/.claude/settings.json` or `claude-hud`.

  ```json
  {
    "hooks": {
      "Stop": [{
        "hooks": [{
          "type": "command",
          "command": "$HOME/Scout/scripts/sum-session-tokens.sh"
        }]
      }]
    }
  }
  ```

### Shell (untouched)

- `write-session-cost.sh`, `usage-tracker.jsonl`, `budget-check.sh`, `heartbeat.sh`, `connector-log.sh`, `connector-health-report.sh`, `connector-alerts.log`, `connector-alerts-pending.md`, `connector-health.md` — all continue to behave exactly as they do today.

### Swift (new)

- **`Scout/Services/SessionTokensService.swift`** — `@MainActor` ObservableObject mirroring the shape of `UsageTrackerService`. `@Published entries: [SessionTokenEntry]`, backed by `FileSystemEventSource`-watched `.scout-logs/session-tokens.jsonl`. Parser silently skips unparseable lines (same defensive pattern as `UsageTrackerService.parseFile`). Exposes lookup methods:
  - `tokens(for sessionId: String) -> SessionTokenEntry?`
  - `totalsForToday()` / `totalsForCurrentWeek()` returning `TokenTotals` (input/output/cache_read/cache_create/cost, plus per-model breakdown).

- **`Scout/Services/ConnectorHealthService.swift`** — `@MainActor` ObservableObject. Two responsibilities kept deliberately separate:
  1. **Matrix & rates:** reads all `.scout-logs/connector-calls-*.jsonl` within the 14-day window, groups by `session_id × connector`, computes ok/err counts + 7-day success rates. Pure aggregation, no alert logic duplicated from the shell.
  2. **Alert state:** reads `.scout-logs/connector-alerts.log` (newline-delimited, shell-authoritative), parses the most-recent entry per `connector+level` fingerprint, cross-references `ConnectorAckStore` to filter out acked alerts.

  The connector set rendered in the card matches `connector-health-report.sh:29-41` exactly (Slack, Linear, Gmail, Calendar, Granola, Drive, GitHub, Chrome). Constant declared once in Swift so the two sides can drift only with an obvious diff.

- **`Scout/Services/ConnectorAckStore.swift`** — JSON-backed store at `.scout-cache/connector-alerts-acked.json`:
  ```json
  { "sha256(connector|level|first_seen_ts)": "2026-04-22T22:10:33Z" }
  ```
  Fingerprint = `sha256(connector + "|" + level + "|" + first_seen_ts)`. Using `first_seen_ts` in the hash means acking the *current* Drive CRITICAL doesn't suppress a *new* Drive CRITICAL that starts tomorrow. On app launch, GC any fingerprints that no longer appear in `connector-alerts.log`.

- **`Scout/ControlCenter/UsageRailCard.swift`** — replaces `BudgetRailCard` in `ControlCenterView.rail` (ControlCenterView.swift:59-66). Layout:
  ```
  ┌──────────────────────────────────────────────┐
  │ TODAY'S USAGE                                │
  │ 11.3M tokens                                 │
  │ in 1.2M · out 340K · cache-r 8.9M · cache-c  │
  │   920K                                       │
  │                                              │
  │ Week: 47.8M tokens  ·  opus 85%  sonnet 15%  │
  │                                              │
  │ Quota: TBD (Phase 2)                         │  (faint)
  └──────────────────────────────────────────────┘
  ```
  Today = `cal.isDateInToday(entry.ts)`; week = current ET week (Monday-start, aligning with Scout's existing `EasternWeek` helper if present; otherwise calendar week). `BudgetRailCard` is deleted in the same change — the file content is effectively renamed.

- **`Scout/ControlCenter/ConnectorHealthRailCard.swift`** — slots into `rail` between `RepoStateRailCard` and `SignalsRailCard`. Layout:
  ```
  ┌──────────────────────────────────────────────┐
  │ CONNECTOR HEALTH                             │
  │             r1  r2  r3  r4  r5   7d         │
  │ Slack       ✅  ✅  ✅  ✅  ✅  100%         │
  │ Linear      ✅  ·   ✅  ✅  ✅   97%         │
  │ Gmail       ✅  ✅  ⚠️  ✅  ✅   94%         │
  │ Calendar    ✅  ✅  ✅  ✅  ✅   99%         │
  │ Granola     ❌  ❌  ❌  ❌  ❌    0%  ⚠ crit│
  │ Drive       ✅  ✅  ✅  ·   ✅   98%         │
  │ GitHub      ✅  ✅  ✅  ✅  ✅  100%         │
  │ Chrome      ·   ·   ·   ·   ·    —           │
  │                                              │
  │           [ View full report → ]             │
  └──────────────────────────────────────────────┘
  ```
  Five columns (not ten) to fit the 320pt rail. "View full report" opens a sheet rendering `knowledge-base/connector-health.md` as markdown (the shell-generated artifact — no duplication). Column labels `r1..r5` intentionally terse; on hover, tooltip shows `MM-DD HH:mm  dreaming`.

  Empty state (no `connector-calls-*.jsonl` exists yet — this is today's on-disk reality): `"No scheduled runs have produced connector data yet. First run will populate this."`

- **`Scout/ControlCenter/ConnectorAlertBanner.swift`** — red banner stretched across the top of `ControlCenterView` (new row above the existing `header`). Visible only when `ConnectorHealthService.activeAlerts` (filtered through `ConnectorAckStore`) is non-empty. One-line summary: `⚠ Drive connector: CRITICAL — zero successful calls in last 3 runs`. Click → popover with (a) multi-line remediation text pulled from the matching "How to fix" block in `connector-health.md`, (b) "Open auth settings" button that opens the relevant URL or copies a terminal command to clipboard, (c) "Acknowledge" button that writes to `ConnectorAckStore`. Acking dismisses the banner in-app only — the underlying alert continues firing in scheduled-run Slack DMs until the connector actually recovers (that's what keeps Jordan from missing it entirely).

### Swift (deleted)

- `BudgetRailCard` (inside `ControlCenterView.swift`). Being renamed into `UsageRailCard` as its own file; the inline struct is removed.

## Data flow (per-run tokens loop, concrete)

1. `launchd` → `~/Scout/run-dreaming.sh` fires at 6:12 ET.
2. Runner `exec`s `claude -p --settings ~/Scout/.claude/settings.json ...` (note: project-local settings already auto-resolve when cwd is `~/Scout`; the explicit flag is belt-and-suspenders and is not strictly necessary — TBD verified during implementation).
3. Claude Code session runs to completion.
4. Claude Code fires `Stop` hook with stdin JSON: `{"session_id":"...","transcript_path":"/Users/jordanburger/.claude/projects/-Users-jordanburger-Scout/<uuid>.jsonl","cwd":"/Users/jordanburger/Scout",...}`.
5. `sum-session-tokens.sh` reads stdin, `jq -s 'map(.message.usage // empty)'` on transcript path, sums each field, identifies primary model from most-frequent `.message.model` across turns, computes cost, appends JSONL row.
6. `SessionTokensService.startWatching()` kqueue/`FileSystemEventSource` fires. `parseFile` re-reads. `entries` updates. SwiftUI view `UsageRailCard` recomputes.

## Error handling

- **Missing transcript:** `sum-session-tokens.sh` writes a row with zeros and `"error":"transcript_not_found"`. The run still appears in the app with a visible error flag.
- **Corrupt/partial transcript turns:** per-turn skip when `.message.usage` is missing. Sum what's available.
- **Unknown model in pricing table:** log to stderr, charge at Opus rates (conservative default; Phase 2 removes the pricing table anyway).
- **Unparseable JSONL row, Swift-side:** silent skip (matches `UsageTrackerService.parseFile`). Not a user-visible error.
- **Ack fingerprint collision after ack:** if the corresponding entry no longer appears in `connector-alerts.log`, ack is GC'd on next `ConnectorAckStore.load()`.
- **No telemetry files exist yet** (real current state on disk): both cards render clean empty states; no fallback to the old `BudgetRailCard`.

## Testing

- **Unit (Swift):**
  - `SessionTokensService.parseFile` — happy path, malformed line in the middle, empty file, last-line-without-newline.
  - `ConnectorHealthService` matrix aggregation — fixture JSONL covering all four cell states (✅/⚠️/❌/·) and a multi-day window.
  - `ConnectorAckStore` — add / dismiss / fingerprint-collision / GC-on-load.
- **Unit (shell):**
  - `sum-session-tokens.sh` with a canned CC transcript fixture committed to `tests/fixtures/`.
  - Include a mixed-model fixture to assert per-turn pricing is correctly summed.
- **Integration (Swift):**
  - `SessionTokensService` with `FileSystemEventSource` test double driven by `AsyncStream.Continuation`. This fills the same test-double gap flagged in `BACKLOG.md` under *Nice-to-have → `SessionLogService.reconcile()` orphan-sweep end-to-end test* — the double is reusable.
- **Not doing:** UI snapshot tests; end-to-end Stop-hook invocation from a real `claude -p` run (flaky, account-dependent).

## Rollout

All shippable as a single PR. Feature flags unnecessary — the old `BudgetRailCard` is replaced, not toggled; connector card just appears. Before merging:

1. Hand-run `sum-session-tokens.sh` against one existing CC transcript file to confirm the schema.
2. Temporarily register the Stop hook via `~/Scout/.claude/settings.json` and fire a manual `claude -p "..."` in `~/Scout` — confirm one row appears in `session-tokens.jsonl`.
3. Commit after confirmation.

## Open items deferred to implementation

- Exact location of the `ConnectorAlertBanner` row inside `ControlCenterView` (above `header` vs. inside a top-safe-area overlay). Decide during implementation when the layout is visible.
- Whether `SessionTokensService` should also eagerly join against existing `Run` entries for the Per-Run Stats Pane (the next backlog item). Probably yes, but that's the stats-pane's design to make.
- Whether the "View full report" sheet should open `connector-health.md` from `knowledge-base/` or render the live computed matrix. Probably the file — it's what Scout writes elsewhere and is Jordan's canonical format.

## References

- `BACKLOG.md` — *Control Center view → Soon* items for Budget panel and Connector health.
- `~/Scout/scripts/write-session-cost.sh:50-58` — the ~50% zero-cost warning.
- `~/Scout/scripts/connector-health-report.sh:29-41, 208-242` — authoritative connector set and alert rules.
- `scout-app/Scout/ControlCenter/ControlCenterView.swift:59-66` — rail insertion point.
- `scout-app/Scout/Services/UsageTrackerService.swift` — shape `SessionTokensService` mirrors.
- Claude Code binary string dump (spike): confirmed `Stop` hook payload includes `transcript_path`; confirmed `rate_limits` is **not** present in `-p` mode hooks (phase 2 constraint).
