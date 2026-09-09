import Testing
import Foundation
@testable import Scout

/// Canned `scoutctl budget show --json` output. Mirrors the payload asserted by
/// the engine's `test_budget_show_json_emits_the_payload` — if these key names
/// drift apart, the app silently shows defaults.
private let defaultsJSON = """
{
  "config_path": "/Users/alex/Scout/scout-config.yaml",
  "source": "defaults",
  "daily_usd": 50.0,
  "window_hours": 5,
  "skip_at_pct": 80.0,
  "failure_backoff_minutes": 60,
  "window_budget_usd": 10.42,
  "skip_threshold_usd": 8.34
}
"""

private let vaultJSON = """
{
  "config_path": "/Users/alex/Scout/scout-config.yaml",
  "source": "vault",
  "daily_usd": 200.0,
  "window_hours": 3,
  "skip_at_pct": 90.0,
  "failure_backoff_minutes": 30,
  "window_budget_usd": 25.0,
  "skip_threshold_usd": 22.5
}
"""

/// Stub runner returning a queued stdout per call, reusing the last entry when
/// the queue is exhausted, and recording every invocation for assertion.
private actor StubBudgetRunner: ProcessRunner {
    struct Call: Sendable {
        let arguments: [String]
    }

    private(set) var calls: [Call] = []
    private var stdouts: [String]
    private let exitCode: Int32
    private let stderr: String

    init(stdouts: [String], exitCode: Int32 = 0, stderr: String = "") {
        self.stdouts = stdouts
        self.exitCode = exitCode
        self.stderr = stderr
    }

    func recordedCalls() -> [Call] { calls }

    nonisolated func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        workingDirectory: URL?
    ) async throws -> ProcessResult {
        let out = await next(arguments: arguments)
        return ProcessResult(
            exitCode: exitCode,
            stdout: Data(out.utf8),
            stderr: Data(stderr.utf8)
        )
    }

    private func next(arguments: [String]) -> String {
        calls.append(Call(arguments: arguments))
        if stdouts.count > 1 { return stdouts.removeFirst() }
        return stdouts.first ?? ""
    }
}

@MainActor
private func makeService(
    stdouts: [String],
    exitCode: Int32 = 0,
    stderr: String = "",
    argumentsPrefix: [String] = []
) -> (BudgetSettingsService, StubBudgetRunner) {
    let runner = StubBudgetRunner(stdouts: stdouts, exitCode: exitCode, stderr: stderr)
    let service = BudgetSettingsService(
        scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"),
        runner: runner,
        argumentsPrefix: argumentsPrefix
    )
    return (service, runner)
}

@Suite("BudgetSettingsService")
struct BudgetSettingsServiceTests {

    @Test("load decodes the engine payload")
    @MainActor
    func loadDecodes() async throws {
        let (service, _) = makeService(stdouts: [vaultJSON])
        try await service.load()
        let settings = try #require(service.settings)
        #expect(settings.source == .vault)
        #expect(settings.dailyUSD == 200)
        #expect(settings.windowHours == 3)
        #expect(settings.skipAtPct == 90)
        #expect(settings.failureBackoffMinutes == 30)
        #expect(settings.windowBudgetUSD == 25.0)
        #expect(settings.skipThresholdUSD == 22.5)
        #expect(settings.configPath == "/Users/alex/Scout/scout-config.yaml")
    }

    @Test("load reads the defaults source")
    @MainActor
    func loadDefaultsSource() async throws {
        let (service, _) = makeService(stdouts: [defaultsJSON])
        try await service.load()
        #expect(try #require(service.settings).source == .defaults)
    }

    @Test("load asks scoutctl for JSON")
    @MainActor
    func loadArguments() async throws {
        let (service, runner) = makeService(stdouts: [vaultJSON])
        try await service.load()
        let calls = await runner.recordedCalls()
        #expect(calls.count == 1)
        #expect(calls[0].arguments == ["budget", "show", "--json"])
    }

    @Test("load honours the argumentsPrefix used for the env fallback")
    @MainActor
    func loadArgumentsPrefix() async throws {
        let (service, runner) = makeService(stdouts: [vaultJSON], argumentsPrefix: ["scoutctl"])
        try await service.load()
        let calls = await runner.recordedCalls()
        #expect(calls[0].arguments == ["scoutctl", "budget", "show", "--json"])
    }

    /// The engine's numbers and ours must agree. At the DEFAULT knobs the
    /// two-step and collapsed roundings differ by a cent, so this is the case
    /// that catches a collapsed mirror.
    @Test("no parity warning when our arithmetic matches the engine")
    @MainActor
    func parityClean() async throws {
        let (service, _) = makeService(stdouts: [defaultsJSON])
        try await service.load()
        #expect(service.parityWarning == nil)
    }

    @Test("parity warning when the engine reports different figures")
    @MainActor
    func parityDrift() async throws {
        let drifted = defaultsJSON.replacingOccurrences(
            of: "\"skip_threshold_usd\": 8.34",
            with: "\"skip_threshold_usd\": 8.33"
        )
        let (service, _) = makeService(stdouts: [drifted])
        try await service.load()
        #expect(service.parityWarning != nil)
        // Still publishes the engine's values — they are authoritative.
        #expect(try #require(service.settings).skipThresholdUSD == 8.33)
    }

    @Test("unrecognized source decodes to defaults rather than throwing")
    @MainActor
    func unknownSource() async throws {
        let odd = vaultJSON.replacingOccurrences(of: "\"vault\"", with: "\"something-new\"")
        let (service, _) = makeService(stdouts: [odd])
        try await service.load()
        #expect(try #require(service.settings).source == .defaults)
    }

    @Test("load surfaces a non-zero exit with the CLI's stderr")
    @MainActor
    func loadFailure() async throws {
        let (service, _) = makeService(stdouts: [""], exitCode: 1, stderr: "boom: no vault")
        await #expect(throws: (any Error).self) { try await service.load() }
        #expect(service.settings == nil)
    }

    @Test("load surfaces undecodable stdout")
    @MainActor
    func loadBadJSON() async throws {
        let (service, _) = makeService(stdouts: ["not json at all"])
        await #expect(throws: (any Error).self) { try await service.load() }
    }

    @Test("setArguments passes every knob")
    func setArguments() {
        let args = BudgetSettingsService.setArguments(
            dailyUSD: 200,
            windowHours: 3,
            skipAtPct: 90,
            failureBackoffMinutes: 30
        )
        #expect(args == [
            "budget", "set",
            "--daily-usd", "200",
            "--window-hours", "3",
            "--skip-at-pct", "90",
            "--failure-backoff-minutes", "30",
            "--json",
        ])
    }

    @Test("setArguments keeps a fractional dollar amount")
    func setArgumentsFractional() {
        let args = BudgetSettingsService.setArguments(
            dailyUSD: 12.5,
            windowHours: 3,
            skipAtPct: 90,
            failureBackoffMinutes: 30
        )
        #expect(args.contains("12.5"))
    }

    @Test("save writes then republishes from the set payload")
    @MainActor
    func saveRepublishes() async throws {
        let (service, runner) = makeService(stdouts: [defaultsJSON, vaultJSON])
        try await service.load()
        try await service.save(dailyUSD: 200, windowHours: 3, skipAtPct: 90, failureBackoffMinutes: 30)

        let settings = try #require(service.settings)
        #expect(settings.source == .vault)
        #expect(settings.dailyUSD == 200)

        let calls = await runner.recordedCalls()
        #expect(calls.count == 2)
        #expect(calls[1].arguments.contains("set"))
        #expect(calls[1].arguments.contains("--daily-usd"))
    }

    @Test("a rejected save surfaces the error and publishes nothing")
    @MainActor
    func saveFailureSurfaces() async throws {
        let (service, _) = makeService(
            stdouts: [""],
            exitCode: 1,
            stderr: "error: skip_at_pct=500 is outside the usable range [0.0, 100.0]"
        )
        await #expect(throws: (any Error).self) {
            try await service.save(dailyUSD: 1, windowHours: 1, skipAtPct: 500, failureBackoffMinutes: 1)
        }
        #expect(service.settings == nil)
    }
}
