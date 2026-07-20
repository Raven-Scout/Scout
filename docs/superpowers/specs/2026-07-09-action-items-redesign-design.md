# Action Items redesign — clean title / description / relations, bottom-up

**Date:** 2026-07-09 (reworked 2026-07-12 after review)
**Status:** Reworked per Adam's review — source-side generation, refs in a
separate block, **no app-side stripping**. Plan updated to match.
**Origin:** Slack thread (Adam + Jordan, 2026-07-07). Adam: *"There should be a
short title of action item in that bold font (without any status, IDs, hashtags,
etc) and then description with smaller font below. Those IDs should be just used
for rendering links/relations etc and not present at all… no emojis in that
title… and there is some unrendered MD with underscores."* PR review
(2026-07-10): *"instead of stripping emojis and IDs generate the titles without
them and adjust prompt to make them very human readable and natural and pass IDs
separately in different block."* ← this revision adopts that.
**Repos touched:** `scout-plugin` (generation prompt — now the load-bearing
track) and `scout-app` (rendering + Refs-block recognition + markdown fix +
tests).

> **Rebased on #78 / v0.10.0 (2026-07-20).** Re-anchored against merged PR #78
> ("ring+dot marker + SF Symbol sweep"), which deleted `DS.kindGlyph`, added a
> `KindMarker` view + `DS.kindSymbol(_:)`, and replaced the section-header
> color-dots with `KindMarker`. Consequences for this spec, all verified
> against `main` @ `4106a38`:
> - **Priority on a task card is the left color stripe** (`DS.priorityColor`),
>   unchanged by #78. Section *headers* now render their kind via `KindMarker`
>   (not raw 🔴🟡🟢 dots) — but the daily-file **markdown** section headers
>   still carry the 🔴/🟡/🟢 emoji the engine writes, so the engine-prompt rule
>   "priority = section placement" is unaffected. Read "🔴/🟡/🟢 section headers"
>   below as "the KindMarker-rendered section header + the card's left stripe".
> - **No collision with #78's `KindMarker`.** The relation `TaskChip` (a model
>   struct with its own `Glyph` enum) is unrelated to the `KindMarker` view;
>   the two new chip kinds add `TaskChip.Glyph` cases only.
> - **Task 5 anchors hold.** `TaskCardView.header` (89-116) and `nestedRow`
>   (341-367) and the referenced `quickActions`/`trailingStatus`/`DS.serif`/
>   `DS.Ink` symbols all still exist on v0.10.0; the nested row already renders
>   `subject`+`body` verbatim. PR #79 ("copy + menu-bar quick control") is still
>   **open/unmerged**, and `quickActions` already exists on `main` independent
>   of it, so the plan does not depend on #79.

## Problem

An action item is one markdown checkbox line — a text blob, canonical in the
vault and hand-editable in Obsidian. The LLM authors the whole line, and it
crams the **bold segment** (the de-facto title) with everything: priority/status
emoji, the `[#TAG]` continuity id, Linear/GitHub ids, cross-reference hashtags,
dates, quotes, and internal ` — ` clauses. The app then renders that raw blob as
the title. The result, structurally (anonymized):

```
- [ ] [#REPLYX] 🟡 **Reply to Alex — purchase question still open (Thu Jul 3 4:55 PM: "1/8 to 1/2")** _(carries 7/3→7/6; unanswered per sweep)_ — She also asked about the trip. [[people/alex]]
```

Four independent failures compound here:

1. **The "title" is a run-on sentence.** The bold span is prose with embedded
   dates, quotes, and ` — ` clauses, so there is no *short* title to read.
2. **Priority + status live inside the bold** (`🟡`, `🔥 🆕`, "FIX MERGED +
   DEPLOYED") — the "no emoji / no status in title" complaint.
3. **Machine ids are inline literal text** (`[#REPLYX]`, `PROJ-3026`,
   `example-org/repo#7056`, cross-ref `#XREF`) — "usability is ZERO."
4. **`_(…)_` italic renders as raw underscores** — an app markdown bug.

## Current architecture (as-built)

| Layer | Reality |
|---|---|
| Storage | One markdown line per item (+ indented sub-lines: comments, scout markers). No JSON. Markdown is canonical + Obsidian-editable. |
| Authoring | LLM writes the line per `scout-plugin/phases/core/action-items.md`. |
| Parse contract | 4 fields — `short_prefix`, `subject` (markdown + emoji + ids retained), `plain_subject`, `body` (after first top-level ` — `). Checksum-guarded `parser-corpus.json` across 3 repos. Sub-lines are recognized app-side (comment shapes, scout markers) and attach to the preceding task. |
| App model | `ActionTask` (`Scout/ActionItems/Models/ActionTask.swift`): `subject` = raw markdown head; **no title field**; `body`, `deepLinks`, `shortPrefix`, `comments`, `snoozedUntil`, `carriedInFrom`, `indentLevel`, `snoozedFromKind`. |
| App render | `TaskCardView` header renders `subject` (serif bold, `lineLimit 2`); `body` hidden behind expand. Priority emoji left inline **and** shown as left color stripe (redundant). Chips (`TaskChip.chips(for:)`) already turn deep-links into clickable relations. |
| Markdown | One renderer, `InlineMarkdownText`. Line 59 calls `AttributedString(markdown:)` with default `.full` syntax and silently falls back to raw text on throw. |

## Decision

**Approach: generate clean at the source; pass refs in a separate block; the
app renders verbatim.** The engine prompt is the single place titles get clean —
short natural imperative, no ids/emoji/status. All machine refs move out of the
prose into a dedicated, structured **`Refs:` sub-bullet** under the task line.
The app learns to *parse* that block (feeding the existing chip row) — it never
strips or rewrites display text. There is **no app-side display cleaner**; the
previous revision's `TaskDisplayText` defensive-cleaning component is dropped.

Why not clean in the app (the previous revision): stripping is a band-aid — a
second system fighting the first, with regexes chasing whatever shape the model
emitted. And the corpus argument for it doesn't hold: open items are carried
forward and re-rendered by the engine daily, so the source fix converges the
live view on its own (see Migration). Reviewer was right; this revision fixes
the producer and keeps the consumer dumb.

Decisions:

- **Title:** short natural imperative phrase, generated clean. Never contains
  ids, hashtags, status words, dates, quotes, emoji, or internal ` — ` clauses.
- **Description:** the body after ` — ` is human prose. `[[entity|Name]]`
  wikilinks are allowed inline where a *name* reads naturally in a sentence.
  Bare machine ids are not.
- **Refs block (the "separate block"):** one optional indented sub-bullet per
  task holding every machine ref — Linear ids, GitHub refs, Slack permalinks,
  cross-ref hashtags, and entity wikilinks that are pure references (not part
  of a sentence):
  ```
  - [ ] [#REPLYX] **Reply to Alex about her purchase question** — She said 1/8–1/2; still open per the sweep. Loop in [[people/priya|Priya]] on onboarding.
    - Refs: [[people/alex]] · [[PROJ-3026]] · example-org/repo#7056 · #XREF
  ```
  The app renders this block as the relation chip row, never as prose.
- **Priority:** conveyed by section placement (the `KindMarker`-rendered
  section header, post-#78) and the app's left color stripe only. No inline
  priority emoji on task lines.
- **`[#TAG]` continuity key:** stays at line-start (machine identity, parser
  already extracts it to `short_prefix`); hidden from the default view.
- **Carry-forward normalization:** when the engine carries an open item into a
  new daily file, it rewrites the item into this shape (preserving the `[#TAG]`
  verbatim and all facts). This is what converges the existing corpus — see
  Migration.

## Architecture — two tracks (engine now load-bearing)

```
ENGINE (scout-plugin, prompt)               APP (scout-app, rendering)
────────────────────────────────           ─────────────────────────────────
phases/core/action-items.md                 InlineMarkdownText: .inlineOnly…
  title = short natural imperative,           + graceful throw fallback  → fixes _italic_
  no id/status/emoji/date/dash             ActionItemsParser: recognize the
  detail → after ' — ' / sub-bullets          `- Refs:` sub-bullet (same
  machine refs → `- Refs:` sub-bullet         mechanism as comment sub-lines);
  priority = section placement only           route to chips, exclude from prose
  carry-forward = rewrite to new shape     TaskChip: + entity (wikilink) and
        (main-line 4-field contract          cross-ref chips sourced from Refs
         unchanged; Refs recognition       TaskCardView: title + 2-line desc
         is additive sub-line handling)      + chip row — all VERBATIM, no
                                             cleaning; body/comments/actions
                                             behind expand; priority = stripe
```

The engine track is the fix; the app track renders what the engine now
guarantees (plus the markdown bug fix, which is independent). Old lines render
as-authored until carry-forward normalizes them.

## Components

### Engine — `scout-plugin/phases/core/action-items.md` (prompt)

Rewrite the task-line authoring rules and worked example. New rules:

1. **The bold title is a short natural imperative phrase.** It must NOT
   contain: the `[#TAG]`, any Linear/GitHub id, any hashtag, status words
   ("MERGED", "DEPLOYED"), dates, quotes, emoji, or an internal ` — `
   separator.
2. **All narrative detail goes after the ` — ` separator (the body) or into
   `- Source:` / `- Context:` sub-bullets.** Entity wikilinks may appear inline
   in the body only as names in a sentence.
3. **All machine refs go in a single `- Refs:` sub-bullet** (` · `-separated):
   Linear ids, GitHub refs, Slack permalinks, cross-ref hashtags, and
   pure-reference entity wikilinks. No bare ids anywhere in title or body.
4. **Priority is conveyed only by section placement.** No inline priority
   emoji on task lines.
5. The `[#TAG]` stays at line-start (after the checkbox, before the bold) —
   unchanged.
6. **Carry-forward rewrites.** When carrying an open item forward, re-author it
   in this shape: same `[#TAG]`, same facts, clean title, refs consolidated
   into the Refs block. Do not preserve legacy formatting for its own sake.
7. Update the self-check guidance + the worked example to model the new shape
   (the anonymized example above).

No change to `scout.ids`, `parser.py`, `render.py`, `backfill.py`, `writer.py`,
or the main-line 4-field contract in `parser-corpus.json`.

### App — `scout-app`

**1. Markdown fix — `Scout/ActionItems/Views/InlineMarkdownText.swift` (line ~59).**
Parse with `AttributedString.MarkdownParsingOptions(interpretedSyntax:
.inlineOnlyPreservingWhitespace)` (matching the existing correct call site in
`ControlCenter/Detail/SummaryTab.swift:140`). Replace the silent
`?? AttributedString(rewritten)` raw-markdown fallback with a fallback that
normalizes the common inline tokens so a parser throw never dumps literal
`_`/`**` to screen. Independent of everything else; fixes Adam's underscore
complaint.

**2. Refs-block recognition — `Scout/ActionItems/ActionItemsParser.swift`.**
Recognize an indented `- Refs: …` sub-bullet under a task line, the same way
comment sub-lines are recognized today (this is *additive sub-line handling*,
like the v0.4 comment shape — not a change to the main-line 4-field split).
Parse its ` · `-separated tokens through the existing `detectDeepLinks`
machinery plus two new token shapes: `[[entity|Name]]` / `[[entity]]` wikilink
refs and `#XREF` cross-ref hashtags. Attach the result to `ActionTask`
(e.g. `refs: [TaskDeepLink]` merged into `deepLinks`, plus new cases). The
Refs line never appears in `body`/prose rendering.

**3. Chips — `Scout/ActionItems/Views/TaskChip.swift`.**
Two new chip kinds sourced from the Refs block: **entity** (opens the KB note
in the KB tab) and **cross-ref** (scrolls to the referenced item if present in
the current file). Linear / GitHub / Slack chips work as today. Coverage rule:
every token in the Refs block yields exactly one chip; unrecognized tokens
render as a plain-text chip rather than vanish.

**4. `TaskCardView` layout — `Scout/ActionItems/Views/TaskCardView.swift`.**
Collapsed card:
- **title** = `subject`, rendered verbatim via `InlineMarkdownText`, serif
  ~15.5 medium, `lineLimit(2)`, strikethrough when done;
- **description** = `body`, rendered verbatim, smaller (~13), muted,
  `lineLimit(2)` collapsed / unlimited expanded;
- the **chip row** (deepLinks + Refs block + carry);
- left priority stripe (kept); trailing done/snooze pill (kept); expand
  chevron. The `#TAG` mono chip is removed from the default view.
Expanded adds the existing `TaskBodyView`, `CommentListView`, `TaskLinksView`,
`TaskActionsView`, `CommentComposerView`. **No display cleaning anywhere** —
legacy messy lines render as they are until the engine normalizes them.

**5. Other surfaces.**
`BoardCardView`, `SectionView` (focus / completedList), and `DigestView`
render `subject` verbatim as today — they inherit clean titles from the source
fix with no app change beyond what they already do.

## Error handling

- Markdown parse throw → the new fallback renders normalized plain text, never
  raw `_`/`**`.
- A malformed Refs line (bad token, missing ` · `) degrades to plain-text
  chips; nothing is dropped silently and nothing leaks into prose.
- A machine ref that (incorrectly) still appears in body prose renders as
  text — visible, ugly, and self-correcting on the next carry-forward. The app
  does not attempt to catch it.

## Testing

- **Parser tests (extend, app-side).** `Refs:` sub-bullet: attaches to the
  preceding task; tokens parse to the right chip kinds; malformed tokens
  degrade to plain chips; the line is excluded from `body`. Sub-line handling
  is app-only (like comment recognition), so `parser-corpus.json` and its
  checksum are untouched.
- **`InlineMarkdownText` test (new).** `_italic_` and `_(parenthetical)_`
  render as attributed italic runs, not literal underscores; the throw path
  renders normalized text, not raw markdown.
- **`TaskChipTests` (extend).** Entity + cross-ref chips from a Refs block;
  every Refs token yields exactly one chip.
- **Main-line contract tests unchanged and green** — `ParserContractTests`
  4-field cases are not touched.
- **Build + full `ScoutTests` target green.** Run the whole `ScoutTests`
  target — `-only-testing:ScoutTests/ActionItems` runs zero tests (known
  false-green).
- **Engine.** Regenerate a sample action-items file with the rewritten prompt
  and eyeball: titles short/natural, refs in the block, no inline emoji; run a
  simulated carry-forward over a legacy messy file and eyeball the
  normalization.

## Migration & compatibility

- **No data migration.** Open items are carried forward daily and the prompt
  now rewrites them into the new shape on carry — the live view converges
  within a carry cycle of the engine change shipping. Archived files are
  historical records and stay byte-for-byte as they are.
- **Interim window:** until an item's first post-change carry, it renders
  as-authored (messy). Accepted — it's days, not months, and honest.
- **Obsidian / hand-editability preserved** — everything stays in the `.md`;
  the Refs sub-bullet is ordinary markdown.
- **scoutctl write path unchanged** — `matchableSubject` and the `--by-id` /
  `--subject` protocol are untouched.

## Out of scope

- Preamble / Today's Focus / Trading / section-header rendering.
- A Board-mode layout overhaul.
- Any change to the main-line 4-field parse contract or `scout.ids`.
- iOS render (the source fix benefits it for free).
- Drag-to-restatus, board changes.

## Sequencing (for the plan)

1. **Engine M1 — prompt rewrite** (title rules + Refs block + carry
   normalization) + regenerate/eyeball sample. This is the fix; ship first.
2. **App M2 — markdown fix** (`InlineMarkdownText`) + test. Independent,
   shippable any time.
3. **App M3 — Refs-block recognition** (parser sub-line + chip kinds) + tests.
4. **App M4 — `TaskCardView` layout** (title + description + chips, verbatim)
   + other-surface check.

Engine M1 ships as one scout-plugin PR; app M2–M4 as one scout-app PR. Per the
review-first flow, this reworked spec + plan land on the PR for re-review
before code.
