# Scout Event Architecture (v0.5+)

**Date:** 2026-04-25
**Status:** Vision document — not bound to v0.4 implementation. Companion to [`2026-04-24-scout-unification-design.md`](./2026-04-24-scout-unification-design.md).
**Author:** Jordan Burger (drafted with Claude; revised after first review)
**Audience:** Anyone considering using or extending Scout. Read the v0.4 unification spec first for product context — this document picks up where that one ends.

## TL;DR

Scout today is a personal autonomous-knowledge tool: a Claude-powered engine plus a Mac menu-bar app that reads/writes a `~/Scout` data directory. The v0.4 unification spec consolidates engine + plugin + app distribution. This document is the next step.

In v0.5+, **Scout becomes a personal event bus with pluggable bidirectional connectors.** Many sources push events in (Slack messages, Linear status changes, GitHub PR events, Telegram messages, calendar events). Many handlers consume them (action-item updates, KB writes, ad-hoc Claude sessions, projection caches). Action items, the KB, the Mac app, the TUI — all become *projections* over a SQLite event store rather than the canonical store themselves.

A defining property: **Scout itself can author new connectors on the fly.** When the user describes a new source-and-trigger pairing in chat, Scout fills out a connector manifest YAML; the engine validates and starts a worker process. No commit, no PR. The pattern is the same one Home Assistant, Beeper, n8n, and Zapier use, with a Claude-shaped front door.

A second defining property: **Scout is a product, not a personal hack.** A three-tier connector model (official / auto-discovered from the user's existing claude.ai or local MCPs / community-contributed) makes the system useful out-of-the-box for any user, not just for the original author. v0.8 ships `scoutctl connectors discover` to detect and opt-in to MCPs the user already has wired up, reducing first-time-setup cost to near-zero for connector-heavy users.

A third: **the user is treated as an authoritative async source, not as the operator of every action.** Scout fans out scheduled-run summaries to multiple channels (Slack DM, Telegram bot DM, future Email/SMS). Per-run it asks at most N (default 2) targeted questions to fill knowledge-graph gaps; user replies async on whichever channel suits them; the next scheduled session picks up the answers. Interactive escalation (macOS deep-link → Claude Code session with preloaded context) is the escape hatch when async isn't enough.

This is a multi-year evolution split into versions v0.5 → v0.10+. Each step is independently shippable. The endpoint is a system that's harness-independent (Scout as its own MCP client, pluggable LLM backend) and useful to any user who wants reactive synthesis over their personal data sources.

## Why this exists

The v0.4 spec consolidates a working personal tool. It does not address three things that became visible during v0.4 brainstorming:

1. **External event ingestion.** Linear status changes, Slack messages, GitHub events, Telegram messages from the user. Today, none of them flow into Scout. Scout discovers them by polling later, if at all.
2. **Multi-source identity.** Substring matching (`mark_done --subject "lever"`) makes external sync structurally impossible. If Linear says *"ENG-1234 changed state to Done,"* there's no join key from that event to a markdown line in `action-items-2026-04-25.md`.
3. **Self-extensibility.** A growing fraction of the user's actual desire is *"write me a small daemon that does X with my Slack/Linear/calendar/Telegram."* Doing this case by case as new shell scripts is exactly the unmaintainable pattern v0.4 is unwinding.

Without an architectural answer, every new "Scout watches X" feature lands as another bespoke shell script with its own concurrency model, its own credentials handling, its own retry logic, and its own coupling to action-item markdown. v0.4 was the last reasonable iteration of "more scripts;" v0.5 is the architectural shift.

## The reframe

| Before (v0.4 and earlier) | After (v0.5+) |
|---|---|
| Scout is a personal knowledge log | Scout is a personal event bus with attached projections |
| Markdown is the canonical store | Markdown is one projection (the human/LLM-readable one) |
| Scripts are mutators | Scripts are *event sources* and *event handlers* |
| New features = new scripts | New features = new connectors and new subscribers |
| Concurrency = single-writer assumption + atomic rename | Concurrency = SQLite WAL + idempotent handlers |
| Multi-source sync = manual reconciliation | Multi-source sync = each source is just another bidirectional connector |

## Architecture

```
                    External world (sources)
   ┌─────────┬─────────┬─────────┬──────────┬───────────┐
   │ Linear  │  Slack  │ GitHub  │ Telegram │ Calendar  │
   │webhooks │ events  │ webhooks│  bot     │   ICS     │
   └────┬────┴────┬────┴────┬────┴─────┬────┴──────┬────┘
        │ ↕       │ ↕       │ ↕        │ ↕         │ ↕
   ╔════▼═════════▼═════════▼══════════▼═══════════▼════╗
   ║          BIDIRECTIONAL CONNECTORS                  ║
   ║  Each owns one source: inbound (events ← world)    ║
   ║  + outbound (events → world). YAML manifest;       ║
   ║  isolated process; per-connector secrets.          ║
   ╚════════════════════════╤═══════════════════════════╝
                            │
   ╔════════════════════════▼═══════════════════════════╗
   ║             EVENT STORE  (canonical)               ║
   ║   $SCOUT_DATA_DIR/.scout-state/events.db           ║
   ║   SQLite (WAL). Append-mostly + tombstones.        ║
   ║   Row: id, ts, source, kind, payload (JSON),       ║
   ║   dedup_key, superseded_by                         ║
   ╚════════════════════════╤═══════════════════════════╝
                            │
   ╔════════════════════════▼═══════════════════════════╗
   ║                  DISPATCHER                        ║
   ║  Routes events → handlers. Idempotency, retry,     ║
   ║  backpressure, dead-letter queue, snapshots.       ║
   ╚═╦══════════╦═════════╦════════╦═══════════╦═══════╝
     │          │         │        │           │
     ▼          ▼         ▼        ▼           ▼
 Action     KB        Session   Watch       Custom
 Items      Updater   Spawner   Diff       (user-
 Projection                     Stream     authored)
     │          │
     ▼          ▼
 Markdown    KB markdown +
 files       schema files
   ↑
   │ (markdown-watcher emits source:"watcher:markdown" events;
   │  projection handler ignores them — see "Markdown ↔ event store sync")
```

Three properties to notice:

1. **The event store is canonical.** Markdown action items, KB files, and projection caches are all *materializations* of the store. They can be rebuilt from scratch by replay; tombstones suppress superseded events.
2. **Connectors are bidirectional and isolated.** Each connector is its own process, owns inbound (world → events) and outbound (events → world) for one external source. A crash in the Telegram listener doesn't take down Slack. API keys live inside one process per source.
3. **Handlers subscribe to event kinds, not to sources.** The "Action Items Projection" handler subscribes to `action_item.*` events regardless of whether they came from CLI, Obsidian, Linear, or a hand-authored connector — *with one important exception, see "Markdown ↔ event store sync."*

## Core concepts

### Event

Logical schema (shown as JSON for readability; physically stored as a SQLite row with `payload` as a JSON column):

```json
{
  "id": "01HXABC...",
  "ts": "2026-04-25T14:32:01.234Z",
  "source": "connector:linear",
  "kind": "linear.issue.updated",
  "dedup_key": "linear:ENG-1234:state:Done",
  "payload": {"issue_id": "ENG-1234", "state": "Done", "title": "..."},
  "superseded_by": null
}
```

ULID for `id` (sortable, no clock sync needed). `kind` follows a flat namespace: `<domain>.<entity>.<verb>`. `dedup_key` lets the dispatcher drop redeliveries that at-least-once delivery inevitably produces. `superseded_by` is set when a tombstone supersedes the event (see below); replay logic skips superseded rows.

### Tombstone

Append-mostly logs cannot rewrite history; instead, errors and schema changes are corrected by appending **tombstone events** that supersede prior events:

```json
{
  "id": "01HXTOMB...",
  "kind": "scout.event.superseded",
  "source": "scout:migrator",
  "payload": {
    "superseded_id": "01HXBADD...",
    "reason": "wrong issue_id; corrected by 01HXGOOD...",
    "replacement_id": "01HXGOOD..."
  }
}
```

The dispatcher updates `superseded_by` on the targeted row when applying the tombstone. Replay skips superseded rows. Schema migrations emit tombstones for old-shape events alongside new-shape replacements; projections rebuild from the corrected sequence. Tombstones are themselves part of the audit trail and never deleted.

### Schedule events

The schedule dispatcher (`scoutctl schedule tick`, introduced in Plan 5 / `2026-05-04-schedule-v2-design.md`) emits four canonical event kinds. Every tick — whether a normal 5-minute heartbeat or a wake-from-sleep catch-up — produces at least one `schedule.tick.completed` and zero or more per-slot events:

| Kind | Source | Payload fields | When emitted |
|---|---|---|---|
| `slot.fired` | `cli:schedule_tick` | `slot_key`, `slot_type`, `target_local`, `target_utc`, `runner`, `pid_spawned` | The dispatcher spawned the runner subprocess for this slot |
| `slot.skipped` | `cli:schedule_tick` | `slot_key`, `slot_type`, `target_local`, `reason` | The slot was due but skipped; `reason ∈ {on_miss=skip, collapsed-into=<key>, stale-after-window, cooldown_active, laptop-asleep, network-offline}` |
| `slot.fire_failed` | `cli:schedule_tick` | `slot_key`, `slot_type`, `target_local`, `error` | Subprocess spawn failed (runner script missing, lock contention, etc.) |
| `schedule.tick.completed` | `cli:schedule_tick` | `fired: [slot_key,...]`, `skipped: [...]`, `duration_ms` | Every tick, after all per-slot evaluations |

The `slot.skipped` `reason` field is load-bearing: connector-health-report distinguishes `laptop-asleep` skips from real connector outages (no false-positive Slack-dark alerts on a Monday morning that follows a closed-laptop weekend).

In v0.4 these emit through the existing JSONL writer the hooks use; in v0.5+ they flow through `emit()` into the SQLite event store like every other event, picked up by projections (the run-tracker projection updates `last_fire_ts`; a future "missed-runs" projection feeds scout-app's "you missed X runs while traveling" indicator).

### Connector

A worker process that bridges an external source ↔ the event store. Connectors are **bidirectional** — they own both inbound (external → events) and outbound (events → external) for one source. A "Telegram connector" both ingests user messages (via webhook) and sends responses (via Bot API). API keys, rate-limiting, and retry logic stay contained in one process per source.

```yaml
# ~/Scout/connectors/linear.yaml
name: linear
version: 1
authored_by: scout-session-2026-04-25-1432  # or human@email
inbound:
  kind: webhook
  url_path: /webhooks/linear
  secret_env: LINEAR_WEBHOOK_SECRET
  transform:
    jq: |
      {
        kind: "linear.issue.updated",
        payload: {issue_id: .data.id, state: .data.state.name},
        dedup_key: ("linear:" + .data.id + ":state:" + .data.state.name)
      }
  emits:
    - linear.issue.updated
    - linear.issue.commented
outbound:
  subscribes_to:
    - action_item.completed   # mark Linear issue Done when matching action item is completed
  handler:
    builtin: linear-rest-egress  # or "python-script" with handler.py
permissions:
  - read:secret:LINEAR_WEBHOOK_SECRET
  - read:secret:LINEAR_API_KEY
  - write:event_store
  - call:external:linear.app/graphql
runtime:
  builtin: connector-runner
  resources: {cpu: "100m", mem: "64Mi"}
```

The engine ships **builtin runners** (`webhook-runner`, `polling-runner`, `bot-runner`, `script-runner`, `connector-runner`) that interpret manifests of common shapes. Power-user fallback: a connector can ship a `connector.py` and `handler.py` implementing the documented `Connector` interface.

#### Egress failure handling

External APIs fail. Linear returns 502; Slack rate-limits; a Telegram bot token expires. Every connector follows a uniform protocol so the rest of the system stays predictable:

1. **Retry with exponential backoff.** Standard envelope: 1s, 4s, 16s, 60s, then give up. Per-source overrides (e.g., respect `Retry-After` headers) are allowed.
2. **Emit on giveup.** After the retry budget is exhausted, the connector emits `system.connector.egress_failed`:

    ```json
    {
      "kind": "system.connector.egress_failed",
      "source": "connector:linear",
      "payload": {
        "target_event_id": "01HX...",
        "error_class": "http.502",
        "attempts": 4,
        "outbound_kind": "outbound.linear.update_issue"
      }
    }
    ```

    The original outbound event's `superseded_by` is set to the failure event's id so future replays don't retry indefinitely.
3. **Dead-letter projection.** A built-in projection consumes `system.connector.egress_failed` events and exposes them via `scoutctl connector dead-letter list`. Scout-app's status bar surfaces a count plus a per-source banner: *"Linear API is down — 3 outbound updates queued."*
4. **Manual replay or discard.** `scoutctl connector dead-letter retry <event_id>` re-emits the original outbound event with a fresh attempt counter. `scoutctl connector dead-letter discard <event_id>` writes a tombstone and removes the entry from the dead-letter projection (used when the external state has already been resolved out-of-band).

Inbound delivery failures (a webhook the connector couldn't parse, an auth signature mismatch) follow the same protocol with `kind: "system.connector.ingress_failed"`. The point is that no event ever silently disappears: every failure is itself an event in the store, visible to the user and to future replays.

### Handler

A function that subscribes to event kinds and emits side effects (which may include further events). In v0.5 these live in the engine as Python; in v0.7+ they can be user-authored alongside connectors:

```python
@handler.subscribe("action_item.completed", "linear.issue.updated")
def update_action_items_projection(events: list[Event]) -> list[Event]:
    """Materialize event batch into the action-items markdown.
    Idempotent: re-runnable on the same events."""
```

### Projection

A derived view rebuilt from events. The action-items markdown projection is one. The Mac app's in-memory cache is another. The KB summary cache (Plan 5 of v0.4) is a third. Projections may lag the store; the dispatcher tracks "projection X is current through event Y" and exposes that to UIs.

To keep replay cheap as the store grows, the dispatcher maintains **projection snapshots**: every N events (or on schedule), each projection's current state is written to a snapshot row in SQLite. Replay starts from `latest_snapshot.event_id` rather than the beginning of time. Snapshot tables are themselves rebuildable from the log; truncating them is safe.

### Markdown ↔ event store sync

Markdown is editable. Users edit `action-items-2026-04-26.md` in Obsidian, by hand. Those edits must flow back into the event store, since the store is canonical. But the action-items projection handler also rewrites markdown when, say, a Linear event marks a task done. Without care, this produces an infinite loop: handler writes markdown → file watcher detects change → emits event → handler writes markdown.

The rule: **events are tagged with their source; handlers respect origin.**

Concretely:

1. A markdown file watcher subscribed to `~/Scout/action-items/*.md` parses the file on change, diffs against the last-known projection state stored in SQLite, and emits granular events tagged `source: "watcher:markdown"`.
2. The action-items projection handler subscribes to `action_item.*` events from **all sources except `watcher:markdown`**. Watcher-sourced events are persisted (they're real history) but trigger no markdown rewrite — the file is already in the desired state.
3. External-source events (`connector:linear`, `connector:slack`) flow through the handler and *do* trigger markdown rewrites. The watcher then sees the rewrite, parses it, diffs, and finds no net change (the projection state already matches) — no event emitted, loop closed.

**Conflict case:** user edits a task in Obsidian *while* a Linear webhook arrives for the same task. Two events land for the same `item_id`, with different sources, near-simultaneously. v0.5 resolves with **last-write-wins by `ts`**, plus a banner in scout-app and a `scout.conflict.detected` audit event recorded to the store. Per-field merge ("Linear can change `state`, only the user can change `title`") is a v0.9+ refinement; the conflict surface is small enough at single-user scale that LWW + visible warning is honest.

**Identity preservation:** action items in markdown carry a short prefix (see *ID surface forms* under "What v0.4 must commit to"). If the user accidentally deletes the prefix, the diff engine fuzzy-matches by title + section position. If no match, the line is treated as a *new* item — never silently merged with an old one.

## Architectural principles

- **Hexagonal architecture (Cockburn).** Core domain (action items, KB, sessions) is at the center. *Ports* are interfaces (`EventSource`, `EventStore`, `Projection`, `SessionLauncher`). *Adapters* are swappable implementations. New external sources plug into existing ports without touching the core.
- **Bidirectional connectors.** Every external system is owned by one connector process responsible for both directions. API keys, rate limits, and retry logic live there. Handlers never call external APIs directly — they emit `outbound.<source>.*` events that the relevant connector subscribes to.
- **CQRS / event sourcing.** Writes go to the event store; reads come from projections. Current state is `fold(events_since_latest_snapshot, snapshot)`. Replay rebuilds any projection.
- **Idempotency by construction.** Every external source delivers at-least-once. Every handler must be re-runnable. `dedup_key` plus stable IDs make this tractable.
- **Capability-based security.** Each connector manifest declares required secrets, event kinds, and external destinations. The engine grants exactly those — never more. A user-authored connector cannot quietly read other connectors' API keys. New permission grants prompt the user.
- **Backpressure and coalescing.** Slack busy-day = 100 events/min. The dispatcher coalesces: *"5 messages in thread X within 30s → one `slack.thread.active` event."* Per-handler rate limits prevent runaway Claude session spawning.
- **Choreography over orchestration.** Handlers react to events independently. One event can have many subscribers; new subscribers don't require coordinator changes. Orchestration (a central state machine) is reserved for true multi-step sagas.
- **Origin-aware sync.** Events carry `source`. Projection handlers that round-trip through external surfaces (markdown, the Mac app's edit fields) skip events whose source is the same surface — closes feedback loops by construction.
- **Eventual consistency, made visible.** Projections may lag. The Mac app shows a small indicator when its view is more than ~2s behind the store. Honest UX beats the illusion of synchronous truth.
- **Fail open, retry forever.** A handler crash logs to a dead-letter queue and retries with exponential backoff. The event is never lost. The user sees a banner; nothing wedges silently.
- **WAL checkpoint discipline.** The dispatcher owns SQLite WAL checkpointing. It runs `PRAGMA wal_checkpoint(TRUNCATE)` on an hourly cadence and after each batch of ≥100 events processed. All other processes (connectors, projection handlers, CLI invocations) open short-lived connections — they never hold read transactions across event-loop iterations. This prevents unbounded `events.db-wal` growth (a long-held read transaction blocks checkpointing in WAL mode) and keeps checkpoint latency predictable.
- **Multi-source identity matching.** Every entity is matchable on ≥ 2 identifiers from different sources (phone + Slack ID + email + WhatsApp ID + Telegram chat ID + GitHub handle + Linear user ID, etc.). Single-source matches go to a review queue; they never auto-mutate the entity. Names alone are not stable join keys across systems — phone numbers are; email is; account-system IDs are. Adding a new connector tier means adding new identifier fields to the `person` (and other) entity schemas — not a new matching algorithm.
- **Knowledge velocity exceeds human bandwidth — by design.** Scout's value is producing more knowledge than the user can review in real time. The architecture is built around that as a feature: the user is the authority on facts only they know; Scout handles routine writes autonomously and surfaces only what genuinely needs human input. The opportunistic-questions budget (per-run cap on KG-gap questions to the user) is the throttle that keeps async collaboration sustainable rather than overwhelming.

## Self-extensibility: Scout authors its own connectors

The motivating scenario:

> *"When my colleague comments 'ship it' on a GitHub PR I authored, send me a Telegram message and append a 🟢 action item to today's list."*

The flow:

1. User invokes a `scout-build-connector` skill in chat.
2. Scout (the agent) elicits the source shape, the trigger condition, the action.
3. Scout fills out a connector manifest YAML (or, for shapes the manifest can't express, a small `connector.py` + `handler.py` against the documented interface).
4. Scout writes it to `~/Scout/connectors/<name>.yaml` and asks the user to confirm.
5. User runs `scoutctl connector enable <name>`. The engine validates the manifest, prompts for any required secrets it doesn't already hold, and starts a worker process.
6. Inbound events start flowing. A handler subscribes (existing one, or a small new one Scout authored alongside) and produces side effects via outbound connectors — Telegram message via the Telegram connector's outbound channel, `action_item.created` event consumed by the action-items projection.

The pattern is **declarative-first, escape hatch to code**. Scout doesn't write daemons from scratch — that's a debuggability and security catastrophe. Scout fills in templates the engine knows how to run, and writes small handlers against a stable subscription API.

This is the same shape as MCP servers, Home Assistant integrations, and Zapier triggers — all of which thrive precisely because the boundary is well-defined.

### Safety rails on self-authoring

- **Manifest validation** — schema check before enable; rejects unknown fields, unsupported runtime types, malformed jq, undeclared event kinds.
- **User-mediated secret prompts** — the engine never ingests a secret from the agent's chat-message context. User pastes into a separate prompt or keychain entry.
- **Dry-run mode** — `scoutctl connector enable --dry-run` runs the worker against a sample event without producing side effects.
- **Per-connector kill switch** — `scoutctl connector disable <name>` stops the worker; events queued for it drain to dead-letter.
- **Audit log** — every connector enable/disable, every secret grant, every authored manifest is itself an event in the store (`scout.connector.authored`, `scout.connector.enabled`, `scout.permission.granted`). Self-monitoring.

## Connector taxonomy and discovery

Three classes of connector, one consistent integration shape. All three share the same surface: a YAML entry in `connectors.yaml`, a phase MD in `phases/connectors/<name>.md` (consumed by the renderer in Plan 5+), optional remediation guidance in `connector-health-report`, optional MCP server config in `.mcp.json`. Adding a connector to any tier costs the same effort.

| Tier | Source | Examples | Maintenance |
|---|---|---|---|
| **Official** | `scout-plugin` repo | Slack, Linear, Gmail, Calendar, Drive, GitHub, Granola, WhatsApp inbound, Telegram outbound, Google Messages | Maintained by the project; ships in the public plugin |
| **Auto-discovered** | User's existing claude.ai MCP setup, local `.mcp.json`, local Claude Code plugins | Whatever the user has wired (Apollo, Atlassian, Sentry, custom MCPs…) | Plugin detects on session start; user opts in per-connector on first use |
| **Community-contributed** | External repos, community plugin marketplace, `scout-plugin-community` contrib path | Strava, Notion, Apple Reminders, Spotify, anything someone builds for themselves and shares | Loaded from a contrib path or git URL; sandbox-isolated; same phase-MD contract as official connectors |

### Discovery flow

`scoutctl connectors discover` introspects the user's environment and emits a candidate list:

1. **Existing claude.ai MCP connectors** (read via the claude.ai connector list, exposed through the local Claude Code config or an authenticated API call).
2. **Local `.mcp.json` files** in `~/.claude/` and project `.mcp.json` overrides.
3. **Claude Code plugin manifests** in `~/.claude/plugins/` that declare MCP servers.
4. **Community contrib path** (`~/.scout-community/connectors/`) if installed.

For each candidate not yet in `connectors.yaml`, the user opts in via `scoutctl connectors enable <name>`:
- **Official tier:** phase MD + YAML entry already in plugin; enable just instantiates the vault overlay (empty `<vault>/connectors/<name>.local.md` template) and registers any required hooks.
- **Auto-discovered tier:** prompts the user to author a phase MD via `scoutctl connectors author` (Scout-assisted, declarative-first per *Self-extensibility* §). The agent fills out the manifest in chat; the engine validates and starts the worker.
- **Community tier:** clones from the contrib path or git URL into a sandboxed directory under `~/.scout-community/`. Same shape as official but with capability-grant prompts for any new permissions and sandbox isolation enforced by the `connector-runner`.

The discovery + enable flow is explicitly the answer to *"users with their own niche use cases"* — you don't need to fork `scout-plugin` to wire up Strava or Spotify or your company's internal API. Scout discovers what you already have, helps you author the phase MD if needed, and runs it.

## Working with the user as collaborator

Scout treats the user as an **authoritative information source** that responds asynchronously. The architecture supports two modes — async-first (the common case) and interactive-when-needed (the escape hatch).

### Async-first user comms

**Multi-channel notification fan-out.** Every notification carries a `(slot_type, tier)` tuple: `slot_type` is the type of session that produced it (`briefing` / `consolidation` / `dreaming` / `research` / `manual`), tier is `informational` or `action_required`. Per-tuple channel routing is configured in `connectors.yaml`. Routing rules key on slot **type** (the small, fixed plugin vocabulary), not on slot **key** (which is user-chosen and varies per user); see Plan 5 / `2026-05-04-schedule-v2-design.md` for the slot key vs type distinction. Examples:

| Slot type | Tier | Channels | Notification style |
|---|---|---|---|
| briefing | informational | Slack DM (silent), Telegram bot DM (silent) | Run summary, no push sound |
| briefing | action_required | Slack DM, Telegram DM (loud), Email | Sound + 🔴 prefix; surfaces in lock-screen previews |
| dreaming | action_required | Telegram DM only (loud) | Don't wake Slack notifications overnight |
| consolidation | informational | Slack DM (silent) | Quiet — just a record |

Channels (v0.5+): Slack DM, Telegram bot DM, Email, SMS via Twilio. The notification routing layer is a thin abstraction over outbound connectors — adding a new channel is a new outbound connector + a config row.

**Opportunistic knowledge-graph enrichment.** A *KG gap detector* runs as part of every scheduled session's wrap-up phase. It identifies entities with low confidence, missing relationships, or single-source identifier matches. Scout fires at most **N targeted user questions per scheduled run** (default `N=2`, configurable in `connectors.yaml` under `kg_enrichment.questions_per_run`), embedded in the wrap message under a "Help me learn" section.

Examples of opportunistic questions:
- *"Is Andrea Novakova your spouse, partner, or close friend? (relationship_type is currently null on her entity)"*
- *"What does AI-3076 stand for? The Linear issue has no description and you've referenced it in 3 messages this week."*
- *"I see a phone number `+15551234567` in two SMS threads but no `person` entity matches it — should I create one, or is it the same person as someone already tracked?"*

The user replies async via whichever channel they prefer; the next scheduled run picks up the reply via the connector's inbound listener and updates the KG. **The N cap is the throttle**: never overwhelming, always making forward progress on graph quality. Calibrate `N` per user engagement style; some users will tolerate 5/run, others need 1/week.

**Multi-conversation context preservation.** Multiple parallel async threads stay distinct via durable thread/channel IDs. Scout never loses context between a question fired Tuesday morning and the answer received Wednesday evening. Each open question is an event in the store with `kind: scout.question.asked`; the matching reply (when received) is `kind: scout.question.answered` with the source event ID in payload.

**Reply-binding via platform quote/thread features.** When Scout fires multiple questions in one wrap-up message (`N` up to 5 per run depending on user calibration), the inbound connector cannot disambiguate user replies by content — *"she's on the design team"* could plausibly answer any of three "who is X?" questions in the same thread. The disambiguation contract is **the platform's native reply primitive**:

- **Telegram inbound connector.** When emitting `scout.question.asked`, the outbound side records the Telegram message ID returned by `sendMessage`. The inbound webhook handler reads `update.message.reply_to_message.message_id` on incoming user messages; if present, looks up the matching `scout.question.asked` event by message ID; emits `scout.question.answered` with `source_question_id: <ulid>` filled. If absent (the user replied in the thread without quoting), the connector emits `scout.message.received` with no question binding and the next session's planner classifies whether to interpret it as a question answer (with explicit ambiguity disclosure) or a free-form note.
- **Slack inbound connector.** Same pattern using `thread_ts` (the parent message timestamp) — Slack's "Reply in thread" preserves the parent linkage. Each `scout.question.asked` records the Slack `ts` of the outbound message; inbound replies to that `ts` bind unambiguously.
- **Email inbound.** Standard `In-Reply-To` / `References` headers carry the binding.

The user-facing implication: when Scout asks two or three questions in one DM, the user must use the platform's "Reply" / "Quote" feature on the specific question they're answering, not just send a free-form follow-up. The wrap-up message text instructs users to do this on first encounter ("To answer, tap the question and reply / quote-reply"), and `connectors.yaml` includes the canonical instruction string per platform so it stays consistent across runs. Free-form replies stay parseable but with a flagged ambiguity that the next session's planner resolves, possibly by re-asking with a single explicit question if confidence is low.

### Interactive escalation (when async isn't enough)

Sometimes the back-and-forth would take too many async round-trips, or the decision is irreversible enough to warrant a live conversation. The architecture supports both directions:

**Scout-initiated escalation.** When the runner detects a "needs Jordan" blocker class — calendar mutation requiring approval, sandbox-scope grant needed, ambiguous classification, conflict requiring human resolution, or a synthesis that the user must validate — it composes context, writes it to `.scout-state/interactive-context/<context-hash>.md`, and fires a Telegram message with a `claude-scout://session/<context-hash>` deep link. User taps; macOS handler launches Claude Code into a fresh interactive session with the context preloaded as a `--resume`-equivalent. User decides; session ends; the result feeds the next scheduled run via a `scout.interactive.completed` event.

**User-initiated session.** The user DMs the Scout bot (`/work`, `/ask`, or freeform). The bot:
- If the user is at their machine (heartbeat / last-activity timestamp within 60s), fires the same launcher mechanism as Scout-initiated.
- If not, replies *"queued for next scheduled run — I'll have an answer for you in ~3 hours"* and writes a JSONL row to `.scout-state/queued-questions.jsonl`. The next scheduled session reads queued questions during wrap-up and answers them in the wrap message.

Both directions need: (a) a deep-link/launcher mechanism on macOS (custom URL scheme `claude-scout://`), (b) a return bridge that ingests inbound channel messages as feedback signals — same pattern as Slack thread-reply ingestion that already exists for the Apr 27 feedback-prefetch hook.

## Long-term: harness independence

Scout v0.4–v0.8 rides on Claude Code's harness — schedule via launchd → run-scout.sh → claude CLI → MCP servers via claude.ai connectors. v0.9+ targets harness independence: Scout owns the agent loop directly.

What that means concretely:
- **Scout becomes its own MCP client.** Manages credentials, MCP server lifecycles, and connector authentication directly — no claude.ai marketplace dependency.
- **Pluggable LLM backend.** Anthropic SDK, Claude API, or any provider becomes a swappable adapter. Sessions can run against different models per mode (cheap-fast for consolidation, large-context for dreaming).
- **Decouples Scout from a single vendor.** The connector tier model, event store, and projection-consumer contracts already separate concerns; harness independence is the last vendor-coupling to break.

Multi-year horizon. Architectural rules to keep the door open today (every v0.5–v0.8 design choice should respect these):
- Engine code never imports Claude-Code-specific paths. Session JSONL formats, the `~/.claude/projects/...` directory layout, and the `claude` CLI surface are *not* dependencies of `scout-engine`.
- Connector definitions describe **what** a connector does (capabilities, auth method, data shape), never **how** claude.ai surfaces it. The MCP-via-claude.ai variant of a connector is one of multiple possible realisations of the same connector spec.
- `.scout-secrets/` is designed for direct programmatic access (file mode 600, `age`-encrypted optional, never embedded in tool I/O), not as a passthrough to claude.ai.
- Session prompts are *generated* by the renderer, not edited in place — the renderer is the ground truth across harnesses.

## Roadmap

| Version | Adds | Carries forward |
|---|---|---|
| **v0.4** (current spec) | Stable IDs (short-prefix surface form for markdown), mutations function-shaped as events, `watch` reframed as projection consumer; **`connectors.yaml` lifts the connector roster to a single source of truth** (Plan 4 — co-shipped with the hooks/scripts port); **WhatsApp inbound + Telegram outbound + Google Messages connectors land as official-tier additions** (Plan 4 same scope, MCP-server-based for WhatsApp, Bot API for Telegram, Chrome MCP for Google Messages) | All of unification |
| **v0.5** | SQLite event store (WAL); mutations *also* append to store (markdown still authoritative); first projection rebuilders + snapshot table; markdown watcher with origin-tagged events | Markdown stays canonical for action items; no external connectors yet beyond v0.4's official-tier additions |
| **v0.6** | Dispatcher with idempotency + retries + tombstone application; first **bidirectional connector (Linear)** — easiest: stable IDs, signed webhooks, REST API for outbound; event store becomes co-canonical with markdown; LWW conflict resolution + scout-app banner | Plan 6's scout-app reads from the projection cache |
| **v0.7** | Connector manifest schema v1 + generic builtin runners; **bidirectional Telegram connector** (upgrades v0.4's outbound-only stub with the inbound return-bridge — user replies become feedback signals); **Slack bidirectional**; **WhatsApp upgraded** to bidirectional (still inbound-only by user choice but architecturally bidirectional). **Multi-channel notification fan-out** (per-`(mode, tier)` channel routing). **Opportunistic KG-enrichment loop** ships with default 2-questions-per-run cap. Migrate v0.4's `connector-log` and `session-tokens` hooks to emit events | Markdown remains the editable surface |
| **v0.8** | **Connector tier model lands**: `scoutctl connectors discover` introspects user's existing claude.ai MCPs / local `.mcp.json` / Claude Code plugins → emits candidate list → `scoutctl connectors enable <name>` opts in. **Community-contributed connector tier** + sandbox isolation + capability-grant prompts. **Interactive escalation**: macOS `claude-scout://` URL scheme + queued-questions JSONL for offline users. `scoutctl connector build` skill: agent fills out manifest in chat, engine validates and starts | First user-authored and auto-discovered connectors land |
| **v0.9** | **Harness-independence prep**: extract LLM backend into a pluggable adapter (Anthropic SDK / Claude API / others); session runner owns its agent loop directly rather than shelling out to `claude` CLI; engine paths fully decouple from `~/.claude/projects/...`. Saga support for multi-step orchestration; per-field conflict resolution | Claude Code remains a supported harness alongside the new direct-MCP-client mode |
| **v0.10+** | **Scout as MCP client**: own MCP server lifecycles, credentials, connector authentication; claude.ai marketplace becomes one optional source among several; `claude-hud` and `scout.app` continue to work via the harness-agnostic engine | Long-term horizon — multi-year |

Roughly a year of evolution. Each step independently shippable.

## What v0.4 must commit to (the small price of admission)

The full architecture lands in v0.5+. v0.4 makes three commitments that keep the door open without expanding scope. These are detailed in §13 of the v0.4 unification spec; summary here for vision-doc readers:

### 1. Stable IDs on every mutable entity

Every action item, KB entry, hook log line, and session is assigned a ULID at creation time. **Storage form** is the full 26-character ULID. **Surface form for action items in markdown** is a 4-character Crockford base32 prefix in square brackets — `- [ ] [#A3F7] task title` — to avoid ULID-as-comment visual noise in Obsidian. The engine maintains the prefix↔ULID mapping in `.scout-state/id-map.json` (in v0.5+, in the SQLite store).

Prefix length is **fixed at 4 characters**; the engine never rewrites markdown in the background to extend a prefix (that would race the user's editor). Collision handling is additive: regenerate a fresh random prefix at creation time on collision; for user-introduced copy-paste duplicates, identify the original via title + position match and reassign a new prefix to the copy on the next user-initiated write. See v0.4 spec §13.1 for the full rule. If the user accidentally deletes a prefix, the diff engine fuzzy-matches by title + section position; if reattachment fails, the line is treated as new (never silently merged).

KB entries, JSONL log lines, and session records carry the full ULID directly — they're not edited in human-friendly markdown.

### 2. Mutations return event-shaped values

Functions that mutate persistent state return an `Event` dataclass alongside their existing side effect. The CLI ignores the return value in v0.4; tests assert on it. Hooks `emit(event)` instead of writing JSONL directly. In v0.5, `emit()` gains an event-store append behind the same interface.

### 3. `watch` and `kb refresh-summary` are projection-consumer contracts

Their CLI help text and spec wording describe them as *streams of changes,* not *file watchers.* The v0.4 implementation is still file-watcher-based (no event store yet), but the public contract admits transparent substitution in v0.5.

## What this is *not*

- **A real-time collaboration system.** Scout is single-user. Multi-user / true CRDT editing is out of scope.
- **A general workflow engine.** It's specialized to "events trigger LLM sessions and projections." If you need branching workflows with manual approval gates, use n8n or Inngest.
- **A replacement for Linear/Slack/GitHub.** Scout aggregates and reacts; it does not become the source of truth for any external system.
- **A hosted service.** Single-machine, single-user, file-based. The dispatcher and connectors are local processes. (A future hosted variant is conceivable but explicitly out of scope.)
- **Open-ended code generation.** Scout authors connectors against a constrained manifest schema. It does not write arbitrary daemons.
- **A high-throughput message bus.** SQLite WAL handles the personal-scale write rate (tens of events/sec at burst peak). Scaling to hundreds-of-events/sec sustained would require a proper queue (NATS, Redis Streams) and is out of scope.
- **Permanently harness-locked.** Scout v0.4–v0.8 runs on Claude Code, but v0.9+ targets harness independence (Scout as MCP client + pluggable LLM backend). Connector definitions describe **what** they do, not **how** claude.ai surfaces them. Engine code never imports Claude-Code-specific paths. The point is to keep the door open, not to plan a forced migration.
- **A complete replacement for direct API access.** Scout's connector tier model (official / auto-discovered / community) makes wiring up niche sources cheap, but it's not a goal to subsume every external API the user might ever touch. If you need real-time programmatic access to Stripe, Mixpanel, or your CRM with no Scout-style synthesis on top, write a script. Scout is for sources where reactive synthesis + KG integration earn their keep.

## Open questions

1. **Secret storage portability.** macOS Keychain is the obvious answer for v0.4-era. For colleague portability (Linux, headless), an opt-in encrypted file fallback (`age` or `sops`) may be needed. Defer to v0.5 design.
2. **Snapshot cadence and retention.** Snapshots solve replay speed; the open question is *how often* to snapshot and *how long* to retain raw events behind a snapshot. v0.6 problem.
3. **Per-field conflict resolution.** v0.5 ships LWW + visible banner. Per-field origin tracking ("Linear owns `state`, user owns `title`") is correct but complex. v0.9 problem.
4. **Backfill semantics.** When a new Linear connector is enabled, does it backfill historical events, or only stream from "now"? Probably user-configurable; default "now"; backfill is a separate `scoutctl connector backfill` command.
5. **Event versioning vocabulary.** Tombstones cover *correction*; the open question is the convention for *additive* schema change (new optional field) vs. *breaking* (renamed field). Likely a `v` field on each event payload + per-kind compatibility rules. v0.6–v0.7 problem.
6. **Knowledge-velocity throttle calibration.** The default opportunistic-questions budget is `2 per scheduled run`. Right value depends on session frequency × user engagement patterns. Users with 5 sessions/day and high engagement may tolerate `5/run`; users with 2 sessions/day may need `1/week`. v0.7 problem; needs telemetry on user-reply rates.
7. **Auto-discovery API surface (v0.8).** Is `scoutctl connectors discover` blocking on user approval for every candidate, or does it auto-enable safe-by-default connectors (e.g., the user's existing claude.ai Gmail/Calendar)? Probably manual-approve for v1; auto-enable opt-in via `connectors.yaml: auto_enable_discovered: true` later.
8. **Community connector trust model (v0.8).** Sandboxing approach (subprocess + user/group separation? Containerization?), signing (Sigstore?), allowlist, manual review per connector? Probably allowlist + manual review for v1; explore signing once contributors materialize.
9. **Interactive escalation deep-link reliability (v0.8).** macOS `claude-scout://` URL scheme requires an `LSHandlers` registration and a launcher binary. Verify the deep-link handler reliably preloads context across Claude Code versions — if Claude Code's session-resume API drifts, the launcher needs a layer of indirection (e.g., write context to a known cache path, fire `claude --resume <session>` with a context-loading prompt).
10. **Harness-independence transition path (v0.9+).** When the engine starts owning the agent loop, the per-mode runner scripts (`run-scout.sh`, `run-dreaming.sh`, etc.) need to either invoke the new direct-LLM mode or stay on `claude` CLI. Plan a *parallel-run period* where both work, then a flag-flip cutover.

Resolved by this revision (no longer open):

- ~~*Where do outbound connectors live?*~~ — Connectors are bidirectional. Outbound is a connector concern, not a separate component.
- ~~*Cross-process coordination on the JSONL log.*~~ — SQLite WAL replaces JSONL; concurrent writes are handled by the database.
- ~~*Schema evolution for events.*~~ — Tombstones are the v0.5 mechanism; details deferred to Open Question #5.

## Prior art

| System | Pattern stolen |
|---|---|
| **Home Assistant** | Connector manifest YAML; integration registry; capability declaration; user-mediated permission grants |
| **Beeper / Matrix bridges** | One bridge process per source; bidirectional isolation; common event vocabulary |
| **Inngest / Trigger.dev** | Durable event-driven LLM workflows; idempotency primitives; dead-letter retry |
| **n8n / Activepieces** | User-authored connector SDK; declarative-first; visual flow as optional layer |
| **Apache Camel / Spring Integration** | Enterprise integration patterns vocabulary (Hohpe & Woolf 2003) |
| **MCP (Model Context Protocol)** | Cleanest existing model for "agent authors a new external interface" |
| **TaskWarrior** | CLI-first task management with sync server (`taskd`) |
| **Logseq, org-roam** | Markdown-canonical with derived index — the pattern v0.4 keeps and v0.5 extends |
| **SQLite WAL + event-sourced apps (Datomic, EventStoreDB lite)** | Append-mostly store with tombstones; projection snapshots for replay speed |

## References

- v0.4 unification spec: [`./2026-04-24-scout-unification-design.md`](./2026-04-24-scout-unification-design.md), especially §13 ("Forward-compatibility commitments for v0.5+")
- Hohpe & Woolf, *Enterprise Integration Patterns* (2003) — the vocabulary
- Cockburn, *Hexagonal Architecture* (2005) — the boundary model
- Home Assistant developer docs on integrations and the event bus
- MCP specification: `modelcontextprotocol.io`
- Inngest concepts: durable functions, event-driven workflows, idempotency keys
- SQLite WAL mode docs: `sqlite.org/wal.html`

## Status notes

This is a *vision document*, not a binding spec. Implementation details (exact CLI surface, exact handler API, SQLite schema) will be finalized in version-specific specs (`2026-MM-DD-scout-event-architecture-v0.5-design.md` etc.) when each version is brainstormed in turn. The purpose of this document is to establish the destination, so v0.4 does not paint into a corner and so colleagues can evaluate whether this trajectory matches their needs.
