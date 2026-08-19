# Wishlist/Research: resolved-item outcomes & per-item activity (#43)

**Date:** 2026-06-28
**Issue:** [#43](https://github.com/Raven-Scout/Scout/issues/43) — "see the outcome of resolved items (link to the resolving run)"
**Follows:** #41 (editable priority/status, shipped in #61), which made the deferral of #43 to its own spec.

## Summary

Make the **work behind each Wishlist/Research item visible and traceable**. Today a resolved (done/dropped) item just moves to the collapsible "Resolved" section showing its final body — there's no way to see *which run* resolved it or *what changed*. Active items are equally opaque about progress so far.

This adds a per-item **activity timeline**, derived entirely from git, shown in a **detail pane**: the commits that touched the item's file (newest first), each labeled with the Scout run that made it (or "you" for in-app/manual changes), each expandable to its diff. For resolved items the most-recent commit is surfaced as the **outcome** ("Resolved by Dreaming · Jun 22").

## Decisions (from brainstorming)

| Decision | Choice | Rationale |
|---|---|---|
| **Mechanism** | App-side, git-derived only | Zero scout-plugin changes, single-repo, ships now. No dependency on cross-repo metadata coordination. |
| **Scope** | Resolved **and** active items | The machinery is identical; "outcome" framing for resolved, "work so far" for active. (A live "working now" indicator stays out — it needs the unsolved running-session infra.) |
| **Layout** | Dedicated detail pane | Mirrors Control Center's existing state-driven `.side`/`.full` detail panel; richest home for a timeline + diffs; no navigation-model surgery. |
| **Diff depth** | Per-commit expandable diff | Every timeline row expands in place to its own diff; the detail pane has room and it lets you inspect any step. |

## Non-goals (v1)

- No scout-plugin changes; no `resolved_by:` frontmatter metadata (a possible v2 — the heuristic link is good enough now).
- No live "working now" indicator (depends on unsolved running-session infra).
- Distinguishing hand-edits from app-writes — all non-run commits are labeled "you".
- Diff truncation / syntax highlighting — scrollable raw diff with +/− coloring only.

## Architecture & components

| Piece | Type | Responsibility |
|---|---|---|
| `GitService.commits(touching:)` | new method | `git -C <repo> log --follow --format=<RS-joined %H %h %ct %s> --shortstat -- <relPath>` → `[Commit]`. Reuses the existing `parse(gitLogOutput:prefix:)` with an empty prefix. `--follow` requires a single pathspec (satisfied — one file). |
| `CommitRunLinker` | new helper, colocated with `SessionLogService` | The **reverse** of `SessionLogService.commits(for:)`: given a `Commit` and the known `[Run]`, return the run whose commit window (the same one `commits(for:)` builds — `startedAt` minus the lead margin through `endedAt` plus the wind-down margin) contains `commit.timestamp` **and** whose `type.commitsPrefix` matches `commit.subject`. Returns `Run?` (nil → "you"/unattributed). The exact window expression is read from `commits(for:)` at implementation time and the two are kept colocated so they stay consistent. |
| `ItemActivityEntry` | new model | `{ commit: Commit, run: Run?, isResolving: Bool }`. Pure derivation; unit-testable. |
| `PerFileItemActivityModel` | new `ObservableObject` | Given a `PerFileItem` + `GitService` + the loaded `[Run]`: lazily loads commits (off-main), links runs, flags the resolving commit, lazily loads per-commit diffs on expansion. Mirrors the laziness of `SessionLogService.commits(for:)`. |
| `PerFileItemDetailView` | new view | Header (title + status/priority pills, back chevron, expand toggle — mirrors `ControlCenterView.detailHeader`) + outcome summary (resolved) + the timeline. |
| `CommitDiffView` | new view | Scrollable monospace diff with +/− line coloring, fed by `GitService.diff(from: "<sha>^", to: "<sha>")`. |
| `PerFileItemCardView` | change | Add an `onSelect` callback and a `isSelected` highlight; tapping the card body opens the detail pane. Existing Start/Done/Drop/priority controls keep working (their hit areas take precedence over the card-body tap). |
| `PerFileListView` | change | Add `detail: PerFileDetailPresentation? (.side(item) / .full(item))` state; wrap list + side panel in an HStack (list grows, side detail ~460pt fixed) and a `.full` replacement — **identical structure to `ControlCenterView.primaryColumn`**. |
| `AppState` | change | `requestOpenRun(_ id: Run.ID)`: sets a published intent and switches the sidebar selection to Control Center; `ControlCenterView` observes the intent and opens that run's detail. The one cross-tab wiring piece — it fulfills "link to the resolving run". |

## Data flow

1. Tap a card → `PerFileListView` sets `.side(item)` and highlights the row.
2. `PerFileItemDetailView` creates `PerFileItemActivityModel(item:)`, which computes `relPath` = `item.fileURL` relative to `~/Scout` (the repo root) and runs `GitService.commits(touching: relPath)`.
3. For each `Commit`, `CommitRunLinker` attaches a `Run?` from the runs already loaded in `AppState`.
4. **Resolving commit** = the most-recent commit when the item's status is terminal (done/dropped) — status is terminal, so the latest commit is the one that set it. It drives the summary line: *"Resolved by Dreaming · Jun 22"*, or *"Resolved by you · Jun 22"* when no run links.
5. Expanding a timeline row lazily fetches that commit's diff via `GitService.diff(from: "<sha>^", to: "<sha>")` and renders it in `CommitDiffView`.
6. A run-linked row offers **"Open run in Control Center"** → `AppState.requestOpenRun(run.id)` → sidebar switches to Control Center → it opens that run's detail (session log, commits, errors).

## The run-link heuristic and its limits

The commit→run link is the same **time-window + subject-prefix** heuristic the app already trusts in `SessionLogService.commits(for:)`, run in reverse. It can mis-attribute (overlapping run windows; a hand-edit landing inside a run's window) or find no match.

This is acceptable because **the diff is always shown regardless of the run link**. A missing or wrong run badge never hides the actual evidence — you still see exactly what changed. The link is a convenience for jumping to the run's log, not the source of truth for "what happened".

## Edge & error handling

- **Uncommitted new item** (file only in the working tree) → empty timeline, *"No activity yet"* empty state.
- **Resolved but no matching run** → *"Resolved · Jun 22"* with no run badge ("you").
- **Large diff** → scrollable container; no truncation in v1 (revisit only if it beachballs).
- **`git log`/`git diff` failure or non-repo** → surface an inline error row in the pane; the list stays usable. Errors are **not** swallowed (the #47 lesson).
- **Renames** → `--follow` preserves history across them.
- All git work is async/off-main and lazy per opened item — cards you never open cost nothing.

## Testing

Deterministic and injectable via the existing `ProcessRunner` seam:

- **`GitService.commits(touching:)`** — assert the invocation includes `--follow -- <relPath>`; parse a fixture `git log` output into the expected `[Commit]` (the underlying parser is already covered, so this focuses on path-scoping + the `--follow` arg).
- **`CommitRunLinker`** — in-window + matching-prefix commit → the right run; out-of-window, wrong-prefix, and no-run (manual) → nil.
- **`ItemActivityEntry` derivation** — resolving-commit identification for terminal vs active status; "you" vs run labeling.
- **UI views** (`PerFileItemDetailView`, `CommitDiffView`) are not unit-tested, consistent with the existing PerFileItems/Control Center approach (services + models carry the tests).

## Open follow-ups (out of v1)

- **Session-written metadata** (`resolved_by: <run-id>` + a short "delivered/findings" summary in frontmatter) for reliable linkage — would be preferred over the heuristic when present. Cross-repo; its own spec.
- **Live "working now"** indicator once running-session infra exists.
- Pairs with **#42 "Do now"** (a focused run is the cleanest run↔item link) and **#50** (rich detail for implemented proposals — same timeline/diff machinery).
