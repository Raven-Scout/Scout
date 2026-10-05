import SwiftUI

/// Small color-coded capsule for a per-file item's priority. Read-only by
/// default; when `options` is non-empty it becomes a Menu for changing the
/// priority (issue #41).
struct ItemPriorityPill: View {
    let priority: ItemPriority
    var options: [ItemPriority] = []
    var onSelect: ((ItemPriority) -> Void)? = nil

    var body: some View {
        if options.isEmpty || onSelect == nil {
            capsule
        } else {
            Menu {
                ForEach(options, id: \.self) { opt in
                    Button {
                        if opt != priority { onSelect?(opt) }
                    } label: {
                        if opt == priority { Label(opt.displayName, systemImage: "checkmark") }
                        else { Text(opt.displayName) }
                    }
                }
            } label: {
                capsule
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Change priority")
        }
    }

    private var capsule: some View {
        Text(priority.displayName.uppercased())
            .font(DS.sans(10, weight: .semibold))
            .tracking(0.06 * 10)
            .foregroundStyle(tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Capsule().fill(tint.opacity(0.14)))
            .overlay(Capsule().strokeBorder(tint.opacity(0.35), lineWidth: 0.5))
            .fixedSize()
    }

    private var tint: Color {
        switch priority {
        case .urgent: return DS.Priority.urgent
        case .high:   return DS.Priority.todo
        case .medium: return DS.Priority.watch
        case .low:    return DS.Ink.p3
        }
    }
}
