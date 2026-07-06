# Design — "Comment for Scout" (Knowledge Base phase)

- **Date:** 2026-07-06
- **Status:** Approved (design); ready for implementation plan
- **Feature family:** Post-v0.9.0 Knowledge Base follow-ups (feature 1 of 3)
- **Related:**
  - Ships on top of the Knowledge Base tab (PR Raven-Scout/Scout#69, released in v0.9.0)
  - Action Items counterpart is **out of scope here** and tracked by **Raven-Scout/scout-plugin#186** (needs a `scoutctl` marker command)
  - Sibling specs to follow: graph navigability (feature 2), network-analysis stats (feature 3)

## Summary

Let the user select a block in a Knowledge Base note and attach a comment **for Scout to act on during its next dreaming session**. The comment is persisted as an inline `//==<< … >>==//` marker on its own line beneath that block, using the KB writer's existing guarded + git-committed path. No new plugin-side reader is required — the dreaming session already consumes these markers.

## Goals

- One-affordance path from "I'm reading a note" → "leave Scout a note about *this* block."
- Reuse the mechanisms the KB tab and the Scout plugin already have: the `//==<<` contract, the block-addressable Read view, the guarded writer.
- Keep the note's prose and the plugin's structured tokens **byte-identical** except for the inserted marker line (the surgical-splice invariant the KB editor is built around).
- Let the user **see** their pending markers in-app and **retract** one before dreaming processes it.

## Non-goals (this phase)

- **Action Items** comments-for-Scout — blocked on `scoutctl` gaining a marker command (scout-plugin#186); fast-follow.
- **Non-file surfaces** (Control Center, Schedules, the graph) — no markdown to anchor into.
- **Arbitrary character-span** selection (Docs-style) — we attach at block granularity, matching the Read view's existing model.
- A **persistent annotation layer / sidecar** — markers are transient by contract (dreaming strips them when done); we are not building a durable comment store.

## Background: the mechanism we build on

The Scout dreaming session already has a per-location feedback channel (verified in `~/scout-plugin/phases/modes/kb-deep-work.md` and `~/Scout/DREAMING.md` Step 2-pre):

- The user places `//==<< comment >>==//` at the exact spot in any `~/Scout/**/*.md` file.
- Dreaming scans for them with `rg -F '//==<<'`, acts on each, and **removes the marker when resolved** (removal = the "processed" signal; unresolved markers are left in place).
- More than 5 markers at once → dreaming switches to triage and inventories them into a dated `knowledge-base/comment-triage-YYYY-MM-DD.md` ledger.

KB notes live under `~/Scout/knowledge-base/`, so they are already in the scan set. This feature is therefore a **UI + writer affordance over an existing contract**, not a new pipeline.

## User experience

1. In **Read mode** (`KBEditableView`), hovering a block reveals a **"💬 Comment for Scout"** control alongside the existing double-click-to-edit affordance (mirrors the hover pencil/trash in Action Items' `CommentListView.swift:100-127`).
2. Clicking it opens a small composer beneath the block (same collapsed → `TextEditor` → `Cancel` / `Send` (⌘↵) pattern as `CommentComposerView.swift:33-58`).
3. On send, a new line `//==<< <text> >>==//` is written immediately after the block's source range and git-committed.
4. The marker then renders as a distinct **"for Scout · pending"** chip (not raw text), with a **× to retract** (deletes the marker line via the writer).
5. When the next dreaming run resolves and strips the marker, the chip disappears on the next FSEvent reparse — an honest signal that Scout handled it.

## Architecture & components

All changes are in `scout-app`; no plugin dependency for this phase.

### 1. `ScoutMarker` (new, `Scout/KnowledgeBase/Models/ScoutMarker.swift`)
Single source of truth for the syntax. Small, pure, `nonisolated`, unit-tested:
- `format(_ text: String) -> String` → `"//==<< \(trimmed) >>==//"` (collapses internal newlines to spaces so the marker is a single line; trims; rejects empty).
- `isMarkerLine(_ line: String) -> Bool` and `body(of line: String) -> String?` → detect a standalone marker line and extract its comment text (for rendering the chip and for retract).
- Regex mirrors the plugin/Action Items convention already parsed at `ActionItemsParser.swift:249`.

### 2. `KBDocSegment` insertion + classification (`Scout/KnowledgeBase/Models/KBDocSegment.swift`)
- Add `insertLine(after segment:, text:) -> String` that splices a new line after `segment`'s existing source `lineRange` (the same range machinery `replaceLines` uses), preserving all other bytes. Handles end-of-file (append) and ensures the marker sits on its own line.
- Add a segment **kind** for a standalone marker line (e.g. `.scoutComment(text:)`) so the parser classifies `//==<<` lines distinctly instead of as prose. `KBEditableView` renders that kind as the chip rather than as body text.

### 3. `KBEditableView` affordance (`Scout/KnowledgeBase/Views/KBEditableView.swift`)
- Add the hover "💬 Comment for Scout" control to `segmentView` (`:51-66`), next to the existing `startEdit` path (`:97-105`).
- On submit: call `KBDocSegment.insertLine(after:text: ScoutMarker.format(...))`, then persist via the writer (below). Reuse the memoized `SegmentCache` (`:19-29`) reparse after write.
- Render `.scoutComment` segments as the chip with the × retract control (retract = `KBDocSegment` line-delete of that segment + writer save).
- A small **KB-local composer** view following `CommentComposerView`'s UX (kept KB-local to avoid coupling to the Action Items op path; a shared composer can be extracted later if a second consumer appears).

### 4. Persistence — reuse `KnowledgeBaseFileWriter.save` (`Scout/KnowledgeBase/KnowledgeBaseFileWriter.swift:64-80`)
No new writer op. Insertion and retraction are both whole-file `save(fileURL:contents:baselineContents:label:)` calls:
- Baseline-content conflict guard (`performSave` `:144-169`) already surfaces a concurrent plugin/Obsidian write instead of clobbering it.
- Scoped git commit already happens on success; a commit failure surfaces as `.commitFailed` ("written but uncommitted") per the v0.9.0 fix — reuse as-is.
- Confined to the KB by `ensureInsideKB` (`:133-140`).

## Data flow

```
hover block → "💬 Comment for Scout" → composer.send(text)
  → newContents = KBDocSegment.insertLine(after: block, text: ScoutMarker.format(text))
  → KnowledgeBaseFileWriter.save(fileURL, contents: newContents, baselineContents: current, label: "kb: comment for Scout")
      → guarded write + scoped git commit
  → FSEvents (250ms debounce) → service.reparse() → SegmentCache refresh
  → block now shows a "for Scout · pending" chip (× to retract)

(next dreaming run) → rg finds marker → acts → strips marker → commits
  → FSEvents → reparse → chip gone
```

## Edge cases & error handling

- **Empty / whitespace-only comment** → composer send disabled; `ScoutMarker.format` also guards.
- **Multiline input** → collapsed to a single-line marker (the contract is line-oriented; `rg -F '//==<<'` and the parsers are line-based).
- **Block inside a code fence / table** → insert the marker on the line *after* the fenced block or table (never inside), so it isn't captured as code and the fence stays balanced. `KBDocSegment` already tracks fence/table boundaries; insertion uses the segment's outer `lineRange`.
- **Multiple markers on one block** → allowed; each is its own `.scoutComment` segment/line.
- **Concurrent external edit** → baseline guard returns `.conflict`; surface the existing "changed on disk" affordance rather than overwriting.
- **Marker byte-integrity** → only a whole new line is added; no existing line is rewritten (distinct from `replaceLines`/`replaceCell`), so structured tokens are untouched.

## Testing

Pure-logic tests in `ScoutTests/KnowledgeBase/` mirroring the existing `KnowledgeBaseTests.swift` style (no real identifiers per repo `CLAUDE.md`):
- `ScoutMarker`: format (trim, empty-reject, newline-collapse); detect + body-extract; round-trip; escaped/edge content.
- `KBDocSegment.insertLine(after:)`: after a paragraph, after a list item, after a fenced code block (marker lands outside the fence), after a table, at EOF, and into a frontmatter-led document; asserts all other bytes are unchanged.
- Segment classification: a standalone `//==<<` line parses as `.scoutComment`, not prose; a `//==<<` occurring mid-prose is left to the existing rules (not our insertion path).

## Out of scope / follow-ups

- **Action Items "Comment for Scout"** — needs `scoutctl action-items add-marker` (or `add_comment --scout`); tracked in **scout-plugin#186**. Once shipped, `ActionItemsWriter` gains a `WriteOp` that calls it; the app UI mirrors this block-level affordance on task cards.
- **Feature 2 — graph navigability** and **Feature 3 — network-analysis stats**: separate specs. (Exploration already mapped the hook points: `KnowledgeBaseService.fullGraph()`/`undirectedEdges()` for filtering/stats, `KBGraphCanvas` knobs for the map.)
