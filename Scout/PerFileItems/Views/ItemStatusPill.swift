import SwiftUI

/// Small color-coded capsule for a per-file item's lifecycle status. Follows
/// the same editorial chip family as `ProposalStatusPill`.
struct ItemStatusPill: View {
    let status: ItemStatus

    var body: some View {
        Text(status.displayName.uppercased())
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
        switch status {
        case .open:         return DS.Priority.todo
        case .inProgress:   return DS.SlotType.consolidation
        case .done:         return DS.Status.ok
        case .dropped:      return DS.Ink.p3
        case .unknown:      return DS.Ink.p3
        }
    }
}
