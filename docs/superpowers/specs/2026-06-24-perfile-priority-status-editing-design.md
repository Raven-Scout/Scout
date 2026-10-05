# Wishlist/Research — editable priority & status on cards

**Issue:** #41 (user-changeable priority) + ROADMAP nice-to-haves "mark in-progress" and "reopen resolved items".
**Date:** 2026-06-24
**Status:** design approved; ready for implementation plan.

## Summary

Make the Wishlist and Research cards' **priority** and **status** directly editable, instead of being write-once at Add time (or only by Scout sessions). All of it lives in the shared `Scout/PerFileItems/` core, so both tabs get it at once.

Three user-facing capabilities, one underlying mechanism (rewrite a single frontmatter field, then a path-scoped git commit, guarded by the #48 race guard):

1. **Change priority** — tap the priority pill → menu of the tab's priorities, write `priority:`.
2. **Start** — flip `status: open → in-progress`.
3. **Reopen** — flip a resolved item (`done`/`dropped`) back to `open`. (Today the resolved section's resolve callback is a literal no-op, so reopening is impossible.)

`Done`/`Drop` (the existing resolve actions) are unchanged in behavior; they're refactored to share the new status-write path.

### Why `urgent` matters (Research)
Research's `priority: urgent` is load-bearing — the research session's START-IMMEDIATELY preemption keys on it (`phases/research/research-targets.md` in scout-plugin). Letting the user set `urgent` from the app is the point: it promotes a topic to run-first.

## Decisions (from brainstorm)

- **Affordance:** tap the priority pill → SwiftUI `Menu` (current value checked). Issue's first suggestion; most discoverable.
- **Priority vocab per tab:** from existing `PerFileTabConfig.priorities` — Wishlist `high/medium/low`, Research `urgent/high/medium/low`.
- **Priority editing is active-only** (open / in-progress). Resolved items show a read-only pill + a **Reopen** action (reopen first, then re-prioritize).
- **Field-missing behavior:** the generalized rewrite **throws** if the field (or the frontmatter block) is absent — preserving today's `rewriteFrontmatterStatus` behavior. Both `status:` and `priority:` are always written by `renderItemFile` and the plugin templates, so a missing field is a real anomaly worth surfacing inline, not silently inserting.

## State machine

| Status | Priority pill | Actions |
|--------|--------------|---------|
| `open` | editable menu | **Start** · Done · Drop |
| `in-progress` | editable menu | Done · Drop |
| `done` / `dropped` | read-only | **Reopen** |
| `unknown(...)` | read-only | Reopen (treated as resolved, matching `isActive == false`) |

## Architecture

### Writer — `PerFileItemWriter`
- Generalize `rewriteFrontmatterStatus(text:newStatusValue:file:)` →
  **`rewriteFrontmatterField(text:key:value:file:)`** (same scan/replace logic, parameterized key; preserves leading indentation; throws `frontmatterNotFound` / `fieldNotFound(field:file:)`).
- **`setPriority(_ priority: ItemPriority, fileURL:, label:)`** — writes `priority: <rawValue>`; commit `app: set <label> priority to <value>`.
- **`setStatus(_ status: ItemStatus, fileURL:, label:)`** — writes `status: <frontmatterValue>`; commit message by target: `app: start <label>` (in-progress), `app: reopen <label>` (open), `app: mark <label> done`, `app: mark <label> dropped`.
- `resolve(_:fileURL:label:)` is refactored to delegate to `setStatus`, so there is exactly one write path (`GuardedFileWrite.apply` + `commitPaths`). Public signature kept for existing callers/tests.
- Error enum: `statusFieldNotFound(file:)` → `fieldNotFound(field:file:)` (generic).

All writes reuse the existing serialized `tail` queue + off-actor `performResolve`-style helper + `GuardedFileWrite` race guard + single-file-scoped `commitPaths`.

### Models
No new types. `ItemPriority` already exposes `rawValue` (frontmatter value), `displayName`, and `CaseIterable`. `ItemStatus` already has `.open`, `.inProgress`, `.done`, `.dropped` with `frontmatterValue`. `PerFileTabConfig.priorities` already holds the per-tab vocab.

### Card UI — `PerFileItemCardView` + `ItemPriorityPill`
- `ItemPriorityPill` gains optional `options: [ItemPriority]` + `onSelect: (ItemPriority) -> Void`. When `options` is non-empty it renders as a `Menu` (current checked); otherwise the existing static capsule (unchanged for resolved/read-only).
- `PerFileItemCardView` takes new callbacks `onChangePriority` and `onChangeStatus` alongside `onResolve`. Its `inFlight: ItemResolution?` / `errorText` state generalizes to a single "busy" indicator that disables all controls and shows the inline error on failure (same UX as today).
- **Re-selecting the current value is a no-op:** `GuardedFileWrite.apply` short-circuits when the transform returns unchanged text (`updated == text` → returns `false`), so picking the priority/status an item already has performs no write and no commit. The card treats `false` as success (no error).
- Action area renders by status (table above): active → Start (when `open`) · Done · Drop; resolved → Reopen.

### Data flow — `PerFileListView`
- New callbacks wired to the writer, mirroring `resolve(_:_:)`:
  - `changePriority(item, ItemPriority)` → `writer.setPriority(...)` → `docService.reload()`
  - `changeStatus(item, ItemStatus)` → `writer.setStatus(...)` → `docService.reload()`
- The resolved section stops passing a no-op; its cards get the Reopen path.
- `reload()` re-parses + re-sorts (awaiting sorted by priority), so a priority bump or status change re-positions the card automatically — no extra view logic.

## Testing (TDD)

Writer (mirrors existing `PerFileItemWriterTests` / `commitsScopedTo…` patterns, `GitServiceProtocol` stub):
- `rewriteFrontmatterField` replaces `priority` value, preserves indentation, leaves other fields intact.
- `setStatus` round-trips `open → in-progress → done`; `setStatus(.open)` reopens a `dropped` file.
- `setPriority` writes the new `priority:` and nothing else.
- field-missing (`priority:`/`status:` absent within a present block) throws `fieldNotFound`; no `---` block throws `frontmatterNotFound`.
- commit is path-scoped to the single item file (no `-A`), message matches the action.
- the `GuardedFileWrite` race-guard path is already covered by existing tests and inherited unchanged.

Model:
- the pill's options derive from `config.priorities` (Research includes `urgent`, Wishlist does not).

UI layout: not unit-tested (consistent with the rest of the suite); verified by build + the existing card render.

## Out of scope (deferred to its own spec)

**Per-item work/activity view (#43)** — "link directly to the work Scout did and is doing on each item." The work history lives in the item file's **git log** (commits like `research [01:14]: …`) and the body's `## Progress` sections, not in frontmatter. v1 direction (agreed): a derived per-item commit list (date · run-type · subject) via a new `GitService.commits(touchingPath:)` (`git log --follow -- <file>`), later linking each commit to its Control Center Run via the existing `SessionLogService` commit↔run mapping; a live "working now" indicator depends on the still-unsolved running-session infra. This gets its own brainstorm → spec.
