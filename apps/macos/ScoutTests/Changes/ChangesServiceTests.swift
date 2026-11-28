import Foundation
import Testing
@testable import Scout

@MainActor
@Suite("ChangesService")
struct ChangesServiceTests {
    private static let exe = URL(fileURLWithPath: "/usr/bin/scoutctl")

    private func service(_ runner: RuleBasedRunner, engine: URL? = ChangesServiceTests.exe) -> ChangesService {
        ChangesService(scoutctl: { engine }, runner: runner,
                       logsDirectory: URL(fileURLWithPath: "/tmp/nonexistent-\(UUID().uuidString)"),
                       pendingDirectory: URL(fileURLWithPath: "/tmp/nonexistent-\(UUID().uuidString)"),
                       fileEvents: NoopFS(), clock: FixedClock(date: Date(timeIntervalSince1970: 1_800_000_000)))
    }

    private func fixture(_ name: String) throws -> String {
        String(decoding: try ChangesContractTests.data(name), as: UTF8.self)
    }

    /// The three reads answered with the three contract fixtures.
    private func answering(_ runner: RuleBasedRunner) throws {
        runner.on(tool: "scoutctl", prefix: ["changes", "digest"], stdout: try fixture("changes-digest"))
        runner.on(tool: "scoutctl", prefix: ["changes", "--json"], stdout: try fixture("changes"))
        runner.on(tool: "scoutctl", prefix: ["engagement", "--json"], stdout: try fixture("engagement"))
    }

    @Test func loadsAllThreeDocuments() async throws {
        let runner = RuleBasedRunner()
        try answering(runner)
        let svc = service(runner)
        #expect(svc.state == .idle)
        await svc.refresh()
        #expect(svc.snapshot?.changes.changes.count == 7)
        #expect(svc.lastChecked == Date(timeIntervalSince1970: 1_800_000_000))
        let calls = runner.calls.map(\.arguments)
        #expect(calls.count == 3)
        #expect(calls.contains(["changes", "--json", "--since", "365d", "--limit", "5000"]))
        #expect(calls.contains(["engagement", "--json", "--since", "365d"]))
        #expect(calls.contains(["changes", "digest", "--json"]))
    }

    @Test func missingCommandIsUnsupported() async throws {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["changes", "digest"], stderr: "Error: No such command 'digest'.", exit: 2)
        runner.on(tool: "scoutctl", prefix: ["changes", "--json"], stdout: try fixture("changes"))
        runner.on(tool: "scoutctl", prefix: ["engagement", "--json"], stdout: try fixture("engagement"))
        let svc = service(runner)
        await svc.refresh()
        #expect(svc.state == .unsupported)
    }

    @Test func aNewerSchemaIsUnsupported() async throws {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["changes", "digest"], stdout: try fixture("changes-digest"))
        runner.on(tool: "scoutctl", prefix: ["changes", "--json"], stdout: #"{"schema_version": 2}"#)
        runner.on(tool: "scoutctl", prefix: ["engagement", "--json"], stdout: try fixture("engagement"))
        let svc = service(runner)
        await svc.refresh()
        #expect(svc.state == .unsupported)
    }

    @Test func anyOtherFailureIsAShortReasonNeverContent() async throws {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["changes", "digest"], stdout: try fixture("changes-digest"))
        runner.on(tool: "scoutctl", prefix: ["changes", "--json"], stdout: try fixture("changes"))
        runner.on(tool: "scoutctl", prefix: ["engagement", "--json"], stdout: "SECRET-CONTENT not json", stderr: "boom", exit: 1)
        let svc = service(runner)
        await svc.refresh()
        guard case .failed(let reason) = svc.state else { Issue.record("expected failed, got \(svc.state)"); return }
        #expect(reason == "The engine's engagement report couldn't be read.")
        #expect(!reason.contains("SECRET"))
    }

    @Test func aRunnerThatCannotSpawnFails() async {
        let runner = RuleBasedRunner()   // no rules: every call throws ENOENT
        let svc = service(runner)
        await svc.refresh()
        #expect(svc.state == .failed("Couldn't run Scout's engine."))
    }

    @Test func noEngineRunsNothing() async {
        let runner = RuleBasedRunner()
        let svc = service(runner, engine: nil)
        await svc.refresh()
        #expect(runner.calls.isEmpty)
        #expect(svc.state == .failed("Scout's engine isn't set up yet."))
    }

    /// m-b: once loaded, a failed refresh (a bad read, a spawn failure, the engine gone) keeps the snapshot and
    /// reports why in `refreshProblem`; the next good read clears it.
    @Test func aFailureAfterAGoodReadKeepsTheSnapshot() async throws {
        let runner = RuleBasedRunner()
        try answering(runner)
        var engine: URL? = Self.exe
        let svc = ChangesService(
            scoutctl: { engine }, runner: runner,
            logsDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"),
            pendingDirectory: URL(fileURLWithPath: "/tmp/none-\(UUID().uuidString)"), fileEvents: NoopFS())
        await svc.refresh()
        let good = try #require(svc.snapshot)
        #expect(svc.refreshProblem == nil)
        engine = nil
        await svc.refresh()
        #expect(svc.snapshot == good && svc.refreshProblem == "Scout's engine isn't set up yet.")
        engine = URL(fileURLWithPath: "/usr/bin/not-scoutctl")   // no rule answers it: the spawn fails
        await svc.refresh()
        #expect(svc.snapshot == good && svc.refreshProblem == "Couldn't run Scout's engine.")
        engine = Self.exe
        await svc.refresh()
        #expect(svc.snapshot == good && svc.refreshProblem == nil)
    }

    @Test func aFailureBeforeAnyGoodReadIsTheState() async {
        let svc = service(RuleBasedRunner())
        await svc.refresh()
        #expect(svc.state == .failed("Couldn't run Scout's engine.") && svc.refreshProblem == nil)
    }

    @Test func aLaterGoodReadReplacesAnEarlierFailure() async throws {
        let runner = RuleBasedRunner()
        let svc = service(runner)
        await svc.refresh()
        #expect(svc.snapshot == nil)
        try answering(runner)
        await svc.refresh()
        #expect(svc.snapshot != nil)
    }
}
