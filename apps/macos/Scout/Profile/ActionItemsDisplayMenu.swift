import SwiftUI

extension DisplayProfileService {
    /// A binding into the Action Items options that writes through `update`.
    func binding<V>(_ keyPath: WritableKeyPath<ActionItemsDisplay, V>) -> Binding<V> {
        Binding(
            get: { self.profile.actionItems[keyPath: keyPath] },
            set: { value in self.update { $0[keyPath: keyPath] = value } }
        )
    }
}

/// The View menu in the Action Items toolbar: sort, grouping, density and the
/// fields on cards. Every choice is written to `scout-profile.json`.
struct ActionItemsDisplayMenu: View {
    @EnvironmentObject var service: DisplayProfileService
    /// Grouping only applies to the List; the Board's columns are its grouping.
    var showsGrouping = true

    var body: some View {
        Menu {
            Picker("Sort", selection: service.binding(\.sort)) {
                ForEach(ActionItemsDisplay.Sort.allCases) { Text($0.displayName).tag($0) }
            }
            .pickerStyle(.inline)
            if showsGrouping {
                Picker("Group", selection: service.binding(\.grouping)) {
                    ForEach(ActionItemsDisplay.Grouping.allCases) { Text($0.displayName).tag($0) }
                }
                .pickerStyle(.inline)
            }
            Picker("Density", selection: service.binding(\.density)) {
                ForEach(ActionItemsDisplay.Density.allCases) { Text($0.displayName).tag($0) }
            }
            .pickerStyle(.inline)
            Section("Show on cards") {
                Toggle("References", isOn: service.binding(\.fields.refs))
                Toggle("Snooze date", isOn: service.binding(\.fields.snooze))
                Toggle("Comment count", isOn: service.binding(\.fields.comments))
            }
        } label: {
            Label("View", systemImage: "slider.horizontal.3")
                .font(DS.sans(11.5, weight: .medium))
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help("Sort, group, density and what cards show")
    }
}
