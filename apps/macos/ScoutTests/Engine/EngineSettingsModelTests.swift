import Testing
import Foundation
@testable import Scout

@Suite("EngineSettingsModel")
struct EngineSettingsModelTests {
    let install = EngineInstall(root: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/0.10.0"),
                                scoutctl: URL(fileURLWithPath: "/s"), python: nil, version: "0.10.0", vault: nil)

    @Test func managedAndGreen() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true),
                                    doctor: DoctorReport(severity: .green, errors: [], warnings: []), bundledVersion: "0.10.0")
        #expect(m.sourceLabel == "App-managed")
        #expect(m.installedVersionLabel == "0.10.0")
        #expect(m.healthLabel == "Healthy")
        #expect(m.healthIsOK)
        #expect(!m.canUpdate)
    }

    @Test func managedBehindBundledCanUpdate() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true), doctor: nil, bundledVersion: "0.11.0")
        #expect(m.canUpdate)
        #expect(m.bundledVersionLabel == "0.11.0")
    }

    @Test func externalBehindShowsHandOff() {
        let m = EngineSettingsModel(state: .external(install, .devCheckout), doctor: nil, bundledVersion: "0.11.0")
        #expect(m.sourceLabel == "Dev checkout (~/scout-plugin)")
        #expect(m.showsHandOff)
        #expect(!m.canUpdate)
        #expect(!m.canRepair)
    }

    @Test func notInstalledAndRedDoctorMessages() {
        let m1 = EngineSettingsModel(state: .notInstalled, doctor: nil, bundledVersion: nil)
        #expect(m1.sourceLabel == "Not installed" && m1.installedVersionLabel == "—" && !m1.healthIsOK && m1.canRepair)
        let m2 = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true),
                                     doctor: DoctorReport(severity: .red, errors: ["launchd: com.scout.heartbeat not registered"], warnings: ["w"]), bundledVersion: nil)
        #expect(m2.healthLabel == "Needs attention")
        #expect(m2.messages == ["launchd: com.scout.heartbeat not registered", "w"])
    }

    @Test func atBundledVersionShowsNeitherUpdateNorHandOff() {
        let managed = EngineSettingsModel(state: .managed(install, vaultBootstrapped: true), doctor: nil, bundledVersion: "0.10.0")
        #expect(!managed.canUpdate)
        #expect(!managed.showsHandOff)
        let external = EngineSettingsModel(state: .external(install, .devCheckout), doctor: nil, bundledVersion: "0.10.0")
        #expect(!external.canUpdate)
        #expect(!external.showsHandOff)
    }

    @Test func vaultNotBootstrappedShowsHonestHealthLabel() {
        let m = EngineSettingsModel(state: .managed(install, vaultBootstrapped: false),
                                    doctor: DoctorReport(severity: .green, errors: [], warnings: []), bundledVersion: nil)
        #expect(m.healthLabel == "Vault not set up")
        #expect(!m.healthIsOK)
        #expect(m.messages.isEmpty)
    }
}
