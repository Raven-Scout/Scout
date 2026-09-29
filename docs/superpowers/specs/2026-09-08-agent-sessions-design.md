# Agent Sessions — Design

**Date:** 2026-09-08
**Status:** Proposed (for review)
**Surfaces:** scout-plugin engine (`scoutctl session …`), scout-plugin phases
(`phases/connectors/claude-sessions.md`), Scout.app (new **Sessions** sidebar page)
**Supersedes:** `docs/feature-roadmap.md` F-2 (Sessions page). Closes sub-tasks (3) and
(4) of the vault wishlist item *Scout-on-Scout development work must be visible*
(2026-05-07).

## 1. Context

Jordan runs many Claude Code agent sessions in parallel — on 2026-09-08 the desktop
app listed ~40 unarchived sessions across nine sidebar groups (the three Scout
repos, five work projects, and Archived),
roughly half of them linked to a GitHub PR. The desktop sidebar groups them by
project but says nothing about *state*: which are running, which are blocked on a
review or a question, which have been abandoned with a dirty worktree, which are
merged and could be archived.

Scout's awareness of these sessions is thin today:

- `scoutctl session cc-cache` (engine `scout/scripts/cc_session_cache.py`) walks
  `~/.claude/projects/*/*.jsonl` modified in the last 24 h and writes
  `.scout-cache/cc-sessions.md` — first prompt + up to 10 files touched per session,
  Scout's own sessions excluded. Consolidation Phase 1f reads it; the multi-session
  hard rule (≥3 sessions in one repo ⇒ must reach the action items) hangs off it.
- `phases/connectors/claude-sessions.md` still tells the model that "desktop-app …
  sessions are NOT locally accessible". That is no longer true (see §2).
- Scout.app's Control Center shows only Scout's *own* scheduled runs. Its
  `ClaudeSessionService` parses the JSONL of those runs for the Tools/Files tabs and
  nothing else.

### What Claude Code already writes to disk (verified 2026-09-08)

Scout never needs to talk to Claude Code. Both the CLI and the desktop app persist
everything the feature needs:

| Source | Path | Gives |
|---|---|---|
| Desktop per-session record | `~/Library/Application Support/Claude/claude-code-sessions/<org>/<user>/local_<uuid>.json` (196 files; `deleted_<uuid>` tombstones alongside) | `title`, `titleSource`, `cwd`, `originCwd`, `worktreePath`/`worktreeName`, `branch`, `sourceBranch`, `writtenBranches`, `createdAt`/`lastActivityAt`/`lastFocusedAt` (epoch ms), `model`, `effort`, `isArchived`, `completedTurns`, `prs[]` (`prNumber`, `repo`, `url`), legacy `prNumber`/`prState`, `spawnedFrom{sessionId,taskId}`, `forkedFromSessionId`, `scheduledTaskId`, `keptDirtyWorktree`, `transcriptUnavailable`, `cliSessionId` |
| Desktop group names + membership | `~/Library/Application Support/Claude/claude_desktop_config.json` → `preferences.epitaxyPrefs["dframe-group-scopes"]["<org>/<user>"]` → `groups[{id,name}]`, `assignments{"code:<sessionId>": <groupId>}` | project grouping as the user arranged it |
| Desktop worktree registry | `~/Library/Application Support/Claude/git-worktrees.json` → `worktrees{name → path, baseRepo, branch, leasedBy}` | which session leases which worktree |
| Live processes | `~/.claude/sessions/<pid>.json` → `pid`, `sessionId` (CLI uuid), `cwd`, `startedAt`, `entrypoint` | present only while the process is alive |
| Transcripts | `~/.claude/projects/<encoded-cwd>/<cliSessionId>.jsonl` | first prompt, files touched, tool calls, shape of the last turn |
| PR review state | `gh pr view <n> --repo <r> --json state,isDraft,reviewDecision,reviewRequests,statusCheckRollup,mergeStateStatus,updatedAt,url` | review + CI state (local `gh`, `repo` scope) |

Two behaviours make this live rather than a nightly snapshot: the desktop app
rewrites a session's JSON (`lastActivityAt`) on every turn, and PID files appear on
start and vanish on exit. The app is not sandboxed (`ENABLE_APP_SANDBOX = NO`) and
its `FileWatcher` accepts any directory, so it can watch all of the above.

## 2. Goals and non-goals

### Goals

1. **One shared index.** The engine produces a single machine-readable picture of
   every local Claude Code session — desktop and CLI — with a derived *state* and
   the reasons for it. App, scheduled runs, and humans all read the same file.
2. **A Sessions page in Scout.app** organised by *project × state*: project swimlanes
   with cards sorted by severity, a Now strip for needs-you and running, a table
   view, and a detail pane with resume / PR / worktree actions.
3. **Smarter briefings.** Consolidation and briefing consume a state-first digest so
   "changes requested on your agent's PR" or "four stale worktrees" reach the action
   items with evidence.
4. **Fix the stale claim** in the connector phase that desktop sessions are invisible.

### Non-goals

- Writing to the desktop app's store (archiving, renaming, grouping). Read-only.
- Resuming an existing session *inside* Claude Desktop. The documented deep link
  (`claude://code/new`) only creates new sessions
  (`docs/superpowers/specs/2026-08-05-claude-desktop-code-launch-design.md`).
- Embedded terminal (F-1), session ↔ action-item linkage (F-4), live-attach
  (issue #30). The index's stable ids make F-4 cheap later; not built here.
- claude.ai web / cloud sessions. No local artefacts exist for them.
- Cost accounting per session. `session-tokens.jsonl` covers Scout's own runs only.

## 3. Architecture

```
  Claude Code CLI ─┐ writes ┌─ ~/.claude/projects/**/*.jsonl      (transcripts)
                   ├───────►├─ ~/.claude/sessions/<pid>.json      (live processes)
  Claude Desktop ──┘        ├─ …/Claude/claude-code-sessions/**   (session records)
                            ├─ …/Claude/claude_desktop_config.json (groups)
                            └─ …/Claude/git-worktrees.json        (leases)
                                          │  read-only
                                          ▼
                       scoutctl session index [--json] [--render] [--no-gh]
                          (engine: scout/sessions/…)      ◄── gh pr view (TTL cache)
                                          │
                     ┌────────────────────┼─────────────────────┐
                     ▼                    ▼                     ▼
   .scout-cache/sessions-index.json   .scout-cache/cc-sessions.md   stdout --json
   (machine view, schema v1)          (LLM digest, same filename    (app / humans)
                                       as today)
                     │                    │
        Scout.app SessionIndexService   consolidation / briefing / dreaming
        (FSEvents on the sources +      via the existing pre-session hook
         debounced scoutctl refresh)
```

**Ownership.** The engine owns parsing, merging and state derivation. The app is a
renderer plus a trigger. Phase docs consume the digest and never parse JSONL.

**Refresh triggers.**

| Trigger | Command | Cadence |
|---|---|---|
| App: FSEvents on the four source dirs + `.scout-cache/` | `session index --json --no-gh` | debounced 2 s |
| App: PR refresh timer (while Sessions tab visible) | `session index --json` | every `pr_refresh_minutes` (10) and once on tab appear |
| Scheduled run pre-session hook (`scripts/cc-session-cache.sh` → `scoutctl session cc-cache`) | `session index --render --hours 24` | every run |
| App: sidebar badge while the Sessions tab is hidden | `session index --json --no-gh` | every 5 min |
| Human | `scoutctl session list` / `session index --json` | on demand |

No Claude Code hooks, MCP calls or APIs are required. A user-level Claude Code
`Stop` hook that nudges the index is a possible later refinement, not in scope.

## 4. Engine: `scout/sessions/`

New package (not a single script) because it has several independent loaders:

```
engine/scout/sessions/
  __init__.py
  model.py        AgentSession, Project, PRInfo, TranscriptInfo, WorktreeInfo, Index (dataclasses + to_json)
  desktop.py      load_desktop_records(), load_groups(), load_worktree_leases()
  cli_home.py     load_live_pids(), iter_transcripts()  (~/.claude)
  transcript.py   parse_transcript() → first_prompt, files_touched, tool_calls, last_turn  (moves the
                  extractors out of scripts/cc_session_cache.py; that module becomes a thin alias)
  github.py       refresh_pr_states() with the TTL cache
  derive.py       resolve_project(), derive_state(), is_scout_run()
  index.py        build_index() orchestrator; atomic write; source_errors
  render.py       render_digest() → cc-sessions.md
```

### 4.1 Identity and merge

- `id` = desktop `sessionId` (`local_…`) when a desktop record exists, else
  `cli:<transcript uuid>`.
- `cli_session_id` = desktop `cliSessionId` or the transcript uuid. Transcript lookup
  is by uuid across all `~/.claude/projects/*/`; the desktop `cwd` is authoritative
  for the path (the encoded dir name is lossy: Claude Code replaces every
  non-alphanumeric character with `-`, so CLI-only transcripts are matched by
  encoding known real paths — the vault, every desktop `cwd`/`originCwd` — the
  same way before falling back to a best-effort decode).
- Desktop records sharing a `cliSessionId` (forks, `priorCliSessionIds`) dedupe to
  the one with the latest `lastActivityAt`.
- Tombstones (`deleted_*`) are skipped. Records with `transcriptUnavailable` get
  `transcript: null`.
- CLI-only sessions (transcript, no desktop record) are included; they have no
  title (first prompt is used), no PRs, no group.

### 4.2 Project resolution

`project.key` = repo root: `git -C <origin_cwd> rev-parse --show-toplevel` if it
succeeds within 2 s (memoised per `origin_cwd`, so a dozen git spawns rather than
one per session), else `origin_cwd` with a trailing `/.claude/worktrees/<name>`
stripped, else `origin_cwd`. `project.name` = the desktop group name assigned to the
session when one exists, else the basename of the key. If sessions of one repo are
split across groups, the most common group name wins and the others are kept per
session as `group_name`. A group literally named `Archived` marks its sessions
`is_archived = true`.

### 4.3 Scout's own runs

`is_scout_run` is true when `origin_cwd` is the vault (`paths.data_dir()`) **and**
either the title / transcript `customTitle` matches `^scout-[a-z-]+-\d{8}-\d{4}$`
or `scheduledTaskId` starts with `scout-`. Interactive sessions opened in the vault
are *not* Scout runs. The digest excludes Scout runs (as today); the index keeps
them flagged so the app can offer a toggle.

### 4.4 Liveness

- `is_open` = a `~/.claude/sessions/<pid>.json` names this `cli_session_id` **and**
  `os.kill(pid, 0)` does not raise `ProcessLookupError` (`PermissionError` counts as
  alive). Stale PID files are ignored.
- `last_activity_at` = max(desktop `lastActivityAt`, transcript mtime).
- **Running** = `is_open` and `now − last_activity_at ≤ running_window_seconds`
  (120). Open but idle sessions are *not* running; the app shows them as open.

### 4.5 Transcript facts (`transcript.py`)

Deep-parsed only when `last_activity_at` is within `transcript_window_days` (14)
and the file's `mtime_ns` changed since the cache. Otherwise the cached entry is
reused, or the block is `null`.

- `first_prompt` — existing extractor (first 50 lines, 500 chars).
- `files_touched` — existing extractor (noise filter, `~` collapse, cap 10).
- `tool_calls` — count of `tool_use` blocks.
- `last_turn` — from the last ~40 lines: `{at, kind}` where `kind ∈
  end_turn | tool_use | question | unknown`. `question` when the final assistant
  message contains an `AskUserQuestion` tool_use with no later `tool_result`, or its
  last text block ends with `?` (heuristic; surfaced as reason
  "ended on a question").

Cache file: `.scout-cache/sessions-transcripts.cache.json` (replaces
`cc-sessions.cache.json`, same mtime-keyed pattern).

### 4.6 PR state (`github.py`)

For each session PR (from `prs[]`, falling back to legacy `prNumber`/`prRepository`)
not already terminal (`MERGED`/`CLOSED`), and only when `use_gh` and `gh` is on PATH:

```
gh pr view <number> --repo <repo> --json state,isDraft,reviewDecision,reviewRequests,statusCheckRollup,mergeStateStatus,updatedAt,url
```

Sequential, 10 s timeout each, at most `pr_fetch_cap` (25) fetches per run, oldest
`fetched_at` first. Cache `.scout-cache/sessions-pr.cache.json` keyed `repo#number`
with `fetched_at`; entries younger than `pr_refresh_minutes` are not refetched.
On failure the cached value stands with `stale: true`; with no cache, `state:
"unknown"`.

`state` and `merge_state` are upper-case GitHub values, except that a missing value
and GitHub's own not-yet-computed `UNKNOWN` are both normalised to lowercase
`"unknown"` — one sentinel for the app to decode.

Derived fields: `checks ∈ passing | failing | pending | none` (failing if any
conclusion is `FAILURE`/`ERROR`/`TIMED_OUT`; pending if any status ≠ `COMPLETED`;
none if empty), `review_requested` = `reviewRequests` non-empty or `reviewDecision ==
REVIEW_REQUIRED`, `merge_state` from `mergeStateStatus` (`DIRTY` ⇒ conflict). A
session with several PRs uses the most recently updated *open* one for state.

### 4.7 State derivation (`derive.py`) — first match wins

| # | Rule | State | Reason strings (examples) |
|---|---|---|---|
| 1 | `is_archived` (desktop flag or Archived group) | `done` | `archived` |
| 2 | running (§4.4) | `running` | `live pid 36808`, `active 40s ago` |
| 3 | PR `MERGED` or `CLOSED` | `done` | `PR #98 merged`, `PR #47 closed` |
| 4 | PR `reviewDecision == CHANGES_REQUESTED`, or `checks == failing`, or `merge_state` conflict, or `last_turn.kind == question` **while fresh** (idle ≤ `stale_after_days`; an older question becomes a rule-6 signal instead); **or** PR open, not draft, `checks ∈ {passing, none}`, `merge_state == CLEAN` and (`reviewDecision == APPROVED` or no review requested) — *ready to merge* | `needs_you` | `changes requested on PR #98`, `CI failing`, `merge conflict`, `ended on a question`, `PR #102 ready to merge` |
| 5 | PR open, not draft, and (review requested / required and not approved, or `checks == pending`) | `waiting` | `PR #98 awaiting review 5d`, `checks pending` |
| — | Draft PRs match neither 4 nor 5 and fall through to 6/7 with reason `draft PR #n` | | |
| 6 | `now − last_activity_at > stale_after_days` (3), or `keptDirtyWorktree` and idle > `stale_after_days` | `stale` | `idle 4d`, `dirty worktree, idle 6d` |
| 7 | otherwise | `parked` | `open, idle 12m` / `last active 2h ago` |

`state_reasons` always lists every matched signal (not just the deciding one) so
the detail pane and the digest can show, e.g., both `CI failing` and `awaiting
review`.

### 4.8 Index schema (`sessions-index.json`, `schema_version: 1`)

```json
{
  "schema_version": 1,
  "generated_at": "2026-09-08T19:19:03Z",
  "source_counts": {"desktop": 196, "cli_only": 12, "open": 4, "running": 1, "prs_refreshed": 3},
  "source_errors": [{"source": "gh", "message": "timeout after 10s: example-org/example-repo#102"}],
  "display": {"done_visible_hours": 24, "stale_after_days": 3},
  "projects": [
    {"key": "/Users/alex/code/example-repo", "name": "Example Repo", "group_id": "cg-…",
     "counts": {"running": 1, "needs_you": 0, "waiting": 2, "parked": 3, "stale": 1, "done": 4}}
  ],
  "sessions": [
    {"id": "local_…", "cli_session_id": "7ea0…", "title": "Fix the parser",
     "title_source": "auto", "project_key": "/Users/alex/code/example-repo", "group_name": "Example Repo",
     "cwd": "…/.claude/worktrees/compassionate-cerf-cbb3d3", "origin_cwd": "/Users/alex/code/example-repo",
     "worktree": {"path": "…", "name": "compassionate-cerf-cbb3d3", "branch": "claude/…",
                  "source_branch": "main", "dirty": false},
     "created_at": "…Z", "last_activity_at": "…Z", "model": "claude-opus-5", "effort": "xhigh",
     "turns": 19, "is_archived": false, "is_open": false, "is_scout_run": false,
     "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
     "pr": {"number": 98, "repo": "example-org/example-repo", "url": "https://github.com/…/pull/98",
            "state": "OPEN", "is_draft": false, "review_decision": "CHANGES_REQUESTED",
            "review_requested": false, "checks": "passing", "merge_state": "CLEAN",
            "fetched_at": "…Z", "stale": false, "updated_at": "…Z"},
     "prs": [ /* every linked PR, same shape as "pr" */ ],
     "transcript": {"path": "~/.claude/projects/…/7ea0….jsonl", "first_prompt": "Please fix the parser…",
                    "files_touched": ["~/code/example-repo/cli.py"], "tool_calls": 212,
                    "last_turn": {"at": "…Z", "kind": "end_turn"}, "mtime_ns": 1788800000000000000},
     "state": "needs_you", "state_reasons": ["changes requested on PR #98"]}
  ]
}
```

Amended during plan 1: `prs` (all linked PRs; `pr` is the one chosen per §4.6),
`pr.updated_at` (drives the "awaiting review Nd" age) and `transcript.mtime_ns`
(the cache key) are part of schema v1; a contract test in the engine locks the key
sets. `state_reasons` lists the deciding rule's reasons first, then every other
matched rule-4/5/6 signal (§4.7); `done` lists only its deciding reason.

Timestamps are ISO-8601 UTC; rendering to the configured zone happens at display
time (`scout.config.resolve_timezone` — never bare local time).
`worktree.dirty` is the desktop `keptDirtyWorktree` flag; a live `git status` sweep
is deliberately not run (50 worktrees × git is too slow for a 2 s debounce).

### 4.9 Digest (`cc-sessions.md`, `render.py`)

Same filename so assembled SKILL.md / DREAMING.md references keep working. New shape:

```
# Claude Code Sessions — state digest
Generated 2026-09-08 15:19 EDT · 38 sessions · 1 running · 2 need you · 6 waiting · 5 stale

## Needs you (2)
- **Fix the parser** — Example Repo — changes requested on PR #98 — <url>
…
## Running now (1)
## Waiting on others (6)
## Stale (5)
## Activity — last 24h, by project        ← today's list, unchanged format
### Example Repo
#### Session: … first prompt … files touched …
```

Buckets are capped at `render_max_per_bucket` (15), most recent first; Scout runs
and archived sessions are excluded; the activity section keeps the `--hours` window.

### 4.10 CLI

```
scoutctl session index [--json] [--render] [--no-gh] [--hours 24]
                       [--instance-name Scout] [--timezone TZ] [--strict]
scoutctl session list  [--state S ...] [--project P] [--include-archived]
                       [--include-scout-runs] [--json]
scoutctl session cc-cache …            # unchanged flags; now = index --render (back-compat alias)
```

Exit codes: `0` success including partial data, `1` output could not be written,
`2` bad arguments. `--strict` turns any `source_error` into exit `1` (tests, CI).
`index` always writes `sessions-index.json`; `--render` also writes the digest;
`--json` prints the index to stdout.

### 4.11 Configuration (`scout-config.yaml`)

The vault's existing `sessions:` key belongs to budget/session-limit settings, so
this feature uses a new top-level block, with packaged defaults in
`engine/scout/defaults/`:

```yaml
agent_sessions:
  stale_after_days: 3
  running_window_seconds: 120
  pr_refresh_minutes: 10
  pr_fetch_cap: 25
  transcript_window_days: 14
  done_visible_hours: 24        # app: hide done sessions older than this
  render_max_per_bucket: 15
  use_gh: true
  desktop_support_dir: null     # override ~/Library/Application Support/Claude (tests)
  claude_home: null             # override ~/.claude (tests)
```

Display-only values (`done_visible_hours`, `stale_after_days`) are echoed into the
index under `display`, so the app never parses the vault YAML.

### 4.12 Failure handling

- Each loader is independent and returns `(data, errors)`. Missing directories
  (no desktop app, no `~/.claude/sessions`) are *not* errors; unreadable or
  malformed files are recorded in `source_errors` and skipped.
- The index and both caches are written via temp file + `os.replace`.
- JSONL parsing tolerates malformed lines (existing pattern).
- `gh` absent ⇒ every PR `state: "unknown"`, one `source_error` `gh: not found`.
- Performance budget: warm run (nothing changed) < 1 s; cold run without `gh` < 5 s
  for 200 desktop records + 450 transcripts; `gh` adds ≤ 25 × 10 s worst case,
  typically < 5 s. Measured in `engine/tests/perf/`.

## 5. Phases (scout-plugin)

`phases/connectors/claude-sessions.md` (assembled into the vault SKILL.md for
consolidation; verified present in `~/Scout/.scout-state/last-assembled/SKILL.md`):

1. Replace **Find Recent Sessions** / **Scan Session History** (the `find … -mtime`
   and `history.jsonl` snippets) with **Read the digest**: `cat
   {{SCOUT_DIR}}/.scout-cache/cc-sessions.md`; fallback `scoutctl session list`.
   Never parse JSONL from inside a run.
2. Rewrite **Remote-Session Limitation**: desktop-app sessions *are* local and
   indexed (title, PR, state); only claude.ai web / cloud sessions are invisible.
3. Add **Agent-session state → action items**:
   - `needs_you` with a PR reason ⇒ 🔴 item citing the PR and the reason
     (`changes requested`, `CI failing`, `merge conflict`).
   - `needs_you` with `ready to merge` ⇒ 🟡 item "merge PR #n (or request review)"
     citing the checks state.
   - `needs_you` with `ended on a question` ⇒ 🟡 item "answer the agent's question"
     with the session title and project.
   - `waiting` older than 48 h ⇒ 🟡 nudge item (who is being waited on, from
     `reviewRequests`).
   - `stale` ⇒ **one** 🟢 roll-up item "N stale agent worktrees (repo list)", never
     one item per session.
   - `running` ⇒ narrative only, never an action item.
   Keep the existing Hard Gate (≥3 sessions per repo), Claim Gate, Uncommitted
   Working-Tree Sweep and Scout-on-Scout narration; point them at the digest.
4. `phases/modes/kb-deep-work.md` cache table row and the README "Pre-Session Hooks"
   row describe the state digest.
5. Vault wishlist `docs/wishlist/2026-05-07-scout-on-scout-development-work-must-be-visible.md`:
   tick sub-tasks (3) and (4) with a pointer to this spec (small manual edit in the
   vault, committed as `scout: …`).

The vault's SKILL.md is regenerated through the existing `/scout-update` →
`scoutctl phases` back-port path; Jordan's self-edited copy is not hand-patched.

## 6. Scout.app: Sessions page

### 6.1 Navigation and layout

- `SidebarItem.sessions` (label **Sessions**, symbol `rectangle.stack.badge.person.crop`,
  status label `sessions`), placed after Control Center. Badge = needs-you count.
- Master/detail like Schedules: master pane with header + content, 380 pt detail pane.
- Header (`SessionsHeader`): view toggle **Board | Table** (`EditorialSegmentedControl`,
  persisted with `@SceneStorage`), state chips with live counts (tap to filter,
  multi-select), project menu, search field, options menu with *Show Scout's own
  runs* and *Show recently done* (default off; done sessions older than
  `done_visible_hours` are hidden even when on).

### 6.2 Board view (primary)

- **Now strip** at the top: needs-you cards then running cards across all projects;
  hidden when both are empty.
- **Swimlanes**: one row per project, sorted by most recent activity. Row header:
  project name (serif), per-state counts as tiny pills, a collapsed *Done (n)*
  affordance at the row's end that expands inline. Cards flow left-to-right sorted
  by severity: needs_you → running → waiting → parked (open first) → stale.
- **Card** (`SessionCardView`, styled after `BoardCardView`): 3 pt state ring on the
  leading edge (colour per state, below), title (serif 13.5, 2 lines), one reason
  line (sans 11, ink p3), footer chips: PR `#98 · changes requested · ✓/✗ checks`,
  branch (mono), relative last-active, model tag. Sub-agent cards show a small
  `↳ parent title` chip; hovering highlights the parent card when visible.
- State colours reuse `DS`: needs_you → `Priority.urgent`, running → `Status.ok`,
  waiting → `Priority.todo`, parked → `Ink.p4` (open: `Ink.p3`), stale →
  `Priority.done` at 60 % opacity, done → `Priority.done`.

### 6.3 Table view

`Table` with columns: state dot, title, project, reason, PR (number + review +
checks), last active, model, turns. Default sort severity then recency; every
column sortable. Shares selection and filters with the board. This is also the
accessible path (VoiceOver rows).

### 6.4 Detail pane (`SessionDetailView`)

Header: title, project chip, state pill + all reasons, branch / worktree name,
model · effort, created, last active, turns, open/closed indicator.

Tabs: **Summary** (first prompt, PR card with review decision / checks / merge state
/ link, state reasons), **Files** and **Tools** (reuse `FilesTab` / `ToolsTab` via a
small adapter from `TranscriptInfo`), **Tree** (parent and children by
`parent_session_id`, clickable).

Actions (toolbar):

| Action | Mechanism |
|---|---|
| Resume in terminal | `ClaudeLauncher` CLI path with a new `extraArgs: ["--resume", cli_session_id]`, cwd = session `cwd` (falls back to `origin_cwd` if the worktree is gone) |
| Open PR | `NSWorkspace.open(pr.url)` |
| Reveal worktree | `NSWorkspace.activateFileViewerSelecting` |
| Copy handoff | Markdown: title, project, state + reasons, PR, branch, first prompt, files touched (reuses the copy-format vocabulary from action items) |

No archive / rename / group actions (non-goal). Empty detail state shows aggregate
counts and `generated_at`.

### 6.5 Service and models

```
Scout/Sessions/Models/AgentSession.swift      AgentSession, AgentSessionState (severity order), PRInfo,
                                              TranscriptInfo, WorktreeInfo  — Codable, Sendable
Scout/Sessions/Models/SessionIndex.swift      SessionIndex, SessionProject, schema-version check
Scout/Sessions/Models/SessionsLayout.swift    pure: rows(projects×sessions, filters) → [SessionRow],
                                              nowStrip(), counts(), severity sort
Scout/Sessions/SessionIndexService.swift      @MainActor ObservableObject
Scout/Sessions/Views/SessionsView.swift       master/detail shell
Scout/Sessions/Views/SessionsHeader.swift
Scout/Sessions/Views/SessionsBoardView.swift  Now strip + swimlanes
Scout/Sessions/Views/SessionCardView.swift
Scout/Sessions/Views/SessionsTableView.swift
Scout/Sessions/Views/SessionDetailView.swift  (+ TreeTab)
Scout/Sessions/Views/SessionStatePill.swift
Scout/Shell/MainWindowView.swift              SidebarItem.sessions + routing
Scout/Shell/SidebarView.swift                 row + badge
Scout/Shell/AppState.swift                    wiring; Configuration gains the watch roots
Scout/Utilities/ClaudeLauncher.swift          extraArgs for --resume
```

`SessionIndexService`:

- Inputs: `scoutctl` invocation (executable + args prefix, as `ScheduleService`),
  `ProcessRunner`, `FileSystemEventSource`, index file URL, watch roots (desktop
  sessions dir, `~/.claude/sessions`, `~/.claude/projects`, vault `.scout-cache`),
  `ClockSource`.
- Publishes `index: SessionIndex?`, `lastError: String?`, `isRefreshing`,
  `generatedAt`.
- On `start()`: load the index file if present, then run a full refresh (with `gh`).
  Subscribe to each existing watch root through `DebouncedFileEvents` (2 s); on an
  event run `session index --json --no-gh`. Timer every `pr_refresh_minutes` runs
  with `gh`. `stop()` on tab disappear; the Control Center badge still needs the
  count, so the service keeps a low-rate (5 min) refresh while the tab is hidden.
- If the process fails, re-read the index file and surface `lastError` (same
  message shaping as `ScheduleService.formatRunnerError`). If `schema_version` is
  unsupported, show a banner asking to update scout-plugin and keep the last good
  index.
- Missing watch roots are skipped silently (e.g., no desktop app installed).

### 6.6 Optional later view: the world (stretch, separate plan)

Kept as a third **World** toggle built on the same `SessionsLayout` rows. A SpriteKit
`SKScene` in a `SpriteView`, drawn in `DS` paper/ink tokens: one room per row with
desks, a review board, a door and a couch; sessions as small ink figures whose
behaviour encodes state (running: typing at a desk; open idle: seated still;
needs_you: at the door with a `!` bubble; waiting: at the board holding a PR tag;
parked: slow wander; stale: dozing on the couch, faded; done: exits through the
door). Sub-agents trail their parent. Reconciliation by session id on each refresh,
pan/zoom, click-to-select shared with the other views, scene paused when hidden.
Layout/behaviour mapping lives in pure types (`WorldLayout`, `AgentBehavior`);
the scene is a thin renderer. Nothing in §6.1–6.5 depends on it; it ships only if
still wanted after plan 3.

## 7. Testing

### Engine (`engine/tests/unit/test_sessions_*.py`, pytest)

Fixtures under `engine/tests/fixtures/sessions/` following the repo's anonymisation
rules (people `Alex`/`Priya`/`Sam`, repos `example-org/<repo>`, no real Linear
prefixes or Slack ids): a fake Application Support tree (5 `local_*.json` covering
PR-linked, spawned, archived, legacy `prNumber`, `transcriptUnavailable`; one
`deleted_*`; a `claude_desktop_config.json` with two groups and assignments; a
`git-worktrees.json`), a fake `~/.claude` (two PID files, one pointing at a dead
pid; six transcripts including one ending on `AskUserQuestion`, one ending with
`?`, one Scout run with `customTitle`), and a stub `gh` (monkeypatched `_run`
returning canned JSON, plus a timeout case).

- `test_sessions_desktop.py` — record/group/lease loading, tombstones, legacy PR fields.
- `test_sessions_cli_home.py` — PID liveness incl. dead pid; transcript discovery.
- `test_sessions_transcript.py` — first prompt, files, tool count, `last_turn` kinds; cache reuse on unchanged mtime.
- `test_sessions_github.py` — checks/review/merge derivation, TTL cache, terminal states never refetched, cap, failure ⇒ stale.
- `test_sessions_derive.py` — table-driven: every rule, precedence, open-vs-running, stale thresholds, multi-PR choice, `is_scout_run`, project naming (group vs basename, worktree stripping, Archived group).
- `test_sessions_index.py` — merge/dedupe, `source_errors`, atomic write, `--strict`.
- `test_sessions_render.py` — golden `cc-sessions.md`.
- `test_cli_session_subapp.py` — `index --json/--render/--no-gh`, `list` filters, `cc-cache` alias still writes the digest.
- `test_hermeticity.py` extended: no network, no real home touched.
- `engine/tests/perf/test_sessions_index_perf.py` — synthetic 200 records / 450 transcripts under budget.

### App (`ScoutTests/Sessions/`, Swift Testing)

- `SessionIndexDecodingTests` — decode `ScoutTests/Fixtures/sessions-index.fixture.json`
  (generated from the engine fixtures by `scoutctl session index --json`, anonymised);
  tolerant of unknown fields; rejects unsupported `schema_version`.
- `SessionsLayoutTests` — row grouping, severity sort, open-before-closed within
  parked, filters, Now strip contents, done hiding by `done_visible_hours`.
- `SessionIndexServiceTests` — with the existing fake `ProcessRunner` and
  `FileSystemEventSource` doubles: refresh on event (debounced), `--no-gh` vs `gh`
  arguments, fallback to file on failure, `lastError` shaping, schema banner.
- `ClaudeLauncherTests` — `--resume` argument rendering for each terminal path.
- View smoke tests for board, table, header, detail (pattern from
  `test: cover the logic layer and add view smoke tests`); the CI coverage floor
  holds because logic lives in pure types.

### Manual acceptance

1. `scoutctl session index --json | jq '.source_counts, [.sessions[] | select(.state=="running")]'`
   on this machine shows the currently open sessions with `is_open: true`.
2. Open the Sessions tab: the four live sessions appear, the one actively working
   shows *running*; PR chips match GitHub for three spot-checked PRs.
3. Start a new desktop session: its card appears within ~5 s; end it: `is_open`
   drops within ~5 s.
4. Run a consolidation: the digest's *Needs you* items reach the action items with
   PR links.

## 8. Sequencing

| Plan | Repo | Deliverable | Depends on |
|---|---|---|---|
| 1 · Engine index | scout-plugin | `scout/sessions/`, CLI, render, config defaults, tests; `cc-cache` alias; release | — |
| 2 · Phases + docs | scout-plugin, vault | phase edits (§5), README row, wishlist tick; `/scout-update` regenerates vault SKILL.md | 1 |
| 1b · Index performance | scout-plugin | mtime-keyed cache of desktop records with a last-good fallback for mid-write reads; pure-Python git toplevel; realistic (~700 KB/record) perf fixture; skip rewriting unchanged caches — plan 1 measured cold 5.5 s / warm 1.4 s on a 369-session machine vs the §4.12 budget. Design and plan: `docs/superpowers/specs/2026-09-28-agent-sessions-index-speed-design.md` ([#114](https://github.com/Raven-Scout/Scout/pull/114)) | 1; must land before 3's 2 s debounce |
| 3 · App Sessions page | scout-app | §6.1–6.5, fixture, tests, roadmap pointer; ignore the engine's own writes under `.scout-cache/` so the watch does not refresh itself | 1, 1b (schema); UI can start on the fixture in parallel |
| 4 · World view (optional) | scout-app | §6.6 | 3, and Jordan still wants it |

Plans live at `docs/superpowers/plans/2026-09-08-agent-sessions-plan-<n>-….md`.

## 9. Decisions recorded during design

- Shared engine index first; app and phases both consume it (Jordan, 2026-09-08).
- Primary organisation is project × state (swimlanes + severity), not a game world;
  the SpriteKit world is a secondary, optional later view (Jordan: "maybe too
  cartoonish … secondary goal").
- Scout never writes to the desktop app's store; archiving stays in Claude Desktop.
- Config block is `agent_sessions`, because `sessions` is already the budget block.
- The digest keeps the `cc-sessions.md` filename for back-compat with assembled skills.
- "Open" (process alive) and "running" (active in the last 2 min) are distinct.
- No live `git status` sweep across worktrees; `keptDirtyWorktree` is the dirty signal.
- An open, mergeable PR with no pending review is *needs you* (ready to merge), not
  *waiting*; draft PRs are parked/stale, never waiting.
- A session that ended on a question is *needs you* only while fresh (idle ≤
  `stale_after_days`); after that the question is listed as a stale signal
  (decided after plan 1's real-machine run, where 13 of 16 question-driven
  needs-you sessions were a week old).
