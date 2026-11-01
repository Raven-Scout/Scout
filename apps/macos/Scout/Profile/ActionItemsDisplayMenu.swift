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
/// fields on cards. Every choice is written to `scout-display.json`.
struct ActionItemsDisplayMenu: View {
    enum Option: CaseIterable, Sendable { case sort, group, density, fields }

    /// The options that apply in `mode` (Raven-Scout/Scout#347 §2.3). The
    /// Board's columns are its grouping. Topics has its own order, grouping and
    /// fixed-height rows and doesn't read the card fields yet, so it gets none
    /// and the toolbar leaves the menu out.
    nonisolated static func options(for mode: ActionItemsViewMode) -> [Option] {
        switch mode {
        case .list:   return [.sort, .group, .density, .fields]
        case .board:  return [.sort, .density, .fields]
        case .topics: return []
        }
    }

    @EnvironmentObject var service: DisplayProfileService
    let mode: ActionItemsViewMode

    var body: some View {
        let options = Self.options(for: mode)
        Menu {
            if options.contains(.sort) {
                Picker("Sort", selection: service.binding(\.sort)) {
                    ForEach(ActionItemsDisplay.Sort.allCases) { Text($0.displayName).tag($0) }
                }
                .pickerStyle(.inline)
            }
            if options.contains(.group) {
                Picker("Group", selection: service.binding(\.grouping)) {
                    ForEach(ActionItemsDisplay.Grouping.allCases) { Text($0.displayName).tag($0) }
                }
                .pickerStyle(.inline)
            }
            if options.contains(.density) {
                Picker("Density", selection: service.binding(\.density)) {
                    ForEach(ActionItemsDisplay.Density.allCases) { Text($0.displayName).tag($0) }
                }
                .pickerStyle(.inline)
            }
            if options.contains(.fields) {
                Section("Show on cards") {
                    Toggle("References", isOn: service.binding(\.fields.refs))
                    Toggle("Snooze date", isOn: service.binding(\.fields.snooze))
                    Toggle("Comment count", isOn: service.binding(\.fields.comments))
                }
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
