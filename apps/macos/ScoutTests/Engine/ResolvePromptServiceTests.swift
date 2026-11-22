import Foundation
import Testing
@testable import Scout

/// "Resolve with Claude": `ResolvePromptService` runs `scoutctl health resolve-prompt` and
/// refuses anything it can't trust: a key the engine wouldn't accept, an answer for another
/// vault, an engine that failed.
@Suite("Resolve prompt service")
@MainActor
struct ResolvePromptServiceTests {
    private let vault = URL(fileURLWithPath: "/Users/alex/Scout")
    private let scoutctl = URL(fileURLWithPath: "/opt/scout/bin/scoutctl")

    private func service(_ runner: RuleBasedRunner) -> ResolvePromptService {
        ResolvePromptService(scoutctl: scoutctl, argumentsPrefix: [], runner: runner,
                             environment: ["SCOUT_DATA_DIR": vault.path], scoutDirectory: vault)
    }

    private func findingDocument(cwd: String) throws -> String {
        let data = try ResolvePromptContractTests.document("finding") { $0["cwd"] = cwd }
        return String(decoding: data, as: UTF8.self)
    }

    @Test func buildsArguments() {
        #expect(ResolvePromptService.arguments(for: .run(logName: "a.log"))
                == ["health", "resolve-prompt", "run", "a.log", "--json"])
        #expect(ResolvePromptService.arguments(for: .connector("mcp:example_tracker"))
                == ["health", "resolve-prompt", "connector", "mcp:example_tracker", "--json"])
    }

    @Test func acceptsSuffixedFindingKey() {
        #expect(ResolveSubject.finding("slot.stale:morning-briefing").isValidKey)
        #expect(ResolveSubject.finding("lane.dark:mail").isValidKey)
        #expect(ResolveSubject.connector("mcp:claude_ai_Slack").isValidKey)
        #expect(ResolveSubject.run(logName: "scout-2026-11-13_17-57-30-48213.log").isValidKey)
    }

    @Test(arguments: [
        ResolveSubject.run(logName: "../x.log"),
        .run(logName: "run/../../etc.log"),
        .run(logName: "x.txt"),
        .finding("a b"),
        .finding(""),
        .finding("Run.X"),
        .connector("mcp: x"),
        // `$` would match before a final newline; the patterns anchor at the very end.
        .run(logName: "x.log\n"),
        .finding("runs.failing\n"),
        .connector("mcp:example_tracker\n"),
    ])
    func rejectsUnsafeKeys(_ subject: ResolveSubject) async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", stdout: "{}")
        await #expect(throws: ResolvePromptError.invalidKey) {
            try await service(runner).prompt(for: subject)
        }
        #expect(runner.calls.isEmpty)
    }

    @Test func returnsParsedPrompt() async throws {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["health", "resolve-prompt"], stdout: try findingDocument(cwd: vault.path))
        let p = try await service(runner).prompt(for: .finding("run.integrity_restored"))
        #expect(p.key == "run.integrity_restored" && p.cwd == vault.path)
        #expect(runner.calls.first?.arguments == ["health", "resolve-prompt", "finding", "run.integrity_restored", "--json"])
        #expect(runner.calls.first?.environment["SCOUT_DATA_DIR"] == vault.path)
    }

    @Test func refusesMismatchedCwd() async throws {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["health", "resolve-prompt"], stdout: try findingDocument(cwd: "/elsewhere"))
        await #expect(throws: ResolvePromptError.wrongFolder) {
            try await service(runner).prompt(for: .finding("run.integrity_restored"))
        }
    }

    @Test func refusesAnAnswerForAnotherSubject() async throws {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["health", "resolve-prompt"], stdout: try findingDocument(cwd: vault.path))
        await #expect(throws: ResolvePromptError.unreadable) {
            try await service(runner).prompt(for: .finding("runs.failing"))
        }
    }

    @Test func surfacesEngineFailure() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["health", "resolve-prompt"], stderr: "boom\nmore", exit: 2)
        await #expect(throws: ResolvePromptError.engineFailed("boom")) {
            try await service(runner).prompt(for: .run(logName: "a.log"))
        }
    }

    @Test func prefersTheEnginesJSONError() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["health", "resolve-prompt"],
                  stdout: #"{"schema_version": 1, "error": "no such run log"}"#, stderr: "resolve-prompt: no such run log", exit: 2)
        await #expect(throws: ResolvePromptError.engineFailed("no such run log")) {
            try await service(runner).prompt(for: .run(logName: "a.log"))
        }
    }

    @Test func errorsSayWhatWentWrong() {
        #expect(ResolvePromptError.wrongFolder.errorDescription?.hasPrefix("Couldn't build the prompt: ") == true)
        #expect(ResolvePromptError.engineFailed("boom").errorDescription == "Couldn't build the prompt: boom")
    }

    private final class Count: @unchecked Sendable { var n = 0 }
    private final class Clock { var now = Date(timeIntervalSince1970: 1_800_000_000) }

    /// A runner whose first `manifest show` lacks the feature and later ones have it.
    private func upgradingEngine(_ count: Count) -> RuleBasedRunner {
        let runner = RuleBasedRunner()
        runner.on({ _, args in args == ["manifest", "show"] }) { _, _, _ in
            count.n += 1
            let features = count.n == 1 ? #"{"features": {"topic_board_v1": true}}"#
                                        : #"{"features": {"resolve_prompt_v1": true}}"#
            return ProcessResult(exitCode: 0, stdout: Data(features.utf8), stderr: Data())
        }
        return runner
    }

    private func service(_ runner: RuleBasedRunner, clock: Clock) -> ResolvePromptService {
        ResolvePromptService(scoutctl: scoutctl, runner: runner, environment: [:], scoutDirectory: vault,
                             now: { clock.now })
    }

    @Test func supportCachesAYesAndANoForAMinute() async {
        let count = Count(), clock = Clock()
        let s = service(upgradingEngine(count), clock: clock)
        #expect(await s.isSupported() == false)
        clock.now += 30
        #expect(await s.isSupported() == false)   // a no is remembered for a minute
        #expect(count.n == 1)
        clock.now += 31
        #expect(await s.isSupported() == true)    // then asked again: the engine was upgraded
        clock.now += 3600
        #expect(await s.isSupported() == true)    // a yes is remembered for good
        #expect(count.n == 2)
    }

    @Test func buttonsShareOneCheck() async {
        let count = Count()
        let runner = RuleBasedRunner()
        runner.on({ _, args in args == ["manifest", "show"] }) { _, _, _ in
            count.n += 1
            try await Task.sleep(for: .milliseconds(50))
            return ProcessResult(exitCode: 0, stdout: Data(#"{"features": {"resolve_prompt_v1": true}}"#.utf8),
                                 stderr: Data())
        }
        let s = service(runner, clock: Clock())
        async let a = s.isSupported()
        async let b = s.isSupported()
        async let c = s.isSupported()
        let all = await [a, b, c]
        #expect(all == [true, true, true])
        #expect(count.n == 1)
    }
}
