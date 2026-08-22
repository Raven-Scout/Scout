# Action Items Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Reworked 2026-07-12** per Adam's PR review: titles are generated clean at
> the source and machine refs move to a separate `Refs:` block — there is **no
> app-side display cleaner**. The previous revision's `TaskDisplayText` tasks
> are gone. Engine track ships first.

> **Rebased on #78 / v0.10.0 (2026-07-20).** Re-anchored against merged PR #78
> (`KindMarker` + SF Symbol sweep). Verified against `main` @ `4106a38`:
> **(1)** Task 5's line anchors still hold — `TaskCardView.header` is 89-116,
> `nestedRow` is 341-367, and `quickActions`/`trailingStatus`/`chevron`/
> `DS.serif`/`DS.Ink` all exist; the nested sub-task row *already* renders
> `subject`+`body` via `InlineMarkdownText`, so Task 5 Step 2 is effectively a
> no-op. **(2)** PR #79 (copy + menu-bar quick control) is still **open**;
> `quickActions` exists on `main` independent of it, so nothing here depends on
> #79. **(3)** No collision between the relation `TaskChip` (model struct +
> `Glyph` enum) and #78's `KindMarker` view. **(4)** Card priority stays the
> left `DS.priorityColor` stripe; "section placement" priority reads via the
> `KindMarker`-rendered section header now, but the daily-file markdown headers
> still carry 🔴🟡🟢, so the engine prompt in Task 1 is unaffected.

**Goal:** Make action items readable — a clean, short bold title with a smaller
description below and machine ids demoted to relation chips — by fixing the
*source* (the engine's authoring prompt: clean titles, refs in a dedicated
sub-bullet, carry-forward normalization) and teaching the app to render the new
shape verbatim (markdown fix, Refs-block recognition, card layout).

**Architecture:** Two tracks, engine first. The **engine track** (scout-plugin,
Task 1) rewrites the generation prompt so titles are short natural imperatives,
all machine refs land in a `- Refs:` sub-bullet, and carried-forward items are
rewritten into the new shape (this is the corpus migration — there is no other).
The **app track** (scout-app, Tasks 2–5) fixes the markdown renderer, adds
additive sub-line recognition for the Refs block (same mechanism as comment
sub-lines), adds entity/cross-ref chip kinds, and rebuilds the card layout to
render `subject`/`body` **verbatim**. The main-line 4-field parse contract,
`parser-corpus.json`, and the scoutctl write path are untouched.

**Tech Stack:** Swift / SwiftUI (scout-app), Swift Testing (`import Testing`),
`AttributedString(markdown:)`, `NSRegularExpression`; Markdown prompt file
(scout-plugin).

## Global Constraints

- **Do NOT change** the main-line subject/body split (`splitSubjectBody`),
  `ActionTask.plainSubject`, `ActionTask.matchableSubject`,
  `cleanForScoutctlMatch`, or `ScoutTests/Fixtures/parser-corpus.json`. These
  feed the scoutctl `--subject` matcher and the checksum-guarded cross-language
  parser contract. Refs-block recognition is **additive sub-line handling**
  (like the v0.4 comment shape) — it must not alter any existing corpus case's
  4-field output.
- **No display cleaning.** Nothing in the app may strip, rewrite, or normalize
  `subject`/`body` for display. If a legacy line looks messy, it renders messy
  until the engine's carry-forward normalizes it. Do not reintroduce
  `TaskDisplayText` in any form.
- **Test framework:** Swift Testing — `import Testing`, `@testable import Scout`,
  `@Suite`, `@Test`, `#expect(...)`. Mirror `ScoutTests/ActionItems/MatchableSubjectTests.swift`.
- **Running tests:** run the WHOLE `ScoutTests` target. `-only-testing:ScoutTests/ActionItems`
  (a path) matches ZERO tests and reports a false green. Use:
  `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
- **Building:** `xcodebuild build -scheme Scout -destination 'platform=macOS'`
- New `.swift` files under `Scout/` or `ScoutTests/` auto-compile (synchronized
  file groups) — no `.pbxproj` edits.
- **Public repo:** any example strings in code/tests/docs use the anonymized
  stand-ins (`Alex`/`Priya`/`Sam`, Linear `PROJ-####`/`OPS-####`, GitHub
  `example-org/<repo>`, cross-ref `#XREF`). Never real vault content.
- Commit after each task with a scoped message.
- SourceKit "Cannot find type" / "No such module 'Testing'" in-editor diagnostics
  are IDE noise — trust `xcodebuild`, not the editor.

---

## File Structure

**scout-plugin**
- Modify `phases/core/action-items.md` — authoring prompt rewrite: title rules,
  `Refs:` block, carry-forward normalization (Task 1).

**scout-app**
- Modify `Scout/ActionItems/Views/InlineMarkdownText.swift` — markdown parse fix (Task 2).
- Create `ScoutTests/ActionItems/InlineMarkdownTextTests.swift` — markdown fix test (Task 2).
- Modify `Scout/ActionItems/ActionItemsParser.swift` — `Refs:` sub-line recognition (Task 3).
- Modify `Scout/ActionItems/Models/TaskDeepLink.swift` + `Scout/ActionItems/Models/ActionTask.swift` — entity/cross-ref link kinds, refs storage (Task 3).
- Create `ScoutTests/ActionItems/RefsBlockTests.swift` — parser + token tests (Task 3).
- Modify `Scout/ActionItems/Views/TaskChip.swift` (+ `TaskChipTests`) — entity/cross-ref chips (Task 4).
- Modify `Scout/ActionItems/Views/TaskCardView.swift` — new card layout (Task 5).

---

## Task 1: Rewrite the engine authoring prompt (scout-plugin) — the fix

**Files:**
- Modify: `../scout-plugin/phases/core/action-items.md` (Action Items File
  Format ~48-92, Hard Rule region ~107-111, carry-forward rules)

**Interfaces:**
- No code interface. The prompt is prose the LLM follows when generating the
  daily file. No change to `parser.py`, `render.py`, `scout.ids`, or
  `parser-corpus.json` — the new shape is a valid instance of the unchanged
  4-field contract.

**Design:** Titles are generated clean (no stripping anywhere, ever). All
machine refs move to a single `- Refs:` sub-bullet. Carried-forward items are
rewritten into the new shape — this is the whole migration story. Ships as its
own scout-plugin PR.

- [ ] **Step 1: Add the title + Refs hard rule after the canonical shape (after line 111)**

Insert this block in `phases/core/action-items.md` immediately after the
"Canonical task line shape" paragraph:

```markdown
### Hard Rule — Clean Title, Prose Body, Refs Block

The **bold segment is the human-readable title** and must read as a short
natural imperative phrase — what to do, in plain words. Keep it scannable
(aim for a single line).

The bold title MUST NOT contain any of:
- the `[#TAG]` (it sits *before* the bold, never inside it);
- Linear ids (`PROJ-1234`), GitHub refs (`#1234`, `owner/repo#1234`), or
  cross-reference hashtags (`#SHORTCODE`);
- status words ("MERGED", "DEPLOYED", "created + self-assigned", "done→todo");
- dates, times, or quoted snippets;
- emoji of any kind (including priority 🔴🟡🟢);
- an internal ` — ` / ` – ` separator (that dash separates title from body).

The **body** (after the ` — ` separator, plus `- Source:` / `- Context:`
sub-bullets) is human prose: status, dates, quotes, context. Entity wikilinks
(`[[people/alex|Alex]]`) may appear inline in the body ONLY where a name reads
naturally in a sentence. Bare machine ids never appear in the body prose.

**All machine refs go in ONE `- Refs:` sub-bullet** directly under the task
line, ` · `-separated: Linear ids, GitHub refs, Slack permalinks,
cross-reference hashtags, and entity wikilinks that are pure references (not
part of a sentence). Omit the sub-bullet when a task has no refs.

**Priority is expressed only by which section the item lives in** (🔴 Urgent /
🟡 To Do / 🟢 Watching). Do NOT prepend a priority emoji to a task line.

Good vs bad (anonymized):

    ✅  - [ ] [#REPLYX] **Reply to Alex about her purchase question** — She said 1/8–1/2; still open per the sweep. Loop in [[people/priya|Priya]] on onboarding.
    ✅    - Refs: [[people/alex]] · [[PROJ-3026]] · example-org/repo#7056 · #XREF
    ❌  - [ ] [#REPLYX] 🟡 **Reply to Alex — purchase Q still open (Thu 4:55 PM: "1/8 to 1/2") PROJ-3026** _(carries)_ — …
```

- [ ] **Step 2: Add the carry-forward normalization rule**

In the carry-forward section of the same file (where verbatim-tag copying is
specified), add:

```markdown
**Carry-forward rewrites the item into the canonical shape.** When carrying an
open item into today's file: keep the `[#TAG]` verbatim and preserve every
fact, but re-author the line — clean title per the hard rule above, narrative
into the body, all machine refs consolidated into the `- Refs:` sub-bullet,
no inline priority emoji. Do NOT preserve legacy formatting for its own sake.
This is how the corpus converges; there is no other migration.
```

- [ ] **Step 3: Update the file-format examples (lines 57-85)**

Update the canonical examples to model the new shape: no inline priority
emoji, no ids in title or body prose, a `- Refs:` sub-bullet on at least one
example. Keep the `**[Item title]** — [Description]` skeleton.

- [ ] **Step 4: Verify the self-check grep still holds**

The `[#TAG]` self-check grep (lines 123-128) matches the leading tag, which is
unchanged: `- [ ] [#REPLYX] **…**` still contains ` [#REPLYX] ` → passes.

- [ ] **Step 5: Contract check**

The new shape is a valid instance of the unchanged 4-field contract (subject =
clean bold, body = post-dash remainder; the Refs sub-bullet is a sub-line the
Python parser already ignores for main-line fields).

Run (in `../scout-plugin`): `pytest engine/tests/unit/test_parser_contract.py engine/tests/unit/test_parser_corpus_checksum.py -q`
Expected: PASS (contract + checksum unchanged).

- [ ] **Step 6: Manual generation check**

Regenerate (or hand-write) one section using the new rules and eyeball: titles
short/natural, refs in the block, no inline emoji. Then simulate a
carry-forward over one legacy messy item (hand-run the prompt against it) and
eyeball the normalization output.

- [ ] **Step 7: Commit (in scout-plugin)**

```bash
cd ../scout-plugin
git add phases/core/action-items.md
git commit -m "feat(action-items): clean generated titles; machine refs -> Refs block; carry-forward normalizes"
```

---

## Task 2: Fix the inline-markdown renderer (`_italic_` + throw fallback)

**Files:**
- Modify: `Scout/ActionItems/Views/InlineMarkdownText.swift:53-63`
- Test: `ScoutTests/ActionItems/InlineMarkdownTextTests.swift` (create)

**Interfaces:**
- Produces: `InlineMarkdownText.attributedString(for:) -> AttributedString`
  (change from `private` to internal `static` so it is testable).

**Why:** `attributedString(for:)` calls `AttributedString(markdown:)` with default
`.full` interpreted syntax and silently falls back to the RAW string on any
throw, so a legit `_italic_` (e.g. `_(net-new from the review)_`) can render as
literal underscores. The correct sibling call site
(`ControlCenter/Detail/SummaryTab.swift:140`) already uses
`.inlineOnlyPreservingWhitespace`. Match it. Independent of everything else.

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/ActionItems/InlineMarkdownTextTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("Inline markdown rendering")
struct InlineMarkdownTextTests {
    /// `_word_` must render as an italic (emphasized) run, not literal underscores.
    @Test func underscoreItalicRendersEmphasized() {
        let attr = InlineMarkdownText.attributedString(for: "start _emphasis here_ end")
        let plain = String(attr.characters)
        #expect(!plain.contains("_"))                       // underscores consumed, not literal
        let emphasized = attr.runs.contains { run in
            run.inlinePresentationIntent?.contains(.emphasized) == true
        }
        #expect(emphasized)
    }

    /// A parenthetical italic (the shape Adam saw unrendered) also emphasizes.
    @Test func parentheticalItalicRenders() {
        let attr = InlineMarkdownText.attributedString(for: "note _(net-new from the review)_ tail")
        #expect(!String(attr.characters).contains("_"))
    }

    /// Bold still works after the syntax change.
    @Test func boldStillRenders() {
        let attr = InlineMarkdownText.attributedString(for: "**Reply to Alex**")
        let plain = String(attr.characters)
        #expect(plain == "Reply to Alex")
        let strong = attr.runs.contains { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true }
        #expect(strong)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: FAIL — `attributedString(for:)` is `private` (compile error) and/or
`underscoreItalicRendersEmphasized` fails because the default parse leaves `_`.

- [ ] **Step 3: Apply the fix**

In `Scout/ActionItems/Views/InlineMarkdownText.swift`, change the visibility of
`attributedString(for:)` from `private static` to `static` and replace the parse
line. Full new body of the function (lines 53-63):

```swift
    static func attributedString(for raw: String) -> AttributedString {
        if let hit = cache[raw] { return hit }
        // Linkify GitHub refs before wikilinks: the linkifier protects existing
        // markdown links / wikilinks, and rewriteWikilinks then leaves the
        // GitHub `[label](https://…)` links untouched.
        let rewritten = rewriteWikilinks(GitHubRefLinkifier.linkify(raw))
        // Inline-only + preserve whitespace: matches SummaryTab's call site,
        // avoids block-level parsing (fewer throws) and the whitespace collapse
        // that broke `_italic_` under the default `.full` syntax.
        let options = AttributedString.MarkdownParsingOptions(
            interpretedSyntax: .inlineOnlyPreservingWhitespace
        )
        let computed = (try? AttributedString(markdown: rewritten, options: options))
            ?? AttributedString(rewritten)
        if cache.count >= cacheCap { cache.removeAll(keepingCapacity: true) }
        cache[raw] = computed
        return computed
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: PASS for the three new tests; the full suite stays green (watch
`GitHubRefLinkifierTests`, `PreambleCard`, `SummaryTab` for regressions).

- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/Views/InlineMarkdownText.swift ScoutTests/ActionItems/InlineMarkdownTextTests.swift
git commit -m "fix(action-items): render _italic_ markdown (inline-only syntax, no raw fallback)"
```

---

## Task 3: Recognize the `Refs:` sub-bullet (parser + model)

**Files:**
- Modify: `Scout/ActionItems/ActionItemsParser.swift` (sub-line handling,
  ~lines 220-245 region where comment shapes are recognized)
- Modify: `Scout/ActionItems/Models/TaskDeepLink.swift` (new cases),
  `Scout/ActionItems/Models/ActionTask.swift` (refs storage)
- Test: `ScoutTests/ActionItems/RefsBlockTests.swift` (create)

**Interfaces:**
- Produces: `TaskDeepLink.entity(path: String, label: String?)` and
  `TaskDeepLink.crossRef(tag: String)` cases (with `id`, `title`, destination
  handling mirroring the existing cases at `TaskDeepLink.swift:4-34`).
- Produces: Refs-block tokens merged into `ActionTask.deepLinks` (chips read
  from there already); the Refs line excluded from `body`/prose.

**Design:** Mirror the existing comment-sub-line recognizers
(`ActionItemsParser.swift:220-245`): an indented `- Refs: …` line attaches to
the preceding task instead of rendering as body text or a comment. Tokens are
` · `-separated; each token runs through `detectDeepLinks` first (Linear /
GitHub / Slack), then the two new shapes: `[[entity]]` / `[[entity|Label]]`
wikilink refs and `#XREF` cross-ref hashtags. An unrecognized token becomes a
plain-text token (rendered as an inert chip in Task 4) — nothing is dropped.

**TDD steps:**

- [ ] **Step 1: Write failing tests** (`RefsBlockTests.swift`): a task followed
  by `  - Refs: [[people/alex]] · [[PROJ-3026]] · example-org/repo#7056 · #XREF`
  yields (a) the four refs on the task (entity, linear, githubPR, crossRef),
  (b) a `body` that does NOT contain the Refs text, (c) no comment created for
  the line. Add: a malformed token (`??garbage`) surfaces as a plain token;
  a task with no Refs line parses exactly as before (regression guard against
  the comment carve-outs at parser lines ~225-231).
- [ ] **Step 2:** Run — expect FAIL (line currently parses as body/comment).
- [ ] **Step 3:** Add the `Refs:` recognizer + `TaskDeepLink` cases + token
  parsing. Keep the recognizer's regex anchored like the comment recognizers
  so ordinary `- Source:` / `- Context:` sub-bullets are untouched.
- [ ] **Step 4:** Run — expect PASS, full `ScoutTests` green, and
  `ParserContractTests` untouched/green (main-line contract unaffected).
- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/ActionItemsParser.swift Scout/ActionItems/Models/TaskDeepLink.swift Scout/ActionItems/Models/ActionTask.swift ScoutTests/ActionItems/RefsBlockTests.swift
git commit -m "feat(action-items): parse the Refs: sub-bullet into deep links (entity + cross-ref kinds)"
```

---

## Task 4: Entity + cross-ref chips

**Files:**
- Modify: `Scout/ActionItems/Views/TaskChip.swift`
- Test: extend `ScoutTests/ActionItems/TaskChipTests.swift`

**Interfaces:**
- Consumes: the new `TaskDeepLink.entity` / `.crossRef` cases (Task 3).
- Produces: chips for them — entity chip opens the KB note in the KB tab
  (reuse the KB-tab deep-link mechanism from the 2026-07-06 KB work);
  cross-ref chip scrolls to the referenced item when it exists in the current
  file, renders inert otherwise.

**Steps:**

- [ ] **Step 1: Failing tests** — `TaskChip.chips(for:)` on a task with the
  Task-3 fixture yields one chip per Refs token (coverage rule: N tokens → N
  chips, plain tokens included); entity chip label is the wikilink label (or
  last path segment); cross-ref chip label is the bare tag.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement chip derivation + destinations.
- [ ] **Step 4:** Run — PASS, full suite green.
- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/Views/TaskChip.swift ScoutTests/ActionItems/TaskChipTests.swift
git commit -m "feat(action-items): entity + cross-ref chips from the Refs block"
```

---

## Task 5: Rebuild the `TaskCardView` layout (title + description + chips, verbatim)

**Files:**
- Modify: `Scout/ActionItems/Views/TaskCardView.swift:89-116` (header),
  `:341-367` (nested row)

**Interfaces:**
- Consumes: `task.subject` and `task.body` **verbatim**, existing
  `TaskChip.chips(for:)` (now incl. Task-4 kinds), `InlineMarkdownText`,
  `TaskBodyView`. **No display-cleaning helper exists or is added.**

**Design:** Collapsed card = bold title (`InlineMarkdownText(task.subject)`,
`lineLimit 2`) + 2-line description teaser (`InlineMarkdownText(task.body)`,
smaller/muted) + chip row. The `#PREFIX` mono chip is removed from the default
view. Expanded = full body via `TaskBodyView(rawBody: task.body)` (unchanged) +
comments + links + actions + composer. Priority reads only from the existing
left stripe. View-layout change; verified by build, the Task 2–4 tests it
consumes, and a visual check.

- [ ] **Step 1: Replace the header (lines 89-116)**

```swift
    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                InlineMarkdownText(task.subject)
                    .font(DS.serif(15.5, weight: .medium))
                    .foregroundStyle(task.done ? DS.Ink.p3 : DS.Ink.p1)
                    .strikethrough(task.done, color: DS.Ink.p4)
                    .lineLimit(expanded ? nil : 2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .onTapGesture { toggle() }
                if !expanded { quickActions }
                trailingStatus
                chevron
            }
            if !expanded && !task.body.isEmpty {
                InlineMarkdownText(task.body)
                    .font(DS.serif(13))
                    .foregroundStyle(DS.Ink.p3)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .onTapGesture { toggle() }
            }
            if !chips.isEmpty {
                chipRow
            }
        }
        .padding(14)
        .contentShape(Rectangle())
    }
```

(This deletes the `if let prefix = task.shortPrefix { Text("#\(prefix)") … }`
block — the continuity key is hidden from the default view. The expanded
detail's `TaskBodyView(rawBody: task.body)` is already correct and unchanged.)

- [ ] **Step 2: Nested sub-task row (lines 348-361)** — same treatment:
`InlineMarkdownText(task.subject)` at 13.5 serif + `InlineMarkdownText(task.body)`
at 12.5 muted below it, both verbatim.

- [ ] **Step 3: Build + run the full suite**

Run: `xcodebuild build -scheme Scout -destination 'platform=macOS'`
Then: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: build succeeds; full suite green (no test references the deleted
`#prefix` header).

- [ ] **Step 4: Visual verification**

Launch the app (use the `/run` skill). Against a *new-shape* file (hand-write
one if the engine change hasn't shipped): titles short + bold, description
below, refs as chips, no Refs line in prose, `_italic_` renders italic.
Against a *legacy* file: items render as-authored (messy but intact — that's
the contract), nothing crashes, nothing is stripped.

- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/Views/TaskCardView.swift
git commit -m "feat(action-items): card = title + description + chips (verbatim render); hide #prefix"
```

---

## Self-Review

**Spec coverage:**
- Engine prompt rewrite (clean title, prose body, `Refs:` block, priority =
  section, carry-forward normalization) → **Task 1**.
- Markdown `_italic_` fix → **Task 2**.
- Refs-block recognition, excluded from prose, additive sub-line handling →
  **Task 3**.
- Entity + cross-ref chips; every Refs token yields exactly one chip → **Task 4**.
- Card layout = verbatim title + 2-line description + chips; hide `#TAG`;
  priority stripe only → **Task 5**.
- No app-side cleaning anywhere → global constraint; enforced by the absence of
  any cleaner component.
- Main-line contract/corpus/scoutctl unchanged + green → asserted in Tasks 1,
  3, 5.
- Migration = carry-forward normalization (Task 1 Step 2); legacy render
  honesty checked in Task 5 Step 4.

**Known limitation (documented, not a gap):** until the engine change ships
and an item is carried once, legacy items render as-authored — messy titles
included. This is deliberate: the interim is days, and it keeps the app free of
a second cleaning system. Bare GitHub refs in *legacy* prose remain clickable
via `GitHubRefLinkifier`; in new files they live in the Refs block as chips.

**Placeholder scan:** Tasks 3–4 specify mirrors + interfaces rather than full
code (parser internals are implemented against the comment-recognizer pattern
at `ActionItemsParser.swift:220-245` and the `TaskDeepLink` cases at
`Models/TaskDeepLink.swift:4-34`); Tasks 1, 2, 5 show complete
content/code.

## Execution Handoff

Engine track = Task 1 (one scout-plugin PR, ship first); app track = Tasks 2–5
(one scout-app PR). Per the review-first flow, this reworked plan + spec land
on the scout-app PR (#76) for re-review before code.
