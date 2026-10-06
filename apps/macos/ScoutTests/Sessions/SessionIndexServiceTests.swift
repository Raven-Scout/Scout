import Combine
import Foundation
import Testing
@testable import Scout

/// Answers `scoutctl session index` calls: `--no-gh` ones from `fast`, the
/// others from `pr`. Either lane can be held open to prove the other one does
/// not wait for it.
actor ScriptedSessionsRunner: ProcessRunner {
    private(set) var calls: [[String]] = []
    private(set) var environments: [[String: String]] = []
    private var fast: ProcessResult
    private var pr: ProcessResult
    private var holdingFast = false
    private var holdingPR = false
    private var fastWaiters: [CheckedContinuation<Void, Never>] = []
    private var prWaiters: [CheckedContinuation<Void, Never>] = []

    init(fast: ProcessResult, pr: ProcessResult? = nil) {
        self.fast = fast
        self.pr = pr ?? fast
    }

    var fastCalls: Int { calls.filter { $0.contains("--no-gh") }.count }
    var prCalls: Int { calls.filter { !$0.contains("--no-gh") }.count }
    /// Appearing runs one fast build and one PR build, which a fast build follows.
    var settledAfterAppearing: Bool { prCalls == 1 && fastCalls >= 2 }

    func setFast(_ result: ProcessResult) { fast = result }
    func holdFast() { holdingFast = true }
    func holdPR() { holdingPR = true }
    func releaseFast() { holdingFast = false; fastWaiters.forEach { $0.resume() }; fastWaiters = [] }
    func releasePR() { holdingPR = false; prWaiters.forEach { $0.resume() }; prWaiters = [] }

    nonisolated func run(
        executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?
    ) async throws -> ProcessResult {
        await answer(arguments, environment: environment)
    }

    private func answer(_ arguments: [String], environment: [String: String]) async -> ProcessResult {
        calls.append(arguments)
        environments.append(environment)
        if arguments.contains("--no-gh") {
            if holdingFast { await withCheckedContinuation { fastWaiters.append($0) } }
            return fast
        }
        if holdingPR { await withCheckedContinuation { prWaiters.append($0) } }
        return pr
    }
}

@MainActor
@Suite("SessionIndexService", .serialized)
struct SessionIndexServiceTests {

    private nonisolated static let quick = SessionsRefresh.Intervals(
        eventWindow: .milliseconds(10),
        visibleHeartbeat: .seconds(3600),
        hiddenHeartbeat: .seconds(3600),
        prInterval: .seconds(3600)
    )

    @MainActor
    private struct Harness {
        let service: SessionIndexService
        let runner: ScriptedSessionsRunner
        let events: InjectableFS
        let vault: URL
        let roots: [URL]

        func tearDown() {
            service.stop()
            try? FileManager.default.removeItem(at: vault)
        }
    }

    private func harness(
        runner: ScriptedSessionsRunner,
        intervals: SessionsRefresh.Intervals = quick,
        prefix: [String] = []
    ) throws -> Harness {
        let fm = FileManager.default
        let vault = fm.temporaryDirectory.appendingPathComponent("sessions-service-\(UUID().uuidString)")
        let cache = vault.appendingPathComponent(".scout-cache", isDirectory: true)
        let roots = ["claude-code-sessions", "sessions", "projects"].map {
            vault.appendingPathComponent("sources/\($0)", isDirectory: true)
        }
        for dir in [cache] + roots { try fm.createDirectory(at: dir, withIntermediateDirectories: true) }
        let events = InjectableFS()
        let service = SessionIndexService(configuration: .init(
            scoutctl: URL(fileURLWithPath: "/usr/bin/env"),
            argumentsPrefix: prefix,
            runner: runner,
            fileEvents: events,
            indexFile: cache.appendingPathComponent("sessions-index.json"),
            watchRoots: roots,
            intervals: intervals,
            clock: FixedSessionsClock()
        ))
        return Harness(service: service, runner: runner, events: events, vault: vault, roots: roots)
    }

    /// Poll an actor-backed condition; liveness, not latency, so the budget is generous.
    private func eventually(_ timeout: Duration = .seconds(20), _ condition: () async -> Bool) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now + timeout
        while clock.now < deadline {
            if await condition() { return true }
            try? await Task.sleep(for: .milliseconds(10))
        }
        return await condition()
    }

    private func fixtureVariant(_ change: (inout [String: Any]) -> Void) throws -> Data {
        var object = try SessionsFixture.object()
        change(&object)
        return try SessionsFixture.encode(object)
    }

    // MARK: Publishing

    @Test func aFastBuildPublishesTheIndexAndTheBadge() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner, prefix: ["scoutctl"])
        defer { h.tearDown() }
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.count == 11)
        #expect(h.service.needsYouCount == 1)
        #expect(h.service.availability == .ok && h.service.lastError == nil)
        #expect(h.service.lastRefreshAt == FixedSessionsClock.instant)
        #expect(await runner.calls == [["scoutctl", "session", "index", "--json", "--no-gh"]])
    }

    @Test func aRebuildWithTheSameContentDoesNotRepublish() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        var publishes = 0
        let watch = h.service.$index.dropFirst().sink { _ in publishes += 1 }
        defer { watch.cancel() }
        await runner.setFast(.ok(try fixtureVariant { $0["generated_at"] = "2026-09-15T12:00:02Z" }))
        await h.service.refreshFast()
        #expect(publishes == 0)
        await runner.setFast(.ok(try fixtureVariant { object in
            var sessions = object["sessions"] as? [[String: Any]] ?? []
            sessions[0]["state_reasons"] = ["CI failing"]
            object["sessions"] = sessions
        }))
        await h.service.refreshFast()
        #expect(publishes == 1)
        #expect(h.service.index?.sessions.first?.stateReasons == ["CI failing"])
    }

    // MARK: Lanes

    @Test func requestsDuringABuildCollapseIntoOneFollowUp() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await runner.holdFast()
        h.service.requestFast()
        #expect(await eventually { await runner.fastCalls == 1 })
        for _ in 0..<5 { h.service.requestFast() }
        await runner.releaseFast()
        await h.service.refreshFast()
        #expect(await runner.fastCalls == 2)
    }

    @Test func thePRLaneNeverBlocksTheFastLaneAndIsNeverPublished() async throws {
        let prIndex = try fixtureVariant { object in
            object["source_counts"] = ["desktop": 10, "cli_only": 1, "open": 3, "running": 1, "prs_refreshed": 3]
            object["source_errors"] = [["source": "gh", "message": "pr view failed: example-org/example-repo#110"]]
            var sessions = object["sessions"] as? [[String: Any]] ?? []
            sessions[0]["title"] = "A 30-second-old snapshot"
            object["sessions"] = sessions
        }
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()), pr: .ok(prIndex))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await runner.holdPR()
        h.service.requestPRs()
        #expect(await eventually { await runner.prCalls == 1 })
        h.service.requestPRs()  // single-flight: ignored while one runs
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.count == 11)
        #expect(await runner.prCalls == 1)

        await runner.releasePR()
        await h.service.refreshPRs()
        #expect(h.service.prStatus?.refreshed == 3)
        #expect(h.service.prStatus?.errors.map(\.source) == ["gh"])
        // The PR build's own index is never shown; a fast build follows it.
        #expect(await eventually { await runner.fastCalls == 2 })
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.first?.title == "Fix the parser")
    }

    @Test func bothLanesRunWithTheUsersToolDirectoriesOnPath() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        await h.service.refreshPRs()
        let environments = await runner.environments
        #expect(environments.count >= 2)
        for environment in environments {
            let path = (environment["PATH"] ?? "").split(separator: ":").map(String.init)
            #expect(path.contains("/opt/homebrew/bin") && path.contains("/usr/local/bin"), "PATH was \(path)")
        }
    }

    // MARK: Failures

    @Test func aFailedBuildKeepsTheLastIndexAndSaysWhy() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        await runner.setFast(.failed(1, stderr: "session index: could not write the index: disk full"))
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.count == 11)
        #expect(h.service.availability == .ok)
        let message = h.service.lastError ?? ""
        #expect(message.contains("exited 1") && message.contains("disk full"), "got: \(message)")
    }

    @Test func aFailedFirstBuildShowsTheIndexOnDisk() async throws {
        let runner = ScriptedSessionsRunner(fast: .failed(1, stderr: "boom"))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        try SessionsFixture.data().write(to: h.vault.appendingPathComponent(".scout-cache/sessions-index.json"))
        await h.service.refreshFast()
        #expect(h.service.index?.sessions.count == 11)
        #expect(h.service.lastError?.contains("boom") == true)
    }

    @Test func anEngineWithoutSessionIndexIsTooOldAndSkipsPRBuilds() async throws {
        let runner = ScriptedSessionsRunner(fast: .failed(2, stderr: "Usage: scoutctl session [OPTIONS] COMMAND\nError: No such command 'index'."))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        #expect(h.service.availability == .engineTooOld)
        #expect(h.service.lastError?.contains("0.11.0") == true)
        h.service.requestPRs()
        await h.service.refreshPRs()
        #expect(await runner.prCalls == 0)
    }

    @Test func scoutctlMissingFromPathIsReported() async throws {
        let runner = ScriptedSessionsRunner(fast: .failed(127, stderr: "env: scoutctl: No such file or directory"))
        let h = try harness(runner: runner, prefix: ["scoutctl"])
        defer { h.tearDown() }
        await h.service.refreshFast()
        #expect(h.service.availability == .engineMissing)
        #expect(h.service.lastError?.contains("scoutctl not found") == true)
    }

    @Test func aScoutctlThatCannotBeLaunchedIsReported() async throws {
        let fm = FileManager.default
        let vault = fm.temporaryDirectory.appendingPathComponent("sessions-service-\(UUID().uuidString)")
        defer { try? fm.removeItem(at: vault) }
        let service = SessionIndexService(configuration: .init(
            scoutctl: vault.appendingPathComponent("no-such-scoutctl"),
            argumentsPrefix: [],
            runner: SystemProcessRunner(),
            fileEvents: NoopFS(),
            indexFile: vault.appendingPathComponent(".scout-cache/sessions-index.json"),
            watchRoots: [],
            intervals: Self.quick
        ))
        await service.refreshFast()
        #expect(service.availability == .engineMissing)
        #expect(service.lastError?.contains("scoutctl not found") == true, "got: \(service.lastError ?? "nil")")
    }

    @Test func anUnsupportedSchemaKeepsTheLastIndex() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        await runner.setFast(.ok(try fixtureVariant { $0["schema_version"] = 2 }))
        await h.service.refreshFast()
        #expect(h.service.availability == .unsupportedSchema(2))
        #expect(h.service.index?.sessions.count == 11)
    }

    @Test func outputThatIsNotJSONIsReportedWithASnippet() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(Data("Traceback (most recent call last):".utf8)))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        await h.service.refreshFast()
        #expect(h.service.index == nil)
        #expect(h.service.lastError?.contains("Traceback") == true)
    }

    // MARK: Watching

    @Test func fileEventsRefreshButTheEnginesOwnWritesDoNot() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        h.service.setVisible(true)
        #expect(await eventually { await runner.settledAfterAppearing })
        await h.service.refreshFast()
        let settled = await runner.fastCalls

        let cache = h.vault.appendingPathComponent(".scout-cache", isDirectory: true)
        for name in ["sessions-index.json", ".sessions-index.json.k3j9x2qa.tmp", "sessions-pr.cache.json"] {
            h.events.emit(FileSystemEvent(url: cache.appendingPathComponent(name), kind: .modified))
        }
        try await Task.sleep(for: .milliseconds(300))
        #expect(await runner.fastCalls == settled)

        h.events.emit(FileSystemEvent(url: h.roots[2].appendingPathComponent("-Users-alex-code-example-repo/x.jsonl"),
                                      kind: .modified))
        #expect(await eventually { await runner.fastCalls > settled })
    }

    @Test func hidingThePageStopsWatching() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        h.service.setVisible(true)
        #expect(await eventually { await runner.settledAfterAppearing })
        h.service.setVisible(false)
        await h.service.refreshFast()
        let settled = await runner.fastCalls
        h.events.emit(FileSystemEvent(url: h.roots[1].appendingPathComponent("123.json"), kind: .created))
        try await Task.sleep(for: .milliseconds(300))
        #expect(await runner.fastCalls == settled)
    }

    /// A page left on screen in a minimised, hidden or fully covered window is
    /// not being looked at: watching stops, and showing it again refreshes and
    /// resumes watching.
    @Test func anOccludedAppStopsWatchingUntilItIsShownAgain() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        let transcript = h.roots[2].appendingPathComponent("-Users-alex-code-example-repo/x.jsonl")
        h.service.setVisible(true)
        #expect(await eventually { await runner.settledAfterAppearing })

        h.service.setAppVisible(false)
        await h.service.refreshFast()
        let hidden = await runner.fastCalls
        h.events.emit(FileSystemEvent(url: transcript, kind: .modified))
        try await Task.sleep(for: .milliseconds(300))
        #expect(await runner.fastCalls == hidden)

        h.service.setAppVisible(true)
        #expect(await eventually { await runner.fastCalls > hidden })
        await h.service.refreshFast()
        let shown = await runner.fastCalls
        h.events.emit(FileSystemEvent(url: transcript, kind: .modified))
        #expect(await eventually { await runner.fastCalls > shown })
    }

    @Test func appVisibilityAloneStartsNothing() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        h.service.setAppVisible(false)
        h.service.setAppVisible(true)
        try await Task.sleep(for: .milliseconds(200))
        #expect(await runner.calls.isEmpty)
    }

    @Test func theHeartbeatRebuildsWithoutAnyFileEvent() async throws {
        var intervals = Self.quick
        intervals.visibleHeartbeat = .milliseconds(30)
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner, intervals: intervals)
        defer { h.tearDown() }
        h.service.start()
        h.service.setVisible(true)
        #expect(await eventually { await runner.fastCalls >= 5 })
    }

    @Test func startIsIdempotent() async throws {
        let runner = ScriptedSessionsRunner(fast: .ok(try SessionsFixture.data()))
        let h = try harness(runner: runner)
        defer { h.tearDown() }
        h.service.start()
        h.service.start()
        #expect(await eventually { await runner.prCalls == 1 })
        try await Task.sleep(for: .milliseconds(100))
        #expect(await runner.prCalls == 1)
    }

    // MARK: Messages

    @Test func exitClassification() {
        #expect(SessionIndexService.classifyExit(.ok(Data())) == nil)
        #expect(SessionIndexService.classifyExit(.failed(2, stderr: "Error: No such command 'index'.")) == .engineTooOld)
        #expect(SessionIndexService.classifyExit(.failed(2, stderr: "Error: No such option: --json")) == .failed)
        #expect(SessionIndexService.classifyExit(.failed(127, stderr: "")) == .engineMissing)
        #expect(SessionIndexService.classifyExit(.failed(1, stderr: "")) == .failed)
    }
}

extension ProcessResult {
    static func ok(_ data: Data) -> ProcessResult { ProcessResult(exitCode: 0, stdout: data, stderr: Data()) }
    static func failed(_ code: Int32, stderr: String) -> ProcessResult {
        ProcessResult(exitCode: code, stdout: Data(), stderr: Data(stderr.utf8))
    }
}

struct FixedSessionsClock: ClockSource {
    static let instant = SessionsFixture.now
    func now() -> Date { Self.instant }
}
