import Testing
import Foundation
@testable import Scout

/// Final review I2: the launch-upgrade back-off's rules, independent of
/// AppState. Every test uses a throwaway suite, never `.standard`.
@Suite("EngineUpgradeFailureMemo")
struct EngineUpgradeFailureMemoTests {
    func withSuite(_ body: (UserDefaults) throws -> Void) rethrows {
        let name = "scout.tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { UserDefaults().removePersistentDomain(forName: name) }
        try body(defaults)
    }

    @Test func onlyStepsFromBootstrapVaultOnAreRemembered() {
        #expect(EngineUpgrader.upgradeSteps.filter(EngineUpgradeFailureMemo.isRemembered) == [.bootstrapVault, .registerWithClaudeCode, .verify])
    }

    @Test func suppressesOnlyTheSameTarget() {
        let memo = EngineUpgradeFailureMemo(targetVersion: "0.11.0", failedStep: .registerWithClaudeCode)
        #expect(memo.suppressesAutoUpgrade(to: "0.11.0"))
        #expect(!memo.suppressesAutoUpgrade(to: "0.12.0"))
        #expect(!EngineUpgradeFailureMemo(targetVersion: "0.11.0", failedStep: .buildVenv).suppressesAutoUpgrade(to: "0.11.0"))
    }

    @Test func roundTripsThroughDefaultsAndClears() {
        withSuite { defaults in
            #expect(EngineUpgradeFailureMemo.load(from: defaults) == nil)
            let memo = EngineUpgradeFailureMemo(targetVersion: "0.11.0", failedStep: .verify)
            memo.save(to: defaults)
            #expect(EngineUpgradeFailureMemo.load(from: defaults) == memo)
            EngineUpgradeFailureMemo.clear(in: defaults)
            #expect(EngineUpgradeFailureMemo.load(from: defaults) == nil)
        }
    }

    @Test func anUnreadableMemoIsIgnored() {
        withSuite { defaults in
            defaults.set(["target": "0.11.0", "step": "noSuchStep"], forKey: EngineUpgradeFailureMemo.defaultsKey)
            #expect(EngineUpgradeFailureMemo.load(from: defaults) == nil)
            defaults.set("garbage", forKey: EngineUpgradeFailureMemo.defaultsKey)
            #expect(EngineUpgradeFailureMemo.load(from: defaults) == nil)
        }
    }

    @Test func forFailureNamesTheFailedLateStep() {
        func progress(_ failed: InstallStep) -> [InstallStep: InstallProgress] {
            var p: [InstallStep: InstallProgress] = [:]
            for step in EngineUpgrader.upgradeSteps {
                p[step] = InstallProgress(step: step, status: step == failed ? .failed("x") : .done, log: "")
                if step == failed { break }
            }
            return p
        }
        #expect(EngineUpgradeFailureMemo.forFailure(target: "0.11.0", progress: progress(.registerWithClaudeCode))
                == .init(targetVersion: "0.11.0", failedStep: .registerWithClaudeCode))
        #expect(EngineUpgradeFailureMemo.forFailure(target: "0.11.0", progress: progress(.bootstrapVault))?.failedStep == .bootstrapVault)
        #expect(EngineUpgradeFailureMemo.forFailure(target: "0.11.0", progress: progress(.buildVenv)) == nil)
        #expect(EngineUpgradeFailureMemo.forFailure(target: "0.11.0", progress: [:]) == nil)
    }
}
