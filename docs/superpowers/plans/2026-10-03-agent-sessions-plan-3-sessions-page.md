# Agent Sessions 3 — Sessions Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a **Sessions** page to Scout.app that shows every local Claude Code session, from the desktop app and the CLI, by project and state. It stays current from the engine's session index and has a detail pane to resume a session, open its PR, reveal its worktree or copy a handoff.

**Architecture:**
- **The engine owns parsing and state.** The app runs `scoutctl session index --json` and renders what it prints. It never reads a transcript or the desktop app's store.
- **`SessionIndexService` runs two single-flight lanes** (spec §10.1):
  - a fast `--no-gh` build on file events, on a heartbeat and after each PR build;
  - a `gh` build every 2 minutes in the background, whose own output is never shown.

  It decodes off the main actor and republishes only when the content changed.
- **Pure `nonisolated` types hold every rule:**
  - `SessionIndex`: decoding, the schema check and skipping unreadable sessions.
  - `SessionsLayout`: filters, swimlanes, the Now strip, chip counts and table rows.
  - `SessionsRefresh`: cadence, arguments, watch roots and the filter for the engine's own writes.
  - `SessionsFormat` and `SessionHandoff`: text.

  The views stay thin.
- **The page follows the Schedules master/detail shape.** A header, then the board or the table, beside a 380 pt detail pane. `SidebarItem.sessions` sits after Control Center with a needs-you badge.
- **Part B is a small, independent scout-plugin fix.** `gh` fetches PRs only for sessions that are not archived.

**Tech Stack:**
- The app: Swift 5 language mode (`SWIFT_VERSION = 5.0`, approachable concurrency, `SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor` in the app target), SwiftUI on macOS 15.7, and Swift Testing (`import Testing`).
- Part B: Python ≥ 3.11 with pytest, ruff and mypy.

**Spec:** `docs/superpowers/specs/2026-09-08-agent-sessions-design.md`.
- §6 covers the page, and §3 and §4.8 the data.
- §10 is the addendum this plan adds. It records the PR-refresh decision and the other calls below.
- `docs/superpowers/specs/2026-09-28-agent-sessions-index-speed-design.md` §2 adds one requirement: the app ignores the engine's own writes under `.scout-cache/`.
- The JSON contract the app decodes is scout-plugin's `engine/tests/unit/test_sessions_index.py::test_index_json_contract_key_sets`.

Where the code lives:
- **Part A (Tasks 1–10)** is in this repo, on branch `feat/agent-sessions-page` off `main`, in a worktree. Every path is relative to the repo root.
- **Part B (Task 11)** is in scout-plugin, on branch `fix/sessions-pr-fetch-live-only` off scout-plugin `main`.
  - Use a worktree outside `~/scout-plugin/.claude/worktrees/`, because those get cleaned up.
  - It gets its own PR. Part A does not depend on it, so the two can land in either order.

## The PR-refresh decision

The brief left one decision to this plan. A build with `gh` takes up to ~30 s (25 sequential fetches at ~1.1 s each), so the ~2 s refresh cannot include PR fetches. The decision is recorded in spec §10.1 and §10.7.

1. **App: two lanes.**
   - A fast `--no-gh` lane does the 2-second work. `--no-gh` serves PR state from the engine's PR cache instead of marking it unknown, so the board still shows PR state.
   - A separate PR lane runs the `gh` build every 2 minutes in the background. It never blocks the fast lane, and its own output is never shown, because by then its liveness data is up to 30 s old. The fast build that follows it publishes the new PR state.
2. **Engine: a small fix, not a redesign.**
   - On this machine, 96 never-fetched PRs on *archived* sessions were being fetched ahead of the 23 open PRs on live sessions, because never-fetched refs sort first. Part B fetches only for sessions that are not archived.
   - Concurrent fetches, a separate refresh command and a smaller cap were considered and are not needed. With two lanes, build length no longer reaches the UI, and the 10-minute TTL bounds freshness.

## Dry run

Before review, this plan's code was applied to scratch worktrees and run there. Nothing from them was committed.

- **App (Tasks 1–9), applied to `main` at bc09033.**
  - Every new suite passes:

    | Suite | Tests |
    |---|---|
    | `SessionIndexDecodingTests` | 11 |
    | `SessionsLayoutTests` | 11 |
    | `SessionsRefreshTests` | 6 |
    | `SessionIndexServiceTests` | 16 |
    | `ClaudeLauncherResumeTests` | 7 |
    | `SessionsFormatTests` | 5 |
    | `SessionHandoffTests` | 3 |
    | `SessionsBoardSmokeTests` | 5 |
    | `SessionsPageSmokeTests` | 3 |
    | `AppStateSessionsTests` | 2 |

    The existing launcher suites pass unchanged.
  - `SessionIndexServiceTests` and the view smoke tests ran 20 times in a row and passed every time (460 of 460).
  - The full `ScoutTests` target (about 980 tests) ran eight times. Four runs were green. Each of the other four had a failure in an existing timing-sensitive test, and every failing test passed on rerun:
    - `FileWatcherTests.emitsEventOnFileCreation` (a 3-second ceiling), in two runs. One of those runs also failed `ActionItemsIntegrationTests` and `FakeScoutRunIntegrationTests`.
    - `ConnectorHealthHotPathTests.watcherCoalescesAppendBursts` (at most 3 refreshes in a fixed window), in two runs.

    These tests flake on unmodified `main` too: it failed `watcherCoalescesAppendBursts` in one of five runs. They fail more often with this plan's suites added, which bring more main-actor rendering and process launches into the run.

    Both are being fixed outside this plan:
    - [#117](https://github.com/Raven-Scout/Scout/pull/117), merged 2026-10-03, bounds `watcherCoalescesAppendBursts` relative to the burst (at most `burst / 5`, which is 10) instead of at most 3. In 25 more full runs of this dry run, the test counted 3 or 4 refreshes, so the new bound has margin.
    - [#123](https://github.com/Raven-Scout/Scout/pull/123), open, gives `FileWatcherTests` and the two FSEvents integration suites a 30-second liveness budget.

    Task 10 says how to treat a failure in these tests.
  - Line coverage was 74.8% locally, against a floor of 70%. Local runs read higher than CI.
  - The files this plan touches build without warnings. The dry run found two, and the code below already fixes them.
- **Live data, read-only.**
  - A copy of this machine's real index (382 sessions, 924 KB) decoded with 0 unreadable sessions in 22 ms.
  - The layout for one render (lanes, Now strip, chip counts and table rows) took 3.9 ms. It produced 25 lanes with 27 cards, 84 stale cards collapsed, and 13 sessions needing you.
  - `scoutctl session index --json --no-gh` through the `~/.local/bin/scoutctl` shim took 0.18–0.19 s warm.
  - The live install already runs plan 1b (the legacy `cc-sessions.cache.json` is gone), so this work no longer needs the `scout-sessions-dev` harness.
- **Engine (Task 11), applied to scout-plugin `main` at abd31c6 (after v0.11.1).**
  - `tests/unit/test_sessions_index.py` passes all 34 tests. Three of them fail on the old code.
  - The full suite passed 2544 tests, with 13 skipped. One earlier full run failed `tests/integration/test_action_items_watch.py` once; it passed alone, on unmodified `main`, and in the next full run.
  - ruff and mypy are clean.

## Global Constraints

- **Read only the index.** The app runs `scoutctl session index` and decodes what it prints.
  - It never parses a transcript.
  - It never opens a file under `~/Library/Application Support/Claude/` or `~/.claude`, and never writes there.
  - It only watches three folders there for FSEvents change notifications (spec §10.4).
- **Tests never touch the real `~/Scout`, `~/.claude` or the desktop store.**
  - `AppState.Configuration.agentSessionWatchRoots` defaults to `[]`.
  - Every `SessionIndexService` built in a test gets a temp `indexFile` and a scripted runner.
  - One test runs `SystemProcessRunner` on a path that does not exist.
- **Anonymise every fixture**, as this repo's `CLAUDE.md` requires, because the repo is public.
  - Use the people Alex, Priya and Sam, repos `example-org/<repo>`, tickets `PROJ-1234` and paths under `/Users/alex/`.
  - Use no real session titles, branches or vendor names.
  - The same rule covers screenshots in the PR.
- **Concurrency.**
  - The app target defaults to main-actor isolation. Model types and pure types are declared `nonisolated` and `Sendable`, so they can decode and lay out off the main actor.
  - Views and `SessionIndexService` stay on the main actor.
  - The test target defaults to nonisolated, so suites that touch the service are `@MainActor`.
- **Map the JSON contract explicitly** with `CodingKeys`. Never use `.convertFromSnakeCase`: it also rewrites dictionary keys, which would turn `counts["needs_you"]` into `needsYou`.
- **Republish only on change.** The page must not re-render the whole window every 2 seconds.
  - `SessionIndexService.index` is assigned only when `hasSameContent` is false.
  - `lastRefreshAt` is not `@Published`.
  - `AppState` forwards only the badge count.
- **New files need no project edits.** `Scout/` and `ScoutTests/` are filesystem-synchronized groups.
- **Use design tokens only** (`DS.*`), as the Schedules page does.
- **Build and test** with `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<TypeName>`.
  - Use the Swift type name, not the `@Suite` display name.
  - Confirm the run printed `Test run with N tests`. A selector that matches nothing still prints `** TEST SUCCEEDED **`.
  - In a fresh checkout without signing set up, append `CODE_SIGNING_ALLOWED=NO`.
- **Git.**
  - Work in a worktree on `feat/agent-sessions-page`.
  - Never use bare `git stash`.
  - End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - For Part B, never switch the branch of the `~/scout-plugin` primary checkout. It is the live install.

## Review Focus

These are the inputs most likely to hurt a real user that the spec implies but does not spell out. Each one has a test in the task that owns the code.

1. **A running session that stops writing.** No file is written when the 120-second running window lapses, so without a timed rebuild the session would show *running* forever. Task 4 covers it with `theHeartbeatRebuildsWithoutAnyFileEvent`.
2. **The engine's own writes reaching the watch.** This includes its `.sessions-index.json.<random>.tmp` temp files, and a vault reached through a symlink. If these triggered a refresh, every build would schedule the next one. Covered by:
   - Task 3: `theEnginesOwnWritesAreIgnored` and `aSymlinkedCacheDirectoryStillMatches`;
   - Task 4: `fileEventsRefreshButTheEnginesOwnWritesDoNot`.
3. **A 30-second PR build in flight.** The board must keep refreshing, a second PR build must not start, and the PR build's 30-second-old snapshot must never be shown. Task 4 covers it with `thePRLaneNeverBlocksTheFastLaneAndIsNeverPublished`.
4. **An older or missing engine.** scout-plugin older than 0.11.0 exits 2 with "No such command 'index'". A missing `scoutctl` exits 127 through `/usr/bin/env`, or fails to launch. Each case gets a plain banner and no PR builds, never an endless spinner. Covered by:
   - Task 4: `anEngineWithoutSessionIndexIsTooOldAndSkipsPRBuilds`, `scoutctlMissingFromPathIsReported` and `aScoutctlThatCannotBeLaunchedIsReported`;
   - Task 8: `pageRendersInEveryState`.
5. **One odd session record**, such as a state value from a newer engine or a timestamp that does not parse. The rest of the page renders, and a banner says how many sessions were skipped. Covered by:
   - Task 1: `aSessionThatDoesNotDecodeIsSkippedAndCounted`;
   - Task 8: `pageRendersInEveryState`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `Scout/Sessions/Models/AgentSession.swift` | create | session, PR, transcript and worktree models; `AgentSessionState` and its severity order |
| `Scout/Sessions/Models/SessionIndex.swift` | create | index model; decoding with a schema check and lossy sessions; same-content comparison |
| `Scout/Sessions/Models/SessionsLayout.swift` | create | pure filters, swimlanes, Now strip, chip counts, table rows, parent and children |
| `Scout/Sessions/SessionsRefresh.swift` | create | cadence, `scoutctl` arguments, watch roots, the filter for the engine's own writes |
| `Scout/Sessions/SessionIndexService.swift` | create | the two lanes, heartbeat, watches, decoding off the main actor, error shaping |
| `Scout/Sessions/SessionsFormat.swift` | create | ages, PR chips, model names, resume folder |
| `Scout/Sessions/SessionHandoff.swift` | create | "Copy handoff" Markdown |
| `Scout/Sessions/Views/SessionStatePill.swift` | create | state colours, dot, pill |
| `Scout/Sessions/Views/SessionCardView.swift` | create | one card |
| `Scout/Sessions/Views/SessionsBoardView.swift` | create | Now strip, swimlanes, collapsed Stale and Done |
| `Scout/Sessions/Views/SessionsTableView.swift` | create | sortable `Table` |
| `Scout/Sessions/Views/SessionsHeader.swift` | create | title, freshness, search, menus, Board/Table toggle, state chips; `SessionsViewMode` |
| `Scout/Sessions/Views/SessionDetailView.swift` | create | 380 pt detail pane and its actions |
| `Scout/Sessions/Views/SessionsView.swift` | create | page shell, banners, visibility |
| `Scout/Utilities/ClaudeLauncher.swift` | modify | `CLIPurpose`, `resume(cliSessionID:cwd:config:)`, `--resume` on every terminal path |
| `Scout/Shell/AppState.swift` | modify | build, start and expose the service; forward the badge; `agentSessionWatchRoots` |
| `Scout/Shell/MainWindowView.swift` | modify | `SidebarItem.sessions`, routing, badge |
| `Scout/Shell/SidebarView.swift` | modify | the Sessions row |
| `ScoutTests/Fixtures/sessions-index.fixture.json` | create | eleven anonymised sessions covering every state |
| `ScoutTests/Sessions/*.swift` | create | the suites listed in the dry run, `SessionsFixture`, `ScriptedSessionsRunner` |
| `ScoutTests/Shell/ViewSmokeTests.swift` | modify | nine sidebar destinations |
| `docs/feature-roadmap.md` | modify | F-2 points at this plan |
| scout-plugin `engine/scout/sessions/index.py` | modify | `gh` fetches only for sessions that are not archived |
| scout-plugin `engine/tests/unit/test_sessions_index.py` | modify | the priority test, plus three new tests |
| scout-plugin `CHANGELOG.md` | modify | Unreleased entry |

## Out of scope

- The World view (§6.6). That is plan 4.
- Archive, rename and group actions; F-4 session ↔ action-item links; a Claude Code `Stop` hook.
- Hardening the timing tests named in the dry run. #117 (merged) and #123 do that.

---

# Part A — Scout.app

### Task 1: Index models, fixture and decoding

The app decodes `sessions-index.json` schema v1 exactly as the engine contract defines it.
- A session that does not decode is skipped and counted, so it cannot blank the page.
- A schema other than 1 is reported as such.

**Files:**
- Create: `ScoutTests/Fixtures/sessions-index.fixture.json`
- Create: `ScoutTests/Sessions/SessionsFixture.swift`
- Create: `ScoutTests/Sessions/SessionIndexDecodingTests.swift`
- Create: `Scout/Sessions/Models/AgentSession.swift`
- Create: `Scout/Sessions/Models/SessionIndex.swift`

**Interfaces:**
- Consumes: `FixtureAnchor` (`ScoutTests/ScoutTests.swift`) to find the test bundle.
- Produces (every type `nonisolated`, `Equatable` and `Sendable`):
  - `AgentSessionState`, with cases `needsYou`, `running`, `waiting`, `parked`, `stale` and `done`. The raw values are the engine's spelling. It has `severity: Int` (0–5) and `label: String`.
  - `SessionWorktree`; `SessionPR`, with `reviewLabel: String?`, `checksSymbol: String?` and `webURL: URL?`; `SessionLastTurn`; `SessionTranscript`.
  - `AgentSession`, with `displayTitle: String` and `primaryReason: String?`.
  - `SessionProject`, `SessionSourceError` and `SessionIndexDisplay`.
  - `SessionIndexError`, with cases `.unsupportedSchema(Int)` and `.malformed(String)`.
  - `SessionIndex`, with:
    - `static func decode(_ data: Data) throws -> SessionIndex`;
    - `static func parseTimestamp(_ text: String) -> Date?`;
    - `func hasSameContent(as other: SessionIndex) -> Bool`;
    - `static let supportedSchemaVersion = 1`;
    - `let unreadableSessions: Int`.
  - Test support: `SessionsFixture.now` (2026-09-15T12:00:00Z), and `data()`, `index()`, `object()`, `encode(_:)` and `session(_:in:)`.

- [ ] **Step 1: Add the fixture**

The fixture is written by hand, not generated. The engine's own fixtures are built per test in a temp home, so they carry that run's paths and clock. Task 1's `theFixtureMatchesTheEngineContract` checks its key sets against the engine contract.

It holds eleven sessions:

| Session | State | What it covers |
|---|---|---|
| `local_A` | needs you | PR #98 with changes requested and failing CI; ended on a question; open; in a worktree; two linked PRs |
| `local_R` | running | — |
| `local_W` | waiting | a review is requested |
| `local_C` | parked | open; a sub-agent of A |
| `local_U` | parked | a PR whose state is unknown |
| `local_Q` | parked | closed; a draft PR |
| a `cli:` session | parked | no title |
| `local_SR` | parked | one of Scout's own runs |
| `local_S` | stale | a dirty worktree |
| `local_D` | done | archived 2.5 h before `now` |
| `local_M` | done | PR merged three days before `now` |

Create `ScoutTests/Fixtures/sessions-index.fixture.json`:

```json
{
 "schema_version": 1,
 "generated_at": "2026-09-15T12:00:00Z",
 "source_counts": {"desktop": 10, "cli_only": 1, "open": 3, "running": 1, "prs_refreshed": 0},
 "source_errors": [{"source": "desktop", "message": "local_bad.json: Expecting value: line 1 column 2 (char 1)"}],
 "display": {"done_visible_hours": 24, "stale_after_days": 3},
 "projects": [
  {"key": "/Users/alex/code/example-repo", "name": "Example Repo", "group_id": "cg-0001",
   "counts": {"needs_you": 1, "running": 1, "waiting": 0, "parked": 2, "stale": 1, "done": 1}},
  {"key": "/Users/alex/code/other-repo", "name": "other-repo", "group_id": null,
   "counts": {"needs_you": 0, "running": 0, "waiting": 1, "parked": 2, "stale": 0, "done": 1}},
  {"key": "/Users/alex/Scout", "name": "Scout", "group_id": null,
   "counts": {"needs_you": 0, "running": 0, "waiting": 0, "parked": 1, "stale": 0, "done": 0}}
 ],
 "sessions": [
  {"id": "local_A", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000001",
   "title": "Fix the parser", "title_source": "auto",
   "project_key": "/Users/alex/code/example-repo", "group_name": "Example Repo",
   "cwd": "/Users/alex/code/example-repo/.claude/worktrees/w-compass",
   "origin_cwd": "/Users/alex/code/example-repo",
   "worktree": {"path": "/Users/alex/code/example-repo/.claude/worktrees/w-compass", "name": "w-compass",
                "branch": "claude/w-compass", "source_branch": "main", "dirty": false},
   "created_at": "2026-09-14T08:00:00Z", "last_activity_at": "2026-09-15T11:00:00Z",
   "model": "claude-opus-5", "effort": "xhigh", "turns": 19,
   "is_archived": false, "is_open": true, "is_scout_run": false,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
   "prs": [
    {"number": 98, "repo": "example-org/example-repo", "url": "https://github.com/example-org/example-repo/pull/98",
     "state": "OPEN", "is_draft": false, "review_decision": "CHANGES_REQUESTED", "review_requested": false,
     "checks": "failing", "merge_state": "CLEAN", "fetched_at": "2026-09-15T11:55:00Z", "stale": false,
     "updated_at": "2026-09-15T10:00:00Z"},
    {"number": 90, "repo": "example-org/example-repo", "url": "https://github.com/example-org/example-repo/pull/90",
     "state": "MERGED", "is_draft": false, "review_decision": "APPROVED", "review_requested": false,
     "checks": "passing", "merge_state": "unknown", "fetched_at": "2026-09-14T09:00:00Z", "stale": false,
     "updated_at": "2026-09-14T08:30:00Z"}
   ],
   "pr": {"number": 98, "repo": "example-org/example-repo", "url": "https://github.com/example-org/example-repo/pull/98",
          "state": "OPEN", "is_draft": false, "review_decision": "CHANGES_REQUESTED", "review_requested": false,
          "checks": "failing", "merge_state": "CLEAN", "fetched_at": "2026-09-15T11:55:00Z", "stale": false,
          "updated_at": "2026-09-15T10:00:00Z"},
   "transcript": {"path": "/Users/alex/.claude/projects/-Users-alex-code-example-repo--claude-worktrees-w-compass/aaaaaaaa-0000-0000-0000-000000000001.jsonl",
                  "first_prompt": "Please fix the parser so blank lines between items are kept.",
                  "files_touched": ["~/code/example-repo/parser.py", "~/code/example-repo/tests/test_parser.py"],
                  "tool_calls": 212, "last_turn": {"at": "2026-09-15T11:00:00Z", "kind": "question"},
                  "mtime_ns": 1789470000000000000},
   "state": "needs_you", "state_reasons": ["changes requested on PR #98", "CI failing", "ended on a question"]},

  {"id": "local_R", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000002",
   "title": "Add the export command", "title_source": "user",
   "project_key": "/Users/alex/code/example-repo", "group_name": "Example Repo",
   "cwd": "/Users/alex/code/example-repo/.claude/worktrees/w-export",
   "origin_cwd": "/Users/alex/code/example-repo",
   "worktree": {"path": "/Users/alex/code/example-repo/.claude/worktrees/w-export", "name": "w-export",
                "branch": "claude/w-export", "source_branch": "main", "dirty": false},
   "created_at": "2026-09-13T08:00:00Z", "last_activity_at": "2026-09-15T11:59:20Z",
   "model": "claude-opus-5", "effort": "high", "turns": 7,
   "is_archived": false, "is_open": true, "is_scout_run": false,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
   "prs": [
    {"number": 102, "repo": "example-org/example-repo", "url": "https://github.com/example-org/example-repo/pull/102",
     "state": "OPEN", "is_draft": false, "review_decision": "REVIEW_REQUIRED", "review_requested": true,
     "checks": "passing", "merge_state": "BLOCKED", "fetched_at": "2026-09-15T11:55:00Z", "stale": false,
     "updated_at": "2026-09-13T09:00:00Z"}
   ],
   "pr": {"number": 102, "repo": "example-org/example-repo", "url": "https://github.com/example-org/example-repo/pull/102",
          "state": "OPEN", "is_draft": false, "review_decision": "REVIEW_REQUIRED", "review_requested": true,
          "checks": "passing", "merge_state": "BLOCKED", "fetched_at": "2026-09-15T11:55:00Z", "stale": false,
          "updated_at": "2026-09-13T09:00:00Z"},
   "transcript": {"path": "/Users/alex/.claude/projects/-Users-alex-code-example-repo--claude-worktrees-w-export/aaaaaaaa-0000-0000-0000-000000000002.jsonl",
                  "first_prompt": "Add an export command that writes the report as CSV.",
                  "files_touched": ["~/code/example-repo/cli.py"],
                  "tool_calls": 40, "last_turn": {"at": "2026-09-15T11:59:20Z", "kind": "tool_use"},
                  "mtime_ns": 1789473560000000000},
   "state": "running", "state_reasons": ["active 40s ago", "PR #102 awaiting review 2d"]},

  {"id": "local_W", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000003",
   "title": "Upgrade the HTTP client", "title_source": "auto",
   "project_key": "/Users/alex/code/other-repo", "group_name": null,
   "cwd": "/Users/alex/code/other-repo", "origin_cwd": "/Users/alex/code/other-repo",
   "worktree": null,
   "created_at": "2026-09-10T08:00:00Z", "last_activity_at": "2026-09-14T09:00:00Z",
   "model": "claude-sonnet-5", "effort": "medium", "turns": 12,
   "is_archived": false, "is_open": false, "is_scout_run": false,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
   "prs": [
    {"number": 77, "repo": "example-org/other-repo", "url": "https://github.com/example-org/other-repo/pull/77",
     "state": "OPEN", "is_draft": false, "review_decision": "REVIEW_REQUIRED", "review_requested": true,
     "checks": "passing", "merge_state": "BLOCKED", "fetched_at": "2026-09-15T11:50:00Z", "stale": false,
     "updated_at": "2026-09-10T12:00:00Z"}
   ],
   "pr": {"number": 77, "repo": "example-org/other-repo", "url": "https://github.com/example-org/other-repo/pull/77",
          "state": "OPEN", "is_draft": false, "review_decision": "REVIEW_REQUIRED", "review_requested": true,
          "checks": "passing", "merge_state": "BLOCKED", "fetched_at": "2026-09-15T11:50:00Z", "stale": false,
          "updated_at": "2026-09-10T12:00:00Z"},
   "transcript": null,
   "state": "waiting", "state_reasons": ["PR #77 awaiting review 5d"]},

  {"id": "local_C", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000004",
   "title": "Investigate the flaky parser test", "title_source": "auto",
   "project_key": "/Users/alex/code/example-repo", "group_name": "Example Repo",
   "cwd": "/Users/alex/code/example-repo/.claude/worktrees/w-compass",
   "origin_cwd": "/Users/alex/code/example-repo",
   "worktree": {"path": "/Users/alex/code/example-repo/.claude/worktrees/w-compass", "name": "w-compass",
                "branch": "claude/w-compass", "source_branch": "main", "dirty": false},
   "created_at": "2026-09-15T10:00:00Z", "last_activity_at": "2026-09-15T11:48:00Z",
   "model": "claude-haiku-4-5", "effort": null, "turns": 3,
   "is_archived": false, "is_open": true, "is_scout_run": false,
   "parent_session_id": "local_A", "spawned_task_id": "task_9", "scheduled_task_id": null,
   "prs": [], "pr": null,
   "transcript": {"path": "/Users/alex/.claude/projects/-Users-alex-code-example-repo--claude-worktrees-w-compass/aaaaaaaa-0000-0000-0000-000000000004.jsonl",
                  "first_prompt": "Find out why test_blank_lines fails one run in ten.",
                  "files_touched": [], "tool_calls": 9,
                  "last_turn": {"at": "2026-09-15T11:48:00Z", "kind": "end_turn"},
                  "mtime_ns": 1789472880000000000},
   "state": "parked", "state_reasons": ["open, idle 12m ago"]},

  {"id": "local_U", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000005",
   "title": "Tidy the release notes", "title_source": "auto",
   "project_key": "/Users/alex/code/example-repo", "group_name": "Example Repo",
   "cwd": "/Users/alex/code/example-repo", "origin_cwd": "/Users/alex/code/example-repo",
   "worktree": null,
   "created_at": "2026-09-15T09:00:00Z", "last_activity_at": "2026-09-15T11:30:00Z",
   "model": "claude-opus-5", "effort": "high", "turns": 2,
   "is_archived": false, "is_open": false, "is_scout_run": false,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
   "prs": [
    {"number": 110, "repo": "example-org/example-repo", "url": null,
     "state": "unknown", "is_draft": false, "review_decision": "unknown", "review_requested": false,
     "checks": "unknown", "merge_state": "unknown", "fetched_at": null, "stale": false, "updated_at": null}
   ],
   "pr": {"number": 110, "repo": "example-org/example-repo", "url": null,
          "state": "unknown", "is_draft": false, "review_decision": "unknown", "review_requested": false,
          "checks": "unknown", "merge_state": "unknown", "fetched_at": null, "stale": false, "updated_at": null},
   "transcript": null,
   "state": "parked", "state_reasons": ["last active 30m ago", "PR #110 open (state unknown)"]},

  {"id": "local_Q", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000006",
   "title": "Sketch the plugin settings page", "title_source": "auto",
   "project_key": "/Users/alex/code/other-repo", "group_name": null,
   "cwd": "/Users/alex/code/other-repo", "origin_cwd": "/Users/alex/code/other-repo",
   "worktree": null,
   "created_at": "2026-09-15T08:00:00Z", "last_activity_at": "2026-09-15T10:00:00Z",
   "model": "claude-opus-5", "effort": "high", "turns": 5,
   "is_archived": false, "is_open": false, "is_scout_run": false,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
   "prs": [
    {"number": 81, "repo": "example-org/other-repo", "url": "https://github.com/example-org/other-repo/pull/81",
     "state": "OPEN", "is_draft": true, "review_decision": "", "review_requested": false,
     "checks": "pending", "merge_state": "DRAFT", "fetched_at": "2026-09-15T11:50:00Z", "stale": false,
     "updated_at": "2026-09-15T10:00:00Z"}
   ],
   "pr": {"number": 81, "repo": "example-org/other-repo", "url": "https://github.com/example-org/other-repo/pull/81",
          "state": "OPEN", "is_draft": true, "review_decision": "", "review_requested": false,
          "checks": "pending", "merge_state": "DRAFT", "fetched_at": "2026-09-15T11:50:00Z", "stale": false,
          "updated_at": "2026-09-15T10:00:00Z"},
   "transcript": null,
   "state": "parked", "state_reasons": ["last active 2h ago", "draft PR #81"]},

  {"id": "cli:aaaaaaaa-0000-0000-0000-000000000007", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000007",
   "title": null, "title_source": null,
   "project_key": "/Users/alex/code/other-repo", "group_name": null,
   "cwd": "/Users/alex/code/other-repo", "origin_cwd": "/Users/alex/code/other-repo",
   "worktree": null,
   "created_at": null, "last_activity_at": "2026-09-15T09:00:00Z",
   "model": null, "effort": null, "turns": null,
   "is_archived": false, "is_open": false, "is_scout_run": false,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
   "prs": [], "pr": null,
   "transcript": {"path": "/Users/alex/.claude/projects/-Users-alex-code-other-repo/aaaaaaaa-0000-0000-0000-000000000007.jsonl",
                  "first_prompt": "Sam asked for a release checklist.\nKeep it short.",
                  "files_touched": ["~/code/other-repo/RELEASING.md"], "tool_calls": 4,
                  "last_turn": {"at": "2026-09-15T09:00:00Z", "kind": "end_turn"},
                  "mtime_ns": 1789462800000000000},
   "state": "parked", "state_reasons": ["last active 3h ago"]},

  {"id": "local_SR", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000008",
   "title": "scout-morning-briefing-20260915-0800", "title_source": "custom",
   "project_key": "/Users/alex/Scout", "group_name": null,
   "cwd": "/Users/alex/Scout", "origin_cwd": "/Users/alex/Scout",
   "worktree": null,
   "created_at": "2026-09-15T08:00:00Z", "last_activity_at": "2026-09-15T08:00:00Z",
   "model": "claude-sonnet-5", "effort": null, "turns": 1,
   "is_archived": false, "is_open": false, "is_scout_run": true,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": "scout-morning-briefing",
   "prs": [], "pr": null, "transcript": null,
   "state": "parked", "state_reasons": ["last active 4h ago"]},

  {"id": "local_S", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000009",
   "title": "Try the new tracing job", "title_source": "auto",
   "project_key": "/Users/alex/code/example-repo", "group_name": "Example Repo",
   "cwd": "/Users/alex/code/example-repo/.claude/worktrees/w-trace",
   "origin_cwd": "/Users/alex/code/example-repo",
   "worktree": {"path": "/Users/alex/code/example-repo/.claude/worktrees/w-trace", "name": "w-trace",
                "branch": "claude/w-trace", "source_branch": "main", "dirty": true},
   "created_at": "2026-09-08T08:00:00Z", "last_activity_at": "2026-09-09T12:00:00Z",
   "model": "claude-opus-5", "effort": "high", "turns": 30,
   "is_archived": false, "is_open": false, "is_scout_run": false,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
   "prs": [], "pr": null, "transcript": null,
   "state": "stale", "state_reasons": ["dirty worktree, idle 6d"]},

  {"id": "local_D", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000010",
   "title": "Write the onboarding guide", "title_source": "auto",
   "project_key": "/Users/alex/code/example-repo", "group_name": "Archived",
   "cwd": "/Users/alex/code/example-repo", "origin_cwd": "/Users/alex/code/example-repo",
   "worktree": null,
   "created_at": "2026-09-14T08:00:00Z", "last_activity_at": "2026-09-15T09:30:00Z",
   "model": "claude-opus-5", "effort": "high", "turns": 8,
   "is_archived": true, "is_open": false, "is_scout_run": false,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
   "prs": [
    {"number": 64, "repo": "example-org/example-repo", "url": "https://github.com/example-org/example-repo/pull/64",
     "state": "unknown", "is_draft": false, "review_decision": "unknown", "review_requested": false,
     "checks": "unknown", "merge_state": "unknown", "fetched_at": null, "stale": false, "updated_at": null}
   ],
   "pr": {"number": 64, "repo": "example-org/example-repo", "url": "https://github.com/example-org/example-repo/pull/64",
          "state": "unknown", "is_draft": false, "review_decision": "unknown", "review_requested": false,
          "checks": "unknown", "merge_state": "unknown", "fetched_at": null, "stale": false, "updated_at": null},
   "transcript": null,
   "state": "done", "state_reasons": ["archived"]},

  {"id": "local_M", "cli_session_id": "aaaaaaaa-0000-0000-0000-000000000011",
   "title": "Bump the minimum Python", "title_source": "auto",
   "project_key": "/Users/alex/code/other-repo", "group_name": null,
   "cwd": "/Users/alex/code/other-repo", "origin_cwd": "/Users/alex/code/other-repo",
   "worktree": null,
   "created_at": "2026-09-11T08:00:00Z", "last_activity_at": "2026-09-12T08:00:00Z",
   "model": "claude-opus-5", "effort": "high", "turns": 4,
   "is_archived": false, "is_open": false, "is_scout_run": false,
   "parent_session_id": null, "spawned_task_id": null, "scheduled_task_id": null,
   "prs": [
    {"number": 60, "repo": "example-org/other-repo", "url": "https://github.com/example-org/other-repo/pull/60",
     "state": "MERGED", "is_draft": false, "review_decision": "APPROVED", "review_requested": false,
     "checks": "passing", "merge_state": "unknown", "fetched_at": "2026-09-12T09:00:00Z", "stale": false,
     "updated_at": "2026-09-12T08:30:00Z"}
   ],
   "pr": {"number": 60, "repo": "example-org/other-repo", "url": "https://github.com/example-org/other-repo/pull/60",
          "state": "MERGED", "is_draft": false, "review_decision": "APPROVED", "review_requested": false,
          "checks": "passing", "merge_state": "unknown", "fetched_at": "2026-09-12T09:00:00Z", "stale": false,
          "updated_at": "2026-09-12T08:30:00Z"},
   "transcript": null,
   "state": "done", "state_reasons": ["PR #60 merged"]}
 ]
}
```

- [ ] **Step 2: Add the fixture loader**

Create `ScoutTests/Sessions/SessionsFixture.swift`:

```swift
import Foundation
@testable import Scout

/// `Fixtures/sessions-index.fixture.json`: eleven anonymised sessions covering
/// every state, both PR shapes, a CLI-only session, a sub-agent and one of
/// Scout's own runs. Every timestamp is relative to `now`.
enum SessionsFixture {
    /// The fixture's `generated_at`.
    static let now = Date(timeIntervalSince1970: 1_789_473_600)  // 2026-09-15T12:00:00Z

    static func data() throws -> Data {
        let bundle = Bundle(for: FixtureAnchor.self)
        guard let url = bundle.url(forResource: "sessions-index.fixture", withExtension: "json")
                ?? bundle.resourceURL?.appendingPathComponent("sessions-index.fixture.json") else {
            throw CocoaError(.fileNoSuchFile)
        }
        return try Data(contentsOf: url)
    }

    static func index() throws -> SessionIndex {
        try SessionIndex.decode(data())
    }

    /// The fixture as a mutable JSON object, for tests that need a variant.
    static func object() throws -> [String: Any] {
        try JSONSerialization.jsonObject(with: data()) as? [String: Any] ?? [:]
    }

    static func encode(_ object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object)
    }

    static func session(_ id: String, in index: SessionIndex) -> AgentSession? {
        index.sessions.first { $0.id == id }
    }
}
```

- [ ] **Step 3: Write the failing tests**

Create `ScoutTests/Sessions/SessionIndexDecodingTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("SessionIndex decoding")
struct SessionIndexDecodingTests {

    @Test func decodesEveryFixtureSession() throws {
        let index = try SessionsFixture.index()
        #expect(index.schemaVersion == 1)
        #expect(index.sessions.count == 11)
        #expect(index.unreadableSessions == 0)
        #expect(index.generatedAt == SessionsFixture.now)
        #expect(index.display == SessionIndexDisplay(doneVisibleHours: 24, staleAfterDays: 3))
        #expect(index.projects.map(\.name) == ["Example Repo", "other-repo", "Scout"])
        #expect(index.sourceErrors.map(\.source) == ["desktop"])
        #expect(index.sourceCounts["open"] == 3)
    }

    @Test func mapsEverySnakeCaseField() throws {
        let a = try #require(SessionsFixture.session("local_A", in: SessionsFixture.index()))
        #expect(a.cliSessionID == "aaaaaaaa-0000-0000-0000-000000000001")
        #expect(a.titleSource == "auto")
        #expect(a.projectKey == "/Users/alex/code/example-repo")
        #expect(a.groupName == "Example Repo")
        #expect(a.originCwd == "/Users/alex/code/example-repo")
        #expect(a.worktree?.sourceBranch == "main")
        #expect(a.worktree?.branch == "claude/w-compass")
        #expect(a.isOpen && !a.isArchived && !a.isScoutRun)
        #expect(a.turns == 19)
        #expect(a.state == .needsYou)
        #expect(a.stateReasons == ["changes requested on PR #98", "CI failing", "ended on a question"])
        #expect(a.prs.map(\.number) == [98, 90])
        let pr = try #require(a.pr)
        #expect(pr.reviewDecision == "CHANGES_REQUESTED")
        #expect(pr.checks == "failing")
        #expect(pr.mergeState == "CLEAN")
        #expect(pr.isDraft == false && pr.reviewRequested == false && pr.stale == false)
        #expect(pr.updatedAt == SessionIndex.parseTimestamp("2026-09-15T10:00:00Z"))
        let transcript = try #require(a.transcript)
        #expect(transcript.toolCalls == 212)
        #expect(transcript.filesTouched.count == 2)
        #expect(transcript.lastTurn.kind == "question")
        #expect(transcript.mtimeNs == 1_789_470_000_000_000_000)
    }

    @Test func aCLIOnlySessionHasNoTitleAndUsesItsFirstPromptLine() throws {
        let cli = try #require(SessionsFixture.session(
            "cli:aaaaaaaa-0000-0000-0000-000000000007", in: SessionsFixture.index()))
        #expect(cli.title == nil && cli.worktree == nil && cli.createdAt == nil && cli.turns == nil)
        #expect(cli.displayTitle == "Sam asked for a release checklist.")
    }

    @Test func projectCountsKeepTheEngineSpelling() throws {
        // `.convertFromSnakeCase` would rewrite these dictionary keys to "needsYou".
        let project = try #require(SessionsFixture.index().projects.first)
        #expect(project.counts[AgentSessionState.needsYou.rawValue] == 1)
        #expect(project.counts["needs_you"] == 1)
    }

    @Test func unknownFieldsAreIgnored() throws {
        var object = try SessionsFixture.object()
        object["added_later"] = ["anything": true]
        var sessions = try #require(object["sessions"] as? [[String: Any]])
        sessions[0]["added_later"] = 42
        object["sessions"] = sessions
        let index = try SessionIndex.decode(SessionsFixture.encode(object))
        #expect(index.sessions.count == 11 && index.unreadableSessions == 0)
    }

    @Test func anUnsupportedSchemaVersionIsRejected() throws {
        var object = try SessionsFixture.object()
        object["schema_version"] = 2
        #expect(throws: SessionIndexError.unsupportedSchema(2)) {
            try SessionIndex.decode(SessionsFixture.encode(object))
        }
    }

    @Test func aSessionThatDoesNotDecodeIsSkippedAndCounted() throws {
        var object = try SessionsFixture.object()
        var sessions = try #require(object["sessions"] as? [[String: Any]])
        sessions[1]["state"] = "exploded"
        sessions[2]["last_activity_at"] = "yesterday"
        object["sessions"] = sessions
        let index = try SessionIndex.decode(SessionsFixture.encode(object))
        #expect(index.sessions.count == 9)
        #expect(index.unreadableSessions == 2)
        #expect(index.sessions.first?.id == "local_A")
    }

    @Test func malformedJSONIsReportedAsMalformed() {
        #expect {
            try SessionIndex.decode(Data("{".utf8))
        } throws: { error in
            if case SessionIndexError.malformed = error { return true }
            return false
        }
    }

    @Test func timestampsAcceptWholeAndFractionalSeconds() {
        #expect(SessionIndex.parseTimestamp("2026-09-15T12:00:00Z") == SessionsFixture.now)
        #expect(SessionIndex.parseTimestamp("2026-09-15T12:00:00.250Z") == SessionsFixture.now.addingTimeInterval(0.25))
        #expect(SessionIndex.parseTimestamp("yesterday") == nil)
    }

    @Test func sameContentIgnoresOnlyGeneratedAt() throws {
        let index = try SessionsFixture.index()
        var object = try SessionsFixture.object()
        object["generated_at"] = "2026-09-15T12:00:02Z"
        let later = try SessionIndex.decode(SessionsFixture.encode(object))
        #expect(later.generatedAt != index.generatedAt)
        #expect(later.hasSameContent(as: index))

        var sessions = try #require(object["sessions"] as? [[String: Any]])
        sessions[0]["state_reasons"] = ["changes requested on PR #98"]
        object["sessions"] = sessions
        let changed = try SessionIndex.decode(SessionsFixture.encode(object))
        #expect(!changed.hasSameContent(as: index))
    }

    /// Mirrors scout-plugin `engine/tests/unit/test_sessions_index.py::
    /// test_index_json_contract_key_sets`. When that test changes, change this
    /// one, the fixture and the models together.
    @Test func theFixtureMatchesTheEngineContract() throws {
        let object = try SessionsFixture.object()
        #expect(Set(object.keys) == ["schema_version", "generated_at", "source_counts", "source_errors",
                                     "display", "projects", "sessions"])
        let sessions = try #require(object["sessions"] as? [[String: Any]])
        let sessionKeys: Set<String> = [
            "id", "cli_session_id", "title", "title_source", "project_key", "group_name", "cwd",
            "origin_cwd", "worktree", "created_at", "last_activity_at", "model", "effort", "turns",
            "is_archived", "is_open", "is_scout_run", "parent_session_id", "spawned_task_id",
            "scheduled_task_id", "prs", "pr", "transcript", "state", "state_reasons",
        ]
        let prKeys: Set<String> = [
            "number", "repo", "url", "state", "is_draft", "review_decision", "review_requested",
            "checks", "merge_state", "fetched_at", "stale", "updated_at",
        ]
        for session in sessions {
            #expect(Set(session.keys) == sessionKeys)
            if let worktree = session["worktree"] as? [String: Any] {
                #expect(Set(worktree.keys) == ["path", "name", "branch", "source_branch", "dirty"])
            }
            let prs = (session["prs"] as? [[String: Any]] ?? []) + [session["pr"] as? [String: Any]].compactMap { $0 }
            for pr in prs { #expect(Set(pr.keys) == prKeys) }
            if let transcript = session["transcript"] as? [String: Any] {
                #expect(Set(transcript.keys) == ["path", "first_prompt", "files_touched", "tool_calls",
                                                 "last_turn", "mtime_ns"])
                #expect(Set((transcript["last_turn"] as? [String: Any] ?? [:]).keys) == ["at", "kind"])
            }
        }
        let projects = try #require(object["projects"] as? [[String: Any]])
        for project in projects {
            #expect(Set(project.keys) == ["key", "name", "group_id", "counts"])
            #expect(Set((project["counts"] as? [String: Any] ?? [:]).keys)
                    == Set(AgentSessionState.allCases.map(\.rawValue)))
        }
        #expect(Set((object["display"] as? [String: Any] ?? [:]).keys) == ["done_visible_hours", "stale_after_days"])
        #expect(Set((object["source_counts"] as? [String: Any] ?? [:]).keys)
                == ["desktop", "cli_only", "open", "running", "prs_refreshed"])
    }
}
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionIndexDecodingTests`

Expected: the build fails with `cannot find 'SessionIndex' in scope`.

- [ ] **Step 5: Add the session models**

Create `Scout/Sessions/Models/AgentSession.swift`:

```swift
import Foundation

/// A session's derived state (Agent Sessions spec §4.7). Raw values are the
/// engine's spelling; the declaration order is the page's severity order.
nonisolated enum AgentSessionState: String, Codable, CaseIterable, Hashable, Sendable {
    case needsYou = "needs_you"
    case running
    case waiting
    case parked
    case stale
    case done

    /// 0 = most urgent. Every sort on the page goes through this.
    var severity: Int {
        switch self {
        case .needsYou: return 0
        case .running:  return 1
        case .waiting:  return 2
        case .parked:   return 3
        case .stale:    return 4
        case .done:     return 5
        }
    }

    var label: String {
        switch self {
        case .needsYou: return "Needs you"
        case .running:  return "Running"
        case .waiting:  return "Waiting"
        case .parked:   return "Parked"
        case .stale:    return "Stale"
        case .done:     return "Done"
        }
    }
}

nonisolated struct SessionWorktree: Codable, Equatable, Hashable, Sendable {
    let path: String?
    let name: String?
    let branch: String?
    let sourceBranch: String?
    /// The desktop app's `keptDirtyWorktree` flag; no live `git status` (spec §4.8).
    let dirty: Bool

    enum CodingKeys: String, CodingKey {
        case path, name, branch, dirty
        case sourceBranch = "source_branch"
    }
}

/// One linked PR. The enum-like fields stay strings: they are GitHub's values,
/// upper-case, with a missing or not-yet-computed value spelled `unknown`
/// (spec §4.6), and GitHub can add values the app has never seen.
nonisolated struct SessionPR: Codable, Equatable, Hashable, Sendable {
    let number: Int
    let repo: String
    let url: String?
    /// `OPEN` | `MERGED` | `CLOSED` | `unknown`
    let state: String
    let isDraft: Bool
    /// `""` | `APPROVED` | `CHANGES_REQUESTED` | `REVIEW_REQUIRED` | `unknown`
    let reviewDecision: String
    let reviewRequested: Bool
    /// `passing` | `failing` | `pending` | `none` | `unknown`
    let checks: String
    /// gh `mergeStateStatus`, e.g. `CLEAN` | `DIRTY` | `BLOCKED` | `unknown`
    let mergeState: String
    let fetchedAt: Date?
    /// The last fetch failed and this is the cached value.
    let stale: Bool
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case number, repo, url, state, checks, stale
        case isDraft = "is_draft"
        case reviewDecision = "review_decision"
        case reviewRequested = "review_requested"
        case mergeState = "merge_state"
        case fetchedAt = "fetched_at"
        case updatedAt = "updated_at"
    }

    /// Short review label for chips; nil when there is nothing to say.
    var reviewLabel: String? {
        if state == "MERGED" { return "merged" }
        if state == "CLOSED" { return "closed" }
        if isDraft { return "draft" }
        switch reviewDecision {
        case "CHANGES_REQUESTED": return "changes requested"
        case "APPROVED":          return "approved"
        case "REVIEW_REQUIRED":   return "review required"
        default:                  return reviewRequested ? "review requested" : nil
        }
    }

    /// ✓ / ✗ / … for the checks rollup; nil for `none` and `unknown`.
    var checksSymbol: String? {
        switch checks {
        case "passing": return "✓"
        case "failing": return "✗"
        case "pending": return "…"
        default:        return nil
        }
    }

    var webURL: URL? { url.flatMap(URL.init(string:)) }
}

nonisolated struct SessionLastTurn: Codable, Equatable, Hashable, Sendable {
    let at: Date?
    /// `end_turn` | `tool_use` | `question` | `unknown`
    let kind: String
}

nonisolated struct SessionTranscript: Codable, Equatable, Hashable, Sendable {
    let path: String
    let firstPrompt: String
    let filesTouched: [String]
    let toolCalls: Int
    let lastTurn: SessionLastTurn
    let mtimeNs: Int64

    enum CodingKeys: String, CodingKey {
        case path
        case firstPrompt = "first_prompt"
        case filesTouched = "files_touched"
        case toolCalls = "tool_calls"
        case lastTurn = "last_turn"
        case mtimeNs = "mtime_ns"
    }
}

/// One Claude Code session from `sessions-index.json` (spec §4.8). Keys are
/// mapped explicitly rather than with `.convertFromSnakeCase`, which would
/// also rewrite the *dictionary* keys of `counts` and `source_counts`.
nonisolated struct AgentSession: Codable, Equatable, Hashable, Identifiable, Sendable {
    let id: String
    let cliSessionID: String?
    let title: String?
    let titleSource: String?
    let projectKey: String
    let groupName: String?
    let cwd: String
    let originCwd: String
    let worktree: SessionWorktree?
    let createdAt: Date?
    let lastActivityAt: Date?
    let model: String?
    let effort: String?
    let turns: Int?
    let isArchived: Bool
    let isOpen: Bool
    let isScoutRun: Bool
    let parentSessionID: String?
    let spawnedTaskID: String?
    let scheduledTaskID: String?
    let prs: [SessionPR]
    let pr: SessionPR?
    let transcript: SessionTranscript?
    let state: AgentSessionState
    let stateReasons: [String]

    enum CodingKeys: String, CodingKey {
        case id, title, cwd, worktree, model, effort, turns, prs, pr, transcript, state
        case cliSessionID = "cli_session_id"
        case titleSource = "title_source"
        case projectKey = "project_key"
        case groupName = "group_name"
        case originCwd = "origin_cwd"
        case createdAt = "created_at"
        case lastActivityAt = "last_activity_at"
        case isArchived = "is_archived"
        case isOpen = "is_open"
        case isScoutRun = "is_scout_run"
        case parentSessionID = "parent_session_id"
        case spawnedTaskID = "spawned_task_id"
        case scheduledTaskID = "scheduled_task_id"
        case stateReasons = "state_reasons"
    }

    /// The desktop title, else the first line of the first prompt (CLI-only
    /// sessions have no title), else a placeholder.
    var displayTitle: String {
        if let title, !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return title }
        let firstLine = transcript?.firstPrompt
            .split(whereSeparator: \.isNewline).first
            .map { String($0).trimmingCharacters(in: .whitespaces) } ?? ""
        if firstLine.isEmpty { return "(untitled)" }
        return firstLine.count > 120 ? String(firstLine.prefix(120)) + "…" : firstLine
    }

    /// The reason shown on a card: the deciding one (the engine lists it first).
    var primaryReason: String? { stateReasons.first }
}
```

- [ ] **Step 6: Add the index model and decoder**

Create `Scout/Sessions/Models/SessionIndex.swift`:

```swift
import Foundation

nonisolated struct SessionProject: Codable, Equatable, Hashable, Sendable {
    let key: String
    let name: String
    let groupID: String?
    /// Keyed by `AgentSessionState.rawValue`.
    let counts: [String: Int]

    enum CodingKeys: String, CodingKey {
        case key, name, counts
        case groupID = "group_id"
    }
}

nonisolated struct SessionSourceError: Codable, Equatable, Hashable, Sendable {
    let source: String
    let message: String
}

/// Display-only settings the engine echoes from `agent_sessions:` so the app
/// never parses the vault's YAML (spec §4.11).
nonisolated struct SessionIndexDisplay: Codable, Equatable, Hashable, Sendable {
    let doneVisibleHours: Int
    let staleAfterDays: Int

    enum CodingKeys: String, CodingKey {
        case doneVisibleHours = "done_visible_hours"
        case staleAfterDays = "stale_after_days"
    }
}

nonisolated enum SessionIndexError: Error, Equatable {
    /// The file declares a schema this build does not know. The service keeps
    /// the last good index and asks for a scout-plugin update.
    case unsupportedSchema(Int)
    case malformed(String)
}

/// `.scout-cache/sessions-index.json`, schema v1 (spec §4.8). The contract is
/// the engine's `test_index_json_contract_key_sets`.
nonisolated struct SessionIndex: Equatable, Sendable {
    static let supportedSchemaVersion = 1

    let schemaVersion: Int
    let generatedAt: Date?
    let sourceCounts: [String: Int]
    let sourceErrors: [SessionSourceError]
    let display: SessionIndexDisplay
    let projects: [SessionProject]
    let sessions: [AgentSession]
    /// Entries in `sessions` that did not decode. They are skipped, so one
    /// odd record cannot blank the page, and counted, so the skip is visible.
    let unreadableSessions: Int

    /// Everything but `generatedAt`, which changes on every build. The service
    /// republishes only when this differs.
    func hasSameContent(as other: SessionIndex) -> Bool {
        schemaVersion == other.schemaVersion
            && sessions == other.sessions
            && projects == other.projects
            && sourceErrors == other.sourceErrors
            && display == other.display
            && unreadableSessions == other.unreadableSessions
    }

    static func decode(_ data: Data) throws -> SessionIndex {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            guard let date = parseTimestamp(text) else {
                throw DecodingError.dataCorruptedError(
                    in: container, debugDescription: "not an ISO-8601 timestamp: \(text)")
            }
            return date
        }
        let header: Header
        do {
            header = try decoder.decode(Header.self, from: data)
        } catch {
            throw SessionIndexError.malformed(String(String(describing: error).prefix(200)))
        }
        guard header.schemaVersion == supportedSchemaVersion else {
            throw SessionIndexError.unsupportedSchema(header.schemaVersion)
        }
        let body: Body
        do {
            body = try decoder.decode(Body.self, from: data)
        } catch {
            throw SessionIndexError.malformed(String(String(describing: error).prefix(200)))
        }
        let sessions = body.sessions.compactMap(\.value)
        return SessionIndex(
            schemaVersion: header.schemaVersion,
            generatedAt: body.generatedAt,
            sourceCounts: body.sourceCounts,
            sourceErrors: body.sourceErrors,
            display: body.display,
            projects: body.projects,
            sessions: sessions,
            unreadableSessions: body.sessions.count - sessions.count
        )
    }

    /// The engine writes `2026-09-15T12:00:00Z`; GitHub's `updatedAt`, passed
    /// through, has the same shape. Fractional seconds are accepted too.
    static func parseTimestamp(_ text: String) -> Date? {
        if let date = try? Date(text, strategy: .iso8601) { return date }
        let fractional = Date.ISO8601FormatStyle(includingFractionalSeconds: true)
        return try? Date(text, strategy: fractional)
    }

    private struct Header: Decodable {
        let schemaVersion: Int
        enum CodingKeys: String, CodingKey { case schemaVersion = "schema_version" }
    }

    private struct Body: Decodable {
        let generatedAt: Date?
        let sourceCounts: [String: Int]
        let sourceErrors: [SessionSourceError]
        let display: SessionIndexDisplay
        let projects: [SessionProject]
        let sessions: [Lossy<AgentSession>]

        enum CodingKeys: String, CodingKey {
            case display, projects, sessions
            case generatedAt = "generated_at"
            case sourceCounts = "source_counts"
            case sourceErrors = "source_errors"
        }
    }

    /// Decodes an element, or nil when it does not decode.
    private struct Lossy<Wrapped: Decodable>: Decodable {
        let value: Wrapped?
        init(from decoder: Decoder) throws {
            value = try? Wrapped(from: decoder)
        }
    }
}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionIndexDecodingTests`

Expected: `Test run with 11 tests in 1 suite passed`.

- [ ] **Step 8: Commit**

```bash
git add Scout/Sessions/Models ScoutTests/Fixtures/sessions-index.fixture.json ScoutTests/Sessions
git commit -m "feat(sessions): decode the engine's session index (schema v1)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Board and table layout (`SessionsLayout`)

Every rule for what the page shows lives in one pure type:
- **Filtering.** Scout's own runs and done sessions are hidden by default. Done sessions show only inside `done_visible_hours`.
- **Ordering.** Cards are sorted by severity, with open before closed among parked sessions, then by recency, then by id, so equal cards never swap places between refreshes.
- **Grouping.** Swimlanes are ordered by recency. Stale and done sessions are kept apart so the board can collapse them (spec §10.5). The Now strip holds needs-you and running sessions.
- **Counting.** Chip counts ignore the chips themselves.

**Files:**
- Create: `Scout/Sessions/Models/SessionsLayout.swift`
- Test: `ScoutTests/Sessions/SessionsLayoutTests.swift`

**Interfaces:**
- Consumes: the Task 1 types and `SessionsFixture`.
- Produces (all `nonisolated`):
  - `SessionsFilter`, with `states: Set<AgentSessionState>` (empty means every state), `projectKey: String?`, `search: String`, `showScoutRuns: Bool` and `showRecentlyDone: Bool`.
  - `SessionRow`, with `id` (the project key), `name`, `active`, `stale`, `done`, `lastActivity` and `counts: [AgentSessionState: Int]`.
  - `SessionTableRow`, with `session`, `projectName`, and the sort keys `severity`, `title`, `reason`, `prNumber`, `lastActivity`, `model` and `turns`.
  - `SessionsLayout`, with these static functions:
    - `rows(index:filter:now:) -> [SessionRow]`
    - `nowStrip(index:filter:now:) -> [AgentSession]`
    - `stateCounts(index:filter:now:) -> [AgentSessionState: Int]`
    - `needsYouCount(in:) -> Int`
    - `tableRows(index:filter:now:) -> [SessionTableRow]`
    - `projectName(for:in:) -> String`
    - `menuProjects(index:) -> [SessionProject]`
    - `parent(of:in:) -> AgentSession?`
    - `children(of:in:) -> [AgentSession]`
    - `severityOrder(_:_:)` and `recencyOrder(_:_:)`
    - `visibleSessions`, `isVisible`, `passesNonStateFilters`, `isRecentlyDone` and `matchesSearch`

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Sessions/SessionsLayoutTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("SessionsLayout")
struct SessionsLayoutTests {
    private let now = SessionsFixture.now

    private func ids(_ sessions: [AgentSession]) -> [String] { sessions.map(\.id) }

    @Test func theDefaultFilterHidesScoutRunsAndDoneSessions() throws {
        let index = try SessionsFixture.index()
        let visible = SessionsLayout.visibleSessions(index: index, filter: SessionsFilter(), now: now)
        #expect(Set(ids(visible)) == ["local_A", "local_R", "local_W", "local_C", "local_U", "local_Q",
                                      "cli:aaaaaaaa-0000-0000-0000-000000000007", "local_S"])
    }

    @Test func rowsAreProjectsByRecencyWithCardsBySeverity() throws {
        let index = try SessionsFixture.index()
        let rows = SessionsLayout.rows(index: index, filter: SessionsFilter(), now: now)
        #expect(rows.map(\.name) == ["Example Repo", "other-repo"])
        // needs_you → running → parked (open first, then closed); stale collapses.
        #expect(ids(rows[0].active) == ["local_A", "local_R", "local_C", "local_U"])
        #expect(ids(rows[0].stale) == ["local_S"])
        #expect(rows[0].done.isEmpty)
        #expect(ids(rows[1].active) == ["local_W", "local_Q", "cli:aaaaaaaa-0000-0000-0000-000000000007"])
        #expect(rows[0].counts[.parked] == 2 && rows[0].counts[.stale] == 1)
    }

    @Test func theNowStripIsNeedsYouThenRunningAcrossProjects() throws {
        let index = try SessionsFixture.index()
        #expect(ids(SessionsLayout.nowStrip(index: index, filter: SessionsFilter(), now: now)) == ["local_A", "local_R"])
        var other = SessionsFilter()
        other.projectKey = "/Users/alex/code/other-repo"
        #expect(SessionsLayout.nowStrip(index: index, filter: other, now: now).isEmpty)
    }

    @Test func recentlyDoneShowsOnlyDoneSessionsInsideTheWindow() throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.showRecentlyDone = true
        let rows = SessionsLayout.rows(index: index, filter: filter, now: now)
        // local_D was archived 2.5 h ago; local_M's PR merged 3 days ago (window: 24 h).
        #expect(ids(rows.first { $0.name == "Example Repo" }?.done ?? []) == ["local_D"])
        #expect(rows.first { $0.name == "other-repo" }?.done.isEmpty == true)
        #expect(SessionsLayout.stateCounts(index: index, filter: filter, now: now)[.done] == 1)
        #expect(SessionsLayout.stateCounts(index: index, filter: SessionsFilter(), now: now)[.done] == nil)
    }

    @Test func scoutRunsAppearOnlyWhenAskedFor() throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.showScoutRuns = true
        let rows = SessionsLayout.rows(index: index, filter: filter, now: now)
        #expect(rows.map(\.name).contains("Scout"))
        #expect(SessionsLayout.needsYouCount(in: index) == 1)
    }

    @Test func stateChipsFilterCardsButNotTheirOwnCounts() throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.states = [.parked]
        let rows = SessionsLayout.rows(index: index, filter: filter, now: now)
        #expect(rows.flatMap(\.active).allSatisfy { $0.state == .parked })
        #expect(rows.allSatisfy { $0.stale.isEmpty })
        #expect(SessionsLayout.nowStrip(index: index, filter: filter, now: now).isEmpty)
        let counts = SessionsLayout.stateCounts(index: index, filter: filter, now: now)
        #expect(counts == [.needsYou: 1, .running: 1, .waiting: 1, .parked: 4, .stale: 1])
    }

    @Test(arguments: [
        ("export", ["local_R"]),
        ("#77", ["local_W"]),
        ("w-trace", ["local_S"]),
        ("release checklist", ["cli:aaaaaaaa-0000-0000-0000-000000000007"]),
        ("OTHER-REPO", ["local_W", "local_Q", "cli:aaaaaaaa-0000-0000-0000-000000000007"]),
        ("   ", ["local_A", "local_R", "local_W", "local_C", "local_U", "local_Q",
                 "cli:aaaaaaaa-0000-0000-0000-000000000007", "local_S"]),
    ])
    func searchMatchesTitlePromptBranchPRAndProject(query: String, expected: [String]) throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.search = query
        let found = SessionsLayout.tableRows(index: index, filter: filter, now: now).map(\.id)
        #expect(Set(found) == Set(expected))
    }

    @Test func tableRowsDefaultToSeverityThenRecency() throws {
        let index = try SessionsFixture.index()
        let rows = SessionsLayout.tableRows(index: index, filter: SessionsFilter(), now: now)
        #expect(rows.map(\.id) == ["local_A", "local_R", "local_W", "local_C", "local_U", "local_Q",
                                   "cli:aaaaaaaa-0000-0000-0000-000000000007", "local_S"])
        #expect(rows.map(\.severity) == rows.map(\.severity).sorted())
        #expect(rows.first?.projectName == "Example Repo")
    }

    @Test func sessionsThatTieOnEverythingButIdKeepAStableOrder() throws {
        var object = try SessionsFixture.object()
        var sessions = try #require(object["sessions"] as? [[String: Any]])
        let q = try #require(sessions.first { $0["id"] as? String == "local_Q" })
        var twin = q
        twin["id"] = "local_P"  // sorts before "local_Q"
        sessions.append(twin)
        object["sessions"] = sessions
        let index = try SessionIndex.decode(SessionsFixture.encode(object))
        let a = try #require(SessionsFixture.session("local_P", in: index))
        let b = try #require(SessionsFixture.session("local_Q", in: index))
        #expect(SessionsLayout.severityOrder(a, b))
        #expect(!SessionsLayout.severityOrder(b, a))
        #expect(!SessionsLayout.severityOrder(a, a))
        let row = SessionsLayout.rows(index: index, filter: SessionsFilter(), now: now)
            .first { $0.name == "other-repo" }
        #expect(ids(row?.active ?? []).prefix(3) == ["local_W", "local_P", "local_Q"])
    }

    @Test func parentAndChildrenResolveThroughTheIndex() throws {
        let index = try SessionsFixture.index()
        let child = try #require(SessionsFixture.session("local_C", in: index))
        let parent = try #require(SessionsFixture.session("local_A", in: index))
        #expect(SessionsLayout.parent(of: child, in: index)?.id == "local_A")
        #expect(ids(SessionsLayout.children(of: parent, in: index)) == ["local_C"])
        #expect(SessionsLayout.parent(of: parent, in: index) == nil)
    }

    @Test func anUnknownProjectKeyFallsBackToItsBasename() throws {
        let index = try SessionsFixture.index()
        #expect(SessionsLayout.projectName(for: "/Users/alex/code/zed", in: index) == "zed")
        #expect(SessionsLayout.projectName(for: "/Users/alex/code/example-repo", in: index) == "Example Repo")
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsLayoutTests`

Expected: the build fails with `cannot find 'SessionsLayout' in scope`.

- [ ] **Step 3: Implement the layout**

Create `Scout/Sessions/Models/SessionsLayout.swift`:

```swift
import Foundation

/// What the header's controls select. Shared by the board and the table.
nonisolated struct SessionsFilter: Equatable, Sendable {
    /// Empty means every state.
    var states: Set<AgentSessionState> = []
    var projectKey: String? = nil
    var search: String = ""
    var showScoutRuns: Bool = false
    /// Done sessions appear only when this is on, and then only those active
    /// within the index's `done_visible_hours` (spec §6.1).
    var showRecentlyDone: Bool = false
}

/// One project swimlane on the board.
nonisolated struct SessionRow: Identifiable, Equatable, Sendable {
    /// The project key.
    let id: String
    let name: String
    /// needs_you, running, waiting, parked — in severity order.
    let active: [AgentSession]
    /// Collapsed behind "Stale (n)".
    let stale: [AgentSession]
    /// Collapsed behind "Done (n)"; empty unless recently-done is on.
    let done: [AgentSession]
    let lastActivity: Date?

    var counts: [AgentSessionState: Int] {
        Dictionary(grouping: active + stale + done, by: \.state).mapValues(\.count)
    }
}

/// A table row: the session plus the values its sortable columns compare.
nonisolated struct SessionTableRow: Identifiable, Equatable, Sendable {
    let session: AgentSession
    let projectName: String

    var id: String { session.id }
    var severity: Int { session.state.severity * 2 + (session.state == .parked && !session.isOpen ? 1 : 0) }
    var title: String { session.displayTitle }
    var reason: String { session.primaryReason ?? "" }
    var prNumber: Int { session.pr?.number ?? 0 }
    var lastActivity: Date { session.lastActivityAt ?? .distantPast }
    var model: String { session.model ?? "" }
    var turns: Int { session.turns ?? 0 }
}

/// Pure layout for the Sessions page (spec §6.2–6.3). Every view reads its
/// rows from here, so the rules are tested without rendering anything.
nonisolated enum SessionsLayout {

    // MARK: Filtering

    /// Every rule except the state chips. The chips' counts come from this set,
    /// so selecting one chip does not zero the others.
    static func passesNonStateFilters(
        _ session: AgentSession, filter: SessionsFilter, index: SessionIndex, now: Date
    ) -> Bool {
        if session.isScoutRun && !filter.showScoutRuns { return false }
        if let key = filter.projectKey, session.projectKey != key { return false }
        if session.state == .done && !(filter.showRecentlyDone && isRecentlyDone(session, index: index, now: now)) {
            return false
        }
        return matchesSearch(session, query: filter.search, index: index)
    }

    static func isVisible(_ session: AgentSession, filter: SessionsFilter, index: SessionIndex, now: Date) -> Bool {
        (filter.states.isEmpty || filter.states.contains(session.state))
            && passesNonStateFilters(session, filter: filter, index: index, now: now)
    }

    static func isRecentlyDone(_ session: AgentSession, index: SessionIndex, now: Date) -> Bool {
        guard let last = session.lastActivityAt else { return false }
        return now.timeIntervalSince(last) <= Double(index.display.doneVisibleHours) * 3600
    }

    static func matchesSearch(_ session: AgentSession, query: String, index: SessionIndex) -> Bool {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return true }
        var haystack = [session.displayTitle, projectName(for: session.projectKey, in: index)]
        if let prompt = session.transcript?.firstPrompt { haystack.append(prompt) }
        if let branch = session.worktree?.branch { haystack.append(branch) }
        if let group = session.groupName { haystack.append(group) }
        for pr in session.prs { haystack.append("#\(pr.number)"); haystack.append(pr.repo) }
        return haystack.contains { $0.localizedStandardContains(needle) }
    }

    static func visibleSessions(index: SessionIndex, filter: SessionsFilter, now: Date) -> [AgentSession] {
        index.sessions.filter { isVisible($0, filter: filter, index: index, now: now) }
    }

    // MARK: Ordering

    /// Severity, then open before closed within parked, then most recent
    /// first, then id so equal sessions never swap between refreshes.
    static func severityOrder(_ a: AgentSession, _ b: AgentSession) -> Bool {
        if a.state.severity != b.state.severity { return a.state.severity < b.state.severity }
        if a.state == .parked && a.isOpen != b.isOpen { return a.isOpen }
        let la = a.lastActivityAt ?? .distantPast
        let lb = b.lastActivityAt ?? .distantPast
        if la != lb { return la > lb }
        return a.id < b.id
    }

    static func recencyOrder(_ a: AgentSession, _ b: AgentSession) -> Bool {
        let la = a.lastActivityAt ?? .distantPast
        let lb = b.lastActivityAt ?? .distantPast
        if la != lb { return la > lb }
        return a.id < b.id
    }

    // MARK: Board

    /// One row per project with a visible session, most recently active first.
    static func rows(index: SessionIndex, filter: SessionsFilter, now: Date) -> [SessionRow] {
        let visible = visibleSessions(index: index, filter: filter, now: now)
        let grouped = Dictionary(grouping: visible, by: \.projectKey)
        let rows = grouped.map { key, sessions -> SessionRow in
            let sorted = sessions.sorted(by: severityOrder)
            return SessionRow(
                id: key,
                name: projectName(for: key, in: index),
                active: sorted.filter { $0.state != .stale && $0.state != .done },
                stale: sorted.filter { $0.state == .stale },
                done: sorted.filter { $0.state == .done },
                lastActivity: sessions.compactMap(\.lastActivityAt).max()
            )
        }
        return rows.sorted { a, b in
            let la = a.lastActivity ?? .distantPast
            let lb = b.lastActivity ?? .distantPast
            if la != lb { return la > lb }
            return a.name.localizedStandardCompare(b.name) == .orderedAscending
        }
    }

    /// Needs-you cards, then running cards, across every project (spec §6.2).
    static func nowStrip(index: SessionIndex, filter: SessionsFilter, now: Date) -> [AgentSession] {
        let visible = visibleSessions(index: index, filter: filter, now: now)
        let needsYou = visible.filter { $0.state == .needsYou }.sorted(by: recencyOrder)
        let running = visible.filter { $0.state == .running }.sorted(by: recencyOrder)
        return needsYou + running
    }

    /// Counts for the state chips: every filter but the chips themselves.
    static func stateCounts(index: SessionIndex, filter: SessionsFilter, now: Date) -> [AgentSessionState: Int] {
        var counts: [AgentSessionState: Int] = [:]
        for session in index.sessions where passesNonStateFilters(session, filter: filter, index: index, now: now) {
            counts[session.state, default: 0] += 1
        }
        return counts
    }

    /// The sidebar badge: sessions that need you, never Scout's own runs.
    static func needsYouCount(in index: SessionIndex) -> Int {
        index.sessions.filter { $0.state == .needsYou && !$0.isScoutRun && !$0.isArchived }.count
    }

    // MARK: Table

    static func tableRows(index: SessionIndex, filter: SessionsFilter, now: Date) -> [SessionTableRow] {
        visibleSessions(index: index, filter: filter, now: now)
            .sorted(by: severityOrder)
            .map { SessionTableRow(session: $0, projectName: projectName(for: $0.projectKey, in: index)) }
    }

    // MARK: Lookups

    static func projectName(for key: String, in index: SessionIndex) -> String {
        if let project = index.projects.first(where: { $0.key == key }) { return project.name }
        let base = (key as NSString).lastPathComponent
        return base.isEmpty ? key : base
    }

    /// Every project in the index, by name, for the project menu.
    static func menuProjects(index: SessionIndex) -> [SessionProject] {
        index.projects.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    static func parent(of session: AgentSession, in index: SessionIndex) -> AgentSession? {
        guard let parentID = session.parentSessionID else { return nil }
        return index.sessions.first { $0.id == parentID }
    }

    static func children(of session: AgentSession, in index: SessionIndex) -> [AgentSession] {
        index.sessions.filter { $0.parentSessionID == session.id }.sorted(by: recencyOrder)
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsLayoutTests`

Expected: `Test run with 11 tests in 1 suite passed`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Sessions/Models/SessionsLayout.swift ScoutTests/Sessions/SessionsLayoutTests.swift
git commit -m "feat(sessions): board and table layout — filters, swimlanes, Now strip, counts" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Refresh policy and the filter for the engine's own writes

This task holds the cadence, the two `scoutctl` argument lists, the watch roots (spec §10.4), and the rule that a write by the engine itself never triggers a refresh (index-speed spec §2 and §3.6).

**Files:**
- Create: `Scout/Sessions/SessionsRefresh.swift`
- Test: `ScoutTests/Sessions/SessionsRefreshTests.swift`

**Interfaces:**
- Consumes: nothing new.
- Produces (`nonisolated enum SessionsRefresh`):
  - `Intervals`, with `eventWindow`, `visibleHeartbeat`, `hiddenHeartbeat` and `prInterval`, all of type `Duration`. `.production` sets them to 2 s, 30 s, 300 s and 120 s.
  - `fastArguments(prefix:)`, which returns `prefix + ["session", "index", "--json", "--no-gh"]`.
  - `prArguments(prefix:)`, which returns `prefix + ["session", "index", "--json"]`.
  - `watchRoots(claudeHome:desktopSupport:) -> [URL]` and `productionWatchRoots() -> [URL]`.
  - `isEngineOwnWrite(_ url: URL, cacheDirectory: URL) -> Bool`.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Sessions/SessionsRefreshTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("SessionsRefresh")
struct SessionsRefreshTests {
    private let cache = URL(fileURLWithPath: "/Users/alex/Scout/.scout-cache", isDirectory: true)

    private func url(_ name: String, in dir: URL? = nil) -> URL {
        (dir ?? cache).appendingPathComponent(name)
    }

    @Test func theFastLaneSkipsGhAndThePRLaneDoesNot() {
        #expect(SessionsRefresh.fastArguments(prefix: []) == ["session", "index", "--json", "--no-gh"])
        #expect(SessionsRefresh.prArguments(prefix: ["scoutctl"]) == ["scoutctl", "session", "index", "--json"])
    }

    @Test(arguments: [
        "sessions-index.json",
        "sessions-pr.cache.json",
        "sessions-desktop.cache.json",
        "sessions-transcripts.cache.json",
        ".sessions-index.json.k3j9x2qa.tmp",
        ".sessions-pr.cache.json.ab_12cd9.tmp",
    ])
    func theEnginesOwnWritesAreIgnored(name: String) {
        #expect(SessionsRefresh.isEngineOwnWrite(url(name), cacheDirectory: cache))
    }

    @Test(arguments: [
        "cc-sessions.md",
        "connector-alerts-acked.json",
        "sessions-index.json.bak",
        ".sessions-index.json.tmp",
        "my-sessions-index.json",
    ])
    func otherCacheFilesAreNot(name: String) {
        #expect(!SessionsRefresh.isEngineOwnWrite(url(name), cacheDirectory: cache))
    }

    @Test func theSameNameOutsideTheCacheDirectoryIsNotAnOwnWrite() {
        let elsewhere = URL(fileURLWithPath: "/Users/alex/.claude/projects/-Users-alex-Scout", isDirectory: true)
        #expect(!SessionsRefresh.isEngineOwnWrite(url("sessions-index.json", in: elsewhere), cacheDirectory: cache))
    }

    @Test func aSymlinkedCacheDirectoryStillMatches() throws {
        let fm = FileManager.default
        let base = fm.temporaryDirectory.appendingPathComponent("sessions-refresh-\(UUID().uuidString)")
        let real = base.appendingPathComponent("real/.scout-cache", isDirectory: true)
        try fm.createDirectory(at: real, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: base) }
        let link = base.appendingPathComponent("link")
        try fm.createSymbolicLink(at: link, withDestinationURL: base.appendingPathComponent("real"))
        let viaLink = link.appendingPathComponent(".scout-cache", isDirectory: true)
        #expect(SessionsRefresh.isEngineOwnWrite(real.appendingPathComponent("sessions-index.json"),
                                                 cacheDirectory: viaLink))
    }

    @Test func watchRootsAreTheThreeSourceDirectories() {
        let home = URL(fileURLWithPath: "/Users/alex/.claude", isDirectory: true)
        let support = URL(fileURLWithPath: "/Users/alex/Library/Application Support/Claude", isDirectory: true)
        #expect(SessionsRefresh.watchRoots(claudeHome: home, desktopSupport: support).map(\.path) == [
            "/Users/alex/Library/Application Support/Claude/claude-code-sessions",
            "/Users/alex/.claude/sessions",
            "/Users/alex/.claude/projects",
        ])
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsRefreshTests`

Expected: the build fails with `cannot find 'SessionsRefresh' in scope`.

- [ ] **Step 3: Implement the policy**

Create `Scout/Sessions/SessionsRefresh.swift`. It uses a plain name check rather than a regex. Swift 5 mode does not parse bare `/…/` regex literals, and `Regex` is not `Sendable`.

```swift
import Foundation

/// When and how the Sessions page rebuilds the index (spec §3, addendum §10).
///
/// Two lanes, each single-flight:
/// - **fast** — `session index --json --no-gh`: on source-file events (at most
///   one build per `eventWindow` while events keep arriving), on a heartbeat,
///   and after every PR build. PR state comes from the engine's PR cache.
/// - **PR** — `session index --json` with `gh`: every `prInterval` while the
///   app runs and when the page appears. Up to ~30 s while PRs are due, so it
///   never blocks the fast lane, and its index is never published: by the time
///   it is written its liveness is that old. It only refreshes the PR cache.
nonisolated enum SessionsRefresh {

    struct Intervals: Equatable, Sendable {
        /// Window that coalesces file events into one build.
        var eventWindow: Duration
        /// Fast build while the page is visible, so `running` (which expires
        /// 120 s after the last activity with no file event to announce it)
        /// and relative ages stay current.
        var visibleHeartbeat: Duration
        /// Fast build while the page is hidden, for the sidebar badge.
        var hiddenHeartbeat: Duration
        /// PR build cadence. The engine's 10-minute TTL decides what is
        /// actually fetched, so a run with nothing due costs a fast build.
        var prInterval: Duration

        static let production = Intervals(
            eventWindow: .seconds(2),
            visibleHeartbeat: .seconds(30),
            hiddenHeartbeat: .seconds(300),
            prInterval: .seconds(120)
        )
    }

    static func fastArguments(prefix: [String]) -> [String] {
        prefix + ["session", "index", "--json", "--no-gh"]
    }

    static func prArguments(prefix: [String]) -> [String] {
        prefix + ["session", "index", "--json"]
    }

    /// The directories whose changes can change the index. Not the Claude
    /// Application Support root — the desktop app writes caches and logs there
    /// constantly — and not the vault's `.scout-cache/`, which holds only the
    /// engine's own output (group and lease changes arrive on the heartbeat).
    static func watchRoots(claudeHome: URL, desktopSupport: URL) -> [URL] {
        [
            desktopSupport.appendingPathComponent("claude-code-sessions", isDirectory: true),
            claudeHome.appendingPathComponent("sessions", isDirectory: true),
            claudeHome.appendingPathComponent("projects", isDirectory: true),
        ]
    }

    static func productionWatchRoots() -> [URL] {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return watchRoots(
            claudeHome: home.appendingPathComponent(".claude", isDirectory: true),
            desktopSupport: home.appendingPathComponent("Library/Application Support/Claude", isDirectory: true)
        )
    }

    /// True for a write the engine itself makes under the vault's
    /// `.scout-cache/` (1b spec §3.6): `sessions-index.json`, every
    /// `sessions-*.cache.json`, and the `.<name>.<random>.tmp` files its atomic
    /// writes go through. A refresh must never be triggered by one of these, or
    /// every build would schedule the next.
    static func isEngineOwnWrite(_ url: URL, cacheDirectory: URL) -> Bool {
        let parent = url.deletingLastPathComponent().resolvingSymlinksInPath().standardizedFileURL.path
        let cache = cacheDirectory.resolvingSymlinksInPath().standardizedFileURL.path
        guard parent == cache else { return false }
        var name = Substring(url.lastPathComponent)
        if name.hasPrefix("."), name.hasSuffix(".tmp") {
            // `.sessions-index.json.k3j9x2qa.tmp` → `sessions-index.json`
            name = name.dropFirst().dropLast(4)
            guard let dot = name.lastIndex(of: ".") else { return false }
            name = name[..<dot]
        }
        return name == "sessions-index.json"
            || (name.hasPrefix("sessions-") && name.hasSuffix(".cache.json"))
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsRefreshTests`

Expected: `Test run with 6 tests in 1 suite passed`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Sessions/SessionsRefresh.swift ScoutTests/Sessions/SessionsRefreshTests.swift
git commit -m "feat(sessions): refresh cadence, watch roots, ignore the engine's own writes" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `SessionIndexService` — two lanes, heartbeat, watches

The service decides when to run the engine and what to publish.

- **The fast lane** is single-flight. Requests that arrive while a build runs collapse into one follow-up build.
- **The PR lane** is single-flight too. A request while a PR build runs is ignored. When a PR build ends, a fast build is requested. The PR build's own index is never published: only its `gh` errors and its refresh count are kept.
- **On launch** (`start()`), it shows the last index on disk, runs a fast build, sets the heartbeat to 5 minutes, and runs the PR lane every 2 minutes.
- **While visible** (`setVisible(true)`), it watches the three source roots through `DebouncedFileEvents`, drops the engine's own writes, refreshes immediately in both lanes, and sets the heartbeat to 30 seconds.
- **On failure**, the last good index stays on screen with a message.
  - Exit 2 with "No such command" means the engine is too old.
  - Exit 127, or a failed launch, means `scoutctl` is missing.
  - In both cases there are no PR builds until a fast build succeeds again.
- **`index`** is assigned only when the content changed. `needsYouCount` is assigned only when it changed.

**Files:**
- Create: `Scout/Sessions/SessionIndexService.swift`
- Test: `ScoutTests/Sessions/SessionIndexServiceTests.swift`

**Interfaces:**
- Consumes:
  - `ProcessRunner` and `ProcessResult`, `FileSystemEventSource` and `FileSystemEvent`, `DebouncedFileEvents`, `ClockSource` and `SystemClock`.
  - `ScheduleService.previewBytes(_:max:)` and `ScheduleService.formatDecodeFailure(stdout:stderr:)`. These are static methods on the main actor.
  - `SessionIndex`, `SessionsLayout.needsYouCount(in:)` and `SessionsRefresh`.
  - Tests use `InjectableFS` (`ScoutTests/ActionItems/ActionItemsDocumentServiceTests.swift`) and `NoopFS` (`ScoutTests/Services/UsageTrackerServiceTests.swift`).
- Produces `@MainActor final class SessionIndexService: ObservableObject`:
  - Nested types:
    - `Availability`: `.ok`, `.engineMissing`, `.engineTooOld`, `.unsupportedSchema(Int)`.
    - `PRStatus`: `finishedAt`, `refreshed` and `errors`.
    - `Configuration`: `scoutctl`, `argumentsPrefix`, `runner`, `fileEvents`, `indexFile`, `watchRoots`, `intervals` and `clock`.
    - `ExitProblem`.
  - Published properties: `index: SessionIndex?`, `availability`, `lastError: String?`, `prStatus: PRStatus?` and `needsYouCount: Int`.
  - `lastRefreshAt: Date?`, which is not published.
  - Lifecycle: `start()`, `setVisible(_:)` and `stop()`.
  - Lanes: `requestFast()`, `refreshFast() async`, `requestPRs()` and `refreshPRs() async`.
  - Message helpers: `classifyExit(_:)`, `describeExit(_:)`, `describeLaunchFailure(_:)` and `describeDecodeFailure(_:stdout:stderr:)`.
  - Test support: `ScriptedSessionsRunner` (an actor that answers each lane separately and can hold either one), `ProcessResult.ok(_:)` and `.failed(_:stderr:)`, and `FixedSessionsClock`.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Sessions/SessionIndexServiceTests.swift`:

```swift
import Combine
import Foundation
import Testing
@testable import Scout

/// Answers `scoutctl session index` calls: `--no-gh` ones from `fast`, the
/// others from `pr`. Either lane can be held open to prove the other one does
/// not wait for it.
actor ScriptedSessionsRunner: ProcessRunner {
    private(set) var calls: [[String]] = []
    private var fast: ProcessResult
    private var pr: ProcessResult
    private var holdingFast = false
    private var holdingPR = false
    private var fastWaiters: [CheckedContinuation<Void, Never>] = []
    private var prWaiters: [CheckedContinuation<Void, Never>] = []

    init(fast: ProcessResult, pr: ProcessResult? = nil) {
        self.fast = fast
        self.pr = pr ?? fast
    }

    var fastCalls: Int { calls.filter { $0.contains("--no-gh") }.count }
    var prCalls: Int { calls.filter { !$0.contains("--no-gh") }.count }
    /// Appearing runs one fast build and one PR build, which a fast build follows.
    var settledAfterAppearing: Bool { prCalls == 1 && fastCalls >= 2 }

    func setFast(_ result: ProcessResult) { fast = result }
    func holdFast() { holdingFast = true }
    func holdPR() { holdingPR = true }
    func releaseFast() { holdingFast = false; fastWaiters.forEach { $0.resume() }; fastWaiters = [] }
    func releasePR() { holdingPR = false; prWaiters.forEach { $0.resume() }; prWaiters = [] }

    nonisolated func run(
        executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?
    ) async throws -> ProcessResult {
        await answer(arguments)
    }

    private func answer(_ arguments: [String]) async -> ProcessResult {
        calls.append(arguments)
        if arguments.contains("--no-gh") {
            if holdingFast { await withCheckedContinuation { fastWaiters.append($0) } }
            return fast
        }
        if holdingPR { await withCheckedContinuation { prWaiters.append($0) } }
        return pr
    }
}

@MainActor
@Suite("SessionIndexService", .serialized)
struct SessionIndexServiceTests {

    private nonisolated static let quick = SessionsRefresh.Intervals(
        eventWindow: .milliseconds(10),
        visibleHeartbeat: .seconds(3600),
        hiddenHeartbeat: .seconds(3600),
        prInterval: .seconds(3600)
    )

    @MainActor
    private struct Harness {
        let service: SessionIndexService
        let runner: ScriptedSessionsRunner
        let events: InjectableFS
        let vault: URL
        let roots: [URL]

        func tearDown() {
            service.stop()
            try? FileManager.default.removeItem(at: vault)
        }
    }

    private func harness(
        runner: ScriptedSessionsRunner,
        intervals: SessionsRefresh.Intervals = quick,
        prefix: [String] = []
    ) throws -> Harness {
        let fm = FileManager.default
        let vault = fm.temporaryDirectory.appendingPathComponent("sessions-service-\(UUID().uuidString)")
        let cache = vault.appendingPathComponent(".scout-cache", isDirectory: true)
        let roots = ["claude-code-sessions", "sessions", "projects"].map {
            vault.appendingPathComponent("sources/\($0)", isDirectory: true)
        }
        for dir in [cache] + roots { try fm.createDirectory(at: dir, withIntermediateDirectories: true) }
        let events = InjectableFS()
        let service = SessionIndexService(configuration: .init(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            argumentsPrefix: prefix,
            runner: runner,
            fileEvents: events,
            indexFile: cache.appendingPathComponent("sessions-index.json"),
            watchRoots: roots,
            intervals: intervals,
            clock: FixedSessionsClock()
        ))
        return Harness(service: service, runner: runner, events: events, vault: vault, roots: roots)
    }

    /// Poll an actor-backed condition; liveness, not latency, so the budget is generous.
    private func eventually(_ timeout: Duration = .seconds(20), _ condition: () async -> Bool) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now + timeout
        while clock.now < deadline {
            if await condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return await condition()
    }

    private func fixtureVariant(_ change: (inout [String: Any]) -> Void) throws -> Data {
        var object = try SessionsFixture.object()
        change(&object)
        return try SessionsFixture.encode(object)
    }

    // MARK: Publishing

    @Test func aFastBuildPublishesTheIndexAndTheBadge() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner, prefix: ["scoutctl"])
        defer { h.tearDown() }
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.count == 11)
        #expect(h.service.needsYouCount == 1)
        #expect(h.service.availability == .ok && h.service.lastError == nil)
        #expect(h.service.lastRefreshAt == FixedSessionsClock.instant)
        #expect(await runner.calls == [["scoutctl", "session", "index", "--json", "--no-gh"]])
    }

    @Test func aRebuildWithTheSameContentDoesNotRepublish() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        var publishes = 0
        let watch = h.service.$index.dropFirst().sink { _ in publishes += 1 }
        defer { watch.cancel() }
        await runner.setFast(.ok(try fixtureVariant { $0["generated_at"] = "2026-09-15T12:00:02Z" }))
        await h.service.refreshFast()
        #expect(publishes == 0)
        await runner.setFast(.ok(try fixtureVariant { object in
            var sessions = object["sessions"] as? [[String: Any]] ?? []
            sessions[0]["state_reasons"] = ["CI failing"]
            object["sessions"] = sessions
        }))
        await h.service.refreshFast()
        #expect(publishes == 1)
        #expect(h.service.index?.sessions.first?.stateReasons == ["CI failing"])
    }

    // MARK: Lanes

    @Test func requestsDuringABuildCollapseIntoOneFollowUp() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await runner.holdFast()
        h.service.requestFast()
        #expect(await eventually { await runner.fastCalls == 1 })
        for _ in 0..<5 { h.service.requestFast() }
        await runner.releaseFast()
        await h.service.refreshFast()
        #expect(await runner.fastCalls == 2)
    }

    @Test func thePRLaneNeverBlocksTheFastLaneAndIsNeverPublished() async throws {
        let prIndex = try fixtureVariant { object in
            object["source_counts"] = ["desktop": 10, "cli_only": 1, "open": 3, "running": 1, "prs_refreshed": 3]
            object["source_errors"] = [["source": "gh", "message": "pr view failed: example-org/example-repo#110"]]
            var sessions = object["sessions"] as? [[String: Any]] ?? []
            sessions[0]["title"] = "A 30-second-old snapshot"
            object["sessions"] = sessions
        }
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()), pr: .ok(prIndex))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await runner.holdPR()
        h.service.requestPRs()
        #expect(await eventually { await runner.prCalls == 1 })
        h.service.requestPRs()  // single-flight: ignored while one runs
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.count == 11)
        #expect(await runner.prCalls == 1)

        await runner.releasePR()
        await h.service.refreshPRs()
        #expect(h.service.prStatus?.refreshed == 3)
        #expect(h.service.prStatus?.errors.map(\.source) == ["gh"])
        // The PR build's own index is never shown; a fast build follows it.
        #expect(await eventually { await runner.fastCalls == 2 })
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.first?.title == "Fix the parser")
    }

    // MARK: Failures

    @Test func aFailedBuildKeepsTheLastIndexAndSaysWhy() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        await runner.setFast(.failed(1, stderr: "session index: could not write the index: disk full"))
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.count == 11)
        #expect(h.service.availability == .ok)
        let message = h.service.lastError ?? ""
        #expect(message.contains("exited 1") && message.contains("disk full"), "got: \(message)")
    }

    @Test func aFailedFirstBuildShowsTheIndexOnDisk() async throws {
        let runner = ScriptedSessionsRunner(fast: .failed(1, stderr: "boom"))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        try SessionsFixture.data().write(to: h.vault.appendingPathComponent(".scout-cache/sessions-index.json"))
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.count == 11)
        #expect(h.service.lastError?.contains("boom") == true)
    }

    @Test func anEngineWithoutSessionIndexIsTooOldAndSkipsPRBuilds() async throws {
        let runner = ScriptedSessionsRunner(fast: .failed(2, stderr: "Usage: scoutctl session [OPTIONS] COMMAND\nError: No such command 'index'."))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        #expect(h.service.availability == .engineTooOld)
        #expect(h.service.lastError?.contains("0.11.0") == true)
        h.service.requestPRs()
        await h.service.refreshPRs()
        #expect(await runner.prCalls == 0)
    }

    @Test func scoutctlMissingFromPathIsReported() async throws {
        let runner = ScriptedSessionsRunner(fast: .failed(127, stderr: "env: scoutctl: No such file or directory"))
        let h = try harness(runner: runner, prefix: ["scoutctl"])
        defer { h.tearDown() }
        await h.service.refreshFast()
        #expect(h.service.availability == .engineMissing)
        #expect(h.service.lastError?.contains("scoutctl not found") == true)
    }

    @Test func aScoutctlThatCannotBeLaunchedIsReported() async throws {
        let fm = FileManager.default
        let vault = fm.temporaryDirectory.appendingPathComponent("sessions-service-\(UUID().uuidString)")
        defer { try? fm.removeItem(at: vault) }
        let service = SessionIndexService(configuration: .init(
            scoutctl: vault.appendingPathComponent("no-such-scoutctl"),
            argumentsPrefix: [],
            runner: SystemProcessRunner(),
            fileEvents: NoopFS(),
            indexFile: vault.appendingPathComponent(".scout-cache/sessions-index.json"),
            watchRoots: [],
            intervals: Self.quick
        ))
        await service.refreshFast()
        #expect(service.availability == .engineMissing)
        #expect(service.lastError?.contains("scoutctl not found") == true, "got: \(service.lastError ?? "nil")")
    }

    @Test func anUnsupportedSchemaKeepsTheLastIndex() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        await runner.setFast(.ok(try fixtureVariant { $0["schema_version"] = 2 }))
        await h.service.refreshFast()
        #expect(h.service.availability == .unsupportedSchema(2))
        #expect(h.service.index?.sessions.count == 11)
    }

    @Test func outputThatIsNotJSONIsReportedWithASnippet() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(Data("Traceback (most recent call last):".utf8)))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        #expect(h.service.index == nil)
        #expect(h.service.lastError?.contains("Traceback") == true)
    }

    // MARK: Watching

    @Test func fileEventsRefreshButTheEnginesOwnWritesDoNot() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        h.service.setVisible(true)
        #expect(await eventually { await runner.settledAfterAppearing })
        await h.service.refreshFast()
        let settled = await runner.fastCalls

        let cache = h.vault.appendingPathComponent(".scout-cache", isDirectory: true)
        for name in ["sessions-index.json", ".sessions-index.json.k3j9x2qa.tmp", "sessions-pr.cache.json"] {
            h.events.emit(FileSystemEvent(url: cache.appendingPathComponent(name), kind: .modified))
        }
        try await Task.sleep(for: .milliseconds(300))
        #expect(await runner.fastCalls == settled)

        h.events.emit(FileSystemEvent(url: h.roots[2].appendingPathComponent("-Users-alex-code-example-repo/x.jsonl"),
                                      kind: .modified))
        #expect(await eventually { await runner.fastCalls > settled })
    }

    @Test func hidingThePageStopsWatching() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        h.service.setVisible(true)
        #expect(await eventually { await runner.settledAfterAppearing })
        h.service.setVisible(false)
        await h.service.refreshFast()
        let settled = await runner.fastCalls
        h.events.emit(FileSystemEvent(url: h.roots[1].appendingPathComponent("123.json"), kind: .created))
        try await Task.sleep(for: .milliseconds(300))
        #expect(await runner.fastCalls == settled)
    }

    @Test func theHeartbeatRebuildsWithoutAnyFileEvent() async throws {
        var intervals = Self.quick
        intervals.visibleHeartbeat = .milliseconds(30)
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner, intervals: intervals)
        defer { h.tearDown() }
        h.service.start()
        h.service.setVisible(true)
        #expect(await eventually { await runner.fastCalls >= 5 })
    }

    @Test func startIsIdempotent() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        h.service.start()
        h.service.start()
        #expect(await eventually { await runner.prCalls == 1 })
        try await Task.sleep(for: .milliseconds(100))
        #expect(await runner.prCalls == 1)
    }

    // MARK: Messages

    @Test func exitClassification() {
        #expect(SessionIndexService.classifyExit(.ok(Data())) == nil)
        #expect(SessionIndexService.classifyExit(.failed(2, stderr: "Error: No such command 'index'.")) == .engineTooOld)
        #expect(SessionIndexService.classifyExit(.failed(2, stderr: "Error: No such option: --json")) == .failed)
        #expect(SessionIndexService.classifyExit(.failed(127, stderr: "")) == .engineMissing)
        #expect(SessionIndexService.classifyExit(.failed(1, stderr: "")) == .failed)
    }
}

extension ProcessResult {
    static func ok(_ data: Data) -> ProcessResult { ProcessResult(exitCode: 0, stdout: data, stderr: Data()) }
    static func failed(_ code: Int32, stderr: String) -> ProcessResult {
        ProcessResult(exitCode: code, stdout: Data(), stderr: Data(stderr.utf8))
    }
}

struct FixedSessionsClock: ClockSource {
    static let instant = SessionsFixture.now
    func now() -> Date { Self.instant }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionIndexServiceTests`

Expected: the build fails with `cannot find 'SessionIndexService' in scope`.

- [ ] **Step 3: Implement the service**

Create `Scout/Sessions/SessionIndexService.swift`:

```swift
import Foundation
import Combine

/// Keeps the Sessions page's copy of `sessions-index.json` current by running
/// `scoutctl session index` (spec §6.5, addendum §10). The engine does all the
/// parsing and derivation; this service decides *when* to ask, decodes the
/// answer off the main actor, and publishes it only when it changed.
@MainActor
final class SessionIndexService: ObservableObject {

    enum Availability: Equatable {
        case ok
        /// `scoutctl` could not be run at all.
        case engineMissing
        /// `scoutctl` has no `session index` (scout-plugin older than 0.11.0).
        case engineTooOld
        /// The index declares a schema this build cannot read.
        case unsupportedSchema(Int)
    }

    /// The outcome of the last PR build, for the header's PR line.
    struct PRStatus: Equatable {
        let finishedAt: Date
        let refreshed: Int
        let errors: [SessionSourceError]
    }

    struct Configuration {
        var scoutctl: URL
        var argumentsPrefix: [String]
        var runner: any ProcessRunner
        var fileEvents: any FileSystemEventSource
        /// `<vault>/.scout-cache/sessions-index.json`
        var indexFile: URL
        var watchRoots: [URL]
        var intervals: SessionsRefresh.Intervals = .production
        var clock: any ClockSource = SystemClock()

        var cacheDirectory: URL { indexFile.deletingLastPathComponent() }
    }

    /// Republished only when the content (everything but `generated_at`) changes.
    @Published private(set) var index: SessionIndex?
    @Published private(set) var availability: Availability = .ok
    @Published private(set) var lastError: String?
    @Published private(set) var prStatus: PRStatus?
    /// The sidebar badge. Separate from `index` so AppState can forward it
    /// without re-rendering the whole window on every refresh.
    @Published private(set) var needsYouCount = 0
    /// When the last fast build finished. Not `@Published`: it changes every
    /// build; the header reads it from a `TimelineView`.
    private(set) var lastRefreshAt: Date?

    private let config: Configuration
    private var started = false
    private var isVisible = false
    private var fastTask: Task<Void, Never>?
    private var fastPending = false
    private var prTask: Task<Void, Never>?
    private var heartbeatTask: Task<Void, Never>?
    private var prLoopTask: Task<Void, Never>?
    private var watchTasks: [Task<Void, Never>] = []

    init(configuration: Configuration) {
        self.config = configuration
    }

    // MARK: Lifecycle

    /// App launch: show the last index on disk, then keep it current at the
    /// hidden cadence (for the badge) and refresh PR state in the background.
    func start() {
        guard !started else { return }
        started = true
        Task { await loadIndexFile() }
        requestFast()
        restartHeartbeat()
        prLoopTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.requestPRs()
                try? await Task.sleep(for: self.config.intervals.prInterval)
            }
        }
    }

    /// The page appeared or disappeared. Visible: watch the sources, refresh
    /// now, heartbeat every 30 s. Hidden: no watches, heartbeat every 5 min.
    func setVisible(_ visible: Bool) {
        guard visible != isVisible else { return }
        isVisible = visible
        if visible {
            subscribeToWatchRoots()
            requestFast()
            requestPRs()
        } else {
            watchTasks.forEach { $0.cancel() }
            watchTasks = []
        }
        restartHeartbeat()
    }

    func stop() {
        started = false
        isVisible = false
        heartbeatTask?.cancel()
        prLoopTask?.cancel()
        watchTasks.forEach { $0.cancel() }
        heartbeatTask = nil
        prLoopTask = nil
        watchTasks = []
    }

    // MARK: Lanes

    /// Ask for a fast build. While one runs, further requests collapse into a
    /// single follow-up build.
    func requestFast() {
        if fastTask != nil {
            fastPending = true
            return
        }
        fastTask = Task { [weak self] in
            guard let self else { return }
            repeat {
                self.fastPending = false
                await self.runFastBuild()
            } while self.fastPending
            self.fastTask = nil
        }
    }

    /// Request a fast build and wait until the build that includes it finishes.
    func refreshFast() async {
        requestFast()
        await fastTask?.value
    }

    /// Ask for a PR build unless one is running. It never waits on, or blocks,
    /// the fast lane; when it ends, a fast build picks up the new PR cache.
    func requestPRs() {
        guard prTask == nil, availability != .engineTooOld, availability != .engineMissing else { return }
        prTask = Task { [weak self] in
            guard let self else { return }
            await self.runPRBuild()
            self.prTask = nil
            self.requestFast()
        }
    }

    /// Request a PR build and wait for it (not for the fast build it triggers).
    func refreshPRs() async {
        requestPRs()
        await prTask?.value
    }

    // MARK: Builds

    private func runFastBuild() async {
        let result: ProcessResult
        do {
            result = try await config.runner.run(
                executable: config.scoutctl,
                arguments: SessionsRefresh.fastArguments(prefix: config.argumentsPrefix),
                environment: [:],
                workingDirectory: nil
            )
        } catch {
            availability = .engineMissing
            lastError = Self.describeLaunchFailure(error)
            return
        }
        if let problem = Self.classifyExit(result) {
            switch problem {
            case .engineTooOld:  availability = .engineTooOld
            case .engineMissing: availability = .engineMissing
            default:             break
            }
            lastError = Self.describeExit(result)
            await loadIndexFile()  // the last index on disk still renders
            return
        }
        switch await Self.decodeDetached(result.stdout) {
        case .success(let decoded):
            availability = .ok
            lastError = nil
            lastRefreshAt = config.clock.now()
            publish(decoded)
        case .failure(.unsupportedSchema(let version)):
            availability = .unsupportedSchema(version)  // keep the last good index
        case .failure(let error):
            lastError = Self.describeDecodeFailure(error, stdout: result.stdout, stderr: result.stderr)
        }
    }

    private func runPRBuild() async {
        let result: ProcessResult
        do {
            result = try await config.runner.run(
                executable: config.scoutctl,
                arguments: SessionsRefresh.prArguments(prefix: config.argumentsPrefix),
                environment: [:],
                workingDirectory: nil
            )
        } catch {
            prStatus = PRStatus(finishedAt: config.clock.now(), refreshed: 0,
                                errors: [SessionSourceError(source: "gh", message: Self.describeLaunchFailure(error))])
            return
        }
        guard result.exitCode == 0, case .success(let decoded) = await Self.decodeDetached(result.stdout) else {
            prStatus = PRStatus(finishedAt: config.clock.now(), refreshed: 0,
                                errors: [SessionSourceError(source: "gh", message: Self.describeExit(result))])
            return
        }
        // Deliberately not published: see SessionsRefresh.
        prStatus = PRStatus(
            finishedAt: config.clock.now(),
            refreshed: decoded.sourceCounts["prs_refreshed"] ?? 0,
            errors: decoded.sourceErrors.filter { $0.source == "gh" }
        )
    }

    private func publish(_ decoded: SessionIndex) {
        let count = SessionsLayout.needsYouCount(in: decoded)
        if count != needsYouCount { needsYouCount = count }
        if let current = index, current.hasSameContent(as: decoded) { return }
        index = decoded
    }

    /// Show what is on disk while nothing else is shown — at launch, or when
    /// the first build fails. Never over a newer build's index: the file may
    /// be a PR build's older snapshot.
    private func loadIndexFile() async {
        let url = config.indexFile
        guard let data = await Task.detached(priority: .utility, operation: { try? Data(contentsOf: url) }).value,
              case .success(let decoded) = await Self.decodeDetached(data),
              index == nil else { return }
        publish(decoded)
    }

    nonisolated private static func decodeDetached(_ data: Data) async -> Result<SessionIndex, SessionIndexError> {
        await Task.detached(priority: .utility) {
            do {
                return .success(try SessionIndex.decode(data))
            } catch let error as SessionIndexError {
                return .failure(error)
            } catch {
                return .failure(.malformed(String(describing: error)))
            }
        }.value
    }

    // MARK: Scheduling

    private func restartHeartbeat() {
        heartbeatTask?.cancel()
        guard started else { return }
        let interval = isVisible ? config.intervals.visibleHeartbeat : config.intervals.hiddenHeartbeat
        heartbeatTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: interval)
                guard !Task.isCancelled, let self else { return }
                self.requestFast()
            }
        }
    }

    private func subscribeToWatchRoots() {
        let source = DebouncedFileEvents(base: config.fileEvents, interval: config.intervals.eventWindow)
        let cacheDirectory = config.cacheDirectory
        for root in config.watchRoots where FileManager.default.fileExists(atPath: root.path) {
            let stream = source.events(for: root)
            watchTasks.append(Task { [weak self] in
                for await event in stream {
                    if SessionsRefresh.isEngineOwnWrite(event.url, cacheDirectory: cacheDirectory) { continue }
                    self?.requestFast()
                }
            })
        }
    }

    // MARK: Messages

    nonisolated enum ExitProblem: Equatable, Sendable { case engineTooOld, engineMissing, failed }

    nonisolated static func classifyExit(_ result: ProcessResult) -> ExitProblem? {
        guard result.exitCode != 0 else { return nil }
        let stderr = String(data: result.stderr, encoding: .utf8) ?? ""
        if result.exitCode == 2 && stderr.contains("No such command") { return .engineTooOld }
        if result.exitCode == 127 { return .engineMissing }  // `/usr/bin/env scoutctl` with no scoutctl on PATH
        return .failed
    }

    static func describeExit(_ result: ProcessResult) -> String {
        switch classifyExit(result) {
        case .engineTooOld:
            return "This scout-plugin has no `session index` — update it to 0.11.0 or later (`/scout-update`)."
        case .engineMissing:
            return "scoutctl not found — check that scout-plugin is installed."
        default:
            let detail = ScheduleService.previewBytes(result.stderr, max: 200)
            return "`scoutctl session index` exited \(result.exitCode)" + (detail.isEmpty ? "." : ": \(detail)")
        }
    }

    static func describeLaunchFailure(_ error: Error) -> String {
        let text = String(describing: error)
        if text.contains("ENOENT") || text.contains("No such file") || text.contains("doesn’t exist") {
            return "scoutctl not found — check that scout-plugin is installed."
        }
        return "Couldn't run scoutctl: \(text.prefix(160))"
    }

    static func describeDecodeFailure(_ error: SessionIndexError, stdout: Data, stderr: Data) -> String {
        if case .malformed(let detail) = error, !stdout.isEmpty, stdout.first == UInt8(ascii: "{") {
            return "The session index didn't match this app's schema: \(detail.prefix(160))"
        }
        return ScheduleService.formatDecodeFailure(stdout: stdout, stderr: stderr)
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionIndexServiceTests`

Expected: `Test run with 16 tests in 1 suite passed`.

These tests use real tasks and real timing, so check them for flakiness:

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionIndexServiceTests -test-iterations 10 -run-tests-until-failure`

Expected: `Test run with 160 tests` passed.

- [ ] **Step 5: Commit**

```bash
git add Scout/Sessions/SessionIndexService.swift ScoutTests/Sessions/SessionIndexServiceTests.swift
git commit -m "feat(sessions): SessionIndexService — fast and PR lanes, heartbeat, watches" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `ClaudeLauncher` resume

"Resume in terminal" runs `claude --resume <cli_session_id>` in the user's configured terminal (spec §6.4). The arguments go through every CLI path:
- tmux, as separate argv entries;
- Terminal.app and iTerm2, shell-quoted;
- Ghostty, through its generated script;
- a custom command, inside `{claude}`.

Resume must not overwrite the clipboard the way the action-item launch does. The action-item output must stay byte-identical.

**Files:**
- Modify: `Scout/Utilities/ClaudeLauncher.swift`
- Test: `ScoutTests/Sessions/ClaudeLauncherResumeTests.swift`

**Interfaces:**
- Consumes: the existing `CLIConfig`, `CLITerminal` and `shellQuote(_:)`.
- Produces:
  - `ClaudeLauncher.CLIPurpose`, with cases `.actionItem` and `.resume(cliSessionID: String)`, and `arguments: [String]`.
  - `ClaudeLauncher.resume(cliSessionID:cwd:config:) throws`.
  - These builders gain a `purpose: CLIPurpose = .actionItem` parameter: `makeTerminalShellCommand`, `makeTerminalAppScript`, `makeITermScript`, `makeTmuxNewWindowArguments(session:claudePath:cwd:purpose:)` (new) and `makeGhosttyScript(claudePath:cwd:purpose:)` (now internal).
  - `expandCustomCommand` gains `arguments: [String] = []`.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Sessions/ClaudeLauncherResumeTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

/// `--resume <id>` on every CLI terminal path (spec §6.4 "Resume in terminal").
@Suite("ClaudeLauncher resume")
struct ClaudeLauncherResumeTests {
    private let id = "aaaaaaaa-0000-0000-0000-000000000001"
    private var resume: ClaudeLauncher.CLIPurpose { .resume(cliSessionID: id) }

    @Test func resumeArgumentsAreTheFlagAndTheID() {
        #expect(resume.arguments == ["--resume", id])
        #expect(ClaudeLauncher.CLIPurpose.actionItem.arguments.isEmpty)
    }

    @Test func terminalAndITermExecClaudeWithTheResumeFlag() {
        let cmd = ClaudeLauncher.makeTerminalShellCommand(claudePath: "/cl", cwd: "/w", purpose: resume)
        #expect(cmd.hasPrefix("cd \"/w\" && clear && "))
        #expect(cmd.hasSuffix("exec \"/cl\" '--resume' '\(id)'"))
        #expect(cmd.contains("resuming a Claude Code session"))
        #expect(!cmd.contains("clipboard"))
        #expect(ClaudeLauncher.makeTerminalAppScript(claudePath: "/cl", cwd: "/w", purpose: resume)
            .contains("'--resume' '\(id)'"))
        #expect(ClaudeLauncher.makeITermScript(claudePath: "/cl", cwd: "/w", purpose: resume)
            .contains("'--resume' '\(id)'"))
    }

    @Test func theActionItemCommandIsUnchanged() {
        #expect(ClaudeLauncher.makeTerminalShellCommand(claudePath: "/cl", cwd: "/w")
            == "cd \"/w\" && clear && "
            + "echo 'Scout: action-item context copied to your clipboard. Paste with Cmd+V.' && "
            + "exec \"/cl\"")
    }

    @Test func tmuxPassesTheFlagAsSeparateArgv() {
        #expect(ClaudeLauncher.makeTmuxNewWindowArguments(session: "main", claudePath: "/cl", cwd: "/w", purpose: resume)
            == ["new-window", "-t", "main:", "-c", "/w", "-n", "claude", "/cl", "--resume", id])
        #expect(ClaudeLauncher.makeTmuxNewWindowArguments(session: "main", claudePath: "/cl", cwd: "/w")
            == ["new-window", "-t", "main:", "-c", "/w", "-n", "claude", "/cl"])
    }

    @Test func ghosttyScriptsResumeAndKeepTheActionItemText() {
        let resumed = ClaudeLauncher.makeGhosttyScript(claudePath: "/cl", cwd: URL(fileURLWithPath: "/w"), purpose: resume)
        #expect(resumed.hasSuffix("exec \"/cl\" '--resume' '\(id)'"))
        #expect(!resumed.contains("clipboard"))
        #expect(ClaudeLauncher.makeGhosttyScript(claudePath: "/cl", cwd: URL(fileURLWithPath: "/w")) == """
        #!/bin/bash
        cd "/w" || exit 1
        clear
        echo "Scout: action-item context copied to your clipboard."
        echo "When Claude prompts you, paste (Cmd+V) and press Enter to send."
        echo
        exec "/cl"
        """)
    }

    @Test func aCustomCommandGetsTheArgumentsInsideClaude() {
        #expect(ClaudeLauncher.expandCustomCommand(
            template: "kitty -d {cwd} -e {claude}", claudePath: "/opt/claude", cwd: "/w", arguments: resume.arguments)
            == "kitty -d '/w' -e '/opt/claude' '--resume' '\(id)'")
    }

    @Test func aHostileIDStaysOneArgument() {
        let hostile = ClaudeLauncher.CLIPurpose.resume(cliSessionID: "x'; rm -rf ~; '")
        let cmd = ClaudeLauncher.makeTerminalShellCommand(claudePath: "/cl", cwd: "/w", purpose: hostile)
        #expect(cmd.hasSuffix("'--resume' 'x'\\''; rm -rf ~; '\\'''"))
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ClaudeLauncherResumeTests`

Expected: the build fails with `type 'ClaudeLauncher' has no member 'CLIPurpose'`.

- [ ] **Step 3: Thread the purpose through the launcher**

Make these replacements in `Scout/Utilities/ClaudeLauncher.swift`, in order. Each "replace" block occurs exactly once.

**1. Add the purpose type after `Target`.** Replace:

```swift
    enum Target {
        case cli(cwd: URL, config: CLIConfig)
        case claudeDesktop(DesktopMode)
    }
```

with:

```swift
    enum Target {
        case cli(cwd: URL, config: CLIConfig)
        case claudeDesktop(DesktopMode)
    }

    /// Why a CLI session is being opened. An action item starts a fresh
    /// `claude` with its context on the clipboard; resume reopens an existing
    /// session (`claude --resume <id>`) and leaves the clipboard alone.
    enum CLIPurpose: Equatable {
        case actionItem
        case resume(cliSessionID: String)

        var arguments: [String] {
            switch self {
            case .actionItem:              return []
            case .resume(let cliSessionID): return ["--resume", cliSessionID]
            }
        }
    }
```

**2. Route `launch` through the action-item purpose and add `resume`.** Replace:

```swift
        switch target {
        case .cli(let cwd, let config):  try launchCLI(cwd: cwd, config: config)
        case .claudeDesktop(let mode):   try launchClaudeDesktop(prompt: prompt, mode: mode)
        }
    }
```

with:

```swift
        switch target {
        case .cli(let cwd, let config):  try launchCLI(cwd: cwd, config: config, purpose: .actionItem)
        case .claudeDesktop(let mode):   try launchClaudeDesktop(prompt: prompt, mode: mode)
        }
    }

    /// Reopen an existing Claude Code session in the configured terminal:
    /// `claude --resume <cliSessionID>` run from `cwd`. Unlike `launch`, this
    /// does not touch the clipboard.
    static func resume(cliSessionID: String, cwd: URL, config: CLIConfig) throws {
        try launchCLI(cwd: cwd, config: config, purpose: .resume(cliSessionID: cliSessionID))
    }
```

**3. Custom command: the arguments go inside `{claude}`.** Replace:

```swift
    static func expandCustomCommand(template: String, claudePath: String, cwd: String) -> String {
        template
            .replacingOccurrences(of: "{claude}", with: shellQuote(claudePath))
            .replacingOccurrences(of: "{cwd}", with: shellQuote(cwd))
    }
```

with:

```swift
    /// `arguments` follow the claude path inside `{claude}`, each quoted.
    static func expandCustomCommand(
        template: String, claudePath: String, cwd: String, arguments: [String] = []
    ) -> String {
        let claude = ([claudePath] + arguments).map { shellQuote($0) }.joined(separator: " ")
        return template
            .replacingOccurrences(of: "{claude}", with: claude)
            .replacingOccurrences(of: "{cwd}", with: shellQuote(cwd))
    }
```

**4. Terminal.app / iTerm2 shell command.** Replace:

```swift
    static func makeTerminalShellCommand(claudePath: String, cwd: String) -> String {
        let cwdEsc = shellDoubleQuoteEscape(cwd)
        let claudeEsc = shellDoubleQuoteEscape(claudePath)
        return "cd \"\(cwdEsc)\" && clear && "
            + "echo 'Scout: action-item context copied to your clipboard. Paste with Cmd+V.' && "
            + "exec \"\(claudeEsc)\""
    }
```

with:

```swift
    static func makeTerminalShellCommand(
        claudePath: String, cwd: String, purpose: CLIPurpose = .actionItem
    ) -> String {
        let cwdEsc = shellDoubleQuoteEscape(cwd)
        let claudeEsc = shellDoubleQuoteEscape(claudePath)
        let banner = purpose == .actionItem
            ? "Scout: action-item context copied to your clipboard. Paste with Cmd+V."
            : "Scout: resuming a Claude Code session."
        return "cd \"\(cwdEsc)\" && clear && "
            + "echo \(shellQuote(banner)) && "
            + "exec \"\(claudeEsc)\""
            + purpose.arguments.map { " " + shellQuote($0) }.joined()
    }
```

**5. Terminal.app script.** Replace:

```swift
    static func makeTerminalAppScript(claudePath: String, cwd: String) -> String {
        let cmd = appleScriptEscape(makeTerminalShellCommand(claudePath: claudePath, cwd: cwd))
```

with:

```swift
    static func makeTerminalAppScript(
        claudePath: String, cwd: String, purpose: CLIPurpose = .actionItem
    ) -> String {
        let cmd = appleScriptEscape(makeTerminalShellCommand(claudePath: claudePath, cwd: cwd, purpose: purpose))
```

**6. ITerm2 script.** Replace:

```swift
    static func makeITermScript(claudePath: String, cwd: String) -> String {
        let cmd = appleScriptEscape(makeTerminalShellCommand(claudePath: claudePath, cwd: cwd))
```

with:

```swift
    static func makeITermScript(
        claudePath: String, cwd: String, purpose: CLIPurpose = .actionItem
    ) -> String {
        let cmd = appleScriptEscape(makeTerminalShellCommand(claudePath: claudePath, cwd: cwd, purpose: purpose))
```

**7. `launchCLI` takes the purpose.** Replace:

```swift
    private static func launchCLI(cwd: URL, config: CLIConfig) throws {
        guard let claudePath = resolveClaudePath(override: config.claudePathOverride) else {
            throw LaunchError.claudeCLINotFound
        }
        switch config.terminal {
        case .auto:        try launchAuto(claudePath: claudePath, cwd: cwd)
        case .terminalApp: try launchTerminalApp(claudePath: claudePath, cwd: cwd)
        case .iterm2:      try launchITerm(claudePath: claudePath, cwd: cwd)
        case .custom:      try launchCustom(claudePath: claudePath, cwd: cwd, command: config.customCommand)
        }
    }
```

with:

```swift
    private static func launchCLI(cwd: URL, config: CLIConfig, purpose: CLIPurpose) throws {
        guard let claudePath = resolveClaudePath(override: config.claudePathOverride) else {
            throw LaunchError.claudeCLINotFound
        }
        switch config.terminal {
        case .auto:        try launchAuto(claudePath: claudePath, cwd: cwd, purpose: purpose)
        case .terminalApp: try launchTerminalApp(claudePath: claudePath, cwd: cwd, purpose: purpose)
        case .iterm2:      try launchITerm(claudePath: claudePath, cwd: cwd, purpose: purpose)
        case .custom:
            try launchCustom(claudePath: claudePath, cwd: cwd, command: config.customCommand, purpose: purpose)
        }
    }
```

**8. `launchAuto` passes it on.** Replace:

```swift
    private static func launchAuto(claudePath: String, cwd: URL) throws {
        if let ghosttyURL = NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: ghosttyBundleID
        ) {
            if launchViaTmux(claudePath: claudePath, cwd: cwd) {
                activateGhostty(ghosttyURL: ghosttyURL)
                return
            }
            try launchFreshGhosttyWindow(ghosttyURL: ghosttyURL, claudePath: claudePath, cwd: cwd)
            return
        }
        try launchTerminalApp(claudePath: claudePath, cwd: cwd)
    }
```

with:

```swift
    private static func launchAuto(claudePath: String, cwd: URL, purpose: CLIPurpose) throws {
        if let ghosttyURL = NSWorkspace.shared.urlForApplication(
            withBundleIdentifier: ghosttyBundleID
        ) {
            if launchViaTmux(claudePath: claudePath, cwd: cwd, purpose: purpose) {
                activateGhostty(ghosttyURL: ghosttyURL)
                return
            }
            try launchFreshGhosttyWindow(ghosttyURL: ghosttyURL, claudePath: claudePath, cwd: cwd, purpose: purpose)
            return
        }
        try launchTerminalApp(claudePath: claudePath, cwd: cwd, purpose: purpose)
    }
```

**9. Tmux signature.** Replace:

```swift
    private static func launchViaTmux(claudePath: String, cwd: URL) -> Bool {
```

with:

```swift
    private static func launchViaTmux(claudePath: String, cwd: URL, purpose: CLIPurpose) -> Bool {
```

**10. Tmux argv comes from a pure builder.** Replace:

```swift
        task.arguments = [
            "new-window",
            "-t", "\(session):",
            "-c", cwd.path,
            "-n", "claude",
            claudePath,
        ]
```

with:

```swift
        task.arguments = makeTmuxNewWindowArguments(
            session: session, claudePath: claudePath, cwd: cwd.path, purpose: purpose)
```

**11. Add the tmux argv builder above `firstTmuxSession`.** Replace:

```swift
    /// Lists tmux sessions and returns the first attached one (or the first
```

with:

```swift
    /// argv for `tmux new-window`: claude and its arguments as separate argv
    /// entries, so no shell quoting is involved.
    static func makeTmuxNewWindowArguments(
        session: String, claudePath: String, cwd: String, purpose: CLIPurpose = .actionItem
    ) -> [String] {
        ["new-window", "-t", "\(session):", "-c", cwd, "-n", "claude", claudePath] + purpose.arguments
    }

    /// Lists tmux sessions and returns the first attached one (or the first
```

**12. Ghostty window signature.** Replace:

```swift
    private static func launchFreshGhosttyWindow(
        ghosttyURL: URL,
        claudePath: String,
        cwd: URL
    ) throws {
```

with:

```swift
    private static func launchFreshGhosttyWindow(
        ghosttyURL: URL,
        claudePath: String,
        cwd: URL,
        purpose: CLIPurpose
    ) throws {
```

**13. Ghostty window writes the purpose's script.** Replace:

```swift
            try makeGhosttyScript(claudePath: claudePath, cwd: cwd)
                .write(to: scriptURL, atomically: true, encoding: .utf8)
```

with:

```swift
            try makeGhosttyScript(claudePath: claudePath, cwd: cwd, purpose: purpose)
                .write(to: scriptURL, atomically: true, encoding: .utf8)
```

**14. Ghostty script builder becomes internal (testable) and takes the purpose.** Replace:

```swift
    private static func makeGhosttyScript(claudePath: String, cwd: URL) -> String {
```

with:

```swift
    static func makeGhosttyScript(claudePath: String, cwd: URL, purpose: CLIPurpose = .actionItem) -> String {
```

**15. Ghostty script body.** Replace:

```swift
        // Ghostty inherits Scout's minimal launchd PATH, so we exec `claude`
        // by absolute path rather than relying on PATH lookup.
        return """
        #!/bin/bash
        cd "\(cwdEsc)" || exit 1
        clear
        echo "Scout: action-item context copied to your clipboard."
        echo "When Claude prompts you, paste (Cmd+V) and press Enter to send."
        echo
        exec "\(claudeEsc)"
        """
    }
```

with:

```swift
        let banner = purpose == .actionItem
            ? """
              echo "Scout: action-item context copied to your clipboard."
              echo "When Claude prompts you, paste (Cmd+V) and press Enter to send."
              """
            : "echo \"Scout: resuming a Claude Code session.\""
        let arguments = purpose.arguments.map { " " + shellQuote($0) }.joined()
        // Ghostty inherits Scout's minimal launchd PATH, so we exec `claude`
        // by absolute path rather than relying on PATH lookup.
        return """
        #!/bin/bash
        cd "\(cwdEsc)" || exit 1
        clear
        \(banner)
        echo
        exec "\(claudeEsc)"\(arguments)
        """
    }
```

**16. Terminal.app and iTerm2 launchers.** Replace:

```swift
    private static func launchTerminalApp(claudePath: String, cwd: URL) throws {
        try runAppleScript(makeTerminalAppScript(claudePath: claudePath, cwd: cwd.path))
    }

    private static func launchITerm(claudePath: String, cwd: URL) throws {
```

with:

```swift
    private static func launchTerminalApp(claudePath: String, cwd: URL, purpose: CLIPurpose) throws {
        try runAppleScript(makeTerminalAppScript(claudePath: claudePath, cwd: cwd.path, purpose: purpose))
    }

    private static func launchITerm(claudePath: String, cwd: URL, purpose: CLIPurpose) throws {
```

**17. ITerm2 runs the purpose's script.** Replace:

```swift
        try runAppleScript(makeITermScript(claudePath: claudePath, cwd: cwd.path))
```

with:

```swift
        try runAppleScript(makeITermScript(claudePath: claudePath, cwd: cwd.path, purpose: purpose))
```

**18. Custom command launcher.** Replace:

```swift
    private static func launchCustom(claudePath: String, cwd: URL, command: String) throws {
        let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw LaunchError.customCommandEmpty }
        let expanded = expandCustomCommand(template: trimmed, claudePath: claudePath, cwd: cwd.path)
```

with:

```swift
    private static func launchCustom(claudePath: String, cwd: URL, command: String, purpose: CLIPurpose) throws {
        let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw LaunchError.customCommandEmpty }
        let expanded = expandCustomCommand(
            template: trimmed, claudePath: claudePath, cwd: cwd.path, arguments: purpose.arguments)
```

- [ ] **Step 4: Run the new and existing launcher tests**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ClaudeLauncherResumeTests -only-testing:ScoutTests/CLILauncherTests -only-testing:ScoutTests/ClaudeLauncherErrorTests -only-testing:ScoutTests/ClaudeLauncherPromptTests -only-testing:ScoutTests/ClaudeDesktopURLTests`

Expected: every suite passes, and `ClaudeLauncherResumeTests` reports 7 tests. The build shows no warning in `ClaudeLauncher.swift`. The `{ shellQuote($0) }` closures avoid passing a main-actor method reference to `map`, which would warn.

- [ ] **Step 5: Commit**

```bash
git add Scout/Utilities/ClaudeLauncher.swift ScoutTests/Sessions/ClaudeLauncherResumeTests.swift
git commit -m "feat(launcher): resume a Claude Code session in the configured terminal" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Formatting and the handoff text

These are small pure helpers shared by the views:
- relative ages in the engine's own spelling (`40s ago`, `12m ago`, `2h ago`, `3d ago`);
- PR chips such as `#98 · changes requested · ✗`;
- short model names;
- the folder to resume in;
- the "Copy handoff" Markdown (spec §6.4).

**Files:**
- Create: `Scout/Sessions/SessionsFormat.swift`
- Create: `Scout/Sessions/SessionHandoff.swift`
- Test: `ScoutTests/Sessions/SessionsFormatTests.swift`
- Test: `ScoutTests/Sessions/SessionHandoffTests.swift`

**Interfaces:**
- Consumes: the Task 1 types.
- Produces:
  - `SessionsFormat`, with:
    - `ago(_ date: Date?, now: Date) -> String`
    - `prChip(_ pr: SessionPR) -> String`
    - `shortModel(_:) -> String`
    - `resumeDirectory(for:exists:) -> URL`
  - `SessionHandoff.markdown(for:projectName:) -> String`.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Sessions/SessionsFormatTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("SessionsFormat")
struct SessionsFormatTests {
    private let now = SessionsFixture.now

    @Test(arguments: [(40.0, "40s ago"), (720, "12m ago"), (7200, "2h ago"), (259_200, "3d ago"), (-5, "0s ago")])
    func agesUseTheEngineSpelling(secondsAgo: Double, expected: String) {
        #expect(SessionsFormat.ago(now.addingTimeInterval(-secondsAgo), now: now) == expected)
    }

    @Test func aMissingDateIsADash() {
        #expect(SessionsFormat.ago(nil, now: now) == "—")
    }

    @Test func prChipsCarryReviewAndChecks() throws {
        let index = try SessionsFixture.index()
        func chip(_ id: String) -> String? { SessionsFixture.session(id, in: index)?.pr.map(SessionsFormat.prChip) }
        #expect(chip("local_A") == "#98 · changes requested · ✗")
        #expect(chip("local_R") == "#102 · review required · ✓")
        #expect(chip("local_Q") == "#81 · draft · …")
        #expect(chip("local_U") == "#110")
        #expect(chip("local_M") == "#60 · merged · ✓")
    }

    @Test func modelsLoseTheirClaudePrefix() {
        #expect(SessionsFormat.shortModel("claude-opus-5") == "opus-5")
        #expect(SessionsFormat.shortModel("gpt-x") == "gpt-x")
    }

    @Test func resumeRunsInTheWorktreeThenTheProjectThenHome() throws {
        let a = try #require(SessionsFixture.session("local_A", in: SessionsFixture.index()))
        #expect(SessionsFormat.resumeDirectory(for: a, exists: { _ in true }).path == a.cwd)
        #expect(SessionsFormat.resumeDirectory(for: a, exists: { $0 == a.originCwd }).path == a.originCwd)
        #expect(SessionsFormat.resumeDirectory(for: a, exists: { _ in false })
                == FileManager.default.homeDirectoryForCurrentUser)
    }
}
```

Create `ScoutTests/Sessions/SessionHandoffTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("SessionHandoff")
struct SessionHandoffTests {

    @Test func aFullSessionHandsOffEverything() throws {
        let index = try SessionsFixture.index()
        let a = try #require(SessionsFixture.session("local_A", in: index))
        #expect(SessionHandoff.markdown(for: a, projectName: "Example Repo") == """
        ## Fix the parser

        - **Project:** Example Repo
        - **State:** needs you — changes requested on PR #98; CI failing; ended on a question
        - **PR:** example-org/example-repo#98 (changes requested) — https://github.com/example-org/example-repo/pull/98
        - **Branch:** `claude/w-compass` in worktree `w-compass`
        - **Folder:** `/Users/alex/code/example-repo/.claude/worktrees/w-compass`
        - **Resume:** `claude --resume aaaaaaaa-0000-0000-0000-000000000001`

        ### First prompt

        > Please fix the parser so blank lines between items are kept.

        ### Files touched

        - `~/code/example-repo/parser.py`
        - `~/code/example-repo/tests/test_parser.py`
        """)
    }

    @Test func aSparseSessionLeavesOutWhatItDoesNotHave() throws {
        let index = try SessionsFixture.index()
        let s = try #require(SessionsFixture.session("local_S", in: index))
        let text = SessionHandoff.markdown(for: s, projectName: "Example Repo")
        #expect(!text.contains("**PR:**"))
        #expect(!text.contains("### First prompt"))
        #expect(text.contains("- **State:** stale — dirty worktree, idle 6d"))
    }

    @Test func aMultiLinePromptStaysQuoted() throws {
        let index = try SessionsFixture.index()
        let cli = try #require(SessionsFixture.session("cli:aaaaaaaa-0000-0000-0000-000000000007", in: index))
        let text = SessionHandoff.markdown(for: cli, projectName: "other-repo")
        #expect(text.contains("> Sam asked for a release checklist.\n> Keep it short."))
        #expect(text.hasPrefix("## Sam asked for a release checklist."))
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsFormatTests -only-testing:ScoutTests/SessionHandoffTests`

Expected: the build fails with `cannot find 'SessionsFormat' in scope`.

- [ ] **Step 3: Implement them**

Create `Scout/Sessions/SessionsFormat.swift`:

```swift
import Foundation

/// Small pure formatters the Sessions views share.
nonisolated enum SessionsFormat {

    /// `40s ago`, `12m ago`, `2h ago`, `3d ago` — the engine's own spelling
    /// (`derive.fmt_ago`), so cards and reasons read the same.
    static func ago(_ date: Date?, now: Date) -> String {
        guard let date else { return "—" }
        let seconds = max(0, Int(now.timeIntervalSince(date)))
        if seconds < 60 { return "\(seconds)s ago" }
        if seconds < 3600 { return "\(seconds / 60)m ago" }
        if seconds < 86_400 { return "\(seconds / 3600)h ago" }
        return "\(seconds / 86_400)d ago"
    }

    /// `#98 · changes requested · ✗`
    static func prChip(_ pr: SessionPR) -> String {
        ["#\(pr.number)", pr.reviewLabel, pr.checksSymbol].compactMap { $0 }.joined(separator: " · ")
    }

    /// `claude-opus-5` → `opus-5`
    static func shortModel(_ model: String) -> String {
        model.hasPrefix("claude-") ? String(model.dropFirst("claude-".count)) : model
    }

    /// Where "Resume in terminal" runs `claude --resume`: the session's own
    /// folder, else its project folder when the worktree is gone (spec §6.4),
    /// else the home folder.
    static func resumeDirectory(
        for session: AgentSession,
        exists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) }
    ) -> URL {
        for path in [session.cwd, session.originCwd] where !path.isEmpty && exists(path) {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        return FileManager.default.homeDirectoryForCurrentUser
    }
}
```

Create `Scout/Sessions/SessionHandoff.swift`:

```swift
import Foundation

/// The "Copy handoff" text (spec §6.4): what someone — or another agent —
/// needs to pick the session up, as Markdown.
nonisolated enum SessionHandoff {
    static func markdown(for session: AgentSession, projectName: String) -> String {
        var lines = ["## \(session.displayTitle)", ""]
        lines.append("- **Project:** \(projectName)")
        var state = "- **State:** \(session.state.label.lowercased())"
        if !session.stateReasons.isEmpty { state += " — " + session.stateReasons.joined(separator: "; ") }
        lines.append(state)
        if let pr = session.pr {
            var line = "- **PR:** \(pr.repo)#\(pr.number)"
            if let label = pr.reviewLabel { line += " (\(label))" }
            if let url = pr.url { line += " — \(url)" }
            lines.append(line)
        }
        if let branch = session.worktree?.branch {
            var line = "- **Branch:** `\(branch)`"
            if let name = session.worktree?.name { line += " in worktree `\(name)`" }
            lines.append(line)
        }
        lines.append("- **Folder:** `\(session.cwd)`")
        if let id = session.cliSessionID {
            lines.append("- **Resume:** `claude --resume \(id)`")
        }
        if let prompt = session.transcript?.firstPrompt, !prompt.isEmpty {
            lines += ["", "### First prompt", ""]
            lines += prompt.split(separator: "\n", omittingEmptySubsequences: false).map { "> \($0)" }
        }
        if let files = session.transcript?.filesTouched, !files.isEmpty {
            lines += ["", "### Files touched", ""]
            lines += files.map { "- `\($0)`" }
        }
        return lines.joined(separator: "\n")
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsFormatTests -only-testing:ScoutTests/SessionHandoffTests`

Expected: `Test run with 8 tests in 2 suites passed`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Sessions/SessionsFormat.swift Scout/Sessions/SessionHandoff.swift ScoutTests/Sessions/SessionsFormatTests.swift ScoutTests/Sessions/SessionHandoffTests.swift
git commit -m "feat(sessions): ages, PR chips and the copy-handoff text" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Board, table, cards and header views

These are the master pane's views (spec §6.1–6.3, §10.5).
- **Cards** follow `BoardCardView`: a 3 pt state edge, a two-line serif title, the deciding reason, the PR chip with its age, and the branch with the model.
- **The board** shows the Now strip, then one swimlane per project, with collapsed *Stale (n)* and *Done (n)* pills. Hovering a sub-agent's card highlights its parent's card.
- **The table** is a SwiftUI `Table` with every column sortable. It is also the VoiceOver path.
- **The header** shows the title, a freshness line read through a `TimelineView`, search, the project menu, the options menu (Scout's runs, recently done), the Board/Table toggle and the state chips.

**Files:**
- Create: `Scout/Sessions/Views/SessionStatePill.swift`
- Create: `Scout/Sessions/Views/SessionCardView.swift`
- Create: `Scout/Sessions/Views/SessionsBoardView.swift`
- Create: `Scout/Sessions/Views/SessionsTableView.swift`
- Create: `Scout/Sessions/Views/SessionsHeader.swift`
- Test: `ScoutTests/Sessions/SessionsBoardSmokeTests.swift`

**Interfaces:**
- Consumes:
  - From Tasks 1–6: `SessionsLayout`, `SessionsFormat` and `SessionIndexService` (its `lastRefreshAt` and `prStatus`).
  - From the app: `DS`, `EditorialSegmentedControl` and `.buttonStyle(.plainHit)`.
  - From the tests: `ViewHost.render(_:size:)` (`ScoutTests/Shell/ViewSmokeTests.swift`) and `ScriptedSessionsRunner`.
- Produces:
  - `SessionStateStyle.color(_:isOpen:)`, `SessionStateDot` and `SessionStatePill`.
  - `SessionCardView(session:now:parentTitle:isSelected:isHighlighted:)`.
  - `SessionsBoardView(index:filter:now:selectedID:)` and `SessionsTableView(rows:now:selectedID:)`.
  - `SessionsViewMode`, with cases `.board` and `.table` and a `label`.
  - `SessionsHeader(viewMode:filter:counts:projects:service:)`.
  - Test support: `SessionsFixture.loadedService(answering:)`.

- [ ] **Step 1: Write the failing smoke tests**

Create `ScoutTests/Sessions/SessionsBoardSmokeTests.swift`:

```swift
import AppKit
import SwiftUI
import Testing
@testable import Scout

extension SessionsFixture {
    /// A service that has already run one fast build answering `result`. Its
    /// index file is in a temp folder and nothing is watched.
    @MainActor
    static func loadedService(answering result: ProcessResult) async -> SessionIndexService {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("sessions-smoke-\(UUID().uuidString)")
        let service = SessionIndexService(configuration: .init(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            argumentsPrefix: [],
            runner: ScriptedSessionsRunner(fast: result),
            fileEvents: NoopFS(),
            indexFile: vault.appendingPathComponent(".scout-cache/sessions-index.json"),
            watchRoots: []
        ))
        await service.refreshFast()
        return service
    }
}

/// Renders the board, table, cards, header and pills against the fixture
/// (pattern: ViewSmokeTests). Every body and branch must evaluate and lay out.
@MainActor
@Suite("View smoke — sessions board", .serialized)
struct SessionsBoardSmokeTests {
    private let now = SessionsFixture.now

    @Test("the board renders every lane with each filter")
    func boardRenders() throws {
        let index = try SessionsFixture.index()
        var everything = SessionsFilter()
        everything.showScoutRuns = true
        everything.showRecentlyDone = true
        var nothing = SessionsFilter()
        nothing.search = "no session is called this"
        for filter in [SessionsFilter(), everything, nothing] {
            ViewHost.render(SessionsBoardView(index: index, filter: filter, now: now, selectedID: .constant("local_A")))
        }
    }

    @Test("the table renders every row")
    func tableRenders() throws {
        let index = try SessionsFixture.index()
        var filter = SessionsFilter()
        filter.showScoutRuns = true
        filter.showRecentlyDone = true
        ViewHost.render(SessionsTableView(
            rows: SessionsLayout.tableRows(index: index, filter: filter, now: now),
            now: now,
            selectedID: .constant("local_R")))
    }

    @Test("every fixture session renders as a card")
    func cardsRender() throws {
        for session in try SessionsFixture.index().sessions {
            ViewHost.render(SessionCardView(session: session, now: now, parentTitle: "Fix the parser",
                                            isSelected: true, isHighlighted: true),
                            size: CGSize(width: 260, height: 180))
        }
    }

    @Test("the header renders with chips, menus and freshness")
    func headerRenders() async throws {
        let service = await SessionsFixture.loadedService(answering: .ok(try SessionsFixture.data()))
        let index = try #require(service.index)
        var filter = SessionsFilter()
        filter.states = [.needsYou]
        filter.showRecentlyDone = true
        ViewHost.render(SessionsHeader(
            viewMode: .constant(.table),
            filter: .constant(filter),
            counts: SessionsLayout.stateCounts(index: index, filter: filter, now: now),
            projects: SessionsLayout.menuProjects(index: index),
            service: service))
    }

    @Test("every state renders as a pill")
    func pillsRender() {
        for state in AgentSessionState.allCases {
            ViewHost.render(SessionStatePill(state: state, isOpen: state == .parked), size: CGSize(width: 120, height: 30))
        }
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsBoardSmokeTests`

Expected: the build fails with `cannot find 'SessionsBoardView' in scope`.

- [ ] **Step 3: Add the state colours and pill**

Create `Scout/Sessions/Views/SessionStatePill.swift`:

```swift
import SwiftUI

/// State colours from the design system (spec §6.2). Parked is a shade darker
/// while the session's process is still open.
enum SessionStateStyle {
    static func color(_ state: AgentSessionState, isOpen: Bool = false) -> Color {
        switch state {
        case .needsYou: return DS.Priority.urgent
        case .running:  return DS.Status.ok
        case .waiting:  return DS.Priority.todo
        case .parked:   return isOpen ? DS.Ink.p3 : DS.Ink.p4
        case .stale:    return DS.Priority.done.opacity(0.6)
        case .done:     return DS.Priority.done
        }
    }
}

struct SessionStateDot: View {
    let state: AgentSessionState
    var isOpen = false

    var body: some View {
        Circle()
            .fill(SessionStateStyle.color(state, isOpen: isOpen))
            .frame(width: 8, height: 8)
            .accessibilityLabel(state.label)
    }
}

struct SessionStatePill: View {
    let state: AgentSessionState
    var isOpen = false

    var body: some View {
        HStack(spacing: 5) {
            SessionStateDot(state: state, isOpen: isOpen)
            Text(state.label)
                .font(DS.sans(11, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(Capsule().fill(SessionStateStyle.color(state, isOpen: isOpen).opacity(0.14)))
    }
}
```

- [ ] **Step 4: Add the card**

Create `Scout/Sessions/Views/SessionCardView.swift`:

```swift
import SwiftUI

/// One session on the board (spec §6.2), styled after `BoardCardView`: a 3 pt
/// state edge, a two-line serif title, the deciding reason, then PR, age,
/// branch and model.
struct SessionCardView: View {
    let session: AgentSession
    let now: Date
    /// Set for a sub-agent whose parent is in the index.
    var parentTitle: String? = nil
    var isSelected = false
    /// This card is the parent of the card under the pointer.
    var isHighlighted = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let parentTitle {
                Text("↳ \(parentTitle)")
                    .font(DS.sans(10.5))
                    .foregroundStyle(DS.Ink.p4)
                    .lineLimit(1)
            }
            Text(session.displayTitle)
                .font(DS.serif(13.5, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let reason = session.primaryReason {
                Text(reason)
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(1)
            }
            HStack(spacing: 8) {
                if let pr = session.pr {
                    Text(SessionsFormat.prChip(pr))
                        .font(DS.mono(10.5))
                        .foregroundStyle(DS.Ink.p2)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                Text(SessionsFormat.ago(session.lastActivityAt, now: now))
                    .font(DS.sans(10.5))
                    .foregroundStyle(DS.Ink.p4)
            }
            HStack(spacing: 6) {
                if let branch = session.worktree?.branch {
                    Text(branch)
                        .font(DS.mono(10))
                        .foregroundStyle(DS.Ink.p3)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer(minLength: 0)
                if let model = session.model {
                    Text(SessionsFormat.shortModel(model))
                        .font(DS.mono(10))
                        .foregroundStyle(DS.Ink.p4)
                }
            }
        }
        .padding(12)
        .frame(width: 240, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(borderColor, lineWidth: isSelected || isHighlighted ? 1 : 0.5))
        )
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2)
                .fill(SessionStateStyle.color(session.state, isOpen: session.isOpen))
                .frame(width: 3)
                .padding(.vertical, 6)
        }
        .accessibilityElement(children: .combine)
        .accessibilityValue(session.state.label)
    }

    private var borderColor: Color {
        if isSelected { return DS.Ink.p2 }
        if isHighlighted { return DS.Accent.fill }
        return DS.Rule.soft
    }
}
```

- [ ] **Step 5: Add the board**

Create `Scout/Sessions/Views/SessionsBoardView.swift`:

```swift
import SwiftUI

/// The board (spec §6.2): a Now strip of needs-you and running cards, then one
/// swimlane per project with cards in severity order. Stale and done cards sit
/// behind collapsed "Stale (n)" / "Done (n)" pills at the end of each lane.
struct SessionsBoardView: View {
    let index: SessionIndex
    let filter: SessionsFilter
    let now: Date
    @Binding var selectedID: String?

    @State private var expandedStale: Set<String> = []
    @State private var expandedDone: Set<String> = []
    @State private var hoveredParentID: String?

    var body: some View {
        let rows = SessionsLayout.rows(index: index, filter: filter, now: now)
        let strip = SessionsLayout.nowStrip(index: index, filter: filter, now: now)
        let titles = Dictionary(index.sessions.map { ($0.id, $0.displayTitle) }, uniquingKeysWith: { first, _ in first })
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 22) {
                if !strip.isEmpty {
                    lane(title: "Now", counts: nil) {
                        ForEach(strip) { card($0, titles: titles) }
                    }
                }
                ForEach(rows) { row in
                    lane(title: row.name, counts: row.counts) {
                        ForEach(row.active) { card($0, titles: titles) }
                        collapsible("Stale", sessions: row.stale, rowID: row.id, expanded: $expandedStale, titles: titles)
                        collapsible("Done", sessions: row.done, rowID: row.id, expanded: $expandedDone, titles: titles)
                    }
                }
                if rows.isEmpty && strip.isEmpty {
                    Text("No sessions match these filters.")
                        .font(DS.sans(13))
                        .foregroundStyle(DS.Ink.p3)
                        .padding(.top, 40)
                        .frame(maxWidth: .infinity)
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 16)
        }
    }

    private func lane<Cards: View>(
        title: String, counts: [AgentSessionState: Int]?, @ViewBuilder cards: () -> Cards
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                Text(title)
                    .font(DS.serif(17, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                if let counts {
                    countPills(counts)
                }
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(alignment: .top, spacing: 10) { cards() }
                    .padding(.vertical, 2)
            }
        }
    }

    /// One tiny pill per state present in the lane, in severity order (spec §6.2).
    private func countPills(_ counts: [AgentSessionState: Int]) -> some View {
        HStack(spacing: 4) {
            ForEach(AgentSessionState.allCases.filter { counts[$0] != nil }, id: \.self) { state in
                HStack(spacing: 3) {
                    SessionStateDot(state: state)
                    Text("\(counts[state] ?? 0)")
                        .font(DS.mono(10.5))
                        .foregroundStyle(DS.Ink.p3)
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(Capsule().fill(DS.Paper.sunk))
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(counts[state] ?? 0) \(state.label.lowercased())")
            }
        }
    }

    private func card(_ session: AgentSession, titles: [String: String]) -> some View {
        Button {
            selectedID = session.id
        } label: {
            SessionCardView(
                session: session,
                now: now,
                parentTitle: session.parentSessionID.flatMap { titles[$0] },
                isSelected: selectedID == session.id,
                isHighlighted: hoveredParentID == session.id
            )
        }
        .buttonStyle(.plainHit)
        .onHover { inside in
            if inside { hoveredParentID = session.parentSessionID } else if hoveredParentID == session.parentSessionID { hoveredParentID = nil }
        }
    }

    @ViewBuilder
    private func collapsible(
        _ label: String, sessions: [AgentSession], rowID: String,
        expanded: Binding<Set<String>>, titles: [String: String]
    ) -> some View {
        if !sessions.isEmpty {
            let isOpen = expanded.wrappedValue.contains(rowID)
            Button {
                if isOpen { expanded.wrappedValue.remove(rowID) } else { expanded.wrappedValue.insert(rowID) }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: isOpen ? "chevron.left" : "chevron.right")
                        .imageScale(.small)
                    Text("\(label) (\(sessions.count))")
                }
                .font(DS.sans(11.5, weight: .medium))
                .foregroundStyle(DS.Ink.p3)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Capsule().fill(DS.Paper.sunk))
            }
            .buttonStyle(.plainHit)
            .accessibilityLabel(isOpen ? "Hide \(label.lowercased()) sessions" : "Show \(sessions.count) \(label.lowercased()) sessions")
            if isOpen {
                ForEach(sessions) { card($0, titles: titles) }
            }
        }
    }
}
```

- [ ] **Step 6: Add the table**

Create `Scout/Sessions/Views/SessionsTableView.swift`:

```swift
import SwiftUI

/// The table (spec §6.3): every column sortable, severity then recency by
/// default. Shares filters and selection with the board, and is the page's
/// VoiceOver path — a row reads as cells, not as a drawing.
struct SessionsTableView: View {
    let rows: [SessionTableRow]
    let now: Date
    @Binding var selectedID: String?

    @State private var sortOrder: [KeyPathComparator<SessionTableRow>] = [
        KeyPathComparator(\.severity),
        KeyPathComparator(\.lastActivity, order: .reverse),
    ]

    var body: some View {
        Table(rows.sorted(using: sortOrder), selection: $selectedID, sortOrder: $sortOrder) {
            TableColumn("State", value: \.severity) { row in
                HStack(spacing: 6) {
                    SessionStateDot(state: row.session.state, isOpen: row.session.isOpen)
                    Text(row.session.state.label).font(DS.sans(11.5)).foregroundStyle(DS.Ink.p3)
                }
            }
            .width(min: 80, ideal: 92)
            TableColumn("Title", value: \.title) { row in
                Text(row.title).font(DS.sans(12.5)).foregroundStyle(DS.Ink.p1).lineLimit(1)
            }
            .width(min: 160, ideal: 260)
            TableColumn("Project", value: \.projectName)
                .width(min: 80, ideal: 120)
            TableColumn("Reason", value: \.reason)
                .width(min: 120, ideal: 200)
            TableColumn("PR", value: \.prNumber) { row in
                Text(row.session.pr.map(SessionsFormat.prChip) ?? "").font(DS.mono(11))
            }
            .width(min: 60, ideal: 150)
            TableColumn("Last active", value: \.lastActivity) { row in
                Text(SessionsFormat.ago(row.session.lastActivityAt, now: now)).font(DS.sans(11.5))
            }
            .width(min: 70, ideal: 80)
            TableColumn("Model", value: \.model) { row in
                Text(row.session.model.map(SessionsFormat.shortModel) ?? "").font(DS.mono(11))
            }
            .width(min: 60, ideal: 80)
            TableColumn("Turns", value: \.turns) { row in
                Text(row.session.turns.map(String.init) ?? "").font(DS.mono(11))
            }
            .width(min: 40, ideal: 50)
        }
    }
}
```

- [ ] **Step 7: Add the header**

Create `Scout/Sessions/Views/SessionsHeader.swift`:

```swift
import SwiftUI

/// Board or table. Persists across launches via `@SceneStorage("sessionsView")`.
enum SessionsViewMode: String, CaseIterable, Hashable {
    case board
    case table

    var label: String {
        switch self {
        case .board: return "Board"
        case .table: return "Table"
        }
    }
}

/// Title, freshness line, search, project and option menus, the Board/Table
/// toggle, then one chip per state with live counts (spec §6.1).
struct SessionsHeader: View {
    @Binding var viewMode: SessionsViewMode
    @Binding var filter: SessionsFilter
    /// From `SessionsLayout.stateCounts` — every filter but the chips.
    let counts: [AgentSessionState: Int]
    let projects: [SessionProject]
    /// Read inside a `TimelineView`: `lastRefreshAt` is deliberately not
    /// published, so the line ticks without re-rendering the page.
    let service: SessionIndexService

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Sessions")
                        .font(DS.serif(28, weight: .medium))
                        .foregroundStyle(DS.Ink.p1)
                    TimelineView(.periodic(from: .now, by: 10)) { context in
                        Text(freshness(now: context.date))
                            .font(DS.sans(12))
                            .foregroundStyle(DS.Ink.p3)
                    }
                }
                Spacer()
                TextField("Search sessions", text: $filter.search)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 190)
                projectMenu
                optionsMenu
                EditorialSegmentedControl(
                    selection: $viewMode,
                    options: SessionsViewMode.allCases.map { ($0.label, $0) }
                )
            }
            stateChips
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 16)
    }

    private func freshness(now: Date) -> String {
        var parts: [String] = []
        if let refreshed = service.lastRefreshAt {
            parts.append("updated \(SessionsFormat.ago(refreshed, now: now))")
        }
        if let pr = service.prStatus {
            parts.append(pr.errors.isEmpty
                ? "PRs checked \(SessionsFormat.ago(pr.finishedAt, now: now))"
                : "PR check: \(pr.errors.count) problem\(pr.errors.count == 1 ? "" : "s")")
        }
        return parts.isEmpty ? "Reading your Claude Code sessions…" : parts.joined(separator: " · ")
    }

    private var projectMenu: some View {
        Menu {
            Button("All projects") { filter.projectKey = nil }
            Divider()
            ForEach(projects, id: \.key) { project in
                Button(project.name) { filter.projectKey = project.key }
            }
        } label: {
            Text(projects.first { $0.key == filter.projectKey }?.name ?? "All projects")
                .font(DS.sans(12.5))
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }

    private var optionsMenu: some View {
        Menu {
            Toggle("Show Scout's own runs", isOn: $filter.showScoutRuns)
            Toggle("Show recently done", isOn: $filter.showRecentlyDone)
        } label: {
            Image(systemName: "line.3.horizontal.decrease.circle")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .accessibilityLabel("View options")
    }

    private var stateChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                chip(label: "All", count: counts.values.reduce(0, +), state: nil, isSelected: filter.states.isEmpty) {
                    filter.states = []
                }
                ForEach(AgentSessionState.allCases, id: \.self) { state in
                    if state != .done || filter.showRecentlyDone {
                        chip(label: state.label, count: counts[state] ?? 0, state: state,
                             isSelected: filter.states.contains(state)) {
                            if filter.states.contains(state) { filter.states.remove(state) } else { filter.states.insert(state) }
                        }
                    }
                }
            }
        }
    }

    private func chip(
        label: String, count: Int, state: AgentSessionState?, isSelected: Bool, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let state { SessionStateDot(state: state) }
                Text(label).font(DS.sans(12, weight: .medium))
                Text("\(count)")
                    .font(DS.mono(11))
                    .foregroundStyle(isSelected ? DS.Paper.base.opacity(0.85) : DS.Ink.p3)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Capsule().fill(isSelected ? DS.Ink.p1 : DS.Paper.raised))
            .foregroundStyle(isSelected ? DS.Paper.base : DS.Ink.p2)
        }
        .buttonStyle(.plainHit)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsBoardSmokeTests`

Expected: `Test run with 5 tests in 1 suite passed`.

- [ ] **Step 9: Commit**

```bash
git add Scout/Sessions/Views ScoutTests/Sessions/SessionsBoardSmokeTests.swift
git commit -m "feat(sessions): board, table, cards and header" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Detail pane and the page

**The detail pane** is one scrolling column (spec §10.6). It holds:
- the title, the project chip, the state pill and an open indicator;
- facts: branch, worktree (with "dirty" when kept dirty), model and effort, created, last active, turns and tool calls;
- actions: Resume, Open PR, Reveal and Copy handoff;
- the reasons, the PRs, the first prompt, the files touched, and related sessions (parent and children, clickable).

Resume on an open session asks for confirmation first. With nothing selected, the pane shows aggregate counts and when the index was built.

**The page shell** holds the selection and the filters. It shows a banner for each availability state and for skipped sessions, and turns watching on and off as the page appears and disappears.

**Files:**
- Create: `Scout/Sessions/Views/SessionDetailView.swift`
- Create: `Scout/Sessions/Views/SessionsView.swift`
- Test: `ScoutTests/Sessions/SessionsPageSmokeTests.swift`

**Interfaces:**
- Consumes:
  - Tasks 1–7.
  - `ClaudeLauncher.resume(cliSessionID:cwd:config:)`, `CLIConfig` and `CLITerminal`.
  - The `@AppStorage` keys `claudeCLIPath`, `cliTerminal` and `customLaunchCommand`. These are the ones `TaskActionsView` and `SettingsView` already use.
- Produces:
  - `SessionDetailView(session:index:now:onSelect:)`.
  - `SessionsView`, which reads `@EnvironmentObject var service: SessionIndexService` and stores the view mode in `@SceneStorage("sessionsView")`.

- [ ] **Step 1: Write the failing smoke tests**

Create `ScoutTests/Sessions/SessionsPageSmokeTests.swift`:

```swift
import AppKit
import SwiftUI
import Testing
@testable import Scout

/// Renders the detail pane and the whole page (pattern: ViewSmokeTests).
/// `.onAppear` does not run under `ViewHost`, so no watch or process starts.
@MainActor
@Suite("View smoke — sessions page", .serialized)
struct SessionsPageSmokeTests {
    private let now = SessionsFixture.now

    @Test("every fixture session renders in the detail pane")
    func detailRendersForEverySession() throws {
        let index = try SessionsFixture.index()
        for session in index.sessions {
            ViewHost.render(SessionDetailView(session: session, index: index, now: now) { _ in },
                            size: CGSize(width: 380, height: 900))
        }
    }

    @Test("the detail pane's empty states render")
    func emptyDetailRenders() throws {
        ViewHost.render(SessionDetailView(session: nil, index: try SessionsFixture.index(), now: now) { _ in },
                        size: CGSize(width: 380, height: 600))
        ViewHost.render(SessionDetailView(session: nil, index: nil, now: now) { _ in },
                        size: CGSize(width: 380, height: 600))
    }

    @Test("the page renders populated and in every banner state")
    func pageRendersInEveryState() async throws {
        var object = try SessionsFixture.object()
        object["schema_version"] = 2
        let schemaTwo = try SessionsFixture.encode(object)
        for result in [
            ProcessResult.ok(try SessionsFixture.data()),
            .failed(2, stderr: "Error: No such command 'index'."),
            .failed(127, stderr: "env: scoutctl: No such file or directory"),
            .failed(1, stderr: "boom"),
            .ok(schemaTwo),
        ] {
            let service = await SessionsFixture.loadedService(answering: result)
            ViewHost.render(SessionsView().environmentObject(service))
        }
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsPageSmokeTests`

Expected: the build fails with `cannot find 'SessionDetailView' in scope`.

- [ ] **Step 3: Add the detail pane**

Create `Scout/Sessions/Views/SessionDetailView.swift`:

```swift
import AppKit
import SwiftUI

/// The 380 pt detail pane (spec §6.4): one scrolling column — header and facts,
/// actions, reasons, PRs, first prompt, files touched, related sessions.
struct SessionDetailView: View {
    let session: AgentSession?
    let index: SessionIndex?
    let now: Date
    let onSelect: (String) -> Void

    @AppStorage("claudeCLIPath")       private var claudeCLIPath: String = ""
    @AppStorage("cliTerminal")         private var cliTerminal: String = CLITerminal.auto.rawValue
    @AppStorage("customLaunchCommand") private var customLaunchCommand: String = ""
    @State private var confirmingResume = false
    @State private var launchError: String?

    var body: some View {
        if let session, let index {
            content(session, index: index)
        } else {
            emptyState
        }
    }

    // MARK: Content

    private func content(_ session: AgentSession, index: SessionIndex) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header(session, index: index)
                actions(session, index: index)
                section("Why") {
                    ForEach(Array(session.stateReasons.enumerated()), id: \.offset) { _, reason in
                        Text("• \(reason)").font(DS.sans(12.5)).foregroundStyle(DS.Ink.p2)
                    }
                }
                if !session.prs.isEmpty {
                    section(session.prs.count == 1 ? "Pull request" : "Pull requests") {
                        ForEach(session.prs, id: \.self) { prRow($0) }
                    }
                }
                if let prompt = session.transcript?.firstPrompt, !prompt.isEmpty {
                    section("First prompt") {
                        Text(prompt)
                            .font(DS.sans(12.5))
                            .foregroundStyle(DS.Ink.p2)
                            .lineLimit(14)
                            .textSelection(.enabled)
                    }
                }
                if let files = session.transcript?.filesTouched, !files.isEmpty {
                    section("Files touched") {
                        ForEach(files, id: \.self) { path in
                            Text(path).font(DS.mono(11)).foregroundStyle(DS.Ink.p2).lineLimit(1).truncationMode(.middle)
                        }
                    }
                }
                related(session, index: index)
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .id(session.id)
        .confirmationDialog("This session is still open", isPresented: $confirmingResume) {
            Button("Resume in terminal anyway") { resume(session) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("It is still running in Claude. Resuming it in a terminal starts a second copy on the same conversation.")
        }
        .alert("Couldn't resume the session", isPresented: Binding(
            get: { launchError != nil }, set: { if !$0 { launchError = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(launchError ?? "")
        }
    }

    private func header(_ session: AgentSession, index: SessionIndex) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(session.displayTitle)
                .font(DS.serif(18, weight: .medium))
                .foregroundStyle(DS.Ink.p1)
                .textSelection(.enabled)
            HStack(spacing: 8) {
                Text(SessionsLayout.projectName(for: session.projectKey, in: index))
                    .font(DS.sans(11.5, weight: .medium))
                    .foregroundStyle(DS.Ink.p2)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(DS.Paper.sunk))
                SessionStatePill(state: session.state, isOpen: session.isOpen)
                if session.isOpen {
                    Label("open", systemImage: "circle.fill")
                        .labelStyle(.titleAndIcon)
                        .font(DS.sans(11))
                        .foregroundStyle(DS.Status.ok)
                }
            }
            VStack(alignment: .leading, spacing: 4) {
                if let branch = session.worktree?.branch { fact("Branch", branch, mono: true) }
                if let name = session.worktree?.name { fact("Worktree", name + (session.worktree?.dirty == true ? " (dirty)" : ""), mono: true) }
                if let model = session.model {
                    fact("Model", [SessionsFormat.shortModel(model), session.effort].compactMap { $0 }.joined(separator: " · "))
                }
                if let created = session.createdAt { fact("Created", created.formatted(date: .abbreviated, time: .shortened)) }
                fact("Last active", SessionsFormat.ago(session.lastActivityAt, now: now))
                if let turns = session.turns { fact("Turns", "\(turns)") }
                if let calls = session.transcript?.toolCalls { fact("Tool calls", "\(calls)") }
            }
        }
    }

    private func actions(_ session: AgentSession, index: SessionIndex) -> some View {
        HStack(spacing: 8) {
            Button {
                if session.isOpen { confirmingResume = true } else { resume(session) }
            } label: {
                Label("Resume", systemImage: "terminal")
            }
            .disabled(session.cliSessionID == nil)
            .help("Run claude --resume in your terminal")

            Button {
                if let url = session.pr?.webURL { NSWorkspace.shared.open(url) }
            } label: {
                Label("Open PR", systemImage: "arrow.triangle.pull")
            }
            .disabled(session.pr?.webURL == nil)

            Button {
                if let path = session.worktree?.path {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
                }
            } label: {
                Label("Reveal", systemImage: "folder")
            }
            .disabled(!worktreeExists(session))
            .help("Show the worktree in Finder")

            Button {
                let text = SessionHandoff.markdown(
                    for: session, projectName: SessionsLayout.projectName(for: session.projectKey, in: index))
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
            } label: {
                Label("Copy handoff", systemImage: "doc.on.doc")
            }
        }
        .controlSize(.small)
        .font(DS.sans(12))
    }

    private func prRow(_ pr: SessionPR) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text("\(pr.repo)#\(pr.number)").font(DS.mono(11.5, weight: .medium)).foregroundStyle(DS.Ink.p1)
                if pr.stale {
                    Text("cached").font(DS.sans(10.5)).foregroundStyle(DS.Status.warn)
                }
                Spacer(minLength: 0)
                if let url = pr.webURL {
                    Button("Open") { NSWorkspace.shared.open(url) }
                        .buttonStyle(.plainHit)
                        .font(DS.sans(11.5))
                        .foregroundStyle(DS.Accent.ink)
                }
            }
            Text(prDetail(pr)).font(DS.sans(11.5)).foregroundStyle(DS.Ink.p3)
        }
    }

    private func prDetail(_ pr: SessionPR) -> String {
        var parts = [pr.state == "unknown" ? "state unknown" : pr.state.lowercased()]
        if let review = pr.reviewLabel, review != "merged", review != "closed" { parts.append(review) }
        if pr.checks != "unknown" && pr.checks != "none" { parts.append("checks \(pr.checks)") }
        if pr.mergeState == "DIRTY" { parts.append("merge conflict") }
        if let fetched = pr.fetchedAt { parts.append("fetched \(SessionsFormat.ago(fetched, now: now))") }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private func related(_ session: AgentSession, index: SessionIndex) -> some View {
        let parent = SessionsLayout.parent(of: session, in: index)
        let children = SessionsLayout.children(of: session, in: index)
        if parent != nil || !children.isEmpty {
            section("Related sessions") {
                if let parent { relatedRow(parent, prefix: "Spawned by") }
                ForEach(children) { relatedRow($0, prefix: "Spawned") }
            }
        }
    }

    private func relatedRow(_ other: AgentSession, prefix: String) -> some View {
        Button { onSelect(other.id) } label: {
            HStack(spacing: 6) {
                Text(prefix).font(DS.sans(11)).foregroundStyle(DS.Ink.p4)
                SessionStateDot(state: other.state, isOpen: other.isOpen)
                Text(other.displayTitle).font(DS.sans(12.5)).foregroundStyle(DS.Ink.p1).lineLimit(1)
            }
        }
        .buttonStyle(.plainHit)
    }

    // MARK: Pieces

    private func section<Body: View>(_ title: String, @ViewBuilder body: () -> Body) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title.uppercased())
                .font(DS.sans(10.5, weight: .medium))
                .tracking(0.6)
                .foregroundStyle(DS.Ink.p4)
            body()
        }
    }

    private func fact(_ label: String, _ value: String, mono: Bool = false) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label).font(DS.sans(11.5)).foregroundStyle(DS.Ink.p4).frame(width: 78, alignment: .leading)
            Text(value).font(mono ? DS.mono(11.5) : DS.sans(11.5)).foregroundStyle(DS.Ink.p2).lineLimit(1).truncationMode(.middle)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "rectangle.stack.badge.person.crop")
                .font(.system(size: 36))
                .foregroundStyle(DS.Ink.p4)
            Text(index == nil ? "No session index yet" : "Pick a session")
                .font(DS.serif(18, weight: .medium))
                .foregroundStyle(DS.Ink.p2)
            if let index {
                let counts = SessionsLayout.stateCounts(index: index, filter: SessionsFilter(), now: now)
                Text(AgentSessionState.allCases.compactMap { state in
                    counts[state].map { "\($0) \(state.label.lowercased())" }
                }.joined(separator: " · "))
                .font(DS.sans(12.5))
                .foregroundStyle(DS.Ink.p3)
                if let generated = index.generatedAt {
                    Text("Index built \(SessionsFormat.ago(generated, now: now))")
                        .font(DS.sans(11.5))
                        .foregroundStyle(DS.Ink.p4)
                }
            } else {
                Text("Scout builds it with `scoutctl session index`.")
                    .font(DS.sans(12.5))
                    .foregroundStyle(DS.Ink.p3)
            }
        }
        .multilineTextAlignment(.center)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }

    // MARK: Actions

    private func worktreeExists(_ session: AgentSession) -> Bool {
        guard let path = session.worktree?.path else { return false }
        return FileManager.default.fileExists(atPath: path)
    }

    private func resume(_ session: AgentSession) {
        guard let id = session.cliSessionID else { return }
        let config = CLIConfig(
            claudePathOverride: claudeCLIPath,
            terminal: CLITerminal(rawValue: cliTerminal) ?? .auto,
            customCommand: customLaunchCommand
        )
        do {
            try ClaudeLauncher.resume(cliSessionID: id, cwd: SessionsFormat.resumeDirectory(for: session), config: config)
        } catch {
            launchError = error.localizedDescription
        }
    }
}
```

- [ ] **Step 4: Add the page**

Create `Scout/Sessions/Views/SessionsView.swift`:

```swift
import SwiftUI

/// The Sessions page (spec §6): every local Claude Code session by project and
/// state. Master (header + board or table) beside a 380 pt detail pane, as on
/// the Schedules page. Watching runs only while this page is on screen.
struct SessionsView: View {
    @EnvironmentObject var service: SessionIndexService

    @SceneStorage("sessionsView") private var viewMode: SessionsViewMode = .board
    @State private var filter = SessionsFilter()
    @State private var selectedID: String?

    var body: some View {
        let now = Date()
        HStack(spacing: 0) {
            master(now: now)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            Divider()
            SessionDetailView(session: selected, index: service.index, now: now) { selectedID = $0 }
                .frame(width: 380)
        }
        .background(DS.Paper.base)
        .onAppear { service.setVisible(true) }
        .onDisappear { service.setVisible(false) }
    }

    private var selected: AgentSession? {
        guard let id = selectedID else { return nil }
        return service.index?.sessions.first { $0.id == id }
    }

    private func master(now: Date) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            SessionsHeader(
                viewMode: $viewMode,
                filter: $filter,
                counts: service.index.map { SessionsLayout.stateCounts(index: $0, filter: filter, now: now) } ?? [:],
                projects: service.index.map(SessionsLayout.menuProjects(index:)) ?? [],
                service: service
            )
            Divider().background(DS.Rule.hard)
            banner
            content(now: now)
        }
    }

    @ViewBuilder
    private func content(now: Date) -> some View {
        if let index = service.index {
            switch viewMode {
            case .board:
                SessionsBoardView(index: index, filter: filter, now: now, selectedID: $selectedID)
            case .table:
                SessionsTableView(
                    rows: SessionsLayout.tableRows(index: index, filter: filter, now: now),
                    now: now,
                    selectedID: $selectedID
                )
            }
        } else {
            VStack(spacing: 10) {
                Spacer()
                if service.availability == .ok && service.lastError == nil {
                    ProgressView()
                    Text("Reading your Claude Code sessions…")
                        .font(DS.sans(13))
                        .foregroundStyle(DS.Ink.p3)
                }
                Spacer()
            }
            .frame(maxWidth: .infinity)
        }
    }

    // MARK: Banners

    @ViewBuilder
    private var banner: some View {
        switch service.availability {
        case .engineTooOld:
            bannerRow("Sessions needs scout-plugin 0.11.0 or later. Run /scout-update, then come back.",
                      symbol: "exclamationmark.triangle.fill", tint: DS.Status.warn)
        case .engineMissing:
            bannerRow(service.lastError ?? "scoutctl not found — check that scout-plugin is installed.",
                      symbol: "xmark.octagon.fill", tint: DS.Status.err)
        case .unsupportedSchema(let version):
            bannerRow("The session index is schema v\(version); this Scout reads v\(SessionIndex.supportedSchemaVersion). Update Scout. Showing the last index it could read.",
                      symbol: "exclamationmark.triangle.fill", tint: DS.Status.warn)
        case .ok:
            if let error = service.lastError {
                bannerRow(error, symbol: "xmark.octagon.fill", tint: DS.Status.err)
            } else if let unreadable = service.index?.unreadableSessions, unreadable > 0 {
                bannerRow("\(unreadable) session\(unreadable == 1 ? "" : "s") in the index couldn't be read and \(unreadable == 1 ? "is" : "are") not shown.",
                          symbol: "info.circle", tint: DS.Ink.p3)
            }
        }
    }

    private func bannerRow(_ text: String, symbol: String, tint: Color) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: symbol).foregroundStyle(tint)
            Text(text).font(DS.sans(12.5)).foregroundStyle(DS.Ink.p1).textSelection(.enabled)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 9)
        .background(tint.opacity(0.12))
    }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/SessionsPageSmokeTests`

Expected: `Test run with 3 tests in 1 suite passed`.

- [ ] **Step 6: Commit**

```bash
git add Scout/Sessions/Views/SessionDetailView.swift Scout/Sessions/Views/SessionsView.swift ScoutTests/Sessions/SessionsPageSmokeTests.swift
git commit -m "feat(sessions): detail pane, page shell and banners" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Wire the page into the app

This task adds:
- `SidebarItem.sessions`, after Control Center, with the needs-you badge;
- routing to `SessionsView`;
- the service in `AppState`, started with the other background work, so the badge is live before the page is opened.

`AppState` forwards only `needsYouCount`. Forwarding the service's `objectWillChange` would re-render the whole window on every refresh.

**Files:**
- Modify: `Scout/Shell/AppState.swift`
- Modify: `Scout/Shell/MainWindowView.swift`
- Modify: `Scout/Shell/SidebarView.swift`
- Modify: `ScoutTests/Shell/ViewSmokeTests.swift`
- Modify: `docs/feature-roadmap.md`
- Test: `ScoutTests/Sessions/AppStateSessionsTests.swift`

**Interfaces:**
- Consumes: `SessionIndexService`, `SessionsRefresh.productionWatchRoots()`, `SessionsView`, `AppState.Configuration.testing(scoutDirectory:runner:)` (`ScoutTests/Support/TestingConfiguration.swift`) and `waitUntil`.
- Produces:
  - `AppState.sessionIndexService` and `AppState.sessionsNeedsYouCount`.
  - `AppState.Configuration.agentSessionWatchRoots: [URL] = []`.
  - `SidebarItem.sessions`, with the status label `"sessions"`.
  - `SidebarView(selection:sessionsBadge:…)`.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/Sessions/AppStateSessionsTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("AppState — sessions wiring", .serialized)
struct AppStateSessionsTests {

    @Test func theSidebarBadgeFollowsTheIndex() async throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("sessions-appstate-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: vault) }
        let state = AppState(configuration: .testing(
            scoutDirectory: vault,
            runner: ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))))
        #expect(state.sessionsNeedsYouCount == 0)
        await state.sessionIndexService.refreshFast()
        await waitUntil("the badge never picked up the needs-you count") { state.sessionsNeedsYouCount == 1 }
    }

    @Test func noTestGraphWatchesTheRealSources() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("sessions-appstate-\(UUID().uuidString)")
        #expect(AppState.Configuration.testing(scoutDirectory: vault).agentSessionWatchRoots.isEmpty)
        #expect(AppState.Configuration.testHost().agentSessionWatchRoots.isEmpty)
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        #expect(SessionsRefresh.productionWatchRoots().map(\.path) == [
            home + "/Library/Application Support/Claude/claude-code-sessions",
            home + "/.claude/sessions",
            home + "/.claude/projects",
        ])
    }
}
```

In `ScoutTests/Shell/ViewSmokeTests.swift`:

**1. Nine destinations now.** Replace:

```swift
        #expect(labels.count == 8)
```

with:

```swift
        #expect(labels.count == 9)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/AppStateSessionsTests -only-testing:ScoutTests/ShellViewSmokeTests`

Expected: the build fails because `AppState` has no member `sessionsNeedsYouCount` or `sessionIndexService`.

- [ ] **Step 3: Wire `AppState`**

Make these replacements in `Scout/Shell/AppState.swift`, in order:

**1. The badge count, published on its own.** Replace:

```swift
    @Published private(set) var urgentActionCount: Int = 0
```

with:

```swift
    @Published private(set) var urgentActionCount: Int = 0
    /// Sessions that need you — the Sessions sidebar badge. Forwarded from
    /// `sessionIndexService.needsYouCount` alone, so the window does not
    /// re-render on every index refresh.
    @Published private(set) var sessionsNeedsYouCount: Int = 0
```

**2. The service property.** Replace:

```swift
    let claudeSessionService: ClaudeSessionService
```

with:

```swift
    let claudeSessionService: ClaudeSessionService
    /// The Sessions page's `scoutctl session index` driver.
    let sessionIndexService: SessionIndexService
```

**3. Build the service next to `ClaudeSessionService`.** Replace:

```swift
        let ccSessions = ClaudeSessionService(
            projectsDirectory: configuration.claudeSessionsDirectory
        )
```

with:

```swift
        let ccSessions = ClaudeSessionService(
            projectsDirectory: configuration.claudeSessionsDirectory
        )
        let sessionIndex = SessionIndexService(configuration: .init(
            scoutctl: scoutctlExe,
            argumentsPrefix: scoutctlArgsPrefix,
            runner: runner,
            fileEvents: events,
            indexFile: scoutDir.appendingPathComponent(".scout-cache/sessions-index.json"),
            watchRoots: configuration.agentSessionWatchRoots
        ))
```

**4. Store it.** Replace:

```swift
        self.claudeSessionService = ccSessions
```

with:

```swift
        self.claudeSessionService = ccSessions
        self.sessionIndexService = sessionIndex
```

**5. Forward only the badge count.** Replace:

```swift
        researchDoc.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
```

with:

```swift
        researchDoc.objectWillChange
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)
        sessionIndex.$needsYouCount
            .removeDuplicates()
            .receive(on: DispatchQueue.main)
            .sink { [weak self] count in self?.sessionsNeedsYouCount = count }
            .store(in: &cancellables)
```

**6. Start it with the other background work.** Replace:

```swift
        guard configuration.startsBackgroundWork else { return }
```

with:

```swift
        guard configuration.startsBackgroundWork else { return }

        // Loads the last index from disk, then keeps it current: the sidebar
        // badge needs it before the Sessions page is ever opened.
        sessionIndex.start()
```

**7. The watch roots, defaulting to none.** Replace:

```swift
        /// When false the initializer wires the object graph but starts no
        /// timers, watches, loads, or subprocesses.
        var startsBackgroundWork: Bool
```

with:

```swift
        /// When false the initializer wires the object graph but starts no
        /// timers, watches, loads, or subprocesses.
        var startsBackgroundWork: Bool
        /// Directories whose changes rebuild the session index while the
        /// Sessions page is open (`SessionsRefresh.productionWatchRoots()` in
        /// production). Defaults to none, so a test graph never watches the
        /// real `~/.claude` or the desktop app's store.
        var agentSessionWatchRoots: [URL] = []
```

**8. Production watches the real sources.** Replace:

```swift
                parseCacheURL: SessionLogService.defaultParseCacheURL(),
                startsBackgroundWork: true
            )
```

with:

```swift
                parseCacheURL: SessionLogService.defaultParseCacheURL(),
                startsBackgroundWork: true,
                agentSessionWatchRoots: SessionsRefresh.productionWatchRoots()
            )
```

`TestingConfiguration.swift` and `testHost()` need no change: the new field defaults to no watch roots.

- [ ] **Step 4: Add the destination and the sidebar row**

In `Scout/Shell/MainWindowView.swift`:

**1. Pass the badge.** Replace:

```swift
            SidebarView(selection: $selection,
                        proposalsBadge: proposalsService.pendingCount,
```

with:

```swift
            SidebarView(selection: $selection,
                        sessionsBadge: appState.sessionsNeedsYouCount,
                        proposalsBadge: proposalsService.pendingCount,
```

**2. Route to the page.** Replace:

```swift
        case .controlCenter:
            ControlCenterView()
```

with:

```swift
        case .controlCenter:
            ControlCenterView()
        case .sessions:
            SessionsView()
                .environmentObject(appState.sessionIndexService)
```

**3. The new destination, after Control Center.** Replace:

```swift
    case controlCenter, actionItems, schedules, proposals, wishlist, research, knowledgeBase, settings
```

with:

```swift
    case controlCenter, sessions, actionItems, schedules, proposals, wishlist, research, knowledgeBase, settings
```

**4. Its status-bar label.** Replace:

```swift
        case .controlCenter: return "control"
```

with:

```swift
        case .controlCenter: return "control"
        case .sessions:      return "sessions"
```

In `Scout/Shell/SidebarView.swift`:

**1. The badge input.** Replace:

```swift
    @Binding var selection: SidebarItem
```

with:

```swift
    @Binding var selection: SidebarItem
    /// Count of agent sessions that need you — drives the badge on the
    /// Sessions row. Hidden when zero.
    var sessionsBadge: Int = 0
```

**2. The row, after Control Center.** Replace:

```swift
            row(.controlCenter, label: "Control Center", system: "chart.bar.doc.horizontal")
```

with:

```swift
            row(.controlCenter, label: "Control Center", system: "chart.bar.doc.horizontal")
            row(.sessions,      label: "Sessions",       system: "rectangle.stack.badge.person.crop", badge: sessionsBadge)
```

- [ ] **Step 5: Point the roadmap at this plan**

In `docs/feature-roadmap.md`, under `## F-2. Sessions page`, replace:

```markdown
> shared engine session index + a project × state Sessions page; the SpriteKit world is an optional later view.
```

with:

```markdown
> shared engine session index + a project × state Sessions page; the SpriteKit world is an optional later view.
> The page is built by [plan 3](superpowers/plans/2026-10-03-agent-sessions-plan-3-sessions-page.md).
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/AppStateSessionsTests -only-testing:ScoutTests/ShellViewSmokeTests`

Expected: `Test run with 9 tests in 2 suites passed` (2 in `AppStateSessionsTests`, 7 in `ShellViewSmokeTests`).

- [ ] **Step 7: Commit**

```bash
git add Scout/Shell ScoutTests/Shell/ViewSmokeTests.swift ScoutTests/Sessions/AppStateSessionsTests.swift docs/feature-roadmap.md
git commit -m "feat(sessions): Sessions sidebar page with a needs-you badge" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Verify, accept on real data, open the PR

- [ ] **Step 1: Run the whole suite twice, with coverage**

Run each command twice:

```bash
xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests -resultBundlePath "$SCRATCH/TestResults.xcresult"
./scripts/check-coverage.sh "$SCRATCH/TestResults.xcresult"
```

`$SCRATCH` is any scratch folder outside the repo. Delete the `.xcresult` between runs.

Expected:
- Both runs print `Test run with` about 980 tests and pass.
- Coverage is at or above `scripts/coverage-floor.txt`.

First check whether #123 has merged (`gh pr view 123 --repo Raven-Scout/Scout --json state`), and rebase onto `main` if it has.
- **With #117 and #123 both on `main`,** no failure is expected in `FileWatcherTests`, `ActionItemsIntegrationTests`, `FakeScoutRunIntegrationTests` or `ConnectorHealthHotPathTests`. Investigate any failure there before going on.
- **Until #123 merges,** a failure counts as a known timing flake only if all of the following hold. Record any flake in the PR description.
  - It is confined to `FileWatcherTests`, `ActionItemsIntegrationTests` or `FakeScoutRunIntegrationTests`.
  - That suite passes when run alone.
  - The next full run passes.

Any other failure is a bug in this branch.

- [ ] **Step 2: Check for warnings**

Run:

```bash
xcodebuild clean build -scheme Scout -destination 'platform=macOS' 2>&1 | grep -F "warning:" | grep -E "Scout/Sessions/|ClaudeLauncher.swift|Shell/AppState.swift|SidebarView.swift|MainWindowView.swift"
```

Expected: no output.

- [ ] **Step 3: Check that the diff is anonymised**

Run:

```bash
git diff main -- Scout ScoutTests | grep -E '^\+' | grep -oE '/Users/[A-Za-z0-9._-]+' | sort -u
git diff main -- Scout ScoutTests | grep -E '^\+' | grep -nE '\b(AI|KAI|ST)-[0-9]+' 
```

Expected: the first command prints only `/Users/alex`. The second prints nothing.

- [ ] **Step 4: Accept on real data**

Build and run the Debug app from this worktree. Several Scout.app builds can exist at once, so before judging anything, check that the running process is this build: compare `ps -o comm= -p <pid>` with this worktree's DerivedData path.

Then go through each check:
1. Open **Sessions**. Lanes appear within a few seconds, and the session you are working in shows *running*.
2. Within about 2 minutes of opening the page, the PR chips match GitHub for three PRs you spot-check.
3. Start a new desktop session. Its card appears within about 5 seconds. Quit it: its *open* indicator goes within about 5 seconds.
4. Leave a session idle. About 2.5 minutes after its last activity it moves from *running* to *parked*, without anything else changing.
5. Switch between Board and Table, then quit and relaunch. The choice persists.
6. Try the filters: the state chips, the project menu, search, *Show Scout's own runs* and *Show recently done*. Expand and collapse *Stale (n)*.
7. Resume a closed session: your configured terminal opens `claude --resume …` in its folder. Resume an open session: you are asked first.
8. Try *Open PR*, *Reveal* and *Copy handoff* (paste the result somewhere to check it).
9. Switch to Control Center. Within 5 minutes, the sidebar badge equals the number of needs-you cards.
10. While sessions are running, Scout's CPU stays low. `pgrep -fl "scoutctl session index" | wc -l` never exceeds 2.

Only attach a screenshot if every title, path and branch in it is anonymised.

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin feat/agent-sessions-page
gh pr create --repo Raven-Scout/Scout --base main --head feat/agent-sessions-page \
  --title "feat(sessions): Sessions page — agent sessions by project and state (plan 3)" \
  --body-file "$SCRATCH/pr-body.md"
```

Write `$SCRATCH/pr-body.md` before running the second command. It must cover:
- what the page does;
- the two-lane refresh (spec §10.1);
- the test counts from Steps 1–2;
- any flakes recorded in Step 1;
- the acceptance results from Step 4.

End it with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

# Part B — scout-plugin

### Task 11: Fetch PRs only for sessions that are not archived

Spec §10.7 explains why. This change lands as its own scout-plugin PR, and Part A does not depend on it.

**Files:**
- Modify: `engine/scout/sessions/index.py` (step 4 of `build_index`)
- Modify: `engine/tests/unit/test_sessions_index.py`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes:
  - `github.refresh_pr_states(refs, *, cache, now, ttl, cap, runner)`;
  - `desktop.PRRef.key`;
  - `AgentSession.is_archived`;
  - from the tests: `write_desktop_record`, `support_dir`, `claude_home`, `gh.pr_info_from_payload` and `gh.write_pr_cache`.
- Produces: no new API.
  - `build_index` fetches with `gh` only the refs linked to at least one session that is not archived.
  - Every other ref is served from the cache, or from `unknown`/legacy-terminal, without calling `gh`.
  - `source_counts.prs_refreshed` still counts successful fetches.

- [ ] **Step 0: Create a worktree and a fresh venv**

Do not switch the branch of the primary checkout. From `~/scout-plugin`:

```bash
git fetch origin
git worktree add ../scout-plugin-worktrees/sessions-pr-live-only -b fix/sessions-pr-fetch-live-only origin/main
cd ../scout-plugin-worktrees/sessions-pr-live-only/engine
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/pytest tests/ -q
```

Expected: every test passes. Record the count; in the dry run it was 2544 passed, 13 skipped. If a watch or integration test fails, run it alone before going on.

- [ ] **Step 1: Write the failing tests**

Make these replacements in `engine/tests/unit/test_sessions_index.py`:

**1. Import `PRRef`.** Replace:

```python
from scout.sessions import cli_home
```

with:

```python
from scout.sessions import cli_home
from scout.sessions.desktop import PRRef
```

**2. An archived session's PR is no longer fetched, even with budget left.** Replace:

```python
@pytest.mark.parametrize(("cap", "expected"), [(1, ["2"]), (2, ["2", "1"]), (3, ["2", "1", "3"])])
```

with:

```python
@pytest.mark.parametrize(("cap", "expected"), [(1, ["2"]), (2, ["2", "1"]), (3, ["2", "1"])])
```

**3. And its comment.** Replace:

```python
    # C is the most recently active but archived: its PR is fetched last.
```

with:

```python
    # C is the most recently active but archived: its PR is never fetched, even with budget left.
```

Then add the following at the end of the file:

```python
def _gh_options(fake_data_dir: Path, runner: gh.Runner, *, cap: int = 25) -> BuildOptions:
    return BuildOptions(
        data_dir=fake_data_dir,
        settings=AgentSessionsSettings(pr_fetch_cap=cap),
        claude_home=claude_home(),
        support_dir=support_dir(),
        now=NOW,
        gh_runner=runner,
        gh_available=lambda: True,
        toplevel=lambda p: None,
        pid_alive=lambda pid: False,
    )


def _seed_pr_cache(fake_data_dir: Path, number: int, payload: dict, *, fetched_ago: timedelta) -> None:
    ref = PRRef(number=number, repo="example-org/example-repo", url=None, legacy_state=None)
    info = gh.pr_info_from_payload(ref, payload, fetched_at=dt_to_iso(NOW - fetched_ago))
    gh.write_pr_cache(fake_data_dir / ".scout-cache" / gh.PR_CACHE_FILENAME, {ref.key: info})


def test_archived_sessions_prs_never_take_the_fetch_budget_from_a_live_one(fake_data_dir: Path) -> None:
    s = support_dir()
    repo = "example-org/example-repo"
    # B is live and its PR was fetched 20 minutes ago, past the 10-minute TTL. Thirty archived
    # sessions link PRs that were never fetched; those used to sort first and take the whole cap.
    write_desktop_record(s, "local_B", lastActivityAt=MS - 3_600_000, prs=[{"prNumber": 2, "repo": repo}])
    for n in range(30):
        write_desktop_record(
            s, f"local_C{n:02d}", isArchived=True, lastActivityAt=MS - 60_000, prs=[{"prNumber": 100 + n, "repo": repo}]
        )
    _seed_pr_cache(
        fake_data_dir, 2, {"state": "OPEN", "mergeStateStatus": "BLOCKED"}, fetched_ago=timedelta(minutes=20)
    )
    calls: list[str] = []

    def runner(argv: list[str]) -> str | None:
        calls.append(argv[2])
        return json.dumps({"state": "OPEN", "reviewDecision": "CHANGES_REQUESTED", "mergeStateStatus": "CLEAN"})

    idx = build_index(_gh_options(fake_data_dir, runner, cap=1))
    assert calls == ["2"]
    b = next(x for x in idx.sessions if x.id == "local_B")
    assert b.state == "needs_you" and b.pr is not None and b.pr.review_decision == "CHANGES_REQUESTED"
    archived = [x for x in idx.sessions if x.is_archived]
    assert len(archived) == 30
    assert all(x.pr is not None and x.pr.state == "unknown" and not x.pr.stale for x in archived)


def test_an_archived_sessions_cached_pr_state_is_served_and_kept_without_a_fetch(fake_data_dir: Path) -> None:
    repo = "example-org/example-repo"
    write_desktop_record(support_dir(), "local_C", isArchived=True, prs=[{"prNumber": 3, "repo": repo}])
    _seed_pr_cache(
        fake_data_dir,
        3,
        {"state": "OPEN", "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN"},
        fetched_ago=timedelta(days=2),
    )
    calls: list[str] = []
    idx, _ = run(opts=_gh_options(fake_data_dir, lambda argv: calls.append(argv[2])))
    c = next(x for x in idx.sessions if x.id == "local_C")
    assert calls == []
    assert c.state == "done" and c.pr is not None and c.pr.review_decision == "APPROVED"
    pr_file = fake_data_dir / ".scout-cache" / gh.PR_CACHE_FILENAME
    assert set(json.loads(pr_file.read_text(encoding="utf-8"))) == {f"{repo}#3"}


def test_a_pr_linked_to_a_live_and_an_archived_session_is_fetched(fake_data_dir: Path) -> None:
    s = support_dir()
    repo = "example-org/example-repo"
    write_desktop_record(s, "local_A", isArchived=True, lastActivityAt=MS - 60_000, prs=[{"prNumber": 4, "repo": repo}])
    write_desktop_record(s, "local_B", lastActivityAt=MS - 3_600_000, prs=[{"prNumber": 4, "repo": repo}])
    calls: list[str] = []

    def runner(argv: list[str]) -> str | None:
        calls.append(argv[2])
        return json.dumps({"state": "OPEN", "mergeStateStatus": "BLOCKED"})

    idx = build_index(_gh_options(fake_data_dir, runner))
    assert calls == ["4"]
    assert all(x.pr is not None and x.pr.state == "OPEN" for x in idx.sessions)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sessions_index.py -q`

Expected: 3 failed, 31 passed. The failures are:
- `test_cold_cache_fetches_recent_live_sessions_prs_first[3-expected2]`
- `test_archived_sessions_prs_never_take_the_fetch_budget_from_a_live_one`
- `test_an_archived_sessions_cached_pr_state_is_served_and_kept_without_a_fetch`

`test_a_pr_linked_to_a_live_and_an_archived_session_is_fetched` already passes. It guards against the fix dropping a PR that a live session still links.

- [ ] **Step 3: Implement**

In `engine/scout/sessions/index.py`, inside `build_index`:

**1. Replace the PR-state block (step 4).** Replace:

```python
    # 4. PR state. refresh_pr_states fetches oldest-`fetched_at` first and never-fetched refs
    # tie (stable sort), so on a cold cache this order decides who gets the capped fetches:
    # live sessions before archived ones, most recently active first.
    by_priority = sorted(sessions, key=lambda x: (x.is_archived, -_recency(x)))
    all_refs = [ref for sess in by_priority for ref in refs_by_session.get(sess.id, [])]
    pr_cache = github.load_pr_cache(cache_dir / github.PR_CACHE_FILENAME)
    pr_loaded = dict(pr_cache)  # refresh_pr_states inserts new PRInfo objects, never mutates old ones
    fetched = 0
    resolved: dict[str, PRInfo] = {}
    if all_refs:
        if opts.use_gh and opts.gh_available():
            resolved, e5, fetched = github.refresh_pr_states(
                all_refs,
                cache=pr_cache,
                now=opts.now,
                ttl=timedelta(minutes=s.pr_refresh_minutes),
                cap=s.pr_fetch_cap,
                runner=opts.gh_runner,
            )
            errors.extend(e5)
        else:
            if opts.use_gh:
                errors.append(SourceError(source="gh", message="gh not found on PATH — PR states unknown"))
            # cap=0: serve cached / legacy-terminal / unknown without calling anything.
            resolved, _, fetched = github.refresh_pr_states(
                all_refs, cache=pr_cache, now=opts.now, ttl=timedelta(days=36500), cap=0, runner=lambda argv: None
            )
```

with:

```python
    # 4. PR state. Only PRs linked to a session that is not archived are fetched: an archived
    # session is `done` whatever its PR says (rule 1), so its PRs keep their cached state (or
    # read `unknown`) and never spend the per-build fetch cap. refresh_pr_states fetches
    # oldest-`fetched_at` first and never-fetched refs tie (stable sort), so on a cold cache
    # this order decides who gets the capped fetches: most recently active first.
    by_priority = sorted(sessions, key=lambda x: (x.is_archived, -_recency(x)))
    all_refs = [ref for sess in by_priority for ref in refs_by_session.get(sess.id, [])]
    live_keys = {ref.key for sess in sessions if not sess.is_archived for ref in refs_by_session.get(sess.id, [])}
    pr_cache = github.load_pr_cache(cache_dir / github.PR_CACHE_FILENAME)
    pr_loaded = dict(pr_cache)  # refresh_pr_states inserts new PRInfo objects, never mutates old ones
    fetched = 0
    resolved: dict[str, PRInfo] = {}
    if all_refs:
        fetching = opts.use_gh and opts.gh_available()
        if fetching:
            resolved, e5, fetched = github.refresh_pr_states(
                [ref for ref in all_refs if ref.key in live_keys],
                cache=pr_cache,
                now=opts.now,
                ttl=timedelta(minutes=s.pr_refresh_minutes),
                cap=s.pr_fetch_cap,
                runner=opts.gh_runner,
            )
            errors.extend(e5)
        elif opts.use_gh:
            errors.append(SourceError(source="gh", message="gh not found on PATH — PR states unknown"))
        # cap=0: serve cached / legacy-terminal / unknown without calling anything.
        served, _, _ = github.refresh_pr_states(
            [ref for ref in all_refs if not fetching or ref.key not in live_keys],
            cache=pr_cache,
            now=opts.now,
            ttl=timedelta(days=36500),
            cap=0,
            runner=lambda argv: None,
        )
        resolved.update(served)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_sessions_index.py -q`

Expected: `34 passed`.

- [ ] **Step 5: Run lint, types and the whole suite**

```bash
.venv/bin/ruff format scout tests && .venv/bin/ruff check scout tests && .venv/bin/mypy scout
.venv/bin/pytest tests/ -q
```

Expected:
- ruff and mypy are clean.
- The full suite gives the Step 0 count plus 3 tests: 2547 passed, 13 skipped, if Step 0 matched the dry run.

- [ ] **Step 6: Add the changelog entry**

In `CHANGELOG.md`, add this bullet as the first entry of the `### Fixed` list under `## [Unreleased]`. If a release has emptied Unreleased since this plan was written, add the `### Fixed` heading first.

```markdown
- **PR state is fetched only for sessions that are not archived** (`engine/scout/sessions/index.py`) — an archived session is `done` whatever its PR says, so its PRs no longer spend the 25-fetch budget; they keep their cached state or read `unknown`. Never-fetched refs are fetched first, so on a machine where 96 PRs on archived sessions had never been fetched, those took about four builds' worth of fetches ahead of the open PRs on live sessions. A PR linked to a live session and an archived one is still fetched. Spec: scout-app `docs/superpowers/specs/2026-09-08-agent-sessions-design.md` §10.7.
```

- [ ] **Step 7: Commit, push, open the PR**

```bash
git add engine/scout/sessions/index.py engine/tests/unit/test_sessions_index.py CHANGELOG.md
git commit -m "fix(sessions): fetch PR state only for sessions that are not archived" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin fix/sessions-pr-fetch-live-only
gh pr create --repo Raven-Scout/scout-plugin --base main --head fix/sessions-pr-fetch-live-only \
  --title "fix(sessions): fetch PR state only for sessions that are not archived" \
  --body-file "$SCRATCH/plugin-pr-body.md"
```

Write `$SCRATCH/plugin-pr-body.md` before running `gh pr create`. It should say:
- why: spec §10.7, with the 96-ahead-of-23 measurement;
- what changed;
- the test counts from Steps 4–5.

End it with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
