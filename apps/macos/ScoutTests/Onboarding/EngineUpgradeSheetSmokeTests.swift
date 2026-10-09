import SwiftUI
import Testing
@testable import Scout

/// The upgrade sheet's pure failure/switch-over rules, plus light smoke
/// renders (CI's runner is starved by main-actor suites): the sheet after a
/// failed step, the window's gate showing onboarding, and the Settings sheet.
@Suite("EngineUpgradeSheet")
struct EngineUpgradeSheetTests {
    @Test func failureNamesTheFailedStepElseTheUpgradeError() {
        let failed: [InstallStep: InstallProgress] = [
            .ensureUv: InstallProgress(step: .ensureUv, status: .done, log: "/u"),
            .buildVenv: InstallProgress(step: .buildVenv, status: .failed("x"), log: "install-venv.sh failed: boom"),
        ]
        #expect(EngineUpgradeSheet.failure(progress: failed, error: "ignored") == "Build Python environment failed: install-venv.sh failed: boom")
        #expect(EngineUpgradeSheet.failure(progress: [:], error: "Claude Code wasn't found") == "Claude Code wasn't found")
        #expect(EngineUpgradeSheet.failure(progress: [.verify: InstallProgress(step: .verify, status: .running, log: "")], error: nil) == nil)
    }

    /// Ruling 68: only a completed `bootstrap upgrade` means the vault has
    /// switched; before that the old engine is fully live.
    @Test func switchedOverOnlyOnceBootstrapHasRun() {
        #expect(!EngineUpgradeSheet.switchedOver([.buildVenv: InstallProgress(step: .buildVenv, status: .failed("x"), log: "")]))
        #expect(EngineUpgradeSheet.switchedOver([
            .bootstrapVault: InstallProgress(step: .bootstrapVault, status: .done, log: ""),
            .registerWithClaudeCode: InstallProgress(step: .registerWithClaudeCode, status: .failed("x"), log: ""),
        ]))
    }
}

extension EngineUpgradeSheetTests {
    /// Ruling 69 M2: the note follows what is live — the old engine before
    /// the switch; Claude Code still to move after it; only a re-check once
    /// Claude Code moved too.
    @Test func statusNoteFollowsHowFarTheSwitchGot() {
        func p(_ done: [InstallStep], failed: InstallStep) -> [InstallStep: InstallProgress] {
            var d = Dictionary(uniqueKeysWithValues: done.map { ($0, InstallProgress(step: $0, status: .done, log: "")) })
            d[failed] = InstallProgress(step: failed, status: .failed("x"), log: "x")
            return d
        }
        #expect(EngineUpgradeSheet.statusNote(p([.ensureUv, .unpackEngine], failed: .buildVenv)).contains("current engine keeps working"))
        #expect(EngineUpgradeSheet.statusNote(p([.ensureUv, .unpackEngine, .buildVenv, .bootstrapVault], failed: .registerWithClaudeCode))
                    .contains("finishes switching Claude Code"))
        let verifyOnly = EngineUpgradeSheet.statusNote(p([.ensureUv, .unpackEngine, .buildVenv, .bootstrapVault, .registerWithClaudeCode], failed: .verify))
        #expect(!verifyOnly.contains("switching Claude Code") && verifyOnly.contains("health check"))
    }

    /// Ruling 69 I4: target == installed reads as a repair.
    @Test func titleSaysRepairingWhenTargetIsInstalled() {
        #expect(EngineUpgradeSheet.title(targetVersion: "0.11.0", isRepair: true) == "Repairing the Scout engine")
        #expect(EngineUpgradeSheet.title(targetVersion: "0.11.0", isRepair: false) == "Updating the Scout engine to 0.11.0")
    }
}

@MainActor
@Suite("Engine upgrade + gate — smoke", .serialized)
struct EngineUpgradeSheetSmokeTests {
    @Test("the upgrade sheet renders a failed step with Retry/Later")
    func upgradeSheetAfterAFailure() {
        ViewHost.render(
            EngineUpgradeSheet(
                progress: [
                    .ensureUv: InstallProgress(step: .ensureUv, status: .done, log: "/Users/alex/.local/bin/uv"),
                    .unpackEngine: InstallProgress(step: .unpackEngine, status: .skipped("already unpacked"), log: ""),
                    .buildVenv: InstallProgress(step: .buildVenv, status: .failed("x"), log: "install-venv.sh failed: boom"),
                ],
                error: nil, targetVersion: "0.11.0", isRepair: true, isRunning: false, retry: {}, dismiss: {}),
            size: CGSize(width: 460, height: 420))
    }

    @Test("the window's gate shows onboarding for a missing engine")
    func gateShowsOnboarding() {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("gate-smoke-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        var configuration = AppState.Configuration.testing(scoutDirectory: root)
        configuration.initialEngineState = .notInstalled
        let state = AppState(configuration: configuration)
        #expect(state.onboarding != nil)
        ViewHost.render(
            MainWindowView()
                .environmentObject(state)
                .environmentObject(state.proposalsDocumentService),
            size: CGSize(width: 1100, height: 720))
    }

    @Test("Settings' onboarding sheet renders")
    func settingsOnboardingSheet() {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("gate-smoke-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        var configuration = AppState.Configuration.testing(scoutDirectory: root)
        configuration.initialEngineState = .broken(nil, reason: "engine pointer names a missing scoutctl: /s")
        let state = AppState(configuration: configuration)
        guard let flow = state.beginOnboarding() else { Issue.record("a broken app-owned engine gates, so Settings gets a flow"); return }
        ViewHost.render(OnboardingSheet(model: flow, close: {}), size: CGSize(width: 720, height: 600))
    }
}
