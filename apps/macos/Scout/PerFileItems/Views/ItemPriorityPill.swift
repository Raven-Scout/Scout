import SwiftUI

/// A per-file item's priority as a design-system pill. Read-only by default;
/// when `options` is non-empty it becomes a Menu for changing the priority
/// (issue #41).
struct ItemPriorityPill: View {
    let priority: ItemPriority
    var options: [ItemPriority] = []
    var onSelect: ((ItemPriority) -> Void)? = nil

    var body: some View {
        if options.isEmpty || onSelect == nil {
            pill
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
                pill
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Change priority")
        }
    }

    private var pill: some View {
        DSPill(priority.displayName, tone: Self.tone(priority))
    }

    nonisolated static func tone(_ priority: ItemPriority) -> DS.PillTone {
        switch priority {
        case .urgent: return .urgent
        case .high:   return .accent
        case .medium: return .ok
        case .low:    return .neutral
        }
    }
}
