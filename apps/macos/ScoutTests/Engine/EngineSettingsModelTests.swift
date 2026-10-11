import Testing
import Foundation
@testable import Scout

@Suite("EngineSettingsModel")
struct EngineSettingsModelTests {
    let install = EngineInstall(root: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/0.10.0"),
                                scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil)

    @Test func managedAndGreen() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true),
                                    doctor: DoctorReport(severity: .green, errors: [], warnings: []), lastError: nil, bundledVersion: "0.10.0")
        #expect(m.sourceLabel == "App-managed")
        #expect(m.installedVersionLabel == "0.10.0")
        #expect(m.healthLabel == "Healthy")
        #expect(m.healthIsOK)
        #expect(!m.canUpdate)
    }

    @Test func managedBehindBundledCanUpdate() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true), doctor: nil, lastError: nil, bundledVersion: "0.11.0")
        #expect(m.canUpdate)
        #expect(m.bundledVersionLabel == "0.11.0")
    }

    @Test func externalBehindShowsHandOff() {
        let m = EngineSettingsModel(state: .external(install, .devCheckout), doctor: nil, lastError: nil, bundledVersion: "0.11.0")
        #expect(m.sourceLabel == "Dev checkout (~/scout-plugin)")
        #expect(m.showsHandOff)
        #expect(!m.canUpdate)
        #expect(m.setupAction == nil)
    }

    @Test func notInstalledAndRedDoctorMessages() {
        let m1 = EngineSettingsModel(state: .notInstalled, doctor: nil, lastError: nil, bundledVersion: nil)
        #expect(m1.sourceLabel == "Not installed" && m1.installedVersionLabel == "—" && !m1.healthIsOK && m1.setupAction == .setUp)
        let m2 = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true),
                                     doctor: DoctorReport(severity: .red, errors: ["launchd: com.scout.heartbeat not registered"], warnings: ["w"]),
                                     lastError: nil, bundledVersion: nil)
        #expect(m2.healthLabel == "Needs attention")
        #expect(m2.messages == ["launchd: com.scout.heartbeat not registered", "w"])
    }

    @Test func atBundledVersionShowsNeitherUpdateNorHandOff() {
        let managed = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true), doctor: nil, lastError: nil, bundledVersion: "0.10.0")
        #expect(!managed.canUpdate)
        #expect(!managed.showsHandOff)
        let external = EngineSettingsModel(state: .external(install, .devCheckout), doctor: nil, lastError: nil, bundledVersion: "0.10.0")
        #expect(!external.canUpdate)
        #expect(!external.showsHandOff)
    }

    @Test func vaultNotBootstrappedShowsHonestHealthLabel() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: false),
                                    doctor: DoctorReport(severity: .green, errors: [], warnings: []), lastError: nil, bundledVersion: nil)
        #expect(m.healthLabel == "Vault not set up")
        #expect(!m.healthIsOK)
        #expect(m.messages.isEmpty)
    }

    // MARK: F2 — a doctor that could not run

    @Test("a usable engine whose doctor failed says so, with the error", arguments: [
        EngineState.managed(EngineInstall(root: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/0.10.0"),
                                          scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil),
                            vaultBootstrapped: true),
        .external(EngineInstall(root: URL(fileURLWithPath: "/Users/alex/scout-plugin"),
                                scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.11.0", vault: nil), .devCheckout),
    ])
    func failedDoctorIsVisible(state: EngineState) {
        let m = EngineSettingsModel(state: state, doctor: nil,
                                    lastError: "could not run scoutctl: ENOENT /s", bundledVersion: nil)
        #expect(m.healthLabel == "Could not run doctor")
        #expect(!m.healthIsOK)
        #expect(m.messages == ["could not run scoutctl: ENOENT /s"])
    }

    @Test func noDoctorAndNoErrorIsStillUnknown() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true), doctor: nil, lastError: nil, bundledVersion: nil)
        #expect(m.healthLabel == "Unknown")
        #expect(m.messages.isEmpty)
    }

    /// Gating states keep their own labels/messages even with a doctor error.
    @Test func gatingStatesKeepTheirOwnLabelsOverADoctorError() {
        let err = "doctor output not understood: Traceback"
        let unset = EngineSettingsModel(state: .managed(install, vaultBootstrapped: false), doctor: nil, lastError: err, bundledVersion: nil)
        #expect(unset.healthLabel == "Vault not set up")
        #expect(!unset.messages.contains(err))
        let broken = EngineSettingsModel(state: .broken(install, reason: "engine pointer names a missing scoutctl: /s"),
                                         doctor: nil, lastError: err, bundledVersion: nil)
        #expect(broken.healthLabel == "Broken")
        #expect(broken.messages == ["engine pointer names a missing scoutctl: /s"])
        let missing = EngineSettingsModel(state: .notInstalled, doctor: nil, lastError: err, bundledVersion: nil)
        #expect(missing.healthLabel == "Not installed")
        #expect(missing.messages.isEmpty)
    }

    // MARK: Ruling 41 — a real action for every state the app can act on

    @Test func gatingStatesOpenOnboardingAsSetUpOrRepair() {
        let notInstalled = EngineSettingsModel(state: .notInstalled, doctor: nil, lastError: nil, bundledVersion: "0.11.0")
        #expect(notInstalled.setupAction == .setUp && notInstalled.setupHelp != nil)
        let unset = EngineSettingsModel(state: .managed(install, vaultBootstrapped: false), doctor: nil, lastError: nil, bundledVersion: "0.11.0")
        #expect(unset.setupAction == .setUp)
        #expect(unset.setupHelp == "The engine is installed; finish setting up your vault.")
        #expect(!unset.canUpdate)         // finish onboarding first, as the launch upgrade does
        for state in [EngineState.broken(install, reason: "r"), .broken(nil, reason: "r")] {
            let broken = EngineSettingsModel(state: state, doctor: nil, lastError: nil, bundledVersion: nil)
            #expect(broken.setupAction == .repair && broken.setupHelp != nil)
        }
        #expect(EngineSetupAction.setUp.buttonTitle == "Set up…")
        #expect(EngineSetupAction.repair.buttonTitle == "Repair…")
    }

    @Test func usableEnginesOfferNoSetupAndExternalOnesNeverUpdateInApp() {
        let managed = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true), doctor: nil,
                                          lastError: "could not run scoutctl: x", bundledVersion: "0.11.0")
        #expect(managed.setupAction == nil && managed.setupHelp == nil)
        #expect(managed.canUpdate && !managed.showsHandOff)
        for source in [ExternalSource.devCheckout, .installSh, .claudeCode, .marketplaceCache, .shim, .unknown("x")] {
            let external = EngineSettingsModel(state: .external(install, source), doctor: nil, lastError: nil, bundledVersion: "0.11.0")
            #expect(external.setupAction == nil)
            #expect(!external.canUpdate && external.showsHandOff)
        }
    }

    /// Ruling 69 I4/I1: every managed, set-up engine at or behind the
    /// bundle that needs work gets an upgrade-backed action; nothing that
    /// would downgrade, and nothing for a healthy engine.
    @Test func upgradeActionPerState() {
        func at(_ v: String) -> EngineInstall { EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: v, vault: nil) }
        let red = DoctorReport(severity: .red, errors: ["launchd: com.scout.heartbeat not registered"], warnings: [])
        let green = DoctorReport(severity: .green, errors: [], warnings: [])
        func action(_ s: EngineState, _ d: DoctorReport?, unfinished: Bool = false) -> EngineUpgradeAction? {
            EngineSettingsModel(state: s, doctor: d, lastError: nil, bundledVersion: "0.11.0", unfinishedSwitch: unfinished).upgradeAction
        }
        #expect(action(.managed(at("0.10.0"), vaultBootstrapped: true), green) == .update)
        #expect(action(.managed(at("0.10.0"), vaultBootstrapped: true), red) == .update)          // the update also repairs
        #expect(action(.managed(at("0.11.0"), vaultBootstrapped: true), green, unfinished: true) == .finishUpdate)
        #expect(action(.managed(at("0.11.0"), vaultBootstrapped: true), red) == .repair)
        #expect(action(.managed(at("0.11.0"), vaultBootstrapped: true), green) == nil)
        #expect(action(.managed(at("0.12.0"), vaultBootstrapped: true), red, unfinished: true) == nil)   // never a downgrade
        #expect(action(.managed(at("0.10.0"), vaultBootstrapped: false), red) == nil)           // onboarding first
        #expect(action(.external(at("0.10.0"), .devCheckout), red) == nil)
        #expect(action(.broken(at("0.11.0"), reason: "r"), nil) == nil)
        let finishing = EngineSettingsModel(state: .managed(at("0.11.0"), vaultBootstrapped: true), doctor: green, lastError: nil,
                                            bundledVersion: "0.11.0", unfinishedSwitch: true)
        #expect(finishing.canUpdate)
        #expect(EngineUpgradeAction.finishUpdate.buttonTitle == "Finish update")
        #expect(EngineUpgradeAction.repair.buttonTitle == "Repair…")
    }

    /// Ruling 69 I6: a broken pointer another installer wrote gets the
    /// `/scout-update` hand-off — no Set up / Repair.
    @Test func aForeignBrokenEngineIsHandedOff() {
        let foreign = EngineInstall(root: URL(fileURLWithPath: "/Users/alex/scout-plugin"), scoutctl: URL(fileURLWithPath: "/s"),
                                    python: nil, version: "0.10.0", vault: nil, managedBy: "dev")
        let m = EngineSettingsModel(state: .broken(foreign, reason: "engine pointer names a missing scoutctl: /s"), doctor: nil,
                                    lastError: nil, bundledVersion: "0.11.0")
        #expect(m.setupAction == nil && m.upgradeAction == nil && !m.canUpdate)
        #expect(m.showsHandOff)
        #expect(m.handOffTitle == "Repair engine" && m.handOffHelp.contains("(dev)") && m.handOffHelp.contains("/scout-update"))
        #expect(m.healthLabel == "Broken")
        let ours = EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0",
                                 vault: nil, managedBy: "scout-app")
        #expect(EngineSettingsModel(state: .broken(ours, reason: "r"), doctor: nil, lastError: nil, bundledVersion: nil).setupAction == .repair)
    }

    /// Ruling 69 M1: "/scout-update" in the vault help only for engines
    /// managed in Claude Code.
    @Test func vaultHelpMentionsScoutUpdateOnlyForExternalEngines() {
        #expect(EngineSettingsModel(state: .external(install, .devCheckout), doctor: nil, lastError: nil, bundledVersion: nil).vaultHelp.contains("/scout-update"))
        #expect(!EngineSettingsModel(state: .managed(install, vaultBootstrapped: true), doctor: nil, lastError: nil, bundledVersion: nil).vaultHelp.contains("/scout-update"))
    }

    /// The Update button is offered exactly when the launch-time upgrade
    /// would run, so it never shows a button that does nothing.
    @Test("canUpdate agrees with AppState.shouldAutoUpgrade", arguments: [
        EngineState.notInstalled,
        .managed(EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil), vaultBootstrapped: true),
        .managed(EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil), vaultBootstrapped: false),
        .managed(EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.11.0", vault: nil), vaultBootstrapped: true),
        .external(EngineInstall(root: URL(fileURLWithPath: "/e"), scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil), .devCheckout),
        .broken(nil, reason: "r"),
    ])
    func canUpdateMatchesTheLaunchUpgrade(state: EngineState) {
        let release = EngineRelease(schemaVersion: 2, version: "0.11.0", engine: .init(version: "0.11.0"), uv: .init(version: "0", sha256: [:]))
        for unfinished in [false, true] {
            let m = EngineSettingsModel(state: state, doctor: nil, lastError: nil, bundledVersion: release.engine.version, unfinishedSwitch: unfinished)
            #expect(m.canUpdate == AppState.shouldAutoUpgrade(state: state, release: release, switchUnfinished: unfinished), "unfinished: \(unfinished)")
        }
    }
}
