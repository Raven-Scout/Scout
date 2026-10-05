# Action Items Task Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach every indented line under an action item to that item as
`details`, show it on the card, and include it in every prompt the app builds,
so items whose context lives in sub-bullets stop showing as bare titles.

**Architecture:** A new `TaskDetail` model and a required `ActionTask.details`
field. The parser keeps a "detail window" open from a task line until the next
non-indented line, and appends indented bullets and continuation lines to the
owning task. `summary`, search, `ClaudeLauncher` and `TaskCardView` read the
new field. `body` and the shared parser contract stay as they are.

**Tech Stack:** Swift 6, SwiftUI (macOS), Swift Testing, `xcodebuild`.

**Spec:** `docs/superpowers/specs/2026-10-04-action-items-task-details-design.md`

## Global Constraints

- `ActionTask.body` must not change for any input. `ParserContractTests` and the
  byte-identical `ScoutTests/Fixtures/parser-corpus.json` are untouched. Do not
  edit the corpus or `canonicalSHA256`.
- `details` is a **required** `ActionTask.init` parameter with **no default**.
- Comment classification is unchanged. `  - Word: text` stays a comment, even
  for `Links`, `Source` and `Context`.
- Fixtures are anonymized per `CLAUDE.md`: people `Alex` / `Priya` / `Sam`,
  `PROJ-1234`, `example-org/<repo>`. Use only the tags `DETX`, `NESTX`, `PLN`,
  `LBL`, `IOTA` (checked against the vault 2026-10-04: zero hits for the first
  four; `IOTA` is an existing corpus tag). **Do not use `REPLYX`**, which
  appears in the vault.
- Fixture sub-bullets must not start with `<word>:`, because that shape parses
  as a comment. Write "Waiting on Priya", not "Waiting: Priya".
- New `.swift` files under `Scout/` and `ScoutTests/` compile automatically
  (synchronized groups). Do not edit `project.pbxproj`.
- Test files need explicit imports (`MemberImportVisibility` is on): `Testing`,
  `Foundation`, `@testable import Scout`, plus `SwiftUI` / `AppKit` where a
  view is used.
- Run tests with a **type name** selector. A folder selector such as
  `-only-testing:ScoutTests/ActionItems` runs zero tests and reports green.
  Each full run writes about 200 MB of xcresult, so pass
  `-resultBundlePath "$TMPDIR/xcr-$RANDOM"` and delete the bundle afterwards.

## Review Focus

1. **A rebuild path dropping `details`.** A task with details followed by a
   Refs, snooze, dash-comment, inline-comment or quote-comment line must keep
   every detail. Covered by
   `TaskDetailsParserTests.rebuildingSubLinesKeepDetails` (Task 2).
2. **Context leaking across a boundary.** An indented line after a top-level
   bullet, an HTML comment, a `</details>` tag or a new `##` section must not
   attach to the task above it. Covered by `windowClosesOnTopLevelBullet`,
   `windowClosesOnHTMLComment`, `windowDoesNotCrossSections` and
   `parkedTaskKeepsItsOwnDetails` (Task 2).
3. **Code fences inside a sub-bullet.** Shell commands with `\` continuations,
   relative indentation and a `- ` line inside the fence must come through
   verbatim and in one detail. Covered by `fenceIsVerbatimContinuation`
   (Task 2).
4. **Old one-line items stay the same.** An item with a ` — ` body and no
   sub-bullets must give the same card teaser and the same prompt as before.
   Covered by `ParserContractTests` (unchanged) plus
   `fullContextUnchangedWithoutDetails` (Task 4).
5. **Very long prompts on the Claude Desktop path.** A 40,000-character
   prompt full of emoji must still build a `claude://` URL. Covered by
   `ClaudeDesktopURLTests.buildsURLForVeryLongPrompt` (Task 4). Desktop's own
   limit is checked by hand in Task 6.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `Scout/ActionItems/Models/TaskDetail.swift` | Create | The `TaskDetail` value type |
| `Scout/ActionItems/Models/ActionTask.swift` | Modify | `details` field, `summary`, `replacingDetails`, `matchesSearch` |
| `Scout/ActionItems/ActionItemsParser.swift` | Modify | The detail window; pass `details` through every rebuild |
| `Scout/ActionItems/ActionItemsView.swift` | Modify | Search delegates to `ActionTask.matchesSearch` |
| `Scout/Utilities/ClaudeLauncher.swift` | Modify | `Context:` block, `summary` in concise, nested details in checklist |
| `Scout/ActionItems/Views/TaskDetailsView.swift` | Create | Renders a task's details as a muted, indented bullet list |
| `Scout/ActionItems/Views/TaskCardView.swift` | Modify | Teaser and nested row use `summary`; expanded detail shows `TaskDetailsView` |
| `ScoutTests/ActionItems/TaskDetailModelTests.swift` | Create | `summary`, `replacingDetails`, `matchesSearch` |
| `ScoutTests/ActionItems/TaskDetailsParserTests.swift` | Create | Parser behaviour |
| `ScoutTests/ActionItems/ClaudeLauncherPromptTests.swift` | Modify | Prompt formats with details |
| `ScoutTests/ActionItems/ClaudeDesktopURLTests.swift` | Modify | Long-prompt URL |
| `ScoutTests/Shell/ComponentSmokeTests.swift` | Modify | Card with details and an empty body; `TaskDetailsView` render |
| `ScoutTests/ActionItems/{TaskChipTests,MatchableSubjectTests,ActionBoardColumnTests}.swift`, `ScoutTests/Shell/AppStateFireNowTests.swift` | Modify | Pass `details:` at construction sites |

---

### Task 1: `TaskDetail` model and the required `details` field

**Files:**
- Create: `Scout/ActionItems/Models/TaskDetail.swift`
- Modify: `Scout/ActionItems/Models/ActionTask.swift`
- Modify: `Scout/ActionItems/ActionItemsParser.swift` (the six `ActionTask(` sites, around lines 582, 610, 645, 679, 710 and 736)
- Modify: the six test files that build `ActionTask` (listed in File Structure)
- Test: `ScoutTests/ActionItems/TaskDetailModelTests.swift`

**Interfaces:**
- Produces: `struct TaskDetail { let depth: Int; let text: String }`
  (`nonisolated`, `Equatable, Hashable, Sendable`, memberwise
  `init(depth:text:)`); `ActionTask.details: [TaskDetail]` (required init
  parameter placed after `deepLinks:`); `ActionTask.summary: String`;
  `ActionTask.replacingDetails(_ details: [TaskDetail]) -> ActionTask`;
  `ActionTask.matchesSearch(_ lowercasedNeedle: String) -> Bool`.

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/ActionItems/TaskDetailModelTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("Task details — model")
struct TaskDetailModelTests {
    private func task(body: String = "", details: [TaskDetail] = [],
                      comments: [TaskComment] = []) -> ActionTask {
        ActionTask(
            id: UUID(), lineNumber: 1, done: false,
            subject: "**Ship the fix**", plainSubject: "Ship the fix",
            body: body, comments: comments, deepLinks: [], details: details,
            snoozedUntil: nil, carriedInFrom: nil
        )
    }

    @Test func summaryPrefersBody() {
        let t = task(body: "CI is green.", details: [TaskDetail(depth: 0, text: "Waiting on Priya")])
        #expect(t.summary == "CI is green.")
    }

    @Test func summaryFallsBackToFirstLineOfFirstDetail() {
        let t = task(details: [
            TaskDetail(depth: 0, text: "Waiting on Priya\n```\nmake test\n```"),
            TaskDetail(depth: 0, text: "Second point"),
        ])
        #expect(t.summary == "Waiting on Priya")
    }

    @Test func summaryEmptyWhenNothingToShow() {
        #expect(task().summary == "")
    }

    @Test func replacingDetailsKeepsEveryOtherField() {
        let original = ActionTask(
            id: UUID(), lineNumber: 7, done: true,
            subject: "**S**", plainSubject: "S", body: "b",
            comments: [TaskComment(author: "alex", timestamp: "", text: "hi")],
            deepLinks: [.linear(id: "PROJ-1234")],
            details: [TaskDetail(depth: 0, text: "old")],
            snoozedUntil: Date(timeIntervalSince1970: 1_000),
            carriedInFrom: Date(timeIntervalSince1970: 2_000),
            indentLevel: 1, shortPrefix: "DETX", snoozedFromKind: .urgent
        )
        let replaced = original.replacingDetails([TaskDetail(depth: 1, text: "new")])
        #expect(replaced.details == [TaskDetail(depth: 1, text: "new")])
        #expect(replaced == ActionTask(
            id: original.id, lineNumber: 7, done: true,
            subject: "**S**", plainSubject: "S", body: "b",
            comments: original.comments, deepLinks: original.deepLinks,
            details: [TaskDetail(depth: 1, text: "new")],
            snoozedUntil: original.snoozedUntil, carriedInFrom: original.carriedInFrom,
            indentLevel: 1, shortPrefix: "DETX", snoozedFromKind: .urgent
        ))
    }

    @Test func searchMatchesTextThatOnlyAppearsInADetail() {
        let t = task(details: [TaskDetail(depth: 0, text: "Blocked on the tracing job")])
        #expect(t.matchesSearch("tracing"))
        #expect(!t.matchesSearch("billing"))
    }

    @Test func searchStillMatchesSubjectBodyAndComments() {
        let t = task(body: "CI is green.",
                     comments: [TaskComment(author: "sam", timestamp: "", text: "ping Alex")])
        #expect(t.matchesSearch("ship"))
        #expect(t.matchesSearch("green"))
        #expect(t.matchesSearch("ping alex"))
        #expect(t.matchesSearch(""))
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/TaskDetailModelTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "error:|TEST (SUCCEEDED|FAILED)" | head -20`
Expected: build fails with `cannot find 'TaskDetail' in scope`.

- [ ] **Step 3: Create the model**

Create `Scout/ActionItems/Models/TaskDetail.swift`:

```swift
import Foundation

/// One indented line of context under an action item: a sub-bullet, or a run
/// of continuation lines that belong to the sub-bullet above it.
///
/// The engine's own parser keeps these as `ActionItem.details`
/// (`engine/scout/action_items/parser.py`). Before this type the app sent
/// them to the section's prose, where no card showed them, so an item written
/// as a bold title plus sub-bullets rendered as a bare title.
nonisolated struct TaskDetail: Equatable, Hashable, Sendable {
    /// Nesting below the owning task: 0 = direct sub-bullet, 1 = one level
    /// deeper, and so on.
    let depth: Int
    /// Raw markdown without the bullet marker. Continuation lines are joined
    /// with "\n", with the bullet's own indent removed so code inside a fence
    /// keeps its relative indentation.
    let text: String
}
```

- [ ] **Step 4: Add the field, `summary`, `replacingDetails` and `matchesSearch`**

In `Scout/ActionItems/Models/ActionTask.swift`, add the stored property after
`deepLinks`:

```swift
    let deepLinks: [TaskDeepLink]
    /// Indented sub-bullets and continuation lines under the task line, in
    /// source order. Excludes the sub-lines the parser already turns into
    /// comments, refs or snooze metadata.
    let details: [TaskDetail]
```

Change the init signature and body. `details` has **no default**:

```swift
        deepLinks: [TaskDeepLink],
        details: [TaskDetail],
        snoozedUntil: Date?,
```

```swift
        self.deepLinks = deepLinks
        self.details = details
```

Add below the init:

```swift
    /// What a one-line surface (the collapsed card, a nested row, a concise
    /// prompt) shows under the title: the body when the task line has one,
    /// otherwise the first line of the first detail.
    var summary: String {
        if !body.isEmpty { return body }
        guard let first = details.first else { return "" }
        return first.text
            .split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: false)
            .first.map(String.init) ?? ""
    }

    /// This task with `details` replaced and every other field kept. The
    /// parser's detail branch rebuilds through here, so it can't drop a field.
    func replacingDetails(_ details: [TaskDetail]) -> ActionTask {
        ActionTask(
            id: id,
            lineNumber: lineNumber,
            done: done,
            subject: subject,
            plainSubject: plainSubject,
            body: body,
            comments: comments,
            deepLinks: deepLinks,
            details: details,
            snoozedUntil: snoozedUntil,
            carriedInFrom: carriedInFrom,
            indentLevel: indentLevel,
            shortPrefix: shortPrefix,
            snoozedFromKind: snoozedFromKind
        )
    }

    /// Search filter. `lowercasedNeedle` must already be lowercased; an empty
    /// needle matches everything.
    func matchesSearch(_ lowercasedNeedle: String) -> Bool {
        guard !lowercasedNeedle.isEmpty else { return true }
        return plainSubject.lowercased().contains(lowercasedNeedle)
            || body.lowercased().contains(lowercasedNeedle)
            || comments.contains { $0.text.lowercased().contains(lowercasedNeedle) }
            || details.contains { $0.text.lowercased().contains(lowercasedNeedle) }
    }
```

- [ ] **Step 5: Fix every construction site the compiler now flags**

In `ActionItemsParser.parse`, the task-line site (`currentTasks.append(ActionTask(`) passes:

```swift
                    deepLinks: deepLinks,
                    details: [],
```

Each of the five rebuild sites (quote comment, snooze, Refs, dash comment,
inline comment) passes the owner's details through:

```swift
                    deepLinks: last.deepLinks,   // or `merged` at the Refs site
                    details: last.details,
```

In the test files, add `details: []` after `deepLinks:` at each
`ActionTask(` call in `TaskChipTests`, `MatchableSubjectTests`,
`ActionBoardColumnTests` and `AppStateFireNowTests`. In
`ClaudeLauncherPromptTests.makeTask` and `SmokeFixtures.task`, add a
`details: [TaskDetail] = []` parameter and pass it through (test helpers may
default; the production init may not).

Build and confirm nothing is left:

Run: `xcodebuild build-for-testing -scheme Scout -destination 'platform=macOS' 2>&1 | grep -E "error:|BUILD (SUCCEEDED|FAILED)"`
Expected: `** TEST BUILD SUCCEEDED **` with no `error:` lines.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/TaskDetailModelTests -only-testing:ScoutTests/ParserContractTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "error:|✘|TEST (SUCCEEDED|FAILED)"`
Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 7: Commit**

```bash
git add Scout/ActionItems/Models/TaskDetail.swift Scout/ActionItems/Models/ActionTask.swift Scout/ActionItems/ActionItemsParser.swift ScoutTests
git commit -m "feat(action-items): TaskDetail model + required ActionTask.details"
```

---

### Task 2: Parser — attach indented lines to the owning task

**Files:**
- Modify: `Scout/ActionItems/ActionItemsParser.swift`
- Test: `ScoutTests/ActionItems/TaskDetailsParserTests.swift`

**Interfaces:**
- Consumes: `TaskDetail(depth:text:)`, `ActionTask.replacingDetails(_:)`,
  `ActionTask.details` (Task 1); the existing `indentLevelFor(_:)`.
- Produces: parsed `ActionTask.details` populated per the spec's
  *Parser: the detail window* rules. No public signature changes.

- [ ] **Step 1: Write the failing tests**

Create `ScoutTests/ActionItems/TaskDetailsParserTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("Task details — parser")
struct TaskDetailsParserTests {
    private func parse(_ lines: [String]) throws -> ActionItemsDocument {
        let text = (["# Action Items — 2026-07-20", "", "## 🔴 Urgent", ""] + lines)
            .joined(separator: "\n")
        let url = URL(fileURLWithPath: "/tmp/action-items-2026-07-20.md")
        return try ActionItemsParser.parse(text: text, sourceURL: url, sourceBytes: text.utf8.count)
    }

    private func urgent(_ lines: [String]) throws -> ActionSection {
        let doc = try parse(lines)
        return try #require(doc.sections.first { $0.kind == .urgent })
    }

    private func onlyTask(_ lines: [String]) throws -> ActionTask {
        let section = try urgent(lines)
        return try #require(section.tasks.first)
    }

    private func d(_ depth: Int, _ text: String) -> TaskDetail {
        TaskDetail(depth: depth, text: text)
    }

    @Test func subBulletsAttachInOrder() throws {
        let section = try urgent([
            "- [ ] [#DETX] **Order the roadmap items before Tuesday — you are the decider**",
            "  - 🗓️ Due Tuesday and it has never left Todo.",
            "  - 🔑 The other three wait on the customer; this one does not.",
            "  - 📦 The candidate items are already listed in PROJ-1234.",
        ])
        let task = try #require(section.tasks.first)
        #expect(task.body == "")
        #expect(task.details == [
            d(0, "🗓️ Due Tuesday and it has never left Todo."),
            d(0, "🔑 The other three wait on the customer; this one does not."),
            d(0, "📦 The candidate items are already listed in PROJ-1234."),
        ])
        #expect(section.bullets.isEmpty)
    }

    @Test func bodyAndDetailsCoexist() throws {
        let task = try onlyTask([
            "- [ ] [#PLN] **Ship the fix** — CI is green.",
            "  - Waiting on a second review from Priya.",
        ])
        #expect(task.body == "CI is green.")
        #expect(task.details == [d(0, "Waiting on a second review from Priya.")])
    }

    @Test func nestedBulletsCarryDepth() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Plan the cycle**",
            "  - outer point",
            "    - inner point",
            "      - innermost point",
            "  - back out",
        ])
        #expect(task.details == [
            d(0, "outer point"), d(1, "inner point"), d(2, "innermost point"), d(0, "back out"),
        ])
    }

    @Test func starAndPlusBulletsAreDetails() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Plan the cycle**",
            "  * star point",
            "  + plus point",
        ])
        #expect(task.details == [d(0, "star point"), d(0, "plus point")])
    }

    @Test func nestedChildTaskOwnsItsDetails() throws {
        let section = try urgent([
            "- [ ] [#NESTX] **Parent item**",
            "  - parent context",
            "  - [ ] [#LBL] **Child item**",
            "    - child context",
        ])
        #expect(section.tasks.count == 2)
        #expect(section.tasks[0].details == [d(0, "parent context")])
        #expect(section.tasks[1].indentLevel == 1)
        #expect(section.tasks[1].details == [d(0, "child context")])
    }

    @Test func rebuildingSubLinesKeepDetails() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  - first context",
            "  - Refs: [[people/alex]] · #XREF",
            "  - snoozed-until: 2026-07-25",
            "  - alex: a dash comment",
            "  //==<< an inline note >>==//",
            "  - second context",
            "  > priya (2026-07-20 10:00 AM ET): a quoted comment",
        ])
        #expect(task.details == [d(0, "first context"), d(0, "second context")])
        #expect(task.comments.map(\.author) == ["alex", "user", "priya"])
        #expect(task.deepLinks.contains(.entity(path: "people/alex", label: nil)))
        #expect(task.snoozedUntil != nil)
    }

    @Test func windowClosesOnTopLevelBullet() throws {
        let section = try urgent([
            "- [ ] [#DETX] **Do the thing**",
            "  - task context",
            "- section prose",
            "  - indented after prose",
        ])
        #expect(section.tasks[0].details == [d(0, "task context")])
        #expect(section.bullets == ["section prose", "indented after prose"])
    }

    @Test func windowClosesOnHTMLComment() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  - task context",
            "<!-- moved from yesterday",
            "     still inside the comment -->",
        ])
        #expect(task.details == [d(0, "task context")])
    }

    @Test func blankLinesKeepWindowOpen() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  - first",
            "",
            "  - second",
        ])
        #expect(task.details == [d(0, "first"), d(0, "second")])
    }

    @Test func continuationLinesJoinTheirBullet() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  - wrapped first line",
            "    second line of the same bullet",
        ])
        #expect(task.details == [d(0, "wrapped first line\nsecond line of the same bullet")])
    }

    @Test func continuationWithNoBulletStartsADetail() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Do the thing**",
            "  plain indented text",
        ])
        #expect(task.details == [d(0, "plain indented text")])
    }

    @Test func fenceIsVerbatimContinuation() throws {
        let task = try onlyTask([
            "- [ ] [#DETX] **Make the demo cert**",
            "  - Run this",
            "    ```",
            "    openssl req -x509 \\",
            "      -keyout demo.key",
            "    - not a bullet",
            "    | not | a table |",
            "    ```",
            "  - after the fence",
        ])
        #expect(task.details == [
            d(0, "Run this\n```\nopenssl req -x509 \\\n  -keyout demo.key\n- not a bullet\n| not | a table |\n```"),
            d(0, "after the fence"),
        ])
    }

    @Test func parkedTaskKeepsItsOwnDetails() throws {
        let section = try urgent([
            "<details><summary>Superseded — yesterday</summary>",
            "",
            "- [ ] [#PLN] **Old item**",
            "  - old context",
            "",
            "</details>",
            "  - indented after the archive",
            "- [ ] [#DETX] **Live item**",
        ])
        let group = try #require(section.collapsed.first)
        #expect(group.tasks.first?.details == [d(0, "old context")])
        let live = try #require(section.tasks.first)
        #expect(live.details.isEmpty)
    }

    @Test func windowDoesNotCrossSections() throws {
        let doc = try parse([
            "- [ ] [#DETX] **Do the thing**",
            "  - task context",
            "## 🟡 To Do",
            "  - stray indented line",
        ])
        let urgent = try #require(doc.sections.first { $0.kind == .urgent })
        #expect(urgent.tasks[0].details == [d(0, "task context")])
        let todo = try #require(doc.sections.first { $0.kind == .todo })
        #expect(todo.bullets == ["stray indented line"])
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/TaskDetailsParserTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "✘|TEST (SUCCEEDED|FAILED)" | head -30`
Expected: `** TEST FAILED **`. Every test fails, because the parser still
passes `details: []` everywhere and sends the indented lines to
`section.bullets`.

- [ ] **Step 3: Add the window state and helpers**

In `parse(...)`, declare the state next to the other accumulators (before
`func flushTable()`, so the nested functions can capture it):

```swift
        // --- detail window ---
        // Open from a task line until the next non-indented, non-blank line.
        // While open, indented lines the sub-line branches don't claim are the
        // task's context (`ActionTask.details`), not section prose.
        var detailWindowOpen = false
        var inDetailFence = false
        /// Leading-whitespace width to strip from a continuation line: the
        /// last detail bullet's indent + 2 (`- `).
        var detailContentIndent = 0

        func closeDetailWindow() {
            detailWindowOpen = false
            inDetailFence = false
            detailContentIndent = 0
        }
```

Call `closeDetailWindow()` as the first line of `closeCollapsedGroup()` and of
`flushSection()`.

Add the regex next to `bulletRe`:

```swift
        /// An indented `-`/`*`/`+` bullet under a task — one detail.
        let detailBulletRe = try NSRegularExpression(pattern: #"^(\s+)[-*+]\s+(.+?)\s*$"#)
```

Add these helpers in the `// --- helpers ---` area:

```swift
    /// A markdown fence marker line (already whitespace-trimmed).
    private static func isFenceLine(_ stripped: String) -> Bool {
        stripped.hasPrefix("```") || stripped.hasPrefix("~~~")
    }

    /// Drop at most `n` leading spaces/tabs, so a continuation keeps any
    /// indentation beyond its bullet's content column.
    private static func dropLeadingWhitespace(_ s: String, upTo n: Int) -> String {
        var idx = s.startIndex
        var dropped = 0
        while idx < s.endIndex, dropped < n, s[idx] == " " || s[idx] == "\t" {
            idx = s.index(after: idx)
            dropped += 1
        }
        return String(s[idx...])
    }

    /// Append `line` to the last detail as a continuation, or start a depth-0
    /// detail when the task has none yet.
    private static func appendingContinuation(
        _ line: String, to details: [TaskDetail], contentIndent: Int
    ) -> [TaskDetail] {
        var out = details
        if let last = out.popLast() {
            let piece = dropLeadingWhitespace(line, upTo: contentIndent)
            out.append(TaskDetail(depth: last.depth, text: last.text + "\n" + piece))
        } else {
            out.append(TaskDetail(depth: 0, text: line.trimmingCharacters(in: .whitespaces)))
        }
        return out
    }
```

- [ ] **Step 4: Close the window on non-indented lines and pass fences through**

In the main `while` loop, immediately after
`let stripped = line.trimmingCharacters(in: .whitespaces)`, insert:

```swift
            let isIndented = line.first == " " || line.first == "\t"

            // A non-blank line at column 0 ends the task's sub-list: a new
            // task, a top-level bullet, a paragraph, an HTML comment, `---`,
            // a table, a `<details>` tag or a heading.
            if detailWindowOpen, !stripped.isEmpty, !isIndented {
                closeDetailWindow()
            }

            // Inside a fence that opened in a detail, every line is verbatim
            // continuation — even ones shaped like bullets, comments or table
            // rows. Must run before every other branch for that reason.
            if detailWindowOpen, inDetailFence, let owner = currentTasks.last {
                if Self.isFenceLine(stripped) { inDetailFence = false }
                currentTasks[currentTasks.count - 1] = owner.replacingDetails(
                    Self.appendingContinuation(line, to: owner.details, contentIndent: detailContentIndent)
                )
                i += 1; continue
            }
```

(`parse` is a `static func`; if `Self.` does not resolve in this extension,
call the helpers unqualified, as the existing code does with
`extractShortPrefix`.)

- [ ] **Step 5: Open the window on a task line**

In the task-line branch, just before its `i += 1; continue`, add:

```swift
                detailWindowOpen = true
                inDetailFence = false
                detailContentIndent = 0
```

- [ ] **Step 6: Add the detail branch**

Insert immediately **before** the `// Bullet (section-level)` branch:

```swift
            // Task context: an indented line under the open task that none of
            // the sub-line branches above claimed (Refs, snooze, comments).
            // Before this, these lines fell through to section prose, which no
            // card renders — so an item written as a title plus sub-bullets
            // showed as a bare title. Mirrors the engine parser's `details`.
            if inSection, detailWindowOpen, isIndented, !stripped.isEmpty,
               let owner = currentTasks.last {
                let ns = line as NSString
                let range = NSRange(location: 0, length: ns.length)
                var details = owner.details
                if Self.isFenceLine(stripped) {
                    inDetailFence = true
                    details = Self.appendingContinuation(line, to: details, contentIndent: detailContentIndent)
                } else if let bm = detailBulletRe.firstMatch(in: line, range: range) {
                    let indent = ns.substring(with: bm.range(at: 1))
                    let depth = max(0, indentLevelFor(indent) - owner.indentLevel - 1)
                    details.append(TaskDetail(depth: depth, text: ns.substring(with: bm.range(at: 2))))
                    detailContentIndent = indent.count + 2
                } else {
                    details = Self.appendingContinuation(line, to: details, contentIndent: detailContentIndent)
                    if owner.details.isEmpty {
                        detailContentIndent = line.prefix { $0 == " " || $0 == "\t" }.count
                    }
                }
                currentTasks[currentTasks.count - 1] = owner.replacingDetails(details)
                i += 1; continue
            }
```

- [ ] **Step 7: Run the new tests, the contract and the existing parser suites**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/TaskDetailsParserTests -only-testing:ScoutTests/ParserContractTests -only-testing:ScoutTests/ActionItemsParserTests -only-testing:ScoutTests/RefsBlockTests -only-testing:ScoutTests/CollapsedDetailsTests -only-testing:ScoutTests/ParseSkipAndCancellationTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "✘|TEST (SUCCEEDED|FAILED)"`
Expected: `** TEST SUCCEEDED **`. If an existing suite asserts that an indented
non-comment line lands in `section.bullets` under a task, read the assertion.
That expectation is the bug this plan fixes. Update it to assert the line is
in `task.details`, and say so in the commit message.

- [ ] **Step 8: Commit**

```bash
git add Scout/ActionItems/ActionItemsParser.swift ScoutTests/ActionItems/TaskDetailsParserTests.swift
git commit -m "fix(action-items): attach indented sub-bullets to their task as details"
```

---

### Task 3: Search matches details

**Files:**
- Modify: `Scout/ActionItems/ActionItemsView.swift:637-653` (`filtered(_:)`)

**Interfaces:**
- Consumes: `ActionTask.matchesSearch(_:)` (Task 1, already tested).

- [ ] **Step 1: Replace the inline matcher**

In `filtered(_ section:)`, replace the three-line `return t.plainSubject…`
expression and its `guard !needle.isEmpty` with a call to the model method,
so the view and its test share one definition:

```swift
            guard statusOK else { return false }
            return t.matchesSearch(needle)
```

- [ ] **Step 2: Build and run the model tests**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/TaskDetailModelTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "error:|✘|TEST (SUCCEEDED|FAILED)"`
Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 3: Commit**

```bash
git add Scout/ActionItems/ActionItemsView.swift
git commit -m "fix(action-items): search matches text in a task's details"
```

---

### Task 4: Prompts include details

**Files:**
- Modify: `Scout/Utilities/ClaudeLauncher.swift:153-192`
- Test: `ScoutTests/ActionItems/ClaudeLauncherPromptTests.swift`, `ScoutTests/ActionItems/ClaudeDesktopURLTests.swift`

**Interfaces:**
- Consumes: `ActionTask.details`, `ActionTask.summary` (Task 1).
- Produces: `static func detailLines(_ details: [TaskDetail], baseIndent: String = "") -> [String]` on `ClaudeLauncher` (internal, for tests).

- [ ] **Step 1: Write the failing tests**

Append to `ClaudeLauncherPromptTests` (`makeTask` already takes `details:`
from Task 1):

```swift
    @Test func fullContextIncludesDetailsBeforeCommentsAndLinks() throws {
        let task = makeTask(
            plainSubject: "Order the roadmap items",
            comments: [TaskComment(author: "alex", timestamp: "", text: "Agreed.")],
            deepLinks: [.linear(id: "PROJ-1234")],
            details: [
                TaskDetail(depth: 0, text: "Due Tuesday; never left Todo."),
                TaskDetail(depth: 1, text: "The customer opens this tracker on Tuesday."),
                TaskDetail(depth: 0, text: "Run this\n```\nmake test\n```"),
            ]
        )
        let out = ClaudeLauncher.prompt(for: task)
        #expect(out.contains("""
        Order the roadmap items

        Context:
        - Due Tuesday; never left Todo.
          - The customer opens this tracker on Tuesday.
        - Run this
          ```
          make test
          ```
        """))
        let context = try #require(out.range(of: "Context:"))
        let comments = try #require(out.range(of: "Prior comments:"))
        let links = try #require(out.range(of: "Links:"))
        #expect(context.lowerBound < comments.lowerBound)
        #expect(comments.lowerBound < links.lowerBound)
    }

    @Test func fullContextUnchangedWithoutDetails() {
        let task = makeTask(plainSubject: "Cut release", body: "Tag by EOD.")
        #expect(ClaudeLauncher.prompt(for: task) == """
        Help me make progress on this action item:

        Cut release

        Tag by EOD.
        """)
    }

    @Test func conciseFallsBackToFirstDetail() {
        let task = makeTask(
            plainSubject: "Order the roadmap items",
            details: [TaskDetail(depth: 0, text: "Due Tuesday."), TaskDetail(depth: 0, text: "More.")]
        )
        #expect(ClaudeLauncher.prompt(for: task, format: .concise) == "Order the roadmap items\nDue Tuesday.")
    }

    @Test func checklistNestsDetailsUnderTheItem() {
        let task = makeTask(
            plainSubject: "Order the roadmap items",
            details: [TaskDetail(depth: 0, text: "Due Tuesday."), TaskDetail(depth: 1, text: "Customer review.")]
        )
        #expect(ClaudeLauncher.prompt(for: task, format: .markdownChecklist) == """
        - [ ] Order the roadmap items
          - Due Tuesday.
            - Customer review.
        """)
    }
```

Append to `ClaudeDesktopURLTests`:

```swift
    @Test func buildsURLForVeryLongPrompt() throws {
        // 30 characters per repetition × 1,400 = 42,000.
        let prompt = String(repeating: "🗓️ Due Tuesday — context line.\n", count: 1_400)
        #expect(prompt.count > 40_000)
        let url = try #require(ClaudeLauncher.makeDesktopURL(
            prompt: prompt, mode: .code(folder: URL(fileURLWithPath: "/tmp/scout"))))
        #expect(url.scheme == "claude")
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ClaudeLauncherPromptTests -only-testing:ScoutTests/ClaudeDesktopURLTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "✘|TEST (SUCCEEDED|FAILED)"`
Expected: `fullContextIncludesDetailsBeforeCommentsAndLinks`,
`conciseFallsBackToFirstDetail` and `checklistNestsDetailsUnderTheItem` fail.
`fullContextUnchangedWithoutDetails` and `buildsURLForVeryLongPrompt` pass
already; they guard existing behaviour.

- [ ] **Step 3: Implement**

In `ClaudeLauncher`, add:

```swift
    /// A task's details as a markdown list: two spaces per depth level, and
    /// continuation lines (wrapped text, fenced code) indented under their
    /// bullet.
    static func detailLines(_ details: [TaskDetail], baseIndent: String = "") -> [String] {
        details.flatMap { detail -> [String] in
            let pad = baseIndent + String(repeating: "  ", count: detail.depth)
            let parts = detail.text.components(separatedBy: "\n")
            return ["\(pad)- \(parts[0])"] + parts.dropFirst().map { "\(pad)  \($0)" }
        }
    }
```

In `fullContextBody`, after the body block:

```swift
        if !task.details.isEmpty {
            out += "\n\nContext:\n" + detailLines(task.details).joined(separator: "\n")
        }
```

Replace `conciseBody`:

```swift
    private static func conciseBody(for task: ActionTask) -> String {
        let summary = task.summary
        guard !summary.isEmpty else { return subjectLine(for: task) }
        return "\(subjectLine(for: task))\n\(summary)"
    }
```

In `checklistBody`, after the body lines:

```swift
        lines.append(contentsOf: detailLines(task.details, baseIndent: "  "))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: the Step 2 command.
Expected: `** TEST SUCCEEDED **`. The existing
`conciseIncludesOnlySubjectAndBody` must still pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add Scout/Utilities/ClaudeLauncher.swift ScoutTests/ActionItems/ClaudeLauncherPromptTests.swift ScoutTests/ActionItems/ClaudeDesktopURLTests.swift
git commit -m "feat(launcher): include a task's details in Launch Claude and copy prompts"
```

---

### Task 5: Card shows the summary and the details

**Files:**
- Create: `Scout/ActionItems/Views/TaskDetailsView.swift`
- Modify: `Scout/ActionItems/Views/TaskCardView.swift:120-129` (teaser), `:302-306` (expanded detail), `:382-388` (nested row)
- Test: `ScoutTests/Shell/ComponentSmokeTests.swift`

**Interfaces:**
- Consumes: `ActionTask.summary`, `ActionTask.details`, `TaskDetail` (Task 1).
- Produces: `struct TaskDetailsView: View { let details: [TaskDetail] }`.

- [ ] **Step 1: Write the failing smoke tests**

In `ComponentSmokeTests.taskCardRendersEveryState`, add two variants to
`variants`:

```swift
            SmokeFixtures.task(body: "", details: SmokeFixtures.details),
            SmokeFixtures.task(details: SmokeFixtures.details),
```

Add to `SmokeFixtures`:

```swift
    static let details: [TaskDetail] = [
        TaskDetail(depth: 0, text: "🗓️ Due Tuesday; **never** left Todo — see [[projects/the-demo]]."),
        TaskDetail(depth: 1, text: "Priya opens this tracker on Tuesday."),
        TaskDetail(depth: 0, text: "Run this\n```\nscoutctl digest --batch\n```"),
    ]
```

Add a test next to the other action-item row tests:

```swift
    @Test("task details render at every depth")
    func taskDetailsRender() {
        ViewHost.render(TaskDetailsView(details: SmokeFixtures.details), size: cardSize)
        ViewHost.render(TaskDetailsView(details: []), size: cardSize)
    }
```

- [ ] **Step 2: Run to verify it fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ComponentSmokeTests -resultBundlePath "$TMPDIR/xcr-$RANDOM" 2>&1 | grep -E "error:|TEST (SUCCEEDED|FAILED)"`
Expected: build fails with `cannot find 'TaskDetailsView' in scope`.

- [ ] **Step 3: Create the view**

Create `Scout/ActionItems/Views/TaskDetailsView.swift`:

```swift
import SwiftUI

/// A task's indented sub-bullets, in source order, as a muted bullet list —
/// the context an item written as a title plus sub-bullets carries.
struct TaskDetailsView: View {
    let details: [TaskDetail]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(details.enumerated()), id: \.offset) { _, detail in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text("•")
                        .font(DS.serif(13))
                        .foregroundStyle(DS.Ink.p4)
                    InlineMarkdownText(detail.text)
                        .font(DS.serif(13))
                        .foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.leading, CGFloat(detail.depth) * 14)
            }
        }
    }
}
```

- [ ] **Step 4: Wire it into the card**

In `TaskCardView.header`, change the teaser to read `summary`. Bind it once,
as `chips` is bound, so it is computed once per body pass:

```swift
        let chips = self.chips
        let summary = task.summary
```

```swift
            if !expanded && !summary.isEmpty {
                InlineMarkdownText(summary)
```

In `detail`, after the `TaskBodyView` block:

```swift
            if !task.details.isEmpty {
                TaskDetailsView(details: task.details)
            }
```

In `nestedRow`, replace `task.body` with `task.summary` in both the `if` and
the `InlineMarkdownText(...)`.

- [ ] **Step 5: Run the smoke tests**

Run: the Step 2 command, then also `-only-testing:ScoutTests/LeafSmokeTests`.
Expected: `** TEST SUCCEEDED **`.

- [ ] **Step 6: Commit**

```bash
git add Scout/ActionItems/Views/TaskDetailsView.swift Scout/ActionItems/Views/TaskCardView.swift ScoutTests/Shell/ComponentSmokeTests.swift
git commit -m "feat(action-items): card shows a task's details and a summary line"
```

---

### Task 6: Full verification against the real vault, then the PR

**Files:** none changed unless a check fails.

- [ ] **Step 1: Full test target**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests -resultBundlePath "$TMPDIR/xcr-full" 2>&1 | grep -E "✘|TEST (SUCCEEDED|FAILED)" ; rm -rf "$TMPDIR/xcr-full"`
Expected: `** TEST SUCCEEDED **`. Known load-flaky FSEvents tests
(`watcherCoalescesAppendBursts`, the three liveness tests) may fail under
load. Re-run that suite alone before treating it as a real failure.

- [ ] **Step 2: Perf harness, before and after**

Run `PerfHarnessTests` on `origin/main` and on this branch, and record the
parse time for the populated-day fixture. Expected: within run-to-run noise.
A regression of more than 10% needs a look before the PR.

- [ ] **Step 3: Check the real file by hand (read-only)**

Build and launch the Debug app (the `run` skill or Xcode), select Oct 4, 2026.
Check:
1. Three 🔴 items written with sub-bullets show a teaser line when collapsed
   and a bullet list when expanded.
2. One old item with a ` — ` body looks the same as on `main`.
3. **Copy → Full context** on a sub-bullet item gives a `Context:` list. Paste
   it into a scratch file and compare it against the source lines in the vault
   file.
4. **Launch Claude** (Desktop, Code tab) on the item with the most
   sub-bullets. Record whether the full prompt arrives. If it is truncated,
   record the length at which it cut off and add it to the PR as a follow-up.
   Do not change the launch path in this PR.
5. Search for a word that appears only in one item's sub-bullets: the item
   stays visible.

Do not write to the vault during this check. Use read-only actions only, with
no mark-done, snooze or comment.

- [ ] **Step 4: Open the PR**

Push the branch and open the implementation PR against `main` (or push to the
spec/plan PR, whichever the review asked for). The body lists the before and
after count of items with an empty card on the real file (counts only), the
Desktop-length result, and the three follow-ups from the spec.
