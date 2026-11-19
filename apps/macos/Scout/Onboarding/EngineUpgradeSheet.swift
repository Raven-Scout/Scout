import SwiftUI
import AppKit

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
        return "Your Scout folder already runs the new engine; Retry finishes switching Claude Code over."
    }

    /// Why this sheet opened by itself and how long it takes (audit #91):
    /// it appears at launch with no click, so it has to say what it's doing.
    nonisolated static func explanation(isRepair: Bool) -> String {
        if isRepair {
            return "The engine, the part of Scout that runs your scheduled briefings, didn't finish setting up last time, so Scout is finishing it now. This usually takes a minute or two."
        }
        return "This version of Scout comes with a newer engine, the part that runs your scheduled briefings, so Scout is installing it before you carry on. This usually takes a minute or two."
    }

    /// Everything a bug report needs: what was being done, how far it got,
    /// and the failure itself.
    nonisolated static func copyText(title: String, progress: [InstallStep: InstallProgress], error: String?) -> String {
        var lines = [title]
        for step in EngineUpgrader.upgradeSteps {
            let status = progress[step]?.status ?? .pending
            lines.append("- \(step.title): \(status.spokenStatus)")
        }
        if let failure = failure(progress: progress, error: error) { lines += ["", failure] }
        return lines.joined(separator: "\n")
    }

    @State private var copied = false

    var body: some View {
        let title = Self.title(targetVersion: targetVersion, isRepair: isRepair)
        VStack(alignment: .leading, spacing: 14) {
            Text(title)
                .font(DS.font(.title, .serif, weight: .medium)).foregroundStyle(DS.Ink.p1)
                .accessibilityAddTraits(.isHeader)
            Text(Self.explanation(isRepair: isRepair))
                .font(DS.font(.label)).foregroundStyle(DS.Ink.p2)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 8) {
                ForEach(EngineUpgrader.upgradeSteps, id: \.rawValue) { s in
                    HStack(spacing: 10) {
                        InstallStepStatusIcon(status: progress[s]?.status)
                        Text(s.title).font(DS.font(.body)).foregroundStyle(DS.Ink.p1)
                    }
                    .accessibilityElement(children: .combine)
                }
            }
            if !isRunning, let failure = Self.failure(progress: progress, error: error) {
                ScrollView {
                    Text(failure).font(DS.font(.caption, .mono)).foregroundStyle(DS.Status.err)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(maxHeight: 120)
                Text(Self.statusNote(progress))
                    .font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Button(copied ? "Copied" : "Copy details") {
                        let pb = NSPasteboard.general
                        pb.clearContents()
                        pb.setString(Self.copyText(title: title, progress: progress, error: error), forType: .string)
                        copied = true
                    }
                    .buttonStyle(.dsLink)
                    Spacer()
                    Button("Later", action: dismiss)
                        .buttonStyle(.dsSecondary)
                        .keyboardShortcut(.cancelAction)
                    // No Return: Retry re-runs the install.
                    Button("Retry", action: retry)
                        .buttonStyle(.dsPrimary)
                }
            }
        }
        .padding(28)
        .frame(width: 460, alignment: .leading)
        .interactiveDismissDisabled(isRunning)
    }
}
