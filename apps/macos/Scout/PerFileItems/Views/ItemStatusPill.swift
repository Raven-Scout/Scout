import SwiftUI

/// Small color-coded capsule for a per-file item's lifecycle status. Follows
/// the same editorial chip family as `ProposalStatusPill`.
struct ItemStatusPill: View {
    let status: ItemStatus

    /// Longest label the pill shows. An unrecognized `status:` is free text —
    /// one was a 250-character comment — and this pill is `fixedSize`, so an
    /// unbounded label widened its card past the window.
    static let maxLabelLength = 24

    nonisolated static func label(for status: ItemStatus) -> String {
        let name = status.displayName.uppercased()
        guard name.count > maxLabelLength else { return name }
        return name.prefix(maxLabelLength - 1).trimmingCharacters(in: .whitespaces) + "…"
    }

    var body: some View {
        Text(Self.label(for: status))
            .help(status.displayName)
            .accessibilityLabel(status.displayName)
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
        case .dropped:      return DS.Status.err  // terminal "decided against" — red like Proposals' rejected
        case .unknown:      return DS.Ink.p3
        }
    }
}
