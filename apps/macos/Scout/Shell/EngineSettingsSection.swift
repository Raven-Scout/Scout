import SwiftUI

/// Settings ▸ Engine (spec §5). Buttons that Part C implements are wired
/// through optional closures so Part B ships with them hidden.
struct EngineSettingsSection: View {
    @ObservedObject var health: EngineHealthService
    var bundledVersion: String?
    var onUpdate: (() -> Void)? = nil
    var onRepair: (() -> Void)? = nil
    @AppStorage("scoutDataDir") private var scoutDataDir: String = ""

    private var model: EngineSettingsModel {
        EngineSettingsModel(state: health.state, doctor: health.doctor, lastError: health.lastError, bundledVersion: bundledVersion)
    }

    var body: some View {
        SettingsCard {
            SettingsRow(title: "Engine", help: model.sourceLabel) {
                Text(versionText).font(DS.mono(12, weight: .medium)).foregroundStyle(DS.Ink.p1)
            }
            if let root = model.rootPath {
                SettingsRow(title: "Engine location", help: "The scout-plugin tree the app and launchd jobs run.") {
                    Text(root).font(DS.mono(11)).foregroundStyle(DS.Ink.p3).lineLimit(1).truncationMode(.middle)
                }
            }
            SettingsField(label: "Scout vault", help: "Folder Scout reads and writes. Blank = `~/Scout`, or the vault the engine was set up for. Points the app at a vault; never moves data. Takes effect after restarting Scout. Scheduled runs keep their current vault until you run /scout-update.") {
                SettingsInput(text: $scoutDataDir, placeholder: health.state.install?.vault?.path ?? "~/Scout")
            }
            SettingsRow(title: "Health", help: model.messages.first ?? "Last checked \(health.lastChecked.map { $0.formatted(date: .omitted, time: .shortened) } ?? "never")") {
                HStack(spacing: 10) {
                    Text(model.healthLabel)
                        .font(DS.sans(12, weight: .medium))
                        .foregroundStyle(model.healthIsOK ? DS.Status.ok : DS.Status.warn)
                    Button("Check now") { Task { await health.refresh() } }
                        .buttonStyle(.plainHit)
                        .font(DS.sans(12))
                }
            }
            if model.messages.count > 1 {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(model.messages.dropFirst(), id: \.self) { Text($0).font(DS.mono(11)).foregroundStyle(DS.Ink.p3) }
                }.padding(.vertical, 10)
            }
            // Part B's honest remedy: a copyable command the user runs
            // themselves. The app never runs it. Part C replaces this row
            // with Install/Repair buttons.
            if let step = model.nextStep {
                SettingsRow(title: "Next step", help: step.text) {
                    Button(Self.copyButtonTitle(for: step)) { Self.copyToPasteboard(step.copyValue) }
                        .buttonStyle(.plainHit)
                }
                if !step.copyValue.hasPrefix("/") {
                    Text(step.copyValue)
                        .font(DS.mono(11)).foregroundStyle(DS.Ink.p3)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.vertical, 10)
                }
            }
            if model.canUpdate, let onUpdate {
                SettingsRow(title: "Update engine", help: "Install engine \(bundledVersion ?? "") that ships with this app, then upgrade the vault.") {
                    Button("Update") { onUpdate() }.buttonStyle(.plainHit)
                }
            }
            if model.canRepair, let onRepair {
                SettingsRow(title: "Repair", help: "Re-run the installer steps that failed or went missing.") {
                    Button("Repair…") { onRepair() }.buttonStyle(.plainHit)
                }
            }
            if model.showsHandOff {
                SettingsRow(title: "Update available", help: "This engine is managed outside the app. Run `/scout-update` in Claude Code.") {
                    Button("Copy /scout-update") { Self.copyToPasteboard("/scout-update") }
                        .buttonStyle(.plainHit)
                }
            }
        }
    }

    /// A slash command fits on the button ("Copy /scout-setup"); the
    /// Terminal one-liner doesn't, so it's shown on its own line instead.
    nonisolated static func copyButtonTitle(for step: EngineNextStep) -> String {
        step.copyValue.hasPrefix("/") ? "Copy \(step.copyValue)" : "Copy command"
    }

    private static func copyToPasteboard(_ value: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }

    private var versionText: String {
        guard let bundled = model.bundledVersionLabel, bundled != model.installedVersionLabel else { return model.installedVersionLabel }
        return "\(model.installedVersionLabel) → \(bundled)"
    }
}
