# Action Items redesign — clean title / description / relations, bottom-up

**Date:** 2026-07-09
**Status:** Approved design (brainstorming complete; ready for implementation plan)
**Origin:** Slack thread (Adam + Jordan, 2026-07-07). Adam: *"There should be a
short title of action item in that bold font (without any status, IDs, hashtags,
etc) and then description with smaller font below. Those IDs should be just used
for rendering links/relations etc and not present at all… no emojis in that
title… and there is some unrendered MD with underscores."*
**Repos touched:** `scout-plugin` (generation prompt only) and `scout-app`
(rendering + display-cleaning + markdown fix + tests).

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

The title/description split *mechanism* already exists: the parser splits
`subject` (bold lead) from `body` on the first top-level ` — `. The failures are
that the model stuffs the bold, ids/emoji/status live inline instead of as
metadata/chips, the app renders the raw blob and hides the body, and the markdown
renderer is misconfigured.

## Current architecture (as-built)

| Layer | Reality |
|---|---|
| Storage | One markdown line per item. No JSON, no separate title/description fields. Markdown is canonical + Obsidian-editable. |
| Authoring | LLM writes the line per `scout-plugin/phases/core/action-items.md`. |
| Parse contract | 4 fields — `short_prefix`, `subject` (markdown + emoji + ids retained), `plain_subject`, `body` (after first top-level ` — `). Checksum-guarded `parser-corpus.json` across 3 repos. |
| App model | `ActionTask` (`Scout/ActionItems/Models/ActionTask.swift`): `subject` = raw markdown head; **no title field**; `body`, `deepLinks`, `shortPrefix`, `comments`, `snoozedUntil`, `carriedInFrom`, `indentLevel`, `snoozedFromKind`. |
| App render | `TaskCardView` header renders `subject` (serif bold, `lineLimit 2`); `body` hidden behind expand. Priority emoji left inline **and** shown as left color stripe (redundant). Chips (`TaskChip.chips(for:)`) already turn deep-links into clickable relations. |
| Markdown | One renderer, `InlineMarkdownText`. Line 59 calls `AttributedString(markdown:)` with default `.full` syntax and silently falls back to raw text on throw. |
| Existing cleaners | `ActionTask.matchableSubject` / `cleanForScoutctlMatch` already strip emoji + status + strikethrough — but only to build the scoutctl write needle, never for display. |

## Decision

**Approach: convention + render (no schema/corpus/checksum change).** Fix
authoring at the source (prompt) so the bold title is short and clean and all
detail moves to the body/sub-bullets, then redesign the app render to show a
clean title + smaller description + relation chips and fix the markdown renderer.
Both the producer and the consumer change; the markdown format, the 4-field
parse contract, and `parser-corpus.json` are untouched.

Approved decisions (from brainstorming):

- **Redesign depth:** convention + render. Not a structured data contract (no new
  `title`/`description`/`relations` fields, no parser/corpus/checksum churn); not
  app-only (prose status can't be reliably tokenized out of a run-on title).
- **Relations:** *ids out, names stay.* The app strips machine keys from the
  displayed prose — `[#TAG]`, Linear ids, GitHub refs, Slack links, cross-ref
  hashtags — and surfaces them as chips; it **keeps `[[entity|Name]]` wikilinks
  inline as clickable names** (a name reads naturally in a sentence). Ids remain
  in the markdown source for Obsidian + link detection.
- **Collapsed card:** clean bold title + a 2-line description teaser (smaller,
  muted) + the relation chip row. Expand reveals the full body + comments +
  links + actions + composer. Urgent still auto-expands.
- **Priority:** conveyed by the left color stripe only; inline priority emoji is
  dropped from the display (and from new task lines).
- **`[#TAG]` continuity key:** hidden from the default view (machine key, zero
  human value). Retained in the model + as an accessibility/debug affordance.

## Architecture — two tracks

```
ENGINE (scout-plugin, prompt only)         APP (scout-app, rendering)
────────────────────────────────          ─────────────────────────────────
phases/core/action-items.md                InlineMarkdownText: .inlineOnly…
  bold title = short imperative,             + graceful throw fallback  → fixes _italic_
  no id/status/emoji/date/dash             DisplayText helper (generalize
  detail → after ' — ' or sub-bullets        matchableSubject): strip emoji,
  priority = section placement,              [#TAG], Linear/GitHub/#hashtag ids,
  no inline priority emoji                   _(carry)_ parenthetical; KEEP
                                             [[entity|Name]] inline
        (no parser / corpus /              TaskCardView: title + 2-line desc
         checksum change)                     + chip row; body/comments/actions
                                             behind expand
                                           TaskChip: machine ids removed from
                                             prose now surface here (Linear/
                                             GitHub/Slack/carry already wired)
                                           priority = stripe only
```

The two tracks are independent and independently shippable. The app track alone
improves *existing* (messy) files immediately via defensive cleaning; the engine
track alone improves *new* files' source. Together they deliver the full fix.

## Components

### Engine — `scout-plugin/phases/core/action-items.md` (prompt only)

Rewrite the task-line authoring rules and worked example. New rules:

1. **The bold title is a short imperative phrase describing the action.** It must
   NOT contain: the `[#TAG]`, any Linear/GitHub id, any hashtag, status words
   ("MERGED", "DEPLOYED", "created + self-assigned"), dates, quotes, emoji, or an
   internal ` — ` separator.
2. **All detail goes after the ` — ` separator (the body) or into
   `- Source:` / `- Context:` sub-bullets:** status, dates, quotes, ids/refs,
   links, and context.
3. **Priority is conveyed only by section placement** (🔴 Urgent / 🟡 To Do /
   🟢 Watching section headers). No inline priority emoji on task lines.
4. The `[#TAG]` stays at line-start (after the checkbox, before the bold) for
   identity — unchanged. Carry-forward copies the tag verbatim, unchanged.
5. Update the self-check guidance + the worked example in the prompt to model the
   new shape. Anonymized target shape:
   ```
   - [ ] [#REPLYX] **Reply to Alex about her purchase question** — She said 1/8–1/2; still open per the sweep. Loop in [[people/priya|Priya]] on onboarding. [[people/alex]] [[PROJ-3026]]
   ```

No change to `scout.ids`, `parser.py`, `render.py`, `backfill.py`, `writer.py`,
or `parser-corpus.json`. The existing `subject`/`body` split already yields a
clean title once the bold stops absorbing detail.

### App — `scout-app`

**1. Markdown fix — `Scout/ActionItems/Views/InlineMarkdownText.swift` (line ~59).**
Parse with `AttributedString.MarkdownParsingOptions(interpretedSyntax:
.inlineOnlyPreservingWhitespace)` (matching the existing correct call site in
`ControlCenter/Detail/SummaryTab.swift:140`). Replace the silent
`?? AttributedString(rewritten)` raw-markdown fallback with a fallback that still
strips/normalizes the common inline tokens so a parser throw never dumps literal
`_`/`**` to screen. This is the fix for the `_(…)_` / `_No Linear ticket yet…_`
rendering.

**2. Display-cleaning helper — new, generalizing the existing scoutctl cleaner.**
Add a `DisplayText` (working name) helper that produces the *display* forms of a
task's title and description. It extends the logic already in
`ActionTask.matchableSubject` / `cleanForScoutctlMatch`
(`Scout/ActionItems/Models/ActionTask.swift:94–155`) but for rendering, not
matching. For a given `subject`/`body` it:
- strips priority emoji (🔴🟡🟢) and status emoji (✅🔄❓⬜🆕🔥🛌) anywhere;
- removes the `[#TAG]` (already stripped by the parser) and any residual bracketed
  tag;
- removes Linear ids (`[A-Z]+-\d+`), GitHub refs (`#\d+`, `owner/repo#\d+`), and
  bare cross-ref hashtags (`#[A-Z0-9]{2,8}`), which are surfaced as chips;
- removes the trailing `_(carries …)_` / `_(carried in from …)_` parenthetical
  (surfaced as the carry chip) and normalizes leftover whitespace/punctuation;
- **keeps `[[entity|Name]]` wikilinks** so they render inline as clickable names.

`plainSubject`/`matchableSubject` remain the parse/scoutctl forms and are
unchanged; this is a separate display-only projection so write matching is not
disturbed.

**3. `TaskCardView` layout — `Scout/ActionItems/Views/TaskCardView.swift`.**
Collapsed card:
- clean **title** via `InlineMarkdownText(displayTitle)`, serif ~15.5 medium,
  `lineLimit(2)`, strikethrough when done;
- **description** via `InlineMarkdownText(displayDescription)`, smaller (~13),
  muted, `lineLimit(2)` collapsed / unlimited expanded;
- the **chip row** (`TaskChip.chips(for:)`), now the home for the ids removed
  from prose;
- the left priority stripe (kept); trailing done/snooze pill (kept); expand
  chevron.
Expanded adds the existing `TaskBodyView` (full body), `CommentListView`,
`TaskLinksView`, `TaskActionsView`, `CommentComposerView`. Inline priority emoji
is gone (cleaned), so priority reads only from the stripe. The `#TAG` mono chip
in the header is removed from the default view.

**4. Relation chips — `Scout/ActionItems/Views/TaskChip.swift`.**
The chip derivation already covers GitHub / Linear / Slack / carry from
`deepLinks` + `carriedInFrom` and is already clickable (2026-06-15 design). No
new chip *types* are required — the machine ids we now hide from prose are the
same ids these chips already represent. Verify coverage: any id shown inline
today that becomes hidden must have a corresponding chip (if a Linear id appears
in text but not as a `deepLink`, ensure it is still detected so it doesn't vanish
silently).

**5. Other surfaces (clean title only, no layout overhaul).**
Apply `displayTitle` to `BoardCardView`, `SectionView` (focus / completedList),
and `DigestView` so titles are clean everywhere. Their layouts are unchanged.

## Error handling

- Markdown parse throw → the new fallback renders cleaned/normalized plain text,
  never raw `_`/`**`.
- Display-cleaning is pure string→string and total: any input (including legacy
  messy lines and empty bodies) yields a valid (possibly empty) display string;
  an empty description simply renders no description row.
- A machine id present in prose but absent from `deepLinks` is a coverage gap: the
  helper must not strip an id that has no chip to land in. Guard: only strip id
  token shapes that the chip/deep-link layer recognizes; leave anything else
  inline rather than lose it.

## Testing

- **Display-cleaning unit tests (new).** Feed corpus-style messy subjects/bodies
  and assert: emoji stripped, `[#TAG]` / Linear / GitHub / cross-ref hashtags
  stripped, `_(carries …)_` stripped, `[[entity|Name]]` retained, whitespace
  normalized. Include a table of before→after cases mirroring the anonymized
  fixtures.
- **`InlineMarkdownText` test (new).** `_italic_` and `_(parenthetical)_` render
  as attributed italic runs, not literal underscores; a subject that previously
  tripped the throw path renders cleaned text, not raw markdown.
- **`TaskChipTests` (extend).** Ids removed from prose have a corresponding chip;
  no id disappears with neither inline text nor chip.
- **Parser/contract tests unchanged and green** — `ParserContractTests` +
  `parser-corpus.json` are not touched (verifies the "no contract change" claim).
- **Build + full `ScoutTests` target green.** Run the whole `ScoutTests` target
  (or a real `@Suite` id) — `-only-testing:ScoutTests/ActionItems` runs zero
  tests (known false-green).
- **Engine.** Regenerate a sample action-items file with the rewritten prompt and
  eyeball that titles are short/clean and detail landed in the body; confirm the
  parser still splits the sample correctly (it should, unchanged).

## Migration & compatibility

- **No data migration.** The app's display-cleaning is defensive and works on both
  old (messy) and new (clean) source, so existing vault files render correctly the
  moment the app ships. The prompt change only affects newly generated files.
- **Obsidian / hand-editability preserved** — ids and markup stay in the `.md`;
  the app only changes what it *displays*.
- **scoutctl write path unchanged** — `matchableSubject` and the `--by-id` /
  `--subject` protocol are untouched; display-cleaning is a separate projection.

## Out of scope

- Preamble / Today's Focus / Trading / section-header rendering.
- A Board-mode layout overhaul (Board gets the clean title, not the new layout).
- Any parser / corpus / checksum / `scout.ids` change.
- iOS render (the prompt change benefits it for free; the Swift render is a
  separate follow-up).
- Drag-to-restatus, board changes, new chip types.

## Sequencing (for the plan)

1. **App M1 — markdown fix** (`InlineMarkdownText`) + its test. Smallest, highest
   visible win; independently shippable.
2. **App M2 — display-cleaning helper** + unit tests.
3. **App M3 — `TaskCardView` layout** (title + description + chips) + chip
   coverage check; apply clean title to other surfaces.
4. **Engine M4 — prompt rewrite** + regenerate/eyeball sample.

App M1–M3 ship as one scout-app PR; engine M4 as one scout-plugin PR. Per the
review-first flow, the spec and the implementation plan are pushed to the PR for
review before any code is written.
