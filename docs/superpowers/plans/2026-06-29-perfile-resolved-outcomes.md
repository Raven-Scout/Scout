# Wishlist/Research Resolved-Item Outcomes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Revision 2 (2026-10-03).** Rewritten against `main` @ `7a037c9` after re-validating every reference. See the spec's "Rev 2" table for what changed and why. The June revision's code is superseded in full. Do not mix the two.

**Goal:** Add a per-item, git-derived activity timeline to the Wishlist/Research tabs, shown in a Control-Center-style detail pane. Each row carries the item file's patch, the run that made it, and a jump to that run. The commit that set the terminal `status:` is surfaced as the outcome.

**Architecture:** All data comes from git and the existing run logs. There are no scout-plugin changes.

1. Runs gain a zone-aware start parsed from their log header (Task 1).
2. One shared helper defines a run's commit window and which commit subjects it can claim. Both `SessionLogService.commits(for:)` and the new reverse `CommitRunLinker` use it (Task 2).
3. `GitService` gains a rename-following, file-scoped history (`git log --follow --patch`) and a per-commit file list (Task 3).
4. Pure builders label each revision and find the resolving one (Task 4).
5. A small `@MainActor` model loads the history (Task 5).
6. The views (Tasks 6–7) and the cross-tab jump (Task 8) complete it.

**Tech Stack:** Swift 6 / SwiftUI (macOS), Swift Testing (`import Testing`, `@Suite`/`@Test`/`#expect`). The target sets `SWIFT_DEFAULT_ACTOR_ISOLATION=MainActor`.

## Global Constraints

- **Monorepo layout (since 2026-10-05):** this plan now lives in `Raven-Scout/Scout` (PR #298). Every `Scout/…` and `ScoutTests/…` path below is relative to **`apps/macos/`**, and every `xcodebuild` command runs from there. Bare `#N` references written before the move mean `Raven-Scout/scout-app-legacy#N`; issue #43 is now `Raven-Scout/Scout#286`.
- **Approved 2026-10-05** by Jordan, with the timezone fix (Task 1) kept in this PR.

- **Spec:** `docs/superpowers/specs/2026-06-28-perfile-resolved-outcomes-design.md` (rev 2). Issue #43.
- **No scout-plugin changes; no new frontmatter.**
- **The item's patch is always shown regardless of the run link.** A missing or wrong badge must never hide the change.
- **Errors are surfaced, never swallowed** (#47). git failures become a visible `.failed` state, not an empty list.
- **Keep `PerFileListView`'s `VStack`** and its #83 comment exactly as they are. Never reintroduce `LazyVStack` there.
- **Isolation:** the target defaults to `MainActor`. Mark new pure value types and pure enums `nonisolated`, as `PerFileItem`/`ItemStatus` do, and mark new `Run` extension methods `nonisolated`, since `SessionLogService`'s `nonisolated static` helpers call into `Run`. If the compiler flags a call into a `MainActor`-isolated member from one of them, mark that member `nonisolated` too. Don't wrap the call in a `Task`.
- **New `.swift` files under `Scout/` and `ScoutTests/` auto-compile** (synchronized file groups). Never edit `project.pbxproj`.
- **New stored fields are never defaulted in production initializers.** `ParsedBody.startedAt` and `Run.headerStartedAt` have no default, so the compiler finds every construction site. Only the `#if DEBUG` `Run.make` test factory defaults them.
- **Build:** `xcodebuild build -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS'`
- **Test (one suite):** `xcodebuild test -project Scout.xcodeproj -scheme Scout -destination 'platform=macOS' -only-testing:ScoutTests/<SuiteTypeName> 2>&1 | grep -E "error:|Test run with|✘|TEST (SUCCEEDED|FAILED)"`
  - `<SuiteTypeName>` is the Swift **type** name (`CommitRunLinkerTests`), not the `@Suite("…")` string and not a directory. A selector that matches nothing still prints `** TEST SUCCEEDED **`. Always confirm `Test run with N tests` shows your tests.
  - If signing blocks a local build, append `CODE_SIGNING_ALLOWED=NO`.
- **Test (full target):** the same command with `-only-testing:ScoutTests`. Record the baseline count on `main` before Task 1.
- **Never touch the real vault from tests.** Use temp dirs, and pass `parseCacheURL:` to every `SessionLogService` that calls `loadInitial()`, so the real per-user parse cache is never rewritten.
- **Fixture literals** (CLAUDE.md, public repo): only invented, generic content. Paths go under `/tmp/Scout/…`, subjects are generic ("filed the example item"), and the stand-in names are `Alex`/`Priya`/`Sam`. Task 9 re-checks every new literal against `~/Scout`, excluding `~/Scout/.claude/`.
- **Commit trailer (every commit):**
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```
- **Branch:** `feat/perfile-resolved-outcomes-43` (PR #68). Re-fetch before every push and never force-push.

---

## File Structure

**Create:**
- `Scout/Services/CommitRunLinker.swift` — `CommitFamily`, `Run.commitWindow(now:)`, `Run.claims(_:)`, `CommitRunLinker`.
- `Scout/Models/FileRevision.swift` — one commit plus this file's patch.
- `Scout/PerFileItems/Models/ItemActivity.swift` — `ActivitySource`, `ItemActivityEntry`, `ItemOutcome`, `ItemActivity` builders.
- `Scout/PerFileItems/PerFileItemActivityModel.swift` — `@MainActor ObservableObject`.
- `Scout/PerFileItems/Views/CommitDiffView.swift`
- `Scout/PerFileItems/Views/PerFileItemDetailView.swift`
- `ScoutTests/Services/RunAttributionTests.swift`
- `ScoutTests/Services/GitServiceFileHistoryTests.swift`
- `ScoutTests/PerFile/ItemActivityTests.swift`
- `ScoutTests/PerFile/PerFileItemActivityModelTests.swift`

**Modify:**
- `Scout/Services/SessionLogService.swift` — `startRegex`, `ParsedBody.startedAt`, `Run.headerStartedAt` plumbing; `commits(for:)` uses the shared window/claim; remove `RunType.commitsPrefix`.
- `Scout/Services/SessionLogParseCache.swift` — `ParseCache.version = 2`.
- `Scout/Models/Run.swift` — `headerStartedAt` stored field; `with(status:)`; `Run.make` derives `runnerScript` from `type`.
- `Scout/Services/GitService.swift` — `fileHistory(relativePath:)`, `parseFileHistory(_:)`, `filesChanged(inCommit:)`.
- `Scout/PerFileItems/Views/PerFileItemCardView.swift` — `isSelected`, `onShowHistory`, the **History** button.
- `Scout/PerFileItems/Views/PerFileListView.swift` — selection + side/full detail.
- `Scout/Shell/AppState.swift`, `Scout/Shell/MainWindowView.swift`, `Scout/ControlCenter/ControlCenterView.swift` — the cross-tab jump.
- Tests touched by the compiler: `ScoutTests/Services/SessionLogParseCacheTests.swift` (`ParsedBody` helper), `ScoutTests/Models/RunDisplayTests.swift` (direct `Run(...)`), `ScoutTests/Services/SessionLogStaleSweepTests.swift` (the `commitsPrefix` tests move to `CommitFamily`, plus one forward regression).

**Reuse (do not redefine):** `ScriptedRunner` (bottom of `ScoutTests/Services/GitServiceCommitPathsTests.swift`), `NoopFS` (`UsageTrackerServiceTests.swift`), `FixedClock` (`SessionLogServiceTests.swift`), `Run.make(...)`.

---

### Task 1: Zone-aware run start from the log header

**Why:** `parseFilename` reads the filename's wall time in `TimeZone.current`. Logs written in another zone shift by the zone difference, and the commit window balloons or inverts (spec finding 1). Every log header carries the absolute start. `Run.startedAt` and `Run.id` are deliberately **left filename-derived**, because `ClaudeSessionService.activity(for:)` formats `startedAt` back into a wall-clock title fragment.

**Files:**
- Modify: `Scout/Services/SessionLogService.swift`, `Scout/Services/SessionLogParseCache.swift`, `Scout/Models/Run.swift`
- Modify (compiler-driven): `ScoutTests/Services/SessionLogParseCacheTests.swift`, `ScoutTests/Models/RunDisplayTests.swift`
- Test: `ScoutTests/Services/SessionLogServiceTests.swift` (add to the existing suite)

- [ ] **Step 1: Write the failing tests** — append inside `struct SessionLogServiceTests`:

```swift
    // MARK: - Header start (zone-aware, #43)

    private func writeLog(_ name: String, _ text: String) throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("header-start-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent(name)
        try text.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    @Test func parseBody_headerStartIsZoneAware() throws {
        // Logged in New York, read on a machine set to Berlin.
        let url = try writeLog("research-2026-04-19_15-00.log",
            "=== Scout Research run starting at Sun Apr 19 15:00:01 EDT 2026 ===\n")
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let berlin = TimeZone(identifier: "Europe/Berlin")!
        let parsed = try #require(SessionLogService.parseFilename(url, timeZone: berlin))
        let body = try SessionLogService.parseBody(at: url, filename: parsed)

        let iso = ISO8601DateFormatter()
        #expect(body.startedAt == iso.date(from: "2026-04-19T19:00:01Z"))
        // The drift this fixes: the filename alone reads as 15:00 Berlin time.
        #expect(parsed.startedAt == iso.date(from: "2026-04-19T13:00:00Z"))
    }

    @Test func parseBody_headerStartHandlesEuropeanZoneAndPaddedDay() throws {
        let url = try writeLog("dreaming-2026-10-01_21-05.log",
            "=== SCOUT Dreaming run starting at Thu Oct  1 21:05:09 CEST 2026 ===\n")
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let parsed = try #require(SessionLogService.parseFilename(url))
        let body = try SessionLogService.parseBody(at: url, filename: parsed)
        #expect(body.startedAt == ISO8601DateFormatter().date(from: "2026-10-01T19:05:09Z"))
    }

    @Test func parseBody_missingHeaderLeavesStartNil() throws {
        let url = try writeLog("scout-2026-04-20_08-03.log", "no header here\n")
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let parsed = try #require(SessionLogService.parseFilename(url))
        #expect(try SessionLogService.parseBody(at: url, filename: parsed).startedAt == nil)
    }

    @MainActor
    @Test func loadInitial_carriesHeaderStartButKeepsFilenameIdentity() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("header-load-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        try """
        === Scout Research run starting at Sun Apr 19 15:00:01 EDT 2026 ===
        === Scout Research run finished at Sun Apr 19 15:20:00 EDT 2026 (exit code: 0, duration: 1199s) ===
        """.write(to: dir.appendingPathComponent("research-2026-04-19_15-00.log"),
                  atomically: true, encoding: .utf8)
        let trackerURL = dir.appendingPathComponent("usage-tracker.jsonl")
        try "".write(to: trackerURL, atomically: true, encoding: .utf8)
        let tracker = UsageTrackerService(trackerURL: trackerURL, fileEvents: NoopFS())
        _ = try await tracker.loadInitial()
        let berlin = TimeZone(identifier: "Europe/Berlin")!
        let service = SessionLogService(
            logsDirectory: dir, trackerService: tracker, fileEvents: NoopFS(),
            timeZone: berlin, parseCacheURL: dir.appendingPathComponent("parse-cache.json"))

        let run = try #require(try await service.loadInitial().first)
        let iso = ISO8601DateFormatter()
        #expect(run.headerStartedAt == iso.date(from: "2026-04-19T19:00:01Z"))
        #expect(run.startedAt == iso.date(from: "2026-04-19T13:00:00Z"))   // unchanged semantics
        #expect(run.id == Run.makeId(type: .research, startedAt: run.startedAt))
    }
```

> If `UsageTrackerService.init` is `async` in this context, the compiler will say so. Match `SessionLogStaleSweepTests.makeTracker`, which this mirrors.

- [ ] **Step 2: Run them and watch them fail**

`-only-testing:ScoutTests/SessionLogServiceTests` → FAIL: `value of type 'ParsedBody' has no member 'startedAt'`.

- [ ] **Step 3: Implement**

In `SessionLogService.swift`, add next to `finishRegex`:

```swift
    // `=== Scout[ <Kind>] run starting at <date> <ZONE> <year> ===` — every
    // historical casing (`SCOUT`, `Scout`, `Scout Research`, …). Unlike the
    // filename, this carries the zone the run was logged in, so it is the
    // absolute start (#43: filename starts drift by the zone delta once the
    // machine's zone changes).
    private static let startRegex = try! NSRegularExpression(
        pattern: #"=== Scout(?: \w+)? run starting at (.+?) ==="#,
        options: [.caseInsensitive]
    )
```

Add `startedAt` as the **first** stored property of `ParsedBody` (no default):

```swift
    struct ParsedBody: Equatable, Sendable, Codable {
        /// Absolute start from the log header; nil when the header is absent.
        let startedAt: Date?
        let endedAt: Date?
        …
```

In `parseBody`, before the finish-marker block:

```swift
        var startedAt: Date? = nil
        if let match = startRegex.firstMatch(in: text, range: range),
           let dateRange = Range(match.range(at: 1), in: text) {
            startedAt = parseScoutTimestamp(String(text[dateRange]))
        }
```

and pass `startedAt: startedAt,` first in the `ParsedBody(...)` return.

In `Scout/Models/Run.swift`, add a stored property right after `endedAt` (no default):

```swift
    /// Absolute start parsed from the log header (zone-aware). `startedAt`
    /// stays filename-derived because the run id and the Claude-session title
    /// match format it back into wall-clock text. Commit windows use this
    /// when present (#43).
    let headerStartedAt: Date?
```

Update `with(status:)` to pass `headerStartedAt: headerStartedAt`. In `Run.make`, **append** two parameters at the end of the list, after `retryOf`: `runnerScript: String? = nil, headerStartedAt: Date? = nil`. Appending them keeps every existing call valid, and lets tests write `Run.make(type:startedAt:endedAt:headerStartedAt:)`. Pass `headerStartedAt` through, and derive the runner when it's nil:

```swift
            runnerScript: runnerScript ?? {
                switch type {
                case .dreaming: return "run-dreaming.sh"
                case .research: return "run-research.sh"
                default:        return "run-scout.sh"
                }
            }(),
```

In both `Run(...)` constructions in `SessionLogService` (`loadInitial` and `reconcile`), add `headerStartedAt: body.startedAt,` after `endedAt:`.

In `SessionLogParseCache.swift`, set `static let version = 2`. Cached bodies predate `startedAt` and must re-parse once.

- [ ] **Step 4: Fix the compiler-driven sites**

Build. The compiler flags every remaining construction site. Expected sites:
- `SessionLogParseCacheTests.body(_:)` → add `startedAt: nil,`
- `RunDisplayTests` (direct `Run(...)`) → add `headerStartedAt: nil,`

Fix only what it flags. Then run `grep -rn "Run(" Scout ScoutTests | grep -v "Run.make\|RunType\|Runner"` and `grep -rn "ParsedBody(" Scout ScoutTests`, and confirm each hit passes the new field.

- [ ] **Step 5: Run the tests and watch them pass**

`-only-testing:ScoutTests/SessionLogServiceTests`, then `SessionLogParseCacheTests`, `SessionLogStaleSweepTests` and `RunDisplayTests`, each by type name. All green, and the 4 new tests show in the count.

- [ ] **Step 6: Commit**

```bash
git add Scout/Services/SessionLogService.swift Scout/Services/SessionLogParseCache.swift Scout/Models/Run.swift ScoutTests/Services/SessionLogServiceTests.swift ScoutTests/Services/SessionLogParseCacheTests.swift ScoutTests/Models/RunDisplayTests.swift
git commit -m "fix(runs): carry the zone-aware start from the log header (#43)" -m "Filename starts drift by the zone delta once the machine's zone changes, so commit windows ballooned or inverted for runs logged elsewhere. Run.startedAt/id keep their filename semantics; headerStartedAt is new. Parse cache bumped to v2." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Shared commit window + subject family (forward and reverse)

**Why:** spec findings 1–2. One definition serves both `commits(for:)` (Control Center) and the new reverse linker, so the two can't drift. Families are matched to the stable **runner**, not the hour-bucketed `RunType`.

**Files:**
- Create: `Scout/Services/CommitRunLinker.swift`
- Modify: `Scout/Services/SessionLogService.swift` (`commits(for:)`; delete `RunType.commitsPrefix`)
- Test: create `ScoutTests/Services/RunAttributionTests.swift`; modify `ScoutTests/Services/SessionLogStaleSweepTests.swift`

**Interfaces produced:**
- `nonisolated enum CommitFamily: String, CaseIterable, Sendable` with `static func of(subject:) -> CommitFamily?`, `var runnerScript: String`, `var displayName: String`
- `extension Run { nonisolated func commitWindow(now: Date) -> ClosedRange<Date>; nonisolated func claims(_ commit: Commit) -> Bool }`
- `nonisolated enum CommitRunLinker { static func run(for commit: Commit, in runs: [Run], now: Date) -> Run? }`

- [ ] **Step 1: Write the failing tests** — create `ScoutTests/Services/RunAttributionTests.swift`:

```swift
import Testing
import Foundation
@testable import Scout

@Suite("Run attribution — commit family, window, reverse link")
struct RunAttributionTests {

    private let t0 = ISO8601DateFormatter().date(from: "2026-04-20T22:00:00Z")!

    private func commit(_ subject: String, at offset: TimeInterval) -> Commit {
        Commit(id: "\(subject)-\(offset)", shortSHA: "abc1234",
               timestamp: t0.addingTimeInterval(offset), subject: subject,
               filesChanged: 1, insertions: 1, deletions: 0)
    }

    // MARK: CommitFamily

    @Test func familyReadsTheSubjectToken() {
        #expect(CommitFamily.of(subject: "weekend briefing [2026-04-18]: nothing new on the example board") == .weekendBriefing)
        #expect(CommitFamily.of(subject: "briefing [2026-04-20]: the day ahead") == .briefing)
        #expect(CommitFamily.of(subject: "consolidation [13:1x]: example sweep") == .consolidation)
        #expect(CommitFamily.of(subject: "dreaming [22:1x]: wishlist — filed the example item") == .dreaming)
        #expect(CommitFamily.of(subject: "research: example findings") == .research)
    }

    @Test func familyNeedsADelimiterAfterTheToken() {
        #expect(CommitFamily.of(subject: "researcher notes") == nil)
        #expect(CommitFamily.of(subject: "app: mark Example item done") == nil)
        #expect(CommitFamily.of(subject: "scout: tidy the example folder") == nil)
        #expect(CommitFamily.of(subject: "") == nil)
    }

    @Test func familiesMapToRunners() {
        #expect(CommitFamily.weekendBriefing.runnerScript == "run-scout.sh")
        #expect(CommitFamily.briefing.runnerScript == "run-scout.sh")
        #expect(CommitFamily.consolidation.runnerScript == "run-scout.sh")
        #expect(CommitFamily.dreaming.runnerScript == "run-dreaming.sh")
        #expect(CommitFamily.research.runnerScript == "run-research.sh")
    }

    // MARK: commitWindow / claims

    @Test func headerStartWinsOverFilenameStart() {
        let run = Run.make(type: .research, startedAt: t0.addingTimeInterval(-6 * 3600),
                           endedAt: t0.addingTimeInterval(600), headerStartedAt: t0)
        let w = run.commitWindow(now: t0.addingTimeInterval(86_400))
        #expect(w.lowerBound == t0.addingTimeInterval(-30))
        #expect(w.upperBound == t0.addingTimeInterval(600 + 300))
    }

    @Test func unendedNonRunningRunIsCappedAtItsOrphanCutoff() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: nil, status: .orphaned)
        let w = run.commitWindow(now: t0.addingTimeInterval(10 * 3600))
        #expect(w.upperBound == t0.addingTimeInterval(2 * 3600 + 300))
    }

    @Test func runningRunExtendsToNow() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: nil, status: .running)
        let now = t0.addingTimeInterval(600)
        #expect(run.commitWindow(now: now).upperBound == now.addingTimeInterval(300))
    }

    @Test func windowIsNeverInverted() {
        // No header + an absolute end that predates the drifted filename start.
        let run = Run.make(type: .research, startedAt: t0, endedAt: t0.addingTimeInterval(-3600))
        let w = run.commitWindow(now: t0)
        #expect(w.lowerBound <= w.upperBound)
        #expect(w.upperBound == t0.addingTimeInterval(270))
    }

    @Test func scoutRunnerClaimsAllThreeOfItsFamilies() {
        let run = Run.make(type: .consolidation, startedAt: t0)
        #expect(run.claims(commit("briefing [2026-04-20]: the day ahead", at: 0)))
        #expect(run.claims(commit("weekend briefing [2026-04-18]: nothing new on the example board", at: 0)))
        #expect(run.claims(commit("consolidation [13:1x]: example sweep", at: 0)))
        #expect(!run.claims(commit("dreaming [22:1x]: wishlist — x", at: 0)))
        #expect(!run.claims(commit("app: mark Example item done", at: 0)))
    }

    // MARK: CommitRunLinker

    @Test func linksAClaimedCommitInsideTheWindow() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        let c = commit("dreaming [22:0x]: wishlist — filed the example item", at: 300)
        #expect(CommitRunLinker.run(for: c, in: [run], now: t0)?.id == run.id)
    }

    @Test func appCommitsNeverLink() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        #expect(CommitRunLinker.run(for: commit("app: mark Example item done", at: 300),
                                    in: [run], now: t0) == nil)
    }

    @Test func outOfWindowDoesNotLink() {
        let run = Run.make(type: .dreaming, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        #expect(CommitRunLinker.run(for: commit("dreaming [23:5x]: late", at: 7200),
                                    in: [run], now: t0) == nil)
    }

    @Test func familyRunnerMismatchDoesNotLink() {
        let run = Run.make(type: .research, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        #expect(CommitRunLinker.run(for: commit("dreaming [22:0x]: x", at: 300),
                                    in: [run], now: t0) == nil)
    }

    @Test func overlappingRunsPickTheLatestStart() {
        let older = Run.make(type: .research, startedAt: t0, endedAt: t0.addingTimeInterval(3600))
        let newer = Run.make(type: .research, startedAt: t0.addingTimeInterval(100),
                             endedAt: t0.addingTimeInterval(3600))
        let c = commit("research [22:0x]: example findings", at: 200)
        #expect(CommitRunLinker.run(for: c, in: [older, newer], now: t0)?.id == newer.id)
    }
}
```

In `SessionLogStaleSweepTests.swift`:
- **Delete** `commitsPrefix_perRunType` and `commitsPrefix_manualIsEmpty`. `RunType.commitsPrefix` is removed, and `RunAttributionTests` covers its replacement.
- **Add** the forward regression for weekend briefings:

```swift
    @Test("a weekend run's commits are found even though their subject says 'weekend briefing'")
    func commits_weekendBriefingSubjectsAreClaimed() async throws {
        let dir = try makeTempDir(); defer { try? FileManager.default.removeItem(at: dir) }
        let sep = "\u{1E}"
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let ts = String(Int(started.timeIntervalSince1970) + 60)
        let logOut = [
            ["wb1", "wb1", ts, "weekend briefing [2026-04-18]: nothing new on the example board"].joined(separator: sep),
            ["dr1", "dr1", ts, "dreaming [08:0x]: unrelated"].joined(separator: sep),
        ].joined(separator: "\n")
        let runner = ScriptedRunner(scripted: [
            ProcessResult(exitCode: 0, stdout: Data(logOut.utf8), stderr: Data()),
        ])
        let service = SessionLogService(
            logsDirectory: dir, trackerService: try await makeTracker(in: dir),
            gitService: GitService(repoURL: dir, runner: runner),
            fileEvents: NoopFS(), timeZone: Self.ny)

        let run = Run.make(type: .weekendBriefing, startedAt: started,
                           endedAt: started.addingTimeInterval(600))
        #expect(await service.commits(for: run).map(\.id) == ["wb1"])
    }
```

The existing `commits_queriesPaddedWindowWithTypePrefix` and `commits_openEndedRunUsesClockNow` must keep passing unchanged. They pin the window this task now centralizes.

- [ ] **Step 2: Run and watch them fail**

`-only-testing:ScoutTests/RunAttributionTests` → FAIL: `cannot find 'CommitFamily' in scope`.

- [ ] **Step 3: Implement** — create `Scout/Services/CommitRunLinker.swift`:

```swift
import Foundation

/// The run family a vault commit subject claims. Scout's sessions prefix their
/// commits `<family> [..]: …`, and that prefix is more reliable than the
/// `RunType` derived from a log filename's local hour, which mis-buckets
/// scheduled runs once the machine's timezone changes. Declaration order is
/// match order: longest token first, so "weekend briefing …" never reads as
/// "briefing". (#43)
nonisolated enum CommitFamily: String, CaseIterable, Sendable {
    case weekendBriefing = "weekend briefing"
    case consolidation
    case briefing
    case dreaming
    case research

    static func of(subject: String) -> CommitFamily? {
        for family in allCases where subject.hasPrefix(family.rawValue) {
            let next = subject.dropFirst(family.rawValue.count).first
            if next == nil || next == " " || next == ":" || next == "[" { return family }
        }
        return nil
    }

    /// The runner script whose runs make this family's commits — stable,
    /// unlike the hour-bucketed `RunType`.
    var runnerScript: String {
        switch self {
        case .weekendBriefing, .briefing, .consolidation: return "run-scout.sh"
        case .dreaming: return "run-dreaming.sh"
        case .research: return "run-research.sh"
        }
    }

    var displayName: String {
        switch self {
        case .weekendBriefing: return "Weekend briefing"
        case .briefing:        return "Briefing"
        case .consolidation:   return "Consolidation"
        case .dreaming:        return "Dreaming"
        case .research:        return "Research"
        }
    }
}

extension Run {
    /// The span in which this run's own commits land — the single definition
    /// shared by `SessionLogService.commits(for:)` and `CommitRunLinker`.
    /// Starts 30 s before the (zone-aware, when known) start; ends 5 min after
    /// the finish so wind-down commits count. A run with no finish marker
    /// extends to `now` while running, else stops at its orphan cutoff so it
    /// can't claim commits forever. Never inverted.
    nonisolated func commitWindow(now: Date) -> ClosedRange<Date> {
        let anchor = headerStartedAt ?? startedAt
        let lower = anchor.addingTimeInterval(-30)
        let naturalEnd = endedAt
            ?? (status == .running ? now : anchor.addingTimeInterval(type.orphanAfter))
        let upper = max(naturalEnd, lower).addingTimeInterval(5 * 60)
        return lower...upper
    }

    /// True when the commit's subject names a family this run's runner makes.
    nonisolated func claims(_ commit: Commit) -> Bool {
        CommitFamily.of(subject: commit.subject)?.runnerScript == runnerScript
    }
}

/// Maps a commit back to the run that made it — the reverse of
/// `SessionLogService.commits(for:)`. Overlapping candidates resolve to the
/// latest start. Unclaimed subjects (`app: …`, interactive sessions) never
/// link; the caller labels them instead. The item's patch is shown either
/// way, so a miss never hides evidence (#43).
nonisolated enum CommitRunLinker {
    static func run(for commit: Commit, in runs: [Run], now: Date) -> Run? {
        runs
            .filter { $0.claims(commit) && $0.commitWindow(now: now).contains(commit.timestamp) }
            .max { ($0.headerStartedAt ?? $0.startedAt) < ($1.headerStartedAt ?? $1.startedAt) }
    }
}
```

> If the `nonisolated` extension can't read `type.orphanAfter`, mark `orphanAfter` `nonisolated`. `promoteOrphan`, a `nonisolated static`, already reads it, so this is not expected.

Replace `SessionLogService.commits(for:)` with:

```swift
    /// Resolve commits for a Run on demand (the Diff tab). The window and the
    /// subject claim are shared with the reverse `CommitRunLinker` (#43), so
    /// the subject filter runs here rather than as a git `matchingPrefix`:
    /// a `run-scout.sh` run claims briefing, weekend-briefing and
    /// consolidation commits alike.
    func commits(for run: Run) async -> [Commit] {
        guard let git = gitService else { return [] }
        let window = run.commitWindow(now: clock.now())
        let all = (try? await git.commits(
            between: window.lowerBound,
            and: window.upperBound,
            matchingPrefix: ""
        )) ?? []
        return all.filter(run.claims)
    }
```

Delete the `commitsPrefix` property from the `RunType` extension. `grep -rn commitsPrefix Scout ScoutTests` must then come back empty.

- [ ] **Step 4: Run and watch them pass** — `RunAttributionTests` and `SessionLogStaleSweepTests`, by type name.

- [ ] **Step 5: Commit**

```bash
git add Scout/Services/CommitRunLinker.swift Scout/Services/SessionLogService.swift ScoutTests/Services/RunAttributionTests.swift ScoutTests/Services/SessionLogStaleSweepTests.swift
git commit -m "feat(runs): one commit window + subject-family claim for both directions (#43)" -m "Weekend-briefing commits never matched the 'briefing' prefix, and hour-bucketed RunTypes dropped commits once the zone changed. Families now map to the stable runner; CommitRunLinker is the reverse lookup." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: GitService file-scoped history + per-commit file list

**Files:**
- Create: `Scout/Models/FileRevision.swift`
- Modify: `Scout/Services/GitService.swift`
- Test: create `ScoutTests/Services/GitServiceFileHistoryTests.swift`

**Interfaces produced:**
- `nonisolated struct FileRevision: Identifiable, Equatable, Sendable { let commit: Commit; let patch: String; var id: String }`
- `GitService.fileHistory(relativePath:) async throws -> [FileRevision]`
- `nonisolated static GitService.parseFileHistory(_:) -> [FileRevision]`
- `GitService.filesChanged(inCommit:) async throws -> [String]`

- [ ] **Step 1: Write the failing tests**

```swift
import Testing
import Foundation
@testable import Scout

@Suite("GitService file history")
struct GitServiceFileHistoryTests {

    private let rel = "docs/wishlist/2026-04-01-example-item.md"
    private let RS = "\u{1E}", US = "\u{1F}"

    private func git(_ results: [ProcessResult]) -> (GitService, ScriptedRunner) {
        let runner = ScriptedRunner(scripted: results)
        return (GitService(repoURL: URL(fileURLWithPath: "/tmp/Scout"), runner: runner), runner)
    }

    private var threeRevisions: String {
        """
        \(RS)bbb222\(US)bbb\(US)1776000600\(US)app: mark Example item done

        diff --git a/\(rel) b/\(rel)
        index 1111111..2222222 100644
        --- a/\(rel)
        +++ b/\(rel)
        @@ -1,4 +1,4 @@
         ---
         title: "Example item"
        -status: open
        +status: done
        \(RS)mmm333\(US)mmm\(US)1775990000\(US)Merge branch 'side'
        \(RS)aaa111\(US)aaa\(US)1775900000\(US)dreaming [22:1x]: wishlist — filed the example item

        diff --git a/\(rel) b/\(rel)
        new file mode 100644
        --- /dev/null
        +++ b/\(rel)
        @@ -0,0 +1,3 @@
        +---
        +status: open
        +---

        """
    }

    @Test func fileHistoryParsesRevisionsNewestFirst() async throws {
        let (g, runner) = git([ProcessResult(exitCode: 0, stdout: Data(threeRevisions.utf8), stderr: Data())])

        let revs = try await g.fileHistory(relativePath: rel)

        #expect(revs.map(\.id) == ["bbb222", "mmm333", "aaa111"])
        #expect(revs[0].commit.subject == "app: mark Example item done")
        #expect(revs[0].commit.insertions == 1 && revs[0].commit.deletions == 1)
        #expect(revs[0].patch.contains("+status: done"))
        #expect(revs[1].patch.isEmpty)                       // merge: no patch for this file
        #expect(revs[2].commit.insertions == 3 && revs[2].commit.deletions == 0)
        #expect(revs[2].commit.timestamp == Date(timeIntervalSince1970: 1_775_900_000))

        let args = try #require(runner.calls.first?.arguments)
        #expect(args.contains("--follow") && args.contains("--patch") && args.contains("--no-color"))
        #expect(Array(args.suffix(2)) == ["--", rel])
    }

    @Test func recordSeparatorMidLineDoesNotSplitARevision() {
        let text = "\(RS)ccc\(US)ccc\(US)1776000000\(US)app: edit\n\n+odd \(RS) byte in content\n"
        let revs = GitService.parseFileHistory(text)
        #expect(revs.count == 1)
        #expect(revs[0].patch.contains("odd"))
    }

    @Test func fileHistoryThrowsOnGitError() async {
        let (g, _) = git([ProcessResult(exitCode: 128, stdout: Data(), stderr: Data("fatal".utf8))])
        await #expect(throws: GitServiceError.self) { _ = try await g.fileHistory(relativePath: rel) }
    }

    @Test func filesChangedListsNamesOnly() async throws {
        let out = "docs/wishlist/2026-04-01-example-item.md\nknowledge-base/example-topic.md\n\n"
        let (g, runner) = git([ProcessResult(exitCode: 0, stdout: Data(out.utf8), stderr: Data())])

        let files = try await g.filesChanged(inCommit: "aaa111")

        #expect(files == ["docs/wishlist/2026-04-01-example-item.md", "knowledge-base/example-topic.md"])
        let args = try #require(runner.calls.first?.arguments)
        #expect(args.contains("show") && args.contains("--name-only") && args.contains("--format="))
        #expect(args.last == "aaa111")
    }
}
```

- [ ] **Step 2: Run and watch them fail** — `-only-testing:ScoutTests/GitServiceFileHistoryTests` → `no member 'fileHistory'`.

- [ ] **Step 3: Implement**

`Scout/Models/FileRevision.swift`:

```swift
import Foundation

/// One commit in a single file's history, with that file's patch only
/// (rename-aware). Insertions/deletions on `commit` count this file's lines,
/// not the whole commit's. (#43)
nonisolated struct FileRevision: Identifiable, Equatable, Sendable {
    let commit: Commit
    let patch: String
    var id: String { commit.id }
}
```

In `GitService.swift`, inside the class after `diff(from:to:)`:

```swift
    /// Every commit that touched one file, newest first, each with that file's
    /// patch only. `--follow` tracks renames (one pathspec); scoping `--patch`
    /// by the pathspec turns a 40-file run commit into this item's hunk. One
    /// call per opened item (#43). `relativePath` is repo-relative.
    func fileHistory(relativePath: String) async throws -> [FileRevision] {
        let result = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git", "-C", repoURL.path, "log", "--follow", "--patch",
                        "--no-color", "--no-ext-diff",
                        "--format=\u{1E}%H\u{1F}%h\u{1F}%ct\u{1F}%s",
                        "--", relativePath],
            environment: [:],
            workingDirectory: repoURL
        )
        guard result.exitCode == 0 else {
            throw GitServiceError.gitExitNonZero(Int(result.exitCode))
        }
        return Self.parseFileHistory(String(decoding: result.stdout, as: UTF8.self))
    }

    /// Records begin with RS at the start of a line. No patch line can start
    /// with RS (patch lines start with ' ', '+', '-', '@', '\\' or a header
    /// word), so splitting on "\n" + RS is unambiguous.
    nonisolated static func parseFileHistory(_ text: String) -> [FileRevision] {
        ("\n" + text).components(separatedBy: "\n\u{1E}").dropFirst().compactMap { record in
            let headerEnd = record.firstIndex(of: "\n") ?? record.endIndex
            let fields = record[..<headerEnd].components(separatedBy: "\u{1F}")
            guard fields.count == 4, let ts = TimeInterval(fields[2]) else { return nil }
            let patch = String(record[headerEnd...]).trimmingCharacters(in: .newlines)
            var insertions = 0, deletions = 0
            for line in patch.split(separator: "\n", omittingEmptySubsequences: false) {
                if line.hasPrefix("+++") || line.hasPrefix("---") { continue }
                if line.hasPrefix("+") { insertions += 1 } else if line.hasPrefix("-") { deletions += 1 }
            }
            let commit = Commit(id: fields[0], shortSHA: fields[1],
                                timestamp: Date(timeIntervalSince1970: ts), subject: fields[3],
                                filesChanged: patch.isEmpty ? 0 : 1,
                                insertions: insertions, deletions: deletions)
            return FileRevision(commit: commit, patch: patch)
        }
    }

    /// Repo-relative paths a commit touched (`git show --name-only`), for the
    /// "also changed in this commit" list.
    func filesChanged(inCommit sha: String) async throws -> [String] {
        let result = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/env"),
            arguments: ["git", "-C", repoURL.path, "show", "--name-only", "--format=",
                        "--no-color", sha],
            environment: [:],
            workingDirectory: repoURL
        )
        guard result.exitCode == 0 else {
            throw GitServiceError.gitExitNonZero(Int(result.exitCode))
        }
        return String(decoding: result.stdout, as: UTF8.self)
            .split(separator: "\n").map(String.init).filter { !$0.isEmpty }
    }
```

> The `+++`/`---` guard exists to skip the file headers. It has one known blind spot: a *removed* frontmatter fence shows as `----` and is skipped, which undercounts deletions by one. That's acceptable for a stat line. *Added* fences (`+---`) are counted, which is why the creation revision above expects 3 insertions.

- [ ] **Step 4: Run and watch them pass** — `GitServiceFileHistoryTests` (4 tests). Also re-run `GitServiceLogTests` and `GitServiceCommitPathsTests` by type name.

- [ ] **Step 5: Commit**

```bash
git add Scout/Models/FileRevision.swift Scout/Services/GitService.swift ScoutTests/Services/GitServiceFileHistoryTests.swift
git commit -m "feat(git): file-scoped history with patches + per-commit file list (#43)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Activity labels, resolving revision, outcome

**Files:**
- Create: `Scout/PerFileItems/Models/ItemActivity.swift`
- Test: create `ScoutTests/PerFile/ItemActivityTests.swift`

**Interfaces produced:**
- `nonisolated enum ActivitySource: Equatable, Sendable { case run(Run, CommitFamily), family(CommitFamily), app, other; var label: String; var run: Run? }`
- `nonisolated struct ItemActivityEntry: Identifiable, Equatable, Sendable { let revision: FileRevision; let source: ActivitySource; let isResolving: Bool; var id: String; var commit: Commit }`
- `nonisolated enum ItemOutcome: Equatable, Sendable { case resolved(ItemActivityEntry), resolvedUncommitted; func summary(date: (Date) -> String) -> String }`
- `nonisolated enum ItemActivity` with `source(for:runs:now:)`, `entries(revisions:runs:status:now:)`, `outcome(entries:status:)`, `addsStatus(_:to:)`, `repoRelativePath(of:repo:)`

- [ ] **Step 1: Write the failing tests**

```swift
import Testing
import Foundation
@testable import Scout

@Suite("ItemActivity")
struct ItemActivityTests {

    private let t0 = ISO8601DateFormatter().date(from: "2026-04-20T22:00:00Z")!

    private func rev(_ id: String, _ subject: String, at offset: TimeInterval = 0,
                     patch: String = "") -> FileRevision {
        FileRevision(commit: Commit(id: id, shortSHA: id, timestamp: t0.addingTimeInterval(offset),
                                    subject: subject, filesChanged: 1, insertions: 0, deletions: 0),
                     patch: patch)
    }

    // MARK: Sources

    @Test func labelsEachKindOfCommit() {
        let dreaming = Run.make(type: .dreaming, startedAt: t0, endedAt: t0.addingTimeInterval(600))
        let s = { (subject: String) in
            ItemActivity.source(for: self.rev("x", subject, at: 300).commit, runs: [dreaming], now: self.t0)
        }
        #expect(s("dreaming [22:0x]: wishlist — filed the example item") == .run(dreaming, .dreaming))
        #expect(s("research [22:0x]: example findings") == .family(.research))   // no research run
        #expect(s("app: mark Example item done") == .app)
        #expect(s("scout: tidy the example folder") == .other)
        #expect(ActivitySource.app.label == "You")
        #expect(ActivitySource.other.label == "Other")
        #expect(ActivitySource.run(dreaming, .dreaming).label == "Dreaming")
    }

    // MARK: Resolving revision

    @Test func resolvingIsTheCommitThatAddedTheCurrentStatus_notTheNewest() {
        let revisions = [
            rev("c3", "dreaming [22:3x]: appended a note", patch: "+- a later note"),
            rev("c2", "app: mark Example item done", patch: "-status: open\n+status: done"),
            rev("c1", "dreaming [22:1x]: filed it", patch: "+---\n+status: open\n+---"),
        ]
        let entries = ItemActivity.entries(revisions: revisions, runs: [], status: .done, now: t0)
        #expect(entries.map(\.isResolving) == [false, true, false])
        #expect(ItemActivity.outcome(entries: entries, status: .done) == .resolved(entries[1]))
    }

    @Test func itemCreatedAlreadyResolvedResolvesAtCreation() {
        let entries = ItemActivity.entries(
            revisions: [rev("c1", "research: example findings", patch: "+---\n+status: done\n+---")],
            runs: [], status: .done, now: t0)
        #expect(entries[0].isResolving)
    }

    @Test func matchesTheCurrentTerminalValueOnly_andToleratesQuotes() {
        #expect(ItemActivity.addsStatus("+status: \"dropped\"", to: .dropped))
        #expect(!ItemActivity.addsStatus("+status: done", to: .dropped))
        #expect(!ItemActivity.addsStatus("-status: dropped", to: .dropped))
        #expect(!ItemActivity.addsStatus("+status: open", to: .open))   // active → never "resolving"
    }

    @Test func resolvedWithoutACommitIsUncommitted() {
        let entries = ItemActivity.entries(
            revisions: [rev("c1", "app: add wishlist item Example item", patch: "+status: open")],
            runs: [], status: .done, now: t0)
        #expect(entries.allSatisfy { !$0.isResolving })
        #expect(ItemActivity.outcome(entries: entries, status: .done) == .resolvedUncommitted)
    }

    @Test func activeItemsHaveNoOutcome() {
        let entries = ItemActivity.entries(
            revisions: [rev("c1", "app: start Example item", patch: "-status: open\n+status: in-progress")],
            runs: [], status: .inProgress, now: t0)
        #expect(ItemActivity.outcome(entries: entries, status: .inProgress) == nil)
    }

    @Test func outcomeSummaryWording() {
        let d: (Date) -> String = { _ in "Apr 20" }
        let dreaming = Run.make(type: .dreaming, startedAt: t0)
        func entry(_ s: ActivitySource) -> ItemActivityEntry {
            ItemActivityEntry(revision: rev("c", "x"), source: s, isResolving: true)
        }
        #expect(ItemOutcome.resolved(entry(.run(dreaming, .dreaming))).summary(date: d) == "Resolved by Dreaming · Apr 20")
        #expect(ItemOutcome.resolved(entry(.family(.research))).summary(date: d) == "Resolved by Research · Apr 20")
        #expect(ItemOutcome.resolved(entry(.app)).summary(date: d) == "Resolved by you · Apr 20")
        #expect(ItemOutcome.resolved(entry(.other)).summary(date: d) == "Resolved · Apr 20")
        #expect(ItemOutcome.resolvedUncommitted.summary(date: d) == "Resolved — not committed yet")
    }

    // MARK: Paths

    @Test func repoRelativePathIsNilOutsideTheRepo() {
        let repo = URL(fileURLWithPath: "/tmp/Scout")
        #expect(ItemActivity.repoRelativePath(
            of: URL(fileURLWithPath: "/tmp/Scout/docs/wishlist/a.md"), repo: repo) == "docs/wishlist/a.md")
        #expect(ItemActivity.repoRelativePath(
            of: URL(fileURLWithPath: "/tmp/Elsewhere/a.md"), repo: repo) == nil)
        #expect(ItemActivity.repoRelativePath(
            of: URL(fileURLWithPath: "/tmp/ScoutOther/a.md"), repo: repo) == nil)
    }
}
```

- [ ] **Step 2: Run and watch them fail** — `-only-testing:ScoutTests/ItemActivityTests`.

- [ ] **Step 3: Implement** `Scout/PerFileItems/Models/ItemActivity.swift`:

```swift
import Foundation

/// Who made a commit in an item's history (#43). Labels come from the commit
/// subject's family, not the linked run's `RunType` (which can be
/// hour-mis-bucketed); only `.run` can jump to Control Center.
nonisolated enum ActivitySource: Equatable, Sendable {
    case run(Run, CommitFamily)   // linked to a run log
    case family(CommitFamily)     // a Scout run by its subject, no run log in the window
    case app                      // Scout.app's own writes ("app: …")
    case other                    // interactive sessions, hand commits, plugin upgrades

    var label: String {
        switch self {
        case .run(_, let f), .family(let f): return f.displayName
        case .app:   return "You"
        case .other: return "Other"
        }
    }

    var run: Run? { if case .run(let r, _) = self { return r } else { return nil } }
}

nonisolated struct ItemActivityEntry: Identifiable, Equatable, Sendable {
    let revision: FileRevision
    let source: ActivitySource
    /// The commit that added the item's current terminal `status:` line.
    let isResolving: Bool
    var id: String { revision.id }
    var commit: Commit { revision.commit }
}

nonisolated enum ItemOutcome: Equatable, Sendable {
    case resolved(ItemActivityEntry)
    /// Terminal status on disk, but no commit adds it yet (the write hasn't
    /// been committed, or was swept into a commit we can't see).
    case resolvedUncommitted

    func summary(date: (Date) -> String) -> String {
        switch self {
        case .resolvedUncommitted:
            return "Resolved — not committed yet"
        case .resolved(let e):
            switch e.source {
            case .run, .family: return "Resolved by \(e.source.label) · \(date(e.commit.timestamp))"
            case .app:          return "Resolved by you · \(date(e.commit.timestamp))"
            case .other:        return "Resolved · \(date(e.commit.timestamp))"
            }
        }
    }
}

nonisolated enum ItemActivity {

    static func source(for commit: Commit, runs: [Run], now: Date) -> ActivitySource {
        if let family = CommitFamily.of(subject: commit.subject) {
            if let run = CommitRunLinker.run(for: commit, in: runs, now: now) {
                return .run(run, family)
            }
            return .family(family)
        }
        return commit.subject.hasPrefix("app:") ? .app : .other
    }

    /// Label each revision (newest first, as `git log` returns them) and flag
    /// the newest one that added the item's current terminal status.
    static func entries(revisions: [FileRevision], runs: [Run], status: ItemStatus,
                        now: Date) -> [ItemActivityEntry] {
        let resolvingID = status.isActive ? nil
            : revisions.first(where: { addsStatus($0.patch, to: status) })?.id
        return revisions.map { rev in
            ItemActivityEntry(revision: rev,
                              source: source(for: rev.commit, runs: runs, now: now),
                              isResolving: rev.id == resolvingID)
        }
    }

    static func outcome(entries: [ItemActivityEntry], status: ItemStatus) -> ItemOutcome? {
        guard !status.isActive else { return nil }
        return entries.first(where: \.isResolving).map(ItemOutcome.resolved) ?? .resolvedUncommitted
    }

    /// True when `patch` adds a `status: <terminal value>` line (quotes
    /// tolerated). Active statuses never count as resolving.
    static func addsStatus(_ patch: String, to status: ItemStatus) -> Bool {
        guard !status.isActive else { return false }
        let wanted = status.frontmatterValue.lowercased()
        return patch.split(separator: "\n").contains { line in
            guard line.hasPrefix("+"), !line.hasPrefix("+++") else { return false }
            let body = line.dropFirst().trimmingCharacters(in: .whitespaces)
            guard body.lowercased().hasPrefix("status:") else { return false }
            let value = body.dropFirst("status:".count)
                .trimmingCharacters(in: CharacterSet.whitespaces.union(CharacterSet(charactersIn: "\"'")))
            return value.lowercased() == wanted
        }
    }

    /// `fileURL` relative to `repo`, or nil when it isn't inside it (a custom
    /// folder override). Mirrors `PerFileItemWriter`'s prefix rule but refuses
    /// instead of falling back to the bare filename.
    static func repoRelativePath(of fileURL: URL, repo: URL) -> String? {
        let full = fileURL.standardizedFileURL.path
        let prefix = repo.standardizedFileURL.path + "/"
        return full.hasPrefix(prefix) ? String(full.dropFirst(prefix.count)) : nil
    }
}
```

- [ ] **Step 4: Run and watch them pass** — `ItemActivityTests` (8 tests).

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/Models/ItemActivity.swift ScoutTests/PerFile/ItemActivityTests.swift
git commit -m "feat(perfile): label item history and find the resolving commit (#43)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: PerFileItemActivityModel

**Files:**
- Create: `Scout/PerFileItems/PerFileItemActivityModel.swift`
- Test: create `ScoutTests/PerFile/PerFileItemActivityModelTests.swift`

**Interfaces produced (all `@MainActor`):**
- `enum LoadState: Equatable { case loading, loaded([FileRevision]), unavailable(String), failed(String) }`
- `enum FilesState: Equatable { case loading, loaded([String]), failed(String) }`
- `@Published private(set) var state: LoadState`, `@Published private(set) var otherFiles: [String: FilesState]`
- `init(git: GitService, repoURL: URL)`, `func load(_ item: PerFileItem) async`, `func loadOtherFiles(for commit: Commit) async`

The model holds **no runs**. Labeling happens at render time from the live `SessionLogService.runs` (Task 6), so runs that finish loading later still link without a git refetch.

- [ ] **Step 1: Write the failing tests**

```swift
import Testing
import Foundation
@testable import Scout

@MainActor
@Suite("PerFileItemActivityModel")
struct PerFileItemActivityModelTests {

    private let repo = URL(fileURLWithPath: "/tmp/Scout")

    private func item(at path: String, status: ItemStatus = .open) -> PerFileItem {
        PerFileItem(fileURL: URL(fileURLWithPath: path), date: "2026-04-01",
                    title: "Example item", status: status, priority: .medium,
                    source: nil, area: nil, bodyMarkdown: "")
    }

    private func model(_ results: [ProcessResult]) -> (PerFileItemActivityModel, ScriptedRunner) {
        let runner = ScriptedRunner(scripted: results)
        return (PerFileItemActivityModel(git: GitService(repoURL: repo, runner: runner), repoURL: repo), runner)
    }

    @Test func loadPublishesRevisions() async throws {
        let out = "\u{1E}aaa\u{1F}aaa\u{1F}1776000000\u{1F}app: add wishlist item Example item\n\n+status: open\n"
        let (m, runner) = model([ProcessResult(exitCode: 0, stdout: Data(out.utf8), stderr: Data())])

        await m.load(item(at: "/tmp/Scout/docs/wishlist/2026-04-01-example-item.md"))

        guard case .loaded(let revs) = m.state else { Issue.record("got \(m.state)"); return }
        #expect(revs.map(\.id) == ["aaa"])
        #expect(runner.calls.first?.arguments.last == "docs/wishlist/2026-04-01-example-item.md")
    }

    @Test func gitErrorIsSurfaced() async {
        let (m, _) = model([ProcessResult(exitCode: 128, stdout: Data(), stderr: Data())])
        await m.load(item(at: "/tmp/Scout/docs/wishlist/x.md"))
        if case .failed = m.state {} else { Issue.record("expected .failed, got \(m.state)") }
    }

    @Test func outsideTheRepoIsUnavailableAndRunsNoGit() async {
        let (m, runner) = model([])
        await m.load(item(at: "/tmp/Elsewhere/x.md"))
        if case .unavailable = m.state {} else { Issue.record("expected .unavailable, got \(m.state)") }
        #expect(runner.calls.isEmpty)
    }

    @Test func otherFilesExcludeTheItemAndLoadOnce() async {
        let out = "docs/wishlist/x.md\nknowledge-base/example-topic.md\n"
        let hist = "\u{1E}aaa\u{1F}aaa\u{1F}1776000000\u{1F}research: example findings\n"
        let (m, runner) = model([
            ProcessResult(exitCode: 0, stdout: Data(hist.utf8), stderr: Data()),
            ProcessResult(exitCode: 0, stdout: Data(out.utf8), stderr: Data()),
        ])
        await m.load(item(at: "/tmp/Scout/docs/wishlist/x.md"))
        guard case .loaded(let revs) = m.state else { Issue.record("not loaded"); return }

        await m.loadOtherFiles(for: revs[0].commit)
        await m.loadOtherFiles(for: revs[0].commit)

        #expect(m.otherFiles["aaa"] == .loaded(["knowledge-base/example-topic.md"]))
        #expect(runner.calls.count == 2)   // history + one show
    }
}
```

- [ ] **Step 2: Run and watch them fail** — `-only-testing:ScoutTests/PerFileItemActivityModelTests`.

- [ ] **Step 3: Implement** `Scout/PerFileItems/PerFileItemActivityModel.swift`:

```swift
import Combine
import Foundation

/// Loads one item's file-scoped git history for `PerFileItemDetailView`, plus
/// each commit's other-files list on demand. Errors surface as `.failed`,
/// never an empty list (#47). One instance per opened item. (#43)
@MainActor
final class PerFileItemActivityModel: ObservableObject {

    enum LoadState: Equatable {
        case loading
        case loaded([FileRevision])
        case unavailable(String)
        case failed(String)
    }

    enum FilesState: Equatable {
        case loading
        case loaded([String])
        case failed(String)
    }

    @Published private(set) var state: LoadState = .loading
    @Published private(set) var otherFiles: [String: FilesState] = [:]

    private let git: GitService
    private let repoURL: URL
    private var relativePath: String?

    init(git: GitService, repoURL: URL) {
        self.git = git
        self.repoURL = repoURL
    }

    /// (Re)load the history. Keeps showing the previous result while a
    /// refresh is in flight; a cancelled load (the item changed again) never
    /// overwrites a newer one.
    func load(_ item: PerFileItem) async {
        guard let rel = ItemActivity.repoRelativePath(of: item.fileURL, repo: repoURL) else {
            state = .unavailable("History is only available for items inside the Scout vault's git repo.")
            return
        }
        relativePath = rel
        do {
            let revisions = try await git.fileHistory(relativePath: rel)
            guard !Task.isCancelled else { return }
            state = .loaded(revisions)
        } catch {
            guard !Task.isCancelled else { return }
            state = .failed("Couldn't load history — \(error.localizedDescription)")
        }
    }

    func loadOtherFiles(for commit: Commit) async {
        switch otherFiles[commit.id] {
        case .loaded, .loading: return
        default: break
        }
        otherFiles[commit.id] = .loading
        do {
            let files = try await git.filesChanged(inCommit: commit.id)
            otherFiles[commit.id] = .loaded(files.filter { $0 != relativePath })
        } catch {
            otherFiles[commit.id] = .failed("Couldn't list the commit's files — \(error.localizedDescription)")
        }
    }
}
```

- [ ] **Step 4: Run and watch them pass** — `PerFileItemActivityModelTests` (4 tests).

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/PerFileItemActivityModel.swift ScoutTests/PerFile/PerFileItemActivityModelTests.swift
git commit -m "feat(perfile): activity model — file history + lazy other-files (#43)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: CommitDiffView + PerFileItemDetailView

> **No unit tests.** Views aren't unit-tested here; services and models carry the tests. Verified by build plus the Task 9 smoke test. `AppState.requestOpenRun(_:)` lands in Task 8. **Do Task 8 Step 1 (the `AppState` additions) before building this task.**

**Files:**
- Create: `Scout/PerFileItems/Views/CommitDiffView.swift`, `Scout/PerFileItems/Views/PerFileItemDetailView.swift`

- [ ] **Step 1: `CommitDiffView`**

```swift
import SwiftUI

/// Scrollable monospace rendering of one file's patch with +/− coloring.
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
                }
            }
            .textSelection(.enabled)
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

- [ ] **Step 2: `PerFileItemDetailView`**

```swift
import SwiftUI

/// The Wishlist/Research detail pane body (#43): an outcome line for resolved
/// items, then every commit that touched the item's file, newest first. Each
/// row expands to this file's patch, the commit's other files, and (when
/// linked) a jump to the run in Control Center.
struct PerFileItemDetailView: View {
    let item: PerFileItem
    @ObservedObject var sessionLog: SessionLogService
    @StateObject private var model: PerFileItemActivityModel
    @EnvironmentObject private var appState: AppState
    @State private var expanded: Set<String> = []

    init(item: PerFileItem, git: GitService, repoURL: URL, sessionLog: SessionLogService) {
        self.item = item
        _sessionLog = ObservedObject(wrappedValue: sessionLog)
        _model = StateObject(wrappedValue: PerFileItemActivityModel(git: git, repoURL: repoURL))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                content
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // PerFileItem is Equatable: FSEvent republishes with identical content
        // don't refetch; a status/body change does.
        .task(id: item) { await model.load(item) }
    }

    @ViewBuilder
    private var content: some View {
        switch model.state {
        case .loading:
            ProgressView().frame(maxWidth: .infinity).padding(.top, 40)
        case .unavailable(let message):
            Text(message).font(DS.serif(14)).foregroundStyle(DS.Ink.p3).padding(.top, 24)
        case .failed(let message):
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .font(DS.sans(12)).foregroundStyle(DS.Status.err)
        case .loaded(let revisions):
            let entries = ItemActivity.entries(revisions: revisions, runs: sessionLog.runs,
                                               status: item.status, now: Date())
            if let outcome = ItemActivity.outcome(entries: entries, status: item.status) {
                Text(outcome.summary(date: Self.dayText))
                    .font(DS.sans(12.5, weight: .semibold)).foregroundStyle(DS.Ink.p1)
            }
            if entries.isEmpty {
                Text("No activity yet — this item hasn't been committed.")
                    .font(DS.serif(14)).foregroundStyle(DS.Ink.p3).padding(.top, 24)
            } else {
                ForEach(entries) { row($0) }
            }
        }
    }

    // MARK: - Timeline row

    @ViewBuilder
    private func row(_ entry: ItemActivityEntry) -> some View {
        let isOpen = expanded.contains(entry.id)
        VStack(alignment: .leading, spacing: 8) {
            Button { toggle(entry.id) } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: isOpen ? "chevron.down" : "chevron.right")
                        .font(.system(size: 9, weight: .semibold)).foregroundStyle(DS.Ink.p4)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(entry.source.label).font(DS.sans(11, weight: .semibold))
                                .foregroundStyle(DS.Ink.p3)
                            Text(Self.stampText(entry.commit.timestamp)).font(DS.mono(10))
                                .foregroundStyle(DS.Ink.p4)
                            if entry.isResolving {
                                Text("outcome").font(DS.mono(10)).foregroundStyle(DS.Status.ok)
                            }
                        }
                        Text(entry.commit.subject).font(DS.serif(13)).foregroundStyle(DS.Ink.p1)
                            .fixedSize(horizontal: false, vertical: true)
                        Text("\(entry.commit.shortSHA)  +\(entry.commit.insertions) −\(entry.commit.deletions)")
                            .font(DS.mono(10)).foregroundStyle(DS.Ink.p4)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plainHit)

            if isOpen { expandedBody(entry) }
        }
        .padding(.vertical, 6)
        .overlay(alignment: .bottom) { EditorialRule() }
    }

    @ViewBuilder
    private func expandedBody(_ entry: ItemActivityEntry) -> some View {
        if entry.revision.patch.isEmpty {
            Text("No change to this file in this commit.")
                .font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
        } else {
            CommitDiffView(patch: entry.revision.patch)
        }
        otherFilesSection(entry.commit)
        if let run = entry.source.run {
            Button { appState.requestOpenRun(run.id) } label: {
                Label("Open run in Control Center", systemImage: "arrow.up.right.square")
                    .font(DS.sans(11, weight: .medium))
            }
            .buttonStyle(.plainHit).foregroundStyle(DS.Ink.p2)
        }
    }

    @ViewBuilder
    private func otherFilesSection(_ commit: Commit) -> some View {
        switch model.otherFiles[commit.id] {
        case .loaded(let files) where files.isEmpty:
            EmptyView()
        case .loaded(let files):
            VStack(alignment: .leading, spacing: 2) {
                Text("Also changed in this commit (\(files.count))")
                    .font(DS.sans(10.5, weight: .semibold)).foregroundStyle(DS.Ink.p3)
                ForEach(files.prefix(20), id: \.self) { path in
                    Text(path).font(DS.mono(10)).foregroundStyle(DS.Ink.p3).lineLimit(1)
                }
                if files.count > 20 {
                    Text("+\(files.count - 20) more").font(DS.mono(10)).foregroundStyle(DS.Ink.p4)
                }
            }
        case .failed(let message):
            Text(message).font(DS.sans(10.5)).foregroundStyle(DS.Status.err)
        case .loading, nil:
            ProgressView().controlSize(.small)
                .task { await model.loadOtherFiles(for: commit) }
        }
    }

    // MARK: - Helpers

    private func toggle(_ id: String) {
        if expanded.contains(id) { expanded.remove(id) } else { expanded.insert(id) }
    }

    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMM d"; return f
    }()
    private static let stampFormatter: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MMM d, HH:mm"; return f
    }()
    private static func dayText(_ d: Date) -> String { dayFormatter.string(from: d) }
    private static func stampText(_ d: Date) -> String { stampFormatter.string(from: d) }
}
```

- [ ] **Step 3: Apply Task 8 Step 1** (`AppState.requestOpenRun`) so this builds.

- [ ] **Step 4: Build** — `** BUILD SUCCEEDED **`.

- [ ] **Step 5: Commit**

```bash
git add Scout/PerFileItems/Views/CommitDiffView.swift Scout/PerFileItems/Views/PerFileItemDetailView.swift Scout/Shell/AppState.swift
git commit -m "feat(perfile): item history pane + file-scoped diff view (#43)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: History button + side/full pane in the list

> **No unit tests** — view/layout. Build plus smoke test.

**Files:**
- Modify: `Scout/PerFileItems/Views/PerFileItemCardView.swift`, `Scout/PerFileItems/Views/PerFileListView.swift`

- [ ] **Step 1: Card — `isSelected` + History button**

Add after `let onResolve:`:

```swift
    /// Highlights the card whose history is open in the detail pane (#43).
    var isSelected: Bool = false
    /// Opens this item's history pane. Nil hides the History button.
    var onShowHistory: (() -> Void)? = nil
```

Append to the body's modifier chain, after `.editorialCard(padding: 18)`:

```swift
        .overlay(alignment: .leading) {
            if isSelected {
                RoundedRectangle(cornerRadius: 2).fill(DS.Ink.p1)
                    .frame(width: 3).padding(.vertical, 6)
            }
        }
```

In `actions`, insert right before `Spacer(minLength: 0)`, so it applies to both the active and resolved branches:

```swift
            if let onShowHistory {
                Button(action: onShowHistory) {
                    actionLabel("History", systemImage: "clock.arrow.circlepath", tint: DS.Ink.p2)
                }
                .buttonStyle(.plainHit)
                .help("Show this item's history")
            }
```

Extract the existing button label into a helper, and have `actionButton` use it so the two can't drift:

```swift
    private func actionLabel(_ label: String, systemImage: String, tint: Color) -> some View {
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
```

(`actionButton`'s `label:` becomes `actionLabel(label, systemImage: systemImage, tint: tint)`.)

- [ ] **Step 2: List — selection by id, live lookup, side/full pane**

In `PerFileListView`:

(a) After `@EnvironmentObject var writerBox: PerFileItemWriterBox` add `@EnvironmentObject var appState: AppState`. After `@State private var showingAdd = false` add:

```swift
    /// The item whose history pane is open. Stored by id and looked up live
    /// from `docService.items`, so the pane never shows a stale snapshot after
    /// Start/Done/Drop or an FSEvent reparse (#43).
    @State private var selectedItemID: String? = nil
    @State private var detailIsFull = false

    private var selectedItem: PerFileItem? {
        selectedItemID.flatMap { id in docService.items.first { $0.id == id } }
    }
```

(b) Move the current `ScrollView { … }` through `.background(DS.Paper.base)` **unchanged**, including the `VStack` and its #83 comment, into `private var listScroll: some View`. Then make `body`:

```swift
    var body: some View {
        ZStack(alignment: .topTrailing) {
            // The list keeps a stable slot so opening the pane doesn't reset
            // its scroll position.
            HStack(alignment: .top, spacing: 0) {
                listScroll.frame(maxWidth: .infinity)
                if !detailIsFull, let item = selectedItem {
                    sideDetail(item)
                        .frame(width: 460)
                        .padding(.vertical, 16).padding(.trailing, 16)
                }
            }
            if detailIsFull, let item = selectedItem {
                fullDetail(item).transition(.opacity)
            }
        }
        .background(
            Group {
                Button("") { closeDetail() }
                    .keyboardShortcut(".", modifiers: .command)
                Button("") { if selectedItemID != nil { detailIsFull.toggle() } }
                    .keyboardShortcut("f", modifiers: [.command, .shift])
            }
            .opacity(0)
            .frame(width: 0, height: 0)
        )
        .animation(.easeInOut(duration: 0.18), value: selectedItemID)
        .animation(.easeInOut(duration: 0.18), value: detailIsFull)
        .onChange(of: docService.items) { _, _ in
            // The file was deleted or renamed out from under the pane.
            if selectedItemID != nil && selectedItem == nil { closeDetail() }
        }
        .toolbar { /* unchanged */ }
        .sheet(isPresented: $showingAdd) { /* unchanged */ }
        .onAppear { docService.load() }
    }
```

(Keep the existing `.toolbar` and `.sheet` bodies verbatim. The comments above are placeholders for "unchanged", not code.)

(c) Add the panels, mirroring `ControlCenterView.sideDetail`/`fullScreenDetail`/`detailHeader`:

```swift
    // MARK: - History pane (mirrors ControlCenterView's detail panel)

    private func closeDetail() {
        selectedItemID = nil
        detailIsFull = false
    }

    private func historyView(_ item: PerFileItem) -> some View {
        PerFileItemDetailView(item: item, git: appState.gitService,
                              repoURL: appState.scoutDirectory,
                              sessionLog: appState.sessionLogService)
            .id(item.id)
    }

    private func sideDetail(_ item: PerFileItem) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            detailHeader(item, isExpanded: false)
            EditorialRule()
            historyView(item)
        }
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(DS.Paper.raised)
                .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(DS.Rule.soft, lineWidth: 0.5))
                .shadow(color: DS.Neumorphic.shadow.opacity(0.4), radius: 8, x: -2, y: 4)
        )
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func fullDetail(_ item: PerFileItem) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            detailHeader(item, isExpanded: true)
            EditorialRule()
            historyView(item)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(DS.Paper.base)
    }

    private func detailHeader(_ item: PerFileItem, isExpanded: Bool) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Button { closeDetail() } label: {
                Image(systemName: "chevron.left").font(.system(size: 12, weight: .medium))
            }
            .buttonStyle(.plainHit).foregroundStyle(DS.Ink.p3).help("Close (⌘.)")
            Text(item.title).font(DS.serif(16, weight: .medium)).foregroundStyle(DS.Ink.p1)
                .lineLimit(1)
            Spacer()
            ItemStatusPill(status: item.status)
            Button { detailIsFull.toggle() } label: {
                Image(systemName: isExpanded
                      ? "arrow.down.right.and.arrow.up.left"
                      : "arrow.up.left.and.arrow.down.right")
                    .font(.system(size: 12)).foregroundStyle(DS.Ink.p3)
            }
            .buttonStyle(.plainHit)
            .help(isExpanded ? "Collapse (⌘⇧F)" : "Expand to full screen (⌘⇧F)")
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
    }
```

(d) Thread selection into both `ForEach`es. Add these two arguments to the awaiting card and the resolved card:

```swift
                    isSelected: selectedItemID == item.id,
                    onShowHistory: { selectedItemID = item.id }
```

- [ ] **Step 3: Build** — `** BUILD SUCCEEDED **`. Then `git diff Scout/PerFileItems/Views/PerFileListView.swift | grep -n LazyVStack` must print nothing.

- [ ] **Step 4: Commit**

```bash
git add Scout/PerFileItems/Views/PerFileItemCardView.swift Scout/PerFileItems/Views/PerFileListView.swift
git commit -m "feat(perfile): History button + side/full history pane on Wishlist/Research (#43)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Cross-tab "Open run in Control Center"

> **No unit tests** — cross-view wiring. Build plus smoke test. Independently droppable: the patch already shows the evidence in-pane.

**Files:** `Scout/Shell/AppState.swift`, `Scout/Shell/MainWindowView.swift`, `Scout/ControlCenter/ControlCenterView.swift`

- [ ] **Step 1: AppState intent** *(also needed by Task 6)* — next to `@Published var fireNowError`:

```swift
    /// A run another tab asked to open (the Wishlist/Research history pane).
    /// ControlCenterView opens its detail and clears this. (#43)
    @Published var pendingRunToOpen: Run.ID? = nil

    /// A sidebar tab another view asked to switch to. MainWindowView applies
    /// it to its selection and clears it. (#43)
    @Published var requestedSidebar: SidebarItem? = nil
```

and near `fireNow`:

```swift
    /// Switch to Control Center and open `id`'s run detail. (#43)
    func requestOpenRun(_ id: Run.ID) {
        pendingRunToOpen = id
        requestedSidebar = .controlCenter
    }
```

- [ ] **Step 2: MainWindowView** — after the `.safeAreaInset(edge: .bottom, …) { … }` on the `NavigationSplitView`:

```swift
        .onChange(of: appState.requestedSidebar) { _, requested in
            guard let requested else { return }
            selection = requested
            appState.requestedSidebar = nil
        }
```

- [ ] **Step 3: ControlCenterView** — next to `openDetail(_:)`:

```swift
    /// Open a run requested from another tab and clear the intent, even when
    /// the id no longer resolves, so it can't fire later by surprise. (#43)
    private func openPendingRunIfNeeded() {
        guard let id = state.pendingRunToOpen else { return }
        state.pendingRunToOpen = nil
        if let run = state.sessionLogService.runs.first(where: { $0.id == id }) {
            detail = .side(run)
        }
    }
```

and after `.animation(.easeInOut(duration: 0.18), value: detail)` in `body`:

```swift
        .task { openPendingRunIfNeeded() }
        .onChange(of: state.pendingRunToOpen) { _, _ in openPendingRunIfNeeded() }
```

(`.task` covers Control Center being created *by* the sidebar switch; `.onChange` covers it already being on screen.)

- [ ] **Step 4: Build** — `** BUILD SUCCEEDED **`.

- [ ] **Step 5: Commit** — stage only what this task changed. If Task 6 already committed `AppState.swift`, it isn't re-staged.

```bash
git add Scout/Shell/MainWindowView.swift Scout/ControlCenter/ControlCenterView.swift Scout/Shell/AppState.swift
git commit -m "feat(perfile): jump from item history to the run in Control Center (#43)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Verification

- [ ] **Full suite:** `-only-testing:ScoutTests`. All green. Count = the Task 1 baseline + 4 (Task 1) + 13 (Task 2 `RunAttributionTests`) + 1 (Task 2 forward regression) − 2 (Task 2's deleted `commitsPrefix` tests) + 4 (Task 3) + 8 (Task 4) + 4 (Task 5). Explain any difference before proceeding.
- [ ] **Fixture literal check** (CLAUDE.md). Collect every new string literal from the four new test files and the two edited test files into `candidates.txt`, one per line (phrases like `example item`, `example-topic`, `nothing new on the example board`, `example findings`). Then run:
  ```bash
  grep -rIow -F -f candidates.txt ~/Scout | sort -u \
    | grep -v '/Scout/\.claude/' | awk -F: '{print $NF}' | sort | uniq -c | sort -rn
  ```
  Every invented token should land at zero. Generic English words will match; that's expected. Anything that looks like an identifier and scores >0 gets replaced.
- [ ] **No real-data leak:** `git diff origin/main -- ScoutTests docs | grep -nE '\b(AI|KAI|ST)-[0-9]+\b'` prints nothing.
- [ ] **Real-vault read-only check:** with the Debug build running against the real vault, `shasum` the parse cache before and after a launch. It *should* change once (v2 re-parse) and then stay stable across a second launch. No vault files may change: `git -C ~/Scout status --porcelain docs/wishlist knowledge-base/research-queue` must be identical before and after the smoke test, apart from writes you made deliberately through the app.
- [ ] **Manual smoke test** (the `run` skill or Xcode):
  1. **Wishlist** → **History** on an item with several commits → side pane opens; the card shows the accent bar; the list keeps its scroll position.
  2. Expand a run-made row → this file's patch only; the "Also changed in this commit" list loads; **Open run in Control Center** switches the tab and opens that run.
  3. A **resolved** item → "Resolved by … · date", and the `outcome` tag sits on the commit that added `status: done`, even if a later commit touched the file.
  4. **Done** on an active item while its pane is open → the status pill and outcome update in place (live lookup), and a new "You" row appears at the top.
  5. ⌘⇧F toggles full/side; ⌘. closes; ⌘. in Control Center still closes *its* detail.
  6. A brand-new item before commit → "No activity yet"; the Research tab behaves the same.

---

## Self-Review

**1. Spec coverage:**
- Zone-aware start → Task 1. Shared window + family attribution, forward and reverse → Task 2.
- File-scoped diff + other files → Tasks 3, 5, 6.
- Resolving = commit that added the terminal status; uncommitted outcome → Task 4.
- Four source labels → Task 4. History button, live lookup, VStack preserved, ⌘. / ⌘⇧F → Task 7.
- Open run → Task 8.
- Errors surfaced (`.failed`, `.unavailable`) → Tasks 5–6.
- Session index evaluated and not used → spec only.

**2. Placeholder scan:** Every code step is complete. The two `/* unchanged */` markers in Task 7 refer to existing code that is kept verbatim, and are labeled as such. One build-order dependency (Task 6 → Task 8 Step 1) is called out.

**3. Type consistency:** The following names match across tasks and tests:
- `ParsedBody.startedAt`, `Run.headerStartedAt`
- `CommitFamily.of(subject:)` / `.runnerScript` / `.displayName`
- `Run.commitWindow(now:)` / `.claims(_:)`, `CommitRunLinker.run(for:in:now:)`
- `FileRevision{commit,patch}`, `GitService.fileHistory(relativePath:)` / `parseFileHistory(_:)` / `filesChanged(inCommit:)`
- `ActivitySource{run,family,app,other}`, `ItemActivityEntry{revision,source,isResolving}`, `ItemOutcome{resolved,resolvedUncommitted}.summary(date:)`
- `ItemActivity.source/entries/outcome/addsStatus/repoRelativePath`
- `PerFileItemActivityModel{state,otherFiles,load(_:),loadOtherFiles(for:)}`, `PerFileItemDetailView(item:git:repoURL:sessionLog:)`
- `AppState.requestOpenRun(_:)` / `pendingRunToOpen` / `requestedSidebar`
