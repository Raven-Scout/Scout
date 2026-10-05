# Wishlist/Research — Editable Priority & Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user change a Wishlist/Research item's priority and status (Start → in-progress, Reopen resolved → open) directly from its card.

**Architecture:** Generalize the existing single-field frontmatter rewrite + scoped-commit path in `PerFileItemWriter` into `setPriority`/`setStatus` (reusing the `GuardedFileWrite` #48 race guard), then make `ItemPriorityPill` a menu and add Start/Reopen actions to `PerFileItemCardView`, wired through `PerFileListView`.

**Tech Stack:** Swift, SwiftUI, Swift Testing (`@Test`/`@Suite`), xcodebuild.

## Global Constraints

- Build/test: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests`. The full target is currently green at **357 tests**.
- Run a single suite during TDD: `-only-testing:ScoutTests/<SuiteTypeName>` (use the struct type name, e.g. `PerFileItemWriterE2ETests`; a directory selector like `ScoutTests/PerFile` runs **zero** tests — false green).
- New `.swift` files under `Scout/`/`ScoutTests/` auto-compile (synchronized file groups); no `.pbxproj` edits.
- SourceKit may show "Cannot find type 'Run'/'AppState'…" and "No such module 'Testing'" — these are IDE false positives; `xcodebuild` is authoritative.
- Scope is `Scout/PerFileItems/` only. Do **not** touch `Scout/Proposals/ProposalsWriter.swift` (it has its own duplicate `rewriteFrontmatterStatus`/`statusFieldNotFound`; unifying them is separate tech debt).
- All commit messages end with the trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Frontmatter values written verbatim: `ItemPriority.rawValue` (`urgent`/`high`/`medium`/`low`) and `ItemStatus.frontmatterValue` (`open`/`in-progress`/`done`/`dropped`).

---

### Task 1: Generalize the frontmatter-field rewrite + rename error

**Files:**
- Modify: `Scout/PerFileItems/PerFileItemWriter.swift` (error enum ~line 15; `rewriteFrontmatterStatus` ~line 139; `performResolve` call ~line 87)
- Test: `ScoutTests/PerFile/PerFileItemWriterTests.swift` (the `PerFileItemWriterPureTests` suite; existing `rewriteStatusPreservesRest` ~line 37)

**Interfaces:**
- Produces: `static func rewriteFrontmatterField(text: String, key: String, value: String, file: String) throws -> String`; error case `PerFileItemWriterError.fieldNotFound(field: String, file: String)` (replaces `statusFieldNotFound(file:)`).

- [ ] **Step 1: Update the existing pure test + add two new ones**

In `PerFileItemWriterPureTests`, replace `rewriteStatusPreservesRest` and add two tests:

```swift
@Test func rewriteFieldReplacesStatusPreservesRest() throws {
    let text = "---\ntitle: X\nstatus: open\npriority: high\n---\n\n# X\nbody"
    let updated = try PerFileItemWriter.rewriteFrontmatterField(text: text, key: "status", value: "done", file: "x.md")
    #expect(updated.contains("status: done"))
    #expect(updated.contains("priority: high"))
    #expect(updated.contains("# X\nbody"))
}

@Test func rewriteFieldReplacesPriorityPreservesRest() throws {
    let text = "---\ntitle: X\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody"
    let updated = try PerFileItemWriter.rewriteFrontmatterField(text: text, key: "priority", value: "urgent", file: "x.md")
    #expect(updated.contains("priority: urgent"))
    #expect(updated.contains("status: open"))         // untouched
    #expect(updated.contains("date: 2026-06-10"))     // untouched
    #expect(updated.contains("# X\nbody"))
}

@Test func rewriteFieldThrowsWhenFieldMissing() throws {
    let text = "---\ntitle: X\nstatus: open\n---\n\n# X\nbody"  // no priority:
    #expect(throws: PerFileItemWriterError.fieldNotFound(field: "priority", file: "x.md")) {
        _ = try PerFileItemWriter.rewriteFrontmatterField(text: text, key: "priority", value: "low", file: "x.md")
    }
}
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PerFileItemWriterPureTests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: build error / FAIL — `rewriteFrontmatterField` and `fieldNotFound` don't exist yet.

- [ ] **Step 3: Generalize the method, rename the error, update the caller**

In `PerFileItemWriter.swift`, in `enum PerFileItemWriterError` replace the case:

```swift
    case fieldNotFound(field: String, file: String)
```

Replace `rewriteFrontmatterStatus(...)` with:

```swift
static func rewriteFrontmatterField(text: String, key: String, value: String, file: String) throws -> String {
    var lines = text.components(separatedBy: "\n")
    guard let first = lines.first, first.trimmingCharacters(in: .whitespaces) == "---" else {
        throw PerFileItemWriterError.frontmatterNotFound(file: file)
    }
    let wantedKey = key.lowercased()
    var i = 1
    while i < lines.count {
        if lines[i].trimmingCharacters(in: .whitespaces) == "---" { break }
        if let colon = lines[i].firstIndex(of: ":") {
            let k = lines[i][..<colon].trimmingCharacters(in: .whitespaces).lowercased()
            if k == wantedKey {
                let leading = String(lines[i].prefix(while: { $0 == " " || $0 == "\t" }))
                lines[i] = "\(leading)\(key): \(value)"
                return lines.joined(separator: "\n")
            }
        }
        i += 1
    }
    throw PerFileItemWriterError.fieldNotFound(field: key, file: file)
}
```

In `performResolve`, change the transform call from `rewriteFrontmatterStatus(text: text, newStatusValue: resolution.status.frontmatterValue, file: ...)` to:

```swift
try rewriteFrontmatterField(text: text, key: "status", value: resolution.status.frontmatterValue, file: fileURL.lastPathComponent)
```

- [ ] **Step 4: Run the tests, verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PerFileItemWriterPureTests 2>&1 | grep -iE "Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/PerFileItemWriter.swift ScoutTests/PerFile/PerFileItemWriterTests.swift
git commit -m "refactor(perfile): generalize rewriteFrontmatterField + fieldNotFound error (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `setPriority` writer method

**Files:**
- Modify: `Scout/PerFileItems/PerFileItemWriter.swift` (add `performFieldWrite`; add `setPriority`; refactor `performResolve` to delegate)
- Test: `ScoutTests/PerFile/PerFileItemWriterTests.swift` (`PerFileItemWriterE2ETests` suite)

**Interfaces:**
- Consumes: `rewriteFrontmatterField(text:key:value:file:)` (Task 1).
- Produces: `func setPriority(_ priority: ItemPriority, fileURL: URL, label: String) async throws`; `private static func performFieldWrite(fileURL:key:value:commitMessage:scoutDirectory:gitService:) async throws`.

- [ ] **Step 1: Write the failing E2E test**

Add to `PerFileItemWriterE2ETests`:

```swift
@Test func setPriorityRewritesFieldAndCommitsScoped() async throws {
    let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
    let dir = vault.appendingPathComponent("docs/wishlist")
    let fileURL = dir.appendingPathComponent("2026-06-10-x.md")
    try "---\ntitle: X\nstatus: open\npriority: medium\ndate: 2026-06-10\n---\n\n# X\nbody"
        .write(to: fileURL, atomically: true, encoding: .utf8)
    let runner = okRunner()
    let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
    try await writer.setPriority(.urgent, fileURL: fileURL, label: "X")
    let written = try String(contentsOf: fileURL, encoding: .utf8)
    #expect(written.contains("priority: urgent"))
    #expect(written.contains("status: open"))
    let commit = try #require(runner.calls.last)
    #expect(commit.arguments.contains("commit"))
    #expect(commit.arguments.contains("app: set X priority to urgent"))
    #expect(commit.arguments.contains("docs/wishlist/2026-06-10-x.md"))
}
```

- [ ] **Step 2: Run it, verify it fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PerFileItemWriterE2ETests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: build error — `setPriority` doesn't exist.

- [ ] **Step 3: Add `performFieldWrite` + `setPriority`, refactor `performResolve`**

Add the generalized write helper (place near `performResolve`):

```swift
private static func performFieldWrite(fileURL: URL, key: String, value: String, commitMessage: String,
                                      scoutDirectory: URL, gitService: GitServiceProtocol?) async throws {
    let didWrite: Bool
    do {
        didWrite = try GuardedFileWrite.apply(to: fileURL) { text in
            try rewriteFrontmatterField(text: text, key: key, value: value, file: fileURL.lastPathComponent)
        }
    } catch let e as GuardedFileWrite.Failure {
        switch e {
        case .read(let m): throw PerFileItemWriterError.readFailed(m)
        case .write(let m): throw PerFileItemWriterError.writeFailed(m)
        case .conflictPersisted:
            throw PerFileItemWriterError.writeFailed("\(fileURL.lastPathComponent) changed repeatedly under concurrent writes")
        }
    }
    guard didWrite else { return }
    let rel = relativePathInRepo(fileURL: fileURL, repo: scoutDirectory)
    try? await gitService?.commitPaths([rel], message: commitMessage)
}
```

Replace the body of `performResolve` so it delegates (keeps the same commit message `app: mark <label> <word>`):

```swift
private static func performResolve(resolution: ItemResolution, fileURL: URL, label: String,
                                   scoutDirectory: URL, gitService: GitServiceProtocol?) async throws {
    try await performFieldWrite(fileURL: fileURL, key: "status", value: resolution.status.frontmatterValue,
                                commitMessage: "app: mark \(label) \(resolution.word)",
                                scoutDirectory: scoutDirectory, gitService: gitService)
}
```

Add the `setPriority` actor method (mirror `resolve`'s `tail` enqueue):

```swift
func setPriority(_ priority: ItemPriority, fileURL: URL, label: String) async throws {
    let previous = tail
    let task = Task { [scoutDirectory, gitService] in
        _ = await previous?.value
        return try await Self.performFieldWrite(
            fileURL: fileURL, key: "priority", value: priority.rawValue,
            commitMessage: "app: set \(label) priority to \(priority.rawValue)",
            scoutDirectory: scoutDirectory, gitService: gitService)
    }
    tail = Task { _ = try? await task.value }
    return try await task.value
}
```

- [ ] **Step 4: Run it, verify it passes**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PerFileItemWriterE2ETests 2>&1 | grep -iE "Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: PASS (including the existing `resolveFlipsStatusAndCommits`, unchanged).

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/PerFileItemWriter.swift ScoutTests/PerFile/PerFileItemWriterTests.swift
git commit -m "feat(perfile): add setPriority writer method (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `setStatus` writer method + refactor `resolve` to delegate

**Files:**
- Modify: `Scout/PerFileItems/PerFileItemWriter.swift` (add `setStatus` + `statusCommitMessage`; refactor `resolve`; remove now-unused `performResolve`)
- Test: `ScoutTests/PerFile/PerFileItemWriterTests.swift` (`PerFileItemWriterE2ETests`)

**Interfaces:**
- Consumes: `performFieldWrite(...)` (Task 2).
- Produces: `func setStatus(_ status: ItemStatus, fileURL: URL, label: String) async throws`; `static func statusCommitMessage(_ status: ItemStatus, label: String) -> String`.

- [ ] **Step 1: Write the failing E2E tests**

Add to `PerFileItemWriterE2ETests`:

```swift
@Test func setStatusStartFlipsOpenToInProgress() async throws {
    let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
    let fileURL = vault.appendingPathComponent("docs/wishlist/2026-06-10-x.md")
    try "---\ntitle: X\nstatus: open\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody"
        .write(to: fileURL, atomically: true, encoding: .utf8)
    let runner = okRunner()
    let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
    try await writer.setStatus(.inProgress, fileURL: fileURL, label: "X")
    #expect(try String(contentsOf: fileURL, encoding: .utf8).contains("status: in-progress"))
    #expect(try #require(runner.calls.last).arguments.contains("app: start X"))
}

@Test func setStatusReopenFlipsDroppedToOpen() async throws {
    let vault = try makeVault(); defer { try? FileManager.default.removeItem(at: vault) }
    let fileURL = vault.appendingPathComponent("docs/wishlist/2026-06-10-x.md")
    try "---\ntitle: X\nstatus: dropped\npriority: high\ndate: 2026-06-10\n---\n\n# X\nbody"
        .write(to: fileURL, atomically: true, encoding: .utf8)
    let runner = okRunner()
    let writer = PerFileItemWriter(scoutDirectory: vault, gitService: GitService(repoURL: vault, runner: runner), now: { Self.fixedDate() })
    try await writer.setStatus(.open, fileURL: fileURL, label: "X")
    #expect(try String(contentsOf: fileURL, encoding: .utf8).contains("status: open"))
    #expect(try #require(runner.calls.last).arguments.contains("app: reopen X"))
}

@Test func statusCommitMessageMapping() {
    #expect(PerFileItemWriter.statusCommitMessage(.open, label: "T") == "app: reopen T")
    #expect(PerFileItemWriter.statusCommitMessage(.inProgress, label: "T") == "app: start T")
    #expect(PerFileItemWriter.statusCommitMessage(.done, label: "T") == "app: mark T done")
    #expect(PerFileItemWriter.statusCommitMessage(.dropped, label: "T") == "app: mark T dropped")
}
```

- [ ] **Step 2: Run them, verify they fail**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PerFileItemWriterE2ETests 2>&1 | grep -iE "error:|Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: build error — `setStatus`/`statusCommitMessage` don't exist.

- [ ] **Step 3: Add `setStatus` + `statusCommitMessage`, refactor `resolve`, remove `performResolve`**

Add:

```swift
static func statusCommitMessage(_ status: ItemStatus, label: String) -> String {
    switch status {
    case .open:        return "app: reopen \(label)"
    case .inProgress:  return "app: start \(label)"
    case .done:        return "app: mark \(label) done"
    case .dropped:     return "app: mark \(label) dropped"
    case .unknown(let raw): return "app: set \(label) status to \(raw)"
    }
}

func setStatus(_ status: ItemStatus, fileURL: URL, label: String) async throws {
    let previous = tail
    let message = Self.statusCommitMessage(status, label: label)
    let value = status.frontmatterValue
    let task = Task { [scoutDirectory, gitService] in
        _ = await previous?.value
        return try await Self.performFieldWrite(
            fileURL: fileURL, key: "status", value: value, commitMessage: message,
            scoutDirectory: scoutDirectory, gitService: gitService)
    }
    tail = Task { _ = try? await task.value }
    return try await task.value
}
```

Replace `resolve(...)` so it delegates (and delete the now-unused `performResolve`):

```swift
func resolve(_ resolution: ItemResolution, fileURL: URL, label: String) async throws {
    try await setStatus(resolution.status, fileURL: fileURL, label: label)
}
```

- [ ] **Step 4: Run them, verify they pass**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PerFileItemWriterE2ETests 2>&1 | grep -iE "Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: PASS — new tests plus the existing `resolveFlipsStatusAndCommits` (commit message still `app: mark X done` via `statusCommitMessage`).

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/PerFileItemWriter.swift ScoutTests/PerFile/PerFileItemWriterTests.swift
git commit -m "feat(perfile): add setStatus (start/reopen); resolve delegates to it (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Make `ItemPriorityPill` an editable menu

**Files:**
- Modify: `Scout/PerFileItems/Views/ItemPriorityPill.swift`
- Test: `ScoutTests/PerFile/PerFileTabConfigTests.swift` (per-tab priority vocab)

**Interfaces:**
- Produces: `ItemPriorityPill(priority:options:onSelect:)` where `options: [ItemPriority] = []` and `onSelect: ((ItemPriority) -> Void)? = nil`. With empty `options` (default) it renders exactly as today (static capsule), so the existing call site keeps compiling.

- [ ] **Step 1: Write the failing config test**

Add to `PerFileTabConfigTests.swift` (the existing suite):

```swift
@Test func priorityVocabPerTab() {
    #expect(PerFileTabConfig.research.priorities == [.urgent, .high, .medium, .low])
    #expect(PerFileTabConfig.wishlist.priorities == [.high, .medium, .low])
    #expect(!PerFileTabConfig.wishlist.priorities.contains(.urgent))  // wishlist has no urgent
}
```

- [ ] **Step 2: Run it, verify it passes or fails**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PerFileTabConfigTests 2>&1 | grep -iE "Test run with|TEST (SUCCEEDED|FAILED)"`
Expected: PASS (this asserts existing config — it documents the vocab the menu will use). If it FAILS, the config changed; stop and reconcile.

- [ ] **Step 3: Make the pill optionally a menu**

Replace `ItemPriorityPill.swift` body with:

```swift
import SwiftUI

/// Small color-coded capsule for a per-file item's priority. Read-only by
/// default; when `options` is non-empty it becomes a Menu for changing the
/// priority (issue #41).
struct ItemPriorityPill: View {
    let priority: ItemPriority
    var options: [ItemPriority] = []
    var onSelect: ((ItemPriority) -> Void)? = nil

    var body: some View {
        if options.isEmpty || onSelect == nil {
            capsule
        } else {
            Menu {
                ForEach(options, id: \.self) { opt in
                    Button {
                        if opt != priority { onSelect?(opt) }
                    } label: {
                        if opt == priority { Label(opt.displayName, systemImage: "checkmark") }
                        else { Text(opt.displayName) }
                    }
                }
            } label: {
                capsule
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Change priority")
        }
    }

    private var capsule: some View {
        Text(priority.displayName.uppercased())
            .font(DS.sans(10, weight: .semibold))
            .tracking(0.06 * 10)
            .foregroundStyle(tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Capsule().fill(tint.opacity(0.14)))
            .overlay(Capsule().strokeBorder(tint.opacity(0.35), lineWidth: 0.5))
            .fixedSize()
    }

    private var tint: Color {
        switch priority {
        case .urgent: return DS.Priority.urgent
        case .high:   return DS.Priority.todo
        case .medium: return DS.Priority.watch
        case .low:    return DS.Ink.p3
        }
    }
}
```

- [ ] **Step 4: Build to verify it compiles (existing read-only call site still binds via defaults)**

Run: `xcodebuild build -scheme Scout -configuration Debug -destination 'platform=macOS' 2>&1 | grep -iE "error:|BUILD (SUCCEEDED|FAILED)"`
Expected: BUILD SUCCEEDED.

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/Views/ItemPriorityPill.swift ScoutTests/PerFile/PerFileTabConfigTests.swift
git commit -m "feat(perfile): ItemPriorityPill optional editable menu (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Wire editing through the card and list (Start / Reopen / priority)

**Files:**
- Modify: `Scout/PerFileItems/Views/PerFileItemCardView.swift`
- Modify: `Scout/PerFileItems/Views/PerFileListView.swift` (call sites ~lines 123, 155; add `changePriority`/`changeStatus`)

**Interfaces:**
- Consumes: `ItemPriorityPill(priority:options:onSelect:)` (Task 4); `writer.setPriority(_:fileURL:label:)` (Task 2); `writer.setStatus(_:fileURL:label:)` (Task 3); existing `config.priorities`, `docService.reload()`.
- Produces: `PerFileItemCardView(item:optionalLabel:priorityOptions:onChangePriority:onChangeStatus:onResolve:)`.

- [ ] **Step 1: Rewrite the card with the new callbacks + Start/Reopen + editable pill**

Replace `PerFileItemCardView.swift` with:

```swift
import SwiftUI

/// One per-file Wishlist/Research item as an editorial card. Active items can
/// change priority (pill menu), Start (→ in-progress), or resolve (Done/Drop);
/// resolved items can Reopen (issue #41). Owns its busy + error state.
struct PerFileItemCardView: View {
    let item: PerFileItem
    let optionalLabel: String?
    /// Priorities offered in the pill menu (empty → read-only pill).
    var priorityOptions: [ItemPriority] = []
    var onChangePriority: @MainActor (ItemPriority) async throws -> Void = { _ in }
    var onChangeStatus: @MainActor (ItemStatus) async throws -> Void = { _ in }
    let onResolve: @MainActor (ItemResolution) async throws -> Void

    @State private var isWriting = false
    @State private var errorText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if let label = optionalLabel, let value = optionalValue, !value.isEmpty {
                Text("\(label): \(value)")
                    .font(DS.mono(11))
                    .foregroundStyle(DS.Ink.p4)
            }
            if !item.bodyBlocks.isEmpty {
                MarkdownBodyView(blocks: item.bodyBlocks)
            }
            actions
            if let errorText {
                Label(errorText, systemImage: "exclamationmark.triangle.fill")
                    .font(DS.sans(11))
                    .foregroundStyle(DS.Status.err)
            }
        }
        .editorialCard(padding: 18)
    }

    // MARK: - Header

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                if !item.date.isEmpty {
                    Text(item.date).font(DS.mono(11)).foregroundStyle(DS.Ink.p4)
                }
                Text(item.title)
                    .font(DS.serif(17, weight: .medium))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            if item.isActive && !priorityOptions.isEmpty {
                ItemPriorityPill(priority: item.priority, options: priorityOptions) { newPriority in
                    perform { try await onChangePriority(newPriority) }
                }
                .disabled(isWriting)
            } else {
                ItemPriorityPill(priority: item.priority)
            }
            ItemStatusPill(status: item.status)
        }
    }

    private var optionalValue: String? { item.source ?? item.area }

    // MARK: - Actions

    @ViewBuilder
    private var actions: some View {
        HStack(spacing: 6) {
            if item.isActive {
                if item.status == .open {
                    actionButton("Start", systemImage: "play.fill", tint: DS.Ink.p2) {
                        try await onChangeStatus(.inProgress)
                    }
                }
                actionButton("Done", systemImage: "checkmark", tint: DS.Status.ok) {
                    try await onResolve(.done)
                }
                actionButton("Drop", systemImage: "xmark", tint: DS.Ink.p3) {
                    try await onResolve(.dropped)
                }
            } else {
                actionButton("Reopen", systemImage: "arrow.uturn.backward", tint: DS.Ink.p2) {
                    try await onChangeStatus(.open)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.top, 2)
    }

    @ViewBuilder
    private func actionButton(_ label: String, systemImage: String, tint: Color,
                              _ op: @escaping @MainActor () async throws -> Void) -> some View {
        Button { perform(op) } label: {
            HStack(spacing: 5) {
                Image(systemName: systemImage).font(.system(size: 10))
                Text(label).font(DS.sans(11.5, weight: .medium))
            }
            .foregroundStyle(tint)
            .padding(.horizontal, 12)
            .frame(height: 26)
            .background {
                RoundedRectangle(cornerRadius: 5)
                    .fill(DS.Paper.raised)
                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(DS.Rule.hard, lineWidth: 0.5))
            }
        }
        .buttonStyle(.plainHit)
        .disabled(isWriting)
        .onHover { hovering in
            if hovering, !isWriting { NSCursor.pointingHand.push() } else { NSCursor.pop() }
        }
    }

    private func perform(_ op: @escaping @MainActor () async throws -> Void) {
        isWriting = true
        errorText = nil
        Task {
            do { try await op() }
            catch { errorText = "Couldn't update the file — \(error.localizedDescription)" }
            isWriting = false
        }
    }
}
```

- [ ] **Step 2: Wire the list call sites + add writer actions**

In `PerFileListView.swift`, replace the awaiting-items `ForEach` (~line 122) and the resolved-section `ForEach` (~line 154):

```swift
            ForEach(awaiting) { item in
                PerFileItemCardView(
                    item: item,
                    optionalLabel: config.optionalField.label,
                    priorityOptions: config.priorities,
                    onChangePriority: { try await changePriority(item, $0) },
                    onChangeStatus: { try await changeStatus(item, $0) },
                    onResolve: { try await resolve(item, $0) }
                )
            }
```

```swift
            if resolvedExpanded {
                ForEach(resolved) { item in
                    PerFileItemCardView(
                        item: item,
                        optionalLabel: config.optionalField.label,
                        onChangeStatus: { try await changeStatus(item, $0) },
                        onResolve: { _ in }
                    )
                }
            }
```

In the `// MARK: - Actions` section, add alongside `resolve(_:_:)`:

```swift
    private func changePriority(_ item: PerFileItem, _ priority: ItemPriority) async throws {
        try await writerBox.writer.setPriority(priority, fileURL: item.fileURL, label: item.title)
        docService.reload()
    }

    private func changeStatus(_ item: PerFileItem, _ status: ItemStatus) async throws {
        try await writerBox.writer.setStatus(status, fileURL: item.fileURL, label: item.title)
        docService.reload()
    }
```

- [ ] **Step 3: Build + run the full suite**

Run: `xcodebuild test -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests 2>&1 | grep -iE "error:|✘|Test run with [0-9]+ tests|TEST (SUCCEEDED|FAILED)"`
Expected: BUILD/TEST SUCCEEDED, `Test run with 363 tests` (357 baseline + 3 pure + 3 E2E in the writer; +the 1 config test → 364 total if counting; the exact count rises by the tests added in Tasks 1–4 — confirm no failures rather than the precise number).

- [ ] **Step 4: Commit**

```bash
git add Scout/PerFileItems/Views/PerFileItemCardView.swift Scout/PerFileItems/Views/PerFileListView.swift
git commit -m "feat(perfile): editable priority + Start/Reopen on cards (#41)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Verify in the running app (manual, optional but recommended)**

Build Debug, launch, open the Wishlist tab: tap a priority pill → menu changes priority and the card re-sorts; an `open` item shows **Start** → moves to in-progress; a resolved item shows **Reopen** → returns to the awaiting list. (Do not perform write actions against the live `~/Scout` vault if Scout sessions are running concurrently; use a throwaway item.)

---

## Self-Review

**Spec coverage:**
- Priority editing (pill→menu, active-only, per-tab vocab) → Tasks 4 + 5. ✓
- Start (open→in-progress) → Task 3 (`setStatus`) + Task 5 (button). ✓
- Reopen (done/dropped→open) → Task 3 + Task 5 (resolved section button). ✓
- `resolve` refactored to one write path → Task 3. ✓
- Field-missing throws → Task 1 (`fieldNotFound`). ✓
- Re-select current value = no-op → inherited from `GuardedFileWrite` (`didWrite == false`), card treats as success (no error). ✓
- Scoped commits / race guard → reused via `performFieldWrite`. ✓
- #43 work-history → explicitly out of scope. ✓

**Placeholder scan:** none — every code step shows full code.

**Type consistency:** `rewriteFrontmatterField`, `performFieldWrite`, `setPriority`, `setStatus`, `statusCommitMessage`, `PerFileItemCardView(item:optionalLabel:priorityOptions:onChangePriority:onChangeStatus:onResolve:)`, `ItemPriorityPill(priority:options:onSelect:)` are used identically across the tasks that define and consume them. `ItemStatus`/`ItemPriority` cases match the model (`Models/ItemStatus.swift`, `Models/ItemPriority.swift`).
