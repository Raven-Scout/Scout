import Testing
import Foundation
@testable import Scout

/// App audit C11: the Help menu's links and actions, and what Run Doctor… shows.
@Suite("Help menu")
struct HelpMenuTests {
    @Test func everyLink_isTheProjectsOwnHTTPSURL() {
        for link in HelpLink.allCases {
            #expect(link.url.scheme == "https", "\(link)")
            let host = link.url.host ?? ""
            #expect(host == "github.com" || host == "raven-scout.github.io", "\(link) → \(host)")
            #expect(!link.title.isEmpty)
        }
        #expect(HelpLink.privacy.url.path.hasSuffix("privacy.html"))
        #expect(HelpLink.reportIssue.url.path == "/Raven-Scout/Scout/issues/new")
        #expect(HelpLink.troubleshooting.url.fragment == "faq--troubleshooting")
    }

    @Test func logsFolder_isScoutLogsWhenPresent() throws {
        let vault = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: vault) }
        try FileManager.default.createDirectory(at: vault, withIntermediateDirectories: true)
        #expect(HelpActions.logsFolder(vault: vault) == vault, "falls back to the vault before any run")
        let logs = vault.appendingPathComponent(".scout-logs", isDirectory: true)
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        #expect(HelpActions.logsFolder(vault: vault).standardizedFileURL == logs.standardizedFileURL)
    }

    @Test func windowIDs_areDistinct() {
        #expect(HelpWindow.doctor != HelpWindow.diagnostics)
    }
}

@Suite("Doctor presentation")
struct DoctorPresentationTests {
    let install = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/e/scoutctl"),
                                python: nil, version: "0.18.15", vault: nil)

    @Test func notInstalled_pointsToSetup() {
        let p = DoctorPresentation(state: .notInstalled, doctor: nil, lastError: nil)
        #expect(p.headline == "Scout's engine isn't installed yet")
        #expect(p.tone == .warn)
    }

    @Test func broken_showsTheReason() {
        let p = DoctorPresentation(state: .broken(nil, reason: "pointer names a missing scoutctl"), doctor: nil, lastError: nil)
        #expect(p.tone == .error)
        #expect(p.groups.first?.items.first?.text == "pointer names a missing scoutctl")
    }

    @Test func doctorThatCouldNotRun_saysSo() {
        let p = DoctorPresentation(state: .managed(install, vaultBootstrapped: true), doctor: nil, lastError: "could not run scoutctl")
        #expect(p.headline == "Doctor couldn't run")
        #expect(p.groups.first?.items.first?.text == "could not run scoutctl")
    }

    @Test func notRunYet_isNeutral() {
        let p = DoctorPresentation(state: .managed(install, vaultBootstrapped: true), doctor: nil, lastError: nil)
        #expect(p.headline == "Doctor hasn't run yet")
        #expect(p.tone == .neutral)
        #expect(p.groups.isEmpty)
    }

    @Test func everyFindingIsShown_notJustTheFirst() {
        let rh = RunHealth(severity: .red, evaluatedAt: "2026-11-16T12:00:00Z", lastOkAt: nil, findings: [
            .init(id: "runs.failing", severity: "red", since: "2026-11-15T08:00:00Z", summary: "Runs keep failing",
                  fix: "Open Terminal and run: claude auth login", source: "ledger"),
            .init(id: "slot.late", severity: "yellow", since: nil, summary: "A slot is late", fix: "", source: "schedule"),
            .init(id: "runs.note", severity: "note", since: nil, summary: "A note", fix: "", source: "ledger"),
        ], slots: [])
        let doctor = DoctorReport(severity: .yellow, errors: ["e1"], warnings: ["w1", "w2"], runHealth: rh)
        let p = DoctorPresentation(state: .managed(install, vaultBootstrapped: true), doctor: doctor, lastError: nil)
        #expect(p.headline == "Scout's engine works, with warnings")
        #expect(p.groups.map(\.title) == ["Problems", "Warnings", "Scheduled runs"])
        #expect(p.groups[0].items.map(\.text) == ["e1"])
        #expect(p.groups[1].items.map(\.text) == ["w1", "w2"])
        let runs = p.groups[2]
        #expect(runs.tone == .error)
        #expect(runs.items.count == 3)
        #expect(runs.items[0].fix == "Open Terminal and run: claude auth login")
        #expect(runs.items[1].fix == nil)
    }

    @Test func runAgain_onlyWhenTheDoctorCanRun() {
        #expect(DoctorPresentation(state: .notInstalled, doctor: nil, lastError: nil).canRun == false)
        #expect(DoctorPresentation(state: .broken(install, reason: "x"), doctor: nil, lastError: nil).canRun == false)
        #expect(DoctorPresentation(state: .managed(install, vaultBootstrapped: false), doctor: nil, lastError: nil).canRun)
        #expect(DoctorPresentation(state: .external(install, .devCheckout), doctor: nil, lastError: nil).canRun)
    }

    @Test func duplicateFindingTexts_areAllShown() {
        let doctor = DoctorReport(severity: .yellow, errors: [], warnings: ["same", "same"])
        let p = DoctorPresentation(state: .managed(install, vaultBootstrapped: true), doctor: doctor, lastError: nil)
        #expect(p.groups.first?.items.count == 2)
    }

    @Test func healthy_saysSo() {
        let rh = RunHealth(severity: .green, evaluatedAt: "x", lastOkAt: nil, findings: [], slots: [])
        let p = DoctorPresentation(state: .managed(install, vaultBootstrapped: true),
                                   doctor: DoctorReport(severity: .green, errors: [], warnings: [], runHealth: rh), lastError: nil)
        #expect(p.tone == .ok)
        #expect(p.groups.map(\.title) == ["Scheduled runs: healthy"])
        #expect(p.groups[0].items.map(\.text) == ["No problems with recent runs."])
    }
}
