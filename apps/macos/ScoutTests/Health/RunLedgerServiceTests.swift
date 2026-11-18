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
        #expect(svc.ledger?.runs.count == 14)
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
    }
}
