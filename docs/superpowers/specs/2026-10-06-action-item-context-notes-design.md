# Action items: one context note per item

**Date:** 2026-10-06
**Status:** Proposed. Review before the plan.
**Follows:** #297 (`ActionTask.details`: sub-bullet context on the card and in prompts)

## Problem

An action item is meant to be a ready-made package of context. Open it, or
press **Launch Claude**, and you have everything needed to start work. Since
2026-10-05 the package is gone.

That day the vault restructure (the KB-structure work: write protocol, kb-lint
budgets, items-only daily file) ran a one-time converter over the live daily
file. The converter:

- cut every item line to **300 characters**, at a word boundary, and appended ` …`;
- kept **at most one** non-comment sub-bullet, also cut to 300 characters;
- moved everything else, verbatim, to
  `action-items/archive/action-items-<date>-pre-restructure.md`;
- added no link from any item to the snapshot or to any other note.

The daily file went from 6,978 lines (2.5 MB) to 293. The size cut was
needed: the 2.5 MB file is what made the Action Items page freeze.
Measured on the real vault (counts only):

| | |
|---|---|
| Open items in the 2026-10-06 daily file | 91 |
| … whose full context exists only in the snapshot | 75 |
| Context only in the snapshot, those 75 items | ~206 KB |
| Lines in the daily file ending in ` …` | 93 |

The engine's own phase text already names the intended fix. In
`plugin/phases/core/action-items.md`, under "Item line format": *"Context lives
in the linked note, not under the item. If an item needs more than a line of
explanation, write or update the topic/project note and link it."* The format
is `… → [[<project, topic or source note>]]`. Nothing enforces or implements
this. Sessions sometimes link a broad project page. The converter linked
nothing. The app never reads linked notes, so **Copy → Full context** and
**Launch Claude** send whatever is left of the one line.

### Why shared notes are not enough

A project page holds many items and runs up to 15 KB. Pulling it into a
prompt is noisy, and an item with no link gets nothing. The package has to be
specific to the item.

## Goal

Every open item has one note that holds its full context. The daily file
stays one line per item. **Full context** and **Launch Claude** include the
note automatically. The 75 items cut on 2026-10-05, and every other open item
the snapshot covers, get their context back.

## Non-goals

- No change to the daily file's format, its 300-character line cap, or the
  kb-lint size limits.
- No change to #297's `details` (surviving sub-bullets still render and still
  go in prompts).
- No change to the shared parser contract. The note is not part of the daily
  file and is never parsed by the daily-file parser.
- No reading of project, topic or source notes into prompts. The context note
  links to them; following those links is a later decision.
- No app UI for editing a note. "Open note" hands off to the default editor.

## Design

### 1. The note

**Path:** `action-items/context/<TAG>.md`, where `<TAG>` is the item's
`[#TAG]` short prefix (2–8 of `[A-Z0-9]`, at least one letter). The tag
grammar has no `/`, `.` or other path characters, so the path cannot leave the
folder.

**Shape:**

```markdown
---
tag: DETX
title: Order the roadmap items before the review
created: 2026-10-06
updated: 2026-10-06
---

What this is and why it matters, current status, what is blocking it, the
evidence (quotes, timestamps, ticket and PR state), and links to the project,
topic and source notes that hold the durable knowledge.
```

- `title` is plain text, so markdown in the item line doesn't leak into it.
- The body is free markdown. It follows the write protocol's rules for any KB
  file: edit in place, no run narration, no dated "this run found…" sections.
- **Budget:** the existing default `action-items/** → 15,360 bytes` applies.
  Lines use the normal 1,500-character limit; the 500-character strict limit
  is for the daily file only.
- **Lifecycle:** the note outlives its item. When the item is done, archived
  as stale, or dropped, the note stays (small, and it keeps history
  readable). Pruning old notes is a later decision.
- **Re-tag:** when an item's tag changes, its note is renamed to the new tag
  in the same commit (`git mv`). This is a narrow exception to write-protocol
  rule 4 ("never rename or move existing files"), and the phase text says so
  explicitly.

### 2. What the engine writes (phase text)

`plugin/phases/core/write-protocol.md`, the "Every fact has one home" table:

| What you have | Where it goes | Budget |
|---|---|---|
| Something the user must do | **One action-item line** in today's daily file, **plus its context note** `action-items/context/<TAG>.md` | line ≤ 300 chars + one sub-bullet; note ≤ 15 KB |

`plugin/phases/core/action-items.md`, "Item line format":

- Replace *"Context lives in the linked note … write or update the
  topic/project note and link it"* with: *"Context lives in the item's own
  note, `action-items/context/<TAG>.md`. Create it in the same commit as the
  item. Update it, not the daily file, when the item gains context. Link
  project, topic and source notes from inside it."*
- The line format's `→ [[…]]` slot may point at the context note
  (`→ [[action-items/context/<TAG>|context]]`). It's optional. The app finds
  the note by tag, so a missing link breaks nothing. The full path keeps the
  link resolvable by kb-lint's link check and in Obsidian.
- **Carry-forward** never touches the note. A **re-tag** renames it (see §1).
- The "Write with full context and evidence" step (reconciliation step 5)
  writes its context to the note.

### 3. kb-lint: missing note (report-only)

A new check in `scout.kb.lint.lint_staged`. For each **added** open task line
in a staged daily file (`action-items/action-items-*.md`), if
`action-items/context/<TAG>.md` exists neither in the working tree nor in the
staged set, it emits a **warning** (`Finding(..., kind="missing-context-note",
blocking=False)`). It never blocks, in any mode. Lines without a tag are
ignored (the existing `[#TAG]` self-check already covers them). Only added
lines are checked, the same "added lines only" rule as the other checks, so
existing files never start failing.

### 4. One-time backfill

New command: `scoutctl action-items backfill-context --from <snapshot> [--write]`.

- **Input:** the snapshot (verbatim pre-restructure daily file), today's daily
  file, and `action-items/backlog.md`.
- **For each open item** in today's file and in the backlog that has no
  note yet:
  1. Find its block in the snapshot: the item line plus every indented line
     under it, up to the next column-0 line. Match by tag. **First occurrence
     wins**, the same rule the converter used for duplicates.
  2. If the tag isn't in the snapshot, look for the item's own
     `re-tagged from [#OLD]` marker and match `OLD`, so an item re-tagged
     after the snapshot still finds its context.
  3. Write the note: frontmatter (`title` = the item's plain title,
     `created`/`updated` = the snapshot date), a one-line provenance
     sentence ("Restored verbatim from the 2026-10-05 pre-restructure
     snapshot."), then the block **verbatim** inside the body.
- **Never overwrites** an existing note. Running it twice writes nothing
  the second time.
- **Dry run by default:** prints the counts (notes to write, items with no
  snapshot entry, notes over budget) and writes only with `--write`.
- **Over-budget blocks:** 2 of 866 snapshot items exceed 15 KB (the largest
  is 26.6 KB). The command writes them in full. It prints a `kb_budgets:`
  entry per file (`'action-items/context/<TAG>.md': null`) for the vault's
  `scout-config.yaml`, rather than cut anything.
- **Committing:** the backfill is a single commit in the vault, run with the
  user's OK. The engine doesn't run it automatically.

Expected result on the real vault: ~796 notes (the 87 daily plus 709 backlog
items the conversion counted), median 2.7 KB, 90% under 5.1 KB, ~2 MB total
in one folder.

### 5. The app

**Loader** (`Scout/ActionItems/TaskContextNote.swift`, new):

```swift
/// The item's context note, `action-items/context/<TAG>.md`, without its
/// frontmatter. nil when the task has no tag, the file is missing, or it
/// can't be read as UTF-8.
nonisolated enum TaskContextNote {
    /// `scoutDirectory` is the vault root every action-items view already
    /// holds; the note lives at `<vault>/action-items/context/<TAG>.md`.
    static func url(for tag: String, scoutDirectory: URL) -> URL?
    static func load(tag: String?, scoutDirectory: URL) -> String?
}
```

- `url` returns nil for a tag that doesn't match the tag grammar. This is a
  second guard against path tricks, independent of the parser.
- `load` reads synchronously. Notes are small, and it runs only on an
  explicit action, never during the parse or the first paint.
- The YAML frontmatter is stripped (a leading `---` … `---` block). A file
  that is only frontmatter gives nil.

**Prompts** (`ClaudeLauncher`):

- `prompt(for:format:)` gains a `contextNote: String?` parameter (default
  nil, so the existing tests and call sites still compile; every production
  call site is updated in this change, see the next bullet).
- `.fullContext` order: subject, body, details (`Context:`), then a
  **`Context note:`** section holding the note body verbatim, then
  `Prior comments:`, then `Links:`.
- The multi-task form takes a `[UUID: String]` of loaded notes and applies
  the same order per task.
- `.concise` and `.markdownChecklist` don't change. They are short by design.
- **Call sites** (all three load the note at the moment of the action):
  `TaskActionsView` (Copy menu), `LaunchClaudeMenu` (Launch Claude) and
  `ActionItemsView` (bulk copy).

**Card** (`TaskCardView`, expanded detail):

- A **"Context note"** disclosure under the details, **collapsed by default**.
  Expanding it loads the note and renders it with
  `MarkdownBodyView(blocks: MarkdownBodyBlock.blocks(from:))`, the prose plus
  code-block renderer the Wishlist and Research cards already use. Collapsed, it costs one
  file-existence check, made when the card first expands, not during the
  list's first paint.
- An **"Open note"** button opens the file in the default editor
  (`NSWorkspace.open`).
- No tag or no note: the disclosure isn't shown. The card is exactly as
  in #297.

**Search** doesn't read notes. Reading hundreds of files on every keystroke
isn't worth it. The plan may revisit this with a cached index if the
review asks for it.

### 6. Failure behaviour

| Situation | Result |
|---|---|
| Item has no `[#TAG]` | No note lookup. Card and prompts as in #297. |
| Note missing or unreadable | Same as above. No error shown. A missing note is normal for old or untagged items. |
| Note larger than expected (e.g. a 26 KB backfilled block) | Included in full. Claude Desktop launch is unchanged: the prompt is copied to the clipboard first, so nothing is lost if Desktop truncates the URL. |
| Two items share a tag (malformed file) | The note belongs to the tag. Both show it. The existing `--by-id` ambiguity rules are unchanged. |
| Tag fails the grammar check | `url(for:)` returns nil and nothing is read. |

## Testing

**Engine (pytest, anonymized fixtures: tags `DETX`/`NESTX`/`PLN`, people
Alex/Priya/Sam, `PROJ-1234`):**

- backfill: writes a note per open item found in the snapshot; verbatim body;
  frontmatter fields; first occurrence wins on a duplicate tag; the
  `re-tagged from [#OLD]` lookup; never overwrites; a second run writes
  nothing; dry run writes nothing; an over-budget block is written in full
  and reported; items in `backlog.md` are covered; an item missing from the
  snapshot is counted, not invented.
- lint: an added task line with no note gives a non-blocking
  `missing-context-note`; an existing note silences it; a note that is only
  staged silences it; unchanged lines are never checked; untagged lines are
  ignored.
- The phase-assembly tests still pass, and the phase text has no stale
  "write or update the topic/project note" instruction.

**App (Swift Testing):**

- `TaskContextNote`: path for a valid tag; nil for a bad tag (`../X`, `ab`,
  `TOOLONGTAG`); frontmatter stripped; frontmatter-only file → nil; missing
  file → nil.
- Prompts: `Context note:` sits after `Context:` and before
  `Prior comments:`; nil note → output identical to #297 (pins the
  regression); the multi-task form maps notes per task; concise and
  checklist unchanged.
- Card: smoke render with and without a note.

**Manual, on the real vault (read-only until the backfill commit):** a dry
run of the backfill reports ~796 notes and 2 over budget. After the
`--write` commit with the user's OK, **Copy → Full context** on a cut item
contains its full original context.

## Rollout

1. This PR (engine plus app, one PR on Raven-Scout/Scout): the phase text,
   lint check, backfill command, app loader, prompts and card.
2. After merge, `/scout-update` brings the phase text to the vault. From then
   on, new items get notes.
3. Backfill on the vault: a dry run first, then `--write` and one commit,
   with the user's OK.
4. The app part reaches the installed app with the next release (gated on
   Part B, as for #297). Until then, the Debug build shows it.

## Risks

- **Sessions skipping the note.** The phase text is a prose rule, and prose
  rules were ignored before the restructure. The lint warning makes skips
  visible in the report. Making it blocking is one config line if the
  warnings show the rule is being ignored.
- **Folder growth.** ~800 notes now, more over time. Each note is small and
  is read only on demand, so the app's cost doesn't grow with the folder.
  Pruning is out of scope.
- **Ownership.** Another session owns the KB-structure rollout (the converter,
  the kb-lint config, phases B6–B8). This change touches the same phase files,
  so it is coordinated through the coordinator before the plan is executed.

## Open questions for review

1. Should the 300-character line **require** the `→ [[…|context]]` link, or
   keep it optional as proposed?
2. Should done items' notes be pruned after some time, or kept forever (as
   proposed)?
3. Should search include note text (an index cached by file modification
   time), or stay out of scope (as proposed)?
