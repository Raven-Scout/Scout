import Foundation
import Testing
@testable import Scout

/// Records each call and returns a canned result (or throws).
private final class LedgerRecordingRunner: ProcessRunner, @unchecked Sendable {
    var result: ProcessResult?
    private(set) var calls: [[String]] = []
    init(result: ProcessResult?) { self.result = result }
    func run(executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?) async throws -> ProcessResult {
        calls.append(arguments)
        guard let result else { throw CocoaError(.fileNoSuchFile) }
        return result
    }
}

@MainActor
@Suite("RunLedgerService")
struct RunLedgerServiceTests {
    private func service(_ runner: any ProcessRunner, prefix: [String] = []) -> RunLedgerService {
        RunLedgerService(scoutctl: URL(fileURLWithPath: "/usr/bin/false"), argumentsPrefix: prefix, runner: runner,
                         logsDirectory: URL(fileURLWithPath: "/tmp/nonexistent-\(UUID().uuidString)"),
                         fileEvents: NoopFS())
    }

    @Test func loadsTheLedger() async throws {
        let runner = LedgerRecordingRunner(result: ProcessResult(exitCode: 0, stdout: try RunLedgerContractTests.fixtureData(), stderr: Data()))
        let svc = service(runner, prefix: ["--engine"])
        #expect(svc.state == .loading)
        await svc.refresh()
        #expect(runner.calls == [["--engine", "runs", "--json", "--since", "14d"]])
        #expect(svc.ledger?.runs.count == 16)
        #expect(svc.needsAttention)
        #expect(svc.lastChecked != nil)
        // Each finding's first-seen time is the read's time, and survives a re-read.
        let ids = Set(svc.health?.findings.map(\.id) ?? [])
        #expect(!ids.isEmpty && Set(svc.firstSeen.keys) == ids)
        let first = svc.firstSeen
        await svc.refresh()
        #expect(svc.firstSeen == first)
    }

    /// A read that fails keeps the first-seen record, so a transient engine
    /// error doesn't restart the banner's wait.
    @Test func aFailedReadKeepsFirstSeen() async throws {
        let runner = LedgerRecordingRunner(result: ProcessResult(exitCode: 0, stdout: try RunLedgerContractTests.fixtureData(), stderr: Data()))
        let svc = service(runner)
        await svc.refresh()
        let first = svc.firstSeen
        #expect(!first.isEmpty)
        runner.result = nil
        await svc.refresh()
        guard case .failed = svc.state else { Issue.record("got \(svc.state)"); return }
        #expect(svc.firstSeen == first)
    }

    @Test func anOlderEngineIsUnsupportedNotBroken() async {
        let stderr = Data("Usage: scoutctl [OPTIONS] COMMAND [ARGS]...\nTry 'scoutctl --help' for help.\n\nError: No such command 'runs'.\n".utf8)
        let svc = service(LedgerRecordingRunner(result: ProcessResult(exitCode: 2, stdout: Data(), stderr: stderr)))
        await svc.refresh()
        guard case .unsupported(let message) = svc.state else { Issue.record("got \(svc.state)"); return }
        #expect(message.contains("Update the engine"))
        #expect(!svc.needsAttention)
    }

    @Test func aNewerSchemaAsksForAnAppUpdate() {
        let state = RunLedgerService.state(for: ProcessResult(exitCode: 0, stdout: Data(#"{"schema_version": 9}"#.utf8), stderr: Data()))
        guard case .unsupported(let message) = state else { Issue.record("got \(state)"); return }
        #expect(message.contains("Update the Scout app"))
    }

    @Test func theEnginesOwnErrorIsShown() {
        let out = Data(#"{"schema_version": 1, "error": "vault directory missing: /Users/alex/Scout"}"#.utf8)
        #expect(RunLedgerService.state(for: ProcessResult(exitCode: 2, stdout: out, stderr: Data()))
                == .failed("vault directory missing: /Users/alex/Scout"))
    }

    @Test func aTracebackIsAFailureWithItsTail() {
        let state = RunLedgerService.state(for: ProcessResult(exitCode: 1, stdout: Data(), stderr: Data("Traceback: boom\n".utf8)))
        #expect(state == .failed("Traceback: boom"))
        #expect(RunLedgerService.state(for: ProcessResult(exitCode: 1, stdout: Data(), stderr: Data()))
                == .failed("The engine's run report couldn't be read."))
    }

    @Test func aMissingEngineIsAFailure() async {
        let svc = service(LedgerRecordingRunner(result: nil))
        await svc.refresh()
        guard case .failed(let message) = svc.state else { Issue.record("got \(svc.state)"); return }
        #expect(message.hasPrefix("Couldn't run Scout's engine"))
    }
}

@Suite("Ledger status overlay")
struct LedgerOverlayTests {
    private func run(_ log: String, status: RunStatus, startedAt: Date) -> Run {
        Run.make(type: .consolidation, startedAt: startedAt, status: status, logPath: URL(fileURLWithPath: "/v/.scout-logs/\(log)"))
    }

    /// The bug C3 patched with log lines: a safety skip has no "finished"
    /// line, so the log alone reads as running, then orphaned.
    @Test func aSafetySkipIsASkipNotAnOrphan() {
        let t = Date(timeIntervalSince1970: 1_800_000_000)
        let runs = [run("scout-a.log", status: .orphaned, startedAt: t), run("scout-b.log", status: .running, startedAt: t + 60)]
        let out = SessionLogService.overlayLedger(runs, statuses: ["scout-a.log": .skippedGates, "scout-b.log": .skippedSandbox])
        #expect(out.map(\.status) == [.skippedGates, .skippedSandbox])
    }

    @Test func runsWithoutARowKeepTheirLogStatus() {
        let t = Date(timeIntervalSince1970: 1_800_000_000)
        let runs = [run("scout-a.log", status: .running, startedAt: t), run("scout-b.log", status: .rateLimited, startedAt: t)]
        let out = SessionLogService.overlayLedger(runs, statuses: ["scout-b.log": .success])
        #expect(out.map(\.status) == [.running, .success])
        #expect(SessionLogService.overlayLedger(runs, statuses: [:]) == runs)
    }

    @MainActor
    @Test func applyLedgerRepublishesRuns() async throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("ledger-overlay-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        // A skip the runner logged without a finish line.
        try "=== Scout run starting at Mon Nov 16 08:30:00 UTC 2026 ===\n=== privacy checks: skipping this run ===\n"
            .write(to: dir.appendingPathComponent("scout-2026-11-16_08-30.log"), atomically: true, encoding: .utf8)
        let tracker = UsageTrackerService(trackerURL: dir.appendingPathComponent("usage.jsonl"), fileEvents: NoopFS())
        let svc = SessionLogService(logsDirectory: dir, trackerService: tracker, fileEvents: NoopFS(),
                                    parseCacheURL: dir.appendingPathComponent("cache.json"))
        _ = try await svc.loadInitial()
        #expect(svc.runs.count == 1)
        let ledger = try RunLedgerContractTests.fixture()
        svc.applyLedger(ledger.entriesByLog)
        #expect(svc.runs.first?.status == .skippedGates)
        // Card R3: the row's slot rides along, so "Run again" fires it.
        #expect(svc.runs.first?.slotKey == "morning-briefing")
    }

    /// Finding #19: two consolidation runs of different schedules each get
    /// their own slot from the ledger, not one picked from the run type.
    @Test func eachRunGetsItsOwnRowsSlot() {
        let t = Date(timeIntervalSince1970: 1_800_000_000)
        let runs = [run("scout-a.log", status: .success, startedAt: t), run("scout-b.log", status: .failure, startedAt: t + 60)]
        let out = SessionLogService.overlayLedger(
            runs, statuses: [:],
            slots: ["scout-a.log": "morning-consolidation", "scout-b.log": "evening-consolidation"]
        )
        #expect(out.map(\.slotKey) == ["morning-consolidation", "evening-consolidation"])
        #expect(out.map(\.rerunSlotKey) == ["morning-consolidation", "evening-consolidation"])
        // A status change keeps the slot.
        #expect(out[1].with(status: .timeout).slotKey == "evening-consolidation")
    }
}

@Suite("Run again's slot")
struct RerunSlotTests {
    @Test func aScheduledSlotIsRerun() {
        #expect(Run.make(type: .consolidation, slotKey: "evening-consolidation").rerunSlotKey == "evening-consolidation")
        // A schedule made in the app, which the run type could never name.
        #expect(Run.make(type: .research, slotKey: "late-research").rerunSlotKey == "late-research")
    }

    /// No ledger row: Scout doesn't know the slot, so nothing is guessed.
    @Test func anUnknownSlotIsNotGuessed() {
        #expect(Run.make(type: .consolidation).rerunSlotKey == nil)
        #expect(Run.make(type: .consolidation, slotKey: "unknown").rerunSlotKey == nil)
        #expect(Run.make(type: .consolidation, slotKey: "  ").rerunSlotKey == nil)
    }

    /// An event-triggered run's slot is the skill it ran, maybe a schedule's
    /// name too: never re-run it as that schedule (review of #686).
    @Test func anEventTriggeredRunIsNotRerun() {
        #expect(Run.make(type: .consolidation, slotKey: "review-pr", trigger: "event").rerunSlotKey == nil)
        #expect(Run.make(type: .consolidation, slotKey: "evening-consolidation", trigger: "event").rerunSlotKey == nil)
        #expect(Run.make(type: .consolidation, slotKey: "evening-consolidation", trigger: "some-new-kind").rerunSlotKey == nil)
        for trigger in [nil, "schedule", "heartbeat", "manual"] as [String?] {
            #expect(Run.make(type: .consolidation, slotKey: "evening-consolidation", trigger: trigger).rerunSlotKey
                    == "evening-consolidation", "\(trigger ?? "nil")")
        }
    }

    @Test func theLedgersTriggerRidesAlong() {
        let run = Run.make(type: .consolidation, logPath: URL(fileURLWithPath: "/v/.scout-logs/scout-a.log"))
        let out = SessionLogService.overlayLedger(
            [run], statuses: [:], slots: ["scout-a.log": "review-pr"], triggers: ["scout-a.log": "event"]
        )
        #expect(out[0].trigger == "event")
        #expect(out[0].rerunSlotKey == nil)
        #expect(out[0].with(status: .failure).trigger == "event")
    }

    /// A run started outside the schedule has no slot `fire-now` knows.
    @Test func aManualRunHasNoSlotToRerun() {
        for key in ["manual", "dreaming-manual", "research-manual"] {
            #expect(Run.make(type: .manual, slotKey: key).rerunSlotKey == nil, "\(key)")
        }
    }
}

/// Card C9, part two: each run's write protection and integrity check, from its ledger row.
@MainActor
@Suite("Run protection on each run")
struct RunProtectionOnRunsTests {

    @Test func overlayCarriesEachRunsProtectionFromTheLedger() {
        let t = Date(timeIntervalSince1970: 1_800_000_000)
        let a = Run.make(startedAt: t, endedAt: t.addingTimeInterval(60), logPath: URL(fileURLWithPath: "/l/scout-a.log"))
        let b = Run.make(startedAt: t, endedAt: t.addingTimeInterval(60), logPath: URL(fileURLWithPath: "/l/scout-b.log"))
        let off = Run.Protection(sandbox: "off", sandboxSource: "switch", integrity: "clean")
        let out = SessionLogService.overlayLedger([a, b], statuses: [:], protection: ["scout-a.log": off])
        #expect(SessionLogService.restoreCommitPrefix(mode: nil) == nil)
        #expect(out[0].protection == off && out[0].ranUnprotected && !out[0].changedProtectedFile)
        #expect(out[1].protection == nil && !out[1].ranUnprotected)
    }

    @Test func onlyARunThatRanCountsAsUnprotected() {
        let off = Run.Protection(sandbox: "failed")
        #expect(Run.make(status: .success, protection: off).ranUnprotected)
        #expect(Run.make(status: .failure, protection: off).ranUnprotected)
        #expect(!Run.make(status: .skippedSandbox, protection: off).ranUnprotected)   // it never ran
        #expect(!Run.make(status: .success, protection: .init(sandbox: "enforced")).ranUnprotected)
        #expect(!Run.make(status: .success, protection: .init(sandbox: nil)).ranUnprotected)   // an older runner
        #expect(Run.make(protection: .init(sandbox: "enforced", integrity: "restored")).changedProtectedFile)
        #expect(Run.make(protection: .init(sandbox: "enforced", integrity: "unrestored")).changedProtectedFile)
        #expect(!Run.make(protection: .init(sandbox: "enforced", integrity: "error")).changedProtectedFile)
    }

    @Test func theProtectedFileBlockNamesTheRestoringCommit() {
        let commit = Commit(id: String(repeating: "a", count: 40), shortSHA: "aaaaaaa", timestamp: Date(),
                            subject: "Scout: restore protected files a scout run changed in git",
                            filesChanged: 1, insertions: 1, deletions: 1)
        let found = ProtectedFileBlock(restored: true, commits: [commit], showChange: {}, openLog: {})
        #expect(found.title == "Scout put the protected file back")
        #expect(found.message.hasSuffix("in commit aaaaaaa."))
        let notFound = ProtectedFileBlock(restored: true, commits: [], showChange: {}, openLog: {})
        #expect(notFound.message.contains("The run's log names the file and the commit."))
        let stuck = ProtectedFileBlock(restored: false, commits: [], showChange: {}, openLog: {})
        #expect(stuck.title.contains("couldn't put it back") && stuck.message.contains("by hand"))
        // A branch another session has checked out is left alone: say so, and that a reset or stash won't fix it.
        #expect(stuck.message.contains("Another session has the branch checked out, so Scout left that branch alone"))
        #expect(stuck.message.contains("git reset --hard or git stash there keeps the bad file"))
        // Only this run's own mode: a run that started right after another's restore never shows that commit.
        #expect(SessionLogService.restoreCommitPrefix(mode: "evening-consolidation")
                == "Scout: restore protected files a evening-consolidation run changed in git")
        #expect(SessionLogService.restoreCommitPrefix(mode: "") == nil)
    }

    @Test func theRunBadgeSaysWhatHappened() {
        #expect(ProtectionBadge(kind: .unprotected("Write protection turned off in Settings")).title == "Unprotected")
        #expect(ProtectionBadge(kind: .unprotected("Write protection turned off in Settings")).help
                .hasPrefix("Write protection turned off in Settings."))
        #expect(ProtectionBadge(kind: .fileChanged(restored: true)).title == "File put back")
        #expect(ProtectionBadge(kind: .fileChanged(restored: false)).title == "Protected file changed")
        #expect(ProtectionBadge(kind: .unchecked).title == "Files not checked")
    }

    @Test func aRunGetsEveryBadgeItEarns() {
        let both = Run.make(status: .success, protection: .init(sandbox: "off", sandboxSource: "switch", integrity: "restored"))
        #expect(ProtectionBadge.kinds(for: both).map(\.title) == ["File put back", "Unprotected"])
        let unchecked = Run.make(status: .success, protection: .init(sandbox: "enforced", integrity: "error"))
        #expect(ProtectionBadge.kinds(for: unchecked).map(\.title) == ["Files not checked"])
        #expect(unchecked.protectedFilesUnchecked)
        #expect(ProtectionBadge.kinds(for: Run.make(status: .success, protection: .init(sandbox: "enforced", integrity: "clean"))).isEmpty)
        #expect(!Run.make(status: .skippedSandbox, protection: .init(sandbox: "failed", integrity: "unchecked")).protectedFilesUnchecked)
        #expect(SessionsListView.matches(both, "put back") && SessionsListView.matches(both, "unprotected"))
    }
}
