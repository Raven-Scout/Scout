import Foundation
import Testing
@testable import Scout

/// Covers the derived surface of `Run` the Control Center rows render:
/// the manual-run display fallback, the manual badge predicate, and the
/// status-only copy the stale-running reconciler uses.
@Suite("Run derived values")
struct RunDisplayTests {

    // MARK: - displayName

    @Test("a typed run uses its own display name")
    func displayName_typedRunUsesTypeName() {
        #expect(Run.make(type: .morningBriefing).displayName == "Morning briefing")
        #expect(Run.make(type: .weekendBriefing).displayName == "Weekend briefing")
        #expect(Run.make(type: .consolidation).displayName == "Consolidation")
        #expect(Run.make(type: .dreaming).displayName == "Dreaming")
        #expect(Run.make(type: .research).displayName == "Research")
    }

    @Test("a manual run falls back to the runner script's family")
    func displayName_manualRunFallsBackToRunnerScript() {
        func manual(script: String) -> Run {
            let base = Run.make(type: .manual)
            return Run(
                id: base.id, type: .manual, runnerScript: script, source: .manual,
                scheduledAt: nil, startedAt: base.startedAt, endedAt: nil,
                status: .success, exitCode: 0, cost: nil, budgetCap: nil,
                logPath: base.logPath, logSizeBytes: 0,
                errorsDetected: [], commits: [], retryOf: nil)
        }
        #expect(manual(script: "run-dreaming.sh").displayName == "Dreaming (manual)")
        #expect(manual(script: "run-research.sh").displayName == "Research (manual)")
        #expect(manual(script: "run-scout.sh").displayName == "Briefing (manual)")
        #expect(manual(script: "anything-else.sh").displayName == "Briefing (manual)")
        #expect(manual(script: "").displayName == "Briefing (manual)")
    }

    // MARK: - wasManuallyTriggered

    @Test("manual source, retry source, or manual type all earn the badge")
    func wasManuallyTriggered_trueCases() {
        #expect(Run.make(type: .morningBriefing, source: .manual).wasManuallyTriggered)
        #expect(Run.make(type: .morningBriefing, source: .retry).wasManuallyTriggered)
        #expect(Run.make(type: .manual, source: .launchdScheduled).wasManuallyTriggered)
    }

    @Test("a scheduled or heartbeat run is not badged")
    func wasManuallyTriggered_falseCases() {
        #expect(!Run.make(type: .morningBriefing, source: .launchdScheduled).wasManuallyTriggered)
        #expect(!Run.make(type: .dreaming, source: .heartbeat).wasManuallyTriggered)
    }

    // MARK: - with(status:)

    @Test("with(status:) changes only the status")
    func withStatus_preservesEveryOtherField() {
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let original = Run.make(
            type: .dreaming, source: .heartbeat, startedAt: started,
            endedAt: started.addingTimeInterval(60), status: .running,
            exitCode: nil, cost: 1.25,
            logPath: URL(fileURLWithPath: "/tmp/x.log"))

        let demoted = original.with(status: .orphaned)

        #expect(demoted.status == .orphaned)
        #expect(original.status == .running)          // original untouched
        #expect(demoted.id == original.id)
        #expect(demoted.type == original.type)
        #expect(demoted.runnerScript == original.runnerScript)
        #expect(demoted.source == original.source)
        #expect(demoted.scheduledAt == original.scheduledAt)
        #expect(demoted.startedAt == original.startedAt)
        #expect(demoted.endedAt == original.endedAt)
        #expect(demoted.exitCode == original.exitCode)
        #expect(demoted.cost == original.cost)
        #expect(demoted.budgetCap == original.budgetCap)
        #expect(demoted.logPath == original.logPath)
        #expect(demoted.logSizeBytes == original.logSizeBytes)
        #expect(demoted.retryOf == original.retryOf)
    }

    @Test("with(status:) is idempotent for the same status")
    func withStatus_sameStatusIsEqual() {
        let run = Run.make(status: .success)
        #expect(run.with(status: .success) == run)
    }

    // MARK: - makeId

    @Test("ids are namespaced by type so two run families never collide")
    func makeId_isTypeNamespaced() {
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let a = Run.makeId(type: .dreaming, startedAt: started)
        let b = Run.makeId(type: .research, startedAt: started)
        #expect(a != b)
        #expect(a.hasPrefix("dreaming-"))
        #expect(b.hasPrefix("research-"))
    }

    @Test("runs are hashable by identity")
    func run_isHashable() {
        let started = Date(timeIntervalSince1970: 1_781_600_000)
        let a = Run.make(type: .dreaming, startedAt: started)
        let b = Run.make(type: .dreaming, startedAt: started)
        let c = Run.make(type: .research, startedAt: started)
        #expect(Set([a, b, c]).count == 2)
    }

    // MARK: - enum surfaces

    @Test("run source raw values are the persisted contract")
    func runSource_rawValues() {
        #expect(RunSource.launchdScheduled.rawValue == "launchdScheduled")
        #expect(RunSource.heartbeat.rawValue == "heartbeat")
        #expect(RunSource.manual.rawValue == "manual")
        #expect(RunSource.retry.rawValue == "retry")
        #expect(RunSource(rawValue: "manual") == .manual)
        #expect(RunSource(rawValue: "nope") == nil)
    }

    @Test("run status raw values are the persisted contract")
    func runStatus_rawValues() {
        let expected = ["scheduled", "running", "success", "failure", "timeout",
                        "orphaned", "skippedBudget", "skippedConcurrency", "rateLimited"]
        for raw in expected {
            #expect(RunStatus(rawValue: raw) != nil, "missing status \(raw)")
        }
        #expect(RunStatus(rawValue: "invented") == nil)
    }

    @Test("run source and status round-trip through Codable")
    func runEnums_codableRoundTrip() throws {
        let encoder = JSONEncoder(), decoder = JSONDecoder()
        for status in [RunStatus.scheduled, .running, .success, .failure, .timeout,
                       .orphaned, .skippedBudget, .skippedConcurrency, .rateLimited] {
            let data = try encoder.encode(status)
            #expect(try decoder.decode(RunStatus.self, from: data) == status)
        }
        for source in [RunSource.launchdScheduled, .heartbeat, .manual, .retry] {
            let data = try encoder.encode(source)
            #expect(try decoder.decode(RunSource.self, from: data) == source)
        }
        for type in RunType.allCases {
            let data = try encoder.encode(type)
            #expect(try decoder.decode(RunType.self, from: data) == type)
        }
    }
}
