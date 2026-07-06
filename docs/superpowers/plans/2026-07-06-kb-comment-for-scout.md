# KB "Comment for Scout" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user attach a comment to a block in a Knowledge Base note that the Scout dreaming session picks up on its next run, by inserting an inline `//==<< … >>==//` marker beneath that block via the existing guarded + git-committed KB writer.

**Architecture:** A new pure `ScoutMarker` type owns the marker syntax. `KBDocSegment` gains a `.scoutComment` kind so a marker line is parsed as its own block (never absorbed into an adjacent paragraph/table), plus `insertLine`/`removeLines` splicers that add or remove a whole line while leaving every other byte untouched. `KBEditableView` (the Read-mode renderer) gets a hover "💬 Comment for Scout" affordance + inline composer that splices a marker in and asks `KBEditorView` to persist immediately through its existing `save()` path; marker segments render as a "for Scout · pending" chip with a × to retract.

**Tech Stack:** Swift, SwiftUI, AppKit; Swift Testing (`import Testing`, `@Suite`/`@Test`/`#expect`); Xcode project (synchronized file groups — new `.swift` files under `Scout/` and `ScoutTests/` compile automatically, no `.pbxproj` edit).

## Global Constraints

- **Marker syntax is exactly** `//==<< <comment> >>==//` on a single line — the form the dreaming session scans (`rg -F '//==<<'`) and the Action Items parser already recognizes.
- **Byte-integrity:** only ever add or remove *whole lines*; never rewrite an existing line (distinct from `replaceLines`/`replaceCell`). The plugin's structured tokens must stay byte-identical.
- **Scope:** KB notes only. Action Items counterpart is deferred to Raven-Scout/scout-plugin#186. No new writer op — reuse `KnowledgeBaseFileWriter.save`.
- **No real identifiers in tests/fixtures** (repo `CLAUDE.md`): use neutral stand-ins (`Alex`/`Priya`/`Sam`, `atlas`).
- **Test framework:** Swift Testing only. Run the whole `ScoutTests` target for a reliable verdict; a `-only-testing:ScoutTests/<StructName>` selector may be used for speed **only** if the run reports "Executed N tests" with N > 0 (a folder-style selector like `-only-testing:ScoutTests/KnowledgeBase` silently runs zero — false green).
- **Platform:** macOS 13+; build/test destination `platform=macOS`.

---

## File Structure

- **Create** `Scout/KnowledgeBase/Models/ScoutMarker.swift` — the marker vocabulary (format / detect / extract). Pure, `nonisolated`.
- **Modify** `Scout/KnowledgeBase/Models/KBDocSegment.swift` — add `.scoutComment` kind; classify marker lines in `segments(from:)`; add `insertLine(in:afterLineEnd:line:)` and `removeLines(in:start:end:)`.
- **Modify** `Scout/KnowledgeBase/Views/KBEditableView.swift` — `.scoutComment` chip + retract, hover "💬 Comment for Scout" affordance + inline composer, `onRequestSave` callback prop.
- **Modify** `Scout/KnowledgeBase/Views/KBEditorView.swift` — pass `onRequestSave: { save() }` into `KBEditableView`.
- **Create** `ScoutTests/KnowledgeBase/ScoutMarkerTests.swift` — unit tests for `ScoutMarker`.
- **Create** `ScoutTests/KnowledgeBase/KBScoutCommentSegmentTests.swift` — unit tests for the new `KBDocSegment` parsing + splicing.

---

## Task 1: `ScoutMarker` vocabulary

**Files:**
- Create: `Scout/KnowledgeBase/Models/ScoutMarker.swift`
- Test: `ScoutTests/KnowledgeBase/ScoutMarkerTests.swift`

**Interfaces:**
- Produces:
  - `ScoutMarker.format(_ text: String) -> String?` — single-line `//==<< text >>==//`, newlines collapsed to spaces, `nil` for empty/whitespace-only.
  - `ScoutMarker.isMarkerLine(_ line: String) -> Bool` — trims internally.
  - `ScoutMarker.body(of line: String) -> String?` — comment text inside a marker line, else `nil`.
  - `ScoutMarker.open == "//==<<"`, `ScoutMarker.close == ">>==//"`.

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/KnowledgeBase/ScoutMarkerTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("ScoutMarker")
struct ScoutMarkerTests {
    @Test func formatWraps() {
        #expect(ScoutMarker.format("scope tags to people too?")
                == "//==<< scope tags to people too? >>==//")
    }
    @Test func formatCollapsesNewlinesAndTrims() {
        #expect(ScoutMarker.format("  line one \n  line two  ")
                == "//==<< line one line two >>==//")
    }
    @Test func formatRejectsEmpty() {
        #expect(ScoutMarker.format("   ") == nil)
        #expect(ScoutMarker.format("\n\n") == nil)
    }
    @Test func detectsMarkerLine() {
        #expect(ScoutMarker.isMarkerLine("//==<< hi >>==//"))
        #expect(ScoutMarker.isMarkerLine("   //==<< hi >>==//  "))   // leading/trailing space
        #expect(!ScoutMarker.isMarkerLine("a //==<< hi >>==// b"))   // not standalone
        #expect(!ScoutMarker.isMarkerLine("//==<< unterminated"))
        #expect(!ScoutMarker.isMarkerLine("plain text"))
    }
    @Test func extractsBody() {
        #expect(ScoutMarker.body(of: "//==<< scope to people? >>==//") == "scope to people?")
        #expect(ScoutMarker.body(of: "   //==<<   padded   >>==//  ") == "padded")
        #expect(ScoutMarker.body(of: "not a marker") == nil)
    }
    @Test func roundTrips() {
        let m = ScoutMarker.format("hello world")!
        #expect(ScoutMarker.isMarkerLine(m))
        #expect(ScoutMarker.body(of: m) == "hello world")
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScoutMarkerTests 2>&1 | tail -20`
Expected: compile failure — "cannot find 'ScoutMarker' in scope".

- [ ] **Step 3: Write minimal implementation**

Create `Scout/KnowledgeBase/Models/ScoutMarker.swift`:

```swift
import Foundation

/// The inline feedback marker the Scout dreaming session reads. The user leaves
/// `//==<< comment >>==//` at a spot in a note; dreaming's per-location pass
/// (`rg -F '//==<<'`) acts on it and strips it when resolved. This type is the
/// single source of truth for the syntax on the app side (the Action Items
/// markdown parser recognizes the same form).
nonisolated enum ScoutMarker {
    static let open = "//==<<"
    static let close = ">>==//"

    /// Wrap `text` as a single-line marker. Internal newlines collapse to
    /// spaces (the contract is line-oriented — every reader scans line by
    /// line). Returns `nil` for empty / whitespace-only input.
    static func format(_ text: String) -> String? {
        let collapsed = text
            .components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
        guard !collapsed.isEmpty else { return nil }
        return "\(open) \(collapsed) \(close)"
    }

    /// True if `line`, trimmed, is a standalone marker line.
    static func isMarkerLine(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        return t.hasPrefix(open) && t.hasSuffix(close) && t.count >= open.count + close.count
    }

    /// The comment text inside a marker line, or `nil` if `line` isn't one.
    static func body(of line: String) -> String? {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard isMarkerLine(t) else { return nil }
        return t.dropFirst(open.count).dropLast(close.count)
            .trimmingCharacters(in: .whitespaces)
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ScoutMarkerTests 2>&1 | tail -20`
Expected: PASS — "Executed 6 tests, with 0 failures" (confirm N = 6, not 0).

- [ ] **Step 5: Commit**

```bash
git add Scout/KnowledgeBase/Models/ScoutMarker.swift ScoutTests/KnowledgeBase/ScoutMarkerTests.swift
git commit -m "feat(kb): ScoutMarker — //==<< … >>==// vocabulary (format/detect/extract)"
```

---

## Task 2: `KBDocSegment` — `.scoutComment` kind, parser classification, line splicers

**Files:**
- Modify: `Scout/KnowledgeBase/Models/KBDocSegment.swift`
- Modify: `Scout/KnowledgeBase/Views/KBEditableView.swift:111-142` (exhaustive `rendered` switch — add a placeholder `.scoutComment` case to keep the build green; Task 3 makes it the real chip)
- Test: `ScoutTests/KnowledgeBase/KBScoutCommentSegmentTests.swift`

**Interfaces:**
- Consumes: `ScoutMarker.isMarkerLine` (Task 1).
- Produces:
  - `KBDocSegment.Kind.scoutComment` — a marker line parsed as its own single-line segment.
  - `KBDocSegment.insertLine(in source: String, afterLineEnd end: Int, line: String) -> String` — inserts `line` as a new line right after index `end` (clamped; appends at EOF), all other lines byte-identical.
  - `KBDocSegment.removeLines(in source: String, start: Int, end: Int) -> String` — deletes lines `start...end` entirely; returns `source` unchanged for an out-of-range range.

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/KnowledgeBase/KBScoutCommentSegmentTests.swift`:

```swift
import Foundation
import Testing
@testable import Scout

@Suite("KBDocSegment — Scout comment markers")
struct KBScoutCommentSegmentTests {

    // MARK: parse classification

    @Test func markerAfterParagraphParsesAsOwnSegment() {
        let src = "The vault uses tags.\n//==<< scope to people? >>==//\nMore prose."
        let segs = KBDocSegment.segments(from: src)
        #expect(segs.map(\.kind) == [.paragraph, .scoutComment, .paragraph])
        #expect(segs[1].lineStart == 1 && segs[1].lineEnd == 1)
        #expect(ScoutMarker.body(of: segs[1].raw) == "scope to people?")
    }

    @Test func markerDoesNotMergeIntoAdjacentParagraph() {
        // No blank line between prose and marker — still isolated.
        let src = "line a\nline b\n//==<< note >>==//"
        let segs = KBDocSegment.segments(from: src)
        #expect(segs.map(\.kind) == [.paragraph, .scoutComment])
        #expect(segs[0].raw == "line a\nline b")
    }

    @Test func markerAfterTableIsNotSwallowedAsARow() {
        let src = "| A | B |\n| - | - |\n| 1 | 2 |\n//==<< check this >>==//"
        let segs = KBDocSegment.segments(from: src)
        #expect(segs.map(\.kind) == [.table, .scoutComment])
    }

    @Test func markerInsideCodeFenceStaysCode() {
        let src = "```\n//==<< not a comment, it's code >>==//\n```"
        let segs = KBDocSegment.segments(from: src)
        #expect(segs.map(\.kind) == [.code])
    }

    // MARK: insertLine

    @Test func insertLineAfterBlockLeavesOtherBytesIntact() {
        let src = "alpha\nbeta\ngamma"
        // Insert after line index 1 ("beta").
        let out = KBDocSegment.insertLine(in: src, afterLineEnd: 1, line: "//==<< x >>==//")
        #expect(out == "alpha\nbeta\n//==<< x >>==//\ngamma")
    }

    @Test func insertLineAtEndAppends() {
        let src = "alpha\nbeta"
        let out = KBDocSegment.insertLine(in: src, afterLineEnd: 1, line: "//==<< x >>==//")
        #expect(out == "alpha\nbeta\n//==<< x >>==//")
    }

    @Test func insertLineClampsNegativeToPrepend() {
        let out = KBDocSegment.insertLine(in: "only", afterLineEnd: -5, line: "M")
        #expect(out == "M\nonly")
    }

    // MARK: removeLines

    @Test func removeLinesDeletesTheMarkerLine() {
        let src = "alpha\n//==<< x >>==//\nbeta"
        let out = KBDocSegment.removeLines(in: src, start: 1, end: 1)
        #expect(out == "alpha\nbeta")
    }

    @Test func removeLinesOutOfRangeIsNoOp() {
        let src = "alpha\nbeta"
        #expect(KBDocSegment.removeLines(in: src, start: 5, end: 9) == src)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBScoutCommentSegmentTests 2>&1 | tail -20`
Expected: compile failure — `.scoutComment` and `insertLine`/`removeLines` don't exist yet.

- [ ] **Step 3a: Add the `.scoutComment` kind**

In `Scout/KnowledgeBase/Models/KBDocSegment.swift`, extend the `Kind` enum (currently line 8-10):

```swift
    enum Kind: Equatable {
        case heading(Int), paragraph, list, quote, code, table, rule, frontmatter
        /// A standalone `//==<< … >>==//` Scout-feedback marker line.
        case scoutComment
    }
```

- [ ] **Step 3b: Classify marker lines in `segments(from:)`**

In `segments(from:)`, add a top-priority check right after the empty-line `continue` (currently line 47, `if t.isEmpty { i += 1; continue }`):

```swift
            // Scout feedback marker on its own line: isolate it so it never
            // merges into an adjacent paragraph and renders as a chip, not prose.
            if ScoutMarker.isMarkerLine(t) { segs.append(make(.scoutComment, i, i)); i += 1; continue }
```

In the **table row loop**, add a break so a marker after a table (or a comment whose text contains `|`) ends the table instead of being read as a row. After the empty/no-pipe break (currently line 66, `if rt.isEmpty || !rt.contains("|") { break }`):

```swift
                    if ScoutMarker.isMarkerLine(rt) { break }
```

In the **paragraph consumption loop**, add a break so a marker following prose isn't absorbed. Inside the `while j < lines.count` body (currently after line 105, `if KBMarkdownLexer.listItem(lines[j]) != nil { break }`):

```swift
                if ScoutMarker.isMarkerLine(lines[j]) { break }
```

- [ ] **Step 3c: Add the splicers**

In `KBDocSegment`, in the `// MARK: - Splicing` section (after `replaceLines`, ~line 159), add:

```swift
    /// Insert `line` as a whole new line immediately after source line index
    /// `end`, leaving every other line byte-identical. `end` is clamped: a
    /// value ≥ last index appends at EOF; a negative value prepends.
    static func insertLine(in source: String, afterLineEnd end: Int, line: String) -> String {
        var lines = source.components(separatedBy: "\n")
        let at = min(max(end, -1) + 1, lines.count)
        lines.insert(line, at: at)
        return lines.joined(separator: "\n")
    }

    /// Delete source lines `start...end` entirely. Returns `source` unchanged
    /// if the range is out of bounds or inverted.
    static func removeLines(in source: String, start: Int, end: Int) -> String {
        var lines = source.components(separatedBy: "\n")
        guard start >= 0, end < lines.count, start <= end else { return source }
        lines.removeSubrange(start...end)
        return lines.joined(separator: "\n")
    }
```

- [ ] **Step 3d: Keep the build green — placeholder render case**

Adding an enum case makes `KBEditableView.rendered`'s `switch seg.kind` (line 111-142) non-exhaustive. Add a temporary case at the end of that switch (Task 3 replaces it with the real chip):

```swift
        case .scoutComment:
            InlineMarkdownText(ScoutMarker.body(of: seg.raw) ?? seg.raw)
                .font(DS.sans(12)).foregroundStyle(DS.Accent.ink)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/KBScoutCommentSegmentTests 2>&1 | tail -20`
Expected: PASS — "Executed 9 tests, with 0 failures" (confirm N = 9).

- [ ] **Step 5: Guard against regressions in the existing segment suite**

Run the pre-existing KBDocSegment tests to confirm classification changes didn't break block parsing. Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests 2>&1 | tail -25`
Expected: PASS — the full target (confirm "Executed N tests" with the same N as before + 15 new, 0 failures).

- [ ] **Step 6: Commit**

```bash
git add Scout/KnowledgeBase/Models/KBDocSegment.swift Scout/KnowledgeBase/Views/KBEditableView.swift ScoutTests/KnowledgeBase/KBScoutCommentSegmentTests.swift
git commit -m "feat(kb): parse //==<< markers as their own segment + line splicers"
```

---

## Task 3: "Comment for Scout" affordance, chip + retract, immediate persist

**Files:**
- Modify: `Scout/KnowledgeBase/Views/KBEditableView.swift`
- Modify: `Scout/KnowledgeBase/Views/KBEditorView.swift:189`

**Interfaces:**
- Consumes: `ScoutMarker.format` (Task 1); `KBDocSegment.insertLine`, `removeLines`, `.scoutComment` (Task 2); `KnowledgeBaseFileWriter.save` via `KBEditorView.save()` (existing).
- Produces: `KBEditableView(source:onRequestSave:)` — `onRequestSave` defaults to `{}` so double-click edits are unaffected (they still rely on ⌘S); the Scout-comment insert and retract call it to persist immediately.

**No unit test** (this project has no view tests; view logic is verified by build + a manual run). The pure logic it relies on is covered by Tasks 1–2.

- [ ] **Step 1: Add the callback prop + composer state to `KBEditableView`**

In `KBEditableView` (after `@State private var cache = SegmentCache()`, line 15) add:

```swift
    /// Persist immediately after a Scout comment is added or retracted (the
    /// parent routes this to its `save()`). Defaults to no-op so ordinary
    /// double-click edits keep their edit → ⌘S flow.
    var onRequestSave: () -> Void = {}

    @State private var commentingOn: Int? = nil   // segment id the composer is open on
    @State private var commentBuffer: String = ""
    @State private var hovering: Int? = nil        // segment id currently hovered
```

- [ ] **Step 2: Route `.scoutComment` and the hover affordance in `segmentView`**

Replace `segmentView(_:)` (lines 51-66) with:

```swift
    @ViewBuilder
    private func segmentView(_ seg: KBDocSegment) -> some View {
        if case .scoutComment = seg.kind {
            scoutCommentChip(seg)
        } else if editing == seg.id {
            inlineEditor(seg)
        } else if case .table = seg.kind {
            KBEditableTableView(headers: seg.headers, rows: seg.rows, rowLines: seg.rowLines) { line, col, value in
                source = KBDocSegment.replaceCell(in: source, sourceLine: line, col: col, value: value)
            }
        } else {
            VStack(alignment: .leading, spacing: 4) {
                rendered(seg)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                    .onTapGesture(count: 2) { startEdit(seg) }
                    .help("Double-click to edit")
                if commentingOn == seg.id {
                    scoutCommentComposer(seg)
                } else if hovering == seg.id {
                    commentAffordance(seg)
                }
            }
            .onHover { hovering = $0 ? seg.id : (hovering == seg.id ? nil : hovering) }
        }
    }
```

- [ ] **Step 3: Add the affordance button, composer, chip, and actions**

In `KBEditableView`, after `commit(_:)` (line 105), add:

```swift
    // MARK: - Comment for Scout

    private func commentAffordance(_ seg: KBDocSegment) -> some View {
        Button { commentBuffer = ""; commentingOn = seg.id } label: {
            Label("Comment for Scout", systemImage: "bubble.left.and.text.bubble.right")
                .font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
        }
        .buttonStyle(.plain)
        .help("Leave a note here for Scout to act on during its next dreaming session")
    }

    private func scoutCommentComposer(_ seg: KBDocSegment) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            TextEditor(text: $commentBuffer)
                .font(DS.sans(12.5)).foregroundStyle(DS.Ink.p1)
                .scrollContentBackground(.hidden)
                .frame(minHeight: 52)
                .padding(8)
                .background(RoundedRectangle(cornerRadius: 6).fill(DS.Paper.sunk))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Accent.fill.opacity(0.6), lineWidth: 1))
            HStack(spacing: 8) {
                Text("Scout reads this on its next dreaming run")
                    .font(DS.sans(10.5)).foregroundStyle(DS.Ink.p4)
                Spacer()
                Button("Cancel") { commentingOn = nil }
                    .buttonStyle(.plain).font(DS.sans(12)).foregroundStyle(DS.Ink.p3)
                    .keyboardShortcut(.cancelAction)
                Button("Send to Scout") { submitComment(seg) }
                    .buttonStyle(.plain).font(DS.sans(12, weight: .semibold)).foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 5)
                    .background(Capsule().fill(DS.Accent.fill))
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled(commentBuffer.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(.vertical, 4)
    }

    private func scoutCommentChip(_ seg: KBDocSegment) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.system(size: 10)).foregroundStyle(DS.Accent.ink)
            Text(ScoutMarker.body(of: seg.raw) ?? seg.raw)
                .font(DS.sans(12)).foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
            Text("for Scout · pending").font(DS.sans(10)).foregroundStyle(DS.Ink.p4)
            Spacer(minLength: 8)
            Button { retractComment(seg) } label: {
                Image(systemName: "xmark.circle.fill").font(.system(size: 12)).foregroundStyle(DS.Ink.p4)
            }
            .buttonStyle(.plain).help("Retract this comment (removes the marker)")
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 6).fill(DS.Accent.wash))
    }

    private func submitComment(_ seg: KBDocSegment) {
        guard let marker = ScoutMarker.format(commentBuffer) else { return }
        source = KBDocSegment.insertLine(in: source, afterLineEnd: seg.lineEnd, line: marker)
        commentingOn = nil
        commentBuffer = ""
        onRequestSave()
    }

    private func retractComment(_ seg: KBDocSegment) {
        source = KBDocSegment.removeLines(in: source, start: seg.lineStart, end: seg.lineEnd)
        onRequestSave()
    }
```

- [ ] **Step 4: Remove the Task-2 placeholder `.scoutComment` case in `rendered`**

The chip is now rendered by `segmentView`, so the `.scoutComment` case added to `rendered(_:)` in Task 2 Step 3d is dead. Replace it with a safe no-op to keep the switch exhaustive:

```swift
        case .scoutComment:
            EmptyView()   // rendered as a chip in segmentView, never here
```

- [ ] **Step 5: Wire the parent to persist immediately**

In `KBEditorView.swift`, the `.read` case (line 189) currently reads `KBEditableView(source: $draft)`. Replace with:

```swift
            case .read:
                // Rendered, but editable in place: double-click a paragraph,
                // heading, list item or table cell to edit just that piece.
                // A "Comment for Scout" inserts a marker and saves immediately.
                KBEditableView(source: $draft, onRequestSave: { save() })
```

- [ ] **Step 6: Build**

Run: `xcodebuild -scheme Scout -configuration Debug -destination 'platform=macOS' build 2>&1 | tail -15`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 7: Run the full test target (nothing regressed)**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' 2>&1 | tail -15`
Expected: PASS — "Executed N tests, with 0 failures".

- [ ] **Step 8: Manual verification (drive the real app)**

Use the `/run` skill (or build + launch the Debug "Scout Dev"). Then:
1. Open a KB note in **Read** mode, hover a paragraph → a "Comment for Scout" button appears beneath it.
2. Click it → composer opens → type a comment → **Send to Scout** (or ⌘↵).
3. Confirm the block now shows a **"for Scout · pending"** chip with a × .
4. In a terminal, confirm the marker landed in the file and was committed:
   - `rg -n -F '//==<<' ~/Scout/knowledge-base/<that-note>.md` shows `//==<< <your comment> >>==//` on its own line right under the block.
   - `git -C ~/Scout log -1 --stat` (or the app's repo) shows the scoped `app: edit …` commit touching only that note.
5. Click the chip's × → the marker line is removed from the file and a new commit is made; the chip disappears.
6. Confirm prose byte-integrity: `git -C ~/Scout show HEAD~1 -- <note>` diff adds exactly one line (the marker) and changes nothing else.

- [ ] **Step 9: Commit**

```bash
git add Scout/KnowledgeBase/Views/KBEditableView.swift Scout/KnowledgeBase/Views/KBEditorView.swift
git commit -m "feat(kb): Comment for Scout — block affordance, pending chip, retract, immediate save"
```

---

## Self-Review

**1. Spec coverage** (against `2026-07-06-kb-comment-for-scout-design.md`):
- `ScoutMarker` single source of truth → Task 1. ✓
- Block-granularity marker insertion under the block via existing writer → Task 3 (`insertLine` + `save()`). ✓
- Byte-integrity (only whole lines added/removed) → Tasks 2–3 (`insertLine`/`removeLines`, never rewrite a line); verified in Task 3 Step 8.6. ✓
- Marker parsed distinctly (not absorbed) incl. code-fence/table edge cases → Task 2 parse tests. ✓
- Pending chip + retract → Task 3. ✓
- Reuse `KnowledgeBaseFileWriter.save`, conflict/commit-failure handling → Task 3 routes through `KBEditorView.save()` (unchanged) → existing `.conflict`/`.commitFailed` alerts apply. ✓
- Tests mirror existing style, no real identifiers → Tasks 1–2 (`atlas`/neutral prose). ✓
- Deferred: Action Items (scout-plugin#186), features 2/3 — explicitly out of scope, no task. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to" — every code step has complete code. ✓

**3. Type consistency:** `insertLine(in:afterLineEnd:line:)`, `removeLines(in:start:end:)`, `.scoutComment`, `onRequestSave`, `ScoutMarker.format/isMarkerLine/body` — names identical across the tasks that define and consume them. ✓

## Notes for the implementer

- **Immediate-save semantics:** `submitComment`/`retractComment` mutate the `source` binding then call `onRequestSave()` → `KBEditorView.save()`, which saves the whole `draft`. If the user had other unsaved in-place edits, those are saved too — intended (the marker insertion is itself an edit). The existing `isDirty` guard means `save()` is a no-op only if nothing changed, which can't happen right after an insert/remove.
- **Conflict path is free:** because persistence reuses `save()`, a concurrent plugin/Obsidian write surfaces the existing "File changed on disk" alert; a commit failure surfaces the existing "written but uncommitted" message. No new error handling needed.
