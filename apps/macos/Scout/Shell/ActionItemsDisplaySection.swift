import SwiftUI

/// Settings > Action Items: the default view plus the options of the toolbar's
/// View menu, all kept in `scout-profile.json` in the vault root (#52).
struct ActionItemsDisplaySection: View {
    @EnvironmentObject var service: DisplayProfileService

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            SettingsCard {
                SettingsRow(
                    title: "Default view",
                    help: "What Action Items opens in. The List/Board switch in the toolbar only changes the current view."
                ) {
                    picker(\.defaultView, ActionItemsViewMode.allCases) { $0.displayName }
                }
                SettingsRow(title: "Sort", help: "Order of tasks in each section and Board column.") {
                    picker(\.sort, ActionItemsDisplay.Sort.allCases) { $0.displayName }
                }
                SettingsRow(
                    title: "Group",
                    help: "By section, or one list of open tasks. Each card keeps its priority stripe."
                ) {
                    picker(\.grouping, ActionItemsDisplay.Grouping.allCases) { $0.displayName }
                }
                SettingsRow(
                    title: "Density",
                    help: "Compact hides the body preview and starts every card collapsed."
                ) {
                    picker(\.density, ActionItemsDisplay.Density.allCases) { $0.displayName }
                }
                SettingsRow(title: "References", help: "Linear, GitHub, Slack and other links on collapsed cards.") {
                    SettingsToggle(isOn: service.binding(\.fields.refs))
                }
                SettingsRow(title: "Snooze date", help: "The snooze date on collapsed cards.") {
                    SettingsToggle(isOn: service.binding(\.fields.snooze))
                }
                SettingsRow(title: "Comment count", help: "How many comments a task has, on collapsed cards.") {
                    SettingsToggle(isOn: service.binding(\.fields.comments))
                }
            }
            Text("Saved in \(path)")
                .font(DS.mono(11))
                .foregroundStyle(DS.Ink.p4)
            if let warning {
                Text(warning)
                    .font(DS.sans(11.5))
                    .foregroundStyle(DS.Status.warn)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func picker<V: Hashable & Identifiable>(
        _ keyPath: WritableKeyPath<ActionItemsDisplay, V>,
        _ options: [V],
        label: @escaping (V) -> String
    ) -> some View {
        Picker("", selection: service.binding(keyPath)) {
            ForEach(options) { Text(label($0)).tag($0) }
        }
        .labelsHidden()
        .pickerStyle(.menu)
        .fixedSize()
    }

    private var path: String {
        (service.fileURL.path as NSString).abbreviatingWithTildeInPath
    }

    private var warning: String? {
        var lines: [String] = []
        switch service.status {
        case .ok(let warnings) where !warnings.isEmpty:
            lines.append("Ignored in the file: " + warnings.joined(separator: "; ") + ".")
        case .unreadable(let reason):
            lines.append("Can't read the file: \(reason). Fix or delete it to save changes again.")
        case .unsupportedSchema(let found):
            lines.append("Written by a newer Scout (schema \(found)). Changes here won't be saved.")
        case .ok:
            break
        }
        if let error = service.writeError {
            lines.append("Couldn't save: \(error)")
        }
        return lines.isEmpty ? nil : lines.joined(separator: "\n")
    }
}
