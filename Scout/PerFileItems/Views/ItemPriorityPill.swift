import SwiftUI

/// Small color-coded capsule for a per-file item's priority. Follows the
/// same editorial chip family as `ProposalStatusPill`.
struct ItemPriorityPill: View {
    let priority: ItemPriority

    var body: some View {
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
