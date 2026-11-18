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
    /// The vault the app shows; set, it adds the Troubleshooting row (Help menu's tools, audit C11).
    var vault: URL? = nil
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
            SettingsRow(title: "Health", help: healthHelp) {
                HStack(spacing: 10) {
                    Text(model.healthLabel)
                        .font(DS.sans(12, weight: .medium))
                        .foregroundStyle(model.healthIsOK && !model.healthHasWarnings ? DS.Status.ok : DS.Status.warn)
                    Button("Check now") { Task { await health.refresh() } }
                        .buttonStyle(.plainHit)
                        .font(DS.sans(12))
                }
            }
            if model.showsRuns {
                // The findings list below has each finding's fix and log.
                SettingsRow(title: "Runs", help: model.runsHelp) {
                    Text(model.runsLabel)
                        .font(DS.sans(12, weight: .medium))
                        .foregroundStyle(model.runsIsOK ? DS.Status.ok : model.runsIsPaused ? DS.Ink.p2 : DS.Status.warn)
                }
            }
            if !model.findingGroups.isEmpty {
                DoctorFindingsList(groups: model.findingGroups)
            } else if model.messages.count > 1 {
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
            if model.updatesAutomatically {
                SettingsRow(title: "Updates", help: model.updatesHelp) {
                    Text(model.updatesIsOK ? "Automatic" : "Needs attention")
                        .font(DS.sans(12, weight: .medium))
                        .foregroundStyle(model.updatesIsOK ? DS.Status.ok : DS.Status.warn)
                }
            }
            if model.showsHandOff {
                SettingsRow(title: model.handOffTitle, help: model.handOffHelp) {
                    Button("Copy /scout-update") { Self.copyToPasteboard("/scout-update") }
                        .buttonStyle(.plainHit)
                }
            }
            if let vault {
                TroubleshootingRow(vault: vault)
            }
        }
    }

    /// A broken engine or an unreadable doctor says why; a doctor that
    /// answered gives its counts (the list below has every finding).
    private var healthHelp: String {
        let checked = "Last checked \(health.lastChecked.map { $0.formatted(date: .omitted, time: .shortened) } ?? "never")"
        if let counts = model.findingCountsLabel { return "\(counts) · \(checked)" }
        return model.messages.first ?? checked
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

/// Every doctor finding, worst first: Problems, then Warnings, then Notes.
/// Each shows its summary, the failing run's own line, the fix and its log.
struct DoctorFindingsList: View {
    let groups: [DoctorFindingGroup]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(groups, id: \.severity) { group in
                VStack(alignment: .leading, spacing: 8) {
                    Text("\(group.severity.title.uppercased()) · \(group.items.count)")
                        .font(DS.sans(10, weight: .semibold))
                        .tracking(0.6)
                        .foregroundStyle(color(group.severity))
                        .accessibilityAddTraits(.isHeader)
                    ForEach(group.items) { item in row(item, severity: group.severity) }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 12)
    }

    private func row(_ item: DoctorFindingItem, severity: DoctorFindingGroup.Severity) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Circle().fill(color(severity)).frame(width: 6, height: 6)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.summary)
                    .font(DS.sans(12))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                if let detail = item.detail {
                    Text(detail).font(DS.mono(11)).foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                if let fix = item.fix {
                    Text(fix).font(DS.sans(11)).foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let log = item.logURL {
                    Button("Show log") { NSWorkspace.shared.activateFileViewerSelecting([log]) }
                        .buttonStyle(.plainHit)
                        .font(DS.sans(11))
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(severity.singular.capitalized): \(item.summary)")
    }

    private func color(_ severity: DoctorFindingGroup.Severity) -> Color {
        switch severity {
        case .problem: return DS.Status.err
        case .warning: return DS.Status.warn
        case .note:    return DS.Ink.p3
        }
    }
}
