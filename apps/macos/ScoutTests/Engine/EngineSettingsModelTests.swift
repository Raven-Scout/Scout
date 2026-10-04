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
        #expect(!m.canRepair)
    }

    @Test func notInstalledAndRedDoctorMessages() {
        let m1 = EngineSettingsModel(state: .notInstalled, doctor: nil, lastError: nil, bundledVersion: nil)
        #expect(m1.sourceLabel == "Not installed" && m1.installedVersionLabel == "—" && !m1.healthIsOK && m1.canRepair)
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

    // MARK: F3 — the copyable next step, per state

    @Test func notInstalledNextStepIsTheOneLineInstaller() {
        let m = EngineSettingsModel(state: .notInstalled, doctor: nil, lastError: nil, bundledVersion: nil)
        #expect(m.nextStep == EngineNextStep(
            text: "Install the engine: run this in Terminal, then run /scout-setup in Claude Code.",
            copyValue: "curl -fsSL https://raw.githubusercontent.com/Raven-Scout/scout-plugin/main/install.sh | bash"))
    }

    @Test func brokenNextStepIsScoutUpdate() {
        let m = EngineSettingsModel(state: .broken(install, reason: "r"), doctor: nil, lastError: nil, bundledVersion: nil)
        #expect(m.nextStep == EngineNextStep(text: "Repair the engine by running /scout-update in Claude Code.",
                                             copyValue: "/scout-update"))
        let noInstall = EngineSettingsModel(state: .broken(nil, reason: "r"), doctor: nil, lastError: nil, bundledVersion: nil)
        #expect(noInstall.nextStep?.copyValue == "/scout-update")
    }

    @Test func unbootstrappedVaultNextStepIsScoutSetup() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: false), doctor: nil, lastError: nil, bundledVersion: nil)
        #expect(m.nextStep == EngineNextStep(text: "Set up your vault by running /scout-setup in Claude Code.",
                                             copyValue: "/scout-setup"))
    }

    @Test func usableEnginesHaveNoNextStep() {
        let managed = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true), doctor: nil,
                                          lastError: "could not run scoutctl: x", bundledVersion: "0.11.0")
        #expect(managed.nextStep == nil)
        for source in [ExternalSource.devCheckout, .installSh, .claudeCode, .marketplaceCache, .shim, .unknown("x")] {
            let external = EngineSettingsModel(state: .external(install, source), doctor: nil, lastError: nil, bundledVersion: nil)
            #expect(external.nextStep == nil)
        }
    }

    @Test func copyButtonTitleNamesSlashCommandsButNotTheTerminalOneLiner() {
        #expect(EngineSettingsSection.copyButtonTitle(for: EngineNextStep(text: "t", copyValue: "/scout-setup")) == "Copy /scout-setup")
        #expect(EngineSettingsSection.copyButtonTitle(for: EngineNextStep(text: "t", copyValue: "curl -fsSL x | bash")) == "Copy command")
    }
}
