# Action Items Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make action items readable — a clean, short bold title with a smaller
description below and machine ids demoted to relation chips — by fixing the app
renderer and tightening the engine's authoring prompt, with no data-contract
change.

**Architecture:** Two independent tracks. The **app track** (scout-app, Tasks
1–5) fixes the markdown renderer, adds a display-only cleaning helper that strips
machine noise while keeping names inline, and rebuilds the card layout. The
**engine track** (scout-plugin, Task 6) rewrites the generation prompt so new
files are clean at the source. The parser, `parser-corpus.json`, and the scoutctl
write path are untouched.

**Tech Stack:** Swift / SwiftUI (scout-app), Swift Testing (`import Testing`),
`AttributedString(markdown:)`, `NSRegularExpression`; Markdown prompt file
(scout-plugin).

## Global Constraints

- **Do NOT change** `ActionTask.plainSubject`, `ActionTask.matchableSubject`,
  `cleanForScoutctlMatch`, `ActionItemsParser`, or
  `ScoutTests/Fixtures/parser-corpus.json`. These feed the scoutctl `--subject`
  matcher and the checksum-guarded cross-language parser contract. The new
  display cleaner is a **separate, additive projection** that never touches the
  write path.
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

**scout-app**
- Modify `Scout/ActionItems/Views/InlineMarkdownText.swift` — markdown parse fix (Task 1).
- Create `Scout/ActionItems/Models/TaskDisplayText.swift` — display cleaner (Tasks 2–3).
- Create `ScoutTests/ActionItems/TaskDisplayTextTests.swift` — cleaner tests (Tasks 2–3).
- Modify `ScoutTests/ActionItems/InlineMarkdownTextTests.swift` (create) — markdown fix test (Task 1).
- Modify `Scout/ActionItems/Views/TaskCardView.swift` — new card layout (Task 4).
- Modify `Scout/ActionItems/Views/BoardCardView.swift`, `Scout/ActionItems/Views/SectionView.swift`, `Scout/ActionItems/Views/DigestView.swift` — clean title on other surfaces (Task 5).

**scout-plugin**
- Modify `phases/core/action-items.md` — authoring prompt rewrite (Task 6).

---

## Task 1: Fix the inline-markdown renderer (`_italic_` + throw fallback)

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
`.inlineOnlyPreservingWhitespace`. Match it.

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

## Task 2: `TaskDisplayText.clean` — the core string cleaner

**Files:**
- Create: `Scout/ActionItems/Models/TaskDisplayText.swift`
- Test: `ScoutTests/ActionItems/TaskDisplayTextTests.swift` (create)

**Interfaces:**
- Produces: `TaskDisplayText.clean(_ raw: String, linearIDs: Set<String>) -> String`
  — pure, total (any input → a valid, possibly-empty string).

**Behavior (order matters):** strip residual `[#..]` bracket tokens; drop
Linear-id-shaped wikilinks (`[[PROJ-3026]]`) but keep name wikilinks
(`[[people/alex|Alex]]`); drop bare Linear ids passed in `linearIDs` (they have a
chip); drop cross-ref hashtags (`#XREF`, ≥1 letter — leaves numeric `#123`
GitHub refs alone); drop priority/status emoji anywhere; drop only the
`_(carries …)_` / `_(carried in from …)_` italic markers (keep other italics);
normalize dangling dash separators + whitespace. Markdown emphasis markers
(`**`, `_`, `~~`, other `[[..]]`, `[..](..)`) are KEPT so `InlineMarkdownText`
still renders them.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/ActionItems/TaskDisplayTextTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("Task display text — clean")
struct TaskDisplayTextCleanTests {
    private func clean(_ s: String, _ ids: Set<String> = []) -> String {
        TaskDisplayText.clean(s, linearIDs: ids)
    }

    @Test func stripsBracketTags() {
        #expect(clean("[#REPLYX] Reply to Alex") == "Reply to Alex")
        #expect(clean("[#3502] Ship the fix") == "Ship the fix")   // numeric bracket noise
    }

    @Test func stripsPriorityAndStatusEmoji() {
        #expect(clean("🔴 🆕 Reply to Alex") == "Reply to Alex")
        #expect(clean("✅ Merge the PR") == "Merge the PR")
    }

    @Test func dropsChippedBareLinearIDsButKeepsProse() {
        #expect(clean("Follow up on PROJ-3026 with Alex", ["PROJ-3026"]) == "Follow up on with Alex")
    }

    @Test func dropsLinearWikilinksKeepsNameWikilinks() {
        #expect(clean("Sync — [[PROJ-3026]]", ["PROJ-3026"]) == "Sync")
        #expect(clean("Loop in [[people/priya|Priya]] on onboarding")
                == "Loop in [[people/priya|Priya]] on onboarding")   // name kept verbatim
    }

    @Test func dropsCrossRefHashtagsNotNumericRefs() {
        #expect(clean("Escalate the risk #XREF") == "Escalate the risk")
        #expect(clean("See PR example-org/repo#123") == "See PR example-org/repo#123") // numeric ref kept
    }

    @Test func dropsCarryMarkerKeepsOtherItalics() {
        #expect(clean("Reply to Alex _(carries 7/3→7/6; per sweep)_") == "Reply to Alex")
        #expect(clean("Ship it _(net-new from the review)_") == "Ship it _(net-new from the review)_")
    }

    @Test func keepsBoldMarkup() {
        #expect(clean("**Reply to Alex**") == "**Reply to Alex**")
    }

    @Test func totalOnEmptyAndPlain() {
        #expect(clean("") == "")
        #expect(clean("Just a plain sentence.") == "Just a plain sentence.")
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: FAIL to compile — `TaskDisplayText` does not exist yet.

- [ ] **Step 3: Write the implementation**

Create `Scout/ActionItems/Models/TaskDisplayText.swift`:

```swift
import Foundation

/// Display-only projection of a task's title/description. Strips machine noise
/// that has zero human value — ids, priority/status emoji, carry markers — while
/// KEEPING markdown emphasis (so `InlineMarkdownText` renders bold/italic) and
/// KEEPING `[[entity|Name]]` wikilinks that are not Linear-id shaped (a name
/// reads naturally in a sentence).
///
/// SEPARATE from `ActionTask.plainSubject` / `matchableSubject`, which feed the
/// scoutctl `--subject` matcher + the cross-language parser contract and MUST
/// NOT change. `TaskDisplayText` never touches the write path.
enum TaskDisplayText {
    /// Core cleaner. Pure `String -> String`. `linearIDs` = Linear ids that
    /// already have a chip, so they can be removed from prose without losing
    /// navigation.
    static func clean(_ raw: String, linearIDs: Set<String>) -> String {
        var s = raw

        // 1. Residual bracketed id tokens: `[#TAG]` (defensive — the parser
        //    already strips the leading one) and pure-numeric `[#3502]` noise.
        s = replace(s, #"\[#[A-Z0-9]+\]"#, "")

        // 2. Linear-id WIKILINKS: `[[PROJ-3026]]` / `[[PROJ-3026|alias]]` whose
        //    target is Linear-id shaped → drop (a Linear chip carries them).
        //    Name wikilinks are kept.
        s = removeLinearWikilinks(s)

        // 3. Bare Linear ids that have a chip.
        for id in linearIDs {
            s = replace(s, #"\b"# + NSRegularExpression.escapedPattern(for: id) + #"\b"#, "")
        }

        // 4. Cross-ref hashtags: 2–8 [A-Z0-9] with ≥1 letter (`#XREF`). The
        //    ≥1-letter rule leaves numeric `#123` GitHub refs clickable-inline.
        s = replace(s, #"(?<![\w/])#(?=[A-Z0-9]{2,8}\b)[A-Z0-9]*[A-Z][A-Z0-9]*\b"#, "")

        // 5. Priority + status/marker emoji anywhere.
        for e in ["🔴", "🟡", "🟢", "✅", "🔄", "❓", "⬜", "🆕", "🔥", "🛌"] {
            s = s.replacingOccurrences(of: e, with: "")
        }

        // 6. Carry/snooze italic markers ONLY. Legit italics are kept.
        s = replace(s, #"_\((?:carries|carried in from)[^)]*\)_"#, "")

        // 7. Normalize: drop dangling dash separators left by removals, collapse
        //    runs of whitespace, trim.
        s = replace(s, #"\s*[—–]\s*$"#, "")
        s = replace(s, #"^\s*[—–]\s*"#, "")
        while s.contains("  ") { s = s.replacingOccurrences(of: "  ", with: " ") }
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: - Helpers

    private static func replace(_ s: String, _ pattern: String, _ template: String) -> String {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return s }
        let m = NSMutableString(string: s)
        re.replaceMatches(in: m, range: NSRange(location: 0, length: m.length), withTemplate: template)
        return m as String
    }

    private static let linearShape = try? NSRegularExpression(pattern: #"^[A-Z]{2,10}-\d+$"#)

    private static func removeLinearWikilinks(_ s: String) -> String {
        guard let re = try? NSRegularExpression(pattern: #"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]"#),
              let shape = linearShape else { return s }
        let ns = s as NSString
        var result = s
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)).reversed() {
            let target = ns.substring(with: m.range(at: 1))
            let isLinear = shape.firstMatch(
                in: target, range: NSRange(location: 0, length: (target as NSString).length)
            ) != nil
            if isLinear {
                result = (result as NSString).replacingCharacters(in: m.range, with: "")
            }
        }
        return result
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: PASS for all `TaskDisplayTextCleanTests`.

- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/Models/TaskDisplayText.swift ScoutTests/ActionItems/TaskDisplayTextTests.swift
git commit -m "feat(action-items): add TaskDisplayText.clean display cleaner"
```

---

## Task 3: `TaskDisplayText.title` / `.description` / `.chippedLinearIDs`

**Files:**
- Modify: `Scout/ActionItems/Models/TaskDisplayText.swift`
- Test: `ScoutTests/ActionItems/TaskDisplayTextTests.swift`

**Interfaces:**
- Consumes: `TaskDisplayText.clean(_:linearIDs:)` (Task 2); `ActionTask`
  (`subject`, `body`, `deepLinks`).
- Produces:
  - `TaskDisplayText.title(for: ActionTask) -> String`
  - `TaskDisplayText.description(for: ActionTask) -> String`
  - `TaskDisplayText.chippedLinearIDs(_ task: ActionTask) -> Set<String>`

- [ ] **Step 1: Write the failing tests**

Append to `ScoutTests/ActionItems/TaskDisplayTextTests.swift`:

```swift
@Suite("Task display text — title/description")
struct TaskDisplayTextTaskTests {
    private func task(subject: String, body: String = "", links: [TaskDeepLink] = []) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: 1, done: false, subject: subject, plainSubject: subject,
            body: body, comments: [], deepLinks: links, snoozedUntil: nil, carriedInFrom: nil
        )
    }

    @Test func titleStripsNoiseFromSubject() {
        let t = task(
            subject: "🟡 **Reply to Alex** _(carries 7/3→7/6)_",
            links: []
        )
        #expect(TaskDisplayText.title(for: t) == "**Reply to Alex**")
    }

    @Test func descriptionStripsChippedLinearIDFromBody() {
        let t = task(
            subject: "**Sync with Priya**",
            body: "Confirm PROJ-3026 landed; loop in [[people/priya|Priya]].",
            links: [.linear(id: "PROJ-3026")]
        )
        #expect(TaskDisplayText.description(for: t)
                == "Confirm landed; loop in [[people/priya|Priya]].")
    }

    @Test func chippedLinearIDsReflectDeepLinks() {
        let t = task(subject: "x", links: [.linear(id: "PROJ-3026"), .linear(id: "OPS-12")])
        #expect(TaskDisplayText.chippedLinearIDs(t) == ["PROJ-3026", "OPS-12"])
    }

    @Test func emptyBodyDescriptionIsEmpty() {
        #expect(TaskDisplayText.description(for: task(subject: "x", body: "")) == "")
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: FAIL to compile — `title`/`description`/`chippedLinearIDs` don't exist.

- [ ] **Step 3: Write the implementation**

Add to `enum TaskDisplayText` in `Scout/ActionItems/Models/TaskDisplayText.swift`
(above the `// MARK: - Helpers` line):

```swift
    /// Cleaned title for display (from `subject`).
    static func title(for task: ActionTask) -> String {
        clean(task.subject, linearIDs: chippedLinearIDs(task))
    }

    /// Cleaned description for display (from `body`).
    static func description(for task: ActionTask) -> String {
        clean(task.body, linearIDs: chippedLinearIDs(task))
    }

    /// Linear ids that already surface as a `.linear` chip, so removing them
    /// from prose loses no navigation.
    static func chippedLinearIDs(_ task: ActionTask) -> Set<String> {
        Set(task.deepLinks.compactMap {
            if case .linear(let id) = $0 { return id } else { return nil }
        })
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: PASS for `TaskDisplayTextTaskTests`.

- [ ] **Step 5: Commit**

```bash
git add Scout/ActionItems/Models/TaskDisplayText.swift ScoutTests/ActionItems/TaskDisplayTextTests.swift
git commit -m "feat(action-items): add TaskDisplayText title/description/chippedLinearIDs"
```

---

## Task 4: Rebuild the `TaskCardView` layout (title + description + chips)

**Files:**
- Modify: `Scout/ActionItems/Views/TaskCardView.swift:89-116` (header),
  `:278-283` (detail body), `:341-367` (nested row)

**Interfaces:**
- Consumes: `TaskDisplayText.title(for:)`, `TaskDisplayText.description(for:)`
  (Task 3); existing `TaskChip.chips(for:)`, `InlineMarkdownText`, `TaskBodyView`.

**Design:** Collapsed card = clean bold title (`lineLimit 2`) + 2-line
description teaser (smaller/muted) + chip row. The `#PREFIX` mono chip is
removed. Expanded = full description via `TaskBodyView` fed the CLEANED body +
comments + links + actions + composer (unchanged). Priority reads only from the
existing left stripe (emoji now cleaned out). This is a view-layout change; there
is no unit test — verified by build, the Task 1–3 tests it consumes, and a visual
check.

- [ ] **Step 1: Add computed display strings**

In `TaskCardView`, add near the other computed vars (after `effectiveKind`,
around line 59):

```swift
    private var displayTitle: String { TaskDisplayText.title(for: task) }
    private var displayDescription: String { TaskDisplayText.description(for: task) }
```

- [ ] **Step 2: Replace the header (lines 89-116)**

Replace the entire `private var header: some View { ... }` with:

```swift
    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                InlineMarkdownText(displayTitle)
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
            if !expanded && !displayDescription.isEmpty {
                InlineMarkdownText(displayDescription)
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
block — the continuity key is hidden from the default view.)

- [ ] **Step 3: Feed the cleaned body to the expanded detail (lines 280-282)**

In `private var detail`, replace:

```swift
            if !task.body.isEmpty {
                TaskBodyView(rawBody: task.body)
            }
```

with:

```swift
            if !displayDescription.isEmpty {
                TaskBodyView(rawBody: displayDescription)
            }
```

- [ ] **Step 4: Clean the nested sub-task row (lines 348-361)**

In `private var nestedRow`, replace the two `InlineMarkdownText(task.subject)` /
`InlineMarkdownText(task.body)` calls with the cleaned forms:

```swift
                InlineMarkdownText(displayTitle)
                    .font(DS.serif(13.5))
                    .foregroundStyle(task.done ? DS.Ink.p3 : DS.Ink.p2)
                    .strikethrough(task.done, color: DS.Ink.p4)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if !displayDescription.isEmpty {
                    InlineMarkdownText(displayDescription)
                        .font(DS.serif(12.5))
                        .foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
```

- [ ] **Step 5: Build + run the full suite**

Run: `xcodebuild build -scheme Scout -destination 'platform=macOS'`
Then: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: build succeeds; full suite green (no test references the deleted
`#prefix` header).

- [ ] **Step 6: Visual verification**

Launch the app against a day with messy items (use the `/run` skill or open the
built app). Confirm: titles are short + bold with no emoji/ids/carry markers; a
smaller description sits below; machine ids appear only as chips; `_italic_` in
descriptions renders italic; expanding shows the full body + comments + actions.

- [ ] **Step 7: Commit**

```bash
git add Scout/ActionItems/Views/TaskCardView.swift
git commit -m "feat(action-items): card = clean title + description + chips; hide #prefix, drop inline emoji"
```

---

## Task 5: Clean titles on the other surfaces + chip-coverage guard test

**Files:**
- Modify: `Scout/ActionItems/Views/BoardCardView.swift:14-21`,
  `Scout/ActionItems/Views/SectionView.swift` (focus ~96-139, completedList ~143-174),
  `Scout/ActionItems/Views/DigestView.swift`
- Test: `ScoutTests/ActionItems/TaskDisplayTextTests.swift`

**Interfaces:**
- Consumes: `TaskDisplayText.title(for:)`, `TaskDisplayText.description(for:)`.

**Design:** These surfaces render `task.subject`/`task.body` raw. Swap to the
cleaned forms so titles are clean everywhere; layouts are unchanged. Then add a
guard test asserting a bare Linear id present in a chip is removed from prose,
while a Linear id with NO chip is retained (never silently lost).

- [ ] **Step 1: Write the failing guard test**

Append to `ScoutTests/ActionItems/TaskDisplayTextTests.swift` (inside
`TaskDisplayTextTaskTests`):

```swift
    @Test func neverStripsAnUnchippedLinearID() {
        // PROJ-99 appears in prose but is NOT a deep link → no chip → keep it,
        // rather than silently deleting a reference with nowhere to land.
        let t = task(subject: "**Check PROJ-99 status**", links: [])
        #expect(TaskDisplayText.title(for: t) == "**Check PROJ-99 status**")
    }
```

- [ ] **Step 2: Run to verify it passes already (documents the guarantee)**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: PASS — `clean` only strips ids passed in `linearIDs`, and
`chippedLinearIDs` for this task is empty. This test locks the guarantee in place.

- [ ] **Step 3: Apply cleaned title to `BoardCardView` (lines 14-21)**

Replace `InlineMarkdownText(task.subject)` in the board card title with
`InlineMarkdownText(TaskDisplayText.title(for: task))`. Leave `lineLimit(3)` and
the rest as-is.

- [ ] **Step 4: Apply cleaned title to `SectionView` focus + completedList**

In `SectionView.swift`, the `focus` numbered list and `completedList`
DisclosureGroup each render a task via `InlineMarkdownText(task.subject)` (or the
focus bullet). Replace each with `InlineMarkdownText(TaskDisplayText.title(for: task))`.
(Focus bullets that are section `bullets`, not `tasks`, are left unchanged — only
task rows get the cleaner.)

- [ ] **Step 5: Apply cleaned title to `DigestView`**

Where `DigestView` renders task subjects via `InlineMarkdownText`, swap to
`TaskDisplayText.title(for: task)`. (Non-task digest bullets stay unchanged.)

- [ ] **Step 6: Build + run the full suite**

Run: `xcodebuild build -scheme Scout -destination 'platform=macOS'`
Then: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`
Expected: build succeeds; full suite green.

- [ ] **Step 7: Commit**

```bash
git add Scout/ActionItems/Views/BoardCardView.swift Scout/ActionItems/Views/SectionView.swift Scout/ActionItems/Views/DigestView.swift ScoutTests/ActionItems/TaskDisplayTextTests.swift
git commit -m "feat(action-items): clean titles on board/focus/done/digest + chip-coverage guard test"
```

---

## Task 6: Rewrite the engine authoring prompt (scout-plugin)

**Files:**
- Modify: `../scout-plugin/phases/core/action-items.md` (Action Items File
  Format ~48-92, Hard Rule region ~107-111)

**Interfaces:**
- No code interface. The prompt is prose the LLM follows when generating the
  daily file. No change to `parser.py`, `render.py`, `scout.ids`, or
  `parser-corpus.json`.

**Design:** Add explicit title-content constraints so the bold segment is a
short imperative phrase (no ids/status/emoji/dates/internal ` — `), route detail
to the body/sub-bullets, and forbid inline priority emoji on task lines (priority
= section placement). Ships as a separate scout-plugin PR.

- [ ] **Step 1: Add a "Task line anatomy" rule after the canonical shape (after line 111)**

Insert this block in `phases/core/action-items.md` immediately after the
"Canonical task line shape" paragraph (line 111):

```markdown
### Hard Rule — Title Is a Short Human Phrase; Detail Goes in the Body

The **bold segment is the human-readable title** and must read as a short
imperative phrase — what to do, in plain words. Keep it scannable (aim for a
single line).

The bold title MUST NOT contain any of:
- the `[#TAG]` (it sits *before* the bold, never inside it);
- Linear ids (`PROJ-1234`), GitHub refs (`#1234`, `owner/repo#1234`), or
  cross-reference hashtags (`#SHORTCODE`);
- status words ("MERGED", "DEPLOYED", "created + self-assigned", "done→todo");
- dates, times, or quoted snippets;
- emoji of any kind (including priority 🔴🟡🟢);
- an internal ` — ` / ` – ` separator (that dash separates title from body).

Everything else — status, dates, quotes, Linear/GitHub ids, wikilinks, context —
goes **after the ` — ` separator** (the body) or into `- Source:` / `- Context:`
sub-bullets. Ids you reference for linking still belong in the body/sub-bullets,
never in the title.

**Priority is expressed only by which section the item lives in** (🔴 Urgent /
🟡 To Do / 🟢 Watching). Do NOT prepend a priority emoji to a task line.

Good vs bad (anonymized):

    ✅  - [ ] [#REPLYX] **Reply to Alex about her purchase question** — She said 1/8–1/2; still open per the sweep. Loop in [[people/priya|Priya]]. [[PROJ-3026]]
    ❌  - [ ] [#REPLYX] 🟡 **Reply to Alex — purchase Q still open (Thu 4:55 PM: "1/8 to 1/2") PROJ-3026** _(carries)_ — …
```

- [ ] **Step 2: Update the file-format examples (lines 57-85) to drop inline emoji**

The canonical examples at lines 57, 63, 69, 75, 83 already use the
`**[Item title]** — [Description]` shape and no inline priority emoji — confirm
none is added. If any example title carries an emoji or an id, remove it so the
examples model the new rule.

- [ ] **Step 3: Verify the self-check grep still holds**

The `[#TAG]` self-check grep (lines 123-128) is unaffected — it matches the
leading tag, which is unchanged. Run it mentally against the good example above:
`- [ ] [#REPLYX] **…**` still contains ` [#REPLYX] ` → passes.

- [ ] **Step 4: Manual generation check**

Generate (or hand-write) one section using the new rule, then confirm the
existing parser still splits it correctly (subject = clean bold, body = post-dash
remainder) by running the plugin's parser tests — they must stay green since the
format is a valid instance of the unchanged contract:

Run (in `../scout-plugin`): `pytest engine/tests/unit/test_parser_contract.py engine/tests/unit/test_parser_corpus_checksum.py -q`
Expected: PASS (contract + checksum unchanged).

- [ ] **Step 5: Commit (in scout-plugin)**

```bash
cd ../scout-plugin
git add phases/core/action-items.md
git commit -m "feat(action-items): title is a short human phrase; detail → body; priority = section (no inline emoji)"
```

---

## Self-Review

**Spec coverage:**
- Engine prompt rewrite (short clean title, detail → body, priority = section, no
  inline emoji) → **Task 6**.
- Markdown `_italic_` fix → **Task 1**.
- Display-cleaning helper (strip emoji/ids/carry, keep names) → **Tasks 2–3**.
- Card layout = title + 2-line description + chips; hide `#TAG`; priority stripe
  only → **Task 4**.
- Ids-out-of-prose land in chips; no id vanishes without a chip → **Tasks 3 & 5**
  (`chippedLinearIDs` + guard test).
- Clean title on Board/Focus/Done/Digest → **Task 5**.
- Parser/corpus/contract unchanged + stay green → asserted in **Tasks 1, 4, 5, 6**.
- No migration (defensive cleaning covers legacy files) → inherent to the cleaner
  (Task 2 `total` tests cover plain/legacy input).

**Known limitation (documented, not a gap):** bare GitHub refs (`#7056`,
`owner/repo#7056`) are NOT detected as deep links, so they have no chip; per the
coverage guard the cleaner leaves them inline (still clickable via
`GitHubRefLinkifier`). Chip-ifying GitHub refs is a possible follow-up, out of
scope here. The engine rule (Task 6) keeps them out of the *title* regardless, so
they only ever appear in the description prose.

**Placeholder scan:** none — every code step shows complete code; every command
shows expected output.

**Type consistency:** `clean(_:linearIDs:)`, `title(for:)`, `description(for:)`,
`chippedLinearIDs(_:)`, and `attributedString(for:)` are used with identical
signatures across Tasks 1–5.

## Execution Handoff

App track = Tasks 1–5 (one scout-app PR); engine track = Task 6 (one scout-plugin
PR). Per the review-first flow, this plan + the spec are pushed to the scout-app
PR for review before any task is executed.
