import Foundation
import Testing
@testable import Scout

/// Card C3: the menu-bar icon, the panel and the status bar share one status,
/// and it never says "Ready" when the engine is missing or can't be read.
@Suite("ScoutStatus")
struct ScoutStatusTests {
    let install = EngineInstall(root: URL(fileURLWithPath: "/tmp/engine"), scoutctl: URL(fileURLWithPath: "/tmp/engine/scoutctl"),
                                python: nil, version: "0.20.0", vault: nil)
    let green = DoctorReport(severity: .green, errors: [], warnings: [])
    let now = Date(timeIntervalSince1970: 1_800_000_000)

    private func derive(_ engine: EngineState, doctor: DoctorReport? = nil, error: String? = nil,
                        run: Run? = nil) -> ScoutStatus {
        ScoutStatus.derive(engine: engine, doctor: doctor, doctorError: error, latestRun: run, now: now)
    }

    private func run(_ status: RunStatus) -> Run {
        Run.make(type: .morningBriefing, startedAt: now.addingTimeInterval(-600), endedAt: now.addingTimeInterval(-60), status: status)
    }

    // MARK: - The engine comes first

    @Test("no engine: set up, never Ready, even after a successful run")
    func noEngine() {
        let s = derive(.notInstalled, run: run(.success))
        #expect(s.kind == .needsSetup)
        #expect(s.fix == .setUp)
        #expect(s.title != "Ready")
    }

    @Test("an installed engine without a vault: finish setting up")
    func vaultNotSetUp() {
        let s = derive(.managed(install, vaultBootstrapped: false), doctor: green)
        #expect(s.kind == .needsSetup)
        #expect(s.title == "Finish setting up Scout")
    }

    @Test("a broken engine needs attention; the fix depends on who manages it")
    func brokenEngine() {
        let ours = derive(.broken(install, reason: "engine pointer names a missing scoutctl: /x"), run: run(.success))
        #expect(ours.kind == .needsAttention)
        #expect(ours.fix == .setUp)
        let theirs = EngineInstall(root: install.root, scoutctl: install.scoutctl, python: nil, version: "0.20.0",
                                   vault: nil, managedBy: "claude-code")
        #expect(derive(.broken(theirs, reason: "gone")).fix == .openSettings)
    }

    @Test("an unreadable engine needs attention, never Ready")
    func unreadableDoctor() {
        let s = derive(.external(install, .devCheckout), error: "could not run scoutctl: ENOENT", run: run(.success))
        #expect(s.kind == .needsAttention)
        #expect(s.title == "Can't read Scout's engine")
        #expect(s.fix == .openSettings)
    }

    @Test("before the first health check answers: checking, not Ready")
    func checking() {
        let s = derive(.external(install, .devCheckout), run: run(.success))
        #expect(s.kind == .checking)
        #expect(s.tone == .neutral)
    }

    @Test("a red doctor needs attention, counting its problems")
    func redDoctor() {
        let s = derive(.managed(install, vaultBootstrapped: true),
                       doctor: DoctorReport(severity: .red, errors: ["a", "b"], warnings: []), run: run(.success))
        #expect(s.kind == .needsAttention)
        #expect(s.detail == "2 problems with the engine")
    }

    @Test("red run health shows its first finding's summary")
    func redRunHealth() {
        let doctor = DoctorReport(severity: .green, errors: [], warnings: [], runHealth: runHealth(.red, [
            finding("note.x", "note", "A note"), finding("slot.fired_silent", "red", "Morning briefing ran but did nothing"),
        ]))
        let s = derive(.managed(install, vaultBootstrapped: true), doctor: doctor, run: run(.success))
        #expect(s.kind == .needsAttention)
        #expect(s.detail == "Morning briefing ran but did nothing")
    }

    @Test("a usage-limit pause reads as paused")
    func paused() {
        let doctor = DoctorReport(severity: .green, errors: [], warnings: [], runHealth: runHealth(.yellow, [
            finding("runs.paused", "yellow", "Paused", until: "2027-01-15T08:00:00Z"),
        ]))
        let s = derive(.external(install, .devCheckout), doctor: doctor)
        #expect(s.kind == .paused)
        #expect(s.detail.hasPrefix("Claude usage limit · resumes"))
    }

    @Test("warnings keep Ready but drop the green")
    func readyWithWarnings() {
        let s = derive(.external(install, .devCheckout),
                       doctor: DoctorReport(severity: .yellow, errors: [], warnings: ["w"]), run: run(.success))
        #expect(s.kind == .ready)
        #expect(s.tone == .warn)
        #expect(s.title == "Ready · 1 warning")
        #expect(s.detail == "w")   // the reason, not a bare count
        #expect(s.fix == .openSettings)
    }

    @Test("a run that failed today makes Ready amber and names it, after a later run of another schedule finished")
    func failedTodayNamesTheReason() {
        let doctor = DoctorReport(severity: .yellow, errors: [], warnings: ["w1", "w2"], runHealth: runHealth(.yellow, [
            finding("runs.failed_today", "yellow", "morning-briefing (at 08:00, unknown) failed today and hasn't finished a run since."),
        ]))
        let s = derive(.external(install, .devCheckout), doctor: doctor, run: run(.success))
        #expect(s.tone == .warn)
        // The same count Settings and the Health page show; the run's failure first.
        #expect(s.title == "Ready · 3 warnings")
        #expect(s.detail.hasPrefix("morning-briefing (at 08:00, unknown) failed today"))
    }

    @Test("34 grouped warnings count once")
    func groupedWarningsCountOnce() {
        let summary = "privacy: 34 vault scripts read the knowledge base directly"
        let doctor = DoctorReport(severity: .yellow, errors: [], warnings: [summary],
                                  warningGroups: [.init(kind: "privacy.script-raw-kb-read", summary: summary,
                                                        items: (1...34).map { "scripts/s\($0).py" })])
        let s = derive(.external(install, .devCheckout), doctor: doctor, run: run(.success))
        #expect(s.title == "Ready · 1 warning")
        #expect(s.detail == "34 scripts in your Scout folder read the knowledge base directly, without Scout's privacy labels")
    }

    // MARK: - Then the latest run

    @Test("a healthy engine lets the latest run speak")
    func latestRun() {
        let engine = EngineState.external(install, .devCheckout)
        #expect(derive(engine, doctor: green).kind == .ready)
        #expect(derive(engine, doctor: green, run: run(.success)).tone == .ok)
        #expect(derive(engine, doctor: green, run: run(.running)).kind == .running)
        for status in [RunStatus.failure, .timeout, .rateLimited, .orphaned] {
            let s = derive(engine, doctor: green, run: run(status))
            #expect(s.kind == .lastFailed, "\(status)")
            #expect(!s.detail.contains(status.rawValue), "raw status leaked for \(status)")
        }
    }

    @Test("skips say why; safety skips are errors with a fix, routine ones aren't")
    func skips() {
        let engine = EngineState.external(install, .devCheckout)
        for status in RunStatus.allCases where status.isSkip {
            let s = derive(engine, doctor: green, run: run(status))
            let kind: ScoutStatus.Kind = status.isSafetySkip ? .heldForSafety
                : status == .skippedRuntime ? .agentNotAllowed : .skipped
            #expect(s.kind == kind, "\(status)")
            #expect(s.detail.contains(status.skipReason!), "\(status)")
        }
        #expect(derive(engine, doctor: green, run: run(.skippedSandbox)).tone == .err)
        #expect(derive(engine, doctor: green, run: run(.skippedSandbox)).fix == .openSettings)
        #expect(derive(engine, doctor: green, run: run(.skippedBudget)).tone == .warn)
        #expect(derive(engine, doctor: green, run: run(.skippedBudget)).fix == nil)
    }

    @Test("a safety skip has its own words, not a routine skip's (R2)")
    func safetySkipsAreNotRoutine() {
        let engine = EngineState.external(install, .devCheckout)
        #expect(RunStatus.allCases.filter(\.isSafetySkip) == [.skippedGates, .skippedSandbox])
        for status in [RunStatus.skippedGates, .skippedSandbox] {
            let s = derive(engine, doctor: green, run: run(status))
            #expect(s.kind == .heldForSafety, "\(status)")
            #expect(s.title == "Run held back for safety", "\(status)")
            #expect(s.tone == .err, "\(status)")
        }
        #expect(derive(engine, doctor: green, run: run(.skippedBudget)).title == "Last run skipped")
        // An agent that isn't allowed isn't routine either: its own kind, an error with a fix.
        let runtime = derive(engine, doctor: green, run: run(.skippedRuntime))
        #expect(runtime.kind == .agentNotAllowed)
        #expect(runtime.tone == .err)
        #expect(runtime.fix == .openSettings)
    }

    // MARK: - AppState keeps it current

    @Test("AppState derives the status from the engine at once")
    @MainActor
    func appStateDerivesFromEngine() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        vault.state.recomputeStatus()
        let expected = ScoutStatus.derive(engine: vault.state.engineHealth.state, doctor: vault.state.engineHealth.doctor,
                                          doctorError: vault.state.engineHealth.lastError,
                                          latestRun: vault.state.sessionLogService.runs.first)
        #expect(vault.state.status.kind == expected.kind)
        #expect(vault.state.status.title == expected.title)
    }

    // MARK: - Helpers

    private func finding(_ id: String, _ severity: String, _ summary: String, until: String? = nil) -> RunHealth.Finding {
        RunHealth.Finding(id: id, severity: severity, since: nil, summary: summary, fix: "", source: "test", until: until)
    }

    private func runHealth(_ severity: DoctorReport.Severity, _ findings: [RunHealth.Finding]) -> RunHealth {
        RunHealth(severity: severity, evaluatedAt: "2027-01-15T07:00:00Z", lastOkAt: nil, findings: findings, slots: [])
    }
}
