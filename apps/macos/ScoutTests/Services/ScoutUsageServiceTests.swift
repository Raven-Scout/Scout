import Testing
import Foundation
@testable import Scout

/// `Fixtures/usage-summary.json` is a byte copy of the engine's generated
/// `plugin/engine/tests/fixtures/contract/usage-summary.json`
/// (`tests/unit/test_usage_summary.py`; `contract.yml` compares the copies).
/// The engine builds it from a vault where each message's usage repeats over
/// several streamed rows, an interactive vault session and another project's
/// session sit next to the runs' transcripts, and one run's $0 isn't a
/// measurement. The pins below are what the card must show for that vault.
private func fixtureData() throws -> Data {
    let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: "usage-summary", withExtension: "json"))
    return try Data(contentsOf: url)
}

/// Stub runner returning one canned result per call (the last repeats).
private actor StubUsageRunner: ProcessRunner {
    private var results: [ProcessResult]
    private(set) var arguments: [[String]] = []

    init(_ results: [ProcessResult]) { self.results = results }

    func recorded() -> [[String]] { arguments }

    private func next(_ args: [String]) -> ProcessResult {
        arguments.append(args)
        return results.count > 1 ? results.removeFirst() : results[0]
    }

    nonisolated func run(
        executable: URL, arguments: [String], environment: [String: String], workingDirectory: URL?
    ) async throws -> ProcessResult {
        await next(arguments)
    }
}

private func ok(_ data: Data) -> ProcessResult { ProcessResult(exitCode: 0, stdout: data, stderr: Data()) }

@Suite("Scout usage summary")
struct ScoutUsageSummaryTests {
    @Test func theContractFixtureDecodesToTheEnginesFigures() throws {
        let summary = try ScoutUsageService.decode(try fixtureData())
        #expect(summary.schemaVersion == 1)
        #expect(summary.scope == "scheduled-runs")
        #expect(summary.timezone == "America/New_York")

        let today = summary.today
        // Each message once: the repeated content-block rows would triple these,
        // and the interactive and other-project sessions would add millions.
        #expect(today.tokens == UsageTokens(input: 133, output: 89, cacheRead: 25_100, cacheCreation: 2_500))
        #expect(today.tokens.inputOutput == 222)
        #expect((today.runs, today.runsWithTokens, today.sessions, today.pricedRuns) == (2, 2, 3, 2))
        #expect(today.costUSD == 3.35)
        #expect(today.costKind == .priced)

        let week = summary.week
        #expect((week.runs, week.runsWithTokens, week.pricedRuns) == (4, 3, 3))
        #expect(week.costUSD == 4.85)
        #expect(week.costKind == .partial)
    }

    @Test func theCardLabelsSayWhatIsCounted() throws {
        let summary = try ScoutUsageService.decode(try fixtureData())
        #expect(UsageFormat.cost(summary.today) == "≈$3.35 (some runs estimated from tokens)")
        #expect(UsageFormat.cost(summary.week) == "at least $4.85 (3 of 4 runs priced)")
        #expect(UsageFormat.coverage(summary.today) == "2 runs finished")
        #expect(UsageFormat.coverage(summary.week) == "4 runs finished, tokens from 3")
        #expect(UsageFormat.shortCost(summary.today) == "≈$3.35")
        #expect(UsageFormat.shortCost(summary.week) == "≥$4.85")
        #expect(UsageFormat.cacheLine(summary.today.tokens) == "in 133 · out 89 · cache read 25K · cache write 2.5K")
    }

    @Test func anUnpricedOrEmptyDayNeverShowsAMeasuredZero() {
        let zero = UsageTokens(input: 0, output: 0, cacheRead: 0, cacheCreation: 0)
        let unpriced = UsagePeriod(start: "", end: "", runs: 3, runsWithTokens: 0, sessions: 0, tokens: zero,
                                   costUSD: 0, costKind: .unpriced, pricedRuns: 0)
        #expect(UsageFormat.cost(unpriced) == "cost unknown (no run reported one)")
        #expect(UsageFormat.shortCost(unpriced) == "unknown")
        let none = UsagePeriod(start: "", end: "", runs: 0, runsWithTokens: 0, sessions: 0, tokens: zero,
                               costUSD: 0, costKind: .none, pricedRuns: 0)
        #expect(UsageFormat.cost(none) == "no runs yet")
        #expect(UsageFormat.coverage(none) == "no scheduled runs finished yet")
        let reported = UsagePeriod(start: "", end: "", runs: 1, runsWithTokens: 1, sessions: 1, tokens: zero,
                                   costUSD: 0, costKind: .reported, pricedRuns: 1)
        #expect(UsageFormat.cost(reported) == "$0.00 (Claude Code's figure)")
        #expect(UsageFormat.coverage(reported) == "1 run finished")
    }

    @Test func tokenUnitsNeverHideTheScale() {
        #expect(UsageFormat.tokens(950) == "950")
        #expect(UsageFormat.tokens(2_500) == "2.5K")
        #expect(UsageFormat.tokens(12_400) == "12K")
        #expect(UsageFormat.tokens(3_400_000) == "3.4M")
        #expect(UsageFormat.tokens(64_187_831) == "64.2M")
        #expect(UsageFormat.tokens(270_000_000_000) == "270.0B")
        #expect(UsageFormat.tokens(4_000_000_000_000) == "4.0T")
    }

    @Test func anUnknownCostKindReadsAsPartial() throws {
        var json = String(decoding: try fixtureData(), as: UTF8.self)
        json = json.replacingOccurrences(of: "\"cost_kind\": \"priced\"", with: "\"cost_kind\": \"guessed\"")
        let summary = try ScoutUsageService.decode(Data(json.utf8))
        #expect(summary.today.costKind == .partial)
    }

    @Test func todayEndsAtTheVaultsMidnight() throws {
        let summary = try ScoutUsageService.decode(try fixtureData())
        let end = try #require(summary.today.endDate)
        // 2026-04-30T00:00:00-04:00
        #expect(end == Date(timeIntervalSince1970: 1_777_521_600))
        let threePM = end.addingTimeInterval(-9 * 3600)
        let delay = try #require(ScoutUsageService.rolloverDelay(until: end, now: threePM))
        #expect(abs(delay - (9 * 3600 + 1)) < 0.01)
        // A day already over by this clock schedules nothing, so it can't loop.
        #expect(ScoutUsageService.rolloverDelay(until: end, now: end) == nil)
        #expect(ScoutUsageService.rolloverDelay(until: end, now: end.addingTimeInterval(60)) == nil)
    }

    @Test func aNewerSchemaIsRefused() throws {
        var json = String(decoding: try fixtureData(), as: UTF8.self)
        json = json.replacingOccurrences(of: "\"schema_version\": 1", with: "\"schema_version\": 2")
        #expect(throws: (any Error).self) { try ScoutUsageService.decode(Data(json.utf8)) }
    }
}

@Suite("Scout usage service")
@MainActor
struct ScoutUsageServiceTests {
    @Test func refreshAsksTheEngineAndPublishesTheSummary() async throws {
        let runner = StubUsageRunner([ok(try fixtureData())])
        let svc = ScoutUsageService(
            scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"), runner: runner, argumentsPrefix: ["--x"]
        )
        await svc.refresh()
        #expect(svc.summary?.today.runs == 2)
        #expect(svc.lastError == nil)
        #expect(await runner.recorded() == [["--x", "budget", "usage", "--json"]])
    }

    @Test func aFailedRefreshKeepsTheLastSummaryAndSaysWhy() async throws {
        let runner = StubUsageRunner([
            ok(try fixtureData()),
            ProcessResult(exitCode: 2, stdout: Data(), stderr: Data("No such command 'usage'.\n".utf8)),
        ])
        let svc = ScoutUsageService(scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"), runner: runner)
        await svc.refresh()
        await svc.refresh()
        #expect(svc.summary?.today.runs == 2)
        #expect(svc.lastError == "it needs a newer Scout engine (this one has no scoutctl budget usage).")
    }

    @Test func anEngineErrorIsShownAsTheEngineWroteIt() async {
        let runner = StubUsageRunner([
            ProcessResult(exitCode: 1, stdout: Data(), stderr: Data("tracker unreadable\n".utf8)),
        ])
        let svc = ScoutUsageService(scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"), runner: runner)
        await svc.refresh()
        #expect(svc.lastError == "tracker unreadable")
        #expect(ScoutUsageService.failureMessage(exitCode: 1, stderr: "") == "Scout's engine couldn't report usage (exit 1).")
        // Exit 2 is any usage error; only "No such command" means an older engine.
        #expect(ScoutUsageService.failureMessage(exitCode: 2, stderr: "Error: Invalid value for '--json'")
            == "Error: Invalid value for '--json'")
    }

    @Test func undecodableOutputIsAnError() async {
        let runner = StubUsageRunner([ok(Data("not json".utf8))])
        let svc = ScoutUsageService(scoutctl: URL(fileURLWithPath: "/usr/local/bin/scoutctl"), runner: runner)
        await svc.refresh()
        #expect(svc.summary == nil)
        #expect(svc.lastError != nil)
    }
}
