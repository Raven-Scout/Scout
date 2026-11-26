import AppKit
import Foundation
import SwiftUI
import Testing
@testable import Scout

@Suite("Health panel model")
struct HealthPanelModelTests {
    @Test func groupsFindingsWorstFirstAndFoldsNotes() throws {
        let doctor = DoctorReport(severity: .green, errors: [], warnings: [], notes: ["write protection is on"])
        let model = HealthPanelModel(ledger: try RunLedgerContractTests.fixture(), doctor: doctor)
        #expect(model.severity == .red)
        #expect(model.headline == "Scheduled runs need attention · 2 problems · 1 warning")
        #expect(model.groups.map(\.group) == [.red, .yellow])
        #expect(model.groups.first?.findings.map(\.id) == ["gates.probe_unsafe", "run.integrity_restored"])
        #expect(model.groups.last?.findings.map(\.id) == ["runs.failed_today"])
        #expect(model.engineIssues.isEmpty)
        #expect(model.everythingElse.count == 2)
        #expect(model.everythingElse.last == "write protection is on")
    }

    /// T4 review: healthy runs beside an engine problem named the runs healthy next to
    /// Settings' "Needs attention". The engine's problem leads; engine warnings keep R1's wording.
    @Test func anEngineProblemBesideHealthyRunsNamesTheEngine() {
        let green = RunHealth(severity: .green, evaluatedAt: "x", lastOkAt: nil, findings: [], slots: [])
        let problem = HealthSummary(doctor: DoctorReport(severity: .red, errors: ["launchd job missing"], warnings: []),
                                    runHealth: .some(green))
        #expect(HealthPanelModel.headline(runs: .green, shared: problem) == "Scout's engine needs attention · 1 problem")
        let warning = HealthSummary(doctor: DoctorReport(severity: .yellow, errors: [], warnings: ["w"]), runHealth: .some(green))
        #expect(HealthPanelModel.headline(runs: .green, shared: warning) == "Scheduled runs are healthy · 1 warning")
        let red = RunHealth(severity: .red, evaluatedAt: "x", lastOkAt: nil, findings: [
            .init(id: "runs.failing", severity: "red", since: nil, summary: "Runs keep failing", fix: "f", source: "ledger"),
        ], slots: [])
        let both = HealthSummary(doctor: DoctorReport(severity: .red, errors: ["e"], warnings: []), runHealth: .some(red))
        #expect(HealthPanelModel.headline(runs: .red, shared: both) == "Scheduled runs need attention · 2 problems")
    }

    @Test func summarisesTheRuns() throws {
        let model = HealthPanelModel(ledger: try RunLedgerContractTests.fixture())
        #expect(model.summary == "16 runs in the last two weeks: 4 finished, 4 failed, 8 skipped.")
        #expect(HealthPanelModel.summary([]) == "No scheduled runs in the last two weeks.")
    }

    @Test func linesSpeakPlainly() throws {
        let model = HealthPanelModel(ledger: try RunLedgerContractTests.fixture())
        #expect(model.runs.count == 16)
        let gates = try #require(model.runs.first)
        #expect(gates.title == "Morning briefing")
        #expect(gates.outcome == DisplayName.runStatus("skipped_gates"))
        // C4's advice for this cause (`gates_unsafe_endpoint`), via Problem(failureClass:).
        #expect(gates.reason == DisplayName.failureClass("gates_unsafe_endpoint").why)
        #expect(gates.fix == DisplayName.failureClass("gates_unsafe_endpoint").fix)
        #expect(gates.length == nil)
        #expect(gates.tone == .warning)

        let off = try #require(model.runs.first { $0.protection != nil && $0.outcome == "Finished" })
        #expect(off.protection == "Write protection turned off in Settings")
        #expect(off.tone == .warning)

        let restored = try #require(model.runs.first { $0.integrity != nil })
        #expect(restored.integrity == "Scout put a protected file back")

        let timeout = try #require(model.runs.first { $0.outcome == "Stopped: took too long" })
        #expect(timeout.length == "1 h 30 min")
        #expect(timeout.agent == "Claude Code · opus")
        #expect(timeout.tone == .problem)
        #expect(timeout.reason == "The run reached its time limit and Scout stopped it.")
        #expect(timeout.fix != nil)

        // The sign-in race explains itself, never as "an error Scout doesn't recognise".
        let race = try #require(model.runs.first { $0.reason == DisplayName.failureClass("auth_race").why })
        #expect(race.fix == DisplayName.failureClass("auth_race").fix)

        let limited = try #require(model.runs.first { $0.resetsAt != nil })
        #expect(limited.outcome == "Paused: usage limit")

        let lock = try #require(model.runs.first { $0.outcome == "Skipped: another run was going" })
        #expect(lock.tone == .skipped)
    }

    @Test func problemsOnlyHidesRoutineRuns() throws {
        let model = HealthPanelModel(ledger: try RunLedgerContractTests.fixture(), filter: .problems)
        // gates, sandbox-off ok, sandbox skip, runtime skip, network failure, rate limit, sign-in race,
        // integrity restore, timeout.
        #expect(model.runs.count == 9)
        #expect(!model.runs.contains { $0.tone == .ok || $0.tone == .skipped })
    }

    @Test func emptyLedger() {
        let model = HealthPanelModel(ledger: nil)
        #expect(model.headline == "Run health unknown")
        #expect(model.groups.isEmpty && model.runs.isEmpty && model.everythingElse.isEmpty)
    }

    @Test(arguments: [(nil, nil), (0, nil), (30, "1 min"), (360, "6 min"), (3600, "1 h"), (5400, "1 h 30 min")] as [(Int?, String?)])
    func lengths(seconds: Int?, expected: String?) {
        #expect(HealthPanelModel.length(seconds) == expected)
    }
}

@Suite("Run outcome wording")
struct RunOutcomeDisplayTests {
    @Test func sandboxAndIntegrityWording() {
        #expect(RunOutcomeDisplay.sandbox(nil) == nil)
        #expect(RunOutcomeDisplay.sandbox("enforced") == "Write protection on")
        #expect(RunOutcomeDisplay.sandbox("off", source: "env") == "Write protection turned off for this Mac's runs")
        #expect(RunOutcomeDisplay.sandbox("unavailable") == "Write protection isn't available on this Mac")
        #expect(RunOutcomeDisplay.sandbox("conflict") != nil)
        #expect(RunOutcomeDisplay.sandbox("failed") == "Write protection couldn't start")
        #expect(RunOutcomeDisplay.sandbox("partial_mode") == "Write protection: partial mode")
        #expect(!RunOutcomeDisplay.sandboxIsConcern("enforced") && RunOutcomeDisplay.sandboxIsConcern("off"))
        #expect(RunOutcomeDisplay.integrity("unrestored") == "A protected file changed and couldn't be put back")
        #expect(RunOutcomeDisplay.integrity("error") != nil && RunOutcomeDisplay.integrity("unchecked") != nil)
        #expect(RunOutcomeDisplay.integrity(nil) == nil)
        #expect(!RunOutcomeDisplay.integrityIsConcern("clean") && RunOutcomeDisplay.integrityIsConcern("restored"))
    }

    /// Each ledger row's problem comes from C4's `Problem`: a cause-carrying
    /// skip from its failure class, any other skip from its status.
    @Test func rowsExplainThemselvesThroughProblem() throws {
        let runs = try RunLedgerContractTests.fixture().runs
        let gates = try #require(runs.first { $0.outcome == "skipped_gates" })
        #expect(gates.problem == Problem(failureClass: "gates_unsafe_endpoint", details: "Log: .scout-logs/\(gates.log!)"))
        let redundant = try #require(runs.first { $0.outcome == "skipped_redundant" })
        #expect(redundant.problem?.severity == .info)
        #expect(redundant.problem?.title == "Evening consolidation: Skipped: already done")
        let ok = try #require(runs.first { $0.outcome == "ok" })
        #expect(ok.problem == nil && ok.displayStatus == "Finished")
    }

    @Test func groupsAndHeadlines() {
        #expect(RunOutcomeDisplay.Group(severity: "red") == .red)
        #expect(RunOutcomeDisplay.Group(severity: "purple") == .note)
        #expect(RunOutcomeDisplay.Group.allCases.map(\.title) == ["Needs attention", "Worth a look", "Everything else"])
        #expect(RunOutcomeDisplay.overall(.green) == "Scheduled runs are healthy")
        #expect(RunOutcomeDisplay.overall(.yellow) == "Scheduled runs need a look")
    }
}

/// Renders the Health page in every state, against the shared fixture.
@MainActor
@Suite("View smoke — Health", .serialized)
struct HealthViewSmokeTests {
    private func ledgerService(stdout: Data, stderr: Data = Data()) -> RunLedgerService {
        RunLedgerService(scoutctl: URL(fileURLWithPath: "/usr/bin/false"),
                         runner: StubRunner(result: ProcessResult(exitCode: 0, stdout: stdout, stderr: stderr)),
                         logsDirectory: URL(fileURLWithPath: "/tmp/none"), fileEvents: NoopFS())
    }

    private func render(_ svc: RunLedgerService, vault: SmokeVault, width: CGFloat = 1280) {
        ViewHost.render(
            HealthView(logsDirectory: vault.root.appendingPathComponent(".scout-logs"))
                .environmentObject(svc)
                .environmentObject(vault.state.engineHealth),
            size: CGSize(width: width, height: 860))
    }

    @Test("the Health page renders the shared fixture at real widths")
    func rendersLoaded() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        let svc = ledgerService(stdout: try RunLedgerContractTests.fixtureData())
        await svc.refresh()
        #expect(svc.ledger != nil)
        for width in LayoutSize.windowWidths { render(svc, vault: vault, width: width) }
    }

    @Test("the Health page renders loading, unsupported and failed")
    func rendersOtherStates() async throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        render(ledgerService(stdout: Data()), vault: vault)   // still loading
        let old = ledgerService(stdout: Data(), stderr: Data("Error: No such command 'runs'.".utf8))
        await old.refresh()
        render(old, vault: vault)
        let broken = ledgerService(stdout: Data("nope".utf8))
        await broken.refresh()
        render(broken, vault: vault)
    }

    @Test("a run row renders every tone")
    func rendersRows() throws {
        let model = HealthPanelModel(ledger: try RunLedgerContractTests.fixture())
        ViewHost.render(VStack { ForEach(model.runs) { RunLedgerRow(line: $0, logsDirectory: nil) } },
                        size: CGSize(width: 800, height: 1200))
    }

    @Test("the main window routes the Health row")
    func sidebarHasHealth() {
        #expect(SidebarItem.allCases.contains(.health))
        #expect(SidebarItem.health.statusLabel == "health")
        #expect(MainWindowView.showsOnboarding(state: .notInstalled, onboardingActive: false))
    }
}
