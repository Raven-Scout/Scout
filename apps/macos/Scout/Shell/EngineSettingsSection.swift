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
    /// "Check now" is running: the button says so and waits (UI audit #84).
    @State private var isChecking = false
    /// "Copy /scout-update" just copied: the button says "Copied" for a moment.
    @State private var copied = false
    /// Turns "Copied" back into "Copy"; replaced on each copy, so a double
    /// click doesn't revert early.
    @State private var copiedReset: Task<Void, Never>?

    private var model: EngineSettingsModel {
        EngineSettingsModel(state: health.state, doctor: health.doctor, lastError: health.lastError, bundledVersion: bundledVersion,
                            unfinishedSwitch: unfinishedSwitch)
    }

    var body: some View {
        SettingsCard {
            SettingsRow(title: "Engine", help: model.sourceLabel) {
                Text(versionText).font(DS.font(.label, .mono, weight: .medium)).foregroundStyle(DS.Ink.p1)
            }
            if let root = model.rootPath {
                SettingsRow(title: "Engine location", help: "The engine the app and its scheduled runs use.") {
                    Text(root).font(DS.font(.caption, .mono)).foregroundStyle(DS.Ink.p3).lineLimit(1).truncationMode(.middle)
                }
            }
            SettingsField(label: "Scout folder", help: model.vaultHelp) {
                SettingsPathPicker(path: $scoutDataDir,
                                   defaultLabel: health.state.install?.vault?.path ?? "~/Scout",
                                   kind: .folder,
                                   panelMessage: "Choose your Scout folder",
                                   defaultURL: health.state.install?.vault ?? vault,
                                   allowsNewFolder: false,
                                   notes: SettingsPathCheck.scoutFolderNotes(
                                       for: scoutDataDir, engineVault: health.state.install?.vault,
                                       home: FileManager.default.homeDirectoryForCurrentUser))
            }
            SettingsRow(title: "Health", help: healthHelp) {
                HStack(spacing: 10) {
                    Text(model.healthLabel)
                        .font(DS.font(.label, weight: .medium))
                        .foregroundStyle(model.healthIsOK && !model.healthHasWarnings ? DS.Status.ok : DS.Status.warn)
                    if isChecking {
                        ProgressView().controlSize(.small)
                            .accessibilityLabel("Checking the engine")
                    }
                    Button(isChecking ? "Checking…" : "Check now") { checkNow() }
                        .buttonStyle(.ds(.secondary, size: .small))
                        .disabled(isChecking)
                        .accessibilityIdentifier("settings-engine-check-now")
                }
            }
            if model.showsRuns {
                // The findings list below has each finding's fix and log.
                SettingsRow(title: "Runs", help: model.runsHelp) {
                    Text(model.runsLabel)
                        .font(DS.font(.label, weight: .medium))
                        .foregroundStyle(model.runsIsOK ? DS.Status.ok : model.runsIsPaused ? DS.Ink.p2 : DS.Status.warn)
                }
            }
            if !model.findingGroups.isEmpty {
                DoctorFindingsList(groups: model.findingGroups)
            } else if model.messages.count > 1 {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(model.messages.dropFirst(), id: \.self) { Text($0).font(DS.font(.caption, .mono)).foregroundStyle(DS.Ink.p3) }
                }.padding(.vertical, 10)
            }
            if let action = model.setupAction, let onSetUp {
                SettingsRow(title: action.rowTitle, help: model.setupHelp ?? "") {
                    Button(action.buttonTitle) { onSetUp() }.buttonStyle(.dsSecondary)
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
                        .font(DS.font(.label, weight: .medium))
                        .foregroundStyle(model.updatesIsOK ? DS.Status.ok : DS.Status.warn)
                }
            }
            if model.showsHandOff {
                SettingsRow(title: model.handOffTitle, help: model.handOffHelp) {
                    Button { copyHandOff() } label: {
                        Label(copied ? "Copied" : "Copy /scout-update",
                              systemImage: copied ? "checkmark" : "doc.on.doc")
                    }
                    .buttonStyle(.ds(.secondary, size: .small))
                    .accessibilityIdentifier("settings-engine-copy-handoff")
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
        case .update: return "Install engine \(bundledVersion ?? "") that ships with this app, then upgrade your Scout folder."
        case .finishUpdate: return "An earlier update stopped before Claude Code switched over. Finish it with the engine that ships with this app."
        case .repair: return "Re-run the engine's install steps and the Scout folder upgrade with the engine that ships with this app."
        }
    }

    private static func copyToPasteboard(_ value: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(value, forType: .string)
    }

    /// Runs the doctor again, showing that it's working until it answers.
    private func checkNow() {
        guard !isChecking else { return }
        isChecking = true
        Task {
            await health.refresh()
            isChecking = false
            AccessibilityNotification.Announcement("Engine checked: \(model.healthLabel)").post()
        }
    }

    /// Copies the hand-off command and says so, on screen and to VoiceOver.
    private func copyHandOff() {
        Self.copyToPasteboard("/scout-update")
        copied = true
        AccessibilityNotification.Announcement("Copied /scout-update").post()
        copiedReset?.cancel()
        copiedReset = Task {
            try? await Task.sleep(for: .seconds(Self.copiedFor))
            guard !Task.isCancelled else { return }
            copied = false
        }
    }

    /// How long "Copied" stays before the button reads "Copy" again.
    nonisolated static let copiedFor: Double = 2

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
                        .font(DS.font(.caption2, weight: .semibold))
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
                    .font(DS.font(.label))
                    .foregroundStyle(DS.Ink.p1)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                if let detail = item.detail {
                    Text(detail).font(DS.font(.caption, .mono)).foregroundStyle(DS.Ink.p2)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                }
                if let fix = item.fix {
                    Text(fix).font(DS.font(.caption)).foregroundStyle(DS.Ink.p3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !item.items.isEmpty {
                    IssueFilesDisclosure(files: item.items)
                }
                if let log = item.logURL {
                    Button("Show log") { NSWorkspace.shared.activateFileViewerSelecting([log]) }
                        .buttonStyle(.ds(.link, size: .small))
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

/// The files one grouped warning stands for, folded away until asked for (UI audit R1):
/// "34 vault scripts read the knowledge base directly" is one line, not 34 paragraphs.
struct IssueFilesDisclosure: View {
    let files: [String]
    @State private var expanded = false

    nonisolated static func label(_ count: Int) -> String { count == 1 ? "Show the file" : "Show all \(count) files" }

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(files, id: \.self) { file in
                    Text(file).font(DS.font(.caption, .mono)).foregroundStyle(DS.Ink.p2).textSelection(.enabled)
                }
            }
            .padding(.top, 4)
        } label: {
            Text(Self.label(files.count)).font(DS.font(.caption)).foregroundStyle(DS.Ink.p2)
                .accessibilityHint("Lists the files this warning is about")
        }
    }
}
