import Foundation
import Testing
@testable import Scout

/// `ScoutTests/Fixtures/run-ledger.json` is a byte copy of the engine's generated
/// `plugin/engine/tests/fixtures/contract/run-ledger.json` (`scoutctl runs --json`;
/// `contract.yml` compares them). Regenerate it from the engine, never by hand
/// (see the root CLAUDE.md), then update the pins below.
@Suite("Run ledger contract")
struct RunLedgerContractTests {
    private final class FixtureAnchor {}

    static func fixtureData() throws -> Data {
        let url = try #require(Bundle(for: FixtureAnchor.self).url(forResource: "run-ledger", withExtension: "json"))
        return try Data(contentsOf: url)
    }

    static func fixture() throws -> RunLedger {
        switch RunLedger.parse(try fixtureData()) {
        case .success(let ledger): return ledger
        case .failure(let error): Issue.record("fixture didn't parse: \(error)"); throw error
        }
    }

    @Test func decodesTheSharedFixture() throws {
        let ledger = try Self.fixture()
        #expect(ledger.schemaVersion == 1)
        #expect(ledger.truncated == false)
        #expect(ledger.runs.count == 13)
        #expect(ledger.generatedAt == RunLedgerTime.parse("2026-11-16T12:00:00Z"))
        #expect(ledger.since == RunLedgerTime.parse("2026-11-09T12:00:00Z"))
        // Newest first.
        #expect(ledger.runs.map(\.finishedAt) == ledger.runs.map(\.finishedAt).sorted(by: >))
    }

    @Test func everyRowFieldDecodes() throws {
        let ledger = try Self.fixture()
        let timeout = try #require(ledger.runs.first { $0.failureClass == "timeout" })
        #expect(timeout.slot == "weekly-research")
        #expect(timeout.runner == "research")
        #expect(timeout.exitCode == 124)
        #expect(timeout.awakeSeconds == 5400)
        #expect(timeout.durationSeconds == 5400)
        #expect(timeout.agentRuntime == "claude-code")
        #expect(timeout.model == "opus")
        #expect(timeout.sandbox == "enforced")
        #expect(timeout.integrity == "clean")
        #expect(timeout.trigger == "schedule")
        #expect(timeout.log == "research-2026-11-13_12-00.log")

        let limited = try #require(ledger.runs.first { $0.failureClass == "rate_limited" })
        #expect(limited.resetsAt == RunLedgerTime.parse("2026-11-16T15:00:00Z"))

        let restored = try #require(ledger.runs.first { $0.integrity == "restored" })
        #expect(restored.newestCommit == "3f9c2a1")

        let off = try #require(ledger.runs.first { $0.sandbox == "off" })
        #expect(off.sandboxSource == "switch")
    }

    @Test func everyOutcomeMapsToItsOwnStatus() throws {
        let statuses = try Self.fixture().runs.map { "\($0.outcome)/\($0.failureClass ?? "-")=\($0.runStatus.rawValue)" }
        #expect(Set(statuses) == [
            "ok/ok=success",
            "failed/timeout=timeout",
            "failed/rate_limited=rateLimited",
            "skipped_lock/ok=skippedConcurrency",
            "skipped_budget/ok=skippedBudget",
            "skipped_redundant/ok=skippedRedundant",
            "skipped_lease/lease_held=skippedLease",
            "skipped_runtime/ok=skippedRuntime",
            "skipped_sandbox/ok=skippedSandbox",
            "skipped_gates/gates_unsafe_endpoint=skippedGates",
        ])
    }

    /// Every value the engine emits gets plain wording: never the raw key.
    @Test func everyValueHasPlainWording() throws {
        for run in try Self.fixture().runs {
            let words = [
                RunOutcomeDisplay.outcome(run.outcome, failureClass: run.failureClass),
                RunOutcomeDisplay.reason(outcome: run.outcome, failureClass: run.failureClass),
                RunOutcomeDisplay.sandbox(run.sandbox, source: run.sandboxSource),
                RunOutcomeDisplay.integrity(run.integrity),
            ].compactMap { $0 }
            for text in words {
                #expect(!text.contains("_"), "\(text)")
                #expect(!text.lowercased().contains("sandbox"), "\(text)")
            }
            #expect(RunOutcomeDisplay.status(run.runStatus) != run.runStatus.rawValue)
        }
    }

    @Test func carriesTheHealthReport() throws {
        let health = try #require(try Self.fixture().health)
        #expect(health.severity == .red)
        #expect(health.findings.map(\.id) == ["gates.probe_unsafe", "run.integrity_restored", "lease.held"])
        #expect(health.findings.map(\.severity) == ["red", "red", "note"])
        #expect(health.findings.allSatisfy { !$0.fix.isEmpty })
        // Vault-relative, never an absolute path (C8 review).
        #expect(health.findings.compactMap(\.log) == [".scout-logs/scout-2026-11-16_08-30.log",
                                                       ".scout-logs/scout-2026-11-14_08-56.log"])
    }

    @Test func findingLogsResolveInsideTheVault() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent("finding-log-\(UUID().uuidString)")
        let logs = vault.appendingPathComponent(".scout-logs")
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: vault) }
        let file = logs.appendingPathComponent("scout-1.log")
        try "x\n".write(to: file, atomically: true, encoding: .utf8)

        #expect(FindingRow.logURL(".scout-logs/scout-1.log", vault: vault)?.standardizedFileURL == file.standardizedFileURL)
        #expect(FindingRow.logURL(file.path, vault: nil) != nil)               // an older engine's absolute path
        #expect(FindingRow.logURL(".scout-logs/missing.log", vault: vault) == nil)
        #expect(FindingRow.logURL("../outside.log", vault: vault) == nil)
        #expect(FindingRow.logURL(".scout-logs/../../outside.log", vault: vault) == nil)
        let dotted = logs.appendingPathComponent("scout..1.log")
        try "x\n".write(to: dotted, atomically: true, encoding: .utf8)
        #expect(FindingRow.logURL(".scout-logs/scout..1.log", vault: vault) != nil)   // ".." inside a name is fine
        #expect(FindingRow.logURL(".scout-logs/scout-1.log", vault: nil) == nil)
        #expect(FindingRow.logURL(nil, vault: vault) == nil)
    }

    @Test func indexesRowsByLogName() throws {
        let ledger = try Self.fixture()
        #expect(ledger.entriesByLog.count == 13)
        #expect(ledger.entriesByLog["scout-2026-11-16_08-30.log"]?.outcome == "skipped_gates")
    }
}

@Suite("Run ledger parsing")
struct RunLedgerParseTests {
    private func row(_ outcome: String, failure: String = "ok", log: String = "scout-1.log") -> String {
        #"{"slot":"morning-briefing","mode":"scout","started_at":"2026-11-16T08:00:00Z","finished_at":"2026-11-16T08:05:00Z","exit_code":0,"outcome":"\#(outcome)","failure_class":"\#(failure)","log":"\#(log)"}"#
    }

    private func parse(_ json: String) -> Result<RunLedger, RunLedger.ParseError> {
        RunLedger.parse(Data(json.utf8))
    }

    @Test func refusesANewerSchema() {
        #expect(parse(#"{"schema_version": 2, "runs": []}"#) == .failure(.unsupportedSchema(2)))
    }

    @Test func passesTheEnginesError() {
        #expect(parse(#"{"schema_version": 1, "error": "vault directory missing: /x"}"#)
                == .failure(.engine("vault directory missing: /x")))
    }

    @Test func garbageIsNotALedger() {
        #expect(parse("Usage: scoutctl [OPTIONS] COMMAND") == .failure(.notALedger))
        #expect(parse(#"{"severity": "green"}"#) == .failure(.notALedger))
    }

    @Test func oneBadRowCostsOnlyThatRow() throws {
        let json = #"{"schema_version":1,"runs":[\#(row("ok")),{"slot":"x"},\#(row("skipped_gates", log: "scout-2.log"))]}"#
        let ledger = try parse(json).get()
        #expect(ledger.runs.map(\.outcome) == ["ok", "skipped_gates"])
        #expect(ledger.health == nil)
    }

    @Test func anUnreadableHealthBlockKeepsTheRuns() throws {
        let json = #"{"schema_version":1,"runs":[\#(row("ok"))],"health":{"severity":"purple"}}"#
        let ledger = try parse(json).get()
        #expect(ledger.runs.count == 1 && ledger.health == nil)
    }

    @Test func anUnknownOutcomeReadsAsAFailure() throws {
        let ledger = try parse(#"{"schema_version":1,"runs":[\#(row("exploded", failure: "meteor"))]}"#).get()
        let run = try #require(ledger.runs.first)
        #expect(run.runStatus == .failure)
        #expect(RunOutcomeDisplay.outcome(run.outcome, failureClass: run.failureClass) == "Failed")
        #expect(RunOutcomeDisplay.reason(outcome: run.outcome, failureClass: run.failureClass)
                == "The run stopped with an error.")
        #expect(run.needsAttention)
    }

    @Test func routineSkipsDontNeedAttention() throws {
        let ledger = try parse(#"{"schema_version":1,"runs":[\#(row("skipped_lock")),\#(row("skipped_sandbox", log: "b.log"))]}"#).get()
        #expect(ledger.runs.map(\.needsAttention) == [false, true])
    }

    @Test func theNewestRowWinsADuplicateLog() throws {
        let newer = row("skipped_gates").replacingOccurrences(of: "08:05", with: "09:05")
        let ledger = try parse(#"{"schema_version":1,"runs":[\#(newer),\#(row("ok"))]}"#).get()
        #expect(ledger.entriesByLog["scout-1.log"]?.outcome == "skipped_gates")
    }
}
