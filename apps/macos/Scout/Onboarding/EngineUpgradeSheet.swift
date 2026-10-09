import SwiftUI

/// Modal progress while the bundled engine replaces the installed one
/// (spec §5 "every launch"). Steps follow `EngineUpgrader.upgradeSteps`. On
/// failure the failing step's log shows with Retry (re-runs the idempotent
/// upgrade) and Later (hides the sheet; the old engine stays live).
struct EngineUpgradeSheet: View {
    let progress: [InstallStep: InstallProgress]
    let error: String?
    let targetVersion: String
    /// Target == installed: finishing a switch or repairing (Ruling 69 I4).
    var isRepair: Bool = false
    let isRunning: Bool
    let retry: () -> Void
    let dismiss: () -> Void

    /// What went wrong, if anything: the failed step's log, else the
    /// upgrade's own error (it never reached a step).
    nonisolated static func failure(progress: [InstallStep: InstallProgress], error: String?) -> String? {
        let failedStep = EngineUpgrader.upgradeSteps.lazy.compactMap { progress[$0] }.first {
            if case .failed = $0.status { return true }
            return false
        }
        if let failedStep { return "\(failedStep.step.title) failed: \(failedStep.log)" }
        return error
    }

    /// True once `bootstrap upgrade` — the atomic switch (Ruling 68) — has
    /// run, so a later failure no longer leaves the old engine fully live.
    nonisolated static func switchedOver(_ progress: [InstallStep: InstallProgress]) -> Bool {
        progress[.bootstrapVault]?.status == .done
    }

    nonisolated static func title(targetVersion: String, isRepair: Bool) -> String {
        if isRepair { return "Repairing the Scout engine" }
        return targetVersion.isEmpty ? "Updating the Scout engine" : "Updating the Scout engine to \(targetVersion)"
    }

    /// What a failure means for the engine that's live right now: before the
    /// switch the old engine is untouched; after it but before Claude Code
    /// moved, Retry finishes that; once Claude Code moved too (only verify
    /// failed), Retry just re-checks (Ruling 69 M2).
    nonisolated static func statusNote(_ progress: [InstallStep: InstallProgress]) -> String {
        guard switchedOver(progress) else { return "Your current engine keeps working until the update succeeds." }
        if progress[.registerWithClaudeCode]?.status == .done {
            return "The new engine is installed and Claude Code uses it; Retry re-runs the health check."
        }
        return "Your vault already runs the new engine; Retry finishes switching Claude Code over."
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(Self.title(targetVersion: targetVersion, isRepair: isRepair))
                .font(DS.serif(20, weight: .medium)).foregroundStyle(DS.Ink.p1)
            ForEach(EngineUpgrader.upgradeSteps, id: \.rawValue) { s in
                HStack(spacing: 10) {
                    Text(glyph(progress[s]?.status)).font(DS.mono(13)).foregroundStyle(DS.Ink.p3).frame(width: 16)
                    Text(s.title).font(DS.sans(13)).foregroundStyle(DS.Ink.p1)
                }
            }
            if !isRunning, let failure = Self.failure(progress: progress, error: error) {
                Text(failure).font(DS.mono(11)).foregroundStyle(DS.Status.warn).lineLimit(6).textSelection(.enabled)
                Text(Self.statusNote(progress))
                    .font(DS.sans(11.5)).foregroundStyle(DS.Ink.p3)
                HStack(spacing: 16) {
                    Button("Retry", action: retry)
                    Button("Later", action: dismiss).keyboardShortcut(.cancelAction)
                }
                .buttonStyle(.plainHit).font(DS.sans(13, weight: .medium)).foregroundStyle(DS.Accent.ink)
            }
        }
        .padding(28)
        .frame(width: 460, alignment: .leading)
        .interactiveDismissDisabled(isRunning)
    }

    private func glyph(_ s: StepStatus?) -> String {
        switch s { case .done?: return "✓"; case .skipped?: return "–"; case .running?: return "…"; case .failed?: return "✗"; default: return "○" }
    }
}
