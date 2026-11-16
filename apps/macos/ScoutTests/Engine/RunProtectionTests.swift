import Foundation
import Testing
@testable import Scout

/// Settings ▸ Engine ▸ Run protection (card C9): the `scoutctl protection status --json`
/// contract, its plain-language model, the service's commands, and the owner-only switch.
///
/// `protection-status.json` is a byte copy of the engine's generated
/// `plugin/engine/tests/fixtures/contract/protection-status.json` (root CLAUDE.md). When it is
/// regenerated, update the pins in `decodesTheSharedFixture`.
@Suite("Run protection")
@MainActor
struct RunProtectionTests {
    private final class FixtureAnchor {}

    static func sharedFixture() throws -> Data {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: "protection-status", withExtension: "json"))
        return try Data(contentsOf: url)
    }

    static func fixtureStatus() throws -> RunProtectionStatus {
        try #require(RunProtectionStatus.parse(try sharedFixture()))
    }

    private let vault = URL(fileURLWithPath: "/Users/alex/Scout")
    /// The fixture's evaluated_at.
    private let now = ISO8601DateFormatter().date(from: "2026-11-16T12:00:00Z")!

    // MARK: - The contract

    @Test func decodesTheSharedFixture() throws {
        let s = try Self.fixtureStatus()
        #expect(s.schemaVersion == 1)
        #expect(s.evaluatedAt == "2026-11-16T12:00:00Z")
        #expect(s.severity == "red")
        #expect(s.sandbox.status == "off")
        #expect(s.sandbox.switch == "off")
        #expect(s.sandbox.switchSource == "owner")
        #expect(s.gateProbe.status == "failed")
        #expect(s.gateProbe.checkedAt == "2026-11-16T11:00:00Z")
        #expect(s.gateProbe.claudeVersion == "2.9.0")
        #expect(s.gateProbe.stale == false)
        #expect(s.lastRun?.slot == "morning-briefing")
        #expect(s.lastRun?.sandbox == "enforced")
        #expect(s.lastRun?.integrity == "restored")
        #expect(s.lastRun?.log == ".scout-logs/scout-1794818880.log")
        #expect(s.broker.status == "unavailable")
        #expect(s.broker.recordedAt == "2026-11-16T08:48:00Z")
        #expect(s.slots.map(\.key) == ["morning-briefing", "research-weekly"])
        #expect(s.slots.map(\.runtimeSource) == ["builtin", "slot"])
        #expect(s.slots.allSatisfy { $0.runtime == "claude-code" && $0.runTimeoutS == 7200 && $0.timeoutSource == "owner" })
        #expect(s.slots.allSatisfy { $0.maxTokensPerDay == nil && $0.error == nil && $0.ignored.isEmpty })
        #expect(s.problems.map(\.id) == ["run.integrity_restored", "gates.probe_failed", "sandbox.off", "broker.unavailable"])
        #expect(s.problems.map(\.action) == ["show_log", "check_gates", "turn_on_sandbox", nil])
        #expect(s.problems.first?.log == ".scout-logs/scout-1794818880.log")
    }

    @Test func aNewerSchemaIsRefusedNotHalfRead() throws {
        var obj = try #require(try JSONSerialization.jsonObject(with: try Self.sharedFixture()) as? [String: Any])
        obj["schema_version"] = 2
        #expect(RunProtectionStatus.parse(try JSONSerialization.data(withJSONObject: obj)) == nil)
    }

    @Test func unknownStatusValuesStillDecode() throws {
        var obj = try #require(try JSONSerialization.jsonObject(with: try Self.sharedFixture()) as? [String: Any])
        obj["severity"] = "purple"
        var sandbox = try #require(obj["sandbox"] as? [String: Any])
        sandbox["status"] = "quantum"
        obj["sandbox"] = sandbox
        obj["last_run"] = NSNull()
        let s = try #require(RunProtectionStatus.parse(try JSONSerialization.data(withJSONObject: obj)))
        let m = RunProtectionModel(status: s, vault: vault, now: now)
        #expect(m.writeProtection.value == "Unknown")
        #expect(m.fileCheck.value == "No runs yet")
        #expect(m.headlineTone == .neutral)
    }

    @Test func garbageIsNil() {
        #expect(RunProtectionStatus.parse(Data("not json".utf8)) == nil)
        #expect(RunProtectionStatus.parse(Data()) == nil)
    }

    // MARK: - The plain-language model

    @Test func theFixtureReadsInPlainWords() throws {
        let m = RunProtectionModel(status: try Self.fixtureStatus(), vault: vault, now: now)
        #expect(m.headline == "Scheduled runs are paused")
        #expect(m.headlineTone == .bad)
        #expect(m.writeProtection.value == "Off")
        #expect(m.writeProtection.tone == .warn)
        #expect(m.privacyChecks.value == "Didn't load")
        #expect(m.privacyChecks.help.contains("Checked 1 hour ago"))
        #expect(m.fileCheck.value == "Files put back")
        #expect(m.fileCheck.help.hasPrefix("Morning briefing, 3 hours ago."))
        #expect(m.broker.value == "Didn't start")
        #expect(m.lines.map(\.id) == ["write", "privacy", "files", "broker"])
        // Owner-facing words never say sandbox, gate or probe.
        for line in m.lines {
            for word in ["sandbox ", "gate", "probe", "seatbelt"] {
                #expect(!line.value.lowercased().contains(word))
                #expect(!line.title.lowercased().contains(word))
            }
        }
    }

    @Test func eachSlotSaysItsAgentModelAndLimits() throws {
        let m = RunProtectionModel(status: try Self.fixtureStatus(), vault: vault, now: now)
        #expect(m.slotLines.map(\.title) == ["Morning briefing", "Research weekly"])
        #expect(m.slotLines.allSatisfy { $0.value == "Claude Code · opus" })
        #expect(m.slotLines.allSatisfy { $0.help == "Stops after 2 h awake, set by you." })
    }

    @Test func limitsFormatting() {
        func slot(_ s: Int?, _ src: String?, tokens: Int? = nil) -> RunProtectionStatus.Slot {
            RunProtectionStatus.Slot(key: "k", type: "briefing", runtime: "claude-code", runtimeSource: "builtin",
                                     model: "opus", modelSource: "builtin", runTimeoutS: s, timeoutSource: src,
                                     maxTokensPerDay: tokens, ceilingSource: tokens == nil ? "off" : "default",
                                     ignored: [], error: nil)
        }
        #expect(RunProtectionModel.timeLimit(slot(5400, "default")) == "Stops after 90 min awake")
        #expect(RunProtectionModel.timeLimit(slot(2700, "config")) == "Stops after 45 min awake, set in scout-config.yaml")
        #expect(RunProtectionModel.timeLimit(slot(nil, "owner")) == "No time limit (you turned it off)")
        #expect(RunProtectionModel.tokenLimit(slot(60, "default", tokens: 12_000_000)) == "At most 12M tokens a day")
        #expect(RunProtectionModel.tokenLimit(slot(60, "default")) == nil)
        #expect(RunProtectionModel.runtimeName("codex") == "codex")
    }

    @Test func everyProblemGetsItsFixButton() throws {
        let m = RunProtectionModel(status: try Self.fixtureStatus(), vault: vault, now: now)
        let actions = Dictionary(uniqueKeysWithValues: m.problems.map { ($0.id, $0.action) })
        #expect(actions["run.integrity_restored"] == .showLog(vault.appendingPathComponent(".scout-logs/scout-1794818880.log")))
        #expect(actions["gates.probe_failed"] == .checkPrivacyAgain)
        #expect(actions["sandbox.off"] == .turnOnProtection)
        #expect(actions["broker.unavailable"] == .some(nil))
        #expect(m.problems.allSatisfy { !$0.fix.isEmpty })
        #expect(RunProtectionModel.Action.turnOnProtection.buttonTitle == "Turn on")
        #expect(RunProtectionModel.Action.checkPrivacyAgain.buttonTitle == "Check again")
        // Without a vault, a log can't be found, so no Show log button.
        let noVault = RunProtectionModel(status: try Self.fixtureStatus(), vault: nil, now: now)
        #expect(noVault.problems.first?.action == nil)
    }

    @Test func theSwitchOffersTheOppositeOfItsState() throws {
        let off = RunProtectionModel(status: try Self.fixtureStatus(), vault: vault, now: now)
        #expect(off.offersTurnOn && !off.offersTurnOff)
        let on = RunProtectionModel(status: try Self.enforced(), vault: vault, now: now)
        #expect(on.offersTurnOff && !on.offersTurnOn)
        #expect(on.headline == "Scheduled runs are protected")
        #expect(on.writeProtection.value == "On")
    }

    @Test func theTurnOffConfirmationExplainsTheRisk() {
        let text = RunProtectionModel.turnOffMessage
        #expect(text.contains("privacy checks run at all"))
        #expect(text.contains("turn it back on"))
        #expect(RunProtectionModel.turnOffTitle == "Turn off write protection?")
    }

    /// The fixture with write protection on and nothing wrong.
    static func enforced() throws -> RunProtectionStatus {
        try #require(RunProtectionStatus.parse(try enforcedJSON()))
    }

    static func enforcedJSON() throws -> Data {
        var obj = try #require(try JSONSerialization.jsonObject(with: try sharedFixture()) as? [String: Any])
        obj["severity"] = "green"
        obj["sandbox"] = ["status": "enforced", "reason": "sandbox-exec, self-test ok", "switch": "on", "switch_source": "default"]
        obj["problems"] = [Any]()
        return try JSONSerialization.data(withJSONObject: obj)
    }

    // MARK: - The service

    private func service(_ runner: RuleBasedRunner, scoutctl: URL? = URL(fileURLWithPath: "/engine/bin/scoutctl")) -> RunProtectionService {
        RunProtectionService(runner: runner, environment: ["SCOUT_DATA_DIR": "/v"], scoutctl: { scoutctl })
    }

    private func runnerWithStatus() throws -> RuleBasedRunner {
        let runner = RuleBasedRunner()
        let json = String(decoding: try Self.sharedFixture(), as: UTF8.self)
        runner.on(tool: "scoutctl", prefix: ["protection", "status", "--json"], stdout: json)
        return runner
    }

    @Test func refreshReadsTheSummaryWithTheVault() async throws {
        let runner = try runnerWithStatus()
        let svc = service(runner)
        await svc.refresh()
        #expect(svc.status?.severity == "red")
        #expect(svc.lastError == nil)
        #expect(svc.work == nil)
        #expect(runner.calls.first?.environment["SCOUT_DATA_DIR"] == "/v")
    }

    @Test func anEngineFromBeforeTheCommandSaysToUpdate() async {
        let runner = RuleBasedRunner()
        runner.on(tool: "scoutctl", prefix: ["protection"], stderr: "Error: No such command 'protection'.", exit: 2)
        let svc = service(runner)
        await svc.refresh()
        #expect(svc.status == nil)
        #expect(svc.lastError == "This engine is too old to report run protection. Update the engine to see it.")
    }

    @Test func noEngineRunsNothing() async {
        let runner = RuleBasedRunner()
        let svc = service(runner, scoutctl: nil)
        await svc.refresh()
        #expect(runner.calls.isEmpty)
        #expect(svc.lastError == "Scout's engine isn't set up yet.")
    }

    @Test func checkAgainRunsTheCachedPrivacyCheckThenRefreshes() async throws {
        let runner = try runnerWithStatus()
        runner.on(tool: "scoutctl", prefix: ["gates", "probe"], stdout: #"{"ok": false}"#, exit: 3)
        let svc = service(runner)
        await svc.checkPrivacyChecks()
        #expect(runner.calls(to: "scoutctl") == [["gates", "probe", "--cached", "--json"], ["protection", "status", "--json"]])
        #expect(svc.lastError == nil)   // a failed check is a result, shown by the summary
        #expect(svc.status != nil)
    }

    @Test func turnOnRunsSandboxOn() async throws {
        let runner = try runnerWithStatus()
        runner.on(tool: "scoutctl", prefix: ["sandbox", "on"], stdout: "run sandbox: on")
        let svc = service(runner)
        await svc.turnSandboxOn()
        #expect(runner.calls(to: "scoutctl").first == ["sandbox", "on"])
        #expect(svc.lastError == nil)
    }

    @Test func turnOffNeedsTheOwnersConfirmationAndRunsSandboxOff() async throws {
        let runner = try runnerWithStatus()
        runner.on(tool: "scoutctl", prefix: ["sandbox", "off"], stdout: "run sandbox: off")
        let svc = service(runner)
        await svc.turnSandboxOff(OwnerConfirmation(ownerConfirmedInDialog: ()))
        #expect(runner.calls(to: "scoutctl") == [["sandbox", "off"], ["protection", "status", "--json"]])
        #expect(svc.lastError == nil)
    }

    @Test func aRefusedSwitchIsShownNotSwallowed() async throws {
        let runner = try runnerWithStatus()
        runner.on(tool: "scoutctl", prefix: ["sandbox", "off"],
                  stderr: "refused: SCOUT_MODE is set (a scheduled run). Only the owner may turn the run sandbox off; nothing was changed.",
                  exit: 2)
        let svc = service(runner)
        await svc.turnSandboxOff(OwnerConfirmation(ownerConfirmedInDialog: ()))
        #expect(svc.lastError?.hasPrefix("Couldn't turn write protection off: refused") == true)
        #expect(svc.status != nil)   // the summary still refreshes
    }

    // MARK: - Never reachable from automation

    /// The off switch must only be reachable from the confirmation dialog. The app has no URL
    /// scheme, AppleScript dictionary, Services menu entry or user activity a script could use; if
    /// one is added, it must not reach `RunProtectionService.turnSandboxOff`.
    @Test func theAppExposesNoAutomationSurface() throws {
        let info = try #require(Bundle(for: AppState.self).infoDictionary)
        #expect(info["CFBundleURLTypes"] == nil)
        #expect((info["NSAppleScriptEnabled"] as? Bool) != true)
        #expect(info["OSAScriptingDefinition"] == nil)
        #expect(info["NSServices"] == nil)
        #expect(info["NSUserActivityTypes"] == nil)
    }
}
