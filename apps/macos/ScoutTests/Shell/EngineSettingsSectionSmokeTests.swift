import SwiftUI
import Testing
@testable import Scout

/// Smoke coverage for Settings ▸ Engine (spec §5). Renders `EngineSettingsSection`
/// across the states that change which rows appear — `.managed` behind a
/// bundled version (Update + Repair wired), `.external(_, .devCheckout)` behind
/// a bundled version (hand-off row), `.notInstalled`, and `.broken` — plus the
/// whole `SettingsView` wired to a populated vault, the same way
/// `ShellViewSmokeTests.settingsRenders` in `ViewSmokeTests.swift` does.
///
/// Every `EngineHealthService` here is built from an `EngineLayout` rooted at
/// a fresh temp directory (never `.live`) and an `initialState:` passed
/// directly — `refresh()` is never called, so nothing shells out or touches
/// the real `~/.local/state/scout`.
@MainActor
@Suite("Engine settings — smoke", .serialized)
struct EngineSettingsSectionSmokeTests {

    private let install = EngineInstall(
        root: URL(fileURLWithPath: "/Users/alex/.local/share/scout/engine/0.10.0"),
        scoutctl: URL(fileURLWithPath: "/usr/bin/false"), python: nil, version: "0.10.0", vault: nil)

    private func health(_ state: EngineState) -> EngineHealthService {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("EngineSettingsSectionSmokeTests-\(UUID().uuidString)", isDirectory: true)
        return EngineHealthService(
            locator: EngineLocator(layout: EngineLayout(home: home)),
            runner: RuleBasedRunner(),
            initialState: state)
    }

    @Test("managed, behind the bundled version, with Update and Repair wired")
    func managedBehindBundled() {
        ViewHost.render(
            EngineSettingsSection(
                health: health(.managed(install, vaultBootstrapped: true)),
                bundledVersion: "0.11.0",
                onUpdate: {}, onRepair: {}
            ).frame(width: 640),
            size: CGSize(width: 640, height: 420))
    }

    @Test("external dev checkout, behind the bundled version, shows the hand-off row")
    func externalDevCheckoutBehindBundled() {
        ViewHost.render(
            EngineSettingsSection(
                health: health(.external(install, .devCheckout)),
                bundledVersion: "0.11.0"
            ).frame(width: 640),
            size: CGSize(width: 640, height: 420))
    }

    @Test("not installed")
    func notInstalled() {
        ViewHost.render(
            EngineSettingsSection(health: health(.notInstalled), bundledVersion: nil)
                .frame(width: 640),
            size: CGSize(width: 640, height: 420))
    }

    @Test("broken")
    func broken() {
        ViewHost.render(
            EngineSettingsSection(
                health: health(.broken(install, reason: "engine pointer names a missing scoutctl: /s")),
                bundledVersion: nil
            ).frame(width: 640),
            size: CGSize(width: 640, height: 420))
    }

    @Test("the whole Settings pane renders with the Engine section wired")
    func settingsViewRendersWithEngineSection() throws {
        let vault = try SmokeVault(); defer { vault.tearDown() }
        ViewHost.render(
            SettingsView().environmentObject(vault.state),
            size: CGSize(width: 700, height: 900))
    }
}
