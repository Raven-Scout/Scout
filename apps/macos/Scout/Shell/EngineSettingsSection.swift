import SwiftUI

/// Settings ▸ Engine (spec §5, Ruling 41). Every state the app can act on
/// gets a real action: "Set up…" / "Repair…" (broken) open the onboarding
/// flow; "Update" / "Finish update" / "Repair…" (red doctor) run the
/// bundled-engine upgrade. External engines — and a broken one another
/// installer manages — keep the copy `/scout-update` hand-off; the app never
/// modifies them. A nil closure hides its row.
struct EngineSettingsSection: View {
    @ObservedObject var health: EngineHealthService
    var bundledVersion: String?
    var isUpdating: Bool = false
    /// `AppState.engineSwitchUnfinished` (Ruling 69 I1).
    var unfinishedSwitch: Bool = false
    var onUpdate: (() -> Void)? = nil
    var onSetUp: (() -> Void)? = nil
    @AppStorage("scoutDataDir") private var scoutDataDir: String = ""

    private var model: EngineSettingsModel {
        EngineSettingsModel(state: health.state, doctor: health.doctor, lastError: health.lastError, bundledVersion: bundledVersion,
                            unfinishedSwitch: unfinishedSwitch)
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
            SettingsField(label: "Scout vault", help: model.vaultHelp) {
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
            if let action = model.setupAction, let onSetUp {
                SettingsRow(title: action.rowTitle, help: model.setupHelp ?? "") {
                    Button(action.buttonTitle) { onSetUp() }.buttonStyle(.plainHit)
                }
            }
            if let action = model.upgradeAction, let onUpdate {
                SettingsRow(title: action.rowTitle, help: upgradeHelp(action)) {
                    Button(isUpdating ? "Working…" : action.buttonTitle) { onUpdate() }
                        .buttonStyle(.plainHit)
                        .disabled(isUpdating)
                }
            }
            if model.showsHandOff {
                SettingsRow(title: model.handOffTitle, help: model.handOffHelp) {
                    Button("Copy /scout-update") { Self.copyToPasteboard("/scout-update") }
                        .buttonStyle(.plainHit)
                }
            }
        }
    }

    private func upgradeHelp(_ action: EngineUpgradeAction) -> String {
        switch action {
        case .update: return "Install engine \(bundledVersion ?? "") that ships with this app, then upgrade the vault."
        case .finishUpdate: return "An earlier update stopped before Claude Code switched over. Finish it with the engine that ships with this app."
        case .repair: return "Re-run the engine's install steps and vault upgrade with the engine that ships with this app."
        }
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
