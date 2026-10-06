# Wishlist/Research: resolved-item outcomes & per-item activity (#43)

**Date:** 2026-06-28 · **Revised:** 2026-10-03 (rev 2, re-validated against `main` @ `7a037c9`)
**Issue:** [#43](https://github.com/Raven-Scout/Scout/issues/43) — "see the outcome of resolved items (link to the resolving run)"
**Follows:** #41 (editable priority/status, shipped in #61), which made the deferral of #43 to its own spec.
**Plan:** `docs/superpowers/plans/2026-06-29-perfile-resolved-outcomes.md`

## Summary

Make the **work behind each Wishlist/Research item visible and traceable**. Today a resolved (done/dropped) item just moves to the collapsible "Resolved" section showing its final body. There is no way to see *which run* resolved it or *what changed*. Active items are equally opaque about progress so far.

This adds a per-item **activity timeline**, derived entirely from git, shown in a **detail pane**. The pane lists the commits that touched the item's file, newest first. Each row is labeled with the Scout run that made it, or "You" for Scout.app's own writes, and expands to that commit's change to the item. For resolved items, the commit that set the terminal `status:` is surfaced as the **outcome**: "Resolved by Dreaming · Jun 22".

## Rev 2 — what changed since the June draft, and why

The June draft was written against a `main` that is ~38 commits older. Re-validating every code reference, and checking the heuristic against the real vault (read-only `git log` on the item folders plus the `.scout-logs/` run logs), turned up several issues. All are fixed here. The decisions marked **(new)** below need sign-off.

| # | Finding | Rev 2 response |
|---|---|---|
| 1 | **Run start times drift with the machine's timezone.** `SessionLogService.parseFilename` reads the log filename's wall-clock time in `TimeZone.current`. Run logs in the vault were written across three zones as the user travelled, so a run logged in one zone and viewed from another is shifted by the zone difference. Its commit window either balloons by hours or inverts. Measured on the vault: with filename starts, 17% of linked item commits matched **more than one** run. With the zone-aware start that every log already carries in its `=== … run starting at <date> <ZONE> <year> ===` header, ambiguity drops to ~1%. | Parse the header start (zone-aware, through the existing `parseScoutTimestamp`) and use it for commit windows. This is plan Task 1. It also fixes Control Center's own run→commits lookup for off-zone runs. |
| 2 | **Subject prefixes don't match run types.** Weekend briefings commit as `weekend briefing [..]:`, which `RunType.commitsPrefix` (`"briefing"`) never matches. `deriveType` also buckets `scout-…` logs into briefing vs consolidation by the *local* hour, so a scheduled briefing viewed from another zone is typed `.consolidation` and its `briefing …` commits are filtered out. | Attribute by the **family the commit subject claims** (`weekend briefing` / `briefing` / `consolidation` / `dreaming` / `research`), matched to the run's **runner** (`run-scout.sh` / `run-dreaming.sh` / `run-research.sh`), which is stable, instead of the derived `RunType`. Both directions share one helper (Task 2). |
| 3 | **"Newest commit = resolving commit" is wrong** whenever a session touches the file after resolving it, and when the status change hasn't been committed yet. | **(new)** The resolving commit is the newest commit whose patch *adds* the item's current terminal `status:` line, which is exactly what the issue asks for ("the commit that flipped `status:`"). If none exists, the outcome reads "Resolved — not committed yet". |
| 4 | **Whole-commit diffs are the wrong unit.** One scheduled run commits dozens of vault files in one commit. The June draft showed `git diff <sha>^..<sha>` for the whole commit (and `^` fails on a root commit). | **(new)** Each row shows the patch **for this item's file only**: one `git log --follow --patch -- <file>` call per opened item returns every revision's file-scoped patch. Expanding a row also lists lazily the *other* files that commit touched (names only, `git show --name-only`), which covers the issue's "KB files touched" for research. |
| 5 | **Not every non-run commit is "you".** Item files are also committed by interactive vault sessions, plugin upgrades, and other sessions' concurrent `git add -A`. | **(new)** Four source labels: a linked **run**; a run **family** with no run log in the window (labeled, not linkable); **You** for Scout.app's `app:` writes; **Other** for everything else. The row always shows the full commit subject, so "Other" is never opaque. |
| 6 | **Card-body tap conflicts.** Card bodies are `.textSelection(.enabled)` markdown, and cards host a priority `Menu` and action buttons. A card-wide `onTapGesture` fights text selection. | **(new)** An explicit **History** action button on every card (active and resolved) opens the pane. The selected card gets the leading accent bar. |
| 7 | **`PerFileListView` is a `VStack` on purpose** (#83, fourth occurrence). The June plan's list rewrite reintroduced `LazyVStack`. | The plan keeps the `VStack` and its comment verbatim. |
| 8 | **Stale snapshot in the pane.** `PerFileDocumentService` still reparses synchronously and republishes `items` unconditionally on every FSEvent (#103 follow-up). A pane holding a `PerFileItem` copy shows stale status after Start/Done/Drop. | The list stores the **selected item id**, looks the live item up from `docService.items`, and closes the pane if the file disappears. History reloads via `.task(id: item)`. `PerFileItem` is `Equatable`, so no-op republishes don't refetch. Fixing the #103 follow-up itself stays out of scope. |
| 9 | **Control Center's detail layout moved** (#54). The side panel now sits beside the Sessions list inside `primaryColumn`, and ⌘⇧F toggles expand/collapse. | The pane copies the current `ControlCenterView` pieces: `DetailPresentation`, `detailHeader`, the hidden ⌘. / ⌘⇧F buttons, and the 460 pt side width. |

### Considered: the Agent Sessions index as the attribution source

#112/#114 added `scoutctl session index` → `.scout-cache/sessions-index.json` (schema v1), which flags Scout's own runs (`is_scout_run`). It is **not** a better attribution source for v1:

- It records no commit SHAs, so the link would still be time-window based.
- `transcript.files_touched` is capped at 10 per session and only deep-parsed for 14 days. A dreaming run touches far more files, and most item history is older than 14 days.
- Scout.app has no reader for it on `main` yet. The Sessions page is #119, still open.

The run logs plus the zone-aware start already link ~76% of run-prefixed item commits to exactly one run. Most of the rest are `research …` commits with no `research-*.log` near them. Once the app reads the index (#119), its Scout-run sessions are the natural **second** source for exactly those commits. That is listed as a follow-up below.

The vault's own `knowledge-base/session-log.md` also backfills commit hashes, but it is model-written prose in a markdown table. It is not a contract the app should parse.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Mechanism** | App-side, git-derived only | Zero scout-plugin changes, single repo, ships now. |
| **Scope** | Resolved **and** active items | Same machinery. "Outcome" framing for resolved items, "work so far" for active ones. No live "working now" indicator. |
| **Layout** | Dedicated detail pane | Mirrors Control Center's `.side`/`.full` panel. No navigation-model surgery. |
| **Diff unit** *(rev 2)* | The item file's patch per commit, plus a lazy "other files in this commit" list | Item-relevant evidence without a 40-file wall; one git call per opened item. |
| **Resolving commit** *(rev 2)* | Newest commit that adds the current terminal `status:` line | What the issue asks for; robust to later touches. |
| **Attribution** *(rev 2)* | Subject family ↔ runner + zone-aware window | Fixes weekend-briefing misses and timezone drift (findings 1–2). |
| **Entry point** *(rev 2)* | "History" button on each card | Avoids gesture conflicts with selectable markdown and the controls. |

## Non-goals (v1)

- No scout-plugin changes and no `resolved_by:` frontmatter (see follow-ups).
- No live "working now" indicator.
- No reading of the Agent Sessions index (see above).
- No diff syntax highlighting or truncation. The diff is a scrollable raw patch with +/− coloring.
- No fix for `deriveType`'s local-hour bucketing or the cost-tracker lookup, which also keys on the filename start. Both are noted as Control Center follow-ups. This feature sidesteps them by attributing by runner and subject family.

## Architecture & components

| Piece | Type | Responsibility |
|---|---|---|
| `SessionLogService.ParsedBody.startedAt` + `Run.headerStartedAt` | change | Parse the zone-aware `run starting at` header (case-insensitive, all historical casings). `Run.startedAt` and `Run.id` keep their filename-derived values, so nothing that formats `startedAt` back into a wall-clock string (`ClaudeSessionService`'s title match) changes behavior. Bump `ParseCache.version` to 2 so cached bodies re-parse once. |
| `CommitFamily` | new enum (`Scout/Services/CommitRunLinker.swift`) | `weekend briefing` / `briefing` / `consolidation` / `dreaming` / `research`, parsed from a commit subject (longest token first, token must be followed by space, `:`, `[` or end of string). Each maps to its `runnerScript` and a display name. |
| `Run.commitWindow(now:)` + `Run.claims(_:)` | new (same file) | **The single definition of a run's commit window**, used by both directions: `(headerStartedAt ?? startedAt) − 30 s` through `(endedAt ?? min(now, start + type.orphanAfter)) + 5 min`, never inverted. Capping an un-ended run at `orphanAfter` stops a run with no finish marker from claiming commits forever. `claims` checks that the subject's family runs on this run's runner. |
| `CommitRunLinker.run(for:in:now:)` | new | Reverse lookup: among runs that claim the commit and whose window contains its timestamp, pick the latest-starting one. |
| `SessionLogService.commits(for:)` | change | Uses `commitWindow` + `claims`, so the forward (Control Center) and reverse mappings cannot drift. |
| `FileRevision` | new model | `{ commit: Commit, patch: String }`. The patch covers the item's file only. |
| `GitService.fileHistory(relativePath:)` | new | `git log --follow --patch --no-color --no-ext-diff --format=<RS>%H<US>%h<US>%ct<US>%s -- <relPath>` → `[FileRevision]`, newest first. A record starts at a line beginning with RS, which no patch line can. Insertions and deletions are counted from the scoped patch. Non-zero exit throws. |
| `GitService.filesChanged(inCommit:)` | new | `git show --name-only --format= --no-color <sha>` → `[String]`, for the "also changed" list. |
| `ActivitySource`, `ItemActivityEntry`, `ItemOutcome`, `ItemActivity` | new models + pure builders | Label each revision (`.run(Run, CommitFamily)` / `.family` / `.app` / `.other`). Find the resolving revision (`+status: <current terminal value>`). Derive the outcome (`.resolved(by:)` / `.resolvedUncommitted`). Compute the repo-relative path, returning nil outside the repo. |
| `PerFileItemActivityModel` | new `@MainActor ObservableObject` | Loads `[FileRevision]` for one item (`.loading` / `.loaded` / `.unavailable` / `.failed`). Lazily loads each commit's other-files list. Holds no runs: labeling happens at render time from the live `SessionLogService.runs`, so runs that finish loading later still link. |
| `PerFileItemDetailView`, `CommitDiffView` | new views | Outcome line, then a timeline whose rows expand to the patch, the other-files list, and "Open run in Control Center". |
| `PerFileItemCardView` | change | `isSelected` (leading accent bar) and `onShowHistory` (a **History** action button). |
| `PerFileListView` | change | `selectedItemID` + `detailIsFull` state. The live item is looked up from `docService.items`. The list sits in an HStack with a 460 pt side panel, or a `.full` overlay. ⌘. closes, ⌘⇧F toggles. The `VStack` (#83) is kept. |
| `AppState`, `MainWindowView`, `ControlCenterView` | change | `requestOpenRun(_:)` publishes `pendingRunToOpen` + `requestedSidebar`. `MainWindowView` switches its `selection`; `ControlCenterView` opens that run's `.side` detail and clears the intent. |

## Data flow

1. Click **History** on a card → `PerFileListView.selectedItemID = item.id`; the side pane opens with the card highlighted.
2. `PerFileItemDetailView` runs `.task(id: item) { await model.load(item) }`. The model computes the item's repo-relative path. If the file is outside the vault repo (a custom folder override), the state is `.unavailable` with an explanation. Otherwise it calls `GitService.fileHistory(relativePath:)`.
3. At render time, `ItemActivity.entries(revisions:runs:status:now:)` labels each revision from `appState.sessionLogService.runs` and flags the resolving revision.
4. The outcome line: *"Resolved by Dreaming · Jun 22"*, *"Resolved by you · Jun 22"* (an `app:` commit), *"Resolved · Jun 22"* (other or unlinked sources), or *"Resolved — not committed yet"*. Active items show no outcome line.
5. Expanding a row renders its patch immediately (already loaded) and fetches that commit's other-files list once.
6. On a run-linked row, **Open run in Control Center** → `AppState.requestOpenRun(run.id)` → the sidebar switches → Control Center opens that run's detail.

## The run link and its limits

The link is still a heuristic: a time window plus a subject claim. Rev 2 makes it zone-correct and family-correct, but it can still:

- **miss** a run whose log isn't in `.scout-logs/`, or a commit made by a run family with no runner log (most unlinked commits today). These keep their family label and simply have no "Open run" button.
- **mis-attribute** when another session's concurrent `git add -A` sweeps this item's change into *its* commit. The subject then names the other session. No time-window heuristic can detect this.

That is acceptable because **the item's patch is always shown regardless of the link**. A missing or wrong badge never hides what actually changed. The link is a shortcut to the run log, not the source of truth.

## Edge & error handling

- **Never committed** (file only in the working tree) → empty timeline, "No activity yet — this item hasn't been committed."
- **Resolved, status change not yet committed** → outcome "Resolved — not committed yet"; the timeline still shows earlier commits.
- **Item folder outside the vault repo** (`wishlistPath` / `researchQueuePath` override) → `.unavailable`, with the reason, not an error.
- **`git` failure** → an inline `.failed` row with the message. The list stays usable, and errors are never swallowed (#47).
- **Renames** → `--follow`. Items migrated out of the old single-file `WISHLIST.md` start their history at the per-file migration commit. Their pre-split history is not reconstructed.
- **Merge commits** that touch the file appear with an empty patch ("No change to this file in this commit").
- **Large patch** → a scrollable container capped at 320 pt tall. No truncation.
- **Cost** → one `git log --follow --patch` per opened item, ~0.2–0.7 s on the real vault (item files have 1–14 commits). Unopened cards cost nothing.

## Testing

All through the existing `ProcessRunner` seam (`ScriptedRunner`) and pure functions, using Swift Testing:

- **Header start** — EDT and CEST headers parse to the right absolute instant; a missing header gives nil; the cache version bump re-parses old entries.
- **`CommitFamily`** — `weekend briefing` is never read as `briefing`; a token must be followed by a delimiter (`researcher: …` is not research); unknown subjects give nil.
- **`commitWindow` / `claims`** — the header start wins over the filename start; an un-ended run is capped at `orphanAfter`; the window is never inverted; a `run-scout.sh` run claims briefing, weekend briefing and consolidation commits but not dreaming.
- **`CommitRunLinker`** — in-window claimed commit → run; out of window, unclaimed, or `app:` → nil; overlapping runs → latest start.
- **Forward `commits(for:)`** — returns weekend-briefing commits for a weekend run (regression for finding 2).
- **`GitService.fileHistory` / `filesChanged`** — argument shape (`--follow`, `--patch`, the pathspec after `--`); parsing of multi-revision output including a merge with an empty patch; non-zero exit throws.
- **`ItemActivity`** — source labeling; the resolving revision is the newest one that *adds* the current terminal status (not simply the newest); `.resolvedUncommitted`; active items have no outcome; repo-relative path is nil outside the repo.
- **`PerFileItemActivityModel`** — `.loaded`, `.failed` on a git error, `.unavailable` outside the repo, and other-files lazy load.
- **Views** are build-verified plus a manual smoke test, consistent with the PerFileItems and Control Center approach.

All fixture literals follow `CLAUDE.md`'s anonymization rules and are checked against the vault, excluding `~/Scout/.claude/`, before they land.

## Open follow-ups (out of v1)

- **Second attribution source:** once #119's `SessionIndexService` exists, link family-labeled commits that have no runner log to `is_scout_run` sessions by activity window.
- **Session-written metadata** (`resolved_by: <run-id>` plus a short "delivered/findings" summary in frontmatter) for exact linkage. Cross-repo, its own spec.
- **Control Center:** derive `RunType` from the header's zone, not the local hour, and key the cost-tracker lookup on the header start.
- **#103 follow-up:** move `PerFileDocumentService`'s parse off-main and publish only on change.
- **Live "working now"** once running-session infrastructure exists. Pairs with **#42 "Do now"** (a focused run is the cleanest run↔item link) and **#50** (same timeline machinery for implemented proposals).
