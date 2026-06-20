# Scout.app Wishlist & Research Tabs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two Scout.app sidebar tabs — Wishlist and Research — that list the per-file items the plugin writes (`docs/wishlist/`, `knowledge-base/research-queue/`), let the user add new items, and mark active items done/dropped.

**Architecture:** A shared generic `Scout/PerFileItems/` module (model, parser, FSEvents document service, writer actor, views) parameterized by a `PerFileTabConfig`. Wishlist and Research are two config values, not new Swift types. Built as a generalized near-copy of the existing Proposals feature; **Proposals itself is not modified**.

**Tech Stack:** Swift / SwiftUI (macOS app), Swift Testing (`import Testing`, `@Test`/`#expect`), the existing `FileSystemEventSource` + `GitServiceProtocol` + `ProcessRunner` seams.

## Global Constraints

- **Do NOT modify the existing `Scout/Proposals/` feature** or its tests. The new code is self-contained under `Scout/PerFileItems/`.
- **Per-file schema is the contract** (must match what the plugin migration/sessions write): frontmatter `title`, `status` ∈ `open|in-progress|done|dropped`, `priority` ∈ `urgent|high|medium|low`, `date` (`YYYY-MM-DD`), optional `source` (wishlist) / `area` (research); body below a `# <title>` H1 that is stripped on render.
- **Active vs resolved:** `status` `open`/`in-progress` = active (Awaiting); `done`/`dropped` = resolved.
- **Pure types are `nonisolated`**; the document service is `@MainActor final class … ObservableObject`; the writer is an `actor` (serialized via a `tail` chain) wrapped in a plain `ObservableObject` box for `@EnvironmentObject`.
- **Git commits are scoped to the single file and best-effort** (`try? await gitService?.commitPaths(...)`) — a missing/failed git never fails the file write.
- **Reuse, don't re-create:** `InlineMarkdownText` (`Scout/ActionItems/Views/InlineMarkdownText.swift`), the `DS.*` design tokens, the `FileSystemEventSource`/`GitServiceProtocol`/`ProcessRunner` protocols, and the `ScriptedRunner` test double (`ScoutTests/Services/GitServiceCommitPathsTests.swift`).
- **No pbxproj edits:** new `.swift` files under `Scout/` and `ScoutTests/` auto-compile (synchronized groups).
- **Tests:** Swift Testing. Verify with `xcodebuild`, not the IDE (SourceKit shows false "Cannot find type"/"No such module 'Testing'"). Never run a bare-folder `-only-testing:` selector (runs 0 tests = false green) — use a real `@Suite`/test id or the whole `ScoutTests` target.
- **Vault paths:** `scoutDir` is `~/Scout` (hardcoded in `AppState`). Wishlist dir default `~/Scout/docs/wishlist` (override key `wishlistPath`); Research dir default `~/Scout/knowledge-base/research-queue` (override key `researchQueuePath`). Path overrides take effect on next launch (read once in `AppState.init`).

---

## File Structure

**Create (production) — `Scout/PerFileItems/`:**
- `Models/ItemStatus.swift` — status enum (open/in-progress/done/dropped).
- `Models/ItemPriority.swift` — priority enum (urgent/high/medium/low), Comparable.
- `Models/MarkdownBodyBlock.swift` — prose/code body blocks (verbatim copy of `ProposalBodyBlock`).
- `Models/PerFileItem.swift` — the item struct.
- `PerFileItemParser.swift` — pure: `contents -> PerFileItem?`.
- `PerFileTabConfig.swift` — per-tab knobs + `.wishlist`/`.research`.
- `PerFileItemWriter.swift` — actor (`addItem` + `resolve`) + pure helpers + `PerFileItemWriterBox`.
- `PerFileDocumentService.swift` — `@MainActor` FSEvents list service.
- `Views/MarkdownBodyView.swift`, `Views/ItemStatusPill.swift`, `Views/ItemPriorityPill.swift`, `Views/AddItemSheet.swift`, `Views/PerFileItemCardView.swift`, `Views/PerFileListView.swift`.

**Create (tests) — `ScoutTests/PerFile/`:**
- `ItemStatusPriorityTests.swift`, `MarkdownBodyBlockTests.swift`, `PerFileItemParserTests.swift`, `PerFileTabConfigTests.swift`, `PerFileItemWriterTests.swift`, `PerFileDocumentServiceTests.swift`.

**Modify:**
- `Scout/Shell/MainWindowView.swift` — `SidebarItem` cases + detail switch + badges.
- `Scout/Shell/SidebarView.swift` — two rows + badge params.
- `Scout/Shell/AppState.swift` — two document services + one shared writer/box + path resolution + launch load.
- `Scout/Shell/SettingsView.swift` — two path-override fields.

---

## Task 1: ItemStatus + ItemPriority enums

**Files:**
- Create: `Scout/PerFileItems/Models/ItemStatus.swift`
- Create: `Scout/PerFileItems/Models/ItemPriority.swift`
- Test: `ScoutTests/PerFile/ItemStatusPriorityTests.swift`

**Interfaces:**
- Produces: `ItemStatus` (`.open`, `.inProgress`, `.done`, `.dropped`, `.unknown(String)`; `static func parse(_:) -> ItemStatus`; `var isActive: Bool`; `var displayName: String`; `var frontmatterValue: String`). `ItemPriority` (`.urgent`, `.high`, `.medium`, `.low`; `String` raw; `Comparable`; `CaseIterable`; `static func parse(_:) -> ItemPriority`; `var displayName: String`).

- [ ] **Step 1: Write the failing test**

```swift
// ScoutTests/PerFile/ItemStatusPriorityTests.swift
import Testing
@testable import Scout

@Suite("ItemStatus & ItemPriority")
struct ItemStatusPriorityTests {
    @Test func statusParsesKnownAndDefaultsAndUnknown() {
        #expect(ItemStatus.parse("open") == .open)
        #expect(ItemStatus.parse("in-progress") == .inProgress)
        #expect(ItemStatus.parse("in progress") == .inProgress)
        #expect(ItemStatus.parse("done") == .done)
        #expect(ItemStatus.parse("dropped") == .dropped)
        #expect(ItemStatus.parse("") == .open)              // missing -> open
        #expect(ItemStatus.parse("weird") == .unknown("weird"))
    }
    @Test func statusActiveSplit() {
        #expect(ItemStatus.open.isActive)
        #expect(ItemStatus.inProgress.isActive)
        #expect(!ItemStatus.done.isActive)
        #expect(!ItemStatus.dropped.isActive)
        #expect(!ItemStatus.unknown("x").isActive)
    }
    @Test func statusFrontmatterValue() {
        #expect(ItemStatus.open.frontmatterValue == "open")
        #expect(ItemStatus.inProgress.frontmatterValue == "in-progress")
        #expect(ItemStatus.done.frontmatterValue == "done")
        #expect(ItemStatus.dropped.frontmatterValue == "dropped")
    }
    @Test func priorityParsesAndDefaultsMedium() {
        #expect(ItemPriority.parse("urgent") == .urgent)
        #expect(ItemPriority.parse("high") == .high)
        #expect(ItemPriority.parse("low") == .low)
        #expect(ItemPriority.parse("medium") == .medium)
        #expect(ItemPriority.parse("") == .medium)          // missing -> medium
        #expect(ItemPriority.parse("bogus") == .medium)     // unknown -> medium
    }
    @Test func priorityOrderingUrgentHighest() {
        #expect(ItemPriority.urgent < ItemPriority.high)
        #expect(ItemPriority.high < ItemPriority.medium)
        #expect(ItemPriority.medium < ItemPriority.low)
        #expect([ItemPriority.low, .urgent, .medium, .high].sorted() == [.urgent, .high, .medium, .low])
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/ItemStatusPriorityTests 2>&1 | tail -20`
Expected: FAIL — `Cannot find 'ItemStatus' in scope` (types not defined).

- [ ] **Step 3: Write minimal implementation**

```swift
// Scout/PerFileItems/Models/ItemStatus.swift
import Foundation

/// Lifecycle of a per-file Wishlist/Research item (frontmatter `status:`).
nonisolated enum ItemStatus: Equatable, Sendable {
    case open, inProgress, done, dropped, unknown(String)

    static func parse(_ raw: String) -> ItemStatus {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        switch trimmed.lowercased() {
        case "open", "": return .open
        case "in-progress", "in progress", "inprogress": return .inProgress
        case "done": return .done
        case "dropped": return .dropped
        default: return .unknown(trimmed)
        }
    }

    /// open/in-progress are active (Awaiting); done/dropped/unknown are resolved.
    var isActive: Bool {
        switch self {
        case .open, .inProgress: return true
        case .done, .dropped, .unknown: return false
        }
    }

    var displayName: String {
        switch self {
        case .open: return "Open"
        case .inProgress: return "In Progress"
        case .done: return "Done"
        case .dropped: return "Dropped"
        case .unknown(let raw): return raw
        }
    }

    /// The exact value written back into frontmatter.
    var frontmatterValue: String {
        switch self {
        case .open: return "open"
        case .inProgress: return "in-progress"
        case .done: return "done"
        case .dropped: return "dropped"
        case .unknown(let raw): return raw
        }
    }
}
```

```swift
// Scout/PerFileItems/Models/ItemPriority.swift
import Foundation

/// Priority of a per-file item. Wishlist uses high/medium/low; Research adds urgent.
nonisolated enum ItemPriority: String, Equatable, Sendable, Comparable, CaseIterable {
    case urgent, high, medium, low

    static func parse(_ raw: String) -> ItemPriority {
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "urgent": return .urgent
        case "high": return .high
        case "low": return .low
        default: return .medium   // "medium", missing, or unrecognized
        }
    }

    private var rank: Int {
        switch self { case .urgent: return 0; case .high: return 1; case .medium: return 2; case .low: return 3 }
    }
    static func < (lhs: ItemPriority, rhs: ItemPriority) -> Bool { lhs.rank < rhs.rank }

    var displayName: String { rawValue.capitalized }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/ItemStatusPriorityTests 2>&1 | tail -20`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/Models/ItemStatus.swift Scout/PerFileItems/Models/ItemPriority.swift ScoutTests/PerFile/ItemStatusPriorityTests.swift
git commit -m "feat(perfile): ItemStatus + ItemPriority enums"
```

---

## Task 2: MarkdownBodyBlock (copy of ProposalBodyBlock)

**Files:**
- Create: `Scout/PerFileItems/Models/MarkdownBodyBlock.swift`
- Test: `ScoutTests/PerFile/MarkdownBodyBlockTests.swift`

**Interfaces:**
- Produces: `MarkdownBodyBlock` enum (`.prose(String)`, `.code(language: String?, code: String)`, `Identifiable`/`Equatable`/`Sendable`; `static func blocks(from rawBody: String) -> [MarkdownBodyBlock]`).

- [ ] **Step 1: Write the failing test**

```swift
// ScoutTests/PerFile/MarkdownBodyBlockTests.swift
import Testing
@testable import Scout

@Suite("MarkdownBodyBlock")
struct MarkdownBodyBlockTests {
    @Test func splitsProseAndCode() {
        let body = "First para.\n\nSecond para.\n\n```swift\nlet x = 1\n```"
        let blocks = MarkdownBodyBlock.blocks(from: body)
        #expect(blocks == [
            .prose("First para."),
            .prose("Second para."),
            .code(language: "swift", code: "let x = 1"),
        ])
    }
    @Test func proseOnly() {
        #expect(MarkdownBodyBlock.blocks(from: "just text") == [.prose("just text")])
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/MarkdownBodyBlockTests 2>&1 | tail -20`
Expected: FAIL — `Cannot find 'MarkdownBodyBlock' in scope`.

- [ ] **Step 3: Write minimal implementation**

Copy `Scout/Proposals/Models/ProposalBodyBlock.swift` **verbatim** to `Scout/PerFileItems/Models/MarkdownBodyBlock.swift`, then rename: the enum `ProposalBodyBlock` → `MarkdownBodyBlock`, and the two `id` string prefixes `"p:"`/`"c:"` may stay as-is (they're only used for `Identifiable`). The file body (the `blocks(from:)` and `paragraphs(in:)` logic) is generic and copied unchanged. The complete expected content:

```swift
import Foundation

/// A structural block of a per-file item body: prose paragraphs and fenced code.
nonisolated enum MarkdownBodyBlock: Equatable, Sendable, Identifiable {
    case prose(String)
    case code(language: String?, code: String)

    var id: String {
        switch self {
        case .prose(let t):          return "p:\(t)"
        case .code(let lang, let c): return "c:\(lang ?? ""):\(c)"
        }
    }

    static func blocks(from rawBody: String) -> [MarkdownBodyBlock] {
        let lines = rawBody.components(separatedBy: "\n")
        var blocks: [MarkdownBodyBlock] = []
        var proseBuffer: [String] = []
        var codeBuffer: [String] = []
        var codeLanguage: String?
        var inCode = false

        func flushProse() {
            let joined = proseBuffer.joined(separator: "\n")
            for para in paragraphs(in: joined) { blocks.append(.prose(para)) }
            proseBuffer.removeAll(keepingCapacity: true)
        }

        for line in lines {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("```") {
                if inCode {
                    blocks.append(.code(language: codeLanguage, code: codeBuffer.joined(separator: "\n")))
                    codeBuffer.removeAll(keepingCapacity: true)
                    codeLanguage = nil
                    inCode = false
                } else {
                    flushProse()
                    let lang = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    codeLanguage = lang.isEmpty ? nil : lang
                    inCode = true
                }
                continue
            }
            if inCode { codeBuffer.append(line) } else { proseBuffer.append(line) }
        }

        if inCode { blocks.append(.code(language: codeLanguage, code: codeBuffer.joined(separator: "\n"))) }
        flushProse()
        return blocks
    }

    private static func paragraphs(in text: String) -> [String] {
        text.components(separatedBy: "\n")
            .reduce(into: [[String]]()) { acc, line in
                if line.trimmingCharacters(in: .whitespaces).isEmpty {
                    if acc.last?.isEmpty == false { acc.append([]) }
                } else {
                    if acc.isEmpty { acc.append([]) }
                    acc[acc.count - 1].append(line)
                }
            }
            .map { $0.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/MarkdownBodyBlockTests 2>&1 | tail -20`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/Models/MarkdownBodyBlock.swift ScoutTests/PerFile/MarkdownBodyBlockTests.swift
git commit -m "feat(perfile): MarkdownBodyBlock body parser"
```

---

## Task 3: PerFileItem model

**Files:**
- Create: `Scout/PerFileItems/Models/PerFileItem.swift`

**Interfaces:**
- Consumes: `ItemStatus`, `ItemPriority` (Task 1), `MarkdownBodyBlock` (Task 2).
- Produces: `PerFileItem` struct: `let fileURL: URL`, `date: String`, `title: String`, `status: ItemStatus`, `priority: ItemPriority`, `source: String?`, `area: String?`, `bodyMarkdown: String`; `var id: String`, `var isActive: Bool`, `var bodyBlocks: [MarkdownBodyBlock]`.

- [ ] **Step 1: Write the implementation** (model is a plain value type verified by the parser/writer tests that consume it; no standalone test)

```swift
// Scout/PerFileItems/Models/PerFileItem.swift
import Foundation

/// One per-file Wishlist/Research item: YAML frontmatter + markdown body.
nonisolated struct PerFileItem: Identifiable, Equatable, Sendable {
    let fileURL: URL          // stable identity + the file the writer rewrites
    let date: String          // frontmatter date: or filename YYYY-MM-DD prefix
    let title: String         // frontmatter title: or filename stem
    let status: ItemStatus
    let priority: ItemPriority
    let source: String?       // wishlist provenance (optional)
    let area: String?         // research grouping (optional)
    let bodyMarkdown: String

    var id: String { fileURL.path }
    var isActive: Bool { status.isActive }
    var bodyBlocks: [MarkdownBodyBlock] { MarkdownBodyBlock.blocks(from: bodyMarkdown) }
}
```

- [ ] **Step 2: Build to verify it compiles**

Run: `cd ~/scout-app && xcodebuild build -scheme Scout 2>&1 | tail -5`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/Models/PerFileItem.swift
git commit -m "feat(perfile): PerFileItem model"
```

---

## Task 4: PerFileItemParser (pure)

**Files:**
- Create: `Scout/PerFileItems/PerFileItemParser.swift`
- Test: `ScoutTests/PerFile/PerFileItemParserTests.swift`

**Interfaces:**
- Consumes: `PerFileItem` (Task 3), `ItemStatus`/`ItemPriority` (Task 1).
- Produces: `nonisolated enum PerFileItemParser` with `static func parseFile(contents: String, fileURL: URL) -> PerFileItem?` (+ static helpers `splitFrontmatter`, `parseFrontmatterFields`, `stripLeadingHeading`, `datePrefix`).

- [ ] **Step 1: Write the failing test**

```swift
// ScoutTests/PerFile/PerFileItemParserTests.swift
import Foundation
import Testing
@testable import Scout

@Suite("PerFileItemParser")
struct PerFileItemParserTests {
    private func url(_ name: String) -> URL { URL(fileURLWithPath: "/tmp/\(name)") }

    @Test func parsesFullFrontmatterWishlist() throws {
        let text = """
        ---
        title: "Upgrade the graph system"
        status: in-progress
        priority: high
        date: 2026-06-12
        source: "Jordan Slack DM"
        ---

        # Upgrade the graph system

        Evaluate TinkerPop + Gremlin.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("2026-06-12-graph.md")))
        #expect(item.title == "Upgrade the graph system")
        #expect(item.status == .inProgress)
        #expect(item.priority == .high)
        #expect(item.date == "2026-06-12")
        #expect(item.source == "Jordan Slack DM")
        #expect(item.area == nil)
        #expect(item.bodyMarkdown == "Evaluate TinkerPop + Gremlin.")   // H1 stripped
    }

    @Test func parsesResearchAreaAndUrgent() throws {
        let text = """
        ---
        title: Graph upgrade
        status: open
        priority: urgent
        date: 2026-06-10
        area: knowledge-graph
        ---

        Body.
        """
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("x.md")))
        #expect(item.priority == .urgent)
        #expect(item.area == "knowledge-graph")
        #expect(item.source == nil)
    }

    @Test func defaultsAndFilenameDateFallback() throws {
        let text = "---\ntitle: No date here\n---\n\nbody"
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: url("2026-06-16-no-date-here.md")))
        #expect(item.status == .open)        // missing -> open
        #expect(item.priority == .medium)    // missing -> medium
        #expect(item.date == "2026-06-16")   // from filename prefix
    }

    @Test func returnsNilWhenNoFrontmatter() {
        #expect(PerFileItemParser.parseFile(contents: "# Just a heading\n\ntext", fileURL: url("a.md")) == nil)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/PerFileItemParserTests 2>&1 | tail -20`
Expected: FAIL — `Cannot find 'PerFileItemParser' in scope`.

- [ ] **Step 3: Write minimal implementation**

The frontmatter primitives are identical to `Scout/Proposals/ProposalsParser.swift` (`splitFrontmatter`, `parseFrontmatterFields`, `stripLeadingHeading`, `datePrefix`, the private `String.nonEmpty`). Reproduce them here in the new enum, and write `parseFile` to build a `PerFileItem` (extracting `priority`, `source`, `area` in addition to title/date/status):

```swift
// Scout/PerFileItems/PerFileItemParser.swift
import Foundation

/// Pure parser for one per-file item (YAML frontmatter + markdown body).
/// Returns nil when the file has no frontmatter (skips index/non-item files).
nonisolated enum PerFileItemParser {
    static func parseFile(contents: String, fileURL: URL) -> PerFileItem? {
        guard let (frontmatter, body) = splitFrontmatter(contents) else { return nil }
        let fields = parseFrontmatterFields(frontmatter)
        let stem = fileURL.deletingPathExtension().lastPathComponent

        let date = fields["date"]?.nonEmpty ?? datePrefix(of: stem) ?? ""
        let title = fields["title"]?.nonEmpty ?? stem
        let status = ItemStatus.parse(fields["status"] ?? "")
        let priority = ItemPriority.parse(fields["priority"] ?? "")
        let source = fields["source"]?.nonEmpty
        let area = fields["area"]?.nonEmpty
        let cleanBody = stripLeadingHeading(body).trimmingCharacters(in: .whitespacesAndNewlines)

        return PerFileItem(fileURL: fileURL, date: date, title: title, status: status,
                           priority: priority, source: source, area: area, bodyMarkdown: cleanBody)
    }

    static func splitFrontmatter(_ text: String) -> (frontmatter: String, body: String)? {
        let lines = text.components(separatedBy: "\n")
        guard let first = lines.first,
              first.trimmingCharacters(in: .whitespaces) == "---" else { return nil }
        var frontmatter: [String] = []
        var i = 1
        while i < lines.count {
            if lines[i].trimmingCharacters(in: .whitespaces) == "---" {
                let body = i + 1 < lines.count ? lines[(i + 1)...].joined(separator: "\n") : ""
                return (frontmatter.joined(separator: "\n"), body)
            }
            frontmatter.append(lines[i])
            i += 1
        }
        return nil
    }

    static func parseFrontmatterFields(_ frontmatter: String) -> [String: String] {
        var out: [String: String] = [:]
        for line in frontmatter.components(separatedBy: "\n") {
            guard let colon = line.firstIndex(of: ":") else { continue }
            let key = line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            guard !key.isEmpty else { continue }
            var value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            if value.count >= 2, value.hasPrefix("\""), value.hasSuffix("\"") {
                value = String(value.dropFirst().dropLast())
            }
            out[key] = value
        }
        return out
    }

    static func stripLeadingHeading(_ body: String) -> String {
        var lines = body.components(separatedBy: "\n")
        while let first = lines.first, first.trimmingCharacters(in: .whitespaces).isEmpty {
            lines.removeFirst()
        }
        if let first = lines.first, first.hasPrefix("# "), !first.hasPrefix("## ") {
            lines.removeFirst()
        }
        return lines.joined(separator: "\n")
    }

    static func datePrefix(of stem: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: #"^\d{4}-\d{2}-\d{2}"#) else { return nil }
        let ns = stem as NSString
        guard let m = re.firstMatch(in: stem, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range)
    }
}

private extension String {
    var nonEmpty: String? {
        trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/PerFileItemParserTests 2>&1 | tail -20`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/PerFileItemParser.swift ScoutTests/PerFile/PerFileItemParserTests.swift
git commit -m "feat(perfile): PerFileItemParser"
```

---

## Task 5: PerFileTabConfig

**Files:**
- Create: `Scout/PerFileItems/PerFileTabConfig.swift`
- Test: `ScoutTests/PerFile/PerFileTabConfigTests.swift`

**Interfaces:**
- Consumes: `ItemPriority` (Task 1).
- Produces: `struct PerFileTabConfig` with `title`, `priorities: [ItemPriority]`, `defaultPriority: ItemPriority`, `optionalField: OptionalField`, `addNoun: String`, `directoryDefaultRelative: String`, `pathOverrideKey: String`; nested `enum OptionalField { case none; case source(label: String); case area(label: String); var label: String? }`; static `.wishlist` and `.research`.

- [ ] **Step 1: Write the failing test**

```swift
// ScoutTests/PerFile/PerFileTabConfigTests.swift
import Testing
@testable import Scout

@Suite("PerFileTabConfig")
struct PerFileTabConfigTests {
    @Test func wishlistConfig() {
        let c = PerFileTabConfig.wishlist
        #expect(c.title == "Wishlist")
        #expect(c.priorities == [.high, .medium, .low])    // no urgent
        #expect(c.optionalField == .source(label: "Source"))
        #expect(c.directoryDefaultRelative == "docs/wishlist")
        #expect(c.pathOverrideKey == "wishlistPath")
    }
    @Test func researchConfig() {
        let c = PerFileTabConfig.research
        #expect(c.priorities == [.urgent, .high, .medium, .low])
        #expect(c.optionalField == .area(label: "Area"))
        #expect(c.directoryDefaultRelative == "knowledge-base/research-queue")
        #expect(c.pathOverrideKey == "researchQueuePath")
        #expect(c.optionalField.label == "Area")
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/PerFileTabConfigTests 2>&1 | tail -20`
Expected: FAIL — `Cannot find 'PerFileTabConfig' in scope`.

- [ ] **Step 3: Write minimal implementation**

```swift
// Scout/PerFileItems/PerFileTabConfig.swift
import Foundation

/// Per-tab knobs that parameterize the shared per-file UI/writer.
struct PerFileTabConfig: Sendable, Equatable {
    enum OptionalField: Sendable, Equatable {
        case none
        case source(label: String)
        case area(label: String)
        var label: String? {
            switch self {
            case .none: return nil
            case .source(let l), .area(let l): return l
            }
        }
    }

    let title: String
    let priorities: [ItemPriority]
    let defaultPriority: ItemPriority
    let optionalField: OptionalField
    let addNoun: String                  // commit message noun, e.g. "wishlist item"
    let directoryDefaultRelative: String // relative to scoutDir
    let pathOverrideKey: String          // UserDefaults override key

    static let wishlist = PerFileTabConfig(
        title: "Wishlist",
        priorities: [.high, .medium, .low],
        defaultPriority: .medium,
        optionalField: .source(label: "Source"),
        addNoun: "wishlist item",
        directoryDefaultRelative: "docs/wishlist",
        pathOverrideKey: "wishlistPath"
    )

    static let research = PerFileTabConfig(
        title: "Research",
        priorities: [.urgent, .high, .medium, .low],
        defaultPriority: .medium,
        optionalField: .area(label: "Area"),
        addNoun: "research topic",
        directoryDefaultRelative: "knowledge-base/research-queue",
        pathOverrideKey: "researchQueuePath"
    )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/PerFileTabConfigTests 2>&1 | tail -20`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/PerFileTabConfig.swift ScoutTests/PerFile/PerFileTabConfigTests.swift
git commit -m "feat(perfile): PerFileTabConfig (.wishlist/.research)"
```

---

## Task 6: PerFileItemWriter (actor) — add + resolve

**Files:**
- Create: `Scout/PerFileItems/PerFileItemWriter.swift`
- Test: `ScoutTests/PerFile/PerFileItemWriterTests.swift`

**Interfaces:**
- Consumes: `ItemStatus`/`ItemPriority` (Task 1), `PerFileItemParser` (Task 4, for the round-trip test), `GitServiceProtocol`/`GitService` (`Scout/Services/GitService.swift`), `ScriptedRunner` (`ScoutTests/Services/GitServiceCommitPathsTests.swift`).
- Produces: `actor PerFileItemWriter` (`init(scoutDirectory:gitService:now:)`; `func addItem(title:priority:body:source:area:in:noun:) async throws -> URL`; `func resolve(_:fileURL:label:) async throws`); pure statics `renderItemFile(...)`, `slugify(_:)`, `uniqueURL(in:date:slug:)`, `rewriteFrontmatterStatus(text:newStatusValue:file:)`; `enum ItemResolution { case done, dropped }`; `enum PerFileItemWriterError`; `final class PerFileItemWriterBox: ObservableObject`.

- [ ] **Step 1: Write the failing tests**

```swift
// ScoutTests/PerFile/PerFileItemWriterTests.swift
import Foundation
import Testing
@testable import Scout

@Suite("PerFileItemWriter pure helpers")
struct PerFileItemWriterPureTests {
    @Test func slugifyBasic() {
        #expect(PerFileItemWriter.slugify("Upgrade the Graph System!") == "upgrade-the-graph-system")
        #expect(PerFileItemWriter.slugify("G6 · CEE conference entities") == "g6-cee-conference-entities")
    }
    @Test func slugifyTruncatesToEightWords() {
        #expect(PerFileItemWriter.slugify("one two three four five six seven eight nine ten") == "one-two-three-four-five-six-seven-eight")
    }
    @Test func renderEmitsQuotedFrontmatterAndStrippableBody() throws {
        let text = PerFileItemWriter.renderItemFile(title: "Build a config: store", status: .open,
            priority: .high, date: "2026-06-19", source: "Jordan DM", area: nil, body: "The body.")
        #expect(text.hasPrefix("---\n"))
        #expect(text.contains("title: \"Build a config: store\""))   // colon -> quoted
        #expect(text.contains("status: open"))
        #expect(text.contains("priority: high"))
        #expect(text.contains("date: 2026-06-19"))
        #expect(text.contains("source: \"Jordan DM\""))
        #expect(!text.contains("area:"))
        #expect(text.contains("\n# Build a config: store\n"))
        // round-trips through the parser
        let item = try #require(PerFileItemParser.parseFile(contents: text, fileURL: URL(fileURLWithPath: "/tmp/x.md")))
        #expect(item.title == "Build a config: store" && item.status == .open && item.priority == .high && item.source == "Jordan DM")
    }
    @Test func renderResearchAreaNoSource() {
        let text = PerFileItemWriter.renderItemFile(title: "T", status: .open, priority: .urgent,
            date: "2026-06-19", source: nil, area: "kg", body: "b")
        #expect(text.contains("area: \"kg\""))
        #expect(!text.contains("source:"))
    }
    @Test func rewriteStatusPreservesRest() throws {
        let text = "---\ntitle: X\nstatus: open\npriority: high\n---\n\n# X\nbody"
        let updated = try PerFileItemWriter.rewriteFrontmatterStatus(text: text, newStatusValue: "done", file: "x.md")
        #expect(updated.contains("status: done"))
        #expect(updated.contains("priority: high"))
        #expect(updated.contains("# X\nbody"))
    }
}

@Suite("PerFileItemWriter end-to-end (file + git)")
struct PerFileItemWriterE2ETests {
    private static func fixedDate() -> Date {
        var c = DateComponents(); c.year = 2026; c.month = 6; c.day = 19; c.hour = 12
        c.timeZone = TimeZone(identifier: "UTC")
        return Calendar(identifier: .gregorian).date(from: c)!
    }
    private func makeVault() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("perfile-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir.appendingPathComponent("docs/wishlist"), withIntermediateDirectories: true)
        return dir
    }
    private func okRunner() -> ScriptedRunner {  // rev-parse(0) add(0) diff(1=dirty) commit(0)
        ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(), stderr: Data()),
        ])
    }

    @Test func addItemWritesFileAndCommitsScoped() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("docs/wishlist")
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        let url = try await writer.addItem(title: "Alpha thing", priority: .high, body: "do alpha",
                                           source: "Jordan DM", area: nil, in: dir, noun: "wishlist item")
        #expect(url.lastPathComponent == "2026-06-19-alpha-thing.md")
        let written = try String(contentsOf: url, encoding: .utf8)
        #expect(written.contains("status: open") && written.contains("priority: high") && written.contains("source: \"Jordan DM\""))
        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("commit"))
        #expect(commit.arguments.contains("app: add wishlist item Alpha thing"))
        #expect(commit.arguments.contains("docs/wishlist/2026-06-19-alpha-thing.md"))
    }

    @Test func addItemDisambiguatesFilenameCollision() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("docs/wishlist")
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: nil, now: { Self.fixedDate() })
        let u1 = try await writer.addItem(title: "Same", priority: .medium, body: "a", source: nil, area: nil, in: dir, noun: "wishlist item")
        let u2 = try await writer.addItem(title: "Same", priority: .medium, body: "b", source: nil, area: nil, in: dir, noun: "wishlist item")
        #expect(u1.lastPathComponent == "2026-06-19-same.md")
        #expect(u2.lastPathComponent == "2026-06-19-same-2.md")
    }

    @Test func emptyTitleThrowsAndDoesNotCommit() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("docs/wishlist")
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        await #expect(throws: PerFileItemWriterError.emptyTitle) {
            _ = try await writer.addItem(title: "   ", priority: .medium, body: "x", source: nil, area: nil, in: dir, noun: "wishlist item")
        }
        #expect(runner.calls.isEmpty)
    }

    @Test func resolveFlipsStatusAndCommits() async throws {
        let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
        let dir = vault.appendingPathComponent("docs/wishlist")
        let fileURL = dir.appendingPathComponent("2026-06-10-x.md")
        try "---\ntitle: X\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody".write(to: fileURL, atomically: true, encoding: .utf8)
        let runner = okRunner()
        let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
        try await writer.resolve(.done, fileURL: fileURL, label: "X")
        let written = try String(contentsOf: fileURL, encoding: .utf8)
        #expect(written.contains("status: done"))
        let commit = try #require(runner.calls.last)
        #expect(commit.arguments.contains("app: mark X done"))
        #expect(commit.arguments.contains("docs/wishlist/2026-06-10-x.md"))
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/PerFileItemWriterPureTests -only-testing:ScoutTests/PerFileItemWriterE2ETests 2>&1 | tail -20`
Expected: FAIL — `Cannot find 'PerFileItemWriter' in scope`.

- [ ] **Step 3: Write minimal implementation**

```swift
// Scout/PerFileItems/PerFileItemWriter.swift
import Foundation

enum ItemResolution: Sendable, Equatable {
    case done, dropped
    var status: ItemStatus { self == .done ? .done : .dropped }
    var word: String { self == .done ? "done" : "dropped" }
}

enum PerFileItemWriterError: Error, Equatable {
    case emptyTitle
    case readFailed(String)
    case writeFailed(String)
    case frontmatterNotFound(file: String)
    case statusFieldNotFound(file: String)
}

/// Serializes per-file writes (add new item, resolve to done/dropped) and
/// git-commits each change scoped to its single file (best-effort).
actor PerFileItemWriter {
    private let scoutDirectory: URL
    private let gitService: GitServiceProtocol?
    private let now: @Sendable () -> Date
    private var tail: Task<Void, Never>?

    init(scoutDirectory: URL, gitService: GitServiceProtocol?, now: @escaping @Sendable () -> Date = { Date() }) {
        self.scoutDirectory = scoutDirectory
        self.gitService = gitService
        self.now = now
    }

    @discardableResult
    func addItem(title: String, priority: ItemPriority, body: String, source: String?, area: String?,
                 in directoryURL: URL, noun: String) async throws -> URL {
        let previous = tail
        let task = Task { [scoutDirectory, gitService, now] in
            _ = await previous?.value
            return try await Self.performAdd(title: title, priority: priority, body: body, source: source,
                                             area: area, directoryURL: directoryURL, noun: noun,
                                             scoutDirectory: scoutDirectory, gitService: gitService, now: now)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    func resolve(_ resolution: ItemResolution, fileURL: URL, label: String) async throws {
        let previous = tail
        let task = Task { [scoutDirectory, gitService] in
            _ = await previous?.value
            return try await Self.performResolve(resolution: resolution, fileURL: fileURL, label: label,
                                                 scoutDirectory: scoutDirectory, gitService: gitService)
        }
        tail = Task { _ = try? await task.value }
        return try await task.value
    }

    // MARK: - perform (off-actor)

    @discardableResult
    private static func performAdd(title: String, priority: ItemPriority, body: String, source: String?,
                                   area: String?, directoryURL: URL, noun: String,
                                   scoutDirectory: URL, gitService: GitServiceProtocol?,
                                   now: @Sendable () -> Date) async throws -> URL {
        let cleanTitle = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanTitle.isEmpty else { throw PerFileItemWriterError.emptyTitle }
        let date = isoDate(now())
        let text = renderItemFile(title: cleanTitle, status: .open, priority: priority, date: date,
                                  source: source?.nilIfBlank, area: area?.nilIfBlank, body: body)
        do { try FileManager.default.createDirectory(at: directoryURL, withIntermediateDirectories: true) }
        catch { throw PerFileItemWriterError.writeFailed(error.localizedDescription) }
        let dest = uniqueURL(in: directoryURL, date: date, slug: slugify(cleanTitle))
        do { try text.write(to: dest, atomically: true, encoding: .utf8) }
        catch { throw PerFileItemWriterError.writeFailed(error.localizedDescription) }
        let rel = relativePathInRepo(fileURL: dest, repo: scoutDirectory)
        try? await gitService?.commitPaths([rel], message: "app: add \(noun) \(cleanTitle)")
        return dest
    }

    private static func performResolve(resolution: ItemResolution, fileURL: URL, label: String,
                                       scoutDirectory: URL, gitService: GitServiceProtocol?) async throws {
        let text: String
        do { text = try String(contentsOf: fileURL, encoding: .utf8) }
        catch { throw PerFileItemWriterError.readFailed(error.localizedDescription) }
        let updated = try rewriteFrontmatterStatus(text: text, newStatusValue: resolution.status.frontmatterValue,
                                                   file: fileURL.lastPathComponent)
        guard updated != text else { return }
        do { try updated.write(to: fileURL, atomically: true, encoding: .utf8) }
        catch { throw PerFileItemWriterError.writeFailed(error.localizedDescription) }
        let rel = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
        try? await gitService?.commitPaths([rel], message: "app: mark \(label) \(resolution.word)")
    }

    // MARK: - pure helpers

    static func slugify(_ title: String, maxWords: Int = 8) -> String {
        let mapped = title.lowercased().map { ch -> Character in
            (("a"..."z").contains(ch) || ("0"..."9").contains(ch)) ? ch : " "
        }
        let words = String(mapped).split(separator: " ").prefix(maxWords)
        return words.joined(separator: "-")
    }

    static func renderItemFile(title: String, status: ItemStatus, priority: ItemPriority, date: String,
                               source: String?, area: String?, body: String) -> String {
        func yq(_ s: String) -> String {
            "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
        }
        var fm = ["---", "title: \(yq(title))", "status: \(status.frontmatterValue)",
                  "priority: \(priority.rawValue)", "date: \(date)"]
        if let source, !source.isEmpty { fm.append("source: \(yq(source))") }
        if let area, !area.isEmpty { fm.append("area: \(yq(area))") }
        fm.append("---")
        let trimmedBody = body.trimmingCharacters(in: .whitespacesAndNewlines)
        return fm.joined(separator: "\n") + "\n\n# \(title)\n\n" + trimmedBody + "\n"
    }

    static func uniqueURL(in dir: URL, date: String, slug: String) -> URL {
        let base = "\(date)-\(slug)"
        var candidate = dir.appendingPathComponent("\(base).md")
        var n = 2
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = dir.appendingPathComponent("\(base)-\(n).md")
            n += 1
        }
        return candidate
    }

    static func rewriteFrontmatterStatus(text: String, newStatusValue: String, file: String) throws -> String {
        var lines = text.components(separatedBy: "\n")
        guard let first = lines.first, first.trimmingCharacters(in: .whitespaces) == "---" else {
            throw PerFileItemWriterError.frontmatterNotFound(file: file)
        }
        var i = 1
        while i < lines.count {
            if lines[i].trimmingCharacters(in: .whitespaces) == "---" { break }
            if let colon = lines[i].firstIndex(of: ":") {
                let key = lines[i][..<colon].trimmingCharacters(in: .whitespaces).lowercased()
                if key == "status" {
                    let leading = String(lines[i].prefix(while: { $0 == " " || $0 == "\t" }))
                    lines[i] = "\(leading)status: \(newStatusValue)"
                    return lines.joined(separator: "\n")
                }
            }
            i += 1
        }
        throw PerFileItemWriterError.statusFieldNotFound(file: file)
    }

    private static func isoDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone.current
        return f.string(from: date)
    }

    private static func relativePathInRepo(fileURL: URL, repo: URL) -> String {
        let full = fileURL.standardizedFileURL.path
        let prefix = repo.standardizedFileURL.path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : fileURL.lastPathComponent
    }
}

/// Actors can't be `@EnvironmentObject`; wrap for SwiftUI injection.
final class PerFileItemWriterBox: ObservableObject {
    let writer: PerFileItemWriter
    init(writer: PerFileItemWriter) { self.writer = writer }
}

private extension String {
    var nilIfBlank: String? { trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : self }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/PerFileItemWriterPureTests -only-testing:ScoutTests/PerFileItemWriterE2ETests 2>&1 | tail -20`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/PerFileItemWriter.swift ScoutTests/PerFile/PerFileItemWriterTests.swift
git commit -m "feat(perfile): PerFileItemWriter (add + resolve, git-scoped)"
```

---

## Task 7: PerFileDocumentService (FSEvents)

**Files:**
- Create: `Scout/PerFileItems/PerFileDocumentService.swift`
- Test: `ScoutTests/PerFile/PerFileDocumentServiceTests.swift`

**Interfaces:**
- Consumes: `PerFileItemParser` (Task 4), `FileSystemEventSource`/`FileSystemEvent` (`Scout/Services/Protocols/FileSystemEventSource.swift`).
- Produces: `@MainActor final class PerFileDocumentService: ObservableObject` — `init(directoryURL:fileEvents:)`; `@Published private(set) var items: [PerFileItem]`; `@Published private(set) var state: State` (`enum State { case idle, loading, loaded, missing(URL), failed(String) }`); `let directoryURL: URL`; `var activeCount: Int`; `func load()`; `func reload()`.

- [ ] **Step 1: Write the failing test**

```swift
// ScoutTests/PerFile/PerFileDocumentServiceTests.swift
import Foundation
import Testing
@testable import Scout

private struct EmptyFileEvents: FileSystemEventSource {
    func events(for url: URL) -> AsyncStream<FileSystemEvent> { AsyncStream { $0.finish() } }
}

@MainActor
@Suite("PerFileDocumentService")
struct PerFileDocumentServiceTests {
    private func makeDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("perfile-doc-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    @Test func loadsAndCountsActive() throws {
        let dir = try makeDir(); defer { try? FileManager.default.removeItem(at: dir) }
        try "---\ntitle: A\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# A\nbody".write(
            to: dir.appendingPathComponent("2026-06-10-a.md"), atomically: true, encoding: .utf8)
        try "---\ntitle: B\nstatus: done\npriority: low\ndate: 2026-06-09\n---\n\n# B\nbody".write(
            to: dir.appendingPathComponent("2026-06-09-b.md"), atomically: true, encoding: .utf8)
        let svc = PerFileDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()
        #expect(svc.items.count == 2)
        #expect(svc.activeCount == 1)               // only A is open
        #expect(svc.state == .loaded)
    }

    @Test func missingDirectory() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("absent-\(UUID().uuidString)")
        let svc = PerFileDocumentService(directoryURL: dir, fileEvents: EmptyFileEvents())
        svc.load()
        #expect(svc.items.isEmpty)
        #expect(svc.state == .missing(dir))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/PerFileDocumentServiceTests 2>&1 | tail -20`
Expected: FAIL — `Cannot find 'PerFileDocumentService' in scope`.

- [ ] **Step 3: Write minimal implementation**

This mirrors `Scout/Proposals/ProposalsDocumentService.swift` (renamed; `proposals`→`items`, `pendingCount`→`activeCount`, parser call swapped):

```swift
// Scout/PerFileItems/PerFileDocumentService.swift
import Combine
import Foundation
import SwiftUI

@MainActor
final class PerFileDocumentService: ObservableObject {
    enum State: Equatable {
        case idle, loading, loaded
        case missing(URL)
        case failed(String)
    }

    @Published private(set) var items: [PerFileItem] = []
    @Published private(set) var state: State = .idle

    let directoryURL: URL
    private let fileEvents: any FileSystemEventSource
    private var watchTask: Task<Void, Never>?

    var activeCount: Int { items.filter(\.isActive).count }

    init(directoryURL: URL, fileEvents: any FileSystemEventSource) {
        self.directoryURL = directoryURL
        self.fileEvents = fileEvents
    }

    func load() {
        state = .loading
        reparse()
        startWatching()
    }

    func reload() { reparse() }

    private func reparse() {
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: directoryURL.path, isDirectory: &isDir), isDir.boolValue else {
            items = []
            state = .missing(directoryURL)
            return
        }
        let files: [URL]
        do {
            files = try FileManager.default.contentsOfDirectory(at: directoryURL, includingPropertiesForKeys: nil)
        } catch {
            state = .failed(error.localizedDescription)
            return
        }
        items = files
            .filter { $0.pathExtension == "md" }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
            .compactMap { url -> PerFileItem? in
                guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
                return PerFileItemParser.parseFile(contents: text, fileURL: url)
            }
        state = .loaded
    }

    private func startWatching() {
        watchTask?.cancel()
        let stream = fileEvents.events(for: directoryURL)
        watchTask = Task { [weak self] in
            var debounce: Task<Void, Never>?
            for await event in stream {
                guard self != nil else { return }
                guard event.url.pathExtension == "md" else { continue }
                debounce?.cancel()
                debounce = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 250_000_000)
                    self?.reparse()
                }
            }
        }
    }

    deinit { watchTask?.cancel() }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests/PerFileDocumentServiceTests 2>&1 | tail -20`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/PerFileDocumentService.swift ScoutTests/PerFile/PerFileDocumentServiceTests.swift
git commit -m "feat(perfile): PerFileDocumentService (FSEvents list)"
```

---

## Task 8: Shared small views (body view + pills)

**Files:**
- Create: `Scout/PerFileItems/Views/MarkdownBodyView.swift`
- Create: `Scout/PerFileItems/Views/ItemStatusPill.swift`
- Create: `Scout/PerFileItems/Views/ItemPriorityPill.swift`

**Interfaces:**
- Consumes: `MarkdownBodyBlock`, `ItemStatus`, `ItemPriority`, `InlineMarkdownText`, `DS.*` tokens.
- Produces: `MarkdownBodyView(blocks:)`, `ItemStatusPill(status:)`, `ItemPriorityPill(priority:)`.

- [ ] **Step 1: Write the implementations** (SwiftUI views — verified by build; `MarkdownBodyView` mirrors `Scout/Proposals/Views/ProposalBodyView.swift`)

```swift
// Scout/PerFileItems/Views/MarkdownBodyView.swift
import SwiftUI

struct MarkdownBodyView: View {
    let blocks: [MarkdownBodyBlock]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(blocks) { block in
                switch block {
                case .prose(let text):
                    InlineMarkdownText(text)
                        .font(DS.serif(13.5))
                        .foregroundStyle(DS.Ink.p2)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                case .code(_, let code):
                    Text(code)
                        .font(DS.mono(12))
                        .foregroundStyle(DS.Ink.p2)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                        .neumorphicPressed(cornerRadius: 6)
                }
            }
        }
    }
}
```

```swift
// Scout/PerFileItems/Views/ItemStatusPill.swift
import SwiftUI

struct ItemStatusPill: View {
    let status: ItemStatus
    var body: some View {
        Text(status.displayName.uppercased())
            .font(DS.mono(9.5)).tracking(0.6)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(Capsule().fill(tint.opacity(0.16)))
            .foregroundStyle(tint)
    }
    private var tint: Color {
        switch status {
        case .open: return DS.Status.todo
        case .inProgress: return DS.SlotType.consolidation
        case .done: return DS.Status.ok
        case .dropped: return DS.Ink.p3
        case .unknown: return DS.Ink.p3
        }
    }
}
```

```swift
// Scout/PerFileItems/Views/ItemPriorityPill.swift
import SwiftUI

struct ItemPriorityPill: View {
    let priority: ItemPriority
    var body: some View {
        Text(priority.displayName.uppercased())
            .font(DS.mono(9.5)).tracking(0.6)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(Capsule().fill(tint.opacity(0.16)))
            .foregroundStyle(tint)
    }
    private var tint: Color {
        switch priority {
        case .urgent: return DS.Priority.high
        case .high: return DS.Priority.high
        case .medium: return DS.Priority.medium
        case .low: return DS.Ink.p3
        }
    }
}
```

NOTE for the implementer: `DS.Status.todo`, `DS.Status.ok`, `DS.SlotType.consolidation`, `DS.Priority.high`, `DS.Priority.medium`, `DS.Ink.p2`, `DS.Ink.p3`, `DS.serif`, `DS.mono`, and the `.neumorphicPressed(cornerRadius:)` modifier are all used by the Proposals views — confirm the exact token names in `Scout/Proposals/Views/ProposalStatusPill.swift` + `ProposalBodyView.swift` and the design-system file, and substitute the nearest existing token if a name differs. (`urgent` reuses the high tint; pick a distinct red token if one exists.)

- [ ] **Step 2: Build to verify it compiles**

Run: `cd ~/scout-app && xcodebuild build -scheme Scout 2>&1 | tail -5`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/Views/MarkdownBodyView.swift Scout/PerFileItems/Views/ItemStatusPill.swift Scout/PerFileItems/Views/ItemPriorityPill.swift
git commit -m "feat(perfile): body view + status/priority pills"
```

---

## Task 9: AddItemSheet

**Files:**
- Create: `Scout/PerFileItems/Views/AddItemSheet.swift`

**Interfaces:**
- Consumes: `PerFileTabConfig` (Task 5), `ItemPriority` (Task 1).
- Produces: `AddItemSheet(config:onSubmit:onCancel:)` where `onSubmit: (_ title: String, _ priority: ItemPriority, _ body: String, _ optional: String?) async -> Void`.

- [ ] **Step 1: Write the implementation** (SwiftUI form — verified by build + manual test in Task 13)

```swift
// Scout/PerFileItems/Views/AddItemSheet.swift
import SwiftUI

struct AddItemSheet: View {
    let config: PerFileTabConfig
    /// (title, priority, body, optionalFieldValue) — optional is source/area per config.
    let onSubmit: (String, ItemPriority, String, String?) async -> Void
    let onCancel: () -> Void

    @State private var title: String = ""
    @State private var priority: ItemPriority
    @State private var bodyText: String = ""
    @State private var optionalValue: String = ""
    @State private var submitting = false

    init(config: PerFileTabConfig,
         onSubmit: @escaping (String, ItemPriority, String, String?) async -> Void,
         onCancel: @escaping () -> Void) {
        self.config = config
        self.onSubmit = onSubmit
        self.onCancel = onCancel
        _priority = State(initialValue: config.defaultPriority)
    }

    private var canSubmit: Bool {
        !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !submitting
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add \(config.title) item").font(DS.serif(18)).foregroundStyle(DS.Ink.p1)

            field("Title") { TextField("", text: $title).textFieldStyle(.roundedBorder) }

            field("Priority") {
                Picker("", selection: $priority) {
                    ForEach(config.priorities, id: \.self) { Text($0.displayName).tag($0) }
                }.labelsHidden().pickerStyle(.segmented)
            }

            if let label = config.optionalField.label {
                field(label) { TextField("", text: $optionalValue).textFieldStyle(.roundedBorder) }
            }

            field("Notes") {
                TextEditor(text: $bodyText)
                    .font(DS.serif(13.5)).frame(minHeight: 120)
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(DS.Ink.p3.opacity(0.3)))
            }

            HStack {
                Spacer()
                Button("Cancel", action: onCancel).keyboardShortcut(.cancelAction)
                Button("Add") { submit() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canSubmit)
            }
        }
        .padding(20)
        .frame(width: 460)
        .background(DS.Paper.base)
    }

    @ViewBuilder private func field<Content: View>(_ label: String, @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label.uppercased()).font(DS.mono(10)).tracking(0.6).foregroundStyle(DS.Ink.p3)
            content()
        }
    }

    private func submit() {
        guard canSubmit else { return }
        submitting = true
        let optional = optionalValue.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            await onSubmit(title, priority, bodyText, optional.isEmpty ? nil : optional)
        }
    }
}
```

NOTE: confirm `DS.Ink.p1`, `DS.Paper.base` token names against the design system; substitute the nearest if different.

- [ ] **Step 2: Build to verify it compiles**

Run: `cd ~/scout-app && xcodebuild build -scheme Scout 2>&1 | tail -5`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/Views/AddItemSheet.swift
git commit -m "feat(perfile): AddItemSheet form"
```

---

## Task 10: PerFileItemCardView

**Files:**
- Create: `Scout/PerFileItems/Views/PerFileItemCardView.swift`

**Interfaces:**
- Consumes: `PerFileItem`, `ItemResolution`, `ItemStatusPill`, `ItemPriorityPill`, `MarkdownBodyView`, `PerFileTabConfig`.
- Produces: `PerFileItemCardView(item:optionalLabel:onResolve:)` where `onResolve: @MainActor (ItemResolution) async throws -> Void`.

- [ ] **Step 1: Write the implementation** (mirrors `Scout/Proposals/Views/ProposalCardView.swift`: header chip + title + pills, body, and for active items two buttons Done/Drop; local in-flight + error)

```swift
// Scout/PerFileItems/Views/PerFileItemCardView.swift
import SwiftUI

struct PerFileItemCardView: View {
    let item: PerFileItem
    let optionalLabel: String?   // "Source"/"Area" label from config, for displaying item.source/area
    let onResolve: @MainActor (ItemResolution) async throws -> Void

    @State private var inFlight: ItemResolution?
    @State private var errorText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if !item.date.isEmpty {
                    Text(item.date).font(DS.mono(11)).foregroundStyle(DS.Ink.p3)
                }
                Text(item.title).font(DS.serif(15)).foregroundStyle(DS.Ink.p1)
                Spacer()
                ItemPriorityPill(priority: item.priority)
                ItemStatusPill(status: item.status)
            }

            if let label = optionalLabel, let value = optionalValue, !value.isEmpty {
                Text("\(label): \(value)").font(DS.mono(11)).foregroundStyle(DS.Ink.p3)
            }

            if !item.bodyBlocks.isEmpty { MarkdownBodyView(blocks: item.bodyBlocks) }

            if item.isActive { actions }

            if let errorText {
                Label(errorText, systemImage: "exclamationmark.triangle")
                    .font(DS.mono(11)).foregroundStyle(DS.Status.warn)
            }
        }
        .editorialCard(padding: 18)
    }

    private var optionalValue: String? { item.source ?? item.area }

    private var actions: some View {
        HStack(spacing: 8) {
            button(.done, "Done", "checkmark")
            button(.dropped, "Drop", "xmark")
        }
    }

    private func button(_ resolution: ItemResolution, _ label: String, _ icon: String) -> some View {
        Button {
            resolve(resolution)
        } label: {
            if inFlight == resolution { ProgressView().controlSize(.small) }
            else { Label(label, systemImage: icon) }
        }
        .buttonStyle(.plainHit)
        .disabled(inFlight != nil)
    }

    private func resolve(_ resolution: ItemResolution) {
        inFlight = resolution
        errorText = nil
        Task {
            do { try await onResolve(resolution) }
            catch { errorText = error.localizedDescription }
            inFlight = nil
        }
    }
}
```

NOTE: `.editorialCard(padding:)`, `.buttonStyle(.plainHit)`, `DS.Status.warn`, `DS.Ink.p1` — confirm against `ProposalCardView.swift`; substitute nearest existing names if different.

- [ ] **Step 2: Build to verify it compiles**

Run: `cd ~/scout-app && xcodebuild build -scheme Scout 2>&1 | tail -5`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/Views/PerFileItemCardView.swift
git commit -m "feat(perfile): PerFileItemCardView (Done/Drop)"
```

---

## Task 11: PerFileListView

**Files:**
- Create: `Scout/PerFileItems/Views/PerFileListView.swift`

**Interfaces:**
- Consumes: `PerFileDocumentService` + `PerFileItemWriterBox` (via `@EnvironmentObject`), `PerFileTabConfig`, `PerFileItemCardView`, `AddItemSheet`.
- Produces: `PerFileListView(config:)`.

- [ ] **Step 1: Write the implementation** (mirrors `Scout/Proposals/Views/ProposalsView.swift`: header, awaiting/resolved split + collapsible Resolved, toolbar ＋Add + reveal-folder, sheet; awaiting sorted by priority)

```swift
// Scout/PerFileItems/Views/PerFileListView.swift
import SwiftUI

struct PerFileListView: View {
    let config: PerFileTabConfig
    @EnvironmentObject var docService: PerFileDocumentService
    @EnvironmentObject var writerBox: PerFileItemWriterBox
    @State private var resolvedExpanded = false
    @State private var showingAdd = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header
                content
            }
            .frame(maxWidth: 920, alignment: .leading)
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .background(DS.Paper.base)
        .toolbar {
            ToolbarItemGroup {
                Button { showingAdd = true } label: { Label("Add", systemImage: "plus") }
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([docService.directoryURL])
                } label: { Label("Reveal in Finder", systemImage: "folder") }
            }
        }
        .sheet(isPresented: $showingAdd) {
            AddItemSheet(config: config, onSubmit: { title, priority, body, optional in
                await addItem(title: title, priority: priority, body: body, optional: optional)
            }, onCancel: { showingAdd = false })
        }
        .onAppear { docService.load() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(config.title).font(DS.serif(24)).foregroundStyle(DS.Ink.p1)
            Text("\(docService.activeCount) active").font(DS.mono(12)).foregroundStyle(DS.Ink.p3)
        }
    }

    @ViewBuilder private var content: some View {
        switch docService.state {
        case .idle, .loading:
            ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
        case .missing:
            emptyState(icon: "tray", message: "No \(config.title.lowercased()) folder yet. Items appear here once added.")
        case .failed(let err):
            Text(err).font(DS.mono(12)).foregroundStyle(DS.Status.warn)
        case .loaded:
            loadedContent
        }
    }

    @ViewBuilder private var loadedContent: some View {
        let awaiting = docService.items.filter(\.isActive).sorted { $0.priority < $1.priority }
        let resolved = docService.items.filter { !$0.isActive }
        if docService.items.isEmpty {
            emptyState(icon: "sparkles", message: "Nothing here yet. Use ＋ to add one.")
        } else {
            ForEach(awaiting) { item in
                PerFileItemCardView(item: item, optionalLabel: config.optionalField.label) { resolution in
                    try await resolve(item, resolution)
                }
            }
            if !resolved.isEmpty { resolvedSection(resolved) }
        }
    }

    @ViewBuilder private func resolvedSection(_ resolved: [PerFileItem]) -> some View {
        Button {
            withAnimation(.easeInOut(duration: 0.15)) { resolvedExpanded.toggle() }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: resolvedExpanded ? "chevron.down" : "chevron.right")
                Text("Resolved \(resolved.count)").font(DS.mono(11)).tracking(0.4)
            }.foregroundStyle(DS.Ink.p3)
        }.buttonStyle(.plainHit)
        if resolvedExpanded {
            ForEach(resolved) { item in
                PerFileItemCardView(item: item, optionalLabel: config.optionalField.label) { _ in }
            }
        }
    }

    private func emptyState(icon: String, message: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: icon).font(.system(size: 28)).foregroundStyle(DS.Ink.p3)
            Text(message).font(DS.serif(13.5)).foregroundStyle(DS.Ink.p3)
        }.frame(maxWidth: .infinity).padding(.top, 40)
    }

    private func addItem(title: String, priority: ItemPriority, body: String, optional: String?) async {
        var source: String?; var area: String?
        switch config.optionalField {
        case .none: break
        case .source: source = optional
        case .area: area = optional
        }
        _ = try? await writerBox.writer.addItem(title: title, priority: priority, body: body,
                                                source: source, area: area,
                                                in: docService.directoryURL, noun: config.addNoun)
        showingAdd = false
        docService.reload()
    }

    private func resolve(_ item: PerFileItem, _ resolution: ItemResolution) async throws {
        try await writerBox.writer.resolve(resolution, fileURL: item.fileURL, label: item.title)
        docService.reload()
    }
}
```

NOTE: confirm `DS.*` token names, `.buttonStyle(.plainHit)`, and the header/scroll layout against `ProposalsView.swift`; match its exact structure/tokens.

- [ ] **Step 2: Build to verify it compiles**

Run: `cd ~/scout-app && xcodebuild build -scheme Scout 2>&1 | tail -5`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 3: Commit**

```bash
cd ~/scout-app && git add Scout/PerFileItems/Views/PerFileListView.swift
git commit -m "feat(perfile): PerFileListView (list + add + resolve)"
```

---

## Task 12: Sidebar / AppState / Settings wiring

**Files:**
- Modify: `Scout/Shell/MainWindowView.swift`
- Modify: `Scout/Shell/SidebarView.swift`
- Modify: `Scout/Shell/AppState.swift`
- Modify: `Scout/Shell/SettingsView.swift`

**Interfaces:**
- Consumes: everything above. Produces: two live tabs wired into the app.

- [ ] **Step 1: AppState — add two doc services + one shared writer/box + path resolution + launch load**

In `Scout/Shell/AppState.swift`, mirror the existing `proposalsDirURL` / `proposalsDocumentService` / `proposalsWriterBox` wiring. Add a path-resolver helper and two services + one shared writer. Inside `init`, where `proposalsDir`/services are built (the block that constructs `watcher`, `git`, `proposalsDoc`):

```swift
// helper (file-scope or static): resolve an override key or default-relative path under scoutDir
func perFileDir(_ config: PerFileTabConfig) -> URL {
    let override = UserDefaults.standard.string(forKey: config.pathOverrideKey)?
        .trimmingCharacters(in: .whitespacesAndNewlines)
    if let override, !override.isEmpty {
        return URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
    }
    return scoutDir.appendingPathComponent(config.directoryDefaultRelative)
}

let wishlistDoc = PerFileDocumentService(directoryURL: perFileDir(.wishlist), fileEvents: watcher)
let researchDoc = PerFileDocumentService(directoryURL: perFileDir(.research), fileEvents: watcher)
let perFileWriter = PerFileItemWriter(scoutDirectory: scoutDir, gitService: git)
let perFileWriterBox = PerFileItemWriterBox(writer: perFileWriter)
```

Store as `let` properties next to `proposalsDocumentService` / `proposalsWriterBox`:
```swift
let wishlistDocumentService: PerFileDocumentService
let researchDocumentService: PerFileDocumentService
let perFileWriterBox: PerFileItemWriterBox
```
…and assign them in `init` (`self.wishlistDocumentService = wishlistDoc`, etc.).

In the launch `Task` where `proposalsDoc.load()` runs inside `MainActor.run { … }`, add:
```swift
wishlistDoc.load()
researchDoc.load()
```

- [ ] **Step 2: SidebarItem + detail switch + badges — `MainWindowView.swift`**

Add cases to `SidebarItem` and `statusLabel`:
```swift
enum SidebarItem: Hashable {
    case controlCenter, actionItems, schedules, proposals, wishlist, research, settings
    var statusLabel: String {
        switch self {
        case .controlCenter: return "control"
        case .actionItems:   return "actions"
        case .schedules:     return "schedules"
        case .proposals:     return "proposals"
        case .wishlist:      return "wishlist"
        case .research:      return "research"
        case .settings:      return "settings"
        }
    }
}
```

Add detail branches (mirror the `.proposals` case, injecting the per-tab service + the shared writer box):
```swift
case .wishlist:
    PerFileListView(config: .wishlist)
        .environmentObject(appState.wishlistDocumentService)
        .environmentObject(appState.perFileWriterBox)
case .research:
    PerFileListView(config: .research)
        .environmentObject(appState.researchDocumentService)
        .environmentObject(appState.perFileWriterBox)
```

Pass active-count badges to the sidebar. Add `@EnvironmentObject`/`appState` reads for the two services and update the `SidebarView(...)` call:
```swift
SidebarView(selection: $selection,
            proposalsBadge: proposalsService.pendingCount,
            wishlistBadge: appState.wishlistDocumentService.activeCount,
            researchBadge: appState.researchDocumentService.activeCount)
```
(If the badge must be reactive, observe the two services the same way `proposalsService` is observed at the root — add `@EnvironmentObject var wishlistService: PerFileDocumentService` etc. and inject them at the `MainWindowView` use-site, OR read off `appState` if the existing pattern does so. Match the existing `proposalsService` reactivity pattern exactly.)

- [ ] **Step 3: SidebarView rows — `SidebarView.swift`**

Add badge params (default 0) and two rows after the Proposals row:
```swift
var wishlistBadge: Int = 0
var researchBadge: Int = 0
// …in the row list, after the proposals row:
row(.wishlist, label: "Wishlist", system: "star", badge: wishlistBadge)
row(.research, label: "Research", system: "magnifyingglass", badge: researchBadge)
```

- [ ] **Step 4: Settings fields — `SettingsView.swift`**

Add two `@AppStorage` keys and a section (mirror the Proposals section):
```swift
@AppStorage("wishlistPath") private var wishlistPath: String = ""
@AppStorage("researchQueuePath") private var researchQueuePath: String = ""
// …in the body, a new section:
section(label: "Wishlist & Research") {
    SettingsCard {
        SettingsField(label: "Wishlist folder",
            help: "Per-file wishlist items the Wishlist tab reads. Blank = `~/Scout/docs/wishlist`. Takes effect after restarting Scout.") {
            SettingsInput(text: $wishlistPath, placeholder: "~/Scout/docs/wishlist")
        }
        SettingsField(label: "Research queue folder",
            help: "Per-file research topics the Research tab reads. Blank = `~/Scout/knowledge-base/research-queue`. Takes effect after restarting Scout.") {
            SettingsInput(text: $researchQueuePath, placeholder: "~/Scout/knowledge-base/research-queue")
        }
    }
}
```

- [ ] **Step 5: Build the whole app**

Run: `cd ~/scout-app && xcodebuild build -scheme Scout 2>&1 | tail -8`
Expected: `** BUILD SUCCEEDED **`. Fix any token/property-name mismatches against the actual files.

- [ ] **Step 6: Commit**

```bash
cd ~/scout-app && git add Scout/Shell/MainWindowView.swift Scout/Shell/SidebarView.swift Scout/Shell/AppState.swift Scout/Shell/SettingsView.swift
git commit -m "feat(perfile): wire Wishlist + Research tabs (sidebar, AppState, settings)"
```

---

## Task 13: Full test suite + manual end-to-end verification

**Files:** none (verification).

- [ ] **Step 1: Run the full ScoutTests suite**

Run: `cd ~/scout-app && xcodebuild test -scheme Scout -only-testing:ScoutTests 2>&1 | tail -25`
Expected: all tests pass (the new `PerFile/*` suites + all pre-existing suites incl. Proposals). If a Proposals test broke, you violated "don't modify Proposals" — revert that change.

- [ ] **Step 2: Launch the app and verify both tabs against a real/temp vault**

Use the `run` skill (or `xcodebuild` + open the built `.app`). With a vault that has `docs/wishlist/*.md` and `knowledge-base/research-queue/*.md` (Jordan's `~/Scout` is already migrated):
- Both **Wishlist** and **Research** rows appear in the sidebar with active-count badges.
- Each tab lists items (Awaiting sorted with urgent/high first; collapsible Resolved section).
- Card bodies render (prose + any code); the optional field (Source/Area) shows when present.

- [ ] **Step 3: Verify Add**

In the Wishlist tab, click ＋, fill Title + priority + notes (+ Source), Add. Confirm: a new `docs/wishlist/<today>-<slug>.md` appears with correct frontmatter, the item shows under Awaiting, and `git -C ~/Scout log --oneline -1` shows `app: add wishlist item <title>`. Repeat in Research (Area field, `app: add research topic <title>`).

- [ ] **Step 4: Verify Resolve**

On an active card, click **Done**. Confirm the item moves to Resolved, the file's frontmatter `status:` is now `done`, and the latest commit is `app: mark <title> done`. Repeat **Drop** → `dropped`.

- [ ] **Step 5: Report**

Summarize: suite green (count), both tabs list/add/resolve working against the vault, commits scoped + correctly messaged, Proposals untouched and still green. Note that sub-project 3 is complete.
