import SwiftUI
import Testing
@testable import Scout

/// Smoke coverage for Settings ▸ Engine (spec §5, Ruling 41). Renders
/// `EngineSettingsSection` once per action state — `.managed` behind a
/// bundled version (Update, idle and running), `.external(_, .devCheckout)`
/// behind a bundled version (copy-`/scout-update` hand-off), "Set up…" for
/// `.notInstalled` and for `.managed` with a vault not set up, and "Repair…"
/// for `.broken` — plus the whole `SettingsView` wired to a populated vault,
/// the same way `ShellViewSmokeTests.settingsRenders` in `ViewSmokeTests.swift` does.
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

    @Test("managed, behind the bundled version, with Update wired — idle and running", arguments: [false, true])
    func managedBehindBundled(isUpdating: Bool) {
        ViewHost.render(
            EngineSettingsSection(
                health: health(.managed(install, vaultBootstrapped: true)),
                bundledVersion: "0.11.0",
                isUpdating: isUpdating,
                onUpdate: {}, onSetUp: {}
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

    @Test("not installed — Set up…")
    func notInstalled() {
        ViewHost.render(
            EngineSettingsSection(health: health(.notInstalled), bundledVersion: "0.11.0", onUpdate: {}, onSetUp: {})
                .frame(width: 640),
            size: CGSize(width: 640, height: 420))
    }

    @Test("managed but the vault is not set up — Set up…")
    func managedVaultNotSetUp() {
        let state = EngineState.managed(install, vaultBootstrapped: false)
        #expect(EngineSettingsModel(state: state, doctor: nil, lastError: nil, bundledVersion: nil).setupAction == .setUp)
        ViewHost.render(
            EngineSettingsSection(health: health(state), bundledVersion: nil, onUpdate: {}, onSetUp: {})
                .frame(width: 640),
            size: CGSize(width: 640, height: 420))
    }

    @Test("broken — Repair…")
    func broken() {
        ViewHost.render(
            EngineSettingsSection(
                health: health(.broken(install, reason: "engine pointer names a missing scoutctl: /s")),
                bundledVersion: nil,
                onUpdate: {}, onSetUp: {}
            ).frame(width: 640),
            size: CGSize(width: 640, height: 420))
    }

    @Test("managed at the bundled version with an unfinished switch — Finish update")
    func finishUpdate() {
        ViewHost.render(
            EngineSettingsSection(health: health(.managed(install, vaultBootstrapped: true)), bundledVersion: "0.10.0",
                                  unfinishedSwitch: true, onUpdate: {}, onSetUp: {})
                .frame(width: 640),
            size: CGSize(width: 640, height: 420))
    }

    @Test("broken, managed by another installer — /scout-update hand-off")
    func foreignBroken() {
        let foreign = EngineInstall(root: URL(fileURLWithPath: "/Users/alex/scout-plugin"), scoutctl: URL(fileURLWithPath: "/usr/bin/false"),
                                    python: nil, version: "0.10.0", vault: nil, managedBy: "dev")
        ViewHost.render(
            EngineSettingsSection(health: health(.broken(foreign, reason: "engine pointer names a missing scoutctl: /s")),
                                  bundledVersion: "0.11.0", onUpdate: {}, onSetUp: {})
                .frame(width: 640),
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
