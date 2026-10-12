import Foundation

/// Back-off for the launch-time engine upgrade (final review I2). A launch
/// upgrade that fails at or after `bootstrapVault` — the steps that need the
/// vault, Claude Code or the doctor, where a failure is usually permanent
/// until the user acts — is remembered in the app's `UserDefaults` as
/// `(targetVersion, reason)`. The next launch then does not auto-open
/// the upgrade sheet for the same target; Settings ▸ Engine's
/// "Finish update" / "Repair…" row carries it instead. A manual run clears
/// the memo before it starts, a successful run clears it, and a different
/// (newer) bundled target ignores it.
///
/// Earlier failures (uv, unpack, venv) aren't remembered: they leave the old
/// engine fully live (Ruling 68) and are often transient, so the next launch
/// simply tries again.
///
/// A launch upgrade that can't find Claude Code fails before any step runs
/// (#346). It is remembered as its own reason; the next launch skips it while
/// Claude Code is still missing, and runs once it is found.
nonisolated struct EngineUpgradeFailureMemo: Equatable, Sendable {
    enum Reason: Equatable, Sendable {
        case failedStep(InstallStep)
        case claudeCodeMissing
    }

    let targetVersion: String
    let reason: Reason

    static let defaultsKey = "engineAutoUpgradeFailure"
    private static let claudeCodeMissingValue = "claudeCodeMissing"

    init(targetVersion: String, reason: Reason) {
        self.targetVersion = targetVersion
        self.reason = reason
    }

    init(targetVersion: String, failedStep: InstallStep) {
        self.init(targetVersion: targetVersion, reason: .failedStep(failedStep))
    }

    static func claudeCodeMissing(target: String) -> EngineUpgradeFailureMemo {
        EngineUpgradeFailureMemo(targetVersion: target, reason: .claudeCodeMissing)
    }

    var failedStep: InstallStep? {
        if case .failedStep(let step) = reason { return step }
        return nil
    }

    /// `bootstrapVault` and everything after it in `EngineUpgrader.upgradeSteps`.
    static func isRemembered(_ step: InstallStep) -> Bool {
        let order = EngineUpgrader.upgradeSteps
        guard let index = order.firstIndex(of: step), let threshold = order.firstIndex(of: .bootstrapVault) else { return false }
        return index >= threshold
    }

    /// Whether this memo holds back an automatic upgrade to `target`.
    func suppressesAutoUpgrade(to target: String) -> Bool {
        guard targetVersion == target else { return false }
        switch reason {
        case .failedStep(let step): return Self.isRemembered(step)
        case .claudeCodeMissing: return true
        }
    }

    static func load(from defaults: UserDefaults) -> EngineUpgradeFailureMemo? {
        guard let stored = defaults.dictionary(forKey: defaultsKey),
              let target = stored["target"] as? String else { return nil }
        if let reason = stored["reason"] as? String {
            return reason == claudeCodeMissingValue ? .claudeCodeMissing(target: target) : nil
        }
        guard let step = (stored["step"] as? String).flatMap(InstallStep.init(rawValue:)) else { return nil }
        return EngineUpgradeFailureMemo(targetVersion: target, failedStep: step)
    }

    func save(to defaults: UserDefaults) {
        let stored: [String: String]
        switch reason {
        case .failedStep(let step): stored = ["target": targetVersion, "step": step.rawValue]
        case .claudeCodeMissing: stored = ["target": targetVersion, "reason": Self.claudeCodeMissingValue]
        }
        defaults.set(stored, forKey: Self.defaultsKey)
    }

    static func clear(in defaults: UserDefaults) {
        defaults.removeObject(forKey: defaultsKey)
    }

    /// The memo for a finished launch upgrade's progress: the step that
    /// failed, if it is one worth remembering; nil otherwise.
    static func forFailure(target: String, progress: [InstallStep: InstallProgress]) -> EngineUpgradeFailureMemo? {
        let failed = EngineUpgrader.upgradeSteps.first { step in
            if case .failed? = progress[step]?.status { return true }
            return false
        }
        guard let failed, isRemembered(failed) else { return nil }
        return EngineUpgradeFailureMemo(targetVersion: target, failedStep: failed)
    }
}
