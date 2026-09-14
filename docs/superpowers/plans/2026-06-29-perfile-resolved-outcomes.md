# Wishlist/Research Resolved-Item Outcomes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-item, git-derived activity timeline (with per-commit diffs and a link to the resolving Scout run) to the Wishlist/Research tabs, shown in a Control-Center-style detail pane.

**Architecture:** All data is derived from git — no scout-plugin changes. `GitService` gains a `git log --follow` per-file history and a single-commit diff. A pure `CommitRunLinker` maps each commit back to a Scout `Run` (the reverse of `SessionLogService.commits(for:)`'s time-window + subject-prefix heuristic). A `PerFileItemActivityModel` loads commits + diffs lazily off-main. `PerFileItemDetailView` renders the timeline in a `.side`/`.full` detail panel that mirrors `ControlCenterView`'s existing pattern.

**Tech Stack:** Swift 5.x, SwiftUI (macOS), Swift Testing (`import Testing`, `@Suite`/`@Test`/`#expect`). Tests inject the existing `ProcessRunner` seam via the module-level `ScriptedRunner` test double.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-06-28-perfile-resolved-outcomes-design.md`. Issue #43.
- **No scout-plugin changes; no new frontmatter.** v1 is app-side, git-derived only.
- **Reverse run-link rule (verbatim):** a commit links to the run with the latest `startedAt` among runs where `commit.timestamp ∈ [startedAt − 30s … (endedAt ?? now) + 300s]` **and** `run.type.commitsPrefix` is non-empty **and** `commit.subject.hasPrefix(run.type.commitsPrefix)`. `.manual` runs (empty prefix) never link in reverse — prefer a false-negative "you" over a false-positive run attribution. This mirrors `SessionLogService.commits(for:)` (`start = startedAt − 30`, `end = (endedAt ?? now) + 5*60`).
- **The diff is always shown regardless of the run link** — a missing/wrong run badge must never hide the actual changes.
- **Errors are surfaced, never swallowed** (the #47 lesson): git failures become a visible `.failed` state, not a silent empty list.
- **New `.swift` files under `Scout/` and `ScoutTests/` auto-compile** (synchronized file groups) — no `.pbxproj` edits.
- **Build:** `xcodebuild build -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
- **Test (single suite):** `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<SuiteStructName> CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
  - `<SuiteStructName>` is the **struct name**, not the `@Suite("…")` display string. A wrong selector runs **zero** tests and reports green — confirm the run shows your tests executing.
- **Test (full target, for final verification):** same command with `-only-testing:ScoutTests`.
- **Commit trailer (every commit):**
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **Branch:** `feat/perfile-resolved-outcomes-43` (already exists; the spec commit is on it).

---

## File Structure

**Create:**
- `Scout/Services/CommitRunLinker.swift` — pure reverse commit→run mapping (colocated with `SessionLogService`).
- `Scout/PerFileItems/Models/ItemActivityEntry.swift` — `ItemActivityEntry` model + `ItemActivity.entries(...)` pure builder.
- `Scout/PerFileItems/PerFileItemActivityModel.swift` — `@MainActor ObservableObject`; lazy commit + diff loading.
- `Scout/PerFileItems/Views/CommitDiffView.swift` — scrollable monospace diff with +/− coloring.
- `Scout/PerFileItems/Views/PerFileItemDetailView.swift` — detail-pane header + outcome summary + timeline.
- `ScoutTests/Services/GitServiceFileHistoryTests.swift`
- `ScoutTests/Services/CommitRunLinkerTests.swift`
- `ScoutTests/PerFile/ItemActivityTests.swift`
- `ScoutTests/PerFile/PerFileItemActivityModelTests.swift`

**Modify:**
- `Scout/Services/GitService.swift` — add `commits(touchingFile:)` and `diff(forCommit:)`.
- `Scout/PerFileItems/Views/PerFileItemCardView.swift` — add `isSelected` + `onSelect`.
- `Scout/PerFileItems/Views/PerFileListView.swift` — add `.side`/`.full` detail state + side panel; thread selection into cards.
- `Scout/Shell/AppState.swift` — add `pendingRunToOpen`, `requestedSidebar`, `requestOpenRun(_:)`.
- `Scout/Shell/MainWindowView.swift` — observe `requestedSidebar` → switch sidebar selection.
- `Scout/ControlCenter/ControlCenterView.swift` — observe `pendingRunToOpen` → open that run's `.side` detail.

**Reuse (do not redefine):**
- `ScriptedRunner` — module-level `ProcessRunner` double defined at the bottom of `ScoutTests/Services/GitServiceCommitPathsTests.swift`. Reference it directly; redefining it causes an "invalid redeclaration" build error.
- `Run.make(...)` — `#if DEBUG` test factory in `Scout/Models/Run.swift`.

---

### Task 1: GitService per-file history + single-commit diff

**Files:**
- Modify: `Scout/Services/GitService.swift` (add two methods after `diff(from:to:)`, ~line 73)
- Test: `ScoutTests/Services/GitServiceFileHistoryTests.swift` (create)

**Interfaces:**
- Consumes: existing private `GitService.parse(gitLogOutput:prefix:)`, `GitServiceError.gitExitNonZero(Int)`, the `ProcessRunner` `runner`, `repoURL`, and the `Commit` model.
- Produces:
  - `func commits(touchingFile fileURL: URL) async throws -> [Commit]`
  - `func diff(forCommit sha: String) async throws -> String`

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/Services/GitServiceFileHistoryTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("GitService file history")
struct GitServiceFileHistoryTests {

    @Test func commitsTouchingFileParsesLogWithFollow() async throws {
        let RS = "\u{1E}"
        let log = [
            "aaa111\(RS)aaa\(RS)1700000000\(RS)dreaming: ship oauth",
            " 3 files changed, 88 insertions(+), 12 deletions(-)",
            "bbb222\(RS)bbb\(RS)1699990000\(RS)app: start oauth",
            " 1 file changed, 2 insertions(+)"
        ].joined(separator: "\n") + "\n"
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(log.utf8), stderr: Data())
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/Scout"), runner: runner)

        let commits = try await git.commits(touchingFile: URL(fileURLWithPath: "/tmp/Scout/wishlist/x.md"))

        #expect(commits.count == 2)
        #expect(commits[0].subject == "dreaming: ship oauth")
        #expect(commits[0].insertions == 88)
        #expect(commits[0].deletions == 12)
        let call = runner.calls[0]
        #expect(call.arguments.contains("--follow"))
        #expect(call.arguments.contains("--"))
        #expect(call.arguments.contains("/tmp/Scout/wishlist/x.md"))
    }

    @Test func commitsTouchingFileThrowsOnGitError() async {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 128, stdout: Data(), stderr: Data())
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/Scout"), runner: runner)
        await #expect(throws: GitServiceError.self) {
            _ = try await git.commits(touchingFile: URL(fileURLWithPath: "/tmp/Scout/x.md"))
        }
    }

    @Test func diffForCommitReturnsPatch() async throws {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data("+a\n-b\n".utf8), stderr: Data())
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/Scout"), runner: runner)
        let patch = try await git.diff(forCommit: "aaa111")
        #expect(patch == "+a\n-b\n")
        #expect(runner.calls[0].arguments.contains("show"))
        #expect(runner.calls[0].arguments.contains("aaa111"))
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/GitServiceFileHistoryTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: FAIL — compile error, `value of type 'GitService' has no member 'commits(touchingFile:)'`.

- [ ] **Step 3: Write the implementation**

In `Scout/Services/GitService.swift`, after the `diff(from:to:)` method (before `private func parse`), add:

```swift
    /// Full commit history of a single file, newest first, following renames
    /// (`git log --follow`). Used by the per-item activity timeline (#43).
    /// Reuses the same record format + `parse` as `commits(between:and:matchingPrefix:)`,
    /// with no subject filter. The pathspec is absolute — valid with `-C <repo>`.
    func commits(touchingFile fileURL: URL) async throws -> [Commit] {
        let sep = "\u{1E}"
        let format = ["%H", "%h", "%ct", "%s"].joined(separator: sep)
        let args = [
            "-C", repoURL.path,
            "log", "--follow",
            "--format=\(format)",
            "--shortstat",
            "--", fileURL.path
        ]
        let result = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git"] + args,
            environment: [:],
            workingDirectory: repoURL
        )
        guard result.exitCode == 0 else {
            throw GitServiceError.gitExitNonZero(Int(result.exitCode))
        }
        let text = String(data: result.stdout, encoding: .utf8) ?? ""
        return parse(gitLogOutput: text, prefix: "")
    }

    /// The patch a single commit introduced (`git show --format= --patch <sha>`).
    /// `--format=` strips the commit header so the output is just the diff.
    /// `git show` handles root commits (no parent), unlike a `<sha>^..<sha>` range.
    func diff(forCommit sha: String) async throws -> String {
        let result = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git", "-C", repoURL.path, "show", "--format=", "--patch", sha],
            environment: [:],
            workingDirectory: repoURL
        )
        guard result.exitCode == 0 else {
            throw GitServiceError.gitExitNonZero(Int(result.exitCode))
        }
        return String(data: result.stdout, encoding: .utf8) ?? ""
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/GitServiceFileHistoryTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: PASS — 3 tests run and succeed. (Confirm it says 3 tests executed, not 0.)

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/GitService.swift ScoutTests/Services/GitServiceFileHistoryTests.swift
git commit -m "feat(perfile): GitService per-file history + single-commit diff (#43)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: CommitRunLinker (reverse commit→run mapping)

**Files:**
- Create: `Scout/Services/CommitRunLinker.swift`
- Test: `ScoutTests/Services/CommitRunLinkerTests.swift`

**Interfaces:**
- Consumes: `Commit`, `Run`, `RunType.commitsPrefix`.
- Produces: `enum CommitRunLinker { static func run(for commit: Commit, in runs: [Run], now: Date) -> Run? }`

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/Services/CommitRunLinkerTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("CommitRunLinker")
struct CommitRunLinkerTests {

    private func commit(at ts: TimeInterval, subject: String) -> Commit {
        Commit(id: "\(ts)", shortSHA: "s", timestamp: Date(timeIntervalSince1970: ts),
               subject: subject, filesChanged: 0, insertions: 0, deletions: 0)
    }

    private let start = Date(timeIntervalSince1970: 1_000_000)
    private var now: Date { Date(timeIntervalSince1970: 2_000_000) }

    @Test func linksInWindowMatchingPrefix() {
        let run = Run.make(type: .dreaming, startedAt: start, endedAt: start.addingTimeInterval(600))
        let c = commit(at: 1_000_300, subject: "dreaming: ship oauth")
        #expect(CommitRunLinker.run(for: c, in: [run], now: now)?.id == run.id)
    }

    @Test func noLinkForAppCommit() {
        let run = Run.make(type: .dreaming, startedAt: start, endedAt: start.addingTimeInterval(600))
        let c = commit(at: 1_000_300, subject: "app: mark X done")
        #expect(CommitRunLinker.run(for: c, in: [run], now: now) == nil)
    }

    @Test func noLinkOutOfWindow() {
        let run = Run.make(type: .dreaming, startedAt: start, endedAt: start.addingTimeInterval(600))
        let c = commit(at: 1_099_999, subject: "dreaming: late")
        #expect(CommitRunLinker.run(for: c, in: [run], now: now) == nil)
    }

    @Test func manualRunNeverLinks() {
        let run = Run.make(type: .manual, startedAt: start, endedAt: start.addingTimeInterval(600))
        let c = commit(at: 1_000_300, subject: "anything goes")
        #expect(CommitRunLinker.run(for: c, in: [run], now: now) == nil)
    }

    @Test func picksMostRecentMatchingRun() {
        let older = Run.make(type: .research, startedAt: start, endedAt: start.addingTimeInterval(3600))
        let newer = Run.make(type: .research, startedAt: start.addingTimeInterval(100),
                             endedAt: start.addingTimeInterval(3600))
        let c = commit(at: 1_000_200, subject: "research: found it")
        #expect(CommitRunLinker.run(for: c, in: [older, newer], now: now)?.id == newer.id)
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/CommitRunLinkerTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: FAIL — `cannot find 'CommitRunLinker' in scope`.

- [ ] **Step 3: Write the implementation**

Create `Scout/Services/CommitRunLinker.swift`:

```swift
import Foundation

/// Maps a commit back to the Scout `Run` that produced it — the reverse of
/// `SessionLogService.commits(for:)`. A commit links to a run when its
/// timestamp falls inside that run's commit window (the same window the
/// forward mapping builds: `startedAt − 30s` through `(endedAt ?? now) + 5min`)
/// AND its subject carries the run type's commit prefix.
///
/// `.manual` runs have an empty prefix and never link in reverse: matching on
/// time alone would let in-app/manual commits ("app: …") get mis-attributed to
/// a coincidentally-overlapping run. We prefer a false-negative ("you") over a
/// false-positive run attribution — the diff is shown either way (#43).
enum CommitRunLinker {
    static func run(for commit: Commit, in runs: [Run], now: Date) -> Run? {
        runs
            .filter { run in
                let prefix = run.type.commitsPrefix
                guard !prefix.isEmpty, commit.subject.hasPrefix(prefix) else { return false }
                let windowStart = run.startedAt.addingTimeInterval(-30)
                let windowEnd = (run.endedAt ?? now).addingTimeInterval(5 * 60)
                return commit.timestamp >= windowStart && commit.timestamp <= windowEnd
            }
            .max { $0.startedAt < $1.startedAt }
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/CommitRunLinkerTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: PASS — 5 tests run and succeed.

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/CommitRunLinker.swift ScoutTests/Services/CommitRunLinkerTests.swift
git commit -m "feat(perfile): CommitRunLinker — reverse commit→run mapping (#43)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: ItemActivityEntry model + pure builder

**Files:**
- Create: `Scout/PerFileItems/Models/ItemActivityEntry.swift`
- Test: `ScoutTests/PerFile/ItemActivityTests.swift`

**Interfaces:**
- Consumes: `Commit`, `Run`, `CommitRunLinker.run(for:in:now:)` (Task 2).
- Produces:
  - `struct ItemActivityEntry: Identifiable, Equatable, Sendable { let commit: Commit; let run: Run?; let isResolving: Bool; var id: String { commit.id } }`
  - `enum ItemActivity { static func entries(commits: [Commit], runs: [Run], itemIsActive: Bool, now: Date) -> [ItemActivityEntry] }`

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/PerFile/ItemActivityTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("ItemActivity")
struct ItemActivityTests {

    private func c(_ id: String, _ subject: String) -> Commit {
        Commit(id: id, shortSHA: id, timestamp: Date(timeIntervalSince1970: 1),
               subject: subject, filesChanged: 0, insertions: 0, deletions: 0)
    }

    @Test func newestCommitIsResolvingForResolvedItem() {
        let entries = ItemActivity.entries(
            commits: [c("a", "x"), c("b", "y")], runs: [], itemIsActive: false, now: Date())
        #expect(entries.count == 2)
        #expect(entries[0].isResolving)
        #expect(!entries[1].isResolving)
    }

    @Test func activeItemHasNoResolvingCommit() {
        let entries = ItemActivity.entries(
            commits: [c("a", "x")], runs: [], itemIsActive: true, now: Date())
        #expect(!entries[0].isResolving)
    }

    @Test func emptyCommitsYieldsEmpty() {
        #expect(ItemActivity.entries(
            commits: [], runs: [], itemIsActive: false, now: Date()).isEmpty)
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ItemActivityTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: FAIL — `cannot find 'ItemActivity' in scope`.

- [ ] **Step 3: Write the implementation**

Create `Scout/PerFileItems/Models/ItemActivityEntry.swift`:

```swift
import Foundation

/// One commit in a per-item activity timeline, with the Scout run that made it
/// (nil → in-app/manual "you") and whether it's the commit that resolved the
/// item. (#43)
struct ItemActivityEntry: Identifiable, Equatable, Sendable {
    let commit: Commit
    let run: Run?
    let isResolving: Bool
    var id: String { commit.id }
}

enum ItemActivity {
    /// Build timeline entries from a file's commits (newest first, as
    /// `git log` returns them). For a resolved item the newest commit is the
    /// one that set the terminal status, so it's flagged `isResolving`.
    static func entries(commits: [Commit], runs: [Run], itemIsActive: Bool, now: Date) -> [ItemActivityEntry] {
        commits.enumerated().map { index, commit in
            ItemActivityEntry(
                commit: commit,
                run: CommitRunLinker.run(for: commit, in: runs, now: now),
                isResolving: !itemIsActive && index == 0
            )
        }
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/ItemActivityTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: PASS — 3 tests run and succeed.

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/Models/ItemActivityEntry.swift ScoutTests/PerFile/ItemActivityTests.swift
git commit -m "feat(perfile): ItemActivityEntry model + pure timeline builder (#43)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: PerFileItemActivityModel (lazy load + diff)

**Files:**
- Create: `Scout/PerFileItems/PerFileItemActivityModel.swift`
- Test: `ScoutTests/PerFile/PerFileItemActivityModelTests.swift`

**Interfaces:**
- Consumes: `GitService.commits(touchingFile:)` + `diff(forCommit:)` (Task 1), `ItemActivity.entries(...)` (Task 3), `PerFileItem`, `Run`, `Commit`.
- Produces (all `@MainActor`):
  - `final class PerFileItemActivityModel: ObservableObject`
  - `enum LoadState: Equatable { case loading; case loaded([ItemActivityEntry]); case failed(String) }`
  - `enum DiffState: Equatable { case loading; case loaded(String); case failed(String) }`
  - `@Published private(set) var state: LoadState`
  - `@Published private(set) var diffs: [String: DiffState]`
  - `init(item: PerFileItem, git: GitService, runs: [Run], now: @escaping @Sendable () -> Date = { Date() })`
  - `func load() async`
  - `func loadDiff(for commit: Commit) async`

- [ ] **Step 1: Write the failing test**

Create `ScoutTests/PerFile/PerFileItemActivityModelTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@MainActor
@Suite("PerFileItemActivityModel")
struct PerFileItemActivityModelTests {

    private func makeItem(status: ItemStatus) -> PerFileItem {
        PerFileItem(
            fileURL: URL(fileURLWithPath: "/tmp/Scout/wishlist/x.md"),
            date: "2026-06-01", title: "X", status: status, priority: .high,
            source: nil, area: nil, bodyMarkdown: "")
    }

    @Test func loadParsesCommitsAndFlagsResolving() async {
        let RS = "\u{1E}"
        let log = "aaa\(RS)aaa\(RS)1700000000\(RS)dreaming: ship\n 2 files changed, 5 insertions(+)\n"
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(log.utf8), stderr: Data())
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/Scout"), runner: runner)
        let model = PerFileItemActivityModel(
            item: makeItem(status: .done), git: git, runs: [],
            now: { Date(timeIntervalSince1970: 1_700_000_100) })

        await model.load()

        guard case .loaded(let entries) = model.state else {
            Issue.record("expected .loaded, got \(model.state)"); return
        }
        #expect(entries.count == 1)
        #expect(entries[0].isResolving)   // item is .done
    }

    @Test func loadSetsFailedOnGitError() async {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 128, stdout: Data(), stderr: Data())
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/Scout"), runner: runner)
        let model = PerFileItemActivityModel(item: makeItem(status: .open), git: git, runs: [])

        await model.load()

        if case .failed = model.state { } else {
            Issue.record("expected .failed, got \(model.state)")
        }
    }

    @Test func loadDiffStoresPatch() async {
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data("+added\n".utf8), stderr: Data())
        ])
        let git = GitService(repoURL: URL(fileURLWithPath: "/tmp/Scout"), runner: runner)
        let model = PerFileItemActivityModel(item: makeItem(status: .done), git: git, runs: [])
        let c = Commit(id: "aaa", shortSHA: "aaa", timestamp: Date(timeIntervalSince1970: 1),
                       subject: "x", filesChanged: 0, insertions: 0, deletions: 0)

        await model.loadDiff(for: c)

        #expect(model.diffs["aaa"] == .loaded("+added\n"))
    }
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PerFileItemActivityModelTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: FAIL — `cannot find 'PerFileItemActivityModel' in scope`.

- [ ] **Step 3: Write the implementation**

Create `Scout/PerFileItems/PerFileItemActivityModel.swift`:

```swift
import Foundation
import Combine

/// Loads a single item's git activity (commits + per-commit diffs) lazily and
/// off the main thread, for `PerFileItemDetailView`. One instance per opened
/// item. Errors surface as `.failed` rather than an empty list (#47 lesson).
@MainActor
final class PerFileItemActivityModel: ObservableObject {

    enum LoadState: Equatable {
        case loading
        case loaded([ItemActivityEntry])
        case failed(String)
    }

    enum DiffState: Equatable {
        case loading
        case loaded(String)
        case failed(String)
    }

    @Published private(set) var state: LoadState = .loading
    @Published private(set) var diffs: [String: DiffState] = [:]

    private let item: PerFileItem
    private let git: GitService
    private let runs: [Run]
    private let now: @Sendable () -> Date

    init(item: PerFileItem, git: GitService, runs: [Run],
         now: @escaping @Sendable () -> Date = { Date() }) {
        self.item = item
        self.git = git
        self.runs = runs
        self.now = now
    }

    func load() async {
        do {
            let commits = try await git.commits(touchingFile: item.fileURL)
            state = .loaded(ItemActivity.entries(
                commits: commits, runs: runs, itemIsActive: item.isActive, now: now()))
        } catch {
            state = .failed("Couldn't load activity — \(error.localizedDescription)")
        }
    }

    func loadDiff(for commit: Commit) async {
        if case .loaded = diffs[commit.id] { return }   // already fetched
        diffs[commit.id] = .loading
        do {
            diffs[commit.id] = .loaded(try await git.diff(forCommit: commit.id))
        } catch {
            diffs[commit.id] = .failed("Couldn't load diff — \(error.localizedDescription)")
        }
    }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/PerFileItemActivityModelTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: PASS — 3 tests run and succeed.

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/PerFileItemActivityModel.swift ScoutTests/PerFile/PerFileItemActivityModelTests.swift
git commit -m "feat(perfile): PerFileItemActivityModel — lazy commit/diff loading (#43)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: CommitDiffView + PerFileItemDetailView

> **No unit tests** — SwiftUI views aren't unit-tested in this codebase (services + models carry the tests). Verified by build + the manual smoke test at the end of the plan.

**Files:**
- Create: `Scout/PerFileItems/Views/CommitDiffView.swift`
- Create: `Scout/PerFileItems/Views/PerFileItemDetailView.swift`

**Interfaces:**
- Consumes: `PerFileItemActivityModel` (Task 4), `ItemActivityEntry`/`ItemActivity` (Task 3), `AppState.gitService` + `AppState.sessionLogService.runs` + `AppState.requestOpenRun(_:)` (the last lands in Task 7 — guard its call site so this task builds standalone, see Step 2), `ItemStatusPill`, `ItemPriorityPill`, `Run.displayName`, design tokens (`DS.serif/sans/mono`, `DS.Ink`, `DS.Paper`, `DS.Rule`, `DS.Status`), `EditorialRule`, `.plainHit`.
- Produces:
  - `struct CommitDiffView: View` — `init(patch: String)`
  - `struct PerFileItemDetailView: View` — `init(item: PerFileItem, git: GitService, runs: [Run])`

- [ ] **Step 1: Create `CommitDiffView`**

Create `Scout/PerFileItems/Views/CommitDiffView.swift`:

```swift
import SwiftUI

/// Scrollable monospace rendering of a unified diff with +/− line coloring.
struct CommitDiffView: View {
    let patch: String

    private var lines: [Substring] { patch.split(separator: "\n", omittingEmptySubsequences: false) }

    var body: some View {
        ScrollView([.vertical, .horizontal]) {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(line.isEmpty ? " " : String(line))
                        .font(DS.mono(11))
                        .foregroundStyle(color(for: line))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
            }
            .padding(10)
        }
        .frame(maxHeight: 320)
        .background(DS.Paper.base)
        .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        .clipShape(RoundedRectangle(cornerRadius: 6))
    }

    private func color(for line: Substring) -> Color {
        if line.hasPrefix("+") && !line.hasPrefix("+++") { return DS.Status.ok }
        if line.hasPrefix("-") && !line.hasPrefix("---") { return DS.Status.err }
        if line.hasPrefix("@@") { return DS.Ink.p3 }
        return DS.Ink.p2
    }
}
```

- [ ] **Step 2: Create `PerFileItemDetailView`**

Create `Scout/PerFileItems/Views/PerFileItemDetailView.swift`:

```swift
import SwiftUI

/// The per-item activity/outcome timeline shown in the Wishlist/Research detail
/// pane (#43). Resolved items lead with an outcome summary; every item lists the
/// commits touching its file, each labeled with its Scout run (or "you") and
/// expandable to its diff.
struct PerFileItemDetailView: View {
    let item: PerFileItem
    @StateObject private var model: PerFileItemActivityModel
    @EnvironmentObject private var appState: AppState
    @State private var expanded: Set<String> = []

    init(item: PerFileItem, git: GitService, runs: [Run]) {
        self.item = item
        _model = StateObject(wrappedValue: PerFileItemActivityModel(item: item, git: git, runs: runs))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                switch model.state {
                case .loading:
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
                case .failed(let message):
                    Label(message, systemImage: "exclamationmark.triangle.fill")
                        .font(DS.sans(12)).foregroundStyle(DS.Status.err)
                case .loaded(let entries):
                    if entries.isEmpty {
                        Text("No activity yet — this item hasn't been committed.")
                            .font(DS.serif(14)).foregroundStyle(DS.Ink.p3).padding(.top, 24)
                    } else {
                        if let outcome = outcomeSummary(entries) {
                            Text(outcome).font(DS.sans(12, weight: .medium)).foregroundStyle(DS.Ink.p2)
                        }
                        ForEach(entries) { entry in row(entry) }
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .task { await model.load() }
    }

    // MARK: - Outcome summary (resolved items)

    private func outcomeSummary(_ entries: [ItemActivityEntry]) -> String? {
        guard let resolving = entries.first(where: \.isResolving) else { return nil }
        let who = resolving.run?.type.displayName ?? "you"
        return "Resolved by \(who) · \(Self.dateText(resolving.commit.timestamp))"
    }

    // MARK: - Timeline row

    @ViewBuilder
    private func row(_ entry: ItemActivityEntry) -> some View {
        let isOpen = expanded.contains(entry.commit.id)
        VStack(alignment: .leading, spacing: 6) {
            Button {
                toggle(entry.commit)
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: isOpen ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold)).foregroundStyle(DS.Ink.p4)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(sourceLabel(entry)).font(DS.sans(11, weight: .semibold))
                                .foregroundStyle(DS.Ink.p3)
                            Text(Self.dateText(entry.commit.timestamp)).font(DS.mono(10))
                                .foregroundStyle(DS.Ink.p4)
                            if entry.isResolving {
                                Text("outcome").font(DS.mono(10)).foregroundStyle(DS.Status.ok)
                            }
                        }
                        Text(entry.commit.subject).font(DS.serif(13)).foregroundStyle(DS.Ink.p1)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(statLine(entry.commit)).font(DS.mono(10)).foregroundStyle(DS.Ink.p4)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)

            if isOpen {
                diffSection(entry.commit)
                if entry.run != nil {
                    Button { openRun(entry) } label: {
                        Label("Open run in Control Center", systemImage: "arrow.up.right.square")
                            .font(DS.sans(11, weight: .medium))
                    }
                    .buttonStyle(.plainHit).foregroundStyle(DS.Ink.p2)
                }
            }
        }
        .padding(.vertical, 6)
        .overlay(alignment: .bottom) { EditorialRule() }
    }

    @ViewBuilder
    private func diffSection(_ commit: Commit) -> some View {
        switch model.diffs[commit.id] {
        case .loaded(let patch): CommitDiffView(patch: patch)
        case .failed(let message):
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .font(DS.sans(11)).foregroundStyle(DS.Status.err)
        default:
            ProgressView().frame(maxWidth: .infinity, alignment: .leading)
                .task { await model.loadDiff(for: commit) }
        }
    }

    // MARK: - Helpers

    private func toggle(_ commit: Commit) {
        if expanded.contains(commit.id) { expanded.remove(commit.id) }
        else { expanded.insert(commit.id) }
    }

    private func openRun(_ entry: ItemActivityEntry) {
        guard let run = entry.run else { return }
        appState.requestOpenRun(run.id)
    }

    private func sourceLabel(_ entry: ItemActivityEntry) -> String {
        entry.run?.type.displayName ?? "you"
    }

    private func statLine(_ commit: Commit) -> String {
        let files = "\(commit.filesChanged) file\(commit.filesChanged == 1 ? "" : "s")"
        return "\(files)  +\(commit.insertions) −\(commit.deletions)"
    }

    private static func dateText(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "MMM d, HH:mm"
        return f.string(from: date)
    }
}
```

> **Note on Step 2:** this view calls `appState.requestOpenRun(run.id)`, which is added in Task 7. To keep Task 5 building standalone, **do Task 7's Step 1 (the AppState additions) before building Task 5**, or temporarily stub the call. The recommended order is: implement Task 5's files, then jump to Task 7 Step 1 (AppState), then build. If you prefer strict task isolation, replace the `openRun` body with `// wired in Task 7` and a no-op, then restore it in Task 7.

- [ ] **Step 3: Add the AppState hook now (from Task 7 Step 1) so this builds**

Apply **Task 7, Step 1** (the `AppState` additions) before building. Then return here.

- [ ] **Step 4: Build**

Run: `xcodebuild build -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/Views/CommitDiffView.swift Scout/PerFileItems/Views/PerFileItemDetailView.swift Scout/Shell/AppState.swift
git commit -m "feat(perfile): activity timeline detail view + diff view (#43)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Card selection + list detail pane

> **No unit tests** — view/layout change. Build-verified + manual smoke test.

**Files:**
- Modify: `Scout/PerFileItems/Views/PerFileItemCardView.swift`
- Modify: `Scout/PerFileItems/Views/PerFileListView.swift`

**Interfaces:**
- Consumes: `PerFileItemDetailView(item:git:runs:)` (Task 5), `AppState.gitService`, `AppState.sessionLogService.runs`.
- Produces: selectable cards (`isSelected`, `onSelect`) + a `.side`/`.full` detail panel in `PerFileListView` mirroring `ControlCenterView`.

- [ ] **Step 1: Make the card selectable**

In `Scout/PerFileItems/Views/PerFileItemCardView.swift`, add two properties after `let onResolve:` (line 13):

```swift
    var isSelected: Bool = false
    var onSelect: (() -> Void)? = nil
```

Then change the `body`'s outer modifier. Replace:

```swift
        .editorialCard(padding: 18)
    }
```

with:

```swift
        .editorialCard(padding: 18)
        .overlay(alignment: .leading) {
            if isSelected {
                RoundedRectangle(cornerRadius: 2).fill(DS.Ink.p1)
                    .frame(width: 3).padding(.vertical, 6)
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { onSelect?() }
    }
```

(The inner `Start`/`Done`/`Drop` buttons and the priority `Menu` consume their own taps, so the card-body `onTapGesture` only fires on the title/body area.)

- [ ] **Step 2: Add detail state + side/full panel to the list**

In `Scout/PerFileItems/Views/PerFileListView.swift`:

(a) Add the environment object + detail state. After `@EnvironmentObject var writerBox: PerFileItemWriterBox` (line ~8) add:

```swift
    @EnvironmentObject var appState: AppState
```

After `@State private var showingAdd = false` (line ~11) add:

```swift
    @State private var detail: DetailPresentation? = nil

    enum DetailPresentation: Equatable {
        case side(PerFileItem)
        case full(PerFileItem)
        var item: PerFileItem {
            switch self { case .side(let i), .full(let i): return i }
        }
        var isFull: Bool { if case .full = self { return true } else { return false } }
    }
```

(b) Replace the whole `body` (the `ScrollView { … }.onAppear { docService.load() }` block, lines ~13–61) with:

```swift
    var body: some View {
        ZStack(alignment: .topTrailing) {
            mainSurface
            if let detail, detail.isFull {
                fullDetail(detail.item).transition(.opacity)
            }
        }
        .background(
            Button("") { detail = nil }
                .keyboardShortcut(".", modifiers: .command)
                .opacity(0).frame(width: 0, height: 0)
        )
        .animation(.easeInOut(duration: 0.18), value: detail)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                HStack(spacing: 4) {
                    Button { showingAdd = true } label: { Image(systemName: "plus") }
                        .help("Add a new \(config.addNoun)")
                    Button {
                        NSWorkspace.shared.activateFileViewerSelecting([docService.directoryURL])
                    } label: { Image(systemName: "folder") }
                        .help("Reveal the \(config.title.lowercased()) folder in Finder")
                }
            }
        }
        .sheet(isPresented: $showingAdd) {
            AddItemSheet(config: config, onSubmit: { title, priority, body, optional in
                try await addItem(title: title, priority: priority, body: body, optional: optional)
            }, onCancel: { showingAdd = false })
        }
        .onAppear { docService.load() }
    }

    @ViewBuilder
    private var mainSurface: some View {
        if case .side(let item) = detail {
            HStack(alignment: .top, spacing: 0) {
                listScroll.frame(maxWidth: .infinity)
                sideDetail(item).frame(width: 460)
            }
        } else {
            listScroll
        }
    }

    private var listScroll: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header
                content
            }
            .frame(maxWidth: 920, alignment: .leading)
            .padding(.horizontal, 42)
            .padding(.top, 28)
            .padding(.bottom, 64)
            .frame(maxWidth: .infinity, alignment: .center)
        }
        .scrollIndicators(.visible)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
    }

    // MARK: - Detail panels (mirror ControlCenterView)

    private func sideDetail(_ item: PerFileItem) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            detailHeader(item, isExpanded: false)
            EditorialRule()
            PerFileItemDetailView(item: item, git: appState.gitService,
                                  runs: appState.sessionLogService.runs)
                .id(item.id)
        }
        .background(
            RoundedRectangle(cornerRadius: 12).fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
        )
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func fullDetail(_ item: PerFileItem) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            detailHeader(item, isExpanded: true)
            EditorialRule()
            PerFileItemDetailView(item: item, git: appState.gitService,
                                  runs: appState.sessionLogService.runs)
                .id(item.id)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
    }

    private func detailHeader(_ item: PerFileItem, isExpanded: Bool) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Button { detail = nil } label: {
                Image(systemName: "chevron.left").font(.system(size: 12, weight: .medium))
            }
            .buttonStyle(.plainHit).foregroundStyle(DS.Ink.p3).help("Close (⌘.)")
            Text(item.title).font(DS.serif(16, weight: .medium)).foregroundStyle(DS.Ink.p1)
                .lineLimit(1)
            Spacer()
            Button {
                detail = isExpanded ? .side(item) : .full(item)
            } label: {
                Image(systemName: isExpanded
                      ? "arrow.down.right.and.arrow.up.left"
                      : "arrow.up.left.and.arrow.down.right")
                    .font(.system(size: 12)).foregroundStyle(DS.Ink.p3)
            }
            .buttonStyle(.plainHit).help(isExpanded ? "Collapse" : "Expand to full screen")
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
    }
```

(c) Thread selection into the cards. In `loadedContent` (line ~122), change the awaiting `ForEach` to pass selection:

```swift
            ForEach(awaiting) { item in
                PerFileItemCardView(
                    item: item,
                    optionalLabel: config.optionalField.label,
                    priorityOptions: config.priorities,
                    onChangePriority: { try await changePriority(item, $0) },
                    onChangeStatus: { try await changeStatus(item, $0) },
                    onResolve: { try await resolve(item, $0) },
                    isSelected: detail?.item.id == item.id,
                    onSelect: { detail = .side(item) }
                )
            }
```

And in `resolvedSection` (line ~159), change the resolved `ForEach`:

```swift
                ForEach(resolved) { item in
                    PerFileItemCardView(
                        item: item,
                        optionalLabel: config.optionalField.label,
                        onChangeStatus: { try await changeStatus(item, $0) },
                        onResolve: { _ in },
                        isSelected: detail?.item.id == item.id,
                        onSelect: { detail = .side(item) }
                    )
                }
```

- [ ] **Step 3: Build**

Run: `xcodebuild build -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Commit**

```bash
git add Scout/PerFileItems/Views/PerFileItemCardView.swift Scout/PerFileItems/Views/PerFileListView.swift
git commit -m "feat(perfile): selectable cards + activity detail pane on Wishlist/Research (#43)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Cross-tab "Open run in Control Center"

> **No unit tests** — cross-view wiring. Build-verified + manual smoke test. Independently droppable: the diff already shows the evidence in-pane, so the feature is usable without this jump.

**Files:**
- Modify: `Scout/Shell/AppState.swift`
- Modify: `Scout/Shell/MainWindowView.swift`
- Modify: `Scout/ControlCenter/ControlCenterView.swift`

**Interfaces:**
- Consumes: `SidebarItem` (in `MainWindowView.swift`, same module), `AppState.sessionLogService.runs`, `Run.ID`, `ControlCenterView.DetailPresentation.side(Run)`.
- Produces: `AppState.pendingRunToOpen: Run.ID?`, `AppState.requestedSidebar: SidebarItem?`, `AppState.requestOpenRun(_ id: Run.ID)`.

- [ ] **Step 1: Add the intent to AppState** *(also required by Task 5)*

In `Scout/Shell/AppState.swift`, add to the published properties (near `@Published var fireNowError`, line ~14):

```swift
    /// A run the user asked to open from elsewhere (e.g. the Wishlist/Research
    /// activity timeline). Control Center observes this and opens the run's
    /// detail; it clears the value once consumed. (#43)
    @Published var pendingRunToOpen: Run.ID? = nil

    /// A sidebar tab another view asked to switch to. MainWindowView observes
    /// this, sets its local selection, and clears it. (#43)
    @Published var requestedSidebar: SidebarItem? = nil
```

Add this method to `AppState` (anywhere among its methods, e.g. after `fireNow`):

```swift
    /// Switch to Control Center and open `id`'s run detail. (#43)
    func requestOpenRun(_ id: Run.ID) {
        pendingRunToOpen = id
        requestedSidebar = .controlCenter
    }
```

- [ ] **Step 2: Make MainWindowView honor the sidebar request**

In `Scout/Shell/MainWindowView.swift`, add an `.onChange` to the `NavigationSplitView` — attach it right after `.safeAreaInset(edge: .bottom, …) { … }` (line ~30):

```swift
        .onChange(of: appState.requestedSidebar) { _, requested in
            guard let requested else { return }
            selection = requested
            appState.requestedSidebar = nil
        }
```

- [ ] **Step 3: Make ControlCenterView open the pending run**

In `Scout/ControlCenter/ControlCenterView.swift`, add a helper method (next to `openDetail`, line ~153):

```swift
    /// Open a run requested from another tab (e.g. the Wishlist/Research
    /// activity timeline) and clear the intent. (#43)
    private func openPendingRunIfNeeded() {
        guard let id = state.pendingRunToOpen,
              let run = state.sessionLogService.runs.first(where: { $0.id == id }) else { return }
        detail = .side(run)
        state.pendingRunToOpen = nil
    }
```

Then attach both triggers to the root `ZStack` in `body` — add after `.animation(.easeInOut(duration: 0.18), value: detail)` (line ~56):

```swift
        .task { openPendingRunIfNeeded() }
        .onChange(of: state.pendingRunToOpen) { _, _ in openPendingRunIfNeeded() }
```

(The `.task` covers the case where Control Center is created *because* the sidebar just switched; the `.onChange` covers Control Center already being on-screen.)

- [ ] **Step 4: Build**

Run: `xcodebuild build -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 5: Commit**

```bash
git add Scout/Shell/AppState.swift Scout/Shell/MainWindowView.swift Scout/ControlCenter/ControlCenterView.swift
git commit -m "feat(perfile): jump from item activity to the resolving run in Control Center (#43)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> **Note:** if Task 5's Step 3 already committed the AppState change, this task's Step 1 is a no-op — stage only `MainWindowView.swift` and `ControlCenterView.swift` here.

---

## Final Verification

- [ ] **Full test suite:**
  `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY=""`
  Expected: all green, and the count is the previous baseline (364) **plus the 14 new tests** added here (Task 1: 3, Task 2: 5, Task 3: 3, Task 4: 3).

- [ ] **Manual smoke test** (run the app via the `run` skill or Xcode):
  1. Open the **Wishlist** tab. Tap an item with history → side pane opens with a commit timeline.
  2. Expand a commit row → its diff renders with +/− coloring.
  3. Open a **resolved** item → the "Resolved by … · date" summary shows; the newest commit is tagged `outcome`.
  4. On a commit linked to a Scout run, click **Open run in Control Center** → sidebar switches to Control Center and that run's detail opens.
  5. Confirm in-app Start/Done/Drop commits show as **"you"** (no run link), and a brand-new uncommitted item shows **"No activity yet"**.
  6. Confirm the expand (⤢) / close (⌘.) controls on the detail header behave like Control Center's.

---

## Self-Review

**1. Spec coverage:**
- Mechanism (app-side git-derived) → Tasks 1–4. ✓
- Scope (resolved + active) → `ItemActivity.entries` keys `isResolving` off `itemIsActive`; the pane opens for any card (Task 6). ✓
- Layout (detail pane mirroring Control Center) → Task 6 (`.side`/`.full`, `detailHeader`). ✓
- Per-commit expandable diff → Task 5 (`CommitDiffView` + `diffSection`). ✓
- Reverse run-link heuristic + limits → Task 2 (`CommitRunLinker`, manual-never-links, most-recent wins). ✓
- "you" vs run labeling; resolving = newest commit → Tasks 2/3. ✓
- Edge cases: empty history ("No activity yet"), git error (`.failed`, not swallowed), large diff (scrollable, capped height) → Tasks 4/5. ✓
- "Open run in Control Center" → Task 7. ✓
- Testing plan (services + models tested; views build-verified) → Tasks 1–4 tested; 5–7 build + smoke. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; commands are exact. The one forward-reference (Task 5 → Task 7's AppState hook) is called out explicitly with an ordering instruction. ✓

**3. Type consistency:** `commits(touchingFile:)`, `diff(forCommit:)`, `CommitRunLinker.run(for:in:now:)`, `ItemActivity.entries(commits:runs:itemIsActive:now:)`, `ItemActivityEntry{commit,run,isResolving}`, `PerFileItemActivityModel{state,diffs,load(),loadDiff(for:)}`, `PerFileItemDetailView(item:git:runs:)`, `AppState.requestOpenRun(_:)` / `pendingRunToOpen` / `requestedSidebar` — names match across all call sites. ✓
