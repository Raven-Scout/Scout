import Foundation
import Testing
@testable import Scout

/// UI audit R1: one list of issues and one count for every health surface.
@Suite("Health summary")
struct HealthSummaryTests {
    private func finding(_ id: String, _ severity: String, _ summary: String) -> RunHealth.Finding {
        RunHealth.Finding(id: id, severity: severity, since: "2026-11-17T07:00:00Z", summary: summary, fix: "fix \(id)",
                          source: "ledger")
    }

    private func runs(_ severity: DoctorReport.Severity, _ findings: [RunHealth.Finding]) -> RunHealth {
        RunHealth(severity: severity, evaluatedAt: "2026-11-17T12:00:00Z", lastOkAt: nil, findings: findings, slots: [])
    }

    @Test func worstFirstWithRunsBeforeTheInstall() {
        let doctor = DoctorReport(severity: .red, errors: ["install error"], warnings: ["install warning"],
                                  runHealth: runs(.red, [finding("n", "note", "a note"), finding("w", "yellow", "a run warning"),
                                                         finding("r", "red", "a run problem")]))
        let s = HealthSummary(doctor: doctor)
        #expect(s.issues.map(\.title) == ["a run problem", "install error", "a run warning", "install warning", "a note"])
        #expect(s.problemCount == 2 && s.warningCount == 2 && s.noteCount == 1 && s.attentionCount == 4)
        #expect(s.severity == .red)
        #expect(s.reason == "a run problem")
        #expect(s.attentionLabel == "2 problems · 2 warnings")
        #expect(s.issues.first?.since == "2026-11-17T07:00:00Z")
        #expect(s.issues.first?.fix == "fix r")
    }

    @Test func nothingWrongIsGreenWithNoReason() {
        let s = HealthSummary(doctor: DoctorReport(severity: .green, errors: [], warnings: [],
                                                   runHealth: runs(.green, [finding("n", "note", "a note")])))
        #expect(s.severity == .green && s.reason == nil && s.attentionLabel == nil && s.attentionCount == 0)
        #expect(HealthSummary(doctor: nil).issues.isEmpty)
    }

    @Test func aGroupedWarningIsOneIssueWithItsFiles() {
        let files = (1...34).map { "scripts/check-\($0).py" }
        let runners = ["run-a.sh", "run-b.sh"]
        let scriptsLine = "privacy: 34 vault scripts read the knowledge base directly …"
        let runnersLine = "privacy: 2 vault runners don't export SCOUT_READER=autonomous …"
        let futureLine = "something: 3 files do a new thing"
        let doctor = DoctorReport(severity: .yellow, errors: [], warnings: [scriptsLine, runnersLine, futureLine, "a lone warning"],
                                  warningGroups: [
                                      .init(kind: "privacy.script-raw-kb-read", summary: scriptsLine, items: files),
                                      .init(kind: "privacy.runner-no-reader", summary: runnersLine, items: runners),
                                      .init(kind: "future.kind", summary: futureLine, items: ["a", "b", "c"]),
                                  ])
        let s = HealthSummary(doctor: doctor)
        #expect(s.warningCount == 4)
        #expect(s.issues.map(\.title) == [
            "34 vault scripts read the knowledge base directly, without Scout's privacy labels",
            "2 vault runners don't mark their reads as unattended",
            futureLine,   // a kind this build doesn't know keeps the engine's words
            "a lone warning",
        ])
        #expect(s.issues.map(\.items) == [files, runners, ["a", "b", "c"], []])
        #expect(s.issues[2].fix == nil)
        // The engine's fix for a runner names both exports (R1 review).
        #expect(s.issues[1].fix?.contains("export SCOUT_MODE=") == true)
        #expect(s.issues[1].fix?.contains("export SCOUT_READER=autonomous") == true)
        #expect(s.issues.allSatisfy { $0.source == .install })
    }

    @Test func oneFileGroupsReadInTheSingular() {
        let issue = HealthSummary.issue(for: .init(kind: "privacy.script-raw-kb-read", summary: "s", items: ["scripts/x.py"]), id: "w")
        #expect(issue.title == "1 vault script reads the knowledge base directly, without Scout's privacy labels")
        #expect(IssueFilesDisclosure.label(1) == "Show the file")
        #expect(IssueFilesDisclosure.label(34) == "Show all 34 files")
    }

    @Test func theHealthPageCanPassTheLedgersRunHealth() {
        let doctor = DoctorReport(severity: .yellow, errors: [], warnings: ["w"],
                                  runHealth: runs(.red, [finding("old", "red", "from the doctor")]))
        let ledger = runs(.yellow, [finding("runs.failed_today", "yellow", "from the ledger")])
        #expect(HealthSummary(doctor: doctor, runHealth: .some(ledger)).issues.map(\.title) == ["from the ledger", "w"])
        // An explicit "no run health" (a ledger without its block) doesn't fall back to the doctor's.
        #expect(HealthSummary(doctor: doctor, runHealth: .some(nil)).issues.map(\.title) == ["w"])
        #expect(HealthSummary(doctor: doctor).issues.map(\.title) == ["from the doctor", "w"])
    }

    /// The acceptance case from the audit: three surfaces, one count and one reason.
    @Test func everySurfaceTellsTheSameStory() throws {
        let summaryLine = "privacy: 34 vault scripts read the knowledge base directly …"
        let doctor = DoctorReport(
            severity: .yellow, errors: [], warnings: [summaryLine],
            runHealth: runs(.yellow, [finding("runs.failed_today", "yellow",
                                              "morning-briefing (at 08:00, unknown) failed today and hasn't finished a run since.")]),
            warningGroups: [.init(kind: "privacy.script-raw-kb-read", summary: summaryLine, items: (1...34).map { "s\($0)" })])
        let install = EngineInstall(root: URL(fileURLWithPath: "/tmp/engine"), scoutctl: URL(fileURLWithPath: "/tmp/engine/scoutctl"),
                                    python: nil, version: "0.20.0", vault: nil)
        let state = EngineState.managed(install, vaultBootstrapped: true)

        let settings = EngineSettingsModel(state: state, doctor: doctor, lastError: nil, bundledVersion: nil)
        #expect(settings.findingCountsLabel == "2 warnings")
        #expect(settings.healthLabel == "Healthy, with warnings")

        let doctorWindow = DoctorPresentation(state: state, doctor: doctor, lastError: nil)
        #expect(doctorWindow.headline.hasSuffix("· 2 warnings"))

        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let ok = Run.make(type: .consolidation, startedAt: now.addingTimeInterval(-600), endedAt: now.addingTimeInterval(-60),
                          status: .success)
        let status = ScoutStatus.derive(engine: state, doctor: doctor, doctorError: nil, latestRun: ok, now: now)
        #expect(status.title == "Ready · 2 warnings")
        #expect(status.detail.hasPrefix("morning-briefing (at 08:00, unknown) failed today"))

        let ledger = try RunLedgerContractTests.fixture()
        let page = HealthPanelModel(ledger: ledger, doctor: doctor)
        let pageSummary = HealthSummary(doctor: doctor, runHealth: .some(ledger.health))
        #expect(page.headline.hasSuffix("· \(pageSummary.attentionLabel ?? "")"))
        #expect(page.engineIssues.map(\.title) == ["34 vault scripts read the knowledge base directly, without Scout's privacy labels"])
    }
}
